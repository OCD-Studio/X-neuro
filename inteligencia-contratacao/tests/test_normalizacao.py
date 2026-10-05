"""Testes da normalização. Os casos replicam formatos observados no ficheiro
real contratos2025 (dados.gov.pt, versão de 2026-10-04); os valores são de teste."""

from datetime import date
from decimal import Decimal

import pytest

from contratacao.normalizacao import (
    Rejeicao,
    chave_nome,
    nif_valido,
    normalizar_contrato,
    normalizar_procedimento,
    parse_ator,
    parse_cpv,
    parse_data,
    parse_local,
    parse_valor,
)


@pytest.mark.parametrize("nif,ok", [
    ("504615947", True),   # NIPC válido
    ("500697370", True),
    ("504615948", False),  # dígito de controlo errado
    ("12345", False),
    ("abcdefghi", False),
])
def test_nif_valido(nif, ok):
    assert nif_valido(nif) is ok


def test_parse_ator_formatos():
    a = parse_ator("504615947 - MEO - Serviços de Comunicações e Multimédia, S.A.")
    assert a.nif == "504615947" and a.nome.startswith("MEO - Serviços") and a.nif_valido
    b = parse_ator("511005083-Mendes Gomes &amp; Companhia, Lda")  # sem espaços + entidade HTML
    assert b.nif == "511005083" and b.nome == "Mendes Gomes & Companhia, Lda"


def test_parse_ator_sem_nif_identifica_por_nome():
    a = parse_ator("- - ROLLS-ROYCE SOLUTIONS IBERICA SL")
    assert a.nif is None and a.nome == "ROLLS-ROYCE SOLUTIONS IBERICA SL"
    assert a.chave == "nome:rolls royce solutions iberica sl"
    c = parse_ator("--ASCH - Infraestructuras y Servicios, S.A.")
    assert c.nif is None and c.nome.startswith("ASCH")


@pytest.mark.parametrize("bruto", [None, "", "NULL", "-", "- - ", 123])
def test_parse_ator_invalido(bruto):
    assert parse_ator(bruto) is None


def test_chave_nome_ignora_acentos_e_pontuacao():
    assert chave_nome("Construções  Gabriel, S.A.") == "construcoes gabriel s a"
    assert chave_nome("Mendes Gomes &amp; Companhia") == "mendes gomes companhia"


def test_parse_data():
    assert parse_data("06/08/2024") == date(2024, 8, 6)
    assert parse_data("2025-01-31") == date(2025, 1, 31)
    assert parse_data("31/02/2025") is None
    assert parse_data("") is None


def test_parse_valor():
    assert parse_valor(11500.0) == Decimal("11500.0")
    assert parse_valor("1.234,56 €") == Decimal("1234.56")
    assert parse_valor("") is None
    assert parse_valor("abc") is None


@pytest.mark.parametrize("bruto,esperado", [
    ("Ajuste Direto Regime Geral", "ajuste_direto"),
    ("Ajuste direto simplificado ao abrigo da Lei n.º 30/2021, de 21.05", "ajuste_direto_simplificado"),
    ("Ajuste Direto Regime Geral ao abrigo do artigo 7º da Lei n.º 30/2021, de 21.05", "ajuste_direto"),
    ("Consulta Prévia", "consulta_previa"),
    ("Consulta Prévia Simplificada", "consulta_previa_simplificada"),
    ("Concurso público", "concurso_publico"),
    ("Concurso público simplificado", "concurso_publico_simplificado"),
    ("Concurso limitado por prévia qualificação", "concurso_limitado"),
    ("Concurso limitado por prévia qualificação simplificado", "concurso_limitado_simplificado"),
    ("Ao abrigo de acordo-quadro (art.º 259.º)", "acordo_quadro"),
    ("Contratação excluída II", "contratacao_excluida"),
    ("Setores especiais – isenção parte II", "setores_especiais_isencao"),
    ("Procedimento de negociação", "negociacao"),
    ("Concurso de conceção simplificado", "concurso_concecao"),
    ("Coisa nova desconhecida", "outro"),
    (None, "outro"),
])
def test_normalizar_procedimento(bruto, esperado):
    assert normalizar_procedimento(bruto) == esperado


def test_parse_cpv_e_local():
    assert parse_cpv(["79970000-4 - Serviços de publicação"]) == ("79970000", "Serviços de publicação")
    assert parse_cpv([]) == (None, None)
    assert parse_local(["Portugal, Lisboa, Lisboa"]) == ("Lisboa", "Lisboa")
    assert parse_local(["Bélgica"]) == (None, None)
    assert parse_local(None) == (None, None)


def _registo(**extra):
    base = {
        "idcontrato": "1", "idprocedimento": "9", "tipoContrato": ["Aquisição de serviços"],
        "tipoprocedimento": "Concurso público", "objectoContrato": "Teste",
        "adjudicante": ["504615947 - Entidade Teste"], "adjudicatarios": ["500697370 - Fornecedor Teste"],
        "dataPublicacao": "10/01/2025", "dataCelebracaoContrato": "05/01/2025",
        "precoContratual": 1000.0, "cpv": ["45000000-7 - Obras"], "localExecucao": ["Portugal, Porto, Porto"],
        "precoBaseProcedimento": 1200.0, "PrecoTotalEfetivo": 0.0, "Ano": 2025,
        "concorrentes": ["500697370-Fornecedor Teste"], "fundamentAjusteDireto": "",
    }
    base.update(extra)
    return base


def test_normalizar_contrato_completo():
    c = normalizar_contrato(_registo())
    assert c.id_origem == "1" and c.procedimento == "concurso_publico"
    assert c.adjudicante.nif == "504615947"
    assert [a.nif for a in c.adjudicatarios] == ["500697370"]
    assert len(c.concorrentes) == 1
    assert c.preco_base == Decimal("1200.0") and c.preco_efetivo is None
    assert c.distrito == "Porto" and c.cpv == "45000000"
    assert c.avisos == []


def test_concorrentes_vazio_e_desconhecido_nao_zero():
    c = normalizar_contrato(_registo(concorrentes=None))
    assert c.concorrentes is None
    assert "campo concorrentes vazio" in c.avisos


def test_rejeicoes_com_motivo():
    assert isinstance(normalizar_contrato(_registo(idcontrato="")), Rejeicao)
    r = normalizar_contrato(_registo(adjudicante=[]))
    assert isinstance(r, Rejeicao) and "adjudicante" in r.motivo
    r = normalizar_contrato(_registo(adjudicante=["- - Sem NIF"]))
    assert isinstance(r, Rejeicao) and r.motivo == "adjudicante sem NIF"


def test_avisos_nao_inventam_dados():
    c = normalizar_contrato(_registo(precoContratual=None, dataCelebracaoContrato="", Ano=None))
    assert c.preco_contratual is None and c.data_celebracao is None and c.ano is None
    assert "preço contratual ausente" in c.avisos


def test_checksum_muda_com_conteudo():
    a = normalizar_contrato(_registo()).checksum
    b = normalizar_contrato(_registo(precoContratual=1001.0)).checksum
    assert a != b and a == normalizar_contrato(_registo()).checksum
