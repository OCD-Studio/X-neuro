"""Ingestão dos contratos do Portal BASE (IMPIC) publicados em dados.gov.pt.

Fluxo por ano: listar recursos (API dados.gov.pt, com sha1 e data de versão)
-> descarregar ZIP -> ler JSON em STREAMING -> normalizar -> COPY para tabelas
temporárias -> deduplicar e fazer upsert em SQL -> registar qualidade e cobertura.

Memória constante: nenhum ano é carregado inteiro em memória (o JSON de 2025 tem
~440 MB), para correr num servidor modesto.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Iterator

import httpx
import ijson
import psycopg

from ..config import DADOSGOV_API, DATASET_CONTRATOS, DIR_DADOS, FONTE_CONTRATOS
from ..normalizacao import Rejeicao, normalizar_contrato

log = logging.getLogger(__name__)

MAX_EXEMPLOS = 5


# --------------------------------------------------------------------- recursos e descarga


@dataclass(frozen=True)
class Recurso:
    ano: int
    url: str
    sha1: str | None          # checksum publicado pela fonte
    versao: datetime | None   # last_modified do recurso
    tamanho: int | None


def listar_recursos(cliente: httpx.Client | None = None) -> dict[int, Recurso]:
    """Ficheiros ZIP anuais atualmente publicados (os URLs mudam a cada republicação)."""
    c = cliente or httpx.Client(timeout=60)
    r = c.get(f"{DADOSGOV_API}{DATASET_CONTRATOS}/")
    r.raise_for_status()
    return recursos_de_dataset(r.json())


def recursos_de_dataset(dataset: dict[str, Any]) -> dict[int, Recurso]:
    out: dict[int, Recurso] = {}
    for res in dataset.get("resources", []):
        m = re.fullmatch(r"contratos(\d{4})\.zip", res.get("title", "").strip().lower())
        if not m:
            continue
        ck = res.get("checksum") or {}
        lm = res.get("last_modified")
        out[int(m.group(1))] = Recurso(
            ano=int(m.group(1)),
            url=res["url"],
            sha1=ck.get("value") if ck.get("type") == "sha1" else None,
            versao=datetime.fromisoformat(lm) if lm else None,
            tamanho=res.get("filesize"),
        )
    return out


def descarregar(url: str, destino_dir: Path = DIR_DADOS) -> tuple[Path, str, str]:
    """Descarrega para ficheiro temporário e só depois renomeia. Devolve (caminho, sha256, sha1)."""
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / url.rsplit("/", 1)[-1]
    parcial = destino.with_suffix(destino.suffix + ".parcial")
    h256, h1 = hashlib.sha256(), hashlib.sha1()
    with httpx.stream("GET", url, timeout=600, follow_redirects=True) as r:
        r.raise_for_status()
        with parcial.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
                h256.update(bloco)
                h1.update(bloco)
    parcial.replace(destino)
    return destino, h256.hexdigest(), h1.hexdigest()


def sha256_ficheiro(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_registos(caminho: Path) -> Iterator[dict[str, Any]]:
    """Itera os registos do JSON (dentro de ZIP ou solto) sem o carregar inteiro."""
    if caminho.suffix.lower() == ".zip":
        with zipfile.ZipFile(caminho) as z:
            nomes = [n for n in z.namelist() if n.lower().endswith(".json")]
            if len(nomes) != 1:
                raise ValueError(f"Esperado 1 JSON no ZIP, encontrados: {nomes}")
            with z.open(nomes[0]) as f:
                yield from ijson.items(f, "item", use_float=True)
    else:
        with caminho.open("rb") as f:
            yield from ijson.items(f, "item", use_float=True)


# --------------------------------------------------------------------- carga


class _Qualidade:
    """Agrega avisos por tipo (com exemplos) e guarda rejeições individualmente."""

    def __init__(self) -> None:
        self.n: Counter[tuple[str, str]] = Counter()
        self.exemplos: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.detalhe: list[tuple[str | None, str, str]] = []

    @staticmethod
    def tipo(descricao: str) -> str:
        # "NIF ... inválido: 123" -> "NIF ... inválido"; "4 adjudicantes; ..." -> "vários adjudicantes; ..."
        t = descricao.split(":")[0]
        t = re.sub(r"\s*\(\d+x\)", "", t)  # "repetido (2x)" e "(4x)" são o mesmo tipo
        return re.sub(r"^\d+ adjudicantes", "vários adjudicantes", t)

    def add(self, id_origem: str | None, gravidade: str, descricao: str, detalhe: bool = False) -> None:
        chave = (gravidade, self.tipo(descricao))
        self.n[chave] += 1
        if id_origem and len(self.exemplos[chave]) < MAX_EXEMPLOS and id_origem not in self.exemplos[chave]:
            self.exemplos[chave].append(id_origem)
        if detalhe:
            self.detalhe.append((id_origem, gravidade, descricao))


def carregar(
    con: psycopg.Connection,
    registos: Iterable[dict[str, Any]],
    *,
    ano: int,
    url: str | None,
    sha256: str | None,
    sha1: str | None = None,
    versao: datetime | None = None,
    sincronizacao_id: int | None = None,
) -> dict[str, Any]:
    """Normaliza e carrega registos (iterável, consumido uma vez). Devolve contadores."""
    cur = con.cursor()
    execucao_id = cur.execute(
        "INSERT INTO meta.execucao_ingestao (fonte, ano, url, ficheiro_sha256, checksum_fonte, versao_fonte, "
        "sincronizacao_id) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        (FONTE_CONTRATOS, ano, url, sha256, sha1, versao, sincronizacao_id),
    ).fetchone()["id"]

    cur.execute(
        """
        CREATE TEMP TABLE t_ctr (
            seq int, id_origem text, id_procedimento text, adjudicante_chave text, tipo_contrato text,
            procedimento text, procedimento_original text, objeto text, data_publicacao date,
            data_celebracao date, data_decisao_adjudicacao date, preco_contratual numeric,
            preco_base numeric, preco_efetivo numeric, cpv text, cpv_descricao text,
            distrito text, concelho text, n_concorrentes int, fundamento_ajuste_direto text,
            regime text, criterio_adjudicacao text, ano smallint, checksum text, dados_origem jsonb
        ) ON COMMIT DROP;
        CREATE TEMP TABLE t_ent (chave text, nif text, ident text, nome text, nif_valido bool, data date) ON COMMIT DROP;
        CREATE TEMP TABLE t_lig (seq int, id_origem text, chave text, papel text) ON COMMIT DROP;
        CREATE TEMP TABLE t_nome (chave text, nome text) ON COMMIT DROP;
        CREATE TEMP TABLE t_ids (id_origem text) ON COMMIT DROP;
        """
    )

    q = _Qualidade()
    lidos = rejeitados = 0
    with cur.copy("COPY t_ctr FROM STDIN") as cp_ctr:
        # psycopg só permite um COPY ativo por ligação: entidades e ligações vão para listas
        # compactas (tuplos curtos) e são copiadas a seguir.
        ents: dict[str, tuple] = {}
        nomes: set[tuple[str, str]] = set()  # todas as variantes de nome por chave (resolução auditável)
        ids_brutos: set[str] = set()         # todos os ids presentes no ficheiro, mesmo rejeitados
        ligs: list[tuple] = []
        for seq, bruto in enumerate(registos):
            lidos += 1
            if bruto.get("idcontrato") not in (None, ""):
                ids_brutos.add(str(bruto["idcontrato"]).strip())
            r = normalizar_contrato(bruto)
            if isinstance(r, Rejeicao):
                rejeitados += 1
                q.add(r.id_origem, "rejeitado", r.motivo, detalhe=True)
                continue
            for a in r.avisos:
                q.add(r.id_origem, "aviso", a)
            data = r.data_celebracao or r.data_publicacao
            for a in (r.adjudicante, *r.adjudicatarios, *(r.concorrentes or ())):
                nomes.add((a.chave, a.nome))
                anterior = ents.get(a.chave)
                if anterior is None or (data and (anterior[5] is None or data >= anterior[5])):
                    ents[a.chave] = (a.chave, a.nif, "nif" if a.nif else "nome", a.nome, a.nif_valido, data)
            for ch in {a.chave for a in r.adjudicatarios}:
                ligs.append((seq, r.id_origem, ch, "adj"))
            for ch in {a.chave for a in (r.concorrentes or ())}:
                ligs.append((seq, r.id_origem, ch, "conc"))
            cp_ctr.write_row((
                seq, r.id_origem, r.id_procedimento, r.adjudicante.chave, r.tipo_contrato,
                r.procedimento, r.procedimento_original, r.objeto, r.data_publicacao,
                r.data_celebracao, r.data_decisao_adjudicacao, r.preco_contratual,
                r.preco_base, r.preco_efetivo, r.cpv, r.cpv_descricao, r.distrito,
                r.concelho, len(r.concorrentes) if r.concorrentes is not None else None,
                r.fundamento_ajuste_direto, r.regime, r.criterio_adjudicacao, r.ano,
                r.checksum, json.dumps(bruto, ensure_ascii=False, default=str),
            ))
    with cur.copy("COPY t_ent FROM STDIN") as cp:
        for linha in ents.values():
            cp.write_row(linha)
    with cur.copy("COPY t_lig FROM STDIN") as cp:
        for linha in ligs:
            cp.write_row(linha)
    with cur.copy("COPY t_nome FROM STDIN") as cp:
        for linha in nomes:
            cp.write_row(linha)
    with cur.copy("COPY t_ids FROM STDIN") as cp:
        for i in ids_brutos:
            cp.write_row((i,))
    del ents, ligs, nomes, ids_brutos

    # ids repetidos no ficheiro: fica a última ocorrência (observado: diferem em campos menores)
    for l in cur.execute(
        "SELECT id_origem, count(*) AS n FROM t_ctr GROUP BY 1 HAVING count(*) > 1"
    ).fetchall():
        q.add(l["id_origem"], "aviso", f"idcontrato repetido no ficheiro ({l['n']}x); mantida a última ocorrência",
              detalhe=True)
    cur.execute(
        """
        CREATE TEMP TABLE t_ctr_u ON COMMIT DROP AS
        SELECT DISTINCT ON (id_origem) * FROM t_ctr ORDER BY id_origem, seq DESC;
        DELETE FROM t_lig l USING t_ctr_u u WHERE l.id_origem = u.id_origem AND l.seq <> u.seq;
        """
    )

    # --- entidades
    cur.execute(
        """
        INSERT INTO entidade (chave, nif, identificado_por, nome, nif_valido, primeiro_visto, ultimo_visto)
        SELECT chave, nif, ident, nome, nif_valido, data, data FROM t_ent
        ON CONFLICT (chave) DO UPDATE SET
            nome = CASE WHEN EXCLUDED.ultimo_visto >= coalesce(entidade.ultimo_visto, EXCLUDED.ultimo_visto)
                        THEN EXCLUDED.nome ELSE entidade.nome END,
            primeiro_visto = LEAST(entidade.primeiro_visto, EXCLUDED.primeiro_visto),
            ultimo_visto = GREATEST(entidade.ultimo_visto, EXCLUDED.ultimo_visto),
            atualizado_em = now()
        """
    )
    cur.execute(
        """
        INSERT INTO entidade_nome (entidade_id, nome, fonte, visto_em)
        SELECT e.id, t.nome, %s, current_date FROM t_nome t JOIN entidade e ON e.chave = t.chave
        ON CONFLICT DO NOTHING
        """,
        (FONTE_CONTRATOS,),
    )

    # --- contratos
    res = cur.execute(
        """
        INSERT INTO contrato (
            fonte, id_origem, id_procedimento, adjudicante_id, tipo_contrato, procedimento,
            procedimento_original, objeto, data_publicacao, data_celebracao,
            data_decisao_adjudicacao, preco_contratual, preco_base, preco_efetivo, cpv,
            cpv_descricao, distrito, concelho, n_concorrentes, fundamento_ajuste_direto,
            regime, criterio_adjudicacao, ano, checksum, dados_origem, url_fonte)
        SELECT %(fonte)s, t.id_origem, t.id_procedimento, e.id, t.tipo_contrato, t.procedimento,
            t.procedimento_original, t.objeto, t.data_publicacao, t.data_celebracao,
            t.data_decisao_adjudicacao, t.preco_contratual, t.preco_base, t.preco_efetivo, t.cpv,
            t.cpv_descricao, t.distrito, t.concelho, t.n_concorrentes, t.fundamento_ajuste_direto,
            t.regime, t.criterio_adjudicacao, t.ano, t.checksum, t.dados_origem,
            'https://www.base.gov.pt/Base4/pt/detalhe/?type=contratos&id=' || t.id_origem
        FROM t_ctr_u t JOIN entidade e ON e.chave = t.adjudicante_chave
        ON CONFLICT (fonte, id_origem) DO UPDATE SET
            id_procedimento = EXCLUDED.id_procedimento, adjudicante_id = EXCLUDED.adjudicante_id,
            tipo_contrato = EXCLUDED.tipo_contrato, procedimento = EXCLUDED.procedimento,
            procedimento_original = EXCLUDED.procedimento_original, objeto = EXCLUDED.objeto,
            data_publicacao = EXCLUDED.data_publicacao, data_celebracao = EXCLUDED.data_celebracao,
            data_decisao_adjudicacao = EXCLUDED.data_decisao_adjudicacao,
            preco_contratual = EXCLUDED.preco_contratual, preco_base = EXCLUDED.preco_base,
            preco_efetivo = EXCLUDED.preco_efetivo, cpv = EXCLUDED.cpv,
            cpv_descricao = EXCLUDED.cpv_descricao, distrito = EXCLUDED.distrito,
            concelho = EXCLUDED.concelho, n_concorrentes = EXCLUDED.n_concorrentes,
            fundamento_ajuste_direto = EXCLUDED.fundamento_ajuste_direto,
            regime = EXCLUDED.regime, criterio_adjudicacao = EXCLUDED.criterio_adjudicacao,
            ano = EXCLUDED.ano, checksum = EXCLUDED.checksum, dados_origem = EXCLUDED.dados_origem,
            atualizado_em = now()
        WHERE contrato.checksum IS DISTINCT FROM EXCLUDED.checksum
        RETURNING id, (xmax = 0) AS inserido
        """,
        {"fonte": FONTE_CONTRATOS},
    ).fetchall()
    inseridos = sum(1 for x in res if x["inserido"])
    atualizados = len(res) - inseridos
    tocados = [x["id"] for x in res]

    # --- ligações: só para contratos inseridos/alterados
    if tocados:
        cur.execute("CREATE TEMP TABLE t_tocados (id bigint PRIMARY KEY) ON COMMIT DROP")
        with cur.copy("COPY t_tocados FROM STDIN") as cp:
            for i in tocados:
                cp.write_row((i,))
        for tabela, papel in (("contrato_adjudicatario", "adj"), ("contrato_concorrente", "conc")):
            cur.execute(f"DELETE FROM {tabela} WHERE contrato_id IN (SELECT id FROM t_tocados)")
            cur.execute(
                f"""
                INSERT INTO {tabela} (contrato_id, entidade_id)
                SELECT k.id, e.id FROM t_lig l
                JOIN contrato k ON k.fonte = %s AND k.id_origem = l.id_origem
                JOIN t_tocados tt ON tt.id = k.id
                JOIN entidade e ON e.chave = l.chave
                WHERE l.papel = %s
                ON CONFLICT DO NOTHING
                """,
                (FONTE_CONTRATOS, papel),
            )

    # --- contratos deste ano que deixaram de constar do ficheiro (ou que voltaram)
    removidos = cur.execute(
        """
        UPDATE contrato k SET removido_da_fonte_em = now()
        WHERE k.fonte = %s AND k.ano = %s AND k.removido_da_fonte_em IS NULL
          AND NOT EXISTS (SELECT 1 FROM t_ids t WHERE t.id_origem = k.id_origem)
        RETURNING k.id_origem
        """,
        (FONTE_CONTRATOS, ano),
    ).fetchall()
    for l in removidos:
        q.add(l["id_origem"], "aviso", "contrato deixou de constar do ficheiro publicado (marcado, não apagado)",
              detalhe=True)
    cur.execute(
        """
        UPDATE contrato k SET removido_da_fonte_em = NULL
        WHERE k.fonte = %s AND k.ano = %s AND k.removido_da_fonte_em IS NOT NULL
          AND EXISTS (SELECT 1 FROM t_ids t WHERE t.id_origem = k.id_origem)
        """,
        (FONTE_CONTRATOS, ano),
    )

    # --- qualidade
    with cur.copy("COPY meta.resumo_qualidade (execucao_id, gravidade, tipo, n, exemplos) FROM STDIN") as cp:
        for (grav, tipo), n in q.n.items():
            cp.write_row((execucao_id, grav, tipo, n, q.exemplos[(grav, tipo)]))
    with cur.copy(
        "COPY meta.problema_qualidade (execucao_id, fonte, id_origem, gravidade, descricao) FROM STDIN"
    ) as cp:
        for id_origem, gravidade, descricao in q.detalhe:
            cp.write_row((execucao_id, FONTE_CONTRATOS, id_origem, gravidade, descricao))

    n_unicos = cur.execute("SELECT count(*) AS n FROM t_ctr_u").fetchone()["n"]
    contadores = {
        "lidos": lidos,
        "inseridos": inseridos,
        "atualizados": atualizados,
        "inalterados": n_unicos - inseridos - atualizados,
        "rejeitados": rejeitados,
        "removidos_da_fonte": len(removidos),
    }
    cur.execute(
        """
        UPDATE meta.execucao_ingestao SET estado='concluido', terminado_em=clock_timestamp(),
            lidos=%(lidos)s, inseridos=%(inseridos)s, atualizados=%(atualizados)s,
            inalterados=%(inalterados)s, rejeitados=%(rejeitados)s
        WHERE id=%(id)s
        """,
        {**contadores, "id": execucao_id},
    )
    cur.execute(
        """
        INSERT INTO meta.cobertura (fonte, ano, estado, n_registos, checksum_fonte, versao_fonte, execucao_id)
        VALUES (%(f)s, %(a)s, 'ingerido', (SELECT count(*) FROM contrato WHERE fonte=%(f)s AND ano=%(a)s),
                %(sha1)s, %(versao)s, %(ex)s)
        ON CONFLICT (fonte, ano) DO UPDATE SET estado='ingerido', n_registos=EXCLUDED.n_registos,
            checksum_fonte=EXCLUDED.checksum_fonte, versao_fonte=EXCLUDED.versao_fonte,
            execucao_id=EXCLUDED.execucao_id, atualizado_em=now()
        """,
        {"f": FONTE_CONTRATOS, "a": ano, "sha1": sha1, "versao": versao, "ex": execucao_id},
    )
    con.commit()
    return {"execucao_id": execucao_id, **contadores}


def ficheiro_ja_ingerido(con: psycopg.Connection, sha256: str) -> bool:
    return con.execute(
        "SELECT 1 FROM meta.execucao_ingestao WHERE fonte=%s AND ficheiro_sha256=%s AND estado='concluido'",
        (FONTE_CONTRATOS, sha256),
    ).fetchone() is not None


def ingerir_ficheiro(
    con: psycopg.Connection,
    ano: int,
    ficheiro: Path,
    *,
    url: str | None = None,
    sha256: str | None = None,
    sha1: str | None = None,
    versao: datetime | None = None,
    sincronizacao_id: int | None = None,
    forcar: bool = False,
) -> dict[str, Any]:
    sha256 = sha256 or sha256_ficheiro(ficheiro)
    if not forcar and ficheiro_ja_ingerido(con, sha256):
        log.info("Ficheiro %s inalterado (sha256 já ingerido); nada a fazer", ficheiro.name)
        return {"estado": "inalterado", "ficheiro": str(ficheiro)}
    return carregar(con, ler_registos(ficheiro), ano=ano, url=url, sha256=sha256, sha1=sha1,
                    versao=versao, sincronizacao_id=sincronizacao_id)


def ingerir_ano(con: psycopg.Connection, ano: int, ficheiro: Path | None = None,
                forcar: bool = False) -> dict[str, Any]:
    """Ingestão completa de um ano. `ficheiro` permite usar um ZIP/JSON já descarregado;
    `forcar` recarrega mesmo que o ficheiro já tenha sido ingerido (p.ex. após mudar a normalização)."""
    if ficheiro is not None:
        return ingerir_ficheiro(con, ano, ficheiro, forcar=forcar)
    rec = listar_recursos().get(ano)
    if rec is None:
        raise LookupError(f"Ano {ano} não publicado no dataset {DATASET_CONTRATOS}")
    caminho, sha256, sha1 = descarregar(rec.url)
    if rec.sha1 and sha1 != rec.sha1:
        raise ValueError(f"sha1 descarregado ({sha1}) difere do publicado ({rec.sha1})")
    try:
        return ingerir_ficheiro(con, ano, caminho, url=rec.url, sha256=sha256, sha1=rec.sha1, versao=rec.versao,
                                forcar=forcar)
    finally:
        caminho.unlink(missing_ok=True)
