"""Minimização de dados pessoais: pessoas singulares não aparecem em listas nem perfis."""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd, pontuacao
from contratacao.fontes.base_impic import carregar

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


@pytest.mark.parametrize("nif,nome,tipo", [
    ("504615947", "Empresa X", "coletiva"),
    ("600012345", "Instituto Público", "coletiva"),
    ("980123456", "Empresa estrangeira", "coletiva"),
    ("700123456", "Herança de Fulano", "singular"),
    ("801234567", "Empresário em nome individual", "singular"),
    (None, "Maria Odete Machado", "singular_provavel"),
    (None, "JOSÉ SA", "singular_provavel"),            # "Sa"/"Sá" é apelido
    (None, "Tendas Eventos de Custódio Lopes", "singular_provavel"),
    (None, "Construções Silva, S.A.", "coletiva_sem_nif"),
    (None, "Win SF, Lda.", "coletiva_sem_nif"),
    (None, "LGC Genomics GmbH", "coletiva_sem_nif"),
    (None, "Google Ireland Ltd.", "coletiva_sem_nif"),
    (None, "Associação 29 de Abril", "coletiva_sem_nif"),
])
def test_classificar_pessoa(con, nif, nome, tipo):
    assert con.execute("SELECT classificar_pessoa(%s, %s) AS t", (nif, nome)).fetchone()["t"] == tipo


def test_api_nao_lista_nem_perfila_pessoas_singulares(con):
    from fastapi.testclient import TestClient

    import contratacao.api as api

    def reg(id_, forn):
        return {"idcontrato": id_, "tipoContrato": ["Aquisição de serviços"], "tipoprocedimento": "Concurso público",
                "adjudicante": ["504615947 - Município de X"], "adjudicatarios": [forn],
                "dataPublicacao": "01/03/2025", "dataCelebracaoContrato": "01/03/2025", "precoContratual": 1000.0,
                "cpv": ["45000000-7 - x"], "localExecucao": ["Portugal, Porto, Porto"], "Ano": 2025,
                "concorrentes": ["1-X"]}
    carregar(con, iter([reg("1", "- - Maria Odete Machado"), reg("2", "500697370 - Empresa Y, Lda"),
                        reg("3", "- - Construções Silva, S.A.")]), ano=2025, url=None, sha256="m")
    pontuacao.recalcular(con)
    pessoa = con.execute("SELECT id FROM entidade WHERE nome = 'Maria Odete Machado'").fetchone()["id"]

    orig = api.bd.ligar
    api.bd.ligar = lambda url=None: orig(URL)
    try:
        cli = TestClient(api.app)
        for q in ("", "?distrito=Porto", "?de=2025-01-01&ate=2025-12-31", "?q=Maria", "?formato=csv"):
            r = cli.get("/api/entidades" + q)
            assert "Maria Odete" not in r.text, q
        nomes = {x["nome"] for x in cli.get("/api/entidades").json()["resultados"]}
        assert nomes == {"Empresa Y, Lda", "Construções Silva, S.A."}
        assert cli.get(f"/api/entidades/{pessoa}/risco").status_code == 404
        assert cli.get(f"/api/contratos?entidade_id={pessoa}").status_code == 404
        assert cli.get("/api/meta").json()["fornecedores_nao_listados"] == 1
    finally:
        api.bd.ligar = orig
