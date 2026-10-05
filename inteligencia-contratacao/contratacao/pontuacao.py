"""Aplica os indicadores a todos os contratos e grava o resultado explicável e calibrado.

Duas passagens: (1) avaliar cada contrato com cada indicador; (2) calcular as
taxas de referência a partir dos contratos avaliáveis e calibrar os pontos.
"""

from __future__ import annotations

import json
from collections import defaultdict

import psycopg

from .calibracao import Avaliavel, TabelaReferencia, pontos_calibrados
from .regras import INDICADORES, ContratoAvaliavel, avaliar

AVALIAVEL = ("sinal", "sem_sinal")


def recalcular(con: psycopg.Connection) -> dict[str, int]:
    cur = con.cursor()
    linhas = cur.execute(
        """
        SELECT id, procedimento, n_concorrentes, preco_contratual, preco_base,
               data_celebracao, data_publicacao, ano, left(cpv, 2) AS cpv_divisao
        FROM contrato
        """
    ).fetchall()

    # 1) avaliar
    resultados = []  # (linha, Resultado)
    for l in linhas:
        c = ContratoAvaliavel(
            id=l["id"], procedimento=l["procedimento"], n_concorrentes=l["n_concorrentes"],
            preco_contratual=l["preco_contratual"], preco_base=l["preco_base"],
            data_celebracao=l["data_celebracao"], data_publicacao=l["data_publicacao"],
        )
        for r in avaliar(c):
            resultados.append((l, r))

    # 2) referências por indicador
    por_indicador: dict[str, list[Avaliavel]] = defaultdict(list)
    for l, r in resultados:
        if r.estado in AVALIAVEL:
            por_indicador[r.indicador].append(
                Avaliavel(l["ano"], l["procedimento"], l["cpv_divisao"], r.estado == "sinal"))
    tabelas = {ind: TabelaReferencia(av) for ind, av in por_indicador.items()}

    codigos = [i.codigo for i in INDICADORES]
    cur.execute("DELETE FROM avaliacao_risco WHERE indicador = ANY(%s)", (codigos,))
    cur.execute("DELETE FROM referencia_risco WHERE indicador = ANY(%s)", (codigos,))

    contagem: dict[str, int] = defaultdict(int)
    with cur.copy(
        "COPY avaliacao_risco (contrato_id, indicador, versao, estado, pontos, pontos_base, "
        "taxa_referencia, referencia, explicacao, evidencia) FROM STDIN"
    ) as cp:
        for l, r in resultados:
            contagem[f"{r.indicador}:{r.estado}"] += 1
            taxa = ref_txt = None
            pontos = r.pontos
            explicacao = r.explicacao
            if r.estado in AVALIAVEL and r.indicador in tabelas:
                ref = tabelas[r.indicador].referencia(
                    Avaliavel(l["ano"], l["procedimento"], l["cpv_divisao"], r.estado == "sinal"))
                if ref is not None:
                    taxa, ref_txt = ref.taxa, ref.descricao
                    if r.estado == "sinal":
                        pontos = pontos_calibrados(r.pontos, ref.taxa)
                        explicacao += (f" Taxa de referência: {ref.taxa:.0%} ({ref.descricao}); "
                                       f"pontos = {r.pontos} × (1 − {ref.taxa:.2f}) = {pontos}.")
            cp.write_row((l["id"], r.indicador, r.versao, r.estado, pontos, r.pontos, taxa, ref_txt,
                          explicacao, json.dumps(r.evidencia, ensure_ascii=False, default=str)))

    with cur.copy(
        "COPY referencia_risco (indicador, nivel, ano, procedimento, cpv_divisao, n_avaliaveis, n_sinais, taxa) FROM STDIN"
    ) as cp:
        for ind, tab in tabelas.items():
            for nivel, chave, n, s in tab.linhas():
                ano = chave[0] if nivel != "procedimento" else None
                proc = chave[1] if nivel != "procedimento" else chave[0]
                cpv = chave[2] if nivel == "ano+procedimento+cpv" else None
                cp.write_row((ind, nivel, ano, proc, cpv, n, s, round(s / n, 5)))
    con.commit()
    return dict(contagem)
