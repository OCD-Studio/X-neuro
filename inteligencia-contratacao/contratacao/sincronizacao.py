"""Rotina diária: verificação de alterações na fonte + backfill histórico progressivo.

Cada execução:
1. Lista os ficheiros anuais publicados (API dados.gov.pt), com sha1 e data de versão.
2. Planeia (função pura `planear`, testada):
   - ATUALIZAR: anos já ingeridos cujo sha1 na fonte mudou (a fonte republica
     semanalmente; anos antigos também podem ser corrigidos). Sem limite.
   - BACKFILL: anos ainda não ingeridos (ou que falharam), do mais recente para o
     mais antigo, no máximo `max_backfill` por execução — o histórico completo
     vai entrando em segundo plano sem bloquear a atualização diária.
   - Anos ingeridos que deixaram de ser publicados ficam assinalados, nunca apagados.
3. Executa o plano, um ano de cada vez (falha de um ano não trava os outros).
4. Recalcula a pontuação se algo mudou.

Um lock (pg_advisory_lock) impede duas execuções em simultâneo.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import psycopg

from . import grafo, pontuacao
from .config import FONTE_CONTRATOS
from .fontes import base_anuncios, base_impic
from .fontes.base_impic import Recurso

log = logging.getLogger(__name__)

LOCK_ID = 7_202_610  # identificador arbitrário do advisory lock
MAX_BACKFILL_POR_EXECUCAO = 3


@dataclass(frozen=True)
class EstadoAno:
    estado: str                 # 'pendente' | 'ingerido' | 'falhou' | 'indisponivel'
    checksum_fonte: str | None


@dataclass
class Plano:
    atualizar: list[int] = field(default_factory=list)
    backfill: list[int] = field(default_factory=list)
    adiados: list[int] = field(default_factory=list)          # backfill para próximas execuções
    sem_alteracao: list[int] = field(default_factory=list)
    deixaram_de_ser_publicados: list[int] = field(default_factory=list)

    @property
    def anos(self) -> list[int]:
        return self.atualizar + self.backfill


def planear(recursos: dict[int, Recurso], cobertura: dict[int, EstadoAno],
            max_backfill: int = MAX_BACKFILL_POR_EXECUCAO) -> Plano:
    p = Plano()
    for ano in sorted(recursos, reverse=True):
        rec, est = recursos[ano], cobertura.get(ano)
        if est is not None and est.estado == "ingerido":
            # sem sha1 publicado não dá para saber se mudou: re-verifica pelo sha256 após descarga
            if rec.sha1 is None or rec.sha1 != est.checksum_fonte:
                p.atualizar.append(ano)
            else:
                p.sem_alteracao.append(ano)
        elif len(p.backfill) < max_backfill:
            p.backfill.append(ano)
        else:
            p.adiados.append(ano)
    p.deixaram_de_ser_publicados = sorted(
        a for a, e in cobertura.items() if a not in recursos and e.estado == "ingerido")
    return p


@dataclass(frozen=True)
class Fonte:
    nome: str
    listar: Callable[[], dict[int, Recurso]]
    ingerir: Callable[[psycopg.Connection, Recurso, int], dict[str, Any]]


def ler_cobertura(con: psycopg.Connection, fonte: str = FONTE_CONTRATOS) -> dict[int, EstadoAno]:
    return {
        l["ano"]: EstadoAno(l["estado"], l["checksum_fonte"])
        for l in con.execute("SELECT ano, estado, checksum_fonte FROM meta.cobertura WHERE fonte = %s", (fonte,))
    }


def registar_disponiveis(con: psycopg.Connection, recursos: dict[int, Recurso], fonte: str = FONTE_CONTRATOS) -> None:
    """Garante uma linha de cobertura 'pendente' para cada ano publicado."""
    for ano in recursos:
        con.execute(
            "INSERT INTO meta.cobertura (fonte, ano, estado) VALUES (%s, %s, 'pendente') ON CONFLICT DO NOTHING",
            (fonte, ano))
    con.commit()


def _ingerir_contratos(con: psycopg.Connection, rec: Recurso, sincronizacao_id: int) -> dict[str, Any]:
    caminho, sha256, sha1 = base_impic.descarregar(rec.url)
    try:
        if rec.sha1 and sha1 != rec.sha1:
            raise ValueError(f"sha1 descarregado ({sha1}) difere do publicado ({rec.sha1})")
        r = base_impic.ingerir_ficheiro(con, rec.ano, caminho, url=rec.url, sha256=sha256, sha1=rec.sha1,
                                        versao=rec.versao, sincronizacao_id=sincronizacao_id)
        if r.get("estado") == "inalterado":
            # mesmo conteúdo com novo sha1/URL: só atualiza o registo da versão
            con.execute(
                "UPDATE meta.cobertura SET checksum_fonte=%s, versao_fonte=%s, atualizado_em=now() "
                "WHERE fonte=%s AND ano=%s", (rec.sha1, rec.versao, FONTE_CONTRATOS, rec.ano))
            con.commit()
        return r
    finally:
        caminho.unlink(missing_ok=True)  # o ficheiro bruto não é necessário depois da carga


FONTES = [
    Fonte(FONTE_CONTRATOS, base_impic.listar_recursos, _ingerir_contratos),
    Fonte(base_anuncios.FONTE_ANUNCIOS, base_anuncios.listar_recursos, base_anuncios.ingerir_recurso),
]


def executar(
    con: psycopg.Connection,
    *,
    max_backfill: int = MAX_BACKFILL_POR_EXECUCAO,
    fontes: list[Fonte] | None = None,
    listar: Callable[[], dict[int, Recurso]] | None = None,
    ingerir: Callable[[psycopg.Connection, Recurso, int], dict[str, Any]] | None = None,
    pontuar: bool = True,
) -> dict[str, Any]:
    """Executa a rotina para cada fonte (por omissão: contratos e anúncios). `listar`/`ingerir`
    permitem substituir só a fonte de contratos (testes)."""
    if fontes is None:
        fontes = [Fonte(FONTE_CONTRATOS, listar, ingerir)] if listar and ingerir else FONTES
    if not con.execute("SELECT pg_try_advisory_lock(%s) AS ok", (LOCK_ID,)).fetchone()["ok"]:
        log.warning("Outra sincronização está a correr; a sair.")
        return {"estado": "ocupado"}
    sid = con.execute("INSERT INTO meta.sincronizacao DEFAULT VALUES RETURNING id").fetchone()["id"]
    con.commit()
    try:
        planos: dict[str, Plano] = {}
        resultados: dict[str, dict[int, Any]] = {}
        alterou = False
        for fonte in fontes:
            recursos = fonte.listar()
            registar_disponiveis(con, recursos, fonte.nome)
            plano = planear(recursos, ler_cobertura(con, fonte.nome), max_backfill)
            planos[fonte.nome] = plano
            con.execute("UPDATE meta.sincronizacao SET plano=%s WHERE id=%s",
                        (json.dumps({n: asdict(p) for n, p in planos.items()}), sid))
            con.commit()
            log.info("Plano %s: %s", fonte.nome, asdict(plano))
            res_f: dict[int, Any] = {}
            for ano in plano.anos:
                try:
                    r = fonte.ingerir(con, recursos[ano], sid)
                    res_f[ano] = r
                    alterou |= bool(r.get("inseridos") or r.get("atualizados"))
                except Exception as e:  # um ano falhado não trava os outros
                    con.rollback()
                    log.exception("Falhou a ingestão de %s %s", fonte.nome, ano)
                    res_f[ano] = {"erro": repr(e)}
                    con.execute(
                        "INSERT INTO meta.execucao_ingestao (fonte, ano, estado, terminado_em, erro, sincronizacao_id) "
                        "VALUES (%s,%s,'falhou',clock_timestamp(),%s,%s)", (fonte.nome, ano, repr(e), sid))
                    con.execute(
                        "UPDATE meta.cobertura SET estado='falhou', atualizado_em=now() "
                        "WHERE fonte=%s AND ano=%s AND estado <> 'ingerido'", (fonte.nome, ano))
                    con.commit()
            resultados[fonte.nome] = res_f

        pont = pontuacao.recalcular(con) if (pontuar and alterou) else None
        graf = grafo.reconstruir(con) if (pontuar and alterou) else None
        falhas = [f"{f}:{a}" if len(fontes) > 1 else a
                  for f, rs in resultados.items() for a, r in rs.items() if "erro" in r]
        n_total = sum(len(rs) for rs in resultados.values())
        estado = "concluido" if not falhas else ("parcial" if len(falhas) < n_total else "falhou")
        principal = planos[fontes[0].nome]
        resumo = {"resultados": resultados if len(fontes) > 1 else resultados[fontes[0].nome],
                  "pontuacao_recalculada": pont is not None, "grafo": graf, "falhas": falhas,
                  "adiados": principal.adiados if len(fontes) == 1 else {n: p.adiados for n, p in planos.items()},
                  "deixaram_de_ser_publicados": principal.deixaram_de_ser_publicados}
        con.execute("UPDATE meta.sincronizacao SET estado=%s, terminado_em=clock_timestamp(), resumo=%s WHERE id=%s",
                    (estado, json.dumps(resumo, default=str), sid))
        con.commit()
        plano_out = asdict(principal) if len(fontes) == 1 else {n: asdict(p) for n, p in planos.items()}
        return {"sincronizacao_id": sid, "estado": estado, "plano": plano_out, **resumo}
    except Exception as e:
        con.rollback()
        con.execute("UPDATE meta.sincronizacao SET estado='falhou', terminado_em=clock_timestamp(), erro=%s "
                    "WHERE id=%s", (repr(e), sid))
        con.commit()
        raise
    finally:
        con.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
        con.commit()
