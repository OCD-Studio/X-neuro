-- Minimização de dados pessoais (RGPD): distinguir pessoas coletivas de pessoas singulares.
-- A fonte nunca publica NIF de pessoas singulares (aparecem só pelo nome). Regra conservadora:
--   coletiva          NIF de pessoa coletiva (5, 6, 9, 71, 72, 73, 77, 79)
--   singular          NIF de herança indivisa (70, 74, 75) ou de empresário em nome individual (8)
--   coletiva_sem_nif  sem NIF, mas o nome tem forma jurídica/institucional explícita (Lda, S.A., GmbH, Associação…)
--                     ("SA" só com pontos ou depois de vírgula: "Sá"/"Sa" é também apelido)
--   singular_provavel sem NIF e sem forma jurídica no nome (pessoas, empresários em nome individual, nomes ambíguos)
-- Só 'coletiva' e 'coletiva_sem_nif' aparecem em listas e perfis com score.
CREATE OR REPLACE FUNCTION classificar_pessoa(nif text, nome text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN nif IS NOT NULL AND nif ~ '^(70|74|75|8)' THEN 'singular'
    WHEN nif IS NOT NULL AND nif ~ '^(5|6|9|71|72|73|77|79)' THEN 'coletiva'
    WHEN nif IS NOT NULL THEN 'singular_provavel'
    WHEN lower(coalesce(nome, '')) ~ (
        '(,\s*s\.?\s?a\.?$)|(\ms\.\s?a\.?$)'
        '|(\m(lda|ld[aª]|limitada|unipessoal|sgps|crl|c\.r\.l|ace|e\.?p\.?e|ltd|limited|llc|plc|inc|incorporated|corp|corporation|gmbh|ag|s\.?l\.?u?|s\.?r\.?l|sarl|sas|spa|s\.p\.a|b\.?v|n\.?v|d\.o\.o|oy|ab|a/s|kft|sp\. z o\.o)\.?$)'
        '|\m(associa[cç][aã]o|funda[cç][aã]o|instituto|cooperativa|universidade|federa[cç][aã]o|sociedade|companhia|company|group|grupo|hospital|clube|club|santa casa|miseric[oó]rdia|munic[ií]pio|freguesia|agrupamento|escola|col[eé]gio|centro social|ipss|cons[oó]rcio|ag[eê]ncia|empresa|editora|laborat[oó]rio)\M')
      THEN 'coletiva_sem_nif'
    ELSE 'singular_provavel'
  END
$$;

ALTER TABLE entidade ADD COLUMN IF NOT EXISTS tipo_pessoa TEXT
  GENERATED ALWAYS AS (classificar_pessoa(nif, nome)) STORED;
CREATE INDEX IF NOT EXISTS ix_entidade_tipo_pessoa ON entidade(tipo_pessoa);
