-- Resultados compactos + agregados para a lista. As tabelas são reconstruídas pela pontuação
-- num schema auxiliar ('novo') e trocadas atomicamente (sem bloquear a app nem deixar linhas mortas).

CREATE SCHEMA IF NOT EXISTS novo;

-- Catálogo de indicadores (id curto usado nas tabelas grandes).
CREATE TABLE IF NOT EXISTS indicador (
    id          SMALLSERIAL PRIMARY KEY,
    codigo      TEXT NOT NULL UNIQUE,
    nome        TEXT NOT NULL,
    versao      TEXT NOT NULL,
    pontos      SMALLINT NOT NULL,
    ativo       BOOLEAN NOT NULL DEFAULT true
);

-- Estado da última pontuação (para a app e auditoria).
CREATE TABLE IF NOT EXISTS meta.pontuacao (
    id              BIGSERIAL PRIMARY KEY,
    iniciado_em     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    terminado_em    TIMESTAMPTZ,
    resumo          JSONB
);

DROP TABLE IF EXISTS avaliacao_risco;
DROP TABLE IF EXISTS referencia_risco;

-- Taxas de referência por indicador e grupo de comparação.
CREATE TABLE referencia_risco (
    id              INTEGER PRIMARY KEY,
    indicador_id    SMALLINT NOT NULL,
    nivel           TEXT NOT NULL,           -- 'ano+procedimento+cpv' | 'ano+procedimento' | 'procedimento'
    ano             SMALLINT,
    procedimento    TEXT NOT NULL,
    cpv_divisao     TEXT,
    n_avaliaveis    INTEGER NOT NULL,
    n_sinais        INTEGER NOT NULL,
    taxa            REAL NOT NULL
);

-- Um resultado por contrato x indicador avaliável. estado: 's' sinal, 'n' sem sinal, 'i' dados insuficientes.
-- Ausência de linha = não aplicável.
CREATE TABLE avaliacao_risco (
    contrato_id     BIGINT NOT NULL,
    indicador_id    SMALLINT NOT NULL,
    estado          "char" NOT NULL,
    pontos          SMALLINT NOT NULL,       -- pontos calibrados (0 se não é sinal)
    ref_id          INTEGER,                 -- referencia_risco usada (NULL se sem referência)
    PRIMARY KEY (contrato_id, indicador_id)
);

-- Texto e evidência só para sinais e dados insuficientes (o que a app mostra).
CREATE TABLE avaliacao_detalhe (
    contrato_id     BIGINT NOT NULL,
    indicador_id    SMALLINT NOT NULL,
    explicacao      TEXT,
    evidencia       JSONB,
    PRIMARY KEY (contrato_id, indicador_id)
);

-- Agregados por entidade e ano (data de referência), para a lista rápida.
-- papel: 'a' adjudicante, 'f' fornecedor.
CREATE TABLE agregado_entidade_ano (
    papel           "char" NOT NULL,
    entidade_id     BIGINT NOT NULL,
    ano             SMALLINT NOT NULL,
    n_contratos     INTEGER NOT NULL,
    total           NUMERIC(18,2) NOT NULL,
    n_ajuste_direto INTEGER NOT NULL,
    PRIMARY KEY (papel, entidade_id, ano)
);

CREATE TABLE agregado_risco_ano (
    papel           "char" NOT NULL,
    entidade_id     BIGINT NOT NULL,
    ano             SMALLINT NOT NULL,
    indicador_id    SMALLINT NOT NULL,
    o               INTEGER NOT NULL,        -- sinais observados
    e               REAL NOT NULL,           -- sinais esperados (soma das taxas de referência)
    n               INTEGER NOT NULL,        -- contratos avaliáveis
    pontos          INTEGER NOT NULL,
    PRIMARY KEY (papel, entidade_id, ano, indicador_id)
);
