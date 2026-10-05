-- Anúncios de procedimento (Portal BASE / Diário da República), para o indicador de prazos de proposta.
CREATE TABLE IF NOT EXISTS anuncio (
    id_incm             TEXT PRIMARY KEY,        -- identificador do anúncio no Diário da República (IdIncm)
    n_anuncio           TEXT,
    ano                 SMALLINT,
    data_publicacao     DATE,
    nif_entidade        TEXT,
    descricao_chave     TEXT,                    -- descrição normalizada (liga alterações ao anúncio original)
    tipo_acto           TEXT,                    -- 'Anúncio de procedimento' | 'Anúncio de Alteração' | ...
    modelo              TEXT,                    -- 'Concurso público', ...
    tipo_contrato       TEXT,
    preco_base          NUMERIC(16,2),
    cpv                 TEXT,
    prazo_propostas     INTEGER,                 -- dias (como publicado)
    data_limite         DATE,                    -- data-limite de apresentação de propostas
    url                 TEXT,                    -- PDF no Diário da República
    checksum            TEXT NOT NULL,
    registado_em        TIMESTAMPTZ NOT NULL DEFAULT now(),
    atualizado_em       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_anuncio_ligacao ON anuncio(nif_entidade, descricao_chave);

-- Ligação contrato -> anúncio do procedimento.
ALTER TABLE contrato ADD COLUMN IF NOT EXISTS id_incm TEXT
    GENERATED ALWAYS AS (nullif(btrim(dados_origem->>'idINCM'), '')) STORED;
CREATE INDEX IF NOT EXISTS ix_contrato_id_incm ON contrato(id_incm) WHERE id_incm IS NOT NULL;
