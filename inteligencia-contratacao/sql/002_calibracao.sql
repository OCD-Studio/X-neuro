-- Calibração dos indicadores face a taxas de referência (ver contratacao/calibracao.py).
ALTER TABLE avaliacao_risco ADD COLUMN IF NOT EXISTS pontos_base INTEGER;
ALTER TABLE avaliacao_risco ADD COLUMN IF NOT EXISTS taxa_referencia NUMERIC(6,5);  -- p do grupo de comparação
ALTER TABLE avaliacao_risco ADD COLUMN IF NOT EXISTS referencia TEXT;               -- descrição legível do grupo

-- Taxas de referência por indicador e grupo (para auditoria e para a página de metodologia).
CREATE TABLE IF NOT EXISTS referencia_risco (
    indicador       TEXT NOT NULL,
    nivel           TEXT NOT NULL,           -- 'ano+procedimento+cpv' | 'ano+procedimento' | 'procedimento'
    ano             SMALLINT,
    procedimento    TEXT NOT NULL,
    cpv_divisao     TEXT,
    n_avaliaveis    INTEGER NOT NULL,
    n_sinais        INTEGER NOT NULL,
    taxa            NUMERIC(6,5) NOT NULL,
    calculado_em    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_referencia_indicador ON referencia_risco(indicador, procedimento);
