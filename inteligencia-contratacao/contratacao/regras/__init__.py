"""Registo dos indicadores ativos.

Indicadores pedidos mas BLOQUEADOS por falta de fonte (registo comercial):
- empresas recém-criadas a ganhar contratos grandes (precisa da data de constituição) —
  substituído provisoriamente por `fornecedor_estreante`;
- ligações societárias entre fornecedores (moradas/administradores comuns).
"""

from .base import ContratoAvaliavel, Indicador, IndicadorSQL, Resultado
from .concorrente_unico import ConcorrenteUnico
from .conjunto import AjusteDiretoRepetido, Concentracao, FornecedorEstreante, Fracionamento, PrazoCurto
from .por_contrato import (
    AjusteDiretoAcimaLimiar,
    DerrapagemExecucao,
    PrecoAcimaBase,
    PublicacaoTardia,
    TimingEleitoral,
    ValorLogoAbaixoLimiar,
)

INDICADORES: list[Indicador] = [
    ConcorrenteUnico(),
    PrecoAcimaBase(),
    DerrapagemExecucao(),
    AjusteDiretoAcimaLimiar(),
    ValorLogoAbaixoLimiar(),
    PublicacaoTardia(),
    TimingEleitoral(),
]
INDICADORES_SQL: list[IndicadorSQL] = [
    AjusteDiretoRepetido(),
    Fracionamento(),
    Concentracao(),
    FornecedorEstreante(),
    PrazoCurto(),
]
TODOS = [*INDICADORES, *INDICADORES_SQL]


def avaliar(c: ContratoAvaliavel) -> list[Resultado]:
    """Avalia os indicadores por contrato (os de conjunto correm em SQL, ver pontuacao.py)."""
    return [ind.avaliar(c) for ind in INDICADORES]


def score(resultados: list[Resultado]) -> int:
    """Soma simples dos pontos base dos sinais (antes da calibração)."""
    return sum(r.pontos for r in resultados)


__all__ = ["INDICADORES", "INDICADORES_SQL", "TODOS", "ContratoAvaliavel", "Indicador", "IndicadorSQL",
           "Resultado", "avaliar", "score"]
