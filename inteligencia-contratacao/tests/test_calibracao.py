"""Testes da calibração (taxas de referência, pontos e score de entidade)."""

import pytest

from contratacao.calibracao import (
    MIN_AMOSTRA_REF,
    Avaliavel,
    TabelaReferencia,
    pontos_calibrados,
    score_entidade,
    wilson_inferior,
)


def lote(ano, proc, cpv, n, sinais):
    return [Avaliavel(ano, proc, cpv, i < sinais) for i in range(n)]


def test_referencia_usa_grupo_mais_especifico_com_amostra():
    dados = lote(2025, "concurso_publico", "45", 40, 10) + lote(2025, "concurso_publico", "33", 10, 10)
    t = TabelaReferencia(dados)
    r45 = t.referencia(Avaliavel(2025, "concurso_publico", "45", True))
    assert r45.nivel == "ano+procedimento+cpv" and r45.taxa == pytest.approx(0.25) and r45.n == 40
    # CPV 33 só tem 10 (< 30) -> sobe para ano+procedimento (50 contratos, 20 sinais)
    r33 = t.referencia(Avaliavel(2025, "concurso_publico", "33", True))
    assert r33.nivel == "ano+procedimento" and r33.taxa == pytest.approx(0.4) and r33.n == 50


def test_referencia_sobe_para_todos_os_anos_e_none_sem_amostra():
    dados = lote(2024, "consulta_previa", "45", 20, 10) + lote(2025, "consulta_previa", "45", 20, 0)
    t = TabelaReferencia(dados)
    r = t.referencia(Avaliavel(2025, "consulta_previa", "45", False))
    assert r.nivel == "procedimento" and r.taxa == pytest.approx(0.25)
    assert TabelaReferencia(lote(2025, "negociacao", None, MIN_AMOSTRA_REF - 1, 5)).referencia(
        Avaliavel(2025, "negociacao", None, True)) is None


def test_pontos_calibrados():
    assert pontos_calibrados(10, 0.126) == 9   # concurso público: sinal raro pesa mais
    assert pontos_calibrados(10, 0.47) == 5    # consulta prévia: sinal comum pesa menos
    assert pontos_calibrados(10, None) == 10   # sem referência: sem calibração
    assert pontos_calibrados(10, 1.0) == 0


def test_wilson_inferior_valores_conhecidos():
    assert wilson_inferior(0, 0) == 0
    assert wilson_inferior(6, 6) == pytest.approx(0.6893, abs=1e-3)
    assert wilson_inferior(37, 37) == pytest.approx(0.9318, abs=1e-3)
    assert wilson_inferior(5, 10) < 0.5


def test_score_amostra_insuficiente():
    s = score_entidade(4, 0.5, 4)
    assert s.score == 0 and "amostra insuficiente" in s.motivo


def test_score_em_linha_com_esperado_e_zero_mesmo_para_entidade_grande():
    # caso real (MEO 2025): 93 sinais em 328 avaliáveis, 90,9 esperados
    s = score_entidade(93, 90.9, 328)
    assert s.score == 0 and "não significativa" in s.motivo


def test_score_independente_da_dimensao_e_penaliza_amostra_pequena():
    pequeno = score_entidade(6, 3.1, 6)       # 6/6, esperado ~52 %
    grande = score_entidade(37, 13.7, 37)     # 37/37, esperado ~37 %
    assert 0 < pequeno.score < grande.score <= 100
    assert "95 %" in grande.motivo


def test_score_maximo_e_monotonia():
    assert score_entidade(1000, 0, 1000).score >= 99
    assert score_entidade(20, 2, 20).score > score_entidade(15, 2, 20).score
