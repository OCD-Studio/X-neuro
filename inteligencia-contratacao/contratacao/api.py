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

from . import bd
from .config import RAIZ
from .normalizacao import PROCEDIMENTOS
from .calibracao import MIN_CONTRATOS_ENTIDADE, Z_CONFIANCA, score_entidade
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
            "SELECT fonte, ano, estado, n_registos, versao_fonte, atualizado_em FROM meta.cobertura "
            "WHERE fonte = 'base_contratos' ORDER BY ano"
        ).fetchall()
    return {
        "ultima_verificacao": sinc["terminado_em"] if sinc else None,
        "estado_ultima_verificacao": sinc["estado"] if sinc else None,
        "ultima_atualizacao": ultima_alteracao,
        "cobertura": cobertura,
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


# Score por indicador (igual a calibracao.score_entidade — testado) e média dos indicadores com amostra suficiente.
_SQL_SCORE = """
    wl AS (
        SELECT *, e / n AS p_esp,
               ((o::float8 / n) + %(z)s ^ 2 / (2 * n)
                 - %(z)s * sqrt((o::float8 / n) * (1 - o::float8 / n) / n + %(z)s ^ 2 / (4 * n * n)))
               / (1 + %(z)s ^ 2 / n) AS l
        FROM ind WHERE n > 0
    ),
    sc AS (
        SELECT eid, indicador_id, o, e, n, pontos,
               CASE WHEN n < %(nmin)s OR l <= p_esp THEN 0
                    WHEN p_esp >= 1 THEN 100
                    ELSE least(100, round(100 * (l - p_esp) / (1 - p_esp))) END AS score
        FROM wl
    ),
    f AS (
        SELECT eid, coalesce(round(avg(score) FILTER (WHERE n >= %(nmin)s)), 0) AS score,
               count(*) FILTER (WHERE n >= %(nmin)s) AS n_indicadores,
               sum(o) AS n_sinais, round(sum(e)::numeric, 1) AS esperados, sum(n) AS n_avaliaveis,
               sum(pontos) AS pontos
        FROM sc GROUP BY eid
    )
"""


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
    cond = ["TRUE"]
    if q:
        cond.append("(e.nome ILIKE %(q)s OR e.nif = %(qnif)s)"); par["q"] = f"%{q}%"; par["qnif"] = q.strip()
    if score_min is not None:
        cond.append("coalesce(f.score, 0) >= %(smin)s"); par["smin"] = score_min
    if nivel:
        cond.append("coalesce(f.score, 0) BETWEEN %(nmin_s)s AND %(nmax_s)s")
        par["nmin_s"], par["nmax_s"] = NIVEIS[nivel]
    par.update(lim=limite if formato == "json" else 100000, off=pagina * limite if formato == "json" else 0)
    sql = f"""
        WITH ag AS ({ag}), ind AS ({ind}), {_SQL_SCORE}
        SELECT e.id, e.nif, e.identificado_por, e.nome, ag.n_contratos, ag.total,
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
    return {"papel": papel, "caminho": "agregados" if fl.rapido else "contratos", "resultados": linhas}


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
        ent = con.execute("SELECT id, nif, identificado_por, nome FROM entidade WHERE id = %(eid)s", par).fetchone()
        if not ent:
            raise HTTPException(404, "entidade não encontrada")
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


@app.get("/api/distritos")
def distritos():
    with bd.ligar() as con:
        return [r["distrito"] for r in con.execute(
            "SELECT DISTINCT distrito FROM contrato WHERE distrito IS NOT NULL ORDER BY 1").fetchall()]
