"""Ingestão dos anúncios de procedimento do Portal BASE (dados.gov.pt), ficheiros JSON anuais.

São poucos dados (5–30 MB/ano): carregam-se por upsert sem guardar o registo bruto.
Os anúncios de alteração (prorrogações de prazo) ficam guardados e ligam-se ao anúncio
original pela entidade + descrição normalizada (ver indicador prazo_curto).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import httpx
import ijson
import psycopg

from ..config import DADOSGOV_API
from ..normalizacao import chave_nome, parse_cpv, parse_data, parse_valor
from .base_impic import Recurso, _Qualidade, descarregar  # noqa: F401  (reutilização)

DATASET_ANUNCIOS = "contratos-publicos-portal-base-impic-anuncios-de-2012-a-2026"
FONTE_ANUNCIOS = "base_anuncios"
VALOR_IMPLAUSIVEL = 10_000_000_000  # 10 mil milhões €: acima disto é quase certamente erro de registo


def listar_recursos(cliente: httpx.Client | None = None) -> dict[int, Recurso]:
    c = cliente or httpx.Client(timeout=60)
    r = c.get(f"{DADOSGOV_API}{DATASET_ANUNCIOS}/")
    r.raise_for_status()
    out: dict[int, Recurso] = {}
    for res in r.json().get("resources", []):
        m = re.fullmatch(r"anuncios(\d{4})\.json", res.get("title", "").strip().lower())
        if not m:
            continue
        ck = res.get("checksum") or {}
        lm = res.get("last_modified")
        out[int(m.group(1))] = Recurso(int(m.group(1)), res["url"], ck.get("value") if ck.get("type") == "sha1" else None,
                                       datetime.fromisoformat(lm) if lm else None, res.get("filesize"))
    return out


def normalizar_anuncio(r: dict[str, Any]) -> tuple | None:
    id_incm = str(r.get("IdIncm") or "").strip()
    if not id_incm:
        return None
    tipos = r.get("tiposContrato") or []
    cpv, _ = parse_cpv(r.get("CPVs"))
    prazo = r.get("PrazoPropostas")
    canon = json.dumps(r, sort_keys=True, ensure_ascii=False, default=str)
    return (
        id_incm, (r.get("nAnuncio") or None), r.get("Ano"), parse_data(r.get("dataPublicacao")),
        (str(r.get("nifEntidade") or "").strip() or None), chave_nome(r.get("descricaoAnuncio") or "") or None,
        r.get("tipoActo") or None, r.get("modeloAnuncio") or None, tipos[0] if tipos else None,
        parse_valor(r.get("PrecoBase")), cpv, prazo if isinstance(prazo, int) and prazo > 0 else None,
        parse_data(r.get("DataLimitePropostas")), r.get("url") or None,
        hashlib.sha256(canon.encode()).hexdigest(),
    )


def carregar(con: psycopg.Connection, registos: Iterable[dict[str, Any]], *, ano: int, url: str | None,
             sha256: str | None, sha1: str | None = None, versao: datetime | None = None,
             sincronizacao_id: int | None = None) -> dict[str, Any]:
    cur = con.cursor()
    eid = cur.execute(
        "INSERT INTO meta.execucao_ingestao (fonte, ano, url, ficheiro_sha256, checksum_fonte, versao_fonte, "
        "sincronizacao_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (FONTE_ANUNCIOS, ano, url, sha256, sha1, versao, sincronizacao_id)).fetchone()["id"]
    cur.execute("CREATE TEMP TABLE t_an (LIKE anuncio INCLUDING DEFAULTS) ON COMMIT DROP")
    q = _Qualidade()
    lidos = rejeitados = 0
    with cur.copy("COPY t_an (id_incm, n_anuncio, ano, data_publicacao, nif_entidade, descricao_chave, tipo_acto, "
                  "modelo, tipo_contrato, preco_base, cpv, prazo_propostas, data_limite, url, checksum) FROM STDIN") as cp:
        for r in registos:
            lidos += 1
            linha = normalizar_anuncio(r)
            if linha is None:
                rejeitados += 1
                q.add(None, "rejeitado", "anúncio sem IdIncm", detalhe=True)
                continue
            if linha[9] is not None and linha[9] > VALOR_IMPLAUSIVEL:
                q.add(linha[0], "aviso", f"preço base implausível (> 10 mil milhões €): {linha[9]}", detalhe=True)
            if linha[11] is None:
                q.add(linha[0], "aviso", "prazo de propostas em falta ou zero")
            if linha[12] is None:
                q.add(linha[0], "aviso", "data-limite de propostas em falta")
            cp.write_row(linha)
    res = cur.execute(
        """
        INSERT INTO anuncio SELECT DISTINCT ON (id_incm) * FROM t_an ORDER BY id_incm
        ON CONFLICT (id_incm) DO UPDATE SET n_anuncio=EXCLUDED.n_anuncio, ano=EXCLUDED.ano,
            data_publicacao=EXCLUDED.data_publicacao, nif_entidade=EXCLUDED.nif_entidade,
            descricao_chave=EXCLUDED.descricao_chave, tipo_acto=EXCLUDED.tipo_acto, modelo=EXCLUDED.modelo,
            tipo_contrato=EXCLUDED.tipo_contrato, preco_base=EXCLUDED.preco_base, cpv=EXCLUDED.cpv,
            prazo_propostas=EXCLUDED.prazo_propostas, data_limite=EXCLUDED.data_limite, url=EXCLUDED.url,
            checksum=EXCLUDED.checksum, atualizado_em=now()
        WHERE anuncio.checksum IS DISTINCT FROM EXCLUDED.checksum
        RETURNING (xmax = 0) AS inserido
        """).fetchall()
    inseridos = sum(1 for x in res if x["inserido"])
    with cur.copy("COPY meta.resumo_qualidade (execucao_id, gravidade, tipo, n, exemplos) FROM STDIN") as cp:
        for (g, t), n in q.n.items():
            cp.write_row((eid, g, t, n, q.exemplos[(g, t)]))
    cont = {"lidos": lidos, "inseridos": inseridos, "atualizados": len(res) - inseridos, "rejeitados": rejeitados}
    cur.execute("UPDATE meta.execucao_ingestao SET estado='concluido', terminado_em=clock_timestamp(), lidos=%(lidos)s, "
                "inseridos=%(inseridos)s, atualizados=%(atualizados)s, rejeitados=%(rejeitados)s WHERE id=%(id)s",
                {**cont, "id": eid})
    cur.execute(
        """
        INSERT INTO meta.cobertura (fonte, ano, estado, n_registos, checksum_fonte, versao_fonte, execucao_id)
        VALUES (%(f)s, %(a)s, 'ingerido', (SELECT count(*) FROM anuncio WHERE ano = %(a)s), %(sha1)s, %(v)s, %(e)s)
        ON CONFLICT (fonte, ano) DO UPDATE SET estado='ingerido', n_registos=EXCLUDED.n_registos,
            checksum_fonte=EXCLUDED.checksum_fonte, versao_fonte=EXCLUDED.versao_fonte,
            execucao_id=EXCLUDED.execucao_id, atualizado_em=now()
        """, {"f": FONTE_ANUNCIOS, "a": ano, "sha1": sha1, "v": versao, "e": eid})
    con.commit()
    return {"execucao_id": eid, **cont}


def ler_registos(caminho: Path):
    with caminho.open("rb") as f:
        yield from ijson.items(f, "item", use_float=True)


def ingerir_recurso(con: psycopg.Connection, rec: Recurso, sincronizacao_id: int) -> dict[str, Any]:
    caminho, sha256, sha1 = descarregar(rec.url)
    try:
        if rec.sha1 and sha1 != rec.sha1:
            raise ValueError(f"sha1 descarregado ({sha1}) difere do publicado ({rec.sha1})")
        return carregar(con, ler_registos(caminho), ano=rec.ano, url=rec.url, sha256=sha256, sha1=rec.sha1,
                        versao=rec.versao, sincronizacao_id=sincronizacao_id)
    finally:
        caminho.unlink(missing_ok=True)
