"""Indicador: concorrente único (single bidding).

Base: Corruption Risk Index (Fazekas & Kocsis, Government Transparency
Institute) — num procedimento aberto à concorrência, receber apenas uma
proposta é o indicador "elementar" de restrição de concorrência mais usado.
"""

from __future__ import annotations

from ..normalizacao import PROCEDIMENTOS
from .base import ContratoAvaliavel, Indicador, Resultado


class ConcorrenteUnico(Indicador):
    codigo = "concorrente_unico"
    nome = "Concorrente único em procedimento concorrencial"
    versao = "1.0"
    pontos = 10
    descricao = (
        "Procedimento concorrencial (concurso público, consulta prévia, concurso "
        "limitado, negociação, etc.) em que só um operador apresentou proposta. "
        "Pode indicar condições que afastaram concorrentes (prazos, especificações "
        "à medida, pouca divulgação)."
    )
    limites = (
        "Pode ser perfeitamente legal e comum em mercados com poucos operadores, "
        "objetos muito especializados ou valores baixos. Não se aplica a ajustes "
        "diretos (um só convidado por natureza). Se a fonte não lista concorrentes, "
        "o caso é 'dados insuficientes', nunca sinal."
    )
    referencia = "Fazekas, M. & Kocsis, G. (2020), Uncovering High-Level Corruption, Br. J. Polit. Sci."

    def avaliar(self, c: ContratoAvaliavel) -> Resultado:
        info = PROCEDIMENTOS.get(c.procedimento, PROCEDIMENTOS["outro"])
        if not info["competitivo"]:
            return self._r(
                "nao_aplicavel",
                f"Procedimento '{info['rotulo']}' não é concorrencial.",
                procedimento=c.procedimento,
            )
        if c.n_concorrentes is None:
            return self._r(
                "dados_insuficientes",
                "A fonte não indica os concorrentes deste procedimento.",
                procedimento=c.procedimento,
            )
        if c.n_concorrentes == 1:
            return self._r(
                "sinal",
                f"{info['rotulo']} com apenas 1 concorrente registado na fonte.",
                procedimento=c.procedimento,
                n_concorrentes=1,
            )
        return self._r(
            "sem_sinal",
            f"{info['rotulo']} com {c.n_concorrentes} concorrentes.",
            procedimento=c.procedimento,
            n_concorrentes=c.n_concorrentes,
        )
