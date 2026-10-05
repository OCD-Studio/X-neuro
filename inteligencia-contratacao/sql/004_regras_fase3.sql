-- Fase 3: campos derivados usados pelos indicadores e tabelas do motor de regras.

-- Família do regime jurídico (os limiares legais só se aplicam ao regime geral do CCP).
--  ccp2008 = CCP versão DL 18/2008; ccp2017 = CCP revisto pelo DL 111-B/2017 (inclui alterações da Lei 30/2021);
--  especial_ou_regional = regimes excecionais (COVID, medidas especiais, setoriais) ou adaptações regionais
--  (Madeira/Açores), com limiares próprios.
CREATE OR REPLACE FUNCTION classificar_regime(regime text, procedimento_original text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN regime IS NULL THEN 'desconhecido'
    WHEN regime ~* '(DLR|10-A/2020|Medidas Especiais|30/2018|60/2018)'
      OR coalesce(procedimento_original, '') ~* '(Lei n\.º 30/2021|artigo 7)' THEN 'especial_ou_regional'
    WHEN regime ~* '111-B/2017' THEN 'ccp2017'
    WHEN regime ~* '18/2008' THEN 'ccp2008'
    ELSE 'outro'
  END
$$;

ALTER TABLE contrato
  ADD COLUMN IF NOT EXISTS regime_tipo TEXT
    GENERATED ALWAYS AS (classificar_regime(regime, procedimento_original)) STORED,
  ADD COLUMN IF NOT EXISTS criterios_materiais BOOLEAN
    GENERATED ALWAYS AS (CASE dados_origem->>'CritMateriais' WHEN 'Sim' THEN true WHEN 'Não' THEN false END) STORED;

-- Para os indicadores de relação adjudicante-fornecedor.
CREATE INDEX IF NOT EXISTS ix_contrato_adj_data ON contrato(adjudicante_id, data_referencia);

-- Resultados por contrato: 'nao_aplicavel' deixa de ser guardado (ausência = não aplicável), para poupar espaço.
ALTER TABLE avaliacao_risco ALTER COLUMN explicacao DROP NOT NULL;
DELETE FROM avaliacao_risco WHERE estado = 'nao_aplicavel';

-- Formatação de euros independente do locale da BD: 1234567.8 -> '1 234 567,80'
CREATE OR REPLACE FUNCTION eur(v numeric) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT translate(to_char(v, 'FM999,999,999,990.00'), ',.', ' ,')
$$;
