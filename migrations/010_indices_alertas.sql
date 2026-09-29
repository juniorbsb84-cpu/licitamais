-- Consultas de tenant, fila pendente e comandos do bot.
CREATE INDEX idx_alert_subscription_account_enabled ON alert_subscription(account_id, enabled);
CREATE INDEX idx_alert_subscription_target ON alert_subscription(channel, target);
CREATE INDEX idx_alert_outbox_status_subscription ON alert_outbox(status, subscription_id);
CREATE INDEX idx_session_expires ON session(expires_at);
