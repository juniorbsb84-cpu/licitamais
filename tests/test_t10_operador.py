"""Fase vermelha do TDD para alertas do operador via Telegram (T10)."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import pathlib
import sqlite3
import urllib.request
from datetime import UTC, date, datetime, timedelta

import pytest
import requests

from licitamais.schema import init_schema

MODULO_OPERATOR = "licitamais.alerts.operator"
MODULO_TELEGRAM = "licitamais.alerts.telegram"
MODULOS = (MODULO_OPERATOR, MODULO_TELEGRAM)
MODULOS_TIPOS = MODULOS + (
    "licitamais.runner",
    "licitamais.health",
    "licitamais.probes",
    "licitamais.types",
)
ENV_TOKEN = "TELEGRAM_OPERATOR_BOT_TOKEN"
ENV_CHAT = "TELEGRAM_OPERATOR_CHAT_ID"
AGORA = "2026-09-22T12:00:00Z"


class Registro(dict):
    """Duble flexivel para tipos de producao ainda inexistentes."""

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


class RespostaFake:
    """Resposta minima da Bot API, sem qualquer rede real."""

    def __init__(self, corpo=None):
        self.status_code = 200
        self.status = 200
        self.headers = {"Content-Type": "application/json"}
        self._corpo = corpo if corpo is not None else {"ok": True, "result": {"message_id": 1}}
        try:
            self.text = json.dumps(self._corpo, ensure_ascii=False)
        except Exception:
            self.text = '{"ok": true}'
        self.content = self.text.encode("utf-8")
        self.ok = True

    def json(self):
        return self._corpo

    def raise_for_status(self):
        return None

    def read(self):
        return self.content

    def getcode(self):
        return 200


def carregar_modulo(nome):
    """Falha pelo ausencia do modulo, sem abortar a coleta."""
    spec = importlib.util.find_spec(nome)
    assert spec is not None, f"O modulo de producao {nome} ainda nao existe."
    return importlib.import_module(nome)


def obter_funcao(nome):
    for nome_modulo in MODULOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if hasattr(modulo, nome):
            fn = getattr(modulo, nome)
            if callable(fn):
                return fn
    assert False, f"A funcao de producao {nome} ainda nao existe em {MODULOS}."


def obter_classe(nome):
    for nome_modulo in MODULOS_TIPOS:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        try:
            modulo = importlib.import_module(nome_modulo)
        except Exception:
            continue
        if hasattr(modulo, nome):
            return getattr(modulo, nome)
    return None


def exigir_classe(nome):
    cls = obter_classe(nome)
    assert cls is not None, f"A classe de producao {nome} ainda nao existe."
    return cls


def dublar_telegram_sucesso(monkeypatch, chamadas):
    """Troca requests e urllib por dublas que registram sem rede real."""

    def fake_post(url=None, data=None, json=None, **kwargs):
        chamadas.append({"metodo": "POST", "url": str(url), "data": str(data), "json": json, "extra": str(kwargs)})
        return RespostaFake()

    def fake_get(url=None, params=None, **kwargs):
        chamadas.append({"metodo": "GET", "url": str(url), "params": str(params), "extra": str(kwargs)})
        return RespostaFake()

    def fake_request(method=None, url=None, **kwargs):
        chamadas.append(
            {
                "metodo": str(method),
                "url": str(url),
                "json": kwargs.get("json"),
                "data": kwargs.get("data"),
                "extra": str(kwargs),
            }
        )
        return RespostaFake()

    def fake_session_request(self, method=None, url=None, **kwargs):
        chamadas.append(
            {
                "metodo": str(method),
                "url": str(url),
                "json": kwargs.get("json"),
                "data": kwargs.get("data"),
                "extra": str(kwargs),
            }
        )
        return RespostaFake()

    def fake_session_post(self, url=None, data=None, json=None, **kwargs):
        chamadas.append({"metodo": "POST", "url": str(url), "data": str(data), "json": json})
        return RespostaFake()

    def fake_session_get(self, url=None, params=None, **kwargs):
        chamadas.append({"metodo": "GET", "url": str(url), "params": str(params)})
        return RespostaFake()

    def fake_urlopen(url, data=None, **kwargs):
        chamadas.append({"metodo": "URLLIB", "url": str(url), "data": str(data)})
        return RespostaFake()

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(requests.Session, "request", fake_session_request)
    monkeypatch.setattr(requests.Session, "post", fake_session_post)
    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return chamadas


def dublar_telegram_falha(monkeypatch, chamadas):
    """Mesma dubla, mas a rede sempre falha sem levantar fora do send."""

    def fake_post(url=None, data=None, json=None, **kwargs):
        chamadas.append({"metodo": "POST", "url": str(url), "data": str(data), "json": json})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_get(url=None, params=None, **kwargs):
        chamadas.append({"metodo": "GET", "url": str(url)})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_request(method=None, url=None, **kwargs):
        chamadas.append({"metodo": str(method), "url": str(url)})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_session_request(self, method=None, url=None, **kwargs):
        chamadas.append({"metodo": str(method), "url": str(url)})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_session_post(self, url=None, data=None, json=None, **kwargs):
        chamadas.append({"metodo": "POST", "url": str(url)})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_session_get(self, url=None, params=None, **kwargs):
        chamadas.append({"metodo": "GET", "url": str(url)})
        raise requests.exceptions.ConnectionError("rede simulada t10")

    def fake_urlopen(url, data=None, **kwargs):
        chamadas.append({"metodo": "URLLIB", "url": str(url)})
        raise ConnectionError("rede simulada t10")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(requests, "request", fake_request)
    monkeypatch.setattr(requests.Session, "request", fake_session_request)
    monkeypatch.setattr(requests.Session, "post", fake_session_post)
    monkeypatch.setattr(requests.Session, "get", fake_session_get)
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return chamadas


def juntar_chamadas(chamadas):
    partes = []
    for chamada in chamadas:
        try:
            partes.append(json.dumps(chamada, ensure_ascii=False, default=str))
        except Exception:
            partes.append(str(chamada))
    return " ".join(partes)


def espiar_send(monkeypatch, sender):
    textos = []
    original = sender.send

    def espiado(text, *args, **kwargs):
        textos.append(str(text))
        return original(str(text), *args, **kwargs)

    monkeypatch.setattr(sender, "send", espiado)
    return textos


def agora_iso():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalizar_segundos(valor):
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    if isinstance(valor, timedelta):
        return valor.total_seconds()
    if isinstance(valor, datetime):
        base = valor
        if base.tzinfo is None:
            base = base.replace(tzinfo=UTC)
        return (base - datetime.now(UTC)).total_seconds()
    if isinstance(valor, date):
        base = datetime(valor.year, valor.month, valor.day, tzinfo=UTC)
        return (base - datetime.now(UTC)).total_seconds()
    if isinstance(valor, str):
        s = valor.strip()
        if not s:
            return None
        try:
            return float(s)
        except Exception:
            pass
        try:
            txt = s
            if txt.endswith("Z"):
                txt = txt[:-1] + "+00:00"
            dt = datetime.fromisoformat(txt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (dt - datetime.now(UTC)).total_seconds()
        except Exception:
            return None
    return None


@pytest.fixture()
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    conexao.executemany(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES (?, 'api_json', ?, 'teste-1', ?)",
        [
            ("brb", "https://brb.test", 1),
            ("sistema_industria", "https://si.test", 1),
            ("fonte_nova", "https://nova.test", 1),
            ("fonte_desativada", "https://off.test", 0),
        ],
    )
    conexao.commit()
    yield conexao
    conexao.close()


def id_fonte(con, code):
    linha = con.execute("SELECT id FROM source WHERE code = ?", (code,)).fetchone()
    assert linha is not None, f"fonte {code} nao semeada"
    return int(linha[0])


def criar_run(con, source_id, status, fetched=0, novo=0, mudado=0, inalterado=0, quarentena=0, erro=None):
    cursor = con.execute(
        "INSERT INTO source_run"
        ' (source_id, "trigger", adapter_version, status, started_at, finished_at,'
        " fetched_count, new_count, changed_count, unchanged_count, quarantined_count, error)"
        " VALUES (?, 'manual', 'teste-1', ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            int(source_id),
            status,
            AGORA,
            AGORA,
            int(fetched),
            int(novo),
            int(mudado),
            int(inalterado),
            int(quarentena),
            erro,
        ),
    )
    con.commit()
    return int(cursor.lastrowid)


def criar_incidente(
    con,
    source_id,
    kind="failed",
    severity="alta",
    message="falha simulada",
    opened_at=None,
    closed_at=None,
    last_notified_at=None,
    notify_count=0,
):
    cursor = con.execute(
        "INSERT INTO incident"
        " (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            int(source_id),
            kind,
            severity,
            opened_at or AGORA,
            closed_at,
            last_notified_at,
            int(notify_count),
            str(message),
        ),
    )
    con.commit()
    return int(cursor.lastrowid)


def buscar_incidente(con, incident_id):
    linha = con.execute("SELECT * FROM incident WHERE id = ?", (int(incident_id),)).fetchone()
    assert linha is not None, "incidente nao encontrado"
    return linha


def criar_sender(token="token-operador-teste", chat_id="chat-operador-teste", base_url="http://operador.test.invalido"):
    cls = exigir_classe("TelegramSender")
    try:
        return cls(token=token, chat_id=chat_id, base_url=base_url)
    except TypeError:
        pass
    try:
        return cls(token, chat_id, base_url)
    except TypeError:
        return cls(token, chat_id)


def instanciar_flexivel(cls, pool):
    try:
        sig = inspect.signature(cls)
    except Exception:
        try:
            return cls()
        except Exception:
            return Registro(dict(pool))
    kwargs = {}
    for nome, param in sig.parameters.items():
        if nome == "self":
            continue
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        chave = nome.lower()
        melhor = None
        melhor_tam = -1
        for frag, candidato in pool.items():
            if frag in chave and len(frag) > melhor_tam:
                melhor = candidato
                melhor_tam = len(frag)
        if melhor_tam < 0:
            if param.default is not param.empty:
                continue
            melhor = "..."
        kwargs[nome] = melhor
    try:
        return cls(**kwargs)
    except Exception:
        pass
    try:
        return cls(*kwargs.values())
    except Exception:
        return Registro(dict(pool))


def construir_incidente_obj(motivo="falha dura 403 handshake", code="brb"):
    pool = {
        "source": 1,
        "code": code,
        "codigo": code,
        "kind": "failed",
        "tipo": "failed",
        "sever": "alta",
        "grav": "alta",
        "message": motivo,
        "motivo": motivo,
        "reason": motivo,
        "texto": motivo,
        "detail": motivo,
        "opened": AGORA,
        "closed": None,
        "last": None,
        "notify": 0,
        "count": 0,
        "at": AGORA,
        "id": 7,
        "run": 1,
    }
    for nome_cls in ("IncidentRecord", "Incident", "Incidente"):
        cls = obter_classe(nome_cls)
        if cls is None:
            continue
        try:
            return instanciar_flexivel(cls, pool)
        except Exception:
            continue
    return Registro(
        {
            "id": 7,
            "source_id": 1,
            "source_code": code,
            "code": code,
            "kind": "failed",
            "severity": "alta",
            "message": motivo,
            "motivo": motivo,
            "reason": motivo,
            "texto": motivo,
            "opened_at": AGORA,
            "closed_at": None,
            "last_notified_at": None,
            "notify_count": 0,
        }
    )


def construir_fonte_obj(code="brb"):
    pool = {
        "source": code,
        "code": code,
        "codigo": code,
        "name": code,
        "url": "https://brb.test",
        "base": "https://brb.test",
        "version": "teste-1",
        "adapter": "teste-1",
        "enabled": 1,
        "id": 1,
        "run": 1,
    }
    for nome_cls in ("SourceRecord", "Source", "Fonte"):
        cls = obter_classe(nome_cls)
        if cls is None:
            continue
        try:
            return instanciar_flexivel(cls, pool)
        except Exception:
            continue
    return Registro(
        {
            "id": 1,
            "code": code,
            "source_code": code,
            "codigo": code,
            "base_url": "https://brb.test",
            "adapter_version": "teste-1",
            "enabled": 1,
        }
    )


def construir_stats_obj(code="brb"):
    pool = {
        "source": code,
        "code": code,
        "codigo": code,
        "status": "ok",
        "estado": "ok",
        "count": 3,
        "total": 3,
        "new": 1,
        "fetch": 3,
        "run": 1,
        "tempo": "1.2s",
        "duration": 1200,
        "duracao": 1200,
        "exec": "1.2s",
        "day": "2026-09-22",
        "id": 1,
    }
    for nome_cls in ("SourceStats", "Stats", "DailyStats", "ResumoFonte", "Estatistica"):
        cls = obter_classe(nome_cls)
        if cls is None:
            continue
        try:
            return instanciar_flexivel(cls, pool)
        except Exception:
            continue
    return Registro(
        {
            "source_code": code,
            "code": code,
            "status": "ok",
            "fetched": 3,
            "new": 1,
            "changed": 0,
            "counts": "3 fetched, 1 new",
            "tempo_execucao": "1.2s",
            "duration_ms": 1200,
            "day": "2026-09-22",
        }
    )


def test_modulos_de_producao_operador_existem():
    for nome in MODULOS:
        spec = importlib.util.find_spec(nome)
        assert spec is not None, f"O modulo de producao {nome} ainda nao existe."


def test_contrato_telegram_sender_exposto():
    cls = exigir_classe("TelegramSender")
    sig = inspect.signature(cls)
    nomes = list(sig.parameters.keys())
    assert nomes == ["token", "chat_id", "base_url"], (
        f"TelegramSender precisa de (token, chat_id, base_url), achado {nomes}"
    )
    padrao = sig.parameters["base_url"].default
    assert padrao == "https://api.telegram.org", (
        f"base_url padrao precisa ser https://api.telegram.org, achado {padrao!r}"
    )
    assert callable(getattr(cls, "send", None)), "TelegramSender precisa de metodo send"
    sig_send = inspect.signature(cls.send)
    assert "text" in sig_send.parameters, f"send precisa de parametro text, achado {list(sig_send.parameters)}"


def test_contrato_funcoes_operador_expostas():
    esperadas = {
        "notify_incident": 3,
        "notify_resolved": 3,
        "schedule_renotify": 2,
        "send_daily_report": 3,
        "format_incident_message": 2,
        "format_daily_report": 1,
    }
    for nome, aridade in esperadas.items():
        fn = obter_funcao(nome)
        assert callable(fn), f"{nome} precisa ser chamavel"
        try:
            params = list(inspect.signature(fn).parameters.values())
        except Exception:
            continue
        posicionais = [p for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        assert len(posicionais) == aridade, f"{nome} precisa de {aridade} params, achado {len(posicionais)}"


def test_incidente_aberto_dispara_send_com_source_e_motivo(con, monkeypatch):
    notify = obter_funcao("notify_incident")
    chamadas: list = []
    dublar_telegram_sucesso(monkeypatch, chamadas)
    fonte_id = id_fonte(con, "brb")
    motivo = "falha dura 403 no handshake X-XSRF-TOKEN"
    incidente_id = criar_incidente(con, fonte_id, kind="failed", severity="alta", message=motivo)
    sender = criar_sender()
    textos = espiar_send(monkeypatch, sender)
    notify(con, sender, incidente_id)
    assert textos, "notify_incident precisa chamar sender.send"
    combinado = " ".join(textos + [juntar_chamadas(chamadas)]).lower()
    assert "brb" in combinado, "mensagem precisa conter source_code brb"
    assert "403" in combinado or "falha dura" in combinado or "handshake" in combinado, (
        "mensagem precisa conter o motivo"
    )
    assert chamadas, "send precisa bater na Bot API (dublada)"


def test_falha_rede_nao_levanta_e_marca_notificacao(con, monkeypatch):
    notify = obter_funcao("notify_incident")
    chamadas: list = []
    dublar_telegram_falha(monkeypatch, chamadas)
    fonte_id = id_fonte(con, "brb")
    incidente_id = criar_incidente(
        con, fonte_id, message="falha simulada t10", notify_count=2, last_notified_at="2026-09-21T00:00:00Z"
    )
    sender = criar_sender()
    antes = buscar_incidente(con, incidente_id)
    resultado = sender.send("ping de teste t10")
    assert resultado is False, "send com falha de rede precisa devolver False sem levantar"
    notify(con, sender, incidente_id)
    depois = buscar_incidente(con, incidente_id)
    assert int(depois["notify_count"]) == int(antes["notify_count"]) + 1, (
        "notify precisa incrementar notify_count mesmo com falha"
    )
    assert depois["last_notified_at"], "last_notified_at precisa ser preenchido mesmo com falha"
    assert str(depois["last_notified_at"]) != str(antes["last_notified_at"])
    assert chamadas, "tentativa de envio precisa ser registrada mesmo com falha"


def test_relatorio_diario_inclui_todos_sources_ativos_mesmo_sem_novidade(con, monkeypatch):
    enviar = obter_funcao("send_daily_report")
    chamadas: list = []
    dublar_telegram_sucesso(monkeypatch, chamadas)
    brb = id_fonte(con, "brb")
    si = id_fonte(con, "sistema_industria")
    criar_run(con, brb, "ok", fetched=10, novo=2, inalterado=8)
    criar_run(con, si, "failed", fetched=5, erro="403 auth recusada")
    sender = criar_sender()
    textos = espiar_send(monkeypatch, sender)
    enviar(con, sender, date(2026, 9, 22))
    assert textos, "send_daily_report precisa chamar sender.send"
    combinado = " ".join(textos).lower()
    for codigo in ("brb", "sistema_industria", "fonte_nova"):
        assert codigo in combinado, f"relatorio precisa incluir source ativo {codigo} mesmo sem novidade"
    assert "fonte_desativada" not in combinado, "relatorio nao pode incluir fonte desativada"
    assert any(ch.isdigit() for ch in combinado), "relatorio precisa trazer counts"


def test_alertas_operador_nunca_alcancam_usuario(con, monkeypatch):
    notify = obter_funcao("notify_incident")
    resolver = obter_funcao("notify_resolved")
    relatorio = obter_funcao("send_daily_report")
    monkeypatch.setenv(ENV_TOKEN, "token-operador-xyz")
    monkeypatch.setenv(ENV_CHAT, "chat-operador-xyz")
    alvo_usuario = "@usuario_final_t10"
    cur = con.execute(
        "INSERT INTO alert_subscription (kind, channel, target, filter_expr, enabled, created_at)"
        " VALUES ('filtro', 'telegram', ?, 'brb', 1, ?)",
        (alvo_usuario, AGORA),
    )
    sub_id = int(cur.lastrowid)
    con.execute(
        "INSERT INTO alert_outbox (subscription_id, dedup_key, payload, status, created_at)"
        " VALUES (?, ?, ?, 'pending', ?)",
        (sub_id, "t10-dedup-1", '{"processo": "proc-1"}', AGORA),
    )
    con.commit()
    antes_qtd = con.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0]
    chamadas: list = []
    dublar_telegram_sucesso(monkeypatch, chamadas)
    sender = criar_sender(token="token-operador-xyz", chat_id="chat-operador-xyz")
    fonte_id = id_fonte(con, "brb")
    incidente_id = criar_incidente(con, fonte_id, message="falha para teste de canal")
    notify(con, sender, incidente_id)
    relatorio(con, sender, date(2026, 9, 22))
    con.execute("UPDATE incident SET closed_at = ? WHERE id = ?", (agora_iso(), incidente_id))
    con.commit()
    resolver(con, sender, incidente_id)
    depois_qtd = con.execute("SELECT COUNT(*) FROM alert_outbox").fetchone()[0]
    depois_status = con.execute("SELECT status FROM alert_outbox WHERE dedup_key = 't10-dedup-1'").fetchone()[0]
    assert depois_qtd == antes_qtd, "alerta de operador nao pode criar linha em outbox de usuario"
    assert depois_status == "pending", "outbox de usuario precisa continuar pending"
    assert chamadas, "envios de operador precisam existir via bot do operador"
    combinado = juntar_chamadas(chamadas)
    assert "chat-operador-xyz" in combinado, "envio precisa usar o chat do operador"
    assert alvo_usuario not in combinado, "alerta de operador jamais pode ir para usuario final"
    assert getattr(sender, "chat_id", None) == "chat-operador-xyz"
    assert getattr(sender, "chat_id", None) != alvo_usuario


def test_tres_falhas_duras_disparam_notify_com_dump_handshake(con, monkeypatch):
    notify = obter_funcao("notify_incident")
    chamadas: list = []
    dublar_telegram_sucesso(monkeypatch, chamadas)
    fonte_id = id_fonte(con, "sistema_industria")
    for _ in range(3):
        criar_run(con, fonte_id, "failed", erro="403 auth recusada")
    dump = "dump-handshake-t10 X-XSRF-TOKEN=abc/def+ghi Referer=https://si.test/ JSESSIONID=xyz"
    incidente_id = criar_incidente(con, fonte_id, kind="failed", severity="alta", message=f"3 falhas duras 403 {dump}")
    sender = criar_sender()
    textos = espiar_send(monkeypatch, sender)
    notify(con, sender, incidente_id)
    assert textos, "3 falhas duras precisam disparar notify"
    combinado = " ".join(textos + [juntar_chamadas(chamadas)]).lower()
    assert "sistema_industria" in combinado, "notify precisa identificar a fonte"
    assert "x-xsrf-token" in combinado, "texto precisa incluir o dump do ultimo handshake"
    assert "abc/def" in combinado or "dump-handshake-t10" in combinado


def test_renotify_backoff_6h_24h_diario(con):
    agendar = obter_funcao("schedule_renotify")
    agora = datetime.now(UTC).replace(microsecond=0)
    fonte_id = id_fonte(con, "brb")

    def criar_com_count(count):
        base = agora.isoformat().replace("+00:00", "Z")
        return criar_incidente(
            con, fonte_id, message="backoff t10", notify_count=count, last_notified_at=base, opened_at=base
        )

    id1 = criar_com_count(0)
    d1 = normalizar_segundos(agendar(con, id1))
    assert d1 is not None, "schedule_renotify precisa devolver intervalo para count 0"
    assert 5 * 3600 <= d1 <= 7 * 3600, f"primeiro renotify precisa ser 6h, achado {d1}"

    id2 = criar_com_count(1)
    d2 = normalizar_segundos(agendar(con, id2))
    assert d2 is not None, "schedule_renotify precisa devolver intervalo para count 1"
    assert 23 * 3600 <= d2 <= 25 * 3600, f"segundo renotify precisa ser 24h, achado {d2}"

    id3 = criar_com_count(5)
    d3 = normalizar_segundos(agendar(con, id3))
    assert d3 is not None, "schedule_renotify precisa devolver intervalo diario"
    assert 23 * 3600 <= d3 <= 25 * 3600, f"renotify diario precisa ser 24h, achado {d3}"


def test_fechamento_notifica_resolved(con, monkeypatch):
    avisar = obter_funcao("notify_resolved")
    chamadas: list = []
    dublar_telegram_sucesso(monkeypatch, chamadas)
    fonte_id = id_fonte(con, "brb")
    incidente_id = criar_incidente(con, fonte_id, message="falha resolvida t10")
    con.execute("UPDATE incident SET closed_at = ? WHERE id = ?", (agora_iso(), incidente_id))
    con.commit()
    sender = criar_sender()
    textos = espiar_send(monkeypatch, sender)
    avisar(con, sender, incidente_id)
    assert textos, "notify_resolved precisa chamar sender.send"
    combinado = " ".join(textos).lower()
    assert "resolved" in combinado, "fechamento precisa notificar 'resolved'"
    assert "brb" in combinado, "fechamento precisa identificar a fonte"


def test_format_mensagens_contem_campos():
    fmt_inc = obter_funcao("format_incident_message")
    fmt_rel = obter_funcao("format_daily_report")
    incidente = construir_incidente_obj(motivo="falha dura 403 handshake", code="brb")
    fonte = construir_fonte_obj(code="brb")
    texto = str(fmt_inc(incidente, fonte))
    baixo = texto.lower()
    assert "brb" in baixo, "mensagem de incidente precisa conter source_code"
    assert "403" in baixo or "falha dura" in baixo or "handshake" in baixo, "mensagem precisa conter o motivo"
    stats = [construir_stats_obj("brb"), construir_stats_obj("sistema_industria")]
    rel = str(fmt_rel(stats))
    baixo_rel = rel.lower()
    assert "brb" in baixo_rel and "sistema_industria" in baixo_rel, "relatorio precisa listar cada fonte"


def test_config_via_env_operador(monkeypatch):
    carregar_modulo(MODULO_OPERATOR)
    carregar_modulo(MODULO_TELEGRAM)
    monkeypatch.setenv(ENV_TOKEN, "token-env-teste")
    monkeypatch.setenv(ENV_CHAT, "chat-env-teste")
    for nome in MODULOS:
        spec = importlib.util.find_spec(nome)
        assert spec is not None and spec.origin, f"origem de {nome} nao encontrada"
        fonte = pathlib.Path(spec.origin).read_text(encoding="utf-8")
        assert ENV_TOKEN in fonte, f"{nome} precisa ler {ENV_TOKEN}"
        assert ENV_CHAT in fonte, f"{nome} precisa ler {ENV_CHAT}"
    for nome_fn in (
        "load_operator_config",
        "operator_config",
        "config_operador",
        "from_env",
        "carregar_config",
        "load_config",
    ):
        fn = None
        for nome_mod in MODULOS:
            try:
                mod = importlib.import_module(nome_mod)
            except Exception:
                continue
            if hasattr(mod, nome_fn):
                fn = getattr(mod, nome_fn)
                break
        if fn is None or not callable(fn):
            continue
        try:
            cfg = fn()
        except TypeError:
            try:
                cfg = fn(os.environ)
            except Exception:
                continue
        except Exception:
            continue
        par = str(cfg).lower()
        assert "token-env-teste" in par or "chat-env-teste" in par, f"{nome_fn} precisa refletir a env do operador"
        break
