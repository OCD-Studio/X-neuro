-- Schema base. Idempotente (pode correr várias vezes).
-- Convenções:
--  * Uma única tabela "entidade" por NIF: o mesmo NIF pode ser adjudicante e
--    fornecedor; o papel deriva das ligações aos contratos.
--  * Bitemporalidade nas relações: [valido_de, valido_ate) = quando a relação
--    existiu no mundo; [registado_em, substituido_em) = quando o sistema a soube.
--  * natureza: 'documentada' (vem de uma fonte) vs 'inferida' (deduzida por
--    padrão). Nunca se misturam nas vistas.

CREATE SCHEMA IF NOT EXISTS meta;

-- ---------------------------------------------------------------- meta
CREATE TABLE IF NOT EXISTS meta.execucao_ingestao (
    id              BIGSERIAL PRIMARY KEY,
    fonte           TEXT NOT NULL,           -- ex.: 'base_contratos'
    ano             SMALLINT,
    url             TEXT,
    ficheiro_sha256 TEXT,
    iniciado_em     TIMESTAMPTZ NOT NULL DEFAULT now(),
    terminado_em    TIMESTAMPTZ,
    estado          TEXT NOT NULL DEFAULT 'a_correr', -- a_correr|concluido|falhou|inalterado
    lidos           INTEGER DEFAULT 0,
    inseridos       INTEGER DEFAULT 0,
    atualizados     INTEGER DEFAULT 0,
    inalterados     INTEGER DEFAULT 0,
    rejeitados      INTEGER DEFAULT 0,
    erro            TEXT
);

-- Relatório de qualidade de dados: tudo o que falta ou está mal na fonte.
CREATE TABLE IF NOT EXISTS meta.problema_qualidade (
    id              BIGSERIAL PRIMARY KEY,
    execucao_id     BIGINT REFERENCES meta.execucao_ingestao(id) ON DELETE CASCADE,
    fonte           TEXT NOT NULL,
    id_origem       TEXT,
    gravidade       TEXT NOT NULL,           -- 'rejeitado' | 'aviso'
    descricao       TEXT NOT NULL,
    registado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_problema_execucao ON meta.problema_qualidade(execucao_id);

-- Cobertura histórica: que anos de que fonte já estão ingeridos.
CREATE TABLE IF NOT EXISTS meta.cobertura (
    fonte           TEXT NOT NULL,
    ano             SMALLINT NOT NULL,
    estado          TEXT NOT NULL,           -- 'pendente'|'ingerido'|'falhou'|'indisponivel'
    n_registos      INTEGER,
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (fonte, ano)
);

-- ---------------------------------------------------------------- núcleo
CREATE TABLE IF NOT EXISTS entidade (
    id              BIGSERIAL PRIMARY KEY,
    chave           TEXT NOT NULL UNIQUE,    -- NIF, ou 'nome:<nome normalizado>' se a fonte omite o NIF
    nif             TEXT,                    -- NULL quando a fonte não publica NIF
    identificado_por TEXT NOT NULL CHECK (identificado_por IN ('nif','nome')),
    nome            TEXT NOT NULL,           -- nome mais recente visto
    nif_valido      BOOLEAN NOT NULL,
    primeiro_visto  DATE,
    ultimo_visto    DATE,
    criado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Variantes de nome por NIF (resolução de entidades; auditável).
CREATE INDEX IF NOT EXISTS ix_entidade_nif ON entidade(nif);

CREATE TABLE IF NOT EXISTS entidade_nome (
    entidade_id     BIGINT NOT NULL REFERENCES entidade(id) ON DELETE CASCADE,
    nome            TEXT NOT NULL,
    fonte           TEXT NOT NULL,
    visto_em        DATE,
    PRIMARY KEY (entidade_id, nome, fonte)
);

CREATE TABLE IF NOT EXISTS contrato (
    id                      BIGSERIAL PRIMARY KEY,
    fonte                   TEXT NOT NULL DEFAULT 'base_contratos',
    id_origem               TEXT NOT NULL,
    id_procedimento         TEXT,
    adjudicante_id          BIGINT NOT NULL REFERENCES entidade(id),
    tipo_contrato           TEXT,
    procedimento            TEXT NOT NULL,
    procedimento_original   TEXT,
    objeto                  TEXT,
    data_publicacao         DATE,
    data_celebracao         DATE,
    data_decisao_adjudicacao DATE,
    preco_contratual        NUMERIC(16,2),
    preco_base              NUMERIC(16,2),
    preco_efetivo           NUMERIC(16,2),
    cpv                     TEXT,
    cpv_descricao           TEXT,
    distrito                TEXT,
    concelho                TEXT,
    n_concorrentes          INTEGER,         -- NULL = desconhecido na fonte
    fundamento_ajuste_direto TEXT,
    regime                  TEXT,
    criterio_adjudicacao    TEXT,
    ano                     SMALLINT,
    checksum                TEXT NOT NULL,
    dados_origem            JSONB NOT NULL,  -- registo original, para auditoria
    url_fonte               TEXT,
    registado_em            TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fonte, id_origem)
);
CREATE INDEX IF NOT EXISTS ix_contrato_adjudicante ON contrato(adjudicante_id);
CREATE INDEX IF NOT EXISTS ix_contrato_data ON contrato(data_celebracao);
CREATE INDEX IF NOT EXISTS ix_contrato_procedimento ON contrato(procedimento);
CREATE INDEX IF NOT EXISTS ix_contrato_distrito ON contrato(distrito);
CREATE INDEX IF NOT EXISTS ix_contrato_cpv ON contrato(cpv);

CREATE TABLE IF NOT EXISTS contrato_adjudicatario (
    contrato_id     BIGINT NOT NULL REFERENCES contrato(id) ON DELETE CASCADE,
    entidade_id     BIGINT NOT NULL REFERENCES entidade(id),
    PRIMARY KEY (contrato_id, entidade_id)
);
CREATE INDEX IF NOT EXISTS ix_adjudicatario_entidade ON contrato_adjudicatario(entidade_id);

CREATE TABLE IF NOT EXISTS contrato_concorrente (
    contrato_id     BIGINT NOT NULL REFERENCES contrato(id) ON DELETE CASCADE,
    entidade_id     BIGINT NOT NULL REFERENCES entidade(id),
    PRIMARY KEY (contrato_id, entidade_id)
);

-- ---------------------------------------------------------------- pessoas e relações (Fases 4-5)
-- Só pessoas com cargo público ou societário relevante, com fonte. Ver README (RGPD).
CREATE TABLE IF NOT EXISTS pessoa (
    id                  BIGSERIAL PRIMARY KEY,
    nome                TEXT NOT NULL,
    chave_resolucao     TEXT,                -- nome normalizado + contexto; ver resolução
    confianca_resolucao NUMERIC(3,2),        -- 0..1
    removido_em         TIMESTAMPTZ,         -- pedido de remoção (RGPD): oculto em todas as vistas
    criado_em           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS relacao (
    id              BIGSERIAL PRIMARY KEY,
    origem_tipo     TEXT NOT NULL CHECK (origem_tipo IN ('entidade','pessoa')),
    origem_id       BIGINT NOT NULL,
    destino_tipo    TEXT NOT NULL CHECK (destino_tipo IN ('entidade','pessoa')),
    destino_id      BIGINT NOT NULL,
    tipo            TEXT NOT NULL,           -- ex.: 'adjudicou_a', 'administrador_de', 'cargo_publico_em'
    natureza        TEXT NOT NULL CHECK (natureza IN ('documentada','inferida')),
    confianca       NUMERIC(3,2),            -- obrigatório para inferidas
    valido_de       DATE,                    -- tempo de validade (mundo real)
    valido_ate      DATE,                    -- NULL = ainda em vigor / desconhecido
    registado_em    TIMESTAMPTZ NOT NULL DEFAULT now(),  -- tempo de transação
    substituido_em  TIMESTAMPTZ,             -- NULL = versão corrente do conhecimento
    fonte           TEXT NOT NULL,
    fonte_url       TEXT,
    fonte_data      DATE,
    detalhe         JSONB,
    CHECK (natureza = 'documentada' OR confianca IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_relacao_origem ON relacao(origem_tipo, origem_id) WHERE substituido_em IS NULL;
CREATE INDEX IF NOT EXISTS ix_relacao_destino ON relacao(destino_tipo, destino_id) WHERE substituido_em IS NULL;

-- ---------------------------------------------------------------- risco
CREATE TABLE IF NOT EXISTS avaliacao_risco (
    contrato_id     BIGINT NOT NULL REFERENCES contrato(id) ON DELETE CASCADE,
    indicador       TEXT NOT NULL,
    versao          TEXT NOT NULL,
    estado          TEXT NOT NULL,           -- sinal|sem_sinal|nao_aplicavel|dados_insuficientes
    pontos          INTEGER NOT NULL,
    explicacao      TEXT NOT NULL,
    evidencia       JSONB NOT NULL DEFAULT '{}',
    calculado_em    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (contrato_id, indicador)
);
CREATE INDEX IF NOT EXISTS ix_avaliacao_sinal ON avaliacao_risco(indicador) WHERE estado = 'sinal';
