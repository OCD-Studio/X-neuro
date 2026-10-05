"""Acesso à base de dados (PostgreSQL via psycopg 3)."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from .config import DATABASE_URL, RAIZ


@contextmanager
def ligar(url: str | None = None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(url or DATABASE_URL, row_factory=dict_row) as con:
        yield con


def migrar(url: str | None = None) -> list[str]:
    """Aplica os ficheiros sql/*.sql ainda não aplicados, por ordem. Devolve os aplicados.

    Os ficheiros são idempotentes, mas só correm uma vez (registo em meta.migracao): assim um
    arranque da app não tenta alterar tabelas em uso por uma sincronização (ficaria bloqueado).
    Um advisory lock evita corridas quando vários processos arrancam ao mesmo tempo.
    """
    aplicados: list[str] = []
    with ligar(url) as con:
        con.execute("SELECT pg_advisory_xact_lock(7202611)")
        con.execute(
            "CREATE SCHEMA IF NOT EXISTS meta; "
            "CREATE TABLE IF NOT EXISTS meta.migracao (ficheiro TEXT PRIMARY KEY, "
            "aplicado_em TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        feitos = {l["ficheiro"] for l in con.execute("SELECT ficheiro FROM meta.migracao")}
        for f in sorted((RAIZ / "sql").glob("*.sql")):
            if f.name in feitos:
                continue
            con.execute(f.read_text(encoding="utf-8"))
            con.execute("INSERT INTO meta.migracao (ficheiro) VALUES (%s)", (f.name,))
            aplicados.append(f.name)
        con.commit()
    return aplicados
