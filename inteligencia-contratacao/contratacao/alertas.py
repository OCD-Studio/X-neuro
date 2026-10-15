"""Resumo diário de alertas (Fase 6) — gerado sempre como RASCUNHO.

O sistema nunca envia nada: o resumo fica em `alerta_resumo` com estado 'rascunho' e é revisto por um humano
na página /alertas. Só depois de revisto pode ser copiado/descarregado para partilha fora do sistema.

Conteúdo (linguagem de risco, nunca de culpa):
1. Contratos novos com sinais de risco: registados desde o último resumo E publicados há pouco tempo
   (`JANELA_PUBLICACAO_DIAS`) — assim o carregamento de anos antigos (backfill) não aparece como "novo".
   Fornecedores que parecem pessoas singulares não são nomeados (minimização).
2. Entidades que entraram no nível alto (score ≥ 50) ou saíram dele, face ao resumo anterior. O primeiro
   resumo só regista a linha de base.
3. Estado das fontes: sincronizações falhadas ou parciais na janela.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import psycopg

LIMIAR_ALTO = 50
JANELA_PUBLICACAO_DIAS = 30
MAX_CONTRATOS = 40
MAX_ENTIDADES = 40
TIPOS_LISTAVEIS = ("coletiva", "coletiva_sem_nif")
PAPEIS = {"a": "adjudicante", "f": "fornecedor"}

AVISO = ("RASCUNHO PARA REVISÃO HUMANA — não enviar sem revisão. Os sinais são indicadores de risco calculados a partir "
         "de dados públicos; não são prova nem acusação e podem ter justificação legal.")


def _url_base() -> str:
    return os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


def _contratos_novos(con: psycopg.Connection, desde: datetime, ate: datetime) -> tuple[int, list[dict]]:
    par = {"desde": desde, "ate": ate, "dias": JANELA_PUBLICACAO_DIAS, "lim": MAX_CONTRATOS, "tl": list(TIPOS_LISTAVEIS)}
    base = """
        FROM contrato k
        JOIN avaliacao_risco r ON r.contrato_id = k.id AND r.estado = 's'
        WHERE k.registado_em > %(desde)s AND k.registado_em <= %(ate)s AND k.removido_da_fonte_em IS NULL
          AND k.data_publicacao >= (%(desde)s::date - %(dias)s)
    """
    total = con.execute(f"SELECT count(DISTINCT k.id) AS n {base}", par).fetchone()["n"]
    linhas = con.execute(
        f"""
        WITH c AS (SELECT k.id, sum(r.pontos) AS pontos, array_agg(i.nome ORDER BY r.pontos DESC) AS sinais
                   {base.replace("WHERE", "JOIN indicador i ON i.id = r.indicador_id WHERE", 1)}
                   GROUP BY k.id ORDER BY sum(r.pontos) DESC LIMIT %(lim)s)
        SELECT k.id, k.objeto, k.procedimento, k.preco_contratual, k.data_publicacao, k.url_fonte,
               c.pontos, c.sinais, a.id AS adjudicante_id, a.nome AS adjudicante,
               (SELECT json_agg(json_build_object(
                    'id', CASE WHEN f.tipo_pessoa = ANY(%(tl)s) THEN f.id END,
                    'nome', CASE WHEN f.tipo_pessoa = ANY(%(tl)s) THEN f.nome ELSE 'pessoa singular (não identificada)' END))
                FROM contrato_adjudicatario ca JOIN entidade f ON f.id = ca.entidade_id
                WHERE ca.contrato_id = k.id) AS fornecedores
        FROM c JOIN contrato k ON k.id = c.id JOIN entidade a ON a.id = k.adjudicante_id
        ORDER BY c.pontos DESC, k.preco_contratual DESC NULLS LAST
        """, par).fetchall()
    return total, linhas


def _mudancas_nivel(con: psycopg.Connection, linha_base: bool) -> dict[str, Any]:
    """Compara os scores atuais com a fotografia do resumo anterior e atualiza a fotografia."""
    par = {"lim": LIMIAR_ALTO, "tl": list(TIPOS_LISTAVEIS), "max": MAX_ENTIDADES}
    sel = """
        SELECT r.papel, e.id, e.nome, r.score, s.score AS score_anterior, r.n_contratos
        FROM resumo_entidade r JOIN entidade e ON e.id = r.entidade_id
        LEFT JOIN meta.alerta_score s ON s.papel = r.papel AND s.entidade_id = r.entidade_id
        WHERE e.tipo_pessoa = ANY(%(tl)s) AND {cond}
        ORDER BY r.score DESC, r.n_contratos DESC
    """
    entraram, sairam = [], []
    if not linha_base:
        entraram = con.execute(sel.format(cond="r.score >= %(lim)s AND coalesce(s.score, 0) < %(lim)s"), par).fetchall()
        sairam = con.execute(
            "SELECT s.papel, e.id, e.nome, coalesce(r.score, 0) AS score, s.score AS score_anterior "
            "FROM meta.alerta_score s JOIN entidade e ON e.id = s.entidade_id "
            "LEFT JOIN resumo_entidade r ON r.papel = s.papel AND r.entidade_id = s.entidade_id "
            "WHERE s.score >= %(lim)s AND coalesce(r.score, 0) < %(lim)s AND e.tipo_pessoa = ANY(%(tl)s) "
            "ORDER BY s.score DESC", par).fetchall()
    con.execute("DELETE FROM meta.alerta_score")
    con.execute("INSERT INTO meta.alerta_score (papel, entidade_id, score) "
                "SELECT papel, entidade_id, score FROM resumo_entidade WHERE score > 0")
    for l in entraram + sairam:
        l["papel"] = PAPEIS[l["papel"]]
    return {"linha_base": linha_base, "entraram_alto": entraram[:MAX_ENTIDADES], "n_entraram_alto": len(entraram),
            "sairam_alto": sairam[:MAX_ENTIDADES], "n_sairam_alto": len(sairam)}


def _fontes(con: psycopg.Connection, desde: datetime, ate: datetime) -> dict[str, Any]:
    sinc = con.execute(
        "SELECT id, iniciado_em, estado, resumo->'falhas' AS falhas FROM meta.sincronizacao "
        "WHERE iniciado_em > %s AND iniciado_em <= %s ORDER BY id", (desde, ate)).fetchall()
    return {"sincronizacoes": len(sinc), "com_problemas": [s for s in sinc if s["estado"] in ("falhou", "parcial")]}


def _eur(v: Any) -> str:
    if v is None:
        return "—"
    return f"{Decimal(v):,.0f} €".replace(",", " ")


def para_markdown(c: dict[str, Any]) -> str:
    """Versão textual do resumo (pura; testada)."""
    u = c["url_base"]
    l = [f"# Resumo de alertas — {c['ate'][:10]}", "", f"> {AVISO}", "",
         f"Janela: {c['desde'][:16].replace('T', ' ')} → {c['ate'][:16].replace('T', ' ')} (UTC).", ""]
    l += [f"## Contratos novos com sinais de risco ({c['n_contratos']})", ""]
    if not c["contratos"]:
        l.append("Nenhum contrato novo com sinais nesta janela.")
    for k in c["contratos"]:
        forn = ", ".join(f["nome"] for f in (k["fornecedores"] or [])) or "—"
        l.append(f"- **{k['adjudicante']}** → {forn}: {(k['objeto'] or 'sem objeto')[:140]} · {_eur(k['preco_contratual'])} · "
                 f"{k['procedimento']} · sinais: {', '.join(k['sinais'])} ({k['pontos']} pontos) · "
                 f"[perfil]({u}/entidade/{k['adjudicante_id']})" + (f" · [BASE]({k['url_fonte']})" if k["url_fonte"] else ""))
    if c["n_contratos"] > len(c["contratos"]):
        l.append(f"- … e mais {c['n_contratos'] - len(c['contratos'])} (ver a lista na aplicação).")
    l += ["", "## Mudanças de nível de risco", ""]
    n = c["niveis"]
    if n["linha_base"]:
        l.append("Primeiro resumo: registada a linha de base dos scores; as mudanças aparecem a partir do próximo.")
    else:
        l.append(f"Entraram no nível alto (score ≥ {LIMIAR_ALTO}): {n['n_entraram_alto']}.")
        for e in n["entraram_alto"]:
            l.append(f"- {e['nome']} ({e['papel']}): {e['score_anterior'] or 0} → **{e['score']}** · [perfil]({u}/entidade/{e['id']})")
        l.append(f"Saíram do nível alto: {n['n_sairam_alto']}.")
        for e in n["sairam_alto"]:
            l.append(f"- {e['nome']} ({e['papel']}): {e['score_anterior']} → {e['score']}")
    l += ["", "## Fontes", ""]
    f = c["fontes"]
    l.append(f"{f['sincronizacoes']} sincronização(ões) na janela; com problemas: {len(f['com_problemas'])}.")
    for s in f["com_problemas"]:
        l.append(f"- #{s['id']} ({s['estado']}): falhas {s['falhas']}")
    l += ["", "---", "Metodologia e limites: " + u + "/metodologia"]
    return "\n".join(l)


def gerar(con: psycopg.Connection, agora: datetime | None = None) -> dict[str, Any]:
    """Gera e guarda um resumo em rascunho cobrindo o período desde o resumo anterior. Nunca envia nada."""
    ate = agora or datetime.now(timezone.utc)
    ant = con.execute("SELECT max(ate) AS ate FROM alerta_resumo").fetchone()["ate"]
    desde = ant or (ate - timedelta(days=1))
    n_contratos, contratos = _contratos_novos(con, desde, ate)
    conteudo = {"desde": desde.isoformat(), "ate": ate.isoformat(), "url_base": _url_base(),
                "n_contratos": n_contratos, "contratos": contratos, "niveis": _mudancas_nivel(con, linha_base=ant is None),
                "fontes": _fontes(con, desde, ate)}
    conteudo = json.loads(json.dumps(conteudo, default=str))
    texto = para_markdown(conteudo)
    rid = con.execute("INSERT INTO alerta_resumo (desde, ate, conteudo, texto) VALUES (%s, %s, %s, %s) RETURNING id",
                      (desde, ate, json.dumps(conteudo, ensure_ascii=False), texto)).fetchone()["id"]
    con.commit()
    return {"id": rid, "estado": "rascunho", "n_contratos": n_contratos,
            "n_entraram_alto": conteudo["niveis"]["n_entraram_alto"], "linha_base": conteudo["niveis"]["linha_base"]}
