"""Configuração via variáveis de ambiente."""

import os
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres@/contratacao?host=/tmp")
DIR_DADOS = Path(os.environ.get("DIR_DADOS", RAIZ / "dados" / "brutos"))

# dados.gov.pt — "Contratos Públicos - Portal Base - IMPIC - Contratos de 2012 a 2026"
# Os URLs dos ficheiros mudam a cada republicação (semanal); resolvem-se pela API.
DADOSGOV_API = "https://dados.gov.pt/api/1/datasets/"
DATASET_CONTRATOS = "contratos-publicos-portal-base-impic-contratos-de-2012-a-2026"
FONTE_CONTRATOS = "base_contratos"
