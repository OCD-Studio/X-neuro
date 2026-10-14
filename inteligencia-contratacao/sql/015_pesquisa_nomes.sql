-- Pesquisa de nomes sem acentos (sem depender da extensão unaccent, que pode não existir no alojamento).
CREATE OR REPLACE FUNCTION unaccent_simples(t text) RETURNS text LANGUAGE sql IMMUTABLE PARALLEL SAFE AS $$
  SELECT lower(translate(t, 'áàâãäéèêëíìîïóòôõöúùûüçñÁÀÂÃÄÉÈÊËÍÌÎÏÓÒÔÕÖÚÙÛÜÇÑ',
                            'aaaaaeeeeiiiiooooouuuucnaaaaaeeeeiiiiooooouuuucn'))
$$;
CREATE INDEX IF NOT EXISTS ix_cargo_entidade_et ON cargo (entidade_et_id);
CREATE INDEX IF NOT EXISTS ix_cargo_pessoa ON cargo (pessoa_id);
