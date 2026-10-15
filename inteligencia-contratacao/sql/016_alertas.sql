-- Fase 6: resumo diário de alertas, sempre em RASCUNHO. Nada é enviado automaticamente:
-- um humano revê, e só depois decide se e como partilha (fora do sistema).
CREATE TABLE IF NOT EXISTS alerta_resumo (
    id              BIGSERIAL PRIMARY KEY,
    gerado_em       TIMESTAMPTZ NOT NULL DEFAULT now(),
    desde           TIMESTAMPTZ NOT NULL,          -- janela coberta: ]desde, ate]
    ate             TIMESTAMPTZ NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'rascunho' CHECK (estado IN ('rascunho', 'revisto', 'arquivado')),
    revisto_em      TIMESTAMPTZ,
    nota_revisao    TEXT,
    conteudo        JSONB NOT NULL,
    texto           TEXT NOT NULL                  -- versão em Markdown, pronta a copiar depois de revista
);
CREATE INDEX IF NOT EXISTS ix_alerta_resumo_ate ON alerta_resumo (ate DESC);

-- Fotografia dos scores no último resumo (para detetar mudanças de nível).
CREATE TABLE IF NOT EXISTS meta.alerta_score (
    papel           "char" NOT NULL,
    entidade_id     BIGINT NOT NULL,
    score           INTEGER NOT NULL,
    PRIMARY KEY (papel, entidade_id)
);
