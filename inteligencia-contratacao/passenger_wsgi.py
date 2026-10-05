"""Ponto de entrada para alojamento cPanel ("Setup Python App" / Phusion Passenger).

O Passenger fala WSGI; a app é ASGI (FastAPI) — o a2wsgi faz a ponte.
Variáveis de ambiente (DATABASE_URL, …) definem-se no ecrã "Setup Python App".
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from a2wsgi import ASGIMiddleware  # noqa: E402

from contratacao.api import app as _app_asgi  # noqa: E402

application = ASGIMiddleware(_app_asgi)
