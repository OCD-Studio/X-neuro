"""Casos conhecidos do indicador 'concorrente único'."""

import pytest

from contratacao.regras import ContratoAvaliavel, avaliar, score
from contratacao.regras.concorrente_unico import ConcorrenteUnico

R = ConcorrenteUnico()


def c(procedimento, n):
    return ContratoAvaliavel(id=1, procedimento=procedimento, n_concorrentes=n)


@pytest.mark.parametrize("proc", ["concurso_publico", "consulta_previa", "concurso_limitado",
                                  "negociacao", "consulta_previa_simplificada", "concurso_publico_urgente"])
def test_sinal_em_procedimento_concorrencial_com_um_concorrente(proc):
    r = R.avaliar(c(proc, 1))
    assert r.estado == "sinal" and r.pontos == R.pontos
    assert r.evidencia == {"procedimento": proc, "n_concorrentes": 1}
    assert "1 concorrente" in r.explicacao


@pytest.mark.parametrize("n", [2, 3, 15])
def test_sem_sinal_com_varios_concorrentes(n):
    r = R.avaliar(c("concurso_publico", n))
    assert r.estado == "sem_sinal" and r.pontos == 0


@pytest.mark.parametrize("proc", ["ajuste_direto", "ajuste_direto_simplificado", "acordo_quadro",
                                  "contratacao_excluida", "setores_especiais_isencao", "outro", "inexistente"])
def test_nao_aplicavel_fora_de_procedimentos_concorrenciais(proc):
    # Ajuste direto tem 1 convidado por natureza: nunca é sinal deste indicador.
    r = R.avaliar(c(proc, 1))
    assert r.estado == "nao_aplicavel" and r.pontos == 0


def test_concorrentes_desconhecidos_sao_dados_insuficientes_e_nao_pontuam():
    r = R.avaliar(c("concurso_publico", None))
    assert r.estado == "dados_insuficientes" and r.pontos == 0


def test_score_soma_pontos_dos_sinais():
    assert score(avaliar(c("concurso_publico", 1))) == R.pontos
    assert score(avaliar(c("concurso_publico", 4))) == 0
