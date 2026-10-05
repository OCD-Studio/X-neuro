"""Aplica os indicadores a todos os contratos e grava o resultado explicável."""

from __future__ import annotations

import json

import psycopg

from .regras import INDICADORES, ContratoAvaliavel, avaliar


def recalcular(con: psycopg.Connection) -> dict[str, int]:
    cur = con.cursor()
    linhas = cur.execute(
        """
        SELECT id, procedimento, n_concorrentes, preco_contratual, preco_base,
               data_celebracao, data_publicacao
        FROM contrato
        """
    ).fetchall()
    codigos = [i.codigo for i in INDICADORES]
    cur.execute("DELETE FROM avaliacao_risco WHERE indicador = ANY(%s)", (codigos,))
    contagem: dict[str, int] = {}
    with cur.copy(
        "COPY avaliacao_risco (contrato_id, indicador, versao, estado, pontos, explicacao, evidencia) FROM STDIN"
    ) as cp:
        for l in linhas:
            c = ContratoAvaliavel(**l)
            for r in avaliar(c):
                contagem[f"{r.indicador}:{r.estado}"] = contagem.get(f"{r.indicador}:{r.estado}", 0) + 1
                cp.write_row((l["id"], r.indicador, r.versao, r.estado, r.pontos, r.explicacao,
                              json.dumps(r.evidencia, ensure_ascii=False, default=str)))
    con.commit()
    return contagem
