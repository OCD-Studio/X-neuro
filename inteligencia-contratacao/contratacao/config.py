"""Configuração via variáveis de ambiente (ou ficheiro .env na raiz do projeto).

O .env é útil em alojamento partilhado: os Cron Jobs do cPanel não herdam as variáveis
definidas no ecrã "Setup Python App". Variáveis já definidas no ambiente têm prioridade.
"""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _carregar_env(caminho: Path) -> None:
    if not caminho.is_file():
        return
    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue
        chave, valor = linha.split("=", 1)
        os.environ.setdefault(chave.strip(), valor.strip().strip('"').strip("'"))


_carregar_env(RAIZ / ".env")
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@/contratacao?host=/tmp")
DIR_DADOS = Path(os.environ.get("DIR_DADOS", RAIZ / "dados" / "brutos"))

# dados.gov.pt — "Contratos Públicos - Portal Base - IMPIC - Contratos de 2012 a 2026"
# Os URLs dos ficheiros mudam a cada republicação (semanal); resolvem-se pela API.
DADOSGOV_API = "https://dados.gov.pt/api/1/datasets/"
DATASET_CONTRATOS = "contratos-publicos-portal-base-impic-contratos-de-2012-a-2026"
FONTE_CONTRATOS = "base_contratos"
