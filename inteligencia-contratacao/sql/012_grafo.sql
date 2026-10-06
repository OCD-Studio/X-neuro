-- Grafo temporal de relações (bitemporal): ver contratacao/grafo.py.

-- Data "DD/MM/AAAA" -> date (IMMUTABLE, para colunas geradas; NULL se inválida).
CREATE OR REPLACE FUNCTION data_pt(t text) RETURNS date LANGUAGE plpgsql IMMUTABLE AS $$
BEGIN
  IF t IS NULL OR t !~ '^\d{2}/\d{2}/\d{4}$' THEN RETURN NULL; END IF;
  RETURN make_date(substr(t, 7, 4)::int, substr(t, 4, 2)::int, substr(t, 1, 2)::int);
EXCEPTION WHEN others THEN RETURN NULL;
END $$;

-- Fim (real ou estimado) da execução do contrato: data de fecho; senão celebração + prazo de execução (dias).
ALTER TABLE contrato ADD COLUMN IF NOT EXISTS data_fim_estimada DATE GENERATED ALWAYS AS (
  coalesce(
    data_pt(dados_origem->>'dataFechoContrato'),
    CASE WHEN (dados_origem->>'prazoExecucao') ~ '^\d{1,5}$' AND (dados_origem->>'prazoExecucao')::int > 0
         THEN data_celebracao + (dados_origem->>'prazoExecucao')::int END,
    data_celebracao)
) STORED;

-- Uma só versão "corrente" por relação (chave natural).
CREATE UNIQUE INDEX IF NOT EXISTS ux_relacao_corrente
  ON relacao (origem_tipo, origem_id, destino_tipo, destino_id, tipo, fonte) WHERE substituido_em IS NULL;

CREATE TABLE IF NOT EXISTS meta.grafo (
    id              BIGSERIAL PRIMARY KEY,
    iniciado_em     TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    terminado_em    TIMESTAMPTZ,
    resumo          JSONB
);
