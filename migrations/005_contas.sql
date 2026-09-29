CREATE TABLE account (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE CHECK (email = lower(trim(email))),
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE login_token (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES account(id),
    email TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL,
    ip TEXT NOT NULL
);
CREATE INDEX ix_login_token_account_created ON login_token(account_id, created_at);
CREATE INDEX ix_login_token_email_created ON login_token(email, created_at);
CREATE INDEX ix_login_token_ip_created ON login_token(ip, created_at);
CREATE TABLE session (
    id INTEGER PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id),
    session_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE telegram_link (
    code_hash TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES account(id),
    expires_at TEXT NOT NULL,
    used_at TEXT
);
ALTER TABLE alert_subscription ADD COLUMN account_id INTEGER REFERENCES account(id);

-- tentativas erradas de /vincular por chat (forca bruta do codigo de 6 digitos)
CREATE TABLE telegram_link_falha (
    id INTEGER PRIMARY KEY,
    chat TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX idx_telegram_link_falha_chat ON telegram_link_falha(chat, at);
