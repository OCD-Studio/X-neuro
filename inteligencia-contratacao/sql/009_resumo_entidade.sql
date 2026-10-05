-- Resumo por entidade para todo o histórico (vista por omissão da lista), calculado na pontuação.
CREATE TABLE IF NOT EXISTS resumo_entidade (
    papel               "char" NOT NULL,
    entidade_id         BIGINT NOT NULL,
    n_contratos         BIGINT NOT NULL,
    total               NUMERIC NOT NULL,
    n_ajuste_direto     BIGINT NOT NULL,
    score               INTEGER NOT NULL,
    n_indicadores       INTEGER NOT NULL,
    n_sinais            BIGINT NOT NULL,
    esperados           NUMERIC NOT NULL,
    n_avaliaveis        BIGINT NOT NULL,
    pontos              BIGINT NOT NULL,
    PRIMARY KEY (papel, entidade_id)
);
