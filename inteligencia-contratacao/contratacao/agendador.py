"""Agendador (processo de longa duração). Corre a sincronização todos os dias.

    python -m contratacao.agendador

Hora configurável por SYNC_HORA / SYNC_MINUTO (fuso Europe/Lisbon). Alternativa
sem processo residente: cron do sistema a chamar `python -m contratacao.cli sincronizar`.
"""

from __future__ import annotations

import logging
import os

from apscheduler.schedulers.blocking import BlockingScheduler

from . import bd, sincronizacao

log = logging.getLogger(__name__)


def tarefa() -> None:
    max_bf = int(os.environ.get("SYNC_MAX_BACKFILL", sincronizacao.MAX_BACKFILL_POR_EXECUCAO))
    with bd.ligar() as con:
        r = sincronizacao.executar(con, max_backfill=max_bf)
    log.info("Sincronização terminada: %s", {k: r.get(k) for k in ("estado", "falhas", "adiados")})


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    bd.migrar()
    s = BlockingScheduler(timezone="Europe/Lisbon")
    s.add_job(tarefa, "cron", hour=int(os.environ.get("SYNC_HORA", 6)),
              minute=int(os.environ.get("SYNC_MINUTO", 17)), id="sincronizacao_diaria",
              misfire_grace_time=6 * 3600, coalesce=True, max_instances=1)
    if os.environ.get("SYNC_AO_ARRANCAR", "1") == "1":
        s.add_job(tarefa, id="sincronizacao_arranque")  # corre já uma vez (útil para o backfill inicial)
    log.info("Agendador ativo: %s", s.get_jobs())
    s.start()


if __name__ == "__main__":
    main()
