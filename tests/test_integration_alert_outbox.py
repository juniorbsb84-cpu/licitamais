"""Costura da outbox de usuario com o schema SQLite real."""

import sqlite3

from licitamais.alerts.digest import deliver_outbox, flush_outbox
from licitamais.alerts.user import enqueue_alert
from licitamais.schema import init_schema


def test_digest_le_schema_real_sem_marcar_envio_antes_de_entregar(tmp_path):
    con = sqlite3.connect(tmp_path / "alerts.db")
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('teste', 'api_json', 'https://teste.invalid', 'v1')"
    ).lastrowid
    process_id = con.execute(
        "INSERT INTO process (source_id, source_native_id, title, opening_at_source) "
        "VALUES (?, 'p-1', 'Papel', '2030-01-01T10:00:00Z')",
        (source_id,),
    ).lastrowid
    con.execute("INSERT INTO alert_subscription (id, kind, channel, enabled) VALUES (7, 'filtro', 'telegram', 1)")
    alert_id = enqueue_alert(con, 7, process_id, "new", "run-1")
    assert con.execute("SELECT subscription_id FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0] == 7

    try:
        digest = flush_outbox(con, user_id=7, run_id=1)
        assert digest is not None
        assert [p.process_id for p in digest.top_processes] == [process_id]
        assert con.execute("SELECT status FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0] == "pending"
    finally:
        con.close()


def test_entrega_so_confirma_apos_sender_aceitar_e_retry_recupera(tmp_path):
    con = sqlite3.connect(tmp_path / "delivery.db")
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('teste', 'api_json', 'https://teste.invalid', 'v1')"
    ).lastrowid
    process_id = con.execute(
        "INSERT INTO process (source_id, source_native_id, title, opening_at_source) "
        "VALUES (?, 'p-1', 'Papel', '2030-01-01T10:00:00Z')",
        (source_id,),
    ).lastrowid
    con.execute("INSERT INTO alert_subscription (id, kind, channel, enabled) VALUES (7, 'filtro', 'telegram', 1)")
    alert_id = enqueue_alert(con, 7, process_id, "new", "run-1")

    class Sender:
        def __init__(self):
            self.accept = False
            self.messages = []

        def send(self, text):
            self.messages.append(text)
            return self.accept

    sender = Sender()
    try:
        assert deliver_outbox(con, sender, 7, 1) == 0
        assert con.execute("SELECT status FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0] == "pending"
        sender.accept = True
        assert deliver_outbox(con, sender, 7, 1) == 1
        assert con.execute("SELECT status, sent_at FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0] == "sent"
        assert deliver_outbox(con, sender, 7, 1) == 0
        assert len(sender.messages) == 2
    finally:
        con.close()
