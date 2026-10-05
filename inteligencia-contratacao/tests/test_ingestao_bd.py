"""Teste de integração da carga em PostgreSQL (resolução por NIF/nome, idempotência,
qualidade). Corre só se TEST_DATABASE_URL apontar para uma BD descartável."""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd, pontuacao
from contratacao.fontes.base_impic import carregar

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


def reg(id_, adj, forn, conc, proc="Concurso público", preco=1000.0):
    return {
        "idcontrato": id_, "idprocedimento": "p" + id_, "tipoContrato": ["Aquisição de serviços"],
        "tipoprocedimento": proc, "objectoContrato": "Teste", "adjudicante": [adj],
        "adjudicatarios": forn, "dataPublicacao": "10/01/2025", "dataCelebracaoContrato": "05/01/2025",
        "precoContratual": preco, "cpv": ["45000000-7 - Obras"], "localExecucao": ["Portugal, Porto, Porto"],
        "precoBaseProcedimento": 0, "PrecoTotalEfetivo": 0, "Ano": 2025, "concorrentes": conc,
    }


@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


def test_carga_resolucao_e_idempotencia(con):
    ent = "504615947 - Entidade A"
    registos = [
        reg("1", ent, ["500697370 - Forn X"], ["500697370-Forn X"]),
        reg("2", ent, ["500697370 - FORN X (nome alterado)"], ["500697370-Forn X", "510728189-Forn Y"]),
        reg("3", ent, ["- - Maria Teste"], None),
        reg("3", ent, ["- - Maria Teste"], None),       # id repetido -> aviso
        reg("4", "- - Sem NIF", ["500697370 - Forn X"], None),  # rejeitado
    ]
    r = carregar(con, registos, ano=2025, url=None, sha256="x")
    assert r["inseridos"] == 3 and r["rejeitados"] == 1

    # mesmo NIF com nomes diferentes = uma só entidade, variantes guardadas
    e = con.execute("SELECT id, nome FROM entidade WHERE nif='500697370'").fetchall()
    assert len(e) == 1
    nomes = con.execute("SELECT count(*) n FROM entidade_nome WHERE entidade_id=%s", (e[0]["id"],)).fetchone()
    assert nomes["n"] == 2
    # sem NIF -> identificado por nome
    s = con.execute("SELECT * FROM entidade WHERE identificado_por='nome'").fetchone()
    assert s["nif"] is None and s["chave"] == "nome:maria teste"
    # n_concorrentes: desconhecido fica NULL, não 0
    n = {x["id_origem"]: x["n_concorrentes"] for x in con.execute("SELECT id_origem, n_concorrentes FROM contrato")}
    assert n == {"1": 1, "2": 2, "3": None}
    q = con.execute("SELECT descricao FROM meta.problema_qualidade").fetchall()
    assert any("repetido" in x["descricao"] for x in q)
    assert any(x["descricao"] == "adjudicante sem NIF" for x in q)

    # recarregar igual: nada muda; alterar um registo: 1 atualizado
    r2 = carregar(con, registos, ano=2025, url=None, sha256="y")
    assert r2["inseridos"] == 0 and r2["atualizados"] == 0 and r2["inalterados"] == 3
    registos[0] = reg("1", ent, ["500697370 - Forn X"], ["500697370-Forn X"], preco=2000.0)
    r3 = carregar(con, registos, ano=2025, url=None, sha256="z")
    assert r3["atualizados"] == 1

    # pontuação ponta-a-ponta
    cont = pontuacao.recalcular(con)
    assert cont["concorrente_unico:sinal"] == 1
    assert cont["concorrente_unico:sem_sinal"] == 1
    assert cont["concorrente_unico:dados_insuficientes"] == 1


def test_score_sql_igual_ao_python(con):
    """A fórmula do score em SQL (api.py) tem de dar o mesmo que calibracao.score_entidade."""
    from fastapi.testclient import TestClient

    import contratacao.api as api
    from contratacao.calibracao import score_entidade

    ent = "504615947 - Entidade A"
    registos = []
    # referência: 40 concursos públicos com 4 concorrentes (taxa base baixa)
    for i in range(40):
        registos.append(reg(f"b{i}", ent, ["510728189 - Forn Y"], ["510728189-Y", "500697370-X", "1-Z", "2-W"]))
    # fornecedor X: 8 concursos, 6 com concorrente único
    for i in range(8):
        conc = ["500697370-X"] if i < 6 else ["500697370-X", "510728189-Y"]
        registos.append(reg(f"x{i}", ent, ["500697370 - Forn X"], conc))
    carregar(con, registos, ano=2025, url=None, sha256="s")
    pontuacao.recalcular(con)

    orig = api.bd.ligar
    api.bd.ligar = lambda url=None: orig(URL)
    try:
        cli = TestClient(api.app)
        lista = {r["nif"]: r for r in cli.get("/api/entidades").json()["resultados"]}
        det = cli.get(f"/api/entidades/{lista['500697370']['id']}/risco").json()
    finally:
        api.bd.ligar = orig
    x = lista["500697370"]
    esperado = score_entidade(x["n_sinais"], float(x["esperados"]), x["n_avaliaveis"])
    assert x["score"] == esperado.score == det["score"] > 0
