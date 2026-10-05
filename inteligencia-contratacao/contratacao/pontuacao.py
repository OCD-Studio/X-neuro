"""Aplica todos os indicadores e grava o resultado explicável e calibrado.

- Memória limitada: contratos lidos em lotes por cursor do servidor; indicadores de
  conjunto e calibração em SQL (pico medido ~100 MB com 2,3 milhões de contratos).
- Armazenamento compacto: avaliacao_risco só com números; texto em avaliacao_detalhe
  apenas para sinais e dados insuficientes.
- Troca atómica: tudo é construído no schema 'novo' e trocado no fim numa transação
  curta — a app nunca vê resultados a meio nem fica bloqueada durante o cálculo.

Passos:
 1. indicadores por contrato (Python) -> av_nova (COPY por lotes)
 2. indicadores de conjunto (SQL)     -> av_nova
 3. taxas de referência por grupo (GROUPING SETS) -> novo.referencia_risco
 4. pontos calibrados = round_half_up(base × (1 − taxa)) -> novo.avaliacao_risco / novo.avaliacao_detalhe
 5. agregados por entidade e ano -> novo.agregado_entidade_ano / novo.agregado_risco_ano
 6. troca
"""

from __future__ import annotations

import json
import logging
from collections import Counter

import psycopg
from psycopg import sql as psql

from .calibracao import MIN_AMOSTRA_REF, MIN_CONTRATOS_ENTIDADE, Z_CONFIANCA
from .consultas import sql_score
from .regras import INDICADORES_SQL, TODOS, ContratoAvaliavel, avaliar
from .regras.limiares import LIMIAR_AJUSTE_DIRETO, SQL_TABELA_LIMIAR

log = logging.getLogger(__name__)
LOTE = 20_000
TABELAS = ("referencia_risco", "avaliacao_risco", "avaliacao_detalhe", "agregado_entidade_ano", "agregado_risco_ano",
           "resumo_entidade")

_SQL_CONTRATOS = """
    SELECT k.id, k.procedimento, k.n_concorrentes, k.preco_contratual, k.preco_base, k.preco_efetivo,
           k.data_celebracao, k.data_publicacao, k.tipo_contrato, k.regime_tipo, k.fundamento_ad,
           e.nome AS adjudicante_nome
    FROM contrato k JOIN entidade e ON e.id = k.adjudicante_id
"""


def sincronizar_catalogo(con: psycopg.Connection) -> dict[str, int]:
    """Garante uma linha em `indicador` por indicador registado; devolve codigo -> id."""
    con.execute("UPDATE indicador SET ativo = false")
    for i in TODOS:
        con.execute(
            "INSERT INTO indicador (codigo, nome, versao, pontos) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (codigo) DO UPDATE SET nome = EXCLUDED.nome, versao = EXCLUDED.versao, "
            "pontos = EXCLUDED.pontos, ativo = true",
            (i.codigo, i.nome, i.versao, i.pontos))
    return {l["codigo"]: l["id"] for l in con.execute("SELECT id, codigo FROM indicador WHERE ativo")}


def recalcular(con: psycopg.Connection) -> dict[str, int]:
    contagem: Counter[str] = Counter()
    cur = con.cursor()
    pid = cur.execute("INSERT INTO meta.pontuacao DEFAULT VALUES RETURNING id").fetchone()["id"]
    con.commit()
    ids = sincronizar_catalogo(con)

    cur.execute(
        "CREATE TEMP TABLE av_nova (contrato_id bigint, indicador_id smallint, estado text, "
        "pontos_base int, explicacao text, evidencia jsonb) ON COMMIT DROP"
    )

    # 1) indicadores por contrato, em lotes
    leitor = con.cursor(name="contratos_pontuacao")
    leitor.itersize = LOTE
    leitor.execute(_SQL_CONTRATOS)
    while True:
        lote = leitor.fetchmany(LOTE)
        if not lote:
            break
        with cur.copy("COPY av_nova FROM STDIN") as cp:
            for l in lote:
                for r in avaliar(ContratoAvaliavel(**l)):
                    contagem[f"{r.indicador}:{r.estado}"] += 1
                    if r.estado == "nao_aplicavel":
                        continue
                    detalhe = r.estado != "sem_sinal"
                    cp.write_row((l["id"], ids[r.indicador], r.estado, r.pontos,
                                  r.explicacao if detalhe else None,
                                  json.dumps(r.evidencia, ensure_ascii=False, default=str) if detalhe else None))
    leitor.close()

    # 2) indicadores de conjunto
    cur.execute(SQL_TABELA_LIMIAR)
    with cur.copy("COPY limiar FROM STDIN") as cp:
        for (regime, fund), valor in LIMIAR_AJUSTE_DIRETO.items():
            cp.write_row((regime, fund, valor))
    for ind in INDICADORES_SQL:
        # sem parâmetros: as consultas usam '%' do format() do PostgreSQL; valores entram como literais seguros
        consulta = psql.SQL(
            "INSERT INTO av_nova SELECT x.contrato_id, {iid}, x.estado, "
            "CASE WHEN x.estado = 'sinal' THEN {pts} ELSE 0 END, "
            "CASE WHEN x.estado <> 'sem_sinal' THEN x.explicacao END, "
            "CASE WHEN x.estado <> 'sem_sinal' THEN x.evidencia END "
            "FROM ({sub}) x"
        ).format(iid=psql.Literal(ids[ind.codigo]), pts=psql.Literal(ind.pontos), sub=psql.SQL(ind.sql()))
        cur.execute(consulta)
        por_estado = {l["estado"]: l["n"] for l in cur.execute(
            "SELECT estado, count(*) AS n FROM av_nova WHERE indicador_id = %s GROUP BY 1", (ids[ind.codigo],))}
        for e, n in por_estado.items():
            contagem[f"{ind.codigo}:{e}"] += n
        log.info("Indicador %s: %s", ind.codigo, por_estado)

    # tabelas novas (mesma definição que as atuais)
    for t in TABELAS:
        cur.execute(f"DROP TABLE IF EXISTS novo.{t}")
        cur.execute(f"CREATE TABLE novo.{t} (LIKE public.{t} INCLUDING DEFAULTS)")

    # 3) taxas de referência (mesma hierarquia que calibracao.TabelaReferencia)
    cur.execute(
        """
        CREATE TEMP TABLE ref ON COMMIT DROP AS
        SELECT (row_number() OVER ())::int AS id, *
        FROM (
            SELECT a.indicador_id, k.ano, k.procedimento, left(k.cpv, 2) AS cpv2,
                   GROUPING(k.ano, left(k.cpv, 2)) AS g,
                   count(*) AS n, count(*) FILTER (WHERE a.estado = 'sinal') AS s
            FROM av_nova a JOIN contrato k ON k.id = a.contrato_id
            WHERE a.estado IN ('sinal', 'sem_sinal')
            GROUP BY GROUPING SETS ((a.indicador_id, k.ano, k.procedimento, left(k.cpv, 2)),
                                    (a.indicador_id, k.ano, k.procedimento),
                                    (a.indicador_id, k.procedimento))
        ) x;
        CREATE INDEX ON ref (indicador_id, g, procedimento, ano);
        """
    )
    cur.execute(
        """
        INSERT INTO novo.referencia_risco (id, indicador_id, nivel, ano, procedimento, cpv_divisao,
                                           n_avaliaveis, n_sinais, taxa)
        SELECT id, indicador_id,
               CASE g WHEN 0 THEN 'ano+procedimento+cpv' WHEN 1 THEN 'ano+procedimento' ELSE 'procedimento' END,
               CASE WHEN g = 3 THEN NULL ELSE ano END, procedimento, CASE WHEN g = 0 THEN cpv2 END,
               n, s, s::real / n
        FROM ref
        """
    )

    # 4) resultados calibrados
    cur.execute(
        """
        CREATE TEMP TABLE av_final ON COMMIT DROP AS
        SELECT a.contrato_id, a.indicador_id, a.estado, a.pontos_base, a.explicacao, a.evidencia,
               CASE WHEN r1.n >= %(min)s THEN r1.id WHEN r2.n >= %(min)s THEN r2.id
                    WHEN r3.n >= %(min)s THEN r3.id END AS ref_id
        FROM av_nova a
        JOIN contrato k ON k.id = a.contrato_id
        LEFT JOIN ref r1 ON a.estado IN ('sinal', 'sem_sinal') AND r1.g = 0 AND r1.indicador_id = a.indicador_id
             AND r1.ano = k.ano AND r1.procedimento = k.procedimento AND r1.cpv2 IS NOT DISTINCT FROM left(k.cpv, 2)
        LEFT JOIN ref r2 ON a.estado IN ('sinal', 'sem_sinal') AND r2.g = 1 AND r2.indicador_id = a.indicador_id
             AND r2.ano = k.ano AND r2.procedimento = k.procedimento
        LEFT JOIN ref r3 ON a.estado IN ('sinal', 'sem_sinal') AND r3.g = 3 AND r3.indicador_id = a.indicador_id
             AND r3.procedimento = k.procedimento
        """,
        {"min": MIN_AMOSTRA_REF},
    )
    cur.execute(
        """
        INSERT INTO novo.avaliacao_risco (contrato_id, indicador_id, estado, pontos, ref_id)
        SELECT f.contrato_id, f.indicador_id,
               CASE f.estado WHEN 'sinal' THEN 's' WHEN 'sem_sinal' THEN 'n' ELSE 'i' END,
               CASE WHEN f.estado <> 'sinal' THEN 0
                    WHEN r.id IS NULL THEN f.pontos_base
                    ELSE floor(f.pontos_base * (1 - r.s::numeric / r.n) + 0.5) END,
               f.ref_id
        FROM av_final f LEFT JOIN ref r ON r.id = f.ref_id
        ORDER BY f.contrato_id;
        INSERT INTO novo.avaliacao_detalhe (contrato_id, indicador_id, explicacao, evidencia)
        SELECT contrato_id, indicador_id, explicacao, evidencia FROM av_final WHERE estado <> 'sem_sinal';
        """
    )

    # 5) agregados por entidade e ano (ano da data de referência)
    cur.execute(
        """
        CREATE TEMP TABLE papel_contrato ON COMMIT DROP AS
        SELECT 'a'::"char" AS papel, k.adjudicante_id AS entidade_id, k.id AS contrato_id FROM contrato k
        UNION ALL
        SELECT 'f'::"char", ca.entidade_id, ca.contrato_id FROM contrato_adjudicatario ca;

        INSERT INTO novo.agregado_entidade_ano (papel, entidade_id, ano, n_contratos, total, n_ajuste_direto)
        SELECT p.papel, p.entidade_id, extract(year FROM k.data_referencia)::int, count(*),
               coalesce(sum(k.preco_contratual), 0),
               count(*) FILTER (WHERE k.procedimento IN ('ajuste_direto', 'ajuste_direto_simplificado'))
        FROM papel_contrato p JOIN contrato k ON k.id = p.contrato_id
        WHERE k.data_referencia IS NOT NULL
        GROUP BY 1, 2, 3;

        INSERT INTO novo.agregado_risco_ano (papel, entidade_id, ano, indicador_id, o, e, n, pontos)
        SELECT p.papel, p.entidade_id, extract(year FROM k.data_referencia)::int, a.indicador_id,
               count(*) FILTER (WHERE a.estado = 's'), coalesce(sum(r.taxa), 0), count(*), sum(a.pontos)
        FROM papel_contrato p
        JOIN contrato k ON k.id = p.contrato_id
        JOIN novo.avaliacao_risco a ON a.contrato_id = p.contrato_id
        JOIN novo.referencia_risco r ON r.id = a.ref_id
        WHERE a.estado IN ('s', 'n') AND k.data_referencia IS NOT NULL
        GROUP BY 1, 2, 3, 4;
        """
    )

    # resumo por entidade para todo o histórico (vista por omissão da lista; mesma fórmula que a API)
    cur.execute(
        f"""
        INSERT INTO novo.resumo_entidade
        WITH ind AS (SELECT papel, entidade_id AS eid, indicador_id, sum(o) AS o, sum(e)::float8 AS e,
                            sum(n) AS n, sum(pontos) AS pontos
                     FROM novo.agregado_risco_ano GROUP BY 1, 2, 3),
             {sql_score("papel, eid")},
             ag AS (SELECT papel, entidade_id AS eid, sum(n_contratos) AS n_contratos, sum(total) AS total,
                           sum(n_ajuste_direto) AS n_ad FROM novo.agregado_entidade_ano GROUP BY 1, 2)
        SELECT ag.papel, ag.eid, ag.n_contratos, ag.total, ag.n_ad, coalesce(f.score, 0), coalesce(f.n_indicadores, 0),
               coalesce(f.n_sinais, 0), coalesce(f.esperados, 0), coalesce(f.n_avaliaveis, 0), coalesce(f.pontos, 0)
        FROM ag LEFT JOIN f ON f.papel = ag.papel AND f.eid = ag.eid
        """,
        {"z": Z_CONFIANCA, "nmin": MIN_CONTRATOS_ENTIDADE},
    )

    # índices (criados depois da carga: mais rápido)
    cur.execute(
        """
        ALTER TABLE novo.referencia_risco ADD PRIMARY KEY (id);
        ALTER TABLE novo.avaliacao_risco ADD PRIMARY KEY (contrato_id, indicador_id);
        ALTER TABLE novo.avaliacao_detalhe ADD PRIMARY KEY (contrato_id, indicador_id);
        ALTER TABLE novo.agregado_entidade_ano ADD PRIMARY KEY (papel, entidade_id, ano);
        ALTER TABLE novo.agregado_risco_ano ADD PRIMARY KEY (papel, entidade_id, ano, indicador_id);
        CREATE INDEX ON novo.agregado_entidade_ano (papel, ano);
        ALTER TABLE novo.resumo_entidade ADD PRIMARY KEY (papel, entidade_id);
        """
    )

    # 6) troca atómica
    for t in TABELAS:
        cur.execute(f"DROP TABLE public.{t}")
        cur.execute(f"ALTER TABLE novo.{t} SET SCHEMA public")
    cur.execute("UPDATE meta.pontuacao SET terminado_em = clock_timestamp(), resumo = %s WHERE id = %s",
                (json.dumps(dict(contagem)), pid))
    con.commit()
    for t in TABELAS:
        con.execute(f"ANALYZE {t}")
    con.commit()
    log.info("Pontuação recalculada: %s indicadores", len(TODOS))
    return dict(contagem)
