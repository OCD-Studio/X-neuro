"""Verificação dos requisitos de um servidor (útil em alojamento partilhado)."""

from __future__ import annotations

import shutil
import sys

import httpx

from . import bd
from .config import DADOSGOV_API, DATASET_CONTRATOS, DIR_DADOS

PG_MINIMO = 120000  # colunas geradas (GENERATED ... STORED) exigem PostgreSQL 12+
ESPACO_BD_ESTIMADO_GB = 9


def _linha(ok: bool | None, texto: str) -> None:
    print(("OK   " if ok else "AVISO" if ok is None else "FALHA"), texto)


def verificar() -> bool:
    tudo_ok = True
    _linha(sys.version_info >= (3, 10), f"Python {sys.version.split()[0]} (mínimo 3.10)")
    tudo_ok &= sys.version_info >= (3, 10)
    try:
        with bd.ligar() as con:
            v = int(con.execute("SHOW server_version_num").fetchone()["server_version_num"])
            ok = v >= PG_MINIMO
            _linha(ok, f"PostgreSQL {v // 10000}.{v % 10000 // 100 if v < 100000 else v % 10000} (mínimo 12)")
            tudo_ok &= ok
            pode = con.execute(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE') AS c").fetchone()["c"]
            _linha(pode, "permissão para criar schemas na base de dados")
            tudo_ok &= pode
            tam = con.execute("SELECT pg_size_pretty(pg_database_size(current_database())) AS t").fetchone()["t"]
            _linha(True, f"tamanho atual da base de dados: {tam} (com 2012–2026 completo: ~{ESPACO_BD_ESTIMADO_GB} GB)")
    except Exception as e:  # noqa: BLE001
        _linha(False, f"ligação à base de dados ({e.__class__.__name__}: {e})")
        tudo_ok = False
    try:
        r = httpx.get(f"{DADOSGOV_API}{DATASET_CONTRATOS}/", timeout=30)
        _linha(r.status_code == 200, f"acesso a dados.gov.pt (HTTP {r.status_code})")
        tudo_ok &= r.status_code == 200
    except Exception as e:  # noqa: BLE001
        _linha(False, f"acesso a dados.gov.pt ({e.__class__.__name__})")
        tudo_ok = False
    DIR_DADOS.mkdir(parents=True, exist_ok=True)
    livre = shutil.disk_usage(DIR_DADOS).free / 1e9
    _linha(livre > 0.3,
           f"espaço livre para descargas temporárias em {DIR_DADOS}: {livre:.1f} GB (precisa de ~0,2 GB)")
    print("\nTudo pronto." if tudo_ok else "\nHá requisitos em falta (ver FALHA acima).")
    return tudo_ok
