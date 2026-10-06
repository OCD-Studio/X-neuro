"""Linha de comandos.

    python -m contratacao.cli migrar
    python -m contratacao.cli sincronizar [--max-backfill 3]   # rotina diária (deltas + backfill)
    python -m contratacao.cli ingerir --ano 2025 [--ficheiro caminho.zip]
    python -m contratacao.cli pontuar
    python -m contratacao.cli grafo                           # grafo temporal de relações
    python -m contratacao.cli verificar                       # requisitos do servidor
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from . import bd, grafo, pontuacao, sincronizacao
from .config import FONTE_CONTRATOS
from .fontes import base_impic


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="contratacao")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("migrar")
    ps = sub.add_parser("sincronizar")
    ps.add_argument("--max-backfill", type=int, default=sincronizacao.MAX_BACKFILL_POR_EXECUCAO)
    pi = sub.add_parser("ingerir")
    pi.add_argument("--ano", type=int, required=True)
    pi.add_argument("--ficheiro", type=Path)
    pi.add_argument("--forcar", action="store_true", help="recarregar mesmo que o ficheiro já tenha sido ingerido")
    sub.add_parser("pontuar")
    sub.add_parser("grafo", help="reconstrói o grafo temporal de relações")
    sub.add_parser("verificar", help="verifica os requisitos do servidor (PostgreSQL, permissões, rede, disco)")
    a = p.parse_args(argv)

    if a.cmd == "migrar":
        bd.migrar()
        print("Migrações aplicadas.")
    elif a.cmd == "sincronizar":
        bd.migrar()
        with bd.ligar() as con:
            r = sincronizacao.executar(con, max_backfill=a.max_backfill)
        print(json.dumps(r, indent=2, default=str))
    elif a.cmd == "ingerir":
        with bd.ligar() as con:
            try:
                r = base_impic.ingerir_ano(con, a.ano, a.ficheiro, forcar=a.forcar)
            except Exception as e:
                con.rollback()
                con.execute(
                    "INSERT INTO meta.execucao_ingestao (fonte, ano, estado, terminado_em, erro) "
                    "VALUES (%s,%s,'falhou',clock_timestamp(),%s)",
                    (FONTE_CONTRATOS, a.ano, repr(e)),
                )
                con.execute(
                    "INSERT INTO meta.cobertura (fonte, ano, estado) VALUES (%s,%s,'falhou') "
                    "ON CONFLICT (fonte, ano) DO UPDATE SET estado='falhou', atualizado_em=now() "
                    "WHERE meta.cobertura.estado <> 'ingerido'",
                    (FONTE_CONTRATOS, a.ano),
                )
                con.commit()
                raise
        print(json.dumps(r, indent=2, default=str))
    elif a.cmd == "verificar":
        from .verificacao import verificar
        raise SystemExit(0 if verificar() else 1)
    elif a.cmd == "grafo":
        with bd.ligar() as con:
            print(json.dumps(grafo.reconstruir(con), indent=2))
    elif a.cmd == "pontuar":
        with bd.ligar() as con:
            print(json.dumps(pontuacao.recalcular(con), indent=2))


if __name__ == "__main__":
    main()
