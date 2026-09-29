"""M4: cada assinante recebe no proprio chat (alert_subscription.target), nao no do operador."""

from datetime import datetime
from zoneinfo import ZoneInfo

from licitamais.alerts.user import enqueue_alert
from licitamais.scheduler import entregar_alertas
from tests.test_r13_runner_alertas import fonte_id, nova_conexao


def test_alerta_vai_para_o_chat_do_assinante():
    con = nova_conexao()
    sid = fonte_id(con)
    pid = con.execute(
        "INSERT INTO process (source_id, source_native_id, title, opening_at_source)"
        " VALUES (?, 'p1', 'Papel', '2030-01-01T10:00:00Z')",
        (sid,),
    ).lastrowid
    subs = {}
    for chat in ("chat-A", "chat-B"):
        subs[chat] = con.execute(
            "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at)"
            " VALUES ('filtro', 'telegram', ?, '{}', 1, '2026-09-23')",
            (chat,),
        ).lastrowid
        enqueue_alert(con, subs[chat], pid, "new", "run-1")
    con.commit()
    enviados = []

    class Sender:
        def __init__(self, chat):
            self.chat = chat

        def send(self, texto):
            enviados.append(self.chat)
            return True

    n = entregar_alertas(con, Sender, datetime(2026, 9, 23, 12, tzinfo=ZoneInfo("America/Sao_Paulo")))
    assert n == 2
    assert sorted(enviados) == ["chat-A", "chat-B"]
