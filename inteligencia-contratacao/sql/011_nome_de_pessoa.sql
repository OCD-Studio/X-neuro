-- Pessoas coletivas (NIF próprio) cujo nome é o de uma pessoa (p.ex. sociedades unipessoais "M. Odete Machado").
-- São listadas, mas a app avisa que o sinal se refere à entidade e não à pessoa.
ALTER TABLE entidade ADD COLUMN IF NOT EXISTS nome_de_pessoa BOOLEAN
  GENERATED ALWAYS AS (classificar_pessoa(nif, nome) = 'coletiva' AND classificar_pessoa(NULL, nome) = 'singular_provavel') STORED;
