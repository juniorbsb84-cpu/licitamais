"""R38: mensagem de alerta que vende (formato rico do digest)."""

import json
import sqlite3
from datetime import UTC

from licitamais.alerts.digest import (
    deliver_outbox,
    flush_outbox,
    send_digest,
    send_urgent,
)
from licitamais.alerts.user import enqueue_alert
from licitamais.schema import init_schema


def _con():
    con = sqlite3.connect(":memory:")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES ('brb', 'api_json', 'https://edital.invalid', 'v1', 1)"
    )
    con.commit()
    return con


def _sid(con):
    return int(con.execute("SELECT id FROM source WHERE code = 'brb'").fetchone()[0])


def _processo(con, nativo, numero, objeto, abertura, attrs=None):
    cur = con.execute(
        "INSERT INTO process (source_id, source_native_id, number, object,"
        " opening_at_source, attrs) VALUES (?, ?, ?, ?, ?, ?)",
        (_sid(con), nativo, numero, objeto, abertura, json.dumps(attrs or {})),
    )
    con.commit()
    return int(cur.lastrowid)


def _assinar(con, sub_id, keywords):
    con.execute(
        "INSERT INTO alert_subscription (id, kind, channel, target, filter_expr, enabled)"
        " VALUES (?, 'filtro', 'telegram', ?, ?, 1)",
        (sub_id, f"chat-{sub_id}", json.dumps({"keywords": keywords})),
    )
    con.commit()


def _item_estimado(con, pid, nativo, centavos):
    con.execute(
        "INSERT INTO item (source_id, source_native_id, process_id, description,"
        " total_price_estimated_cents) VALUES (?, ?, ?, 'item', ?)",
        (_sid(con), nativo, pid, centavos),
    )
    con.commit()


def _contrato_hist(con, pid, nativo, centavos, objeto):
    con.execute(
        "INSERT INTO contract (source_id, source_native_id, process_id,"
        " supplier_name_raw, value_cents, attrs) VALUES (?, ?, ?, 'FORN', ?, ?)",
        (_sid(con), nativo, pid, centavos, json.dumps({"object": objeto})),
    )
    con.commit()


class Coletor:
    def __init__(self, aceita=True):
        self.aceita = aceita
        self.textos = []

    def send(self, texto):
        self.textos.append(texto)
        return self.aceita


def _base_rica(con):
    _assinar(con, 7, ["notebook"])
    pid = _processo(
        con,
        "ed-1",
        "057/2026",
        "Aquisicao de notebooks para o escritorio central",
        "2030-06-15T14:00:00Z",
        {"edital_url": "https://edital.invalid/pregao/057-2026"},
    )
    _item_estimado(con, pid, "ed-1:item:1", 15000000)
    for i, centavos in enumerate((10000, 20000, 30000), start=1):
        _contrato_hist(con, pid, f"hist:contrato:{i}", centavos, f"Venda de notebook i7 lote {i}")
    con.execute(
        "INSERT INTO attachment (source_id, source_ref, process_id, kind, url_download)"
        " VALUES (?, 'ed-1:anexo:1', ?, 'edital', 'https://edital.invalid/anexo.pdf')",
        (_sid(con), pid),
    )
    con.commit()
    enqueue_alert(con, 7, pid, "new", "run-1")
    con.commit()
    return pid


def test_item_traz_campos_que_vendem():
    con = _con()
    try:
        _base_rica(con)
        digest = flush_outbox(con, user_id=7, run_id=1)
        assert digest is not None
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        assert len(coletor.textos) == 1
        texto = coletor.textos[0]
        assert "brb" in texto
        assert "057/2026" in texto
        assert "Aquisicao de notebooks" in texto
        assert "15/06/2030" in texto
        assert "150.000,00" in texto
        assert "https://edital.invalid/pregao/057-2026" in texto
        baixo = texto.lower()
        assert "pre" in baixo and "hist" in baixo
        assert "3 contrato" in baixo
        assert "200,00" in texto
    finally:
        con.close()


def test_objeto_truncado_em_200_chars():
    con = _con()
    try:
        _assinar(con, 7, ["notebook"])
        objeto = "x" * 300 + "CAUDA-QUE-NAO-PODE-APARECER"
        pid = _processo(con, "ed-2", "058/2026", objeto, "2030-06-15T10:00:00Z")
        enqueue_alert(con, 7, pid, "new", "run-1")
        con.commit()
        digest = flush_outbox(con, user_id=7, run_id=1)
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        texto = coletor.textos[0]
        assert "CAUDA-QUE-NAO-PODE-APARECER" not in texto
        assert "x" * 201 not in texto
    finally:
        con.close()


def test_html_escapado():
    con = _con()
    try:
        _assinar(con, 7, ["notebook"])
        pid = _processo(
            con,
            "ed-3",
            "059/2026",
            "PROMO oferta & preco",
            "2030-06-15T10:00:00Z",
            {"edital_url": "https://edital.invalid/a?b=1&c=2"},
        )
        enqueue_alert(con, 7, pid, "new", "run-1")
        con.commit()
        digest = flush_outbox(con, user_id=7, run_id=1)
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        texto = coletor.textos[0]
        assert "PROMO oferta" in texto
        assert "&amp;" in texto
        assert "&lt;" in texto or "&gt;" in texto or "&amp;" in texto
    finally:
        con.close()


def test_sem_valor_sem_link_sem_historico_omite_linhas():
    con = _con()
    try:
        _assinar(con, 7, ["notebook"])
        pid = _processo(con, "ed-4", "060/2026", "Servico de limpeza", "2030-06-15T10:00:00Z")
        enqueue_alert(con, 7, pid, "new", "run-1")
        con.commit()
        digest = flush_outbox(con, user_id=7, run_id=1)
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        texto = coletor.textos[0]
        assert "060/2026" in texto
        assert "Servico de limpeza" in texto
        assert "Valor estimado" not in texto
        assert "edital" not in texto.lower()
        assert "hist" not in texto.lower()
    finally:
        con.close()


def test_limite_4096_quebra_em_varias_mensagens():
    con = _con()
    try:
        _assinar(con, 7, ["notebook"])
        for i in range(12):
            pid = _processo(
                con,
                f"ed-lote-{i}",
                f"{100 + i}/2026",
                (f"Aquisicao de notebook modelo {i} ") + "z" * 180,
                "2030-06-15T10:00:00Z",
                {"edital_url": "https://edital.invalid/" + "u" * 380},
            )
            enqueue_alert(con, 7, pid, "new", f"run-{i}")
        con.commit()
        digest = flush_outbox(con, user_id=7, run_id=1)
        assert digest is not None
        assert len(digest.top_processes) == 10
        assert digest.extra_count == 2
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        assert len(coletor.textos) >= 2
        for msg in coletor.textos:
            assert len(msg) <= 4096
        assert "E mais 2" in coletor.textos[-1]
    finally:
        con.close()


def test_entrega_confirma_tudo_ou_nada_mantido():
    con = _con()
    try:
        _base_rica(con)
        coletor = Coletor(aceita=False)
        assert deliver_outbox(con, coletor, 7, 1) == 0
        assert con.execute("SELECT status FROM alert_outbox").fetchone()[0] == "pending"
        coletor_ok = Coletor(aceita=True)
        assert deliver_outbox(con, coletor_ok, 7, 1) == 1
        assert con.execute("SELECT status FROM alert_outbox").fetchone()[0] == "sent"
    finally:
        con.close()


def test_schema_minimo_legado_nao_quebra():
    con = sqlite3.connect(":memory:")
    con.execute(
        "CREATE TABLE alert_outbox (id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,"
        " process_id INTEGER NOT NULL, event_kind TEXT NOT NULL, event_ref TEXT NOT NULL,"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " UNIQUE(user_id, process_id, event_kind, event_ref))"
    )
    con.execute("CREATE TABLE process (id INTEGER PRIMARY KEY, opening_at_source DATETIME)")
    con.execute("INSERT INTO process (id, opening_at_source) VALUES (5, '2030-01-01T10:00:00Z')")
    con.execute(
        "INSERT INTO alert_outbox (user_id, process_id, event_kind, event_ref, status)"
        " VALUES (1, 5, 'new', 'run-1', 'pending')"
    )
    con.commit()
    try:
        digest = flush_outbox(con, user_id=1, run_id=1)
        assert digest is not None
        coletor = Coletor()
        assert send_digest(coletor, digest) is True
        assert len(coletor.textos) == 1
        assert "Processo 5" in coletor.textos[0]
    finally:
        con.close()


def test_urgente_traz_detalhe_e_legado_continua():
    con = _con()
    try:
        from datetime import datetime, timedelta

        _assinar(con, 7, ["notebook"])
        abertura = (datetime.now(UTC) + timedelta(hours=12)).isoformat()
        pid = _processo(con, "ed-u", "061/2026", "Aquisicao urgente de notebook", abertura)
        enqueue_alert(con, 7, pid, "new", "run-1")
        con.commit()
        coletor = Coletor()
        assert deliver_outbox(con, coletor, 7, 1) == 1
        assert coletor.textos, "urgente precisa ser enviado"
        texto = coletor.textos[0]
        assert "061/2026" in texto
        assert "notebook" in texto.lower()
        from licitamais.alerts.digest import OutboxRecord, ProcessRecord

        alerta = OutboxRecord(999, 7, pid, "new", "run-1")
        coletor2 = Coletor()
        assert send_urgent(coletor2, alerta, ProcessRecord(pid, None)) is True
        assert (f"processo {pid}") in coletor2.textos[0]
    finally:
        con.close()


def test_telegram_envia_parse_mode_html(monkeypatch):
    import licitamais.alerts.telegram as modulo

    chamadas = []

    class Resposta:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 1}}

    def falso_post(url, json=None, timeout=None):
        chamadas.append({"url": url, "json": json})
        return Resposta()

    monkeypatch.setattr(modulo.requests, "post", falso_post)
    sender = modulo.TelegramSender(token="t", chat_id="c")
    assert sender.send("oi") is True
    assert chamadas and chamadas[0]["json"]["parse_mode"] == "HTML"
    assert chamadas[0]["json"]["chat_id"] == "c"


def test_view_preco_legado_quando_view_ausente():
    import licitamais.alerts.digest as d

    con = _con()
    try:
        n, mediana = d._preco_historico(con, ("notebook", "i7"))
        assert n == 0 and mediana is None
    finally:
        con.close()


def test_event_kind_desconhecido_nao_vira_urgente_e_nao_fura_janela_silencio():
    """Defeito: event_kind desconhecido (ex: 'teste_manual') virou URGENTE e furou a janela de silencio.

    Somente tipos explicitamente urgentes sao urgentes; o resto e digest normal.
    No horario silencioso (so_urgentes=True), o evento desconhecido deve permanecer pending e nao ser enviado.
    """
    from datetime import datetime, timedelta

    con = _con()
    try:
        _assinar(con, 7, ["notebook"])
        # Abertura bem proxima (<48h), que induzia falsamente a urgencia se nao validasse event_kind
        abertura = (datetime.now(UTC) + timedelta(hours=6)).isoformat()
        pid = _processo(con, "ed-desc", "062/2026", "Aquisicao teste_manual de notebook", abertura)
        enqueue_alert(con, 7, pid, "teste_manual", "run-1")
        con.commit()

        # 1. flush_outbox deve colocar no digest normal (top_processes), nao em urgent_alerts
        digest = flush_outbox(con, user_id=7, run_id=1)
        assert digest is not None
        assert len(digest.urgent_alerts) == 0, "event_kind desconhecido nao pode virar urgente"
        assert len(digest.top_processes) == 1, "event_kind desconhecido deve ir para o digest normal"

        # 2. Em horario de silencio (so_urgentes=True), nada deve ser enviado e deve continuar pending
        coletor_silencio = Coletor()
        enviados = deliver_outbox(con, coletor_silencio, 7, 1, so_urgentes=True)
        assert enviados == 0
        assert len(coletor_silencio.textos) == 0
        assert (
            con.execute(
                "SELECT status FROM alert_outbox WHERE json_extract(payload, '$.event_kind') = 'teste_manual'"
            ).fetchone()[0]
            == "pending"
        )

        # 3. Fora do silencio (so_urgentes=False), sai normalmente como digest
        coletor_normal = Coletor()
        enviados = deliver_outbox(con, coletor_normal, 7, 1, so_urgentes=False)
        assert enviados == 1
        assert len(coletor_normal.textos) == 1
        assert "062/2026" in coletor_normal.textos[0]
        assert (
            con.execute(
                "SELECT status FROM alert_outbox WHERE json_extract(payload, '$.event_kind') = 'teste_manual'"
            ).fetchone()[0]
            == "sent"
        )
    finally:
        con.close()
