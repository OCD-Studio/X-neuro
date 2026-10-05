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


def migrar(url: str | None = None) -> None:
    """Aplica os ficheiros sql/*.sql por ordem (são idempotentes)."""
    with ligar(url) as con:
        for f in sorted((RAIZ / "sql").glob("*.sql")):
            con.execute(f.read_text(encoding="utf-8"))
        con.commit()
