"""R15b: user_id gravado como string no payload nao pode travar o alerta."""

import json
import sqlite3

from licitamais.alerts.digest import deliver_outbox, flush_outbox
from licitamais.alerts.user import enqueue_alert
from licitamais.schema import init_schema


def test_payload_com_user_id_string_e_entregue(tmp_path):
    con = sqlite3.connect(tmp_path / "tipo.db")
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
    payload = json.loads(con.execute("SELECT payload FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0])
    payload["user_id"] = "7"
    con.execute("UPDATE alert_outbox SET payload = ? WHERE id = ?", (json.dumps(payload), alert_id))
    con.commit()

    class Sender:
        def send(self, text):
            return True

    try:
        assert flush_outbox(con, user_id=7, run_id=1) is not None
        assert deliver_outbox(con, Sender(), 7, 1) == 1
        assert con.execute("SELECT status FROM alert_outbox WHERE id = ?", (alert_id,)).fetchone()[0] == "sent"
    finally:
        con.close()
