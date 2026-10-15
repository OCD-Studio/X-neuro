"""API dos resumos de alertas (Fase 6). Só leitura e revisão: o sistema NUNCA envia nada.

Mudar o estado (rascunho → revisto/arquivado) exige o cabeçalho X-Token igual a ADMIN_TOKEN (.env).
Sem ADMIN_TOKEN definido, a revisão pela app fica desativada.
"""

from __future__ import annotations

import hmac
import os
from typing import Literal

from fastapi import APIRouter, Header, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from . import bd

router = APIRouter()


@router.get("/api/alertas")
def listar_alertas(limite: int = 60):
    with bd.ligar() as con:
        return con.execute(
            "SELECT id, gerado_em, desde, ate, estado, revisto_em, (conteudo->>'n_contratos')::int AS n_contratos, "
            "(conteudo->'niveis'->>'n_entraram_alto')::int AS n_entraram_alto, "
            "(conteudo->'niveis'->>'linha_base')::boolean AS linha_base "
            "FROM alerta_resumo ORDER BY ate DESC LIMIT %s", (min(limite, 365),)).fetchall()


def _obter(con, rid: int) -> dict:
    r = con.execute("SELECT * FROM alerta_resumo WHERE id = %s", (rid,)).fetchone()
    if not r:
        raise HTTPException(404, "Resumo não encontrado.")
    return r


@router.get("/api/alertas/{rid}")
def obter_alerta(rid: int):
    with bd.ligar() as con:
        return _obter(con, rid)


@router.get("/api/alertas/{rid}/texto.md", response_class=PlainTextResponse)
def texto_alerta(rid: int):
    with bd.ligar() as con:
        r = _obter(con, rid)
    return PlainTextResponse(r["texto"], media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="alertas-{str(r["ate"])[:10]}.md"'})


class Revisao(BaseModel):
    estado: Literal["rascunho", "revisto", "arquivado"]
    nota: str | None = None


@router.post("/api/alertas/{rid}/estado")
def rever_alerta(rid: int, revisao: Revisao, x_token: str | None = Header(default=None)):
    token = os.environ.get("ADMIN_TOKEN")
    if not token:
        raise HTTPException(403, "Revisão pela app desativada (defina ADMIN_TOKEN no .env).")
    if not x_token or not hmac.compare_digest(x_token, token):
        raise HTTPException(403, "Token inválido.")
    with bd.ligar() as con:
        _obter(con, rid)
        con.execute("UPDATE alerta_resumo SET estado = %s, nota_revisao = %s, "
                    "revisto_em = CASE WHEN %s = 'rascunho' THEN NULL ELSE now() END WHERE id = %s",
                    (revisao.estado, revisao.nota, revisao.estado, rid))
        con.commit()
        return _obter(con, rid)
