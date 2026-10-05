"""Indicadores que precisam de olhar para vários contratos (consultas SQL).

Cada `sql()` devolve (contrato_id, estado, explicacao, evidencia). Usam a tabela temporária
`limiar` (regime_tipo, categoria, valor) preenchida a partir de limiares.py.
"""

from __future__ import annotations

from . import limiares
from .base import IndicadorSQL

# Ajustes diretos escolhidos em função do valor (base legal arts. 19.º/20.º, regime geral do CCP).
# 'categoria' = base legal (art. 19.º empreitadas / art. 20.º bens e serviços).
_BASE_AJUSTE_DIRETO = """
    SELECT k.id, k.adjudicante_id, k.preco_contratual AS preco, k.data_referencia AS data_ref,
           extract(year FROM k.data_referencia)::int AS ano_ref, left(k.cpv, 2) AS cpv2, left(k.cpv, 3) AS cpv3,
           k.fundamento_ad AS categoria, l.valor AS limiar
    FROM contrato k
    JOIN limiar l ON l.regime_tipo = k.regime_tipo AND l.fundamento = k.fundamento_ad
    WHERE k.procedimento = 'ajuste_direto'
      AND k.preco_contratual > 0 AND k.data_referencia IS NOT NULL AND k.cpv IS NOT NULL
"""


class AjusteDiretoRepetido(IndicadorSQL):
    codigo = "ajuste_direto_repetido"
    nome = "Ajustes diretos repetidos ao mesmo fornecedor acima do limite acumulado"
    versao = "1.0"
    pontos = 10
    descricao = ("A mesma entidade adjudicou ao mesmo fornecedor, por ajuste direto, contratos do mesmo tipo e setor "
                 "(divisão CPV) cujo valor acumulado no ano e nos dois anteriores atinge o limiar legal. A lei limita "
                 "convites repetidos ao mesmo operador precisamente para evitar este padrão.")
    limites = ("“Mesmo tipo de prestação” aproximado por tipo de contrato + divisão CPV (2 dígitos) — pode agrupar "
               "objetos diferentes. Limiares a validar juridicamente; só regime geral do CCP, sem critérios materiais. "
               "Considera apenas ajustes diretos (não consultas prévias).")
    referencia = "CCP art. 113.º, n.º 2 (limitação de convites); " + limiares.FONTE
    parametros = {"anos": 3, "agrupamento": "adjudicante + fornecedor + tipo + divisão CPV"}

    def sql(self) -> str:
        return f"""
        WITH b AS ({_BASE_AJUSTE_DIRETO}),
        bf AS (SELECT b.*, ca.entidade_id AS fornecedor_id FROM b JOIN contrato_adjudicatario ca ON ca.contrato_id = b.id),
        acum AS (
            SELECT c.id, c.fornecedor_id, c.limiar, c.ano_ref, sum(p.preco) AS acumulado, count(*) AS n
            FROM bf c JOIN bf p
              ON p.adjudicante_id = c.adjudicante_id AND p.fornecedor_id = c.fornecedor_id
             AND p.categoria = c.categoria AND p.cpv2 = c.cpv2
             AND p.ano_ref BETWEEN c.ano_ref - 2 AND c.ano_ref
             AND (p.data_ref, p.id) <= (c.data_ref, c.id)
            GROUP BY c.id, c.fornecedor_id, c.limiar, c.ano_ref
        ),
        pior AS (SELECT DISTINCT ON (id) * FROM acum ORDER BY id, (acumulado >= limiar AND n >= 2) DESC, acumulado DESC)
        SELECT id AS contrato_id,
               CASE WHEN acumulado >= limiar AND n >= 2 THEN 'sinal' ELSE 'sem_sinal' END AS estado,
               CASE WHEN acumulado >= limiar AND n >= 2 THEN
                 format('%s ajustes diretos ao mesmo fornecedor (mesmo tipo e divisão CPV) entre %s e %s somam %s €, '
                        '≥ limiar de %s €.', n, ano_ref - 2, ano_ref, eur(acumulado),
                        eur(limiar))
               END AS explicacao,
               jsonb_build_object('n_contratos', n, 'acumulado', acumulado, 'limiar', limiar,
                                  'fornecedor_id', fornecedor_id, 'anos', array[ano_ref - 2, ano_ref]) AS evidencia
        FROM pior
        """


class Fracionamento(IndicadorSQL):
    codigo = "fracionamento"
    nome = "Possível fracionamento de despesa"
    versao = "1.0"
    pontos = 10
    JANELA_DIAS = 30
    descricao = ("Vários ajustes diretos da mesma entidade ao MESMO fornecedor, com a mesma base legal e grupo CPV "
                 "(3 dígitos), celebrados num intervalo de ±30 dias, cada um abaixo do limiar legal mas que somados o "
                 "ultrapassam. É o padrão típico de divisão de uma compra para evitar um procedimento concorrencial.")
    limites = ("Fornecimentos recorrentes legítimos (consumíveis, manutenção) podem produzir o mesmo padrão. Grupo "
               "CPV de 3 dígitos é uma aproximação ao “mesmo objeto”. Limiares a validar juridicamente; só ajustes "
               "diretos fundamentados no valor, no regime geral do CCP.")
    referencia = "CCP art. 22.º (proibição de fracionamento da despesa); " + limiares.FONTE
    parametros = {"janela_dias": JANELA_DIAS, "agrupamento": "adjudicante + fornecedor + base legal + grupo CPV (3 dígitos)"}

    def sql(self) -> str:
        return f"""
        WITH b AS ({_BASE_AJUSTE_DIRETO}),
        bf AS (SELECT b.*, ca.entidade_id AS fornecedor_id FROM b JOIN contrato_adjudicatario ca ON ca.contrato_id = b.id),
        j0 AS (
            SELECT bf.*,
                   sum(preco) OVER w AS soma_janela,
                   count(*) OVER w AS n_janela
            FROM bf
            WINDOW w AS (PARTITION BY adjudicante_id, fornecedor_id, categoria, cpv3 ORDER BY data_ref
                         RANGE BETWEEN '{self.JANELA_DIAS} days' PRECEDING AND '{self.JANELA_DIAS} days' FOLLOWING)
        ),
        j AS (SELECT DISTINCT ON (id) * FROM j0 ORDER BY id, (n_janela >= 2 AND soma_janela >= limiar) DESC, soma_janela DESC)
        SELECT id AS contrato_id,
               CASE WHEN n_janela >= 2 AND soma_janela >= limiar THEN 'sinal' ELSE 'sem_sinal' END AS estado,
               CASE WHEN n_janela >= 2 AND soma_janela >= limiar THEN
                 format('%s ajustes diretos ao mesmo fornecedor, grupo CPV %s, em ±%s dias somam %s €, acima do limiar de %s € '
                        '(este: %s €).', n_janela, cpv3, {self.JANELA_DIAS}, eur(soma_janela),
                        eur(limiar), eur(preco))
               END AS explicacao,
               jsonb_build_object('n_janela', n_janela, 'soma_janela', soma_janela, 'limiar', limiar,
                                  'cpv3', cpv3, 'fornecedor_id', fornecedor_id) AS evidencia
        FROM j
        WHERE preco < limiar  -- contratos acima do limiar são tratados por 'ajuste_direto_acima_limiar'
        """


class Concentracao(IndicadorSQL):
    codigo = "concentracao"
    nome = "Fornecedor dominante na despesa da entidade"
    versao = "1.0"
    pontos = 10
    QUOTA = 0.5
    MIN_CONTRATOS = 5
    descricao = ("Num dado ano e setor (divisão CPV), o fornecedor deste contrato recebeu 50 % ou mais do valor "
                 "contratado pela entidade, que fez pelo menos 5 contratos nesse setor e ano. Relações exclusivas "
                 "persistentes são um indicador clássico de captura.")
    limites = ("Mercados com poucos operadores, contratos-quadro e serviços públicos essenciais (água, energia) "
               "concentram naturalmente. Contratos com vários adjudicatários contam por inteiro para cada um.")
    referencia = "Fazekas & Kocsis (2020): concentração do comprador (buyer spending concentration)."
    parametros = {"quota_minima": QUOTA, "min_contratos_entidade_setor_ano": MIN_CONTRATOS}

    def sql(self) -> str:
        return f"""
        WITH b AS (
            SELECT k.id, k.adjudicante_id, extract(year FROM k.data_referencia)::int AS ano, left(k.cpv, 2) AS cpv2,
                   k.preco_contratual AS preco, ca.entidade_id AS fornecedor_id
            FROM contrato k JOIN contrato_adjudicatario ca ON ca.contrato_id = k.id
            WHERE k.preco_contratual > 0 AND k.data_referencia IS NOT NULL AND k.cpv IS NOT NULL
        ),
        grupo AS (
            SELECT adjudicante_id, ano, cpv2, count(DISTINCT id) AS n, sum(preco) AS total
            FROM b GROUP BY 1, 2, 3 HAVING count(DISTINCT id) >= {self.MIN_CONTRATOS}
        ),
        forn AS (SELECT adjudicante_id, ano, cpv2, fornecedor_id, sum(preco) AS valor FROM b GROUP BY 1, 2, 3, 4),
        q AS (
            SELECT b.id, b.fornecedor_id, g.n, g.ano, g.cpv2, f.valor / g.total AS quota
            FROM b JOIN grupo g USING (adjudicante_id, ano, cpv2)
            JOIN forn f USING (adjudicante_id, ano, cpv2, fornecedor_id)
        ),
        pior AS (SELECT DISTINCT ON (id) * FROM q ORDER BY id, quota DESC)
        SELECT id AS contrato_id,
               CASE WHEN quota >= {self.QUOTA} THEN 'sinal' ELSE 'sem_sinal' END AS estado,
               CASE WHEN quota >= {self.QUOTA} THEN
                 format('O fornecedor recebeu %s%% do valor contratado pela entidade na divisão CPV %s em %s '
                        '(%s contratos da entidade nesse setor).', round(quota * 100), cpv2, ano, n)
               END AS explicacao,
               jsonb_build_object('quota', round(quota::numeric, 4), 'cpv2', cpv2, 'ano', ano, 'n_contratos', n,
                                  'fornecedor_id', fornecedor_id) AS evidencia
        FROM pior
        """


class FornecedorEstreante(IndicadorSQL):
    codigo = "fornecedor_estreante"
    nome = "Fornecedor sem histórico ganha contrato grande"
    versao = "1.0"
    pontos = 10
    VALOR = 100_000
    DESDE = "2014-01-01"
    descricao = ("Contrato de 100 000 € ou mais ganho por um fornecedor que nunca tinha tido um contrato público "
                 "registado no Portal BASE (desde 2012). Substitui provisoriamente o indicador “empresa recém-criada”, "
                 "que exige a data de constituição (registo comercial, ainda não disponível).")
    limites = ("“Sem histórico no BASE” não significa empresa nova: pode ser uma empresa antiga que só agora vende ao "
               "Estado, ou estrangeira. Só fornecedores com NIF; avalia contratos desde 2014 para haver pelo menos "
               "dois anos de histórico anterior.")
    referencia = "Fazekas & Kocsis (2020): empresas novas/sem historial como indicador de risco do fornecedor."
    parametros = {"valor_minimo_eur": VALOR, "avaliado_desde": DESDE}

    def sql(self) -> str:
        return f"""
        WITH primeiro AS (
            SELECT DISTINCT ON (ca.entidade_id) ca.entidade_id, k.id AS contrato_id, k.data_referencia
            FROM contrato_adjudicatario ca JOIN contrato k ON k.id = ca.contrato_id
            JOIN entidade e ON e.id = ca.entidade_id AND e.identificado_por = 'nif'
            WHERE k.data_referencia IS NOT NULL
            ORDER BY ca.entidade_id, k.data_referencia, k.id
        ),
        grandes AS (
            SELECT k.id, ca.entidade_id, k.preco_contratual
            FROM contrato k JOIN contrato_adjudicatario ca ON ca.contrato_id = k.id
            JOIN entidade e ON e.id = ca.entidade_id AND e.identificado_por = 'nif'
            WHERE k.preco_contratual >= {self.VALOR} AND k.data_referencia >= DATE '{self.DESDE}'
        ),
        r AS (
            SELECT g.id, g.entidade_id, g.preco_contratual, (p.contrato_id = g.id) AS estreia
            FROM grandes g JOIN primeiro p ON p.entidade_id = g.entidade_id
        ),
        pior AS (SELECT DISTINCT ON (id) * FROM r ORDER BY id, estreia DESC)
        SELECT id AS contrato_id,
               CASE WHEN estreia THEN 'sinal' ELSE 'sem_sinal' END AS estado,
               CASE WHEN estreia THEN
                 format('Primeiro contrato público registado do fornecedor desde 2012, no valor de %s €.',
                        eur(preco_contratual))
               END AS explicacao,
               jsonb_build_object('fornecedor_id', entidade_id, 'primeiro_contrato', estreia) AS evidencia
        FROM pior
        """


class PrazoCurto(IndicadorSQL):
    codigo = "prazo_curto"
    nome = "Prazo para apresentação de propostas anormalmente curto"
    versao = "1.0"
    pontos = 10
    PERCENTIL = 0.10
    MIN_GRUPO = 30
    descricao = ("O prazo dado aos concorrentes para apresentar propostas (do anúncio à data-limite, já contando "
                 "prorrogações publicadas) está entre os 10 % mais curtos de procedimentos comparáveis (mesmo tipo de "
                 "anúncio, tipo de contrato e ano). Prazos curtos favorecem quem já conhecia o procedimento.")
    limites = ("Critério estatístico, não o prazo mínimo legal. Prorrogações ligadas ao anúncio original pela entidade "
               "e descrição (≈ 93 % dos casos); as não ligadas fazem o prazo parecer mais curto. Contratos sem anúncio "
               "ligável (sem identificador ou anúncio não publicado nos dados) ficam “dados insuficientes”.")
    referencia = ("Fazekas & Kocsis (2020): “advertisement period” como indicador de restrição de concorrência; "
                  "dados: anúncios do Portal BASE (dados.gov.pt).")
    parametros = {"percentil": PERCENTIL, "grupo": "modelo de anúncio + tipo de contrato + ano (mín. 30)"}

    PROCEDIMENTOS = ("concurso_publico", "concurso_publico_urgente", "concurso_publico_simplificado",
                     "concurso_limitado", "concurso_limitado_simplificado")

    def sql(self) -> str:
        procs = ", ".join(f"'{p}'" for p in self.PROCEDIMENTOS)
        return f"""
        WITH orig AS (
            SELECT a.id_incm, a.modelo, a.nif_entidade, a.descricao_chave, a.data_publicacao, a.data_limite,
                   a.prazo_propostas,
                   CASE WHEN lower(a.tipo_contrato) LIKE '%%empreitada%%' THEN 'empreitadas' ELSE 'bens/serviços' END AS categoria
            FROM anuncio a
            WHERE a.tipo_acto IN ('Anúncio de procedimento', 'Anúncio de concurso urgente') AND a.data_publicacao IS NOT NULL
        ),
        efetivo AS (
            SELECT o.*,
                   (SELECT max(x.data_limite) FROM anuncio x
                     WHERE x.tipo_acto = 'Anúncio de Alteração' AND x.nif_entidade = o.nif_entidade
                       AND x.descricao_chave = o.descricao_chave AND x.data_publicacao >= o.data_publicacao
                       AND x.data_publicacao <= coalesce(o.data_limite, o.data_publicacao + 120)) AS limite_alterado
            FROM orig o
        ),
        prazo AS (
            SELECT id_incm, modelo, categoria, extract(year FROM data_publicacao)::int AS ano,
                   (limite_alterado IS NOT NULL AND limite_alterado > coalesce(data_limite, data_publicacao)) AS prorrogado,
                   -- greatest() ignora NULL; sem data-limite usa-se o prazo publicado
                   coalesce(greatest(limite_alterado, data_limite) - data_publicacao, prazo_propostas) AS dias
            FROM efetivo
        ),
        ref AS (
            SELECT modelo, categoria, ano, GROUPING(ano) AS g, count(*) AS n,
                   percentile_cont({self.PERCENTIL}) WITHIN GROUP (ORDER BY dias) AS p
            FROM prazo WHERE dias > 0
            GROUP BY GROUPING SETS ((modelo, categoria, ano), (modelo, categoria))
        ),
        avaliado AS (
            SELECT pz.*,
                   CASE WHEN r1.n >= {self.MIN_GRUPO} THEN r1.n ELSE r2.n END AS n_grupo,
                   CASE WHEN r1.n >= {self.MIN_GRUPO} THEN r1.p WHEN r2.n >= {self.MIN_GRUPO} THEN r2.p END AS p10
            FROM prazo pz
            LEFT JOIN ref r1 ON r1.g = 0 AND r1.modelo = pz.modelo AND r1.categoria = pz.categoria AND r1.ano = pz.ano
            LEFT JOIN ref r2 ON r2.g = 1 AND r2.modelo = pz.modelo AND r2.categoria = pz.categoria
        )
        SELECT k.id AS contrato_id,
               CASE WHEN v.id_incm IS NULL OR v.dias IS NULL OR v.dias <= 0 OR v.p10 IS NULL THEN 'dados_insuficientes'
                    WHEN v.dias < v.p10 THEN 'sinal' ELSE 'sem_sinal' END AS estado,
               CASE WHEN v.id_incm IS NULL THEN 'Sem anúncio de procedimento ligável nos dados.'
                    WHEN v.dias IS NULL OR v.dias <= 0 THEN 'Anúncio sem prazo de propostas utilizável.'
                    WHEN v.p10 IS NULL THEN 'Sem procedimentos comparáveis suficientes.'
                    WHEN v.dias < v.p10 THEN
                      format('Prazo de %s dias para propostas%s, abaixo do percentil 10 (%s dias) de %s comparáveis '
                             '(%s, %s, %s).', v.dias, CASE WHEN v.prorrogado THEN ' (com prorrogação)' ELSE '' END,
                             round(v.p10::numeric, 1), v.n_grupo, v.modelo, v.categoria, v.ano)
               END AS explicacao,
               jsonb_build_object('dias', v.dias, 'p10', round(v.p10::numeric, 1), 'prorrogado', v.prorrogado,
                                  'id_incm', k.id_incm) AS evidencia
        FROM contrato k
        LEFT JOIN avaliado v ON v.id_incm = k.id_incm
        WHERE k.procedimento IN ({procs})
        """
