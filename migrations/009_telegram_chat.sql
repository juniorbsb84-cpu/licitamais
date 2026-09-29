-- Vinculo persistente do chat com a conta, mesmo sem alertas cadastrados.
-- A migracao e apenas aditiva; registros antigos sao associados ao revincular.
CREATE TABLE telegram_chat (
    chat_id TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL UNIQUE REFERENCES account(id),
    linked_at TEXT NOT NULL
);
