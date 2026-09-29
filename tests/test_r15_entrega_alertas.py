"""Fase vermelha do TDD para T15: remediacao-entrega-alertas-scheduler.

Os alertas enfileirados nunca saem: nada chama deliver_outbox. Estes testes
provam o comportamento esperado de `entregar_alertas` (em licitamais.scheduler)
e do fluxo do scheduler, usando SQLite real (init_schema) e remetentes falsos,
sem nenhuma chamada de rede.
"""

import importlib
import inspect
import logging
import sqlite3
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import licitamais.scheduler as modulo_scheduler
from licitamais.alerts.digest import flush_outbox, is_silent_now
from licitamais.alerts.user import enqueue_alert
from licitamais.schema import init_schema

BRT = "America/Sao_Paulo"
CODIGO_FONTE = "fonte_teste"


def exigir_entregar():
    """Resolve entregar_alertas ou falha pelo defeito (ausencia no scheduler)."""
    fn = getattr(modulo_scheduler, "entregar_alertas", None)
    assert callable(fn), "scheduler precisa expor entregar_alertas(con, sender, agora)"
    return fn


def agora_brt(hora):
    """Devolve hoje as `hora` em BRT, para a janela silenciosa acompanhar o relogio real."""
    hoje = datetime.now(ZoneInfo(BRT)).date()
    return datetime(hoje.year, hoje.month, hoje.day, int(hora), 0, tzinfo=ZoneInfo(BRT))


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES (?, 'api_json', 'https://fonte.test', 'teste-1', 1)",
        (CODIGO_FONTE,),
    )
    con.commit()
    return con


def fonte_id(con):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (CODIGO_FONTE,)).fetchone()
    assert linha is not None
    return int(linha[0])


def criar_processo(con, abertura_iso, nativo):
    sid = fonte_id(con)
    cur = con.execute(
        "INSERT INTO process (source_id, source_native_id, title, object, opening_at_source)"
        " VALUES (?, ?, 'Pregao de material', 'aquisicao de notebook', ?)",
        (sid, nativo, abertura_iso),
    )
    con.commit()
    return int(cur.lastrowid)


def enfileirar(con, usuario, processo, kind="new", ref="run-1"):
    con.execute(
        "INSERT OR IGNORE INTO alert_subscription (id, kind, channel, enabled) VALUES (?, 'filtro', 'telegram', 1)",
        (usuario,),
    )
    alerta = enqueue_alert(con, usuario, processo, kind, ref)
    con.commit()
    return int(alerta)


def ler_status(con, alerta):
    linha = con.execute("SELECT status, sent_at FROM alert_outbox WHERE id = ?", (alerta,)).fetchone()
    assert linha is not None
    return linha[0], linha[1]


class RemetenteOk:
    """Duble de TelegramSender que aceita tudo, sem rede."""

    def __init__(self):
        self.textos = []

    def send(self, texto):
        self.textos.append(texto)
        return True


class RemetenteFalso:
    """Duble que recusa o envio (send devolve False), sem rede."""

    def __init__(self):
        self.textos = []

    def send(self, texto):
        self.textos.append(texto)
        return False


class RemetenteQuebra:
    """Duble que levanta excecao no envio, sem rede."""

    def send(self, texto):
        raise RuntimeError("telegram fora do ar")


def test_contrato_entregar_alertas_existe_com_assinatura():
    entregar = exigir_entregar()
    nomes = list(inspect.signature(entregar).parameters)
    assert nomes[:3] == ["con", "sender", "agora"], (
        f"entregar_alertas precisa ter assinatura (con, sender, agora), achado {nomes!r}"
    )


def test_sender_ok_entrega_pending_com_sent_at():
    entregar = exigir_entregar()
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-ok-1")
        alerta = enfileirar(con, 7, proc)
        assert ler_status(con, alerta)[0] == "pending"
        n = entregar(con, RemetenteOk(), agora_brt(12))
        assert n == 1
        status, enviado_em = ler_status(con, alerta)
        assert status == "sent"
        assert enviado_em, "alerta sent precisa de sent_at preenchido"
    finally:
        con.close()


def test_sender_falso_mantem_pending_e_retry_entrega():
    entregar = exigir_entregar()
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-falso-1")
        alerta = enfileirar(con, 7, proc)
        assert entregar(con, RemetenteFalso(), agora_brt(12)) == 0
        assert ler_status(con, alerta)[0] == "pending"
        assert entregar(con, RemetenteOk(), agora_brt(12)) == 1
        status, enviado_em = ler_status(con, alerta)
        assert status == "sent"
        assert enviado_em, "retry com sender ok deveria preencher sent_at"
    finally:
        con.close()


def test_sender_excecao_mantem_pending_e_retry_entrega():
    entregar = exigir_entregar()
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-quebra-1")
        alerta = enfileirar(con, 7, proc)
        assert entregar(con, RemetenteQuebra(), agora_brt(12)) == 0
        assert ler_status(con, alerta)[0] == "pending"
        assert entregar(con, RemetenteOk(), agora_brt(12)) == 1
        status, enviado_em = ler_status(con, alerta)
        assert status == "sent"
        assert enviado_em, "retry com sender ok deveria preencher sent_at"
    finally:
        con.close()


def test_horario_silencioso_so_urgente_sai():
    entregar = exigir_entregar()
    agora = agora_brt(22)
    assert is_silent_now(agora) is True
    assert is_silent_now(agora_brt(12)) is False
    con = nova_conexao()
    try:
        proc_normal = criar_processo(con, (agora + timedelta(days=30)).isoformat(), "edital-normal-1")
        proc_urgente = criar_processo(con, (agora + timedelta(hours=12)).isoformat(), "edital-urgente-1")
        al_normal = enfileirar(con, 9, proc_normal, "new", "run-1")
        al_urgente = enfileirar(con, 9, proc_urgente, "new", "run-1")
        remetente = RemetenteOk()
        n = entregar(con, remetente, agora)
        assert n == 1, "no horario silencioso so o urgente deveria virar sent"
        assert ler_status(con, al_urgente)[0] == "sent"
        assert ler_status(con, al_normal)[0] == "pending"
        assert remetente.textos, "o urgente deveria ter sido enviado ao remetente"
    finally:
        con.close()


def test_scheduler_chama_entregar_alertas_apos_run(monkeypatch):
    exigir_entregar()
    modulo_runner = importlib.import_module("licitamais.runner")
    chamadas_run = []
    chamadas_entrega = []

    def falso_run_source(*args, **kwargs):
        chamadas_run.append((args, kwargs))
        return SimpleNamespace(
            run_id=1,
            source_id=1,
            source_code=CODIGO_FONTE,
            status="ok",
            counts=SimpleNamespace(),
            error=None,
        )

    def espiar_entrega(con, sender, agora):
        chamadas_entrega.append((sender, agora))
        return 0

    monkeypatch.setattr(modulo_runner, "run_source", falso_run_source)
    monkeypatch.setattr(modulo_scheduler, "entregar_alertas", espiar_entrega)
    con = nova_conexao()
    try:
        cfg = modulo_scheduler.SchedulerConfig(trigger="cron", adapters={CODIGO_FONTE: object()})
        modulo_scheduler.run_scheduler(con, cfg)
    finally:
        con.close()
    assert chamadas_run, "scheduler deveria rodar as fontes"
    assert chamadas_entrega, "scheduler deveria chamar entregar_alertas depois do run"


def test_entregar_sem_sender_preserva_pending():
    entregar = exigir_entregar()
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-sem-sender-1")
        alerta = enfileirar(con, 7, proc)
        n = entregar(con, None, agora_brt(12))
        assert n == 0
        assert ler_status(con, alerta)[0] == "pending"
    finally:
        con.close()


def test_scheduler_sem_sender_nao_marca_sent_e_avisa(monkeypatch, caplog):
    exigir_entregar()
    modulo_runner = importlib.import_module("licitamais.runner")

    def falso_run_source(*args, **kwargs):
        return SimpleNamespace(
            run_id=1,
            source_id=1,
            source_code=CODIGO_FONTE,
            status="ok",
            counts=SimpleNamespace(),
            error=None,
        )

    monkeypatch.setattr(modulo_runner, "run_source", falso_run_source)
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-sched-sem-sender-1")
        alerta = enfileirar(con, 7, proc)
        cfg = modulo_scheduler.SchedulerConfig(trigger="cron", adapters={CODIGO_FONTE: object()})
        with caplog.at_level(logging.WARNING):
            modulo_scheduler.run_scheduler(con, cfg)
        assert ler_status(con, alerta)[0] == "pending"
        avisos = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert avisos, "scheduler sem token deveria registrar aviso sem marcar sent"
    finally:
        con.close()


def test_payload_corrompido_nao_aborta_entrega_do_alerta_valido():
    """REGRESSAO: json.loads sem try em flush_outbox.

    1 payload corrompido de outro usuario aborta a entrega de todos,
    e o `except: pass` de run_scheduler mascara como run ok em silencio.
    O comportamento esperado e que o alerta valido seja entregue mesmo
    com um payload corrompido presente na outbox.
    """
    exigir_entregar()
    con = nova_conexao()
    try:
        abertura = (agora_brt(12) + timedelta(days=30)).isoformat()
        proc = criar_processo(con, abertura, "edital-regressao-corrompido-1")
        alerta = enfileirar(con, 7, proc)
        con.execute(
            "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status) VALUES (NULL, ?, ?, 'pending')",
            ("outro-usuario-corrompido-1", "{json invalido"),
        )
        con.commit()
        digest = flush_outbox(con, 7, 1)
        assert digest is not None, (
            "flush_outbox abortou por payload corrompido de outro usuario;"
            " o alerta valido do usuario 7 deveria ter sido montado"
        )
        n = modulo_scheduler.entregar_alertas(con, RemetenteOk(), agora_brt(12))
        assert n == 1, (
            "payload corrompido abortou a entrega de todos os usuarios; o alerta valido deveria ter virado sent"
        )
        status, enviado_em = ler_status(con, alerta)
        assert status == "sent"
        assert enviado_em, "alerta valido deveria ter sent_at preenchido"
    finally:
        con.close()
