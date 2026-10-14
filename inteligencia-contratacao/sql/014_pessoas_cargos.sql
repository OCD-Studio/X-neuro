-- Fase 5: titulares de cargos públicos (Entidade para a Transparência) e os seus cargos.
-- Minimização: guardam-se apenas nome, identificador do titular na fonte e a lista pública de
-- declarações (entidade, órgão, cargo, datas). Nunca NIF pessoal, data de nascimento, morada,
-- familiares, rendimentos ou património.

ALTER TABLE pessoa ADD COLUMN IF NOT EXISTS fonte TEXT;
ALTER TABLE pessoa ADD COLUMN IF NOT EXISTS id_externo TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_pessoa_fonte ON pessoa (fonte, id_externo);

-- Entidades tal como a Entidade da Transparência as nomeia (órgãos e pessoas coletivas).
CREATE TABLE IF NOT EXISTS entidade_et (
    id              INTEGER PRIMARY KEY,     -- id na fonte (publicquery/getallentities)
    nome            TEXT NOT NULL,
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_entidade_et_nome ON entidade_et (nome);

-- Declarações publicadas (só metadados públicos da lista de pesquisa).
CREATE TABLE IF NOT EXISTS et_declaracao (
    id              INTEGER PRIMARY KEY,
    titular_id      INTEGER NOT NULL,        -- holderId na fonte
    entidade        TEXT,
    orgao           TEXT,
    cargo           TEXT,
    natureza        TEXT,                    -- Initial | Modification | ReElection | Cessation | Final | ...
    estado          TEXT,                    -- Published | Modified | ...
    ano             SMALLINT,
    data_inicio     DATE,
    data_fim        DATE,
    data_entrega    TIMESTAMPTZ,
    recolhido_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_et_declaracao_titular ON et_declaracao (titular_id);

-- Progresso da recolha (retomável; atualização progressiva dos titulares mais antigos primeiro).
CREATE TABLE IF NOT EXISTS meta.et_titular (
    titular_id      INTEGER PRIMARY KEY,
    nome            TEXT NOT NULL,
    ultima_recolha  TIMESTAMPTZ,
    n_declaracoes   INTEGER,
    erro            TEXT
);

-- Cargos consolidados (um por pessoa x entidade x órgão x cargo), com período de validade.
CREATE TABLE IF NOT EXISTS cargo (
    id              BIGSERIAL PRIMARY KEY,
    pessoa_id       BIGINT NOT NULL REFERENCES pessoa(id) ON DELETE CASCADE,
    entidade_et_id  INTEGER REFERENCES entidade_et(id),
    entidade_nome   TEXT NOT NULL,           -- como publicado na fonte
    orgao           TEXT,
    cargo           TEXT,
    valido_de       DATE,
    valido_ate      DATE,                    -- NULL = em funções (sem declaração de cessação)
    declaracoes     INTEGER[] NOT NULL,      -- ids das declarações que o suportam
    fonte           TEXT NOT NULL DEFAULT 'et_cargos',
    atualizado_em   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (pessoa_id, entidade_nome, orgao, cargo)
);

-- As relações passam a poder ligar entidades da Entidade da Transparência.
ALTER TABLE relacao DROP CONSTRAINT IF EXISTS relacao_origem_tipo_check;
ALTER TABLE relacao DROP CONSTRAINT IF EXISTS relacao_destino_tipo_check;
ALTER TABLE relacao ADD CONSTRAINT relacao_origem_tipo_check CHECK (origem_tipo IN ('entidade', 'pessoa', 'entidade_et'));
ALTER TABLE relacao ADD CONSTRAINT relacao_destino_tipo_check CHECK (destino_tipo IN ('entidade', 'pessoa', 'entidade_et'));
CREATE INDEX IF NOT EXISTS ix_relacao_origem_tipo ON relacao (origem_tipo, origem_id, tipo) WHERE substituido_em IS NULL;

-- Resumo de cada consolidação (qualidade: cargos sem entidade identificada na fonte, etc.)
CREATE TABLE IF NOT EXISTS meta.et_consolidacao (
    id              BIGSERIAL PRIMARY KEY,
    executado_em    TIMESTAMPTZ NOT NULL DEFAULT now(),
    resumo          JSONB NOT NULL
);
