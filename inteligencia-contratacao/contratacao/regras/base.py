"""Infraestrutura do motor de regras.

Cada indicador é uma regra testável e explicável. O resultado nunca é um juízo
de culpa: é um SINAL DE RISCO com pontos, explicação legível e evidência
(os valores concretos que o dispararam), para investigação humana.

Estados possíveis de uma avaliação:
- "sinal"               -> o padrão de risco verifica-se; soma pontos
- "sem_sinal"           -> avaliado, padrão não se verifica
- "nao_aplicavel"       -> o indicador não se aplica a este caso
- "dados_insuficientes" -> faltam dados na fonte; NÃO soma pontos e vai para
                           o relatório de qualidade (nunca se assume o pior)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Literal

Estado = Literal["sinal", "sem_sinal", "nao_aplicavel", "dados_insuficientes"]


@dataclass(frozen=True)
class ContratoAvaliavel:
    """Vista mínima de um contrato, independente da BD, usada pelas regras."""

    id: int | str
    procedimento: str
    n_concorrentes: int | None  # None = desconhecido na fonte
    preco_contratual: Decimal | None = None
    preco_base: Decimal | None = None
    data_celebracao: date | None = None
    data_publicacao: date | None = None
    adjudicante_nif: str | None = None
    adjudicatarios_nif: tuple[str, ...] = ()


@dataclass(frozen=True)
class Resultado:
    indicador: str
    versao: str
    estado: Estado
    pontos: int
    explicacao: str
    evidencia: dict[str, Any] = field(default_factory=dict)


class Indicador(ABC):
    codigo: str
    nome: str
    versao: str
    pontos: int
    descricao: str  # o que mede e porque é um sinal de risco (texto para a UI)
    limites: str  # quando pode ser legítimo / falsos positivos conhecidos
    referencia: str  # fundamento metodológico

    @abstractmethod
    def avaliar(self, c: ContratoAvaliavel) -> Resultado: ...

    def _r(self, estado: Estado, explicacao: str, **evidencia: Any) -> Resultado:
        return Resultado(
            indicador=self.codigo,
            versao=self.versao,
            estado=estado,
            pontos=self.pontos if estado == "sinal" else 0,
            explicacao=explicacao,
            evidencia=evidencia,
        )
