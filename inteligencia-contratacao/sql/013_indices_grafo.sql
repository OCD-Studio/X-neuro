-- Histórico de versões de uma relação (todas as versões, correntes e substituídas).
CREATE INDEX IF NOT EXISTS ix_relacao_par ON relacao (origem_id, destino_id, tipo);
CREATE INDEX IF NOT EXISTS ix_relacao_destino_par ON relacao (destino_id, origem_id, tipo) WHERE substituido_em IS NULL;
