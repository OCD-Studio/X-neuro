"""Casos conhecidos dos indicadores por contrato."""

from datetime import date
from decimal import Decimal as D

import pytest

from contratacao.regras import INDICADORES, TODOS
from contratacao.regras.base import ContratoAvaliavel
from contratacao.regras.limiares import limiar
from contratacao.regras.por_contrato import (
    AUTARQUICAS,
    AjusteDiretoAcimaLimiar,
    DerrapagemExecucao,
    PrecoAcimaBase,
    PublicacaoTardia,
    TimingEleitoral,
    ValorLogoAbaixoLimiar,
    e_autarquia,
)


def c(**kw):
    base = dict(id=1, procedimento="concurso_publico")
    base.update(kw)
    return ContratoAvaliavel(**base)


def test_registo_codigos_unicos_e_metadados_completos():
    codigos = [i.codigo for i in TODOS]
    assert len(codigos) == len(set(codigos)) == 12
    for i in TODOS:
        assert i.nome and i.descricao and i.limites and i.referencia and i.pontos > 0


# ---------------------------------------------------------------- preço acima do base

@pytest.mark.parametrize("contratual,base,estado", [
    (D("12000"), D("10000"), "sinal"),
    (D("10000.50"), D("10000"), "sem_sinal"),   # dentro da tolerância de 1 €
    (D("10001.01"), D("10000"), "sinal"),
    (D("8000"), D("10000"), "sem_sinal"),
    (D("8000"), None, "dados_insuficientes"),
    (None, D("10000"), "dados_insuficientes"),
])
def test_preco_acima_base(contratual, base, estado):
    r = PrecoAcimaBase().avaliar(c(preco_contratual=contratual, preco_base=base))
    assert r.estado == estado
    if estado == "sinal":
        assert "acima do preço base" in r.explicacao and r.evidencia["razao"] > 1


# ---------------------------------------------------------------- derrapagem

@pytest.mark.parametrize("efetivo,contratual,estado", [
    (D("12000"), D("10000"), "sinal"),      # 120 % exatamente
    (D("11999"), D("10000"), "sem_sinal"),
    (None, D("10000"), "nao_aplicavel"),    # contrato não fechado
    (D("5000"), None, "dados_insuficientes"),
])
def test_derrapagem(efetivo, contratual, estado):
    assert DerrapagemExecucao().avaliar(c(preco_efetivo=efetivo, preco_contratual=contratual)).estado == estado


# ---------------------------------------------------------------- limiares do ajuste direto

def test_limiares_por_regime_e_base_legal():
    assert limiar("ccp2017", "art20d") == D("20000")
    assert limiar("ccp2017", "art19d") == D("30000")
    assert limiar("ccp2008", "art20a") == D("75000")
    assert limiar("ccp2008", "art19a") == D("150000")
    assert limiar("ccp2017", "art20a") is None     # base legal incoerente com o regime
    assert limiar("ccp2017", "outro") is None


def ad(preco, regime="ccp2017", fund="art20d", proc="ajuste_direto"):
    return c(procedimento=proc, preco_contratual=D(preco) if preco is not None else None,
             regime_tipo=regime, fundamento_ad=fund)


@pytest.mark.parametrize("contrato,estado", [
    (ad("20000"), "sinal"),                          # igual ao limiar: a lei exige "inferior"
    (ad("25000"), "sinal"),
    (ad("19999.99"), "sem_sinal"),
    (ad("80000", regime="ccp2008", fund="art20a"), "sinal"),
    (ad("74000", regime="ccp2008", fund="art20a"), "sem_sinal"),
    (ad("29000", fund="art19d"), "sem_sinal"),       # empreitada: limiar 30 000 €
    (ad("500000", fund="outro"), "nao_aplicavel"),   # critérios materiais: sem limite de valor
    (ad("500000", regime="especial_ou_regional"), "nao_aplicavel"),
    (ad("500000", proc="consulta_previa"), "nao_aplicavel"),
    (ad("25000", fund=None), "dados_insuficientes"),
    (ad(None), "dados_insuficientes"),
])
def test_ajuste_direto_acima_limiar(contrato, estado):
    assert AjusteDiretoAcimaLimiar().avaliar(contrato).estado == estado


@pytest.mark.parametrize("preco,estado", [
    ("18000", "sinal"),       # 90 %
    ("19999", "sinal"),
    ("17999", "sem_sinal"),
    ("20000", "sem_sinal"),   # no limiar já não é "abaixo"
])
def test_valor_logo_abaixo_limiar(preco, estado):
    assert ValorLogoAbaixoLimiar().avaliar(ad(preco)).estado == estado


# ---------------------------------------------------------------- publicação tardia

@pytest.mark.parametrize("cel,pub,estado", [
    (date(2025, 1, 1), date(2025, 4, 2), "sinal"),      # 91 dias
    (date(2025, 1, 1), date(2025, 4, 1), "sem_sinal"),  # 90 dias
    (date(2025, 1, 10), date(2025, 1, 5), "dados_insuficientes"),  # incoerência da fonte
    (None, date(2025, 1, 5), "dados_insuficientes"),
])
def test_publicacao_tardia(cel, pub, estado):
    r = PublicacaoTardia().avaliar(c(data_celebracao=cel, data_publicacao=pub))
    assert r.estado == estado


# ---------------------------------------------------------------- timing eleitoral

def test_datas_autarquicas_confirmadas():
    assert AUTARQUICAS == (date(2013, 9, 29), date(2017, 10, 1), date(2021, 9, 26), date(2025, 10, 12))


@pytest.mark.parametrize("nome,ok", [
    ("Município de Matosinhos", True), ("Câmara Municipal de Lisboa", True),
    ("União das Freguesias de Santa Iria", True), ("Freguesia de Santo António (Lisboa)", True),
    ("Metro do Porto, SA", False), ("Agrupamento de Escolas X", False), (None, False),
])
def test_e_autarquia(nome, ok):
    assert e_autarquia(nome) is ok


@pytest.mark.parametrize("nome,data,estado", [
    ("Município de X", date(2021, 9, 25), "sinal"),        # véspera
    ("Município de X", date(2021, 7, 28), "sinal"),        # 60 dias antes
    ("Município de X", date(2021, 7, 27), "sem_sinal"),    # 61 dias antes
    ("Município de X", date(2021, 9, 26), "sem_sinal"),    # dia da eleição já não conta
    ("Município de X", date(2022, 9, 1), "nao_aplicavel"), # ano sem eleições
    ("Hospital de Y", date(2021, 9, 1), "nao_aplicavel"),
    ("Município de X", None, "dados_insuficientes"),
])
def test_timing_eleitoral(nome, data, estado):
    assert TimingEleitoral().avaliar(c(adjudicante_nome=nome, data_celebracao=data)).estado == estado


def test_todos_os_indicadores_por_contrato_toleram_contrato_vazio():
    # nunca rebentam nem dão "sinal" sem dados
    for ind in INDICADORES:
        r = ind.avaliar(ContratoAvaliavel(id=1, procedimento="outro"))
        assert r.estado != "sinal"
