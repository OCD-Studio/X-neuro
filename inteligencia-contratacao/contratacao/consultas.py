"""Fragmentos SQL partilhados entre a API e a pontuação (uma só definição da fórmula do score)."""

# Score por indicador (igual a calibracao.score_entidade — testado) e média dos indicadores com amostra suficiente.
def sql_score(chave: str = "eid") -> str:
    """CTEs wl/sc/f sobre uma CTE `ind` (chave, indicador_id, o, e, n, pontos). `chave`: colunas de agrupamento."""
    return f"""
    wl AS (
        SELECT *, e / n AS p_esp,
               ((o::float8 / n) + %(z)s ^ 2 / (2 * n)
                 - %(z)s * sqrt((o::float8 / n) * (1 - o::float8 / n) / n + %(z)s ^ 2 / (4 * n * n)))
               / (1 + %(z)s ^ 2 / n) AS l
        FROM ind WHERE n > 0
    ),
    sc AS (
        SELECT {chave}, indicador_id, o, e, n, pontos,
               CASE WHEN n < %(nmin)s OR l <= p_esp THEN 0
                    WHEN p_esp >= 1 THEN 100
                    ELSE least(100, round(100 * (l - p_esp) / (1 - p_esp))) END AS score
        FROM wl
    ),
    f AS (
        SELECT {chave}, coalesce(round(avg(score) FILTER (WHERE n >= %(nmin)s)), 0) AS score,
               count(*) FILTER (WHERE n >= %(nmin)s) AS n_indicadores,
               sum(o) AS n_sinais, round(sum(e)::numeric, 1) AS esperados, sum(n) AS n_avaliaveis,
               sum(pontos) AS pontos
        FROM sc GROUP BY {chave}
    )
"""
