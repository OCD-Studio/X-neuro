"""API (FastAPI) + página estática.

    uvicorn contratacao.api:app --reload

Linguagem: a API fala de "sinais de risco", nunca de culpa.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import bd
from .config import RAIZ
from .normalizacao import PROCEDIMENTOS
from .calibracao import MIN_CONTRATOS_ENTIDADE, Z_CONFIANCA, score_entidade
from .consultas import sql_score
from .regras import TODOS

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


app.mount("/static", StaticFiles(directory=RAIZ / "static"), name="static")


@app.get("/", include_in_schema=False)
def pagina():
    return FileResponse(RAIZ / "static" / "index.html")


@app.get("/metodologia", include_in_schema=False)
def pagina_metodologia():
    return FileResponse(RAIZ / "static" / "metodologia.html")


@app.get("/entidade/{entidade_id}", include_in_schema=False)
def pagina_entidade(entidade_id: int):
    return FileResponse(RAIZ / "static" / "entidade.html")


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
        nao_listados = con.execute(
            "SELECT count(*) AS n FROM resumo_entidade r JOIN entidade e ON e.id = r.entidade_id "
            "WHERE r.papel = 'f' AND e.tipo_pessoa NOT IN ('coletiva', 'coletiva_sem_nif')").fetchone()["n"]
        cobertura = con.execute(
            "SELECT fonte, ano, estado, n_registos, versao_fonte, atualizado_em FROM meta.cobertura "
            "WHERE fonte = 'base_contratos' ORDER BY ano"
        ).fetchall()
    return {
        "ultima_verificacao": sinc["terminado_em"] if sinc else None,
        "estado_ultima_verificacao": sinc["estado"] if sinc else None,
        "ultima_atualizacao": ultima_alteracao,
        "cobertura": cobertura,
        "fornecedores_nao_listados": nao_listados,
        "procedimentos": {k: v["rotulo"] for k, v in PROCEDIMENTOS.items()},
        "indicadores": [
            {"codigo": i.codigo, "nome": i.nome, "pontos": i.pontos, "descricao": i.descricao,
             "limites": i.limites, "referencia": i.referencia, "parametros": i.parametros,
             "tipo": "conjunto" if hasattr(i, "sql") else "contrato"}
            for i in TODOS
        ],
        "indicadores_bloqueados": [
            {"nome": "Empresas recém-criadas a ganhar contratos grandes",
             "motivo": "Precisa da data de constituição (registo comercial), ainda sem fonte aberta. "
                       "Substituído provisoriamente por “Fornecedor sem histórico ganha contrato grande”."},
            {"nome": "Ligações societárias entre fornecedores (moradas/administradores comuns)",
             "motivo": "Precisa do registo comercial (Fases 4–5)."},
        ],
        "niveis": NIVEIS,
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
        cobertura_anuncios = con.execute(
            "SELECT ano, estado, n_registos, versao_fonte FROM meta.cobertura WHERE fonte = 'base_anuncios' ORDER BY ano DESC"
        ).fetchall()
        # problemas da execução mais recente que CARREGOU cada ano (a que está em vigor)
        problemas = con.execute(
            """
            WITH ult AS (
                SELECT DISTINCT ON (ano) id, ano FROM meta.execucao_ingestao
                WHERE estado = 'concluido' AND lidos > 0 AND fonte = 'base_contratos' ORDER BY ano, id DESC)
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
    return {"cobertura": cobertura, "cobertura_anuncios": cobertura_anuncios, "problemas": problemas, "completude": completude,
            "sincronizacoes": sincronizacoes, "execucoes": execucoes}


NIVEIS = {"baixo": (0, 19), "medio": (20, 49), "alto": (50, 100)}

# Minimização de dados pessoais: só pessoas coletivas aparecem em listas e explicações (ver sql/010).
TIPOS_LISTAVEIS = ("coletiva", "coletiva_sem_nif")
_SQL_LISTAVEL = "e.tipo_pessoa IN ('coletiva', 'coletiva_sem_nif')"
_MSG_PROTEGIDO = ("Não disponível: a entidade parece ser uma pessoa singular (a fonte não publica o NIF). "
                  "Por minimização de dados pessoais não é listada nem perfilada.")


def _verificar_listavel(con, entidade_id: int) -> dict:
    ent = con.execute("SELECT id, nif, identificado_por, nome, tipo_pessoa, nome_de_pessoa FROM entidade WHERE id = %s",
                      (entidade_id,)).fetchone()
    if not ent:
        raise HTTPException(404, "entidade não encontrada")
    if ent["tipo_pessoa"] not in TIPOS_LISTAVEIS:
        raise HTTPException(404, _MSG_PROTEGIDO)
    return ent


@dataclass
class Filtros:
    papel: str
    de: date | None = None
    ate: date | None = None
    distrito: str | None = None
    procedimento: list[str] | None = None
    cpv: str | None = None
    valor_min: float | None = None
    valor_max: float | None = None

    @property
    def rapido(self) -> bool:
        """Pode usar os agregados por ano? (só filtros por anos completos)"""
        return (not (self.distrito or self.procedimento or self.cpv)
                and self.valor_min is None and self.valor_max is None
                and (self.de is None or (self.de.month, self.de.day) == (1, 1))
                and (self.ate is None or (self.ate.month, self.ate.day) == (12, 31)))

    @property
    def p(self) -> str:
        return "a" if self.papel == "adjudicante" else "f"


def _sql_base(f: Filtros) -> tuple[str, str, dict]:
    """Devolve (cte_agregados, cte_indicadores, parametros): por entidade, totais e (O, E, N) por indicador."""
    par: dict = {"papel": f.p}
    if f.rapido:
        cond = ["papel = %(papel)s"]
        if f.de:
            cond.append("ano >= %(ano_de)s"); par["ano_de"] = f.de.year
        if f.ate:
            cond.append("ano <= %(ano_ate)s"); par["ano_ate"] = f.ate.year
        w = " AND ".join(cond)
        ag = f"""SELECT entidade_id AS eid, sum(n_contratos)::bigint AS n_contratos, sum(total) AS total,
                        sum(n_ajuste_direto)::bigint AS n_ad
                 FROM agregado_entidade_ano WHERE {w} GROUP BY 1"""
        ind = f"""SELECT entidade_id AS eid, indicador_id, sum(o)::bigint AS o, sum(e)::float8 AS e,
                         sum(n)::bigint AS n, sum(pontos)::bigint AS pontos
                  FROM agregado_risco_ano WHERE {w} GROUP BY 1, 2"""
        return ag, ind, par
    cond = ["k.data_referencia IS NOT NULL"]
    if f.de:
        cond.append("k.data_referencia >= %(de)s"); par["de"] = f.de
    if f.ate:
        cond.append("k.data_referencia <= %(ate)s"); par["ate"] = f.ate
    if f.distrito:
        cond.append("k.distrito = %(distrito)s"); par["distrito"] = f.distrito
    if f.procedimento:
        cond.append("k.procedimento = ANY(%(proc)s)"); par["proc"] = f.procedimento
    if f.cpv:
        cond.append("k.cpv LIKE %(cpv)s"); par["cpv"] = f.cpv.strip() + "%"
    if f.valor_min is not None:
        cond.append("k.preco_contratual >= %(vmin)s"); par["vmin"] = f.valor_min
    if f.valor_max is not None:
        cond.append("k.preco_contratual <= %(vmax)s"); par["vmax"] = f.valor_max
    juncao = ("SELECT k.adjudicante_id AS eid, k.id AS kid, k.preco_contratual, k.procedimento FROM contrato k"
              if f.papel == "adjudicante" else
              "SELECT ca.entidade_id AS eid, k.id AS kid, k.preco_contratual, k.procedimento "
              "FROM contrato k JOIN contrato_adjudicatario ca ON ca.contrato_id = k.id")
    ag = f"""SELECT eid, count(*) AS n_contratos, coalesce(sum(preco_contratual), 0) AS total,
                    count(*) FILTER (WHERE procedimento IN ('ajuste_direto','ajuste_direto_simplificado')) AS n_ad
             FROM ({juncao} WHERE {' AND '.join(cond)}) b GROUP BY 1"""
    ind = f"""SELECT b.eid, a.indicador_id, count(*) FILTER (WHERE a.estado = 's') AS o, sum(r.taxa)::float8 AS e,
                     count(*) AS n, sum(a.pontos) AS pontos
              FROM ({juncao} WHERE {' AND '.join(cond)}) b
              JOIN avaliacao_risco a ON a.contrato_id = b.kid AND a.estado IN ('s', 'n')
              JOIN referencia_risco r ON r.id = a.ref_id
              GROUP BY 1, 2"""
    return ag, ind, par


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
    nivel: Literal["baixo", "medio", "alto"] | None = None,
    q: str | None = None,
    limite: int = Query(50, le=1000),
    pagina: int = 0,
    formato: Literal["json", "csv"] = "json",
):
    """Lista de entidades agregada sobre os contratos que passam nos filtros.

    Papel 'adjudicante' agrega pelo comprador público; 'fornecedor' pelos adjudicatários
    (um contrato com N adjudicatários conta para cada um; o valor não é dividido).
    """
    if ordenar not in ORDENACOES:
        raise HTTPException(400, f"ordenar deve ser um de {list(ORDENACOES)}")
    fl = Filtros(papel, de, ate, distrito, procedimento, cpv, valor_min, valor_max)
    ag, ind, par = _sql_base(fl)
    par.update(z=Z_CONFIANCA, nmin=MIN_CONTRATOS_ENTIDADE)
    historico_completo = fl.rapido and de is None and ate is None
    cond = [_SQL_LISTAVEL]
    if q:
        cond.append("(e.nome ILIKE %(q)s OR e.nif = %(qnif)s)"); par["q"] = f"%{q}%"; par["qnif"] = q.strip()
    if score_min is not None:
        cond.append("coalesce(f.score, 0) >= %(smin)s"); par["smin"] = score_min
    if nivel:
        cond.append("coalesce(f.score, 0) BETWEEN %(nmin_s)s AND %(nmax_s)s")
        par["nmin_s"], par["nmax_s"] = NIVEIS[nivel]
    par.update(lim=limite if formato == "json" else 100000, off=pagina * limite if formato == "json" else 0)
    if historico_completo:
        # tudo pré-calculado na pontuação (resumo_entidade): leitura direta
        sql = f"""
        WITH f AS (SELECT entidade_id AS eid, * FROM resumo_entidade WHERE papel = %(papel)s),
             ag AS (SELECT eid, n_contratos, total, n_ajuste_direto AS n_ad FROM f)
        SELECT e.id, e.nif, e.identificado_por, e.tipo_pessoa, e.nome_de_pessoa, e.nome, ag.n_contratos, ag.total,
               round(ag.total / nullif(ag.n_contratos, 0), 2) AS valor_medio,
               round(100.0 * ag.n_ad / nullif(ag.n_contratos, 0), 1) AS pct_ajuste_direto,
               f.score, f.n_indicadores, f.n_sinais, f.esperados, f.n_avaliaveis, f.pontos
        FROM ag JOIN entidade e ON e.id = ag.eid JOIN f ON f.eid = ag.eid
        WHERE {' AND '.join(cond)}
        ORDER BY {ORDENACOES[ordenar]} {direcao} NULLS LAST, e.id
        LIMIT %(lim)s OFFSET %(off)s
        """
    else:
        sql = f"""
        WITH ag AS ({ag}), ind AS ({ind}), {sql_score()}
        SELECT e.id, e.nif, e.identificado_por, e.tipo_pessoa, e.nome_de_pessoa, e.nome, ag.n_contratos, ag.total,
               round(ag.total / nullif(ag.n_contratos, 0), 2) AS valor_medio,
               round(100.0 * ag.n_ad / nullif(ag.n_contratos, 0), 1) AS pct_ajuste_direto,
               coalesce(f.score, 0) AS score, coalesce(f.n_indicadores, 0) AS n_indicadores,
               coalesce(f.n_sinais, 0) AS n_sinais, coalesce(f.esperados, 0) AS esperados,
               coalesce(f.n_avaliaveis, 0) AS n_avaliaveis, coalesce(f.pontos, 0) AS pontos
        FROM ag JOIN entidade e ON e.id = ag.eid LEFT JOIN f ON f.eid = ag.eid
        WHERE {' AND '.join(cond)}
        ORDER BY {ORDENACOES[ordenar]} {direcao} NULLS LAST, e.id
        LIMIT %(lim)s OFFSET %(off)s
    """
    with bd.ligar() as con:
        linhas = con.execute(sql, par).fetchall()
    if formato == "csv":
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(linhas[0].keys()) if linhas else ["vazio"])
        w.writeheader(); w.writerows(linhas)
        return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                                 headers={"Content-Disposition": f"attachment; filename=entidades_{papel}.csv"})
    caminho = "resumo" if historico_completo else ("agregados" if fl.rapido else "contratos")
    return {"papel": papel, "caminho": caminho, "resultados": linhas}


@app.get("/api/entidades/{entidade_id}/risco")
def risco_entidade(
    entidade_id: int,
    papel: Literal["adjudicante", "fornecedor"] = "fornecedor",
    de: date | None = None,
    ate: date | None = None,
    distrito: str | None = None,
    procedimento: list[str] | None = Query(None),
    cpv: str | None = None,
    valor_min: float | None = None,
    valor_max: float | None = None,
):
    """Explicação do score de uma entidade, indicador a indicador (mesmos filtros que a lista)."""
    fl = Filtros(papel, de, ate, distrito, procedimento, cpv, valor_min, valor_max)
    _, ind, par = _sql_base(fl)
    par["eid"] = entidade_id
    with bd.ligar() as con:
        ent = _verificar_listavel(con, entidade_id)
        linhas = con.execute(
            f"SELECT i.codigo, x.o, x.e, x.n FROM ({ind}) x JOIN indicador i ON i.id = x.indicador_id "
            f"WHERE x.eid = %(eid)s", par).fetchall()
    por_ind = {l["codigo"]: l for l in linhas}
    detalhe = []
    for i in TODOS:
        l = por_ind.get(i.codigo, {"o": 0, "e": 0.0, "n": 0})
        s = score_entidade(l["o"], float(l["e"]), l["n"])
        detalhe.append({"indicador": i.codigo, "nome": i.nome, "score": s.score, "observados": s.observados,
                        "esperados": round(s.esperados, 1), "avaliaveis": s.avaliaveis,
                        "conta_para_media": s.avaliaveis >= MIN_CONTRATOS_ENTIDADE, "explicacao": s.motivo})
    validos = [d["score"] for d in detalhe if d["conta_para_media"]]
    score = int((Decimal(sum(validos)) / len(validos)).quantize(Decimal("1"), ROUND_HALF_UP)) if validos else 0
    return {"entidade": ent, "papel": papel, "score": score, "n_indicadores": len(validos),
            "indicadores": sorted(detalhe, key=lambda d: -d["score"])}


@app.get("/api/contratos")
def contratos(
    entidade_id: int | None = None,
    papel: Literal["adjudicante", "fornecedor"] | None = None,
    sinal: str | None = None,
    limite: int = Query(50, le=500),
    pagina: int = 0,
):
    cond, par = ["TRUE"], {}
    if entidade_id:
        with bd.ligar() as con:
            _verificar_listavel(con, entidade_id)
        if papel == "adjudicante":
            cond.append("k.adjudicante_id = %(e)s")
        elif papel == "fornecedor":
            cond.append("EXISTS (SELECT 1 FROM contrato_adjudicatario ca WHERE ca.contrato_id=k.id AND ca.entidade_id=%(e)s)")
        else:
            cond.append("(k.adjudicante_id = %(e)s OR EXISTS (SELECT 1 FROM contrato_adjudicatario ca "
                        "WHERE ca.contrato_id=k.id AND ca.entidade_id=%(e)s))")
        par["e"] = entidade_id
    if sinal:
        cond.append("EXISTS (SELECT 1 FROM avaliacao_risco a JOIN indicador i ON i.id = a.indicador_id "
                    "WHERE a.contrato_id=k.id AND i.codigo=%(s)s AND a.estado='s')")
        par["s"] = sinal
    par.update(lim=limite, off=pagina * limite)
    with bd.ligar() as con:
        linhas = con.execute(
            f"""
            SELECT k.id, k.id_origem, k.objeto, k.procedimento, k.data_celebracao, k.data_publicacao,
                   k.removido_da_fonte_em, k.preco_contratual, k.n_concorrentes, k.distrito, k.url_fonte,
                   a.nome AS adjudicante,
                   (SELECT json_agg(json_build_object(
                        'indicador', i.codigo, 'nome', i.nome, 'pontos', r.pontos, 'pontos_base', i.pontos,
                        'explicacao', d.explicacao, 'evidencia', d.evidencia, 'taxa_referencia', round(rf.taxa::numeric, 4),
                        'referencia', CASE rf.nivel
                            WHEN 'ano+procedimento+cpv' THEN format('mesmo procedimento, divisão CPV %%s e ano %%s (%%s contratos)', rf.cpv_divisao, rf.ano, rf.n_avaliaveis)
                            WHEN 'ano+procedimento' THEN format('mesmo procedimento e ano %%s (%%s contratos)', rf.ano, rf.n_avaliaveis)
                            WHEN 'procedimento' THEN format('mesmo procedimento, todos os anos (%%s contratos)', rf.n_avaliaveis) END)
                        ORDER BY r.pontos DESC)
                    FROM avaliacao_risco r JOIN indicador i ON i.id = r.indicador_id
                    LEFT JOIN avaliacao_detalhe d ON d.contrato_id = r.contrato_id AND d.indicador_id = r.indicador_id
                    LEFT JOIN referencia_risco rf ON rf.id = r.ref_id
                    WHERE r.contrato_id = k.id AND r.estado = 's') AS sinais
            FROM contrato k JOIN entidade a ON a.id = k.adjudicante_id
            WHERE {' AND '.join(cond)}
            ORDER BY k.data_referencia DESC NULLS LAST LIMIT %(lim)s OFFSET %(off)s
            """,
            par,
        ).fetchall()
    return {"resultados": linhas}


PAPEIS = {"a": "adjudicante", "f": "fornecedor"}


@app.get("/api/entidades/{entidade_id}")
def perfil_entidade(entidade_id: int):
    """Perfil: identificação, variantes de nome, e por papel (adjudicante/fornecedor) totais, evolução anual,
    procedimentos e contrapartes principais. Só para entidades listáveis (minimização de dados pessoais)."""
    with bd.ligar() as con:
        ent = _verificar_listavel(con, entidade_id)
        nomes = con.execute(
            "SELECT nome, fonte, visto_em FROM entidade_nome WHERE entidade_id = %s ORDER BY nome", (entidade_id,)
        ).fetchall()
        datas = con.execute("SELECT primeiro_visto, ultimo_visto FROM entidade WHERE id = %s", (entidade_id,)).fetchone()
        papeis = {}
        for p, nome_papel in PAPEIS.items():
            resumo = con.execute(
                "SELECT n_contratos, total, n_ajuste_direto, score, n_indicadores, n_sinais, esperados, n_avaliaveis "
                "FROM resumo_entidade WHERE papel = %s AND entidade_id = %s", (p, entidade_id)).fetchone()
            if not resumo:
                continue
            anos = con.execute(
                "SELECT ano, n_contratos, total, n_ajuste_direto FROM agregado_entidade_ano "
                "WHERE papel = %s AND entidade_id = %s ORDER BY ano", (p, entidade_id)).fetchall()
            filtro = ("k.adjudicante_id = %(e)s" if p == "a" else
                      "k.id IN (SELECT contrato_id FROM contrato_adjudicatario WHERE entidade_id = %(e)s)")
            procedimentos = con.execute(
                f"SELECT k.procedimento, count(*) AS n, coalesce(sum(k.preco_contratual), 0) AS total "
                f"FROM contrato k WHERE {filtro} GROUP BY 1 ORDER BY n DESC", {"e": entidade_id}).fetchall()
            # contrapartes: relações correntes 'adjudicou_a'; pessoas singulares agregadas numa só linha
            lado, outro = ("origem_id", "destino_id") if p == "a" else ("destino_id", "origem_id")
            contrapartes = con.execute(
                f"""
                SELECT CASE WHEN e.tipo_pessoa IN ('coletiva', 'coletiva_sem_nif') THEN e.id END AS id,
                       CASE WHEN e.tipo_pessoa IN ('coletiva', 'coletiva_sem_nif') THEN e.nome
                            ELSE 'Pessoas singulares (não identificadas)' END AS nome,
                       sum((r.detalhe->>'n_contratos')::int) AS n_contratos,
                       sum((r.detalhe->>'total')::numeric) AS total,
                       min(r.valido_de) AS desde, max(r.valido_ate) AS ate,
                       bool_or(r.valido_ate >= current_date) AS atual,
                       count(*) AS n_entidades
                FROM relacao r JOIN entidade e ON e.id = r.{outro}
                WHERE r.{lado} = %(e)s AND r.tipo = 'adjudicou_a' AND r.substituido_em IS NULL
                GROUP BY 1, 2 ORDER BY total DESC NULLS LAST LIMIT 15
                """, {"e": entidade_id}).fetchall()
            papeis[nome_papel] = {"resumo": resumo, "anos": anos, "procedimentos": procedimentos,
                                  "contrapartes": contrapartes}
    return {"entidade": ent, "nomes": nomes, "periodo": datas, "papeis": papeis,
            "procedimentos_rotulos": {k: v["rotulo"] for k, v in PROCEDIMENTOS.items()}}


TIPOS_RELACAO = {
    "adjudicou_a": "adjudicou a",
    "concorreram_juntos": "concorreram juntos",
    "possivelmente_mesma_entidade": "possivelmente a mesma entidade",
}


@app.get("/api/grafo")
def grafo_entidade(
    entidade_id: int,
    de: date | None = None,
    ate: date | None = None,
    estado: Literal["todas", "atuais", "passadas"] = "todas",
    inferidas: bool = True,
    max_arestas: int = Query(40, le=200),
):
    """Rede de 1.º grau de uma entidade, com temporalidade (atual/passada) e natureza (documentada/inferida).
    Vizinhos que parecem pessoas singulares são agregados num nó anónimo por tipo de relação."""
    cond = ["r.substituido_em IS NULL"]
    par: dict = {"e": entidade_id, "lim": max_arestas}
    if de:  # sobreposição de períodos
        cond.append("(r.valido_ate IS NULL OR r.valido_ate >= %(de)s)"); par["de"] = de
    if ate:
        cond.append("(r.valido_de IS NULL OR r.valido_de <= %(ate)s)"); par["ate"] = ate
    if estado == "atuais":
        cond.append("(r.valido_ate IS NULL OR r.valido_ate >= current_date)")
    elif estado == "passadas":
        cond.append("r.valido_ate < current_date")
    if not inferidas:
        cond.append("r.natureza = 'documentada'")
    with bd.ligar() as con:
        centro = _verificar_listavel(con, entidade_id)
        linhas = con.execute(
            f"""
            WITH r AS (  -- dois ramos (saída/entrada) para usar os índices por origem e por destino
                SELECT r.*, r.destino_id AS vizinho, (r.valido_ate IS NULL OR r.valido_ate >= current_date) AS atual
                FROM relacao r WHERE r.origem_id = %(e)s AND {' AND '.join(cond)}
                UNION ALL
                SELECT r.*, r.origem_id AS vizinho, (r.valido_ate IS NULL OR r.valido_ate >= current_date) AS atual
                FROM relacao r WHERE r.destino_id = %(e)s AND r.origem_id <> %(e)s AND {' AND '.join(cond)}
            )
            SELECT r.id, r.tipo, r.natureza, r.confianca, r.valido_de, r.valido_ate, r.atual, r.fonte, r.detalhe,
                   r.origem_id = %(e)s AS saida, r.vizinho, v.nome, v.tipo_pessoa, v.nif,
                   v.tipo_pessoa IN ('coletiva', 'coletiva_sem_nif') AS listavel,
                   (SELECT count(*) FROM relacao x WHERE x.origem_id = r.origem_id AND x.destino_id = r.destino_id
                      AND x.tipo = r.tipo AND x.substituido_em IS NOT NULL) AS versoes_anteriores,
                   ra.score AS score_adj, rf.score AS score_forn
            FROM r JOIN entidade v ON v.id = r.vizinho
            LEFT JOIN resumo_entidade ra ON ra.papel = 'a' AND ra.entidade_id = r.vizinho
            LEFT JOIN resumo_entidade rf ON rf.papel = 'f' AND rf.entidade_id = r.vizinho
            ORDER BY r.atual DESC, (r.tipo = 'adjudicou_a') DESC,
                     (r.detalhe->>'total')::numeric DESC NULLS LAST, (r.detalhe->>'n_procedimentos')::int DESC NULLS LAST
            """, par).fetchall()
    nos = {entidade_id: {"id": str(entidade_id), "nome": centro["nome"], "papel": "centro", "listavel": True}}
    arestas, anonimos, omitidas = [], {}, 0
    for l in linhas:
        if l["listavel"]:
            if len(arestas) >= max_arestas:
                omitidas += 1
                continue
            vid = str(l["vizinho"])
            papel = ("fornecedor" if (l["tipo"] == "adjudicou_a" and l["saida"]) or l["tipo"] != "adjudicou_a"
                     else "adjudicante")
            nos.setdefault(l["vizinho"], {"id": vid, "nome": l["nome"], "papel": papel, "listavel": True,
                                          "score": l["score_forn"] if papel == "fornecedor" else l["score_adj"]})
        else:  # pessoas singulares: um nó anónimo por tipo de relação, sem nomes
            vid = f"anon-{l['tipo']}"
            a = anonimos.setdefault(vid, {"id": vid, "nome": "", "papel": "anonimo", "listavel": False, "n": 0,
                                          "contratos": 0, "total": 0.0})
            a["n"] += 1
            a["contratos"] += int((l["detalhe"] or {}).get("n_contratos", 0))
            a["total"] += float((l["detalhe"] or {}).get("total", 0))
            continue
        origem, destino = (str(entidade_id), vid) if l["saida"] else (vid, str(entidade_id))
        arestas.append({"id": str(l["id"]), "source": origem, "target": destino, "tipo": l["tipo"],
                        "rotulo": TIPOS_RELACAO.get(l["tipo"], l["tipo"]), "natureza": l["natureza"],
                        "confianca": l["confianca"], "valido_de": l["valido_de"], "valido_ate": l["valido_ate"],
                        "atual": l["atual"], "fonte": l["fonte"], "detalhe": l["detalhe"],
                        "versoes_anteriores": l["versoes_anteriores"]})
    for vid, a in anonimos.items():
        a["nome"] = f"{a['n']} pessoas singulares (não identificadas)"
        nos[vid] = a
        tipo = vid.removeprefix("anon-")
        arestas.append({"id": vid, "source": str(entidade_id), "target": vid, "tipo": tipo,
                        "rotulo": TIPOS_RELACAO.get(tipo, tipo), "natureza": "documentada", "confianca": None,
                        "atual": None, "agregado": True,
                        "detalhe": {"n_contratos": a["contratos"], "total": round(a["total"], 2)}})
    return {"centro": str(entidade_id), "nos": list(nos.values()), "arestas": arestas, "omitidas": omitidas,
            "tipos": TIPOS_RELACAO}


@app.get("/api/relacoes/historico")
def historico_relacao(origem_id: int, destino_id: int, tipo: str = "adjudicou_a"):
    """Todas as versões conhecidas de uma relação (bitemporal: validade + quando foi registada/substituída)."""
    with bd.ligar() as con:
        _verificar_listavel(con, origem_id)
        _verificar_listavel(con, destino_id)
        return con.execute(
            "SELECT tipo, natureza, confianca, valido_de, valido_ate, registado_em, substituido_em, fonte, detalhe "
            "FROM relacao WHERE origem_id = %s AND destino_id = %s AND tipo = %s ORDER BY registado_em",
            (origem_id, destino_id, tipo)).fetchall()


@app.get("/api/distritos")
def distritos():
    with bd.ligar() as con:
        return [r["distrito"] for r in con.execute(
            "SELECT DISTINCT distrito FROM contrato WHERE distrito IS NOT NULL ORDER BY 1").fetchall()]
