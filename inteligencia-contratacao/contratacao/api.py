"""API (FastAPI) + página estática.

    uvicorn contratacao.api:app --reload

Linguagem: a API fala de "sinais de risco", nunca de culpa.
"""

from __future__ import annotations

import csv
import io
from datetime import date
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from . import bd
from .config import RAIZ
from .normalizacao import PROCEDIMENTOS
from .calibracao import MIN_CONTRATOS_ENTIDADE, Z_CONFIANCA, score_entidade
from .regras import INDICADORES

from contextlib import asynccontextmanager


@asynccontextmanager
async def _arranque(_app):
    bd.migrar()  # idempotente; garante o schema mesmo antes da primeira sincronização
    yield


app = FastAPI(title="Inteligência de Contratação Pública — sinais de risco", lifespan=_arranque)

ORDENACOES = {
    "total": "total",
    "n_contratos": "n_contratos",
    "score": "score",
    "valor_medio": "valor_medio",
    "pct_ajuste_direto": "pct_ajuste_direto",
    "n_sinais": "n_sinais",
    "pontos": "pontos",
}


@app.get("/", include_in_schema=False)
def pagina():
    return FileResponse(RAIZ / "static" / "index.html")


@app.get("/metodologia", include_in_schema=False)
def pagina_metodologia():
    return FileResponse(RAIZ / "static" / "metodologia.html")


@app.get("/qualidade", include_in_schema=False)
def pagina_qualidade():
    return FileResponse(RAIZ / "static" / "qualidade.html")


@app.get("/api/meta")
def meta():
    """Última verificação/atualização, cobertura histórica e indicadores ativos."""
    with bd.ligar() as con:
        sinc = con.execute(
            "SELECT id, iniciado_em, terminado_em, estado FROM meta.sincronizacao "
            "WHERE estado IN ('concluido','parcial') ORDER BY id DESC LIMIT 1").fetchone()
        ultima_alteracao = con.execute(
            "SELECT max(terminado_em) AS t FROM meta.execucao_ingestao "
            "WHERE estado = 'concluido' AND (inseridos > 0 OR atualizados > 0)").fetchone()["t"]
        cobertura = con.execute(
            "SELECT fonte, ano, estado, n_registos, versao_fonte, atualizado_em FROM meta.cobertura ORDER BY ano"
        ).fetchall()
    return {
        "ultima_verificacao": sinc["terminado_em"] if sinc else None,
        "estado_ultima_verificacao": sinc["estado"] if sinc else None,
        "ultima_atualizacao": ultima_alteracao,
        "cobertura": cobertura,
        "procedimentos": {k: v["rotulo"] for k, v in PROCEDIMENTOS.items()},
        "indicadores": [
            {"codigo": i.codigo, "nome": i.nome, "pontos": i.pontos, "descricao": i.descricao,
             "limites": i.limites, "referencia": i.referencia}
            for i in INDICADORES
        ],
    }


@app.get("/api/qualidade")
def qualidade():
    """Relatório de qualidade de dados: por ano (última carga), completude e histórico de execuções."""
    with bd.ligar() as con:
        cobertura = con.execute(
            """
            SELECT c.ano, c.estado, c.n_registos, c.versao_fonte, c.atualizado_em,
                   e.lidos, e.inseridos, e.atualizados, e.inalterados, e.rejeitados, e.terminado_em
            FROM meta.cobertura c LEFT JOIN meta.execucao_ingestao e ON e.id = c.execucao_id
            WHERE c.fonte = 'base_contratos' ORDER BY c.ano DESC
            """).fetchall()
        # problemas da execução mais recente que CARREGOU cada ano (a que está em vigor)
        problemas = con.execute(
            """
            WITH ult AS (
                SELECT DISTINCT ON (ano) id, ano FROM meta.execucao_ingestao
                WHERE estado = 'concluido' AND lidos > 0 ORDER BY ano, id DESC)
            SELECT u.ano, r.gravidade, r.tipo, r.n, r.exemplos
            FROM ult u JOIN meta.resumo_qualidade r ON r.execucao_id = u.id
            ORDER BY u.ano DESC, r.gravidade DESC, r.n DESC
            """).fetchall()
        completude = con.execute(
            """
            SELECT ano, count(*) AS n,
                   round(100.0 * count(*) FILTER (WHERE data_celebracao IS NULL) / count(*), 1) AS pct_sem_data_celebracao,
                   round(100.0 * count(*) FILTER (WHERE preco_contratual IS NULL OR preco_contratual <= 0) / count(*), 1) AS pct_sem_preco,
                   round(100.0 * count(*) FILTER (WHERE cpv IS NULL) / count(*), 1) AS pct_sem_cpv,
                   round(100.0 * count(*) FILTER (WHERE distrito IS NULL) / count(*), 1) AS pct_sem_distrito,
                   round(100.0 * count(*) FILTER (WHERE n_concorrentes IS NULL AND procedimento IN
                        (SELECT unnest(%(comp)s::text[]))) / nullif(count(*) FILTER (WHERE procedimento IN
                        (SELECT unnest(%(comp)s::text[]))), 0), 1) AS pct_concorrentes_desconhecidos_concorrenciais,
                   count(*) FILTER (WHERE removido_da_fonte_em IS NOT NULL) AS removidos_da_fonte
            FROM contrato GROUP BY ano ORDER BY ano DESC
            """, {"comp": [k for k, v in PROCEDIMENTOS.items() if v["competitivo"]]}).fetchall()
        sincronizacoes = con.execute(
            "SELECT id, iniciado_em, terminado_em, estado, plano, resumo->'falhas' AS falhas, "
            "resumo->'adiados' AS adiados, erro FROM meta.sincronizacao ORDER BY id DESC LIMIT 15").fetchall()
        execucoes = con.execute(
            "SELECT id, ano, estado, iniciado_em, terminado_em, lidos, inseridos, atualizados, inalterados, "
            "rejeitados, erro FROM meta.execucao_ingestao ORDER BY id DESC LIMIT 30").fetchall()
    return {"cobertura": cobertura, "problemas": problemas, "completude": completude,
            "sincronizacoes": sincronizacoes, "execucoes": execucoes}


def _filtros(papel, de, ate, distrito, procedimento, cpv, valor_min, valor_max):
    cond, par = ["TRUE"], {}
    if de:
        cond.append("k.data_referencia >= %(de)s"); par["de"] = de
    if ate:
        cond.append("k.data_referencia <= %(ate)s"); par["ate"] = ate
    if distrito:
        cond.append("k.distrito = %(distrito)s"); par["distrito"] = distrito
    if procedimento:
        cond.append("k.procedimento = ANY(%(proc)s)"); par["proc"] = procedimento
    if cpv:
        cond.append("k.cpv LIKE %(cpv)s"); par["cpv"] = cpv.rstrip("0") + "%"
    if valor_min is not None:
        cond.append("k.preco_contratual >= %(vmin)s"); par["vmin"] = valor_min
    if valor_max is not None:
        cond.append("k.preco_contratual <= %(vmax)s"); par["vmax"] = valor_max
    return " AND ".join(cond), par


@app.get("/api/entidades")
def entidades(
    papel: Literal["adjudicante", "fornecedor"] = "fornecedor",
    ordenar: str = "total",
    direcao: Literal["asc", "desc"] = "desc",
    de: date | None = None,
    ate: date | None = None,
    distrito: str | None = None,
    procedimento: list[str] | None = Query(None),
    cpv: str | None = None,
    valor_min: float | None = None,
    valor_max: float | None = None,
    score_min: int | None = None,
    q: str | None = None,
    limite: int = Query(50, le=1000),
    pagina: int = 0,
    formato: Literal["json", "csv"] = "json",
):
    """Lista de entidades agregada sobre os contratos que passam nos filtros.

    Papel 'adjudicante' agrega pelo comprador público; 'fornecedor' pelos
    adjudicatários (um contrato com N adjudicatários conta para cada um; o valor
    não é dividido — limitação documentada).
    """
    if ordenar not in ORDENACOES:
        raise HTTPException(400, f"ordenar deve ser um de {list(ORDENACOES)}")
    where, par = _filtros(papel, de, ate, distrito, procedimento, cpv, valor_min, valor_max)
    juncao = (
        "JOIN entidade e ON e.id = k.adjudicante_id"
        if papel == "adjudicante"
        else "JOIN contrato_adjudicatario ca ON ca.contrato_id = k.id JOIN entidade e ON e.id = ca.entidade_id"
    )
    if q:
        where += " AND (e.nome ILIKE %(q)s OR e.nif = %(qnif)s)"; par["q"] = f"%{q}%"; par["qnif"] = q
    filtro_score = ""
    if score_min is not None:
        filtro_score = "WHERE coalesce(f.score, 0) >= %(smin)s"; par["smin"] = score_min
    par.update(nmin=MIN_CONTRATOS_ENTIDADE, z=Z_CONFIANCA, n_ind=len(INDICADORES))
    sql = f"""
        WITH base AS (
            SELECT e.id AS eid, k.id AS kid, k.preco_contratual, k.procedimento
            FROM contrato k {juncao}
            WHERE {where}
        ),
        ag AS (
            SELECT eid, count(*) AS n_contratos,
                   coalesce(sum(preco_contratual), 0) AS total,
                   round(avg(preco_contratual), 2) AS valor_medio,
                   round(100.0 * count(*) FILTER (WHERE procedimento IN ('ajuste_direto','ajuste_direto_simplificado')) / count(*), 1) AS pct_ajuste_direto
            FROM base GROUP BY eid
        ),
        ind AS (  -- por entidade x indicador: O observados, E esperados, N avaliáveis
            SELECT b.eid, r.indicador,
                   count(*) FILTER (WHERE r.estado = 'sinal') AS o,
                   sum(r.taxa_referencia) AS e,
                   count(*) AS n,
                   sum(r.pontos) AS pontos
            FROM base b JOIN avaliacao_risco r ON r.contrato_id = b.kid
            WHERE r.estado IN ('sinal', 'sem_sinal') AND r.taxa_referencia IS NOT NULL
            GROUP BY 1, 2
        ),
        wl AS (  -- limite inferior de Wilson (igual a calibracao.wilson_inferior)
            SELECT *, e / n AS p_esp,
                   ((o::numeric / n) + %(z)s ^ 2 / (2 * n)
                     - %(z)s * sqrt((o::numeric / n) * (1 - o::numeric / n) / n + %(z)s ^ 2 / (4 * n * n)))
                   / (1 + %(z)s ^ 2 / n) AS l
            FROM ind
        ),
        sc AS (  -- mesma fórmula de calibracao.score_entidade (testada para equivalência)
            SELECT eid, o, e, n, pontos,
                   CASE WHEN n < %(nmin)s OR l <= p_esp THEN 0
                        WHEN p_esp >= 1 THEN 100
                        ELSE least(100, round(100 * (l - p_esp) / (1 - p_esp))) END AS score
            FROM wl
        ),
        f AS (
            SELECT eid, round(sum(score)::numeric / %(n_ind)s) AS score, sum(o) AS n_sinais,
                   round(sum(e), 1) AS esperados, sum(n) AS n_avaliaveis, sum(pontos) AS pontos
            FROM sc GROUP BY eid
        )
        SELECT e.id, e.nif, e.identificado_por, e.nome, ag.n_contratos, ag.total, ag.valor_medio,
               ag.pct_ajuste_direto, coalesce(f.score, 0) AS score, coalesce(f.n_sinais, 0) AS n_sinais,
               coalesce(f.esperados, 0) AS esperados, coalesce(f.n_avaliaveis, 0) AS n_avaliaveis,
               coalesce(f.pontos, 0) AS pontos
        FROM ag JOIN entidade e ON e.id = ag.eid LEFT JOIN f ON f.eid = ag.eid
        {filtro_score}
        ORDER BY {ORDENACOES[ordenar]} {direcao} NULLS LAST, e.id
        LIMIT %(lim)s OFFSET %(off)s
    """
    par.update(lim=limite if formato == "json" else 100000, off=pagina * limite if formato == "json" else 0)
    with bd.ligar() as con:
        linhas = con.execute(sql, par).fetchall()
    if formato == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()) if linhas else ["vazio"])
        w.writeheader(); w.writerows(linhas)
        buf.seek(0)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename=entidades_{papel}.csv"})
    return {"papel": papel, "resultados": linhas}


@app.get("/api/entidades/{entidade_id}/risco")
def risco_entidade(entidade_id: int, papel: Literal["adjudicante", "fornecedor"] = "fornecedor"):
    """Explicação do score de uma entidade, indicador a indicador."""
    juncao = (
        "k.adjudicante_id = %(e)s" if papel == "adjudicante"
        else "EXISTS (SELECT 1 FROM contrato_adjudicatario ca WHERE ca.contrato_id = k.id AND ca.entidade_id = %(e)s)"
    )
    with bd.ligar() as con:
        ent = con.execute("SELECT id, nif, identificado_por, nome FROM entidade WHERE id = %(e)s", {"e": entidade_id}).fetchone()
        if not ent:
            raise HTTPException(404, "entidade não encontrada")
        linhas = con.execute(
            f"""
            SELECT r.indicador, count(*) FILTER (WHERE r.estado = 'sinal') AS o,
                   coalesce(sum(r.taxa_referencia), 0) AS e,
                   count(*) AS n
            FROM contrato k JOIN avaliacao_risco r ON r.contrato_id = k.id
            WHERE {juncao} AND r.estado IN ('sinal', 'sem_sinal') AND r.taxa_referencia IS NOT NULL
            GROUP BY 1
            """,
            {"e": entidade_id},
        ).fetchall()
    por_ind = {l["indicador"]: l for l in linhas}
    detalhe = []
    for ind in INDICADORES:
        l = por_ind.get(ind.codigo, {"o": 0, "e": 0, "n": 0})
        s = score_entidade(l["o"], float(l["e"]), l["n"])
        detalhe.append({"indicador": ind.codigo, "nome": ind.nome, "score": s.score, "observados": s.observados,
                        "esperados": round(s.esperados, 1), "avaliaveis": s.avaliaveis, "explicacao": s.motivo})
    return {"entidade": ent, "papel": papel,
            "score": round(sum(d["score"] for d in detalhe) / len(INDICADORES)), "indicadores": detalhe}


@app.get("/api/contratos")
def contratos(
    entidade_id: int | None = None,
    sinal: str | None = None,
    limite: int = Query(50, le=500),
    pagina: int = 0,
):
    cond, par = ["TRUE"], {}
    if entidade_id:
        cond.append("(k.adjudicante_id = %(e)s OR EXISTS (SELECT 1 FROM contrato_adjudicatario ca WHERE ca.contrato_id=k.id AND ca.entidade_id=%(e)s))")
        par["e"] = entidade_id
    if sinal:
        cond.append("EXISTS (SELECT 1 FROM avaliacao_risco r WHERE r.contrato_id=k.id AND r.indicador=%(s)s AND r.estado='sinal')")
        par["s"] = sinal
    par.update(lim=limite, off=pagina * limite)
    with bd.ligar() as con:
        linhas = con.execute(
            f"""
            SELECT k.id, k.id_origem, k.objeto, k.procedimento, k.data_celebracao, k.data_publicacao, k.removido_da_fonte_em, k.preco_contratual,
                   k.n_concorrentes, k.distrito, k.url_fonte, a.nome AS adjudicante,
                   (SELECT json_agg(json_build_object('indicador', r.indicador, 'pontos', r.pontos,
                           'explicacao', r.explicacao)) FROM avaliacao_risco r
                    WHERE r.contrato_id = k.id AND r.estado='sinal') AS sinais
            FROM contrato k JOIN entidade a ON a.id = k.adjudicante_id
            WHERE {' AND '.join(cond)}
            ORDER BY k.data_referencia DESC NULLS LAST LIMIT %(lim)s OFFSET %(off)s
            """,
            par,
        ).fetchall()
    return {"resultados": linhas}


@app.get("/api/distritos")
def distritos():
    with bd.ligar() as con:
        return [r["distrito"] for r in con.execute(
            "SELECT DISTINCT distrito FROM contrato WHERE distrito IS NOT NULL ORDER BY 1").fetchall()]
