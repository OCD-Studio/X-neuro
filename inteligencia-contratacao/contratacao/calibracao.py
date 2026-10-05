"""Calibração dos indicadores face a uma TAXA DE REFERÊNCIA.

Problema que resolve (observado em 2025): um sinal não vale o mesmo em todo o
lado. Em consulta prévia, 47 % dos procedimentos avaliáveis têm um só
concorrente; em concurso público, 12,6 %. E somar sinais favorece quem tem mais
contratos, não quem tem um padrão mais anómalo.

Abordagem (inspirada no Corruption Risk Index, Fazekas et al.):

1. Taxa de referência p de um indicador = proporção de sinais entre os contratos
   AVALIÁVEIS (estado 'sinal' ou 'sem_sinal') do mesmo grupo de comparação.
   Grupos, do mais específico para o mais geral (usa-se o primeiro com
   amostra >= MIN_AMOSTRA_REF):
       (ano, procedimento, divisão CPV)  ->  (ano, procedimento)  ->  (procedimento)
2. Pontos de um contrato com sinal = round(pontos_base * (1 - p)).
   Um sinal raro no seu grupo pesa mais do que um sinal comum.
3. Score de uma ENTIDADE por indicador (0-100), independente da dimensão:
       O = sinais observados, N = contratos avaliáveis, E = soma de p (esperado)
       p_esp = E / N                       (taxa esperada para o seu "cabaz" de contratos)
       L     = limite inferior de Wilson (95 % unilateral) da taxa observada O / N
       score = 100 * (L - p_esp) / (1 - p_esp)     se L > p_esp, senão 0
   Leitura: "com 95 % de confiança, a taxa real desta entidade é pelo menos L,
   contra p_esp esperado". Usar L (e não O/N) penaliza amostras pequenas: 6 em 6
   pesa menos do que 37 em 37. Exige N >= MIN_CONTRATOS_ENTIDADE.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

MIN_AMOSTRA_REF = 30          # contratos avaliáveis mínimos para usar um grupo de referência
MIN_CONTRATOS_ENTIDADE = 5    # contratos avaliáveis mínimos para calcular score de entidade
Z_CONFIANCA = 1.645           # 95 % unilateral (limite inferior de Wilson)


@dataclass(frozen=True)
class Avaliavel:
    """Um contrato avaliável para um indicador."""

    ano: int | None
    procedimento: str
    cpv_divisao: str | None   # 2 primeiros dígitos do CPV
    sinal: bool


@dataclass(frozen=True)
class Referencia:
    taxa: float
    n: int
    nivel: str        # 'ano+procedimento+cpv' | 'ano+procedimento' | 'procedimento'
    descricao: str    # texto legível para a UI


def _chaves(a: Avaliavel) -> list[tuple[str, tuple]]:
    return [
        ("ano+procedimento+cpv", (a.ano, a.procedimento, a.cpv_divisao)),
        ("ano+procedimento", (a.ano, a.procedimento)),
        ("procedimento", (a.procedimento,)),
    ]


class TabelaReferencia:
    """Taxas de referência hierárquicas calculadas a partir dos próprios dados."""

    def __init__(self, avaliaveis: Iterable[Avaliavel]):
        self._cont: dict[tuple[str, tuple], list[int]] = defaultdict(lambda: [0, 0])
        for a in avaliaveis:
            for nivel, chave in _chaves(a):
                c = self._cont[(nivel, chave)]
                c[0] += 1
                c[1] += int(a.sinal)

    def referencia(self, a: Avaliavel) -> Referencia | None:
        for nivel, chave in _chaves(a):
            n, s = self._cont.get((nivel, chave), (0, 0))
            if n >= MIN_AMOSTRA_REF:
                return Referencia(taxa=s / n, n=n, nivel=nivel, descricao=_descrever(nivel, chave, n))
        return None  # sem amostra suficiente em nenhum nível

    def linhas(self) -> list[tuple[str, tuple, int, int]]:
        return [(nivel, chave, n, s) for (nivel, chave), (n, s) in self._cont.items()]


def _descrever(nivel: str, chave: tuple, n: int) -> str:
    if nivel == "ano+procedimento+cpv":
        return f"mesmo procedimento, divisão CPV {chave[2]} e ano {chave[0]} ({n} contratos)"
    if nivel == "ano+procedimento":
        return f"mesmo procedimento e ano {chave[0]} ({n} contratos)"
    return f"mesmo procedimento, todos os anos ({n} contratos)"


def pontos_calibrados(pontos_base: int, taxa: float | None) -> int:
    """Pontos de um contrato com sinal. Sem referência -> pontos base (sem calibração).

    Arredondamento "meio para cima" (igual ao SQL floor(x + 0.5)), não o arredondamento bancário do Python.
    """
    if taxa is None:
        return pontos_base
    return math.floor(pontos_base * (1 - taxa) + 0.5)


def wilson_inferior(sucessos: int, n: int, z: float = Z_CONFIANCA) -> float:
    """Limite inferior do intervalo de Wilson para uma proporção."""
    if n == 0:
        return 0.0
    ph = sucessos / n
    centro = ph + z * z / (2 * n)
    margem = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n))
    return (centro - margem) / (1 + z * z / n)


@dataclass(frozen=True)
class ScoreEntidade:
    score: int              # 0-100
    observados: int         # O
    esperados: float        # E
    avaliaveis: int         # N
    limite_inferior: float | None  # L
    motivo: str             # explicação legível


def score_entidade(observados: int, esperados: float, avaliaveis: int) -> ScoreEntidade:
    """Score 0-100 de uma entidade para um indicador. Ver docstring do módulo."""
    O, E, N = observados, esperados, avaliaveis
    if N < MIN_CONTRATOS_ENTIDADE:
        return ScoreEntidade(0, O, E, N, None,
                             f"amostra insuficiente ({N} < {MIN_CONTRATOS_ENTIDADE} contratos avaliáveis)")
    p_esp = E / N
    L = wilson_inferior(O, N)
    base = f"{O} de {N} contratos com sinal ({O / N:.0%}); esperado {E:.1f} ({p_esp:.0%})"
    if L <= p_esp:
        return ScoreEntidade(0, O, E, N, L, f"{base}; diferença não significativa ou abaixo do esperado")
    score = 100 if p_esp >= 1 else round(100 * (L - p_esp) / (1 - p_esp))
    return ScoreEntidade(min(100, score), O, E, N, L,
                         f"{base}; com 95 % de confiança a taxa real é ≥ {L:.0%}")
