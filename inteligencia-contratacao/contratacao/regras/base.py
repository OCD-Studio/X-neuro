"""Infraestrutura do motor de regras.

Cada indicador é uma regra testável e explicável. O resultado nunca é um juízo
de culpa: é um SINAL DE RISCO com pontos, explicação legível e evidência
(os valores concretos que o dispararam), para investigação humana.

Estados possíveis de uma avaliação:
- "sinal"               -> o padrão de risco verifica-se; soma pontos
- "sem_sinal"           -> avaliado, padrão não se verifica
- "nao_aplicavel"       -> o indicador não se aplica a este caso (não é guardado)
- "dados_insuficientes" -> faltam dados na fonte; NÃO soma pontos (nunca se assume o pior)

Dois tipos de indicador, com o mesmo formato de resultado:
- `Indicador`     — olha para UM contrato (Python puro, testes unitários);
- `IndicadorSQL`  — precisa de olhar para VÁRIOS contratos (histórico de uma relação,
                    quota de mercado…); é uma consulta SQL testada contra PostgreSQL.
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
    """Vista mínima de um contrato, independente da BD, usada pelas regras por contrato."""

    id: int | str
    procedimento: str
    n_concorrentes: int | None = None  # None = desconhecido na fonte
    preco_contratual: Decimal | None = None
    preco_base: Decimal | None = None
    preco_efetivo: Decimal | None = None
    data_celebracao: date | None = None
    data_publicacao: date | None = None
    tipo_contrato: str | None = None
    regime_tipo: str | None = None          # ver sql/004 classificar_regime
    fundamento_ad: str | None = None        # base legal do ajuste direto (ver sql/005); None = em falta
    adjudicante_nome: str | None = None


@dataclass(frozen=True)
class Resultado:
    indicador: str
    versao: str
    estado: Estado
    pontos: int
    explicacao: str
    evidencia: dict[str, Any] = field(default_factory=dict)


class _Meta:
    codigo: str
    nome: str
    versao: str
    pontos: int
    descricao: str   # o que mede e porque é um sinal de risco (texto para a UI)
    limites: str     # quando pode ser legítimo / falsos positivos conhecidos
    referencia: str  # fundamento metodológico ou legal
    parametros: dict[str, Any] = {}  # valores ajustáveis, mostrados na metodologia


class Indicador(_Meta, ABC):
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


class IndicadorSQL(_Meta, ABC):
    """Indicador de conjunto. `sql()` devolve uma consulta com as colunas
    (contrato_id, estado, explicacao, evidencia jsonb) — uma linha por contrato avaliado
    (sinal / sem_sinal / dados_insuficientes); contratos ausentes = não aplicável.
    Pode usar a tabela temporária `limiar` (regime_tipo, fundamento, valor) — ver limiares.py."""

    @abstractmethod
    def sql(self) -> str: ...
