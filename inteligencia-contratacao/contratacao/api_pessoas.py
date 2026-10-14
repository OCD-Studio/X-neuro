"""API de pessoas (titulares de cargos públicos) e do organigrama.

Princípios (ver README e /metodologia):
- só factos de registo público: cargos declarados à Entidade para a Transparência, com fonte e data;
- nada sobre a vida privada; nenhuma relação pessoal é inferida;
- cargo exercido = relação DOCUMENTADA; correspondência entre a entidade da ET e a do BASE = relação
  INFERIDA, sempre com grau de confiança, e nunca apresentada como facto;
- pessoas com pedido de remoção (pessoa.removido_em) não aparecem em lado nenhum.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from . import bd
from .fontes.et_cargos import FONTE, URL_CONSULTA

router = APIRouter()

MAX_PESSOAS_POR_ENTIDADE = 150
FONTE_ROTULO = "Entidade para a Transparência — declarações públicas (lista de declarações)"


def _cond_periodo(de: date | None, ate: date | None, estado: str, alias: str = "c") -> tuple[str, dict]:
    cond, par = [], {}
    if de:  # sobreposição de períodos
        cond.append(f"({alias}.valido_ate IS NULL OR {alias}.valido_ate >= %(de)s)"); par["de"] = de
    if ate:
        cond.append(f"({alias}.valido_de IS NULL OR {alias}.valido_de <= %(ate)s)"); par["ate"] = ate
    if estado == "atuais":
        cond.append(f"({alias}.valido_ate IS NULL OR {alias}.valido_ate >= current_date)")
    elif estado == "passadas":
        cond.append(f"{alias}.valido_ate < current_date")
    return (" AND " + " AND ".join(cond)) if cond else "", par


_SQL_CORRESPONDENCIA = """
    SELECT r.origem_id AS entidade_et_id, e.id AS entidade_id, e.nome, r.confianca, r.detalhe->>'metodo' AS metodo
    FROM relacao r JOIN entidade e ON e.id = r.destino_id
    WHERE r.tipo = 'corresponde_a' AND r.origem_tipo = 'entidade_et' AND r.destino_tipo = 'entidade'
      AND r.substituido_em IS NULL AND r.origem_id = ANY(%(ids)s)
"""


def _correspondencias(con, ids: list[int]) -> dict[int, dict]:
    if not ids:
        return {}
    return {l["entidade_et_id"]: l for l in con.execute(_SQL_CORRESPONDENCIA, {"ids": ids})}


def _pessoa_visivel(con, pessoa_id: int) -> dict:
    p = con.execute("SELECT id, nome, fonte, id_externo FROM pessoa WHERE id = %s AND removido_em IS NULL",
                    (pessoa_id,)).fetchone()
    if not p:
        raise HTTPException(404, "Pessoa não encontrada (ou removida a pedido do titular).")
    return p


@router.get("/api/pessoas")
def procurar_pessoas(q: str = Query(..., min_length=3), limite: int = Query(30, le=100)):
    """Procura titulares de cargos públicos por nome (só pessoas com cargos registados)."""
    with bd.ligar() as con:
        return con.execute(
            """
            SELECT p.id, p.nome, count(c.*) AS n_cargos, bool_or(c.valido_ate IS NULL) AS em_funcoes,
                   (array_agg(c.cargo || ' — ' || c.entidade_nome ORDER BY c.valido_ate DESC NULLS FIRST,
                              c.valido_de DESC NULLS LAST))[1] AS cargo_recente
            FROM pessoa p JOIN cargo c ON c.pessoa_id = p.id
            WHERE p.removido_em IS NULL AND unaccent_simples(p.nome) ILIKE '%%' || unaccent_simples(%(q)s) || '%%'
            GROUP BY p.id ORDER BY p.nome LIMIT %(l)s
            """, {"q": q, "l": limite}).fetchall()


@router.get("/api/entidades-et")
def procurar_entidades_et(q: str = Query(..., min_length=3), limite: int = Query(30, le=100)):
    """Procura entidades tal como a Entidade para a Transparência as nomeia (com cargos registados)."""
    with bd.ligar() as con:
        return con.execute(
            """
            SELECT x.id, x.nome, count(DISTINCT c.pessoa_id) AS n_pessoas
            FROM entidade_et x JOIN cargo c ON c.entidade_et_id = x.id JOIN pessoa p ON p.id = c.pessoa_id
            WHERE p.removido_em IS NULL AND unaccent_simples(x.nome) ILIKE '%%' || unaccent_simples(%(q)s) || '%%'
            GROUP BY x.id ORDER BY x.nome LIMIT %(l)s
            """, {"q": q, "l": limite}).fetchall()


@router.get("/api/pessoas/{pessoa_id}")
def perfil_pessoa(pessoa_id: int):
    """Perfil: só cargos públicos registados, com período, fonte e declarações que os suportam."""
    with bd.ligar() as con:
        p = _pessoa_visivel(con, pessoa_id)
        cargos = con.execute(
            """
            SELECT c.entidade_et_id, c.entidade_nome, c.orgao, c.cargo, c.valido_de, c.valido_ate,
                   (c.valido_ate IS NULL OR c.valido_ate >= current_date) AS atual,
                   cardinality(c.declaracoes) AS n_declaracoes, c.atualizado_em,
                   (SELECT max(d.data_entrega)::date FROM et_declaracao d WHERE d.id = ANY(c.declaracoes)) AS ultima_declaracao
            FROM cargo c WHERE c.pessoa_id = %s
            ORDER BY atual DESC, c.valido_de DESC NULLS LAST
            """, (pessoa_id,)).fetchall()
        corr = _correspondencias(con, [c["entidade_et_id"] for c in cargos if c["entidade_et_id"]])
        recolha = con.execute("SELECT ultima_recolha FROM meta.et_titular WHERE titular_id = %s",
                              (int(p["id_externo"]),)).fetchone() if p["fonte"] == FONTE else None
    for c in cargos:
        c["correspondencia_base"] = corr.get(c["entidade_et_id"])
    return {"pessoa": {"id": p["id"], "nome": p["nome"]}, "cargos": cargos,
            "fonte": {"nome": FONTE_ROTULO, "url": URL_CONSULTA,
                      "recolhido_em": recolha["ultima_recolha"] if recolha else None,
                      "nota": "A fonte não tem ligação direta por declaração: pesquisar pelo nome do titular."}}


@router.get("/api/organigrama")
def organigrama(
    no: str = Query(..., pattern=r"^(pessoa|entidade_et|entidade):\d+$"),
    de: date | None = None,
    ate: date | None = None,
    estado: Literal["todas", "atuais", "passadas"] = "todas",
):
    """Vizinhança de 1.º grau de um nó para o organigrama interativo (expandir = pedir o vizinho).

    - entidade_et: órgãos e pessoas com cargo lá + entidade do BASE correspondente (inferida);
    - pessoa: entidades onde tem/teve cargo (+ correspondência no BASE, inferida);
    - entidade (BASE): entidades da ET que lhe correspondem (inferidas).
    """
    tipo, nid = no.split(":")[0], int(no.split(":")[1])
    cond, par = _cond_periodo(de, ate, estado)
    par["id"] = nid
    nos: dict[str, dict] = {}
    arestas: list[dict] = []
    omitidas = 0

    def no_et(i, nome):
        nos.setdefault(f"entidade_et:{i}", {"id": f"entidade_et:{i}", "tipo": "entidade_et", "nome": nome})

    def no_base(l):
        nos.setdefault(f"entidade:{l['entidade_id']}", {"id": f"entidade:{l['entidade_id']}", "tipo": "entidade",
                                                         "nome": l["nome"]})
        arestas.append({"id": f"corr:{l['entidade_et_id']}", "source": f"entidade_et:{l['entidade_et_id']}",
                        "target": f"entidade:{l['entidade_id']}", "tipo": "corresponde_a",
                        "natureza": "inferida", "confianca": l["confianca"], "metodo": l["metodo"],
                        "rotulo": "possivelmente corresponde a"})

    def aresta_cargo(l):
        """pessoa -> órgão (cargo, documentado); órgão -> entidade (estrutura, só para desenhar a árvore)."""
        et = f"entidade_et:{l['entidade_et_id']}"
        orgao_id = f"orgao:{l['entidade_et_id']}:{l['orgao'] or '—'}"
        if orgao_id not in nos:
            nos[orgao_id] = {"id": orgao_id, "tipo": "orgao", "nome": l["orgao"] or "(órgão não indicado)"}
            arestas.append({"id": f"estrutura:{orgao_id}", "source": orgao_id, "target": et, "tipo": "estrutura",
                            "natureza": "estrutura", "rotulo": ""})
        arestas.append({"id": f"cargo:{l['pessoa_id']}:{l['entidade_et_id']}:{l['orgao']}:{l['cargo']}",
                        "source": f"pessoa:{l['pessoa_id']}", "target": orgao_id, "entidade_et": et,
                        "tipo": "cargo_em", "natureza": "documentada", "orgao": l["orgao"], "cargo": l["cargo"],
                        "valido_de": l["valido_de"], "valido_ate": l["valido_ate"], "atual": l["atual"],
                        "rotulo": l["cargo"], "fonte": FONTE_ROTULO})

    sel = ("SELECT c.pessoa_id, p.nome AS pessoa, c.entidade_et_id, x.nome AS entidade, c.orgao, c.cargo, "
           "c.valido_de, c.valido_ate, (c.valido_ate IS NULL OR c.valido_ate >= current_date) AS atual "
           "FROM cargo c JOIN pessoa p ON p.id = c.pessoa_id AND p.removido_em IS NULL "
           "JOIN entidade_et x ON x.id = c.entidade_et_id ")
    with bd.ligar() as con:
        if tipo == "pessoa":
            p = _pessoa_visivel(con, nid)
            nos[no] = {"id": no, "tipo": "pessoa", "nome": p["nome"]}
            linhas = con.execute(sel + "WHERE c.pessoa_id = %(id)s" + cond + " ORDER BY c.valido_de", par).fetchall()
            for l in linhas:
                no_et(l["entidade_et_id"], l["entidade"])
                aresta_cargo(l)
            for l in _correspondencias(con, list({l["entidade_et_id"] for l in linhas})).values():
                no_base(l)
        elif tipo == "entidade_et":
            x = con.execute("SELECT id, nome FROM entidade_et WHERE id = %s", (nid,)).fetchone()
            if not x:
                raise HTTPException(404, "Entidade não encontrada.")
            no_et(nid, x["nome"])
            linhas = con.execute(
                sel + "WHERE c.entidade_et_id = %(id)s" + cond +
                " ORDER BY atual DESC, c.valido_de DESC NULLS LAST", par).fetchall()
            pessoas_vistas: set[int] = set()
            for l in linhas:
                if l["pessoa_id"] not in pessoas_vistas and len(pessoas_vistas) >= MAX_PESSOAS_POR_ENTIDADE:
                    omitidas += 1
                    continue
                pessoas_vistas.add(l["pessoa_id"])
                pid = f"pessoa:{l['pessoa_id']}"
                nos.setdefault(pid, {"id": pid, "tipo": "pessoa", "nome": l["pessoa"]})
                aresta_cargo(l)
            for l in _correspondencias(con, [nid]).values():
                no_base(l)
        else:
            e = con.execute("SELECT id, nome FROM entidade WHERE id = %s AND tipo_pessoa = 'coletiva'",
                            (nid,)).fetchone()
            if not e:
                raise HTTPException(404, "Entidade não encontrada ou sem perfil (minimização de dados pessoais).")
            nos[no] = {"id": no, "tipo": "entidade", "nome": e["nome"]}
            for l in con.execute(
                    "SELECT r.origem_id AS entidade_et_id, x.nome AS nome_et, r.confianca, r.detalhe->>'metodo' AS metodo "
                    "FROM relacao r JOIN entidade_et x ON x.id = r.origem_id WHERE r.tipo = 'corresponde_a' "
                    "AND r.origem_tipo = 'entidade_et' AND r.destino_tipo = 'entidade' AND r.destino_id = %s "
                    "AND r.substituido_em IS NULL", (nid,)):
                no_et(l["entidade_et_id"], l["nome_et"])
                no_base({**l, "entidade_id": nid, "nome": e["nome"]})
    return {"centro": no, "nos": list(nos.values()), "arestas": arestas, "pessoas_omitidas": omitidas}


@router.get("/api/entidades/{entidade_id}/cargos")
def cargos_da_entidade(entidade_id: int):
    """Para o perfil de entidade: entidades da ET que possivelmente lhe correspondem (INFERIDO) e
    contagem de titulares com cargos registados (atuais / passados)."""
    with bd.ligar() as con:
        return con.execute(
            """
            SELECT x.id, x.nome, r.confianca, r.detalhe->>'metodo' AS metodo,
                   count(DISTINCT c.pessoa_id) FILTER (WHERE c.valido_ate IS NULL OR c.valido_ate >= current_date) AS atuais,
                   count(DISTINCT c.pessoa_id) FILTER (WHERE c.valido_ate < current_date) AS passados
            FROM relacao r JOIN entidade_et x ON x.id = r.origem_id
            LEFT JOIN cargo c ON c.entidade_et_id = x.id
                 AND EXISTS (SELECT 1 FROM pessoa p WHERE p.id = c.pessoa_id AND p.removido_em IS NULL)
            WHERE r.tipo = 'corresponde_a' AND r.origem_tipo = 'entidade_et' AND r.destino_tipo = 'entidade'
              AND r.destino_id = %s AND r.substituido_em IS NULL
            GROUP BY x.id, x.nome, r.confianca, r.detalhe ORDER BY x.nome
            """, (entidade_id,)).fetchall()
