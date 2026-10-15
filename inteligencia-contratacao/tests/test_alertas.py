"""Resumo diário de alertas: sempre rascunho, só o que é novo, minimização e mudanças de nível."""

import os
from datetime import date, datetime, timedelta, timezone

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import alertas, bd, pontuacao
from contratacao.fontes.base_impic import carregar

URL = os.environ.get("TEST_DATABASE_URL")
integracao = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")


def conteudo_minimo(**extra):
    c = {"desde": "2026-10-14T06:00:00+00:00", "ate": "2026-10-15T06:00:00+00:00", "url_base": "https://x.pt",
         "n_contratos": 0, "contratos": [], "fontes": {"sincronizacoes": 1, "com_problemas": []},
         "niveis": {"linha_base": True, "entraram_alto": [], "n_entraram_alto": 0, "sairam_alto": [], "n_sairam_alto": 0}}
    c.update(extra)
    return c


def test_markdown_tem_aviso_e_linguagem_de_risco():
    t = alertas.para_markdown(conteudo_minimo())
    assert "RASCUNHO PARA REVISÃO HUMANA" in t and "não são prova nem acusação" in t
    assert "linha de base" in t
    for proibida in ("corrup", "culpad", "fraude", "crime"):
        assert proibida not in t.lower()


def test_markdown_lista_contratos_e_excedente():
    k = {"adjudicante": "Município A", "adjudicante_id": 1, "fornecedores": [{"id": None, "nome": "pessoa singular (não identificada)"}],
         "objeto": "Obra", "preco_contratual": "150000.00", "procedimento": "ajuste_direto", "sinais": ["Concorrente único"],
         "pontos": 7, "url_fonte": "https://www.base.gov.pt/x"}
    t = alertas.para_markdown(conteudo_minimo(n_contratos=3, contratos=[k]))
    assert "**Município A** → pessoa singular (não identificada)" in t and "150 000 €" in t
    assert "e mais 2" in t and "https://x.pt/entidade/1" in t


# ---------------------------------------------------------------------------- integração
@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


def reg(id_, data: date, forn="510728189 - Fornecedor X, Lda"):
    d = data.strftime("%d/%m/%Y")
    return {"idcontrato": id_, "idprocedimento": f"p{id_}", "tipoContrato": ["Aquisição de serviços"],
            "tipoprocedimento": "Concurso público", "adjudicante": ["504615947 - Município A"], "adjudicatarios": [forn],
            "dataPublicacao": d, "dataCelebracaoContrato": d, "precoContratual": 100000.0, "cpv": ["45000000-7 - x"],
            "localExecucao": ["Portugal, Porto, Porto"], "Ano": data.year, "concorrentes": [forn]}


@integracao
def test_resumo_so_novos_recentes_minimizado_e_rascunho(con):
    r1 = alertas.gerar(con)
    assert r1["linha_base"] and r1["estado"] == "rascunho"

    hoje = date.today()
    carregar(con, iter([reg("novo", hoje), reg("singular", hoje, forn="- - Maria Silva")]), ano=hoje.year, url=None, sha256="a")
    carregar(con, iter([reg("antigo", date(2015, 3, 2))]), ano=2015, url=None, sha256="b")  # backfill: não é "novo"
    pontuacao.recalcular(con)
    r2 = alertas.gerar(con, agora=datetime.now(timezone.utc) + timedelta(seconds=1))
    linha = con.execute("SELECT * FROM alerta_resumo WHERE id = %s", (r2["id"],)).fetchone()
    assert linha["estado"] == "rascunho"
    ids = sorted(k["id"] for k in linha["conteudo"]["contratos"])
    origens = {l["id"]: l["id_origem"] for l in con.execute("SELECT id, id_origem FROM contrato")}
    assert sorted(origens[i] for i in ids) == ["novo", "singular"]
    assert "Maria Silva" not in linha["texto"] and "pessoa singular (não identificada)" in linha["texto"]
    # a janela seguinte começa onde a anterior acabou: nada se repete
    r3 = alertas.gerar(con, agora=datetime.now(timezone.utc) + timedelta(seconds=2))
    assert r3["n_contratos"] == 0


@integracao
def test_mudancas_de_nivel(con):
    carregar(con, iter([reg("1", date.today())]), ano=date.today().year, url=None, sha256="a")
    pontuacao.recalcular(con)
    alertas.gerar(con)  # linha de base
    eid = con.execute("SELECT id FROM entidade WHERE nif = '504615947'").fetchone()["id"]
    con.execute("UPDATE resumo_entidade SET score = 80 WHERE papel = 'a' AND entidade_id = %s", (eid,))
    con.commit()
    r = alertas.gerar(con, agora=datetime.now(timezone.utc) + timedelta(seconds=1))
    c = con.execute("SELECT conteudo FROM alerta_resumo WHERE id = %s", (r["id"],)).fetchone()["conteudo"]
    assert [(e["id"], e["papel"], e["score"]) for e in c["niveis"]["entraram_alto"]] == [(eid, "adjudicante", 80)]
    con.execute("UPDATE resumo_entidade SET score = 10 WHERE papel = 'a' AND entidade_id = %s", (eid,))
    con.commit()
    r = alertas.gerar(con, agora=datetime.now(timezone.utc) + timedelta(seconds=2))
    c = con.execute("SELECT conteudo FROM alerta_resumo WHERE id = %s", (r["id"],)).fetchone()["conteudo"]
    assert c["niveis"]["n_sairam_alto"] == 1 and c["niveis"]["n_entraram_alto"] == 0


def test_revisao_exige_token(monkeypatch):
    """Sem ADMIN_TOKEN a revisão pela app está desativada; com token errado é recusada (antes de tocar na BD)."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from contratacao import api_alertas
    app = FastAPI()
    app.include_router(api_alertas.router)
    c = TestClient(app)
    monkeypatch.delenv("ADMIN_TOKEN", raising=False)
    assert c.post("/api/alertas/1/estado", json={"estado": "revisto"}).status_code == 403
    monkeypatch.setenv("ADMIN_TOKEN", "certo")
    r = c.post("/api/alertas/1/estado", json={"estado": "revisto"}, headers={"X-Token": "errado"})
    assert r.status_code == 403 and r.json()["detail"] == "Token inválido."
    assert c.post("/api/alertas/1/estado", json={"estado": "enviado"}, headers={"X-Token": "certo"}).status_code == 422
