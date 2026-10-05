"""Limiares legais do ajuste direto escolhido em função do valor (Código dos Contratos Públicos).

ATENÇÃO — PARÂMETROS A VALIDAR JURIDICAMENTE. Valores sem IVA.

A base legal vem do campo 'fundamentacao' de cada contrato (classificado em sql/005):
- CCP versão DL 18/2008: art. 19.º a) empreitadas < 150 000 €; art. 20.º n.º 1 a) bens/serviços < 75 000 €.
- CCP revisto pelo DL 111-B/2017 (desde 01/01/2018): art. 19.º d) empreitadas < 30 000 €;
  art. 20.º n.º 1 d) bens/serviços < 20 000 €.

Só se aplica quando o regime do contrato (`regime_tipo`) é coerente com a base legal citada.
Ajustes diretos por critérios materiais (arts. 24.º, 26.º, 27.º…) não têm limite de valor → não aplicável.
"""

from __future__ import annotations

from decimal import Decimal

# (regime_tipo, fundamento_ad) -> limiar (o preço contratual tem de ser inferior)
LIMIAR_AJUSTE_DIRETO: dict[tuple[str, str], Decimal] = {
    ("ccp2008", "art19a"): Decimal("150000"),
    ("ccp2008", "art20a"): Decimal("75000"),
    ("ccp2017", "art19d"): Decimal("30000"),
    ("ccp2017", "art20d"): Decimal("20000"),
}

FONTE = ("CCP, arts. 19.º e 20.º (DL 18/2008 e redação do DL 111-B/2017) — "
         "valores a validar juridicamente antes de uso público")


def limiar(regime_tipo: str | None, fundamento_ad: str | None) -> Decimal | None:
    if regime_tipo is None or fundamento_ad is None:
        return None
    return LIMIAR_AJUSTE_DIRETO.get((regime_tipo, fundamento_ad))


SQL_TABELA_LIMIAR = """
CREATE TEMP TABLE IF NOT EXISTS limiar (regime_tipo text, fundamento text, valor numeric) ON COMMIT DROP;
TRUNCATE limiar;
"""
