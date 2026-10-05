-- Fase 2: sincronização diária, backfill progressivo e qualidade agregada.

-- Data de referência de um contrato: celebração; se a fonte não a tem (ex.: ajustes
-- diretos simplificados do regime COVID, DL 10-A/2020), a data de publicação.
ALTER TABLE contrato ADD COLUMN IF NOT EXISTS data_referencia DATE
    GENERATED ALWAYS AS (coalesce(data_celebracao, data_publicacao)) STORED;
CREATE INDEX IF NOT EXISTS ix_contrato_data_ref ON contrato(data_referencia);
CREATE INDEX IF NOT EXISTS ix_contrato_ano ON contrato(ano);

-- Checksum publicado pela fonte (sha1 na API dados.gov.pt): permite saltar descargas.
ALTER TABLE meta.execucao_ingestao ADD COLUMN IF NOT EXISTS checksum_fonte TEXT;
ALTER TABLE meta.execucao_ingestao ADD COLUMN IF NOT EXISTS versao_fonte TIMESTAMPTZ;  -- last_modified do recurso
ALTER TABLE meta.execucao_ingestao ADD COLUMN IF NOT EXISTS sincronizacao_id BIGINT;
ALTER TABLE meta.cobertura ADD COLUMN IF NOT EXISTS checksum_fonte TEXT;
ALTER TABLE meta.cobertura ADD COLUMN IF NOT EXISTS versao_fonte TIMESTAMPTZ;
ALTER TABLE meta.cobertura ADD COLUMN IF NOT EXISTS execucao_id BIGINT;

-- Avisos de qualidade agregados por execução e tipo (os detalhes por registo ficam
-- só para rejeições e ids repetidos, em meta.problema_qualidade).
CREATE TABLE IF NOT EXISTS meta.resumo_qualidade (
    execucao_id     BIGINT NOT NULL REFERENCES meta.execucao_ingestao(id) ON DELETE CASCADE,
    gravidade       TEXT NOT NULL,           -- 'rejeitado' | 'aviso'
    tipo            TEXT NOT NULL,
    n               INTEGER NOT NULL,
    exemplos        TEXT[] NOT NULL DEFAULT '{}',   -- até 5 idcontrato de exemplo
    PRIMARY KEY (execucao_id, gravidade, tipo)
);

-- Uma execução da rotina diária (verificação + deltas + backfill + pontuação).
CREATE TABLE IF NOT EXISTS meta.sincronizacao (
    id              BIGSERIAL PRIMARY KEY,
    iniciado_em     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    terminado_em    TIMESTAMPTZ,
    estado          TEXT NOT NULL DEFAULT 'a_correr',   -- a_correr|concluido|parcial|falhou
    plano           JSONB,
    resumo          JSONB,
    erro            TEXT
);

-- Contratos que deixaram de constar do ficheiro publicado: marcados, nunca apagados.
ALTER TABLE contrato ADD COLUMN IF NOT EXISTS removido_da_fonte_em TIMESTAMPTZ;
