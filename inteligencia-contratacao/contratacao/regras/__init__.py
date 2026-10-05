"""Registo dos indicadores ativos. Fase 1: só concorrente único."""

from .base import ContratoAvaliavel, Indicador, Resultado
from .concorrente_unico import ConcorrenteUnico

INDICADORES: list[Indicador] = [ConcorrenteUnico()]


def avaliar(c: ContratoAvaliavel) -> list[Resultado]:
    return [ind.avaliar(c) for ind in INDICADORES]


def score(resultados: list[Resultado]) -> int:
    """Score transparente = soma simples dos pontos dos sinais."""
    return sum(r.pontos for r in resultados)


__all__ = ["INDICADORES", "ContratoAvaliavel", "Indicador", "Resultado", "avaliar", "score"]
