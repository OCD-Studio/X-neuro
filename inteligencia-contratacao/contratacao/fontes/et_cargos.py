"""Cargos públicos — Entidade para a Transparência (api.entidadetransparencia.pt, consulta pública).

O que se recolhe (e só isto):
- lista de titulares (id na fonte + nome) e lista de entidades (id + nome);
- por titular, a LISTA de declarações publicadas: entidade, órgão, cargo, natureza da declaração
  (inicial/alteração/renovação/cessação/final), ano, data de início, data de fim e data de entrega.

O que NUNCA se recolhe: o conteúdo das declarações (endpoint getdeclaration), que inclui NIF pessoal,
data de nascimento, morada, cônjuge, rendimentos e património. O filtro `CAMPOS_PERMITIDOS` garante
que nenhum outro campo é guardado, mesmo que a fonte passe a devolvê-lo.

Recolha educada: 1 pedido por segundo, User-Agent identificado, sem contornar proteções.
Retomável e progressiva: os titulares recolhidos há mais tempo (ou nunca) são os primeiros.

Consolidação (`consolidar`):
- pessoa: só titulares com pelo menos uma declaração (nome + id na fonte);
- cargo: um por pessoa x entidade x órgão x cargo; `valido_de` = início mais antigo; `valido_ate` só
  é preenchido se a declaração mais recente for de cessação ou final (senão: em funções);
- relações (fonte 'et_cargos', bitemporais):
  * pessoa -> entidade_et  'cargo_em'     DOCUMENTADA (facto publicado pela Entidade da Transparência);
  * entidade_et -> entidade 'corresponde_a' INFERIDA, com confiança: nome normalizado igual (0,95) ou
    órgão autárquico -> pessoa coletiva ("Câmara Municipal de X" -> "Município de X", 0,9).
    Só quando o alvo no BASE é único.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import date, datetime
from typing import Any, Callable, Iterable

import httpx
import psycopg

from .. import grafo
from ..normalizacao import chave_nome

log = logging.getLogger(__name__)

FONTE = "et_cargos"
API = "https://api.entidadetransparencia.pt"
URL_CONSULTA = "https://entidadetransparencia.pt/consultardeclaracaopublica"
USER_AGENT = "inteligencia-contratacao/0.1 (investigacao de interesse publico; contacto: OCD Studio)"
PAUSA = 1.0  # segundos entre pedidos

NATUREZAS_FIM = ("Cessation", "Final")
CONFIANCA_NOME_IGUAL = 0.95
CONFIANCA_ORGAO_AUTARQUICO = 0.9

# único conjunto de campos da lista pública que é guardado (mapeado para as colunas de et_declaracao)
CAMPOS_PERMITIDOS = {
    "id": "id", "entity": "entidade", "board": "orgao", "role": "cargo", "natureType": "natureza",
    "stateType": "estado", "year": "ano", "startDate": "data_inicio", "endDate": "data_fim",
    "deliveryDate": "data_entrega",
}


# ---------------------------------------------------------------------------------------------- rede
def cliente() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=60,
                        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"})


def _post(c: httpx.Client, caminho: str, corpo: dict) -> Any:
    r = c.post(caminho, json=corpo)
    r.raise_for_status()
    d = r.json()
    if d.get("code") not in (0, None):
        raise RuntimeError(f"{caminho}: código {d.get('code')} {d.get('description')}")
    return d["data"]


def listar_titulares(c: httpx.Client) -> list[dict]:
    return _post(c, "/publicquery/getallholders", {})


def listar_entidades(c: httpx.Client) -> list[dict]:
    return _post(c, "/publicquery/getallentities", {})


def recolher_titular(c: httpx.Client, titular_id: int, pausa: float = PAUSA) -> list[dict]:
    """Todas as páginas da lista de declarações de um titular, já filtradas (`limpar`)."""
    out, pagina = [], 1
    while True:
        d = _post(c, "/publicquery/search",
                  {"pageNumber": pagina, "sortBy": None, "holderId": titular_id, "requiredUser": True})
        out += [limpar(i) for i in d.get("items") or []]
        if pagina >= (d.get("pageCount") or 0):
            return out
        pagina += 1
        time.sleep(pausa)


# ------------------------------------------------------------------------------------- transformação
def _data(v: Any) -> date | None:
    if not v:
        return None
    return datetime.fromisoformat(str(v)[:19]).date()


def limpar(item: dict) -> dict:
    """Mantém apenas os campos públicos permitidos (garantia de minimização)."""
    d = {col: item.get(campo) for campo, col in CAMPOS_PERMITIDOS.items()}
    for k in ("entidade", "orgao", "cargo"):
        d[k] = " ".join(d[k].split()) if isinstance(d[k], str) and d[k].strip() else None
    d["data_inicio"], d["data_fim"] = _data(d["data_inicio"]), _data(d["data_fim"])
    d["data_entrega"] = datetime.fromisoformat(str(d["data_entrega"])[:19]) if d["data_entrega"] else None
    return d


def consolidar_cargos(declaracoes: Iterable[dict]) -> list[dict]:
    """Agrupa as declarações de UM titular em cargos com período de validade (função pura, testada)."""
    grupos: dict[tuple, list[dict]] = {}
    for d in declaracoes:
        if not d.get("entidade"):
            continue
        grupos.setdefault((d["entidade"], d.get("orgao"), d.get("cargo")), []).append(d)
    out = []
    for (ent, orgao, cargo), ds in grupos.items():
        ds = sorted(ds, key=lambda d: (d.get("data_entrega") or datetime.min, d["id"]))
        ultima = ds[-1]
        inicios = [d["data_inicio"] for d in ds if d.get("data_inicio")]
        valido_ate = None
        if ultima.get("natureza") in NATUREZAS_FIM:
            fins = [d["data_fim"] for d in ds if d.get("data_fim")]
            valido_ate = max(fins) if fins else (ultima["data_entrega"].date() if ultima.get("data_entrega") else None)
        out.append({"entidade_nome": ent, "orgao": orgao, "cargo": cargo,
                    "valido_de": min(inicios) if inicios else None, "valido_ate": valido_ate,
                    "declaracoes": [d["id"] for d in ds]})
    return out


_ORGAOS_AUTARQUICOS = [
    (re.compile(r"^(camara municipal|assembleia municipal) d[aeo]s? (.+)$"), "municipio de {}"),
    (re.compile(r"^(junta de freguesia|assembleia de freguesia) d[aeo]s? (.+)$"), "freguesia de {}"),
    (re.compile(r"^(uniao das freguesias) d[aeo]s? (.+)$"), "uniao das freguesias de {}"),
]


def chaves_correspondencia(nome_et: str) -> list[tuple[str, float, str]]:
    """Chaves de nome a procurar no BASE para uma entidade da ET: (chave, confiança, método)."""
    k = chave_nome(nome_et)
    out = [(k, CONFIANCA_NOME_IGUAL, "nome normalizado igual a um dos nomes registados no BASE")]
    for rx, modelo in _ORGAOS_AUTARQUICOS:
        m = rx.match(k)
        if m:
            out.append((modelo.format(m.group(2)), CONFIANCA_ORGAO_AUTARQUICO,
                        f"órgão autárquico -> pessoa coletiva ({m.group(1)})"))
    return out


# ------------------------------------------------------------------------------------------ BD
def carregar_listas(con: psycopg.Connection, titulares: list[dict], entidades: list[dict]) -> None:
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO meta.et_titular (titular_id, nome) VALUES (%s, %s) "
            "ON CONFLICT (titular_id) DO UPDATE SET nome = EXCLUDED.nome",
            [(t["value"], " ".join(str(t["label"]).split())) for t in titulares])
        cur.executemany(
            "INSERT INTO entidade_et (id, nome) VALUES (%s, %s) "
            "ON CONFLICT (id) DO UPDATE SET nome = EXCLUDED.nome, atualizado_em = now() "
            "WHERE entidade_et.nome IS DISTINCT FROM EXCLUDED.nome",
            [(e["value"], " ".join(str(e["label"]).split())) for e in entidades])
    con.commit()


def carregar_titular(con: psycopg.Connection, titular_id: int, declaracoes: list[dict]) -> None:
    """Substitui as declarações guardadas de um titular pelas recolhidas agora."""
    con.execute("DELETE FROM et_declaracao WHERE titular_id = %s", (titular_id,))
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO et_declaracao (id, titular_id, entidade, orgao, cargo, natureza, estado, ano, "
            "data_inicio, data_fim, data_entrega) VALUES (%(id)s, %(t)s, %(entidade)s, %(orgao)s, %(cargo)s, "
            "%(natureza)s, %(estado)s, %(ano)s, %(data_inicio)s, %(data_fim)s, %(data_entrega)s) "
            "ON CONFLICT (id) DO UPDATE SET titular_id = EXCLUDED.titular_id, entidade = EXCLUDED.entidade, "
            "orgao = EXCLUDED.orgao, cargo = EXCLUDED.cargo, natureza = EXCLUDED.natureza, estado = EXCLUDED.estado, "
            "ano = EXCLUDED.ano, data_inicio = EXCLUDED.data_inicio, data_fim = EXCLUDED.data_fim, "
            "data_entrega = EXCLUDED.data_entrega, recolhido_em = now()",
            [{**d, "t": titular_id} for d in declaracoes])
    con.execute("UPDATE meta.et_titular SET ultima_recolha = now(), n_declaracoes = %s, erro = NULL "
                "WHERE titular_id = %s", (len(declaracoes), titular_id))
    con.commit()


def _removidos(con: psycopg.Connection) -> set[int]:
    return {int(l["id_externo"]) for l in con.execute(
        "SELECT id_externo FROM pessoa WHERE fonte = %s AND removido_em IS NOT NULL", (FONTE,))}


def executar(con: psycopg.Connection, *, max_titulares: int | None = None, pausa: float = PAUSA,
             http: httpx.Client | None = None,
             recolher: Callable[[httpx.Client, int, float], list[dict]] = recolher_titular) -> dict[str, Any]:
    """Atualiza listas, recolhe até `max_titulares` titulares (mais antigos primeiro) e consolida."""
    c = http or cliente()
    carregar_listas(con, listar_titulares(c), listar_entidades(c))
    removidos = _removidos(con)
    sql = ("SELECT titular_id FROM meta.et_titular ORDER BY ultima_recolha NULLS FIRST, titular_id"
           + (" LIMIT %s" % int(max_titulares) if max_titulares else ""))
    alvo = [l["titular_id"] for l in con.execute(sql) if l["titular_id"] not in removidos]
    ok = falhas = 0
    for i, tid in enumerate(alvo):
        time.sleep(pausa)
        try:
            carregar_titular(con, tid, recolher(c, tid, pausa))
            ok += 1
        except Exception as e:  # um titular falhado não trava os outros
            con.rollback()
            falhas += 1
            log.warning("ET titular %s falhou: %r", tid, e)
            con.execute("UPDATE meta.et_titular SET erro = %s, ultima_recolha = now() WHERE titular_id = %s",
                        (repr(e)[:500], tid))
            con.commit()
        if (i + 1) % 200 == 0:
            log.info("ET: %d/%d titulares recolhidos (%d falhas)", i + 1, len(alvo), falhas)
    resumo = {"recolhidos": ok, "falhas": falhas, **consolidar(con)}
    log.info("ET cargos: %s", resumo)
    return resumo


def remover_pessoa(con: psycopg.Connection, pessoa_id: int) -> None:
    """Pedido de remoção (RGPD): oculta a pessoa, apaga cargos e declarações guardadas, retira relações
    (ficam substituídas no histórico de conhecimento, sem dados novos) e deixa de a recolher."""
    l = con.execute("UPDATE pessoa SET removido_em = now() WHERE id = %s RETURNING fonte, id_externo",
                    (pessoa_id,)).fetchone()
    if l and l["fonte"] == FONTE:
        con.execute("DELETE FROM et_declaracao WHERE titular_id = %s", (int(l["id_externo"]),))
    con.execute("DELETE FROM cargo WHERE pessoa_id = %s", (pessoa_id,))
    con.execute("UPDATE relacao SET substituido_em = clock_timestamp() WHERE substituido_em IS NULL AND "
                "((origem_tipo = 'pessoa' AND origem_id = %(p)s) OR (destino_tipo = 'pessoa' AND destino_id = %(p)s))",
                {"p": pessoa_id})
    con.commit()


def consolidar(con: psycopg.Connection) -> dict[str, Any]:
    """Recalcula pessoas, cargos e relações a partir das declarações guardadas."""
    removidos = _removidos(con)
    # pessoas: só titulares com declarações
    con.execute(
        "INSERT INTO pessoa (nome, fonte, id_externo) "
        "SELECT t.nome, %s, t.titular_id::text FROM meta.et_titular t "
        "WHERE EXISTS (SELECT 1 FROM et_declaracao d WHERE d.titular_id = t.titular_id) "
        "ON CONFLICT (fonte, id_externo) DO UPDATE SET nome = EXCLUDED.nome WHERE pessoa.nome <> EXCLUDED.nome",
        (FONTE,))
    pessoas = {int(l["id_externo"]): l["id"] for l in con.execute(
        "SELECT id, id_externo FROM pessoa WHERE fonte = %s AND removido_em IS NULL", (FONTE,))}
    ent_et = {chave_nome(l["nome"]): l["id"] for l in con.execute("SELECT id, nome FROM entidade_et")}

    por_titular: dict[int, list[dict]] = {}
    for d in con.execute("SELECT * FROM et_declaracao ORDER BY titular_id, id"):
        por_titular.setdefault(d["titular_id"], []).append(d)

    cargos, sem_entidade = [], set()
    for tid, ds in por_titular.items():
        if tid in removidos or tid not in pessoas:
            continue
        for c in consolidar_cargos(ds):
            c["pessoa_id"] = pessoas[tid]
            c["entidade_et_id"] = ent_et.get(chave_nome(c["entidade_nome"]))
            if c["entidade_et_id"] is None:
                sem_entidade.add(c["entidade_nome"])
            cargos.append(c)

    cur = con.cursor()
    cur.execute("CREATE TEMP TABLE cargo_novo (LIKE cargo INCLUDING DEFAULTS) ON COMMIT DROP")
    with cur.copy("COPY cargo_novo (pessoa_id, entidade_et_id, entidade_nome, orgao, cargo, valido_de, valido_ate, "
                  "declaracoes) FROM STDIN") as cp:
        for c in cargos:
            cp.write_row((c["pessoa_id"], c["entidade_et_id"], c["entidade_nome"], c["orgao"], c["cargo"],
                          c["valido_de"], c["valido_ate"], c["declaracoes"]))
    cur.execute("DELETE FROM cargo c WHERE NOT EXISTS (SELECT 1 FROM cargo_novo n WHERE n.pessoa_id = c.pessoa_id "
                "AND n.entidade_nome = c.entidade_nome AND n.orgao IS NOT DISTINCT FROM c.orgao "
                "AND n.cargo IS NOT DISTINCT FROM c.cargo)")
    cur.execute(
        "INSERT INTO cargo (pessoa_id, entidade_et_id, entidade_nome, orgao, cargo, valido_de, valido_ate, declaracoes) "
        "SELECT pessoa_id, entidade_et_id, entidade_nome, orgao, cargo, valido_de, valido_ate, declaracoes "
        "FROM cargo_novo ON CONFLICT (pessoa_id, entidade_nome, orgao, cargo) DO UPDATE SET "
        "entidade_et_id = EXCLUDED.entidade_et_id, valido_de = EXCLUDED.valido_de, valido_ate = EXCLUDED.valido_ate, "
        "declaracoes = EXCLUDED.declaracoes, atualizado_em = now() "
        "WHERE (cargo.entidade_et_id, cargo.valido_de, cargo.valido_ate, cargo.declaracoes) IS DISTINCT FROM "
        "(EXCLUDED.entidade_et_id, EXCLUDED.valido_de, EXCLUDED.valido_ate, EXCLUDED.declaracoes)")
    # nota: UNIQUE com orgao/cargo NULL não colide; a fonte preenche sempre ambos (lacunas -> qualidade)

    correspondencias = _correspondencias(con)  # antes do COPY (a ligação não aceita consultas durante o COPY)
    cur.execute(grafo.SQL_REL_NOVA)
    cur.execute(
        """
        INSERT INTO rel_nova (origem_tipo, origem_id, destino_tipo, destino_id, tipo, natureza, confianca,
                              valido_de, valido_ate, fonte_data, detalhe)
        SELECT 'pessoa', pessoa_id, 'entidade_et', entidade_et_id, 'cargo_em', 'documentada', NULL,
               min(valido_de), CASE WHEN bool_or(valido_ate IS NULL) THEN NULL ELSE max(valido_ate) END, NULL,
               jsonb_build_object('cargos', jsonb_agg(jsonb_build_object(
                   'orgao', orgao, 'cargo', cargo, 'de', valido_de, 'ate', valido_ate, 'declaracoes', declaracoes)
                   ORDER BY valido_de NULLS LAST, orgao, cargo),
                   'fonte_url', %s::text)
        FROM cargo WHERE entidade_et_id IS NOT NULL
        GROUP BY pessoa_id, entidade_et_id
        """, (URL_CONSULTA,))
    with cur.copy("COPY rel_nova (origem_tipo, origem_id, destino_tipo, destino_id, tipo, natureza, confianca, "
                  "valido_de, valido_ate, fonte_data, detalhe) FROM STDIN") as cp:
        for linha in correspondencias:
            cp.write_row(linha)
    inseridas, substituidas = grafo.aplicar_diferencas(cur, FONTE)
    n = con.execute("SELECT (SELECT count(*) FROM pessoa WHERE fonte = %s AND removido_em IS NULL) AS pessoas, "
                    "(SELECT count(*) FROM cargo) AS cargos, "
                    "(SELECT count(*) FROM cargo WHERE valido_ate IS NULL) AS cargos_em_funcoes", (FONTE,)).fetchone()
    resumo = {**n, "relacoes_inseridas": inseridas, "relacoes_substituidas": substituidas,
              "correspondencias_base": len(correspondencias),
              "entidades_sem_id_na_fonte": len(sem_entidade)}
    con.execute("INSERT INTO meta.et_consolidacao (resumo) VALUES (%s)",
                (json.dumps({**resumo, "exemplos_sem_id": sorted(sem_entidade)[:20]}, ensure_ascii=False),))
    con.commit()
    return resumo


def _correspondencias(con: psycopg.Connection) -> list[tuple]:
    """entidade_et -> entidade do BASE (inferida). Só pessoas coletivas com NIF e alvo único por chave."""
    usadas = {l["entidade_et_id"] for l in con.execute(
        "SELECT DISTINCT entidade_et_id FROM cargo WHERE entidade_et_id IS NOT NULL")}
    if not usadas:
        return []
    por_chave: dict[str, set[int]] = {}
    for l in con.execute("SELECT n.entidade_id, n.nome FROM entidade_nome n JOIN entidade e ON e.id = n.entidade_id "
                         "WHERE e.identificado_por = 'nif' AND e.tipo_pessoa = 'coletiva'"):
        por_chave.setdefault(chave_nome(l["nome"]), set()).add(l["entidade_id"])
    out = []
    for l in con.execute("SELECT id, nome FROM entidade_et WHERE id = ANY(%s)", (list(usadas),)):
        for chave, conf, metodo in chaves_correspondencia(l["nome"]):
            alvos = por_chave.get(chave, set())
            if len(alvos) == 1:
                out.append(("entidade_et", l["id"], "entidade", next(iter(alvos)), "corresponde_a", "inferida", conf,
                            None, None, None, json.dumps({"metodo": metodo, "nome_et": l["nome"]}, ensure_ascii=False)))
                break
    return out
