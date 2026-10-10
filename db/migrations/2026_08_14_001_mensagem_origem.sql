-- Adds semantic authorship without rewriting historical messages.
-- Existing NULL values intentionally remain unknown for compatibility.
ALTER TABLE mensagens_historico
    ADD COLUMN IF NOT EXISTS origem_mensagem VARCHAR(32) NULL;

COMMENT ON COLUMN mensagens_historico.origem_mensagem IS
    'Semantic message origin: cliente, ia, humano, sistema, followup or campanha.';
