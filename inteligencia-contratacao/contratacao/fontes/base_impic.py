"""Ingestão dos contratos do Portal BASE (IMPIC) publicados em dados.gov.pt.

Fluxo por ano: resolver URL (API dados.gov.pt) -> descarregar ZIP -> ler JSON
-> normalizar -> carregar em massa (COPY + upsert) -> registar qualidade e
cobertura. Um ficheiro com o mesmo SHA-256 de uma execução anterior concluída
é ignorado ("inalterado").
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

import httpx
import psycopg

from ..config import DADOSGOV_API, DATASET_CONTRATOS, DIR_DADOS, FONTE_CONTRATOS
from ..normalizacao import Ator, ContratoNormalizado, Rejeicao, normalizar_contrato

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- descarga


def resolver_url(ano: int, cliente: httpx.Client | None = None) -> str:
    """URL atual do ZIP de um ano (muda a cada republicação semanal)."""
    c = cliente or httpx.Client(timeout=60)
    r = c.get(f"{DADOSGOV_API}{DATASET_CONTRATOS}/")
    r.raise_for_status()
    alvo = f"contratos{ano}.zip"
    for res in r.json()["resources"]:
        if res["title"].lower() == alvo:
            return res["url"]
    raise LookupError(f"Recurso {alvo} não encontrado no dataset {DATASET_CONTRATOS}")


def descarregar(url: str, destino_dir: Path = DIR_DADOS) -> tuple[Path, str]:
    destino_dir.mkdir(parents=True, exist_ok=True)
    destino = destino_dir / url.rsplit("/", 1)[-1]
    h = hashlib.sha256()
    with httpx.stream("GET", url, timeout=300, follow_redirects=True) as r:
        r.raise_for_status()
        with destino.open("wb") as f:
            for bloco in r.iter_bytes(1 << 20):
                f.write(bloco)
                h.update(bloco)
    return destino, h.hexdigest()


def sha256_ficheiro(caminho: Path) -> str:
    h = hashlib.sha256()
    with caminho.open("rb") as f:
        for bloco in iter(lambda: f.read(1 << 20), b""):
            h.update(bloco)
    return h.hexdigest()


def ler_registos(caminho: Path) -> list[dict[str, Any]]:
    """Lê o JSON (dentro de ZIP ou solto). O ficheiro é uma lista de objetos."""
    if caminho.suffix.lower() == ".zip":
        with zipfile.ZipFile(caminho) as z:
            nomes = [n for n in z.namelist() if n.lower().endswith(".json")]
            if len(nomes) != 1:
                raise ValueError(f"Esperado 1 JSON no ZIP, encontrados: {nomes}")
            with z.open(nomes[0]) as f:
                dados = json.load(f)
    else:
        dados = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(dados, list):
        raise ValueError("Formato inesperado: a raiz do JSON não é uma lista")
    return dados


# --------------------------------------------------------------------- carga


def _linha_entidade(a: Ator, data) -> tuple:
    return (a.chave, a.nif, "nif" if a.nif else "nome", a.nome, a.nif_valido, data)


def carregar(
    con: psycopg.Connection,
    registos: list[dict[str, Any]],
    *,
    ano: int,
    url: str | None,
    sha256: str | None,
) -> dict[str, int]:
    """Normaliza e carrega registos. Devolve contadores da execução."""
    cur = con.cursor()
    execucao_id = cur.execute(
        "INSERT INTO meta.execucao_ingestao (fonte, ano, url, ficheiro_sha256) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (FONTE_CONTRATOS, ano, url, sha256),
    ).fetchone()["id"]

    problemas: list[tuple] = []
    contratos: dict[str, tuple[ContratoNormalizado, dict]] = {}
    rejeitados = 0
    for bruto in registos:
        r = normalizar_contrato(bruto)
        if isinstance(r, Rejeicao):
            rejeitados += 1
            problemas.append((r.id_origem, "rejeitado", r.motivo))
            continue
        if r.id_origem in contratos:
            # Observado em 2025: 348 ids repetidos, que diferem p.ex. só no critério
            # de adjudicação. Fica o último; o conflito fica registado.
            problemas.append((r.id_origem, "aviso", "idcontrato repetido no ficheiro; mantido o último"))
        contratos[r.id_origem] = (r, bruto)
        for a in r.avisos:
            problemas.append((r.id_origem, "aviso", a))

    # --- entidades (chave única por NIF ou nome marcado)
    entidades: dict[str, tuple] = {}
    nomes: set[tuple[str, str]] = set()
    for c, _ in contratos.values():
        for a in [c.adjudicante, *c.adjudicatarios, *(c.concorrentes or [])]:
            entidades[a.chave] = _linha_entidade(a, c.data_celebracao or c.data_publicacao)
            nomes.add((a.chave, a.nome))

    cur.execute(
        "CREATE TEMP TABLE t_ent (chave text, nif text, ident text, nome text, nif_valido bool, data date) ON COMMIT DROP"
    )
    with cur.copy("COPY t_ent FROM STDIN") as cp:
        for linha in entidades.values():
            cp.write_row(linha)
    cur.execute(
        """
        INSERT INTO entidade (chave, nif, identificado_por, nome, nif_valido, primeiro_visto, ultimo_visto)
        SELECT chave, nif, ident, nome, nif_valido, data, data FROM t_ent
        ON CONFLICT (chave) DO UPDATE SET
            nome = EXCLUDED.nome,
            primeiro_visto = LEAST(entidade.primeiro_visto, EXCLUDED.primeiro_visto),
            ultimo_visto = GREATEST(entidade.ultimo_visto, EXCLUDED.ultimo_visto),
            atualizado_em = now()
        """
    )
    cur.execute("CREATE TEMP TABLE t_nome (chave text, nome text) ON COMMIT DROP")
    with cur.copy("COPY t_nome FROM STDIN") as cp:
        for linha in nomes:
            cp.write_row(linha)
    cur.execute(
        """
        INSERT INTO entidade_nome (entidade_id, nome, fonte, visto_em)
        SELECT e.id, t.nome, %s, current_date FROM t_nome t JOIN entidade e ON e.chave = t.chave
        ON CONFLICT DO NOTHING
        """,
        (FONTE_CONTRATOS,),
    )

    # --- contratos
    cur.execute(
        """
        CREATE TEMP TABLE t_ctr (
            id_origem text, id_procedimento text, adjudicante_chave text, tipo_contrato text,
            procedimento text, procedimento_original text, objeto text, data_publicacao date,
            data_celebracao date, data_decisao_adjudicacao date, preco_contratual numeric,
            preco_base numeric, preco_efetivo numeric, cpv text, cpv_descricao text,
            distrito text, concelho text, n_concorrentes int, fundamento_ajuste_direto text,
            regime text, criterio_adjudicacao text, ano smallint, checksum text, dados_origem jsonb
        ) ON COMMIT DROP
        """
    )
    with cur.copy("COPY t_ctr FROM STDIN") as cp:
        for c, bruto in contratos.values():
            cp.write_row((
                c.id_origem, c.id_procedimento, c.adjudicante.chave, c.tipo_contrato,
                c.procedimento, c.procedimento_original, c.objeto, c.data_publicacao,
                c.data_celebracao, c.data_decisao_adjudicacao, c.preco_contratual,
                c.preco_base, c.preco_efetivo, c.cpv, c.cpv_descricao, c.distrito,
                c.concelho, len(c.concorrentes) if c.concorrentes is not None else None,
                c.fundamento_ajuste_direto, c.regime, c.criterio_adjudicacao, c.ano,
                c.checksum, json.dumps(bruto, ensure_ascii=False),
            ))
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
        FROM t_ctr t JOIN entidade e ON e.chave = t.adjudicante_chave
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
        RETURNING (xmax = 0) AS inserido
        """,
        {"fonte": FONTE_CONTRATOS},
    ).fetchall()
    inseridos = sum(1 for x in res if x["inserido"])
    atualizados = len(res) - inseridos

    # --- ligações contrato <-> adjudicatários / concorrentes (só dos contratos tocados)
    cur.execute("CREATE TEMP TABLE t_lig (id_origem text, chave text, papel text) ON COMMIT DROP")
    with cur.copy("COPY t_lig FROM STDIN") as cp:
        for c, _ in contratos.values():
            for a in {a.chave for a in c.adjudicatarios}:
                cp.write_row((c.id_origem, a, "adj"))
            for a in {a.chave for a in (c.concorrentes or [])}:
                cp.write_row((c.id_origem, a, "conc"))
    for tabela, papel in (("contrato_adjudicatario", "adj"), ("contrato_concorrente", "conc")):
        cur.execute(
            f"""
            DELETE FROM {tabela} WHERE contrato_id IN (
                SELECT k.id FROM contrato k JOIN t_ctr t ON t.id_origem = k.id_origem
                WHERE k.fonte = %s)
            """,
            (FONTE_CONTRATOS,),
        )
        cur.execute(
            f"""
            INSERT INTO {tabela} (contrato_id, entidade_id)
            SELECT k.id, e.id FROM t_lig l
            JOIN contrato k ON k.fonte = %s AND k.id_origem = l.id_origem
            JOIN entidade e ON e.chave = l.chave
            WHERE l.papel = %s
            ON CONFLICT DO NOTHING
            """,
            (FONTE_CONTRATOS, papel),
        )

    # --- qualidade, execução e cobertura
    with cur.copy(
        "COPY meta.problema_qualidade (execucao_id, fonte, id_origem, gravidade, descricao) FROM STDIN"
    ) as cp:
        for id_origem, gravidade, descricao in problemas:
            cp.write_row((execucao_id, FONTE_CONTRATOS, id_origem, gravidade, descricao))

    contadores = {
        "lidos": len(registos),
        "inseridos": inseridos,
        "atualizados": atualizados,
        "inalterados": len(contratos) - inseridos - atualizados,
        "rejeitados": rejeitados,
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
        INSERT INTO meta.cobertura (fonte, ano, estado, n_registos)
        VALUES (%s, %s, 'ingerido', (SELECT count(*) FROM contrato WHERE fonte=%s AND ano=%s))
        ON CONFLICT (fonte, ano) DO UPDATE SET estado='ingerido',
            n_registos=EXCLUDED.n_registos, atualizado_em=now()
        """,
        (FONTE_CONTRATOS, ano, FONTE_CONTRATOS, ano),
    )
    con.commit()
    return {"execucao_id": execucao_id, **contadores}


def ficheiro_ja_ingerido(con: psycopg.Connection, sha256: str) -> bool:
    return con.execute(
        "SELECT 1 FROM meta.execucao_ingestao WHERE fonte=%s AND ficheiro_sha256=%s AND estado='concluido'",
        (FONTE_CONTRATOS, sha256),
    ).fetchone() is not None


def ingerir_ano(con: psycopg.Connection, ano: int, ficheiro: Path | None = None) -> dict[str, Any]:
    """Ingestão completa de um ano. `ficheiro` permite usar um ZIP/JSON já descarregado."""
    if ficheiro is None:
        url = resolver_url(ano)
        ficheiro, sha = descarregar(url)
    else:
        url, sha = None, sha256_ficheiro(ficheiro)
    if ficheiro_ja_ingerido(con, sha):
        log.info("Ficheiro %s inalterado (sha256 já ingerido); nada a fazer", ficheiro.name)
        return {"estado": "inalterado", "ficheiro": str(ficheiro)}
    registos = ler_registos(ficheiro)
    return carregar(con, registos, ano=ano, url=url, sha256=sha)
