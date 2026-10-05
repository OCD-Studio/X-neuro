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
from .regras import INDICADORES

app = FastAPI(title="Inteligência de Contratação Pública — sinais de risco")

ORDENACOES = {
    "total": "total",
    "n_contratos": "n_contratos",
    "score": "score",
    "valor_medio": "valor_medio",
    "pct_ajuste_direto": "pct_ajuste_direto",
    "n_sinais": "n_sinais",
}


@app.get("/", include_in_schema=False)
def pagina():
    return FileResponse(RAIZ / "static" / "index.html")


@app.get("/metodologia", include_in_schema=False)
def pagina_metodologia():
    return FileResponse(RAIZ / "static" / "metodologia.html")


@app.get("/api/meta")
def meta():
    """Última atualização, cobertura histórica e indicadores ativos."""
    with bd.ligar() as con:
        ultima = con.execute(
            "SELECT max(terminado_em) AS t FROM meta.execucao_ingestao WHERE estado IN ('concluido','inalterado')"
        ).fetchone()["t"]
        cobertura = con.execute("SELECT fonte, ano, estado, n_registos, atualizado_em FROM meta.cobertura ORDER BY ano").fetchall()
    return {
        "ultima_atualizacao": ultima,
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
    """Relatório de qualidade de dados agregado por execução e tipo de problema."""
    with bd.ligar() as con:
        execucoes = con.execute("SELECT * FROM meta.execucao_ingestao ORDER BY id DESC LIMIT 20").fetchall()
        problemas = con.execute(
            """
            SELECT p.execucao_id, p.gravidade,
                   regexp_replace(p.descricao, ':.*$', '') AS tipo, count(*) AS n
            FROM meta.problema_qualidade p
            GROUP BY 1,2,3 ORDER BY 1 DESC, n DESC
            """
        ).fetchall()
    return {"execucoes": execucoes, "problemas": problemas}


def _filtros(papel, de, ate, distrito, procedimento, cpv, valor_min, valor_max):
    cond, par = ["TRUE"], {}
    if de:
        cond.append("k.data_celebracao >= %(de)s"); par["de"] = de
    if ate:
        cond.append("k.data_celebracao <= %(ate)s"); par["ate"] = ate
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
    having = []
    if score_min is not None:
        having.append("coalesce(sum(r.pontos),0) >= %(smin)s"); par["smin"] = score_min
    if q:
        where += " AND (e.nome ILIKE %(q)s OR e.nif = %(qnif)s)"; par["q"] = f"%{q}%"; par["qnif"] = q
    sql = f"""
        SELECT e.id, e.nif, e.identificado_por, e.nome,
               count(DISTINCT k.id) AS n_contratos,
               coalesce(sum(k.preco_contratual),0) AS total,
               round(avg(k.preco_contratual),2) AS valor_medio,
               round(100.0 * count(*) FILTER (WHERE k.procedimento IN ('ajuste_direto','ajuste_direto_simplificado')) / count(*), 1) AS pct_ajuste_direto,
               coalesce(sum(r.pontos),0) AS score,
               coalesce(sum(r.n),0) AS n_sinais
        FROM contrato k
        {juncao}
        LEFT JOIN (SELECT contrato_id, sum(pontos) AS pontos, count(*) AS n
                   FROM avaliacao_risco WHERE estado = 'sinal' GROUP BY 1) r ON r.contrato_id = k.id
        WHERE {where}
        GROUP BY e.id
        {"HAVING " + " AND ".join(having) if having else ""}
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
            SELECT k.id, k.id_origem, k.objeto, k.procedimento, k.data_celebracao, k.preco_contratual,
                   k.n_concorrentes, k.distrito, k.url_fonte, a.nome AS adjudicante,
                   (SELECT json_agg(json_build_object('indicador', r.indicador, 'pontos', r.pontos,
                           'explicacao', r.explicacao)) FROM avaliacao_risco r
                    WHERE r.contrato_id = k.id AND r.estado='sinal') AS sinais
            FROM contrato k JOIN entidade a ON a.id = k.adjudicante_id
            WHERE {' AND '.join(cond)}
            ORDER BY k.data_celebracao DESC NULLS LAST LIMIT %(lim)s OFFSET %(off)s
            """,
            par,
        ).fetchall()
    return {"resultados": linhas}


@app.get("/api/distritos")
def distritos():
    with bd.ligar() as con:
        return [r["distrito"] for r in con.execute(
            "SELECT DISTINCT distrito FROM contrato WHERE distrito IS NOT NULL ORDER BY 1").fetchall()]
