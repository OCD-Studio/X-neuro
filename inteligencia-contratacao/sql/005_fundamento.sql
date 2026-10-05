-- Base legal invocada para o ajuste direto (campo 'fundamentacao' do BASE), normalizada.
-- O campo 'CritMateriais' da fonte não é fiável: há contratos marcados "Não" que invocam o art. 24.º
-- (critérios materiais). Os indicadores de limiar usam esta classificação.
--   art19a / art20a = escolha em função do valor, CCP versão DL 18/2008 (empreitadas / bens e serviços)
--   art19d / art20d = escolha em função do valor, CCP revisto pelo DL 111-B/2017
--   outro           = outra base legal (critérios materiais, regimes especiais…)
--   NULL            = fundamentação em falta na fonte
CREATE OR REPLACE FUNCTION classificar_fundamento(fund text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN fund IS NULL OR btrim(fund) = '' THEN NULL
    WHEN fund ~ '^\s*Artigo 19\.º, alínea a\)' THEN 'art19a'
    WHEN fund ~ '^\s*Artigo 20\.º(, n\.º 1)?, alínea a\)' THEN 'art20a'
    WHEN fund ~ '^\s*Artigo 19\.º, alínea d\)' THEN 'art19d'
    WHEN fund ~ '^\s*Artigo 20\.º(, n\.º 1)?, alínea d\)' THEN 'art20d'
    ELSE 'outro'
  END
$$;

ALTER TABLE contrato ADD COLUMN IF NOT EXISTS fundamento_ad TEXT
  GENERATED ALWAYS AS (classificar_fundamento(dados_origem->>'fundamentacao')) STORED;
