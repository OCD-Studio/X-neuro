"""Planeamento da rotina diária (puro) e execução contra PostgreSQL com fonte simulada."""

import os
from datetime import datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd
from contratacao.fontes import base_impic
from contratacao.fontes.base_impic import Recurso, recursos_de_dataset
from contratacao.sincronizacao import EstadoAno, executar, planear


def rec(ano, sha1="s"):
    return Recurso(ano=ano, url=f"http://x/contratos{ano}.zip", sha1=f"{sha1}{ano}", versao=None, tamanho=None)


def test_recursos_de_dataset_le_zip_por_ano_e_ignora_resto():
    ds = {"resources": [
        {"title": "contratos2025.zip", "url": "u25", "checksum": {"type": "sha1", "value": "abc"},
         "last_modified": "2026-10-04T09:04:34+00:00", "filesize": 10},
        {"title": "contratos2025.xlsx", "url": "x"},
        {"title": "contratos2012.zip", "url": "u12"},
    ]}
    r = recursos_de_dataset(ds)
    assert set(r) == {2025, 2012}
    assert r[2025].sha1 == "abc" and r[2025].versao == datetime.fromisoformat("2026-10-04T09:04:34+00:00")
    assert r[2012].sha1 is None


def test_plano_backfill_progressivo_do_mais_recente_para_o_mais_antigo():
    recursos = {a: rec(a) for a in range(2012, 2027)}
    p = planear(recursos, {}, max_backfill=3)
    assert p.backfill == [2026, 2025, 2024]
    assert p.adiados == list(range(2023, 2011, -1))
    assert p.atualizar == []


def test_plano_atualiza_so_anos_cujo_checksum_mudou():
    recursos = {2026: rec(2026, "novo"), 2025: rec(2025), 2024: rec(2024)}
    cob = {2026: EstadoAno("ingerido", "s2026"), 2025: EstadoAno("ingerido", "s2025"),
           2024: EstadoAno("falhou", None)}
    p = planear(recursos, cob, max_backfill=3)
    assert p.atualizar == [2026] and p.sem_alteracao == [2025] and p.backfill == [2024]


def test_plano_sem_sha1_na_fonte_reverifica_e_assinala_anos_despublicados():
    recursos = {2025: Recurso(2025, "u", None, None, None)}
    cob = {2025: EstadoAno("ingerido", None), 2011: EstadoAno("ingerido", "x")}
    p = planear(recursos, cob)
    assert p.atualizar == [2025] and p.deixaram_de_ser_publicados == [2011]


def test_plano_max_backfill_zero_so_faz_deltas():
    p = planear({2026: rec(2026)}, {}, max_backfill=0)
    assert p.anos == [] and p.adiados == [2026]


# ------------------------------------------------------------------ integração

URL = os.environ.get("TEST_DATABASE_URL")
integracao = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


def reg(id_, ano, conc=("500697370-X",)):
    return {
        "idcontrato": id_, "tipoContrato": ["Aquisição de serviços"], "tipoprocedimento": "Concurso público",
        "adjudicante": ["504615947 - Entidade A"], "adjudicatarios": ["500697370 - Forn X"],
        "dataPublicacao": f"10/01/{ano}", "dataCelebracaoContrato": f"05/01/{ano}", "precoContratual": 1000.0,
        "cpv": ["45000000-7 - Obras"], "localExecucao": ["Portugal, Porto, Porto"], "Ano": ano,
        "concorrentes": list(conc),
    }


@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


@integracao
def test_executar_backfill_progressivo_deltas_e_falhas(con):
    fonte = {2026: [reg("a1", 2026), reg("a2", 2026)], 2025: [reg("b1", 2025)], 2024: [reg("c1", 2024)]}
    versoes = {2026: "v1", 2025: "v1", 2024: "v1"}
    falhar = set()

    def listar():
        return {a: rec(a, versoes[a]) for a in fonte}

    def ingerir(c, r, sid):
        if r.ano in falhar:
            raise RuntimeError("rede em baixo")
        return base_impic.carregar(c, iter(fonte[r.ano]), ano=r.ano, url=r.url, sha256=f"{r.sha1}",
                                   sha1=r.sha1, sincronizacao_id=sid)

    # 1.º dia: só 2 anos de backfill (os mais recentes)
    r1 = executar(con, max_backfill=2, listar=listar, ingerir=ingerir)
    assert r1["estado"] == "concluido" and r1["plano"]["backfill"] == [2026, 2025]
    assert r1["adiados"] == [2024] and r1["pontuacao_recalculada"]
    cob = {l["ano"]: l["estado"] for l in con.execute("SELECT ano, estado FROM meta.cobertura")}
    assert cob == {2026: "ingerido", 2025: "ingerido", 2024: "pendente"}

    # 2.º dia: 2026 republicado (a2 desaparece, a3 aparece); 2024 falha
    fonte[2026] = [reg("a1", 2026), reg("a3", 2026)]
    versoes[2026] = "v2"
    falhar.add(2024)
    r2 = executar(con, max_backfill=2, listar=listar, ingerir=ingerir)
    assert r2["plano"]["atualizar"] == [2026] and r2["plano"]["sem_alteracao"] == [2025]
    assert r2["estado"] == "parcial" and r2["falhas"] == [2024]
    rem = con.execute("SELECT id_origem FROM contrato WHERE removido_da_fonte_em IS NOT NULL").fetchall()
    assert [x["id_origem"] for x in rem] == ["a2"]  # marcado, não apagado
    assert con.execute("SELECT count(*) n FROM contrato").fetchone()["n"] == 4
    cob = {l["ano"]: l["estado"] for l in con.execute("SELECT ano, estado FROM meta.cobertura")}
    assert cob[2024] == "falhou"

    # 3.º dia: 2024 recupera; nada mais muda
    falhar.clear()
    r3 = executar(con, max_backfill=2, listar=listar, ingerir=ingerir)
    assert r3["estado"] == "concluido" and r3["plano"]["backfill"] == [2024] and r3["plano"]["atualizar"] == []
    est = [l["estado"] for l in con.execute("SELECT estado FROM meta.sincronizacao ORDER BY id")]
    assert est == ["concluido", "parcial", "concluido"]


@integracao
def test_lock_impede_execucoes_simultaneas(con):
    with psycopg.connect(URL, row_factory=dict_row) as outra:
        outra.execute("SELECT pg_advisory_lock(7202610)")
        r = executar(con, listar=lambda: {}, ingerir=lambda *a: {})
        assert r == {"estado": "ocupado"}


def test_qualidade_agrega_tipos_e_nao_repete_exemplos():
    from contratacao.fontes.base_impic import _Qualidade
    q = _Qualidade()
    q.add("1", "aviso", "idcontrato repetido no ficheiro (2x); mantida a última ocorrência")
    q.add("2", "aviso", "idcontrato repetido no ficheiro (4x); mantida a última ocorrência")
    q.add("3", "aviso", "NIF de adjudicatário com dígito de controlo inválido: 123")
    q.add("3", "aviso", "4 adjudicantes; usado o primeiro")
    q.add("3", "aviso", "2 adjudicantes; usado o primeiro")
    assert q.n[("aviso", "idcontrato repetido no ficheiro; mantida a última ocorrência")] == 2
    assert q.n[("aviso", "NIF de adjudicatário com dígito de controlo inválido")] == 1
    assert q.n[("aviso", "vários adjudicantes; usado o primeiro")] == 2
    assert q.exemplos[("aviso", "vários adjudicantes; usado o primeiro")] == ["3"]
