"""Grafo temporal: relações documentadas/inferidas e versões bitemporais."""

import os
from datetime import date

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd, grafo
from contratacao.fontes.base_impic import carregar
from contratacao.grafo import chave_sem_forma

URL = os.environ.get("TEST_DATABASE_URL")
integracao = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


def test_chave_sem_forma():
    assert chave_sem_forma("Google Ireland Ltd.") == chave_sem_forma("GOOGLE IRELAND LIMITED") == "google ireland"
    assert chave_sem_forma("Construções Silva, S.A.") == "construcoes silva"


def reg(id_, data, adj="504615947 - Município A", forn=("500697370 - Fornecedor X, Lda",), conc=None,
        proc_id=None, prazo=30, fecho=""):
    return {"idcontrato": id_, "idprocedimento": proc_id or f"p{id_}", "tipoContrato": ["Aquisição de serviços"],
            "tipoprocedimento": "Concurso público", "adjudicante": [adj], "adjudicatarios": list(forn),
            "dataPublicacao": data, "dataCelebracaoContrato": data, "precoContratual": 1000.0,
            "cpv": ["45000000-7 - x"], "localExecucao": ["Portugal, Porto, Porto"], "Ano": int(data[-4:]),
            "concorrentes": conc, "prazoExecucao": prazo, "dataFechoContrato": fecho}


@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


def rels(con, tipo, corrente=True):
    return con.execute(
        "SELECT r.*, o.nome AS origem, d.nome AS destino FROM relacao r JOIN entidade o ON o.id = r.origem_id "
        "JOIN entidade d ON d.id = r.destino_id WHERE r.tipo = %s AND (r.substituido_em IS NULL) = %s ORDER BY r.id",
        (tipo, corrente)).fetchall()


@integracao
def test_adjudicou_a_validade_e_bitemporalidade(con):
    carregar(con, iter([reg("1", "10/01/2020", prazo=30), reg("2", "01/06/2023", prazo=0, fecho="31/12/2023")]),
             ano=2020, url=None, sha256="a")
    r1 = grafo.reconstruir(con)
    assert r1["inseridas"] == 1 and r1["substituidas"] == 0
    (a,) = rels(con, "adjudicou_a")
    assert (a["natureza"], a["valido_de"], a["valido_ate"]) == ("documentada", date(2020, 1, 10), date(2023, 12, 31))
    assert a["detalhe"]["n_contratos"] == 2 and a["confianca"] is None

    # reconstruir sem alterações: nada muda (mesma versão)
    r2 = grafo.reconstruir(con)
    assert (r2["inseridas"], r2["substituidas"]) == (0, 0)

    # novo contrato muda a relação: versão antiga fica substituída (não apagada), nova é corrente
    carregar(con, iter([reg("3", "01/03/2026", prazo=365)]), ano=2026, url=None, sha256="b")
    r3 = grafo.reconstruir(con)
    assert (r3["inseridas"], r3["substituidas"]) == (1, 1)
    (atual,) = rels(con, "adjudicou_a")
    (antiga,) = rels(con, "adjudicou_a", corrente=False)
    assert atual["valido_ate"] == date(2027, 3, 1) and atual["detalhe"]["n_contratos"] == 3
    assert antiga["valido_ate"] == date(2023, 12, 31) and antiga["substituido_em"] is not None


@integracao
def test_concorreram_juntos_conta_procedimentos_nao_lotes(con):
    conc = ["500697370-Fornecedor X", "510728189-Fornecedor Y"]
    regs = [reg(f"c{i}", "01/02/2025", conc=conc) for i in range(4)]
    # 3 lotes do mesmo procedimento contam como 1 → total 5 procedimentos
    regs += [reg(f"l{i}", "01/03/2025", conc=conc, proc_id="lotes") for i in range(3)]
    carregar(con, iter(regs), ano=2025, url=None, sha256="c")
    grafo.reconstruir(con)
    (r,) = rels(con, "concorreram_juntos")
    assert r["detalhe"]["n_procedimentos"] == 5 and r["natureza"] == "documentada"
    assert r["valido_ate"] == date(2026, 3, 1)  # última vez + 365 dias


@integracao
def test_possivelmente_mesma_entidade_e_inferida_com_confianca(con):
    carregar(con, iter([
        reg("1", "01/02/2025", forn=("980123456 - Google Ireland Limited",)),
        reg("2", "01/03/2025", forn=("- - Google Ireland Ltd.",)),
        reg("3", "01/03/2025", forn=("- - Maria Silva",)),           # pessoa singular: nunca ligada
        reg("4", "01/03/2025", forn=("504988964 - Boston",)),
        reg("5", "01/03/2025", forn=("- - BOSTON SARL",)),            # nome curto: ambíguo, não liga
        reg("6", "01/03/2025", forn=("505770849 - Xecompex - Equipamentos e Serviços, Lda",)),
        reg("7", "01/03/2025", forn=("- - XECOMPEX - EQUIPAMENTOS E SERVIÇOS, LDA",)),  # igual com forma → 0,8
    ]), ano=2025, url=None, sha256="d")
    grafo.reconstruir(con)
    r = {x["origem"]: x for x in rels(con, "possivelmente_mesma_entidade")}
    assert set(r) == {"Google Ireland Ltd.", "XECOMPEX - EQUIPAMENTOS E SERVIÇOS, LDA"}
    assert r["Google Ireland Ltd."]["destino"] == "Google Ireland Limited"
    assert r["Google Ireland Ltd."]["natureza"] == "inferida" and float(r["Google Ireland Ltd."]["confianca"]) == 0.6
    assert float(r["XECOMPEX - EQUIPAMENTOS E SERVIÇOS, LDA"]["confianca"]) == 0.8


@integracao
def test_api_perfil_e_grafo_sem_pessoas_singulares(con):
    from fastapi.testclient import TestClient

    import contratacao.api as api
    from contratacao import pontuacao

    conc = ["500697370-Fornecedor X", "510728189-Fornecedor Y"]
    regs = [reg(f"x{i}", "01/02/2020", conc=conc) for i in range(5)]                     # relação passada
    regs += [reg("y1", "01/02/2026", forn=("510728189 - Fornecedor Y, S.A.",), prazo=3650)]  # relação atual
    regs += [reg("p1", "01/02/2025", forn=("- - Maria Silva",)), reg("p2", "01/03/2025", forn=("- - João Sousa",))]
    carregar(con, iter(regs), ano=2025, url=None, sha256="g")
    pontuacao.recalcular(con)
    grafo.reconstruir(con)
    mun = con.execute("SELECT id FROM entidade WHERE nif = '504615947'").fetchone()["id"]
    forn_x = con.execute("SELECT id FROM entidade WHERE nif = '500697370'").fetchone()["id"]
    pessoa = con.execute("SELECT id FROM entidade WHERE nome = 'Maria Silva'").fetchone()["id"]

    orig = api.bd.ligar
    api.bd.ligar = lambda url=None: orig(URL)
    try:
        cli = TestClient(api.app)
        perfil = cli.get(f"/api/entidades/{mun}").json()
        adj = perfil["papeis"]["adjudicante"]
        assert adj["resumo"]["n_contratos"] == 8
        assert [a["ano"] for a in adj["anos"]] == [2020, 2025, 2026]
        nomes_cp = [c["nome"] for c in adj["contrapartes"]]
        assert "Pessoas singulares (não identificadas)" in nomes_cp
        assert not any("Maria" in n or "João" in n for n in nomes_cp)

        g = cli.get(f"/api/grafo?entidade_id={mun}").json()
        assert "Maria" not in str(g) and "João" not in str(g)
        anon = [n for n in g["nos"] if n["papel"] == "anonimo"]
        assert len(anon) == 1 and anon[0]["n"] == 2
        estados = {a["target"]: a["atual"] for a in g["arestas"] if not a.get("agregado")}
        assert estados == {str(forn_x): False, str(con.execute("SELECT id FROM entidade WHERE nif='510728189'").fetchone()["id"]): True}

        so_atuais = cli.get(f"/api/grafo?entidade_id={mun}&estado=atuais").json()
        assert all(a["atual"] for a in so_atuais["arestas"] if not a.get("agregado"))
        # fornecedores que concorreram juntos 5 vezes
        gx = cli.get(f"/api/grafo?entidade_id={forn_x}").json()
        assert any(a["tipo"] == "concorreram_juntos" for a in gx["arestas"])
        # perfis e grafos de pessoas singulares estão bloqueados
        assert cli.get(f"/api/entidades/{pessoa}").status_code == 404
        assert cli.get(f"/api/grafo?entidade_id={pessoa}").status_code == 404
        assert cli.get(f"/entidade/{mun}").status_code == 200
        assert cli.get("/static/vendor/cytoscape.min.js").status_code == 200
    finally:
        api.bd.ligar = orig
