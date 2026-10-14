"""Cargos públicos (Entidade para a Transparência): minimização, consolidação, correspondência e remoção."""

import os
from datetime import date, datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd, grafo
from contratacao.fontes import et_cargos
from contratacao.fontes.base_impic import carregar
from contratacao.fontes.et_cargos import chaves_correspondencia, consolidar_cargos, limpar

URL = os.environ.get("TEST_DATABASE_URL")
integracao = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


def item(id_, natureza="Initial", entidade="Câmara Municipal de Chaves", orgao="Câmara Municipal",
         cargo="Vereador", inicio="2021-10-20T00:00:00", fim=None, entrega="2021-11-01T10:00:00", **extra):
    return {"id": id_, "natureType": natureza, "stateType": "Published", "year": int(entrega[:4]),
            "entity": entidade, "board": orgao, "role": cargo, "holder": "Nome Do Titular",
            "startDate": inicio, "endDate": fim, "deliveryDate": entrega, **extra}


def test_limpar_so_guarda_campos_publicos_permitidos():
    # mesmo que a fonte devolva campos sensíveis, nada fora da lista branca é guardado
    d = limpar(item(1, nif="123456789", birthDate="1970-01-01", spouse="X", address="Rua Y", holder="Z"))
    assert set(d) == {"id", "entidade", "orgao", "cargo", "natureza", "estado", "ano",
                      "data_inicio", "data_fim", "data_entrega"}
    assert d["data_inicio"] == date(2021, 10, 20) and d["data_entrega"] == datetime(2021, 11, 1, 10, 0)


def test_cargo_em_funcoes_sem_cessacao():
    (c,) = consolidar_cargos([limpar(item(1)), limpar(item(2, natureza="Modification", entrega="2023-02-01T00:00:00"))])
    assert c["valido_de"] == date(2021, 10, 20) and c["valido_ate"] is None and c["declaracoes"] == [1, 2]


def test_cargo_terminado_com_cessacao():
    ds = [limpar(item(1)), limpar(item(2, natureza="Cessation", fim="2025-10-15T00:00:00", entrega="2025-11-30T00:00:00"))]
    (c,) = consolidar_cargos(ds)
    assert (c["valido_de"], c["valido_ate"]) == (date(2021, 10, 20), date(2025, 10, 15))


def test_cessacao_sem_data_fim_usa_data_de_entrega():
    (c,) = consolidar_cargos([limpar(item(1)), limpar(item(2, natureza="Final", entrega="2026-01-05T00:00:00"))])
    assert c["valido_ate"] == date(2026, 1, 5)


def test_renovacao_depois_de_cessacao_volta_a_em_funcoes():
    ds = [limpar(item(1)), limpar(item(2, natureza="Cessation", fim="2025-10-15T00:00:00", entrega="2025-11-01T00:00:00")),
          limpar(item(3, natureza="ReElection", inicio="2025-10-20T00:00:00", entrega="2025-12-01T00:00:00"))]
    (c,) = consolidar_cargos(ds)
    assert c["valido_ate"] is None


def test_cargos_distintos_nao_se_misturam():
    cs = consolidar_cargos([limpar(item(1)), limpar(item(2, cargo="Presidente", inicio="2025-10-20T00:00:00"))])
    assert sorted(c["cargo"] for c in cs) == ["Presidente", "Vereador"]


def test_chaves_correspondencia_orgaos_autarquicos():
    assert ("municipio de chaves", 0.9) in [(k, c) for k, c, _ in chaves_correspondencia("Câmara Municipal de Chaves")]
    assert ("freguesia de vilar", 0.9) in [(k, c) for k, c, _ in chaves_correspondencia("Junta de Freguesia do Vilar")]
    (unica,) = chaves_correspondencia("Imprensa Nacional-Casa da Moeda, S.A.")
    assert unica[1] == 0.95


# ---------------------------------------------------------------------------- integração
@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


def contrato(id_, adj):
    return {"idcontrato": id_, "idprocedimento": f"p{id_}", "tipoContrato": ["Aquisição de serviços"],
            "tipoprocedimento": "Ajuste Direto", "adjudicante": [adj], "adjudicatarios": ["500697370 - Fornecedor X, Lda"],
            "dataPublicacao": "10/01/2025", "dataCelebracaoContrato": "10/01/2025", "precoContratual": 1000.0,
            "cpv": ["45000000-7 - x"], "localExecucao": ["Portugal, Vila Real, Chaves"], "Ano": 2025}


class HttpFalso:
    """Simula a API (sem rede). Só os endpoints de listas; as declarações vêm de `recolher`."""
    def __init__(self, titulares, entidades):
        self.r = {"/publicquery/getallholders": titulares, "/publicquery/getallentities": entidades}

    def post(self, caminho, json):
        dados = self.r[caminho]

        class R:
            def raise_for_status(self): pass
            def json(self): return {"code": 0, "data": dados}
        return R()


@integracao
def test_recolha_consolidacao_relacoes_e_remocao(con):
    carregar(con, iter([contrato("1", "501205551 - Município de Chaves")]), ano=2025, url=None, sha256="a")
    decl = {10: [item(1), item(2, natureza="Cessation", fim="2025-10-15T00:00:00", entrega="2025-11-01T00:00:00")],
            11: [item(3, cargo="Presidente", inicio="2025-10-20T00:00:00", entrega="2025-11-10T00:00:00")],
            12: []}
    http = HttpFalso([{"value": 10, "label": "Ana  Exemplo"}, {"value": 11, "label": "Rui Exemplo"},
                      {"value": 12, "label": "Sem Declarações"}],
                     [{"value": 500, "label": "Câmara Municipal de Chaves"}])
    r = et_cargos.executar(con, pausa=0, http=http, recolher=lambda c, t, p: [limpar(i) for i in decl[t]])
    assert r["recolhidos"] == 3 and r["pessoas"] == 2  # titular sem declarações não vira pessoa
    assert r["cargos"] == 2 and r["cargos_em_funcoes"] == 1

    rels = con.execute("SELECT * FROM relacao WHERE fonte = 'et_cargos' AND substituido_em IS NULL "
                       "ORDER BY tipo, origem_id").fetchall()
    cargo_em = [x for x in rels if x["tipo"] == "cargo_em"]
    (corr,) = [x for x in rels if x["tipo"] == "corresponde_a"]
    assert {x["natureza"] for x in cargo_em} == {"documentada"} and len(cargo_em) == 2
    assert all(x["confianca"] is None for x in cargo_em)
    assert (corr["natureza"], float(corr["confianca"]), corr["origem_tipo"], corr["destino_tipo"]) == \
        ("inferida", 0.9, "entidade_et", "entidade")

    # fontes independentes: reconstruir o grafo dos contratos não mexe nas relações da ET, e vice-versa
    n_base = grafo.reconstruir(con)["correntes_por_tipo"].get("adjudicou_a")
    assert con.execute("SELECT count(*) AS n FROM relacao WHERE fonte = 'et_cargos' AND substituido_em IS NULL"
                       ).fetchone()["n"] == len(rels)
    assert n_base == 1

    # nova consolidação sem alterações: nada muda
    r2 = et_cargos.consolidar(con)
    assert (r2["relacoes_inseridas"], r2["relacoes_substituidas"]) == (0, 0)
    assert con.execute("SELECT count(*) AS n FROM relacao WHERE tipo = 'adjudicou_a' AND substituido_em IS NULL"
                       ).fetchone()["n"] == 1

    # remoção (RGPD): pessoa oculta, cargos e declarações apagados, relações retiradas e não volta na recolha
    pid = con.execute("SELECT id FROM pessoa WHERE id_externo = '10'").fetchone()["id"]
    et_cargos.remover_pessoa(con, pid)
    et_cargos.executar(con, pausa=0, http=http, recolher=lambda c, t, p: [limpar(i) for i in decl[t]])
    assert con.execute("SELECT count(*) AS n FROM et_declaracao WHERE titular_id = 10").fetchone()["n"] == 0
    assert con.execute("SELECT count(*) AS n FROM cargo WHERE pessoa_id = %s", (pid,)).fetchone()["n"] == 0
    assert con.execute("SELECT count(*) AS n FROM relacao WHERE origem_tipo = 'pessoa' AND origem_id = %s "
                       "AND substituido_em IS NULL", (pid,)).fetchone()["n"] == 0
