"""Fase vermelha do TDD para entrega e renotificacao de incidentes ao operador."""

import inspect
import sqlite3
from datetime import UTC, datetime, timedelta

import licitamais.scheduler as modulo_scheduler
from licitamais.runner import RunCounts, RunOutcome
from licitamais.schema import init_schema


def exigir_notificar_operador():
    funcao = getattr(modulo_scheduler, "notificar_operador", None)
    assert callable(funcao), "scheduler precisa expor notificar_operador(con, sender, agora)"
    return funcao


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled) "
        "VALUES ('fonte_r19', 'api_json', 'https://fonte.test', 'teste-1', 1)"
    )
    con.commit()
    return con


def criar_incidente(con, *, fechado=None):
    fonte_id = con.execute("SELECT id FROM source WHERE code = 'fonte_r19'").fetchone()[0]
    cursor = con.execute(
        "INSERT INTO incident "
        "(source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) "
        "VALUES (?, 'failed', 'alta', ?, ?, NULL, 0, 'falha simulada')",
        (fonte_id, datetime(2026, 9, 23, tzinfo=UTC).isoformat(), fechado),
    )
    con.commit()
    return int(cursor.lastrowid)


def estado_incidente(con, incidente_id):
    return con.execute(
        "SELECT notify_count, last_notified_at FROM incident WHERE id = ?",
        (incidente_id,),
    ).fetchone()


class SenderOk:
    def __init__(self, resultado=True):
        self.resultado = resultado
        self.textos = []

    def send(self, texto):
        self.textos.append(texto)
        return self.resultado


class SenderExcecao:
    def send(self, _texto):
        raise RuntimeError("telegram indisponivel")


def test_contrato_notificar_operador():
    funcao = exigir_notificar_operador()
    assert list(inspect.signature(funcao).parameters) == ["con", "sender", "agora"]


def test_notifica_incidente_novo_e_renotifica_apos_seis_horas():
    con = nova_conexao()
    try:
        incidente_id = criar_incidente(con)
        notificar = exigir_notificar_operador()
        sender = SenderOk()
        inicio = datetime(2026, 9, 23, 10, tzinfo=UTC)

        assert notificar(con, sender, inicio) == 1
        primeiro = estado_incidente(con, incidente_id)
        assert primeiro[0] == 1
        assert primeiro[1], "envio confirmado deve preencher last_notified_at"
        assert len(sender.textos) == 1

        assert notificar(con, sender, inicio + timedelta(hours=1)) == 0
        assert estado_incidente(con, incidente_id)[0] == 1
        assert len(sender.textos) == 1

        assert notificar(con, sender, inicio + timedelta(hours=7)) == 1
        assert estado_incidente(con, incidente_id)[0] == 2
        assert len(sender.textos) == 2
    finally:
        con.close()


def test_envio_recusado_ou_com_excecao_preserva_estado_e_nao_propaga():
    con = nova_conexao()
    try:
        incidente_id = criar_incidente(con)
        notificar = exigir_notificar_operador()
        inicio = datetime(2026, 9, 23, 10, tzinfo=UTC)

        assert notificar(con, SenderOk(False), inicio) == 0
        assert tuple(estado_incidente(con, incidente_id)) == (0, None)

        assert notificar(con, SenderExcecao(), inicio) == 0
        assert tuple(estado_incidente(con, incidente_id)) == (0, None)
    finally:
        con.close()


def test_incidente_fechado_nao_e_notificado():
    con = nova_conexao()
    try:
        incidente_id = criar_incidente(con, fechado=datetime(2026, 9, 23, 9, tzinfo=UTC).isoformat())
        sender = SenderOk()

        assert exigir_notificar_operador()(con, sender, datetime(2026, 9, 23, 10, tzinfo=UTC)) == 0
        assert tuple(estado_incidente(con, incidente_id)) == (0, None)
        assert sender.textos == []
    finally:
        con.close()


def test_scheduler_notifica_depois_das_fontes_e_sem_sender_nao_marca(monkeypatch, caplog):
    con = nova_conexao()
    try:
        incidente_id = criar_incidente(con)
        ordem = []

        def executar_fonte(_con, fonte, _cfg):
            ordem.append("fonte")
            return RunOutcome(1, fonte.id, fonte.code, "ok", RunCounts())

        funcao = exigir_notificar_operador()
        chamada_real = []

        def espiar_notificacao(conexao, sender, agora):
            ordem.append("incidente")
            chamada_real.append((sender, agora))
            return funcao(conexao, sender, agora)

        monkeypatch.setattr("licitamais.runner.run_source", executar_fonte)
        monkeypatch.setattr(modulo_scheduler, "notificar_operador", espiar_notificacao)
        sender = SenderOk()
        cfg = modulo_scheduler.SchedulerConfig(adapters={"fonte_r19": object()}, sender=sender)

        modulo_scheduler.run_scheduler(con, cfg)
        assert ordem == ["fonte", "incidente"]
        assert chamada_real and chamada_real[0][0] is sender
        assert estado_incidente(con, incidente_id)[0] == 1

        incidente_sem_sender = criar_incidente(con)
        sem_sender_cfg = modulo_scheduler.SchedulerConfig(adapters={"fonte_r19": object()}, sender=None)
        with caplog.at_level("WARNING"):
            modulo_scheduler.run_scheduler(con, sem_sender_cfg)
        assert tuple(estado_incidente(con, incidente_sem_sender)) == (0, None)
        assert "sender" in caplog.text.lower() or "operador" in caplog.text.lower()
    finally:
        con.close()
