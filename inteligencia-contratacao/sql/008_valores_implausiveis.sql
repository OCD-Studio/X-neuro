-- A fonte tem valores absurdos (p.ex. preço base de 34 000 000 000 000 000 € num anúncio de 2020).
-- Guardam-se tal como publicados (nunca se corrigem) e assinalam-se no relatório de qualidade.
ALTER TABLE anuncio ALTER COLUMN preco_base TYPE NUMERIC;
