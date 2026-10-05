"""Casos conhecidos dos indicadores de conjunto (SQL), contra PostgreSQL descartável."""

import os

import psycopg
import pytest
from psycopg.rows import dict_row

from contratacao import bd, pontuacao
from contratacao.fontes.base_impic import carregar

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="TEST_DATABASE_URL não definido")

CCP2017 = "Código dos Contratos Públicos ( DL 111-B/2017 )"
ART20D = "Artigo 20.º, n.º 1, alínea d) do Código dos Contratos Públicos"
ART24 = "Artigo 24.º, n.º 1, alínea e), subalínea ii) do Código dos Contratos Públicos"

NIF = {"A": "504615947", "B": "500697370", "X": "510728189", "Y": "500070210", "Z": "500072868", "W": "503439800"}


def reg(id_, data, adj="A", forn="X", preco=1000.0, proc="Ajuste Direto Regime Geral", cpv="45110000-1",
        regime=CCP2017, fund=ART20D):
    d, m, a = data.split("/")
    return {
        "idcontrato": id_, "tipoContrato": ["Aquisição de serviços"], "tipoprocedimento": proc,
        "adjudicante": [f"{NIF[adj]} - Entidade {adj}"], "adjudicatarios": [f"{NIF[forn]} - Fornecedor {forn}"],
        "dataPublicacao": data, "dataCelebracaoContrato": data, "precoContratual": preco,
        "cpv": [f"{cpv} - x"], "localExecucao": ["Portugal, Porto, Porto"], "Ano": int(a),
        "regime": regime, "fundamentacao": fund, "concorrentes": None,
    }


@pytest.fixture()
def con():
    with psycopg.connect(URL, row_factory=dict_row) as c:
        c.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public; DROP SCHEMA IF EXISTS meta CASCADE;")
        c.commit()
    bd.migrar(URL)
    with psycopg.connect(URL, row_factory=dict_row) as c:
        yield c


def carregar_e_pontuar(con, registos):
    por_ano = {}
    for r in registos:
        por_ano.setdefault(r["Ano"], []).append(r)
    for ano, rs in por_ano.items():
        carregar(con, iter(rs), ano=ano, url=None, sha256=f"t{ano}")
    pontuacao.recalcular(con)


NOME_ESTADO = {"s": "sinal", "n": "sem_sinal", "i": "dados_insuficientes"}


def estados(con, indicador):
    return {l["id_origem"]: NOME_ESTADO[l["estado"]] for l in con.execute(
        "SELECT k.id_origem, a.estado FROM avaliacao_risco a JOIN contrato k ON k.id = a.contrato_id "
        "JOIN indicador i ON i.id = a.indicador_id WHERE i.codigo = %s", (indicador,))}


def test_classificacao_regime_e_base_legal(con):
    carregar_e_pontuar(con, [
        reg("r1", "01/03/2025"),
        reg("r2", "01/03/2025", fund=ART24),
        reg("r3", "01/03/2025", regime="Código dos Contratos Públicos (DL 111-B/2017 ) e DLR nº 34/2008/M, de 14.08"),
        reg("r4", "01/03/2015", regime="Código dos Contratos Públicos (DL 18/2008)",
            fund="Artigo 20.º, n.º 1, alínea a) do Código dos Contratos Públicos"),
        reg("r5", "01/03/2025", fund=""),
    ])
    r = {l["id_origem"]: (l["regime_tipo"], l["fundamento_ad"]) for l in con.execute(
        "SELECT id_origem, regime_tipo, fundamento_ad FROM contrato")}
    assert r == {"r1": ("ccp2017", "art20d"), "r2": ("ccp2017", "outro"), "r3": ("especial_ou_regional", "art20d"),
                 "r4": ("ccp2008", "art20a"), "r5": ("ccp2017", None)}


def test_ajuste_direto_repetido(con):
    carregar_e_pontuar(con, [
        reg("a1", "10/02/2024", preco=8000),
        reg("a2", "10/02/2025", preco=8000),
        reg("a3", "10/02/2026", preco=8000),            # acumulado 24 000 ≥ 20 000 → sinal
        reg("b1", "10/02/2021", forn="Y", preco=15000),
        reg("b2", "10/02/2025", forn="Y", preco=15000),  # 2021 fora da janela de 3 anos → sem sinal
        reg("c1", "10/03/2026", forn="Z", preco=15000, cpv="30190000-7"),
        reg("c2", "10/04/2026", forn="Z", preco=15000, cpv="45000000-7"),  # outra divisão CPV → não acumula
        reg("d1", "10/05/2026", forn="W", preco=50000, fund=ART24),        # critérios materiais → n/a
    ])
    e = estados(con, "ajuste_direto_repetido")
    assert e == {"a1": "sem_sinal", "a2": "sem_sinal", "a3": "sinal", "b1": "sem_sinal", "b2": "sem_sinal",
                 "c1": "sem_sinal", "c2": "sem_sinal"}
    expl = con.execute("SELECT d.explicacao, d.evidencia FROM avaliacao_detalhe d JOIN indicador i ON i.id = d.indicador_id "
                       "WHERE i.codigo = 'ajuste_direto_repetido'").fetchone()
    assert "3 ajustes diretos" in expl["explicacao"] and "24 000,00 €" in expl["explicacao"]
    assert expl["evidencia"]["anos"] == [2024, 2026]


def test_fracionamento(con):
    carregar_e_pontuar(con, [
        reg("f1", "01/06/2025", preco=12000),
        reg("f2", "11/06/2025", preco=12000),           # 10 dias depois, soma 24 000 → ambos sinal
        reg("f3", "15/09/2025", preco=12000),           # isolado → sem sinal
        reg("g1", "02/06/2025", forn="Y", preco=12000),  # outro fornecedor → não junta com f1/f2
        reg("h1", "01/06/2025", forn="Z", preco=25000),  # acima do limiar → n/a (outro indicador)
    ])
    assert estados(con, "fracionamento") == {"f1": "sinal", "f2": "sinal", "f3": "sem_sinal", "g1": "sem_sinal"}
    assert estados(con, "ajuste_direto_acima_limiar")["h1"] == "sinal"


def test_concentracao(con):
    proc = "Concurso público"
    carregar_e_pontuar(con, [
        reg("k1", "01/02/2025", adj="B", forn="X", preco=30000, proc=proc),
        reg("k2", "01/03/2025", adj="B", forn="X", preco=30000, proc=proc),
        reg("k3", "01/04/2025", adj="B", forn="X", preco=30000, proc=proc),  # X: 90 % → sinal
        reg("k4", "01/05/2025", adj="B", forn="Y", preco=5000, proc=proc),
        reg("k5", "01/06/2025", adj="B", forn="Z", preco=5000, proc=proc),
        reg("m1", "01/02/2025", adj="A", forn="X", preco=30000, proc=proc),  # A só tem 1 contrato → n/a
    ])
    e = estados(con, "concentracao")
    assert e == {"k1": "sinal", "k2": "sinal", "k3": "sinal", "k4": "sem_sinal", "k5": "sem_sinal"}


def test_fornecedor_estreante(con):
    proc = "Concurso público"
    carregar_e_pontuar(con, [
        reg("z1", "01/02/2020", forn="Z", preco=150000, proc=proc),  # estreia com contrato grande → sinal
        reg("z2", "01/02/2021", forn="Z", preco=200000, proc=proc),  # já tem histórico → sem sinal
        reg("w1", "01/02/2013", forn="W", preco=150000, proc=proc),  # antes de 2014 → não avaliado
        reg("w2", "01/02/2015", forn="W", preco=150000, proc=proc),  # histórico desde 2013 → sem sinal
        reg("y1", "01/02/2022", forn="Y", preco=5000, proc=proc),    # pequeno → não avaliado
        reg("y2", "01/02/2023", forn="Y", preco=500000, proc=proc),  # grande mas não é estreia
    ])
    assert estados(con, "fornecedor_estreante") == {"z1": "sinal", "z2": "sem_sinal", "w2": "sem_sinal",
                                                     "y2": "sem_sinal"}


def test_calibracao_sql_igual_a_python(con):
    """Pontos calibrados em SQL = calibracao.pontos_calibrados com a taxa do grupo."""
    from contratacao.calibracao import pontos_calibrados
    regs = [reg(f"p{i}", "01/03/2025", forn="X" if i < 10 else "Y", preco=1000,
                proc="Concurso público") for i in range(40)]
    for i, r in enumerate(regs):
        r["concorrentes"] = ["510728189-X"] if i < 10 else ["510728189-X", "500070210-Y"]
    carregar_e_pontuar(con, regs)
    linhas = con.execute("SELECT a.estado, a.pontos, r.taxa, r.nivel, r.cpv_divisao, r.n_avaliaveis "
                         "FROM avaliacao_risco a JOIN indicador i ON i.id = a.indicador_id "
                         "LEFT JOIN referencia_risco r ON r.id = a.ref_id "
                         "WHERE i.codigo = 'concorrente_unico'").fetchall()
    assert len(linhas) == 40
    s = [l for l in linhas if l["estado"] == "s"]
    assert len(s) == 10 and all(l["taxa"] == pytest.approx(0.25) for l in s)
    assert all(l["pontos"] == pontos_calibrados(10, 0.25) == 8 for l in s)  # 7,5 → 8 (meio para cima)
    assert (s[0]["nivel"], s[0]["cpv_divisao"], s[0]["n_avaliaveis"]) == ("ano+procedimento+cpv", "45", 40)
    # agregados batem com o detalhe
    ag = con.execute("SELECT sum(o) o, sum(n) n FROM agregado_risco_ano a JOIN indicador i ON i.id = a.indicador_id "
                     "WHERE i.codigo = 'concorrente_unico' AND papel = 'a'").fetchone()
    assert (ag["o"], ag["n"]) == (10, 40)


def test_prazo_curto_com_prorrogacao(con):
    from contratacao.fontes.base_anuncios import carregar as carregar_anuncios

    def an(id_incm, pub, limite, desc="Obra X", tipo="Anúncio de procedimento", nif="504615947"):
        return {"IdIncm": id_incm, "nAnuncio": f"{id_incm}/2025", "dataPublicacao": pub, "nifEntidade": nif,
                "descricaoAnuncio": desc, "tipoActo": tipo, "tiposContrato": ["Aquisição de serviços"],
                "PrecoBase": "100000.00", "CPVs": ["45000000-7 - Construção"], "modeloAnuncio": "Concurso público",
                "Ano": 2025, "PrazoPropostas": 0, "DataLimitePropostas": limite}

    # 40 anúncios "normais" com 20 dias de prazo -> percentil 10 = 20 dias
    anuncios = [an(f"n{i}", "01/03/2025", "21/03/2025", desc=f"Normal {i}") for i in range(40)]
    anuncios += [
        an("curto", "01/03/2025", "06/03/2025", desc="Obra curta"),           # 5 dias -> sinal
        an("prorr", "01/03/2025", "06/03/2025", desc="Obra prorrogada"),       # 5 dias mas prorrogado...
        an("alt1", "04/03/2025", "26/03/2025", desc="Obra prorrogada", tipo="Anúncio de Alteração"),  # ...até 25 dias
    ]
    carregar_anuncios(con, iter(anuncios), ano=2025, url=None, sha256="a")
    contratos = []
    for id_incm in ["curto", "prorr", "n0", "inexistente"]:
        r = reg(f"k_{id_incm}", "01/05/2025", proc="Concurso público", preco=90000)
        r["idINCM"] = id_incm
        contratos.append(r)
    carregar_e_pontuar(con, contratos)
    assert estados(con, "prazo_curto") == {"k_curto": "sinal", "k_prorr": "sem_sinal", "k_n0": "sem_sinal",
                                           "k_inexistente": "dados_insuficientes"}
    ev = con.execute("SELECT d.evidencia FROM avaliacao_detalhe d JOIN contrato k ON k.id = d.contrato_id "
                     "JOIN indicador i ON i.id = d.indicador_id "
                     "WHERE k.id_origem = 'k_curto' AND i.codigo = 'prazo_curto'").fetchone()["evidencia"]
    assert ev["dias"] == 5 and ev["p10"] == 20
