"""Item 7: filtro invalido ignora a assinatura; vazio legitimo mantem match-all."""

import json
import logging
import sqlite3

import pytest

from licitamais.alerts.user import gerar_alertas_do_run, subscriptions_ativas
from licitamais.schema import init_schema

AGORA = "2026-09-23T12:00:00Z"


def nova_conexao():
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('fonte_teste', 'api_json', 'https://fonte.test', 'v1')"
    )
    con.commit()
    return con


def fonte_id(con):
    linha = con.execute("SELECT id FROM source WHERE code = 'fonte_teste'").fetchone()
    assert linha is not None
    return int(linha[0])


def abrir_run(con, sid):
    cur = con.execute(
        "INSERT INTO source_run (source_id, \"trigger\", adapter_version, status) VALUES (?, 'manual', 'v1', 'ok')",
        (sid,),
    )
    con.commit()
    return int(cur.lastrowid)


def criar_processo(con, sid, run_id):
    cur = con.execute(
        "INSERT INTO process (source_id, source_native_id, title, object,"
        " first_seen_run_id, last_changed_run_id)"
        " VALUES (?, 'edital-1', 'Pregao de material',"
        " 'aquisicao de notebook para a sede', ?, ?)",
        (sid, run_id, run_id),
    )
    con.commit()
    return int(cur.lastrowid)


def criar_assinatura(con, filtro, enabled=1):
    cur = con.execute(
        "INSERT INTO alert_subscription"
        " (kind, channel, target, filter_expr, enabled, created_at, attrs)"
        " VALUES ('filtro', 'telegram', 'usuario-teste', ?, ?, ?, ?)",
        (filtro, int(enabled), AGORA, filtro),
    )
    con.commit()
    return int(cur.lastrowid)


def outbox(con):
    return con.execute("SELECT dedup_key, payload, status FROM alert_outbox ORDER BY id").fetchall()


def avisos_da_assinatura(caplog, sub_id):
    return [
        r
        for r in caplog.records
        if r.name == "licitamais.alerts.user" and r.levelno >= logging.WARNING and str(sub_id) in r.getMessage()
    ]


INVS = [
    "{json quebrado",
    '{"keywords": [',
    "not json at all",
    '["notebook"]',
    '"notebook"',
    "42",
    "null",
    "[1, 2]",
]


@pytest.mark.parametrize("filtro", INVS)
def test_filtro_invalido_e_ignorado(caplog, filtro):
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        sub_id = criar_assinatura(con, filtro)
        run_id = abrir_run(con, sid)
        criar_processo(con, sid, run_id)
        with caplog.at_level(logging.WARNING, logger="licitamais.alerts.user"):
            subs = subscriptions_ativas(con)
        assert all(s.user_id != sub_id for s in subs)
        assert avisos_da_assinatura(caplog, sub_id)
        with caplog.at_level(logging.WARNING, logger="licitamais.alerts.user"):
            novos = gerar_alertas_do_run(con, sid, run_id)
        assert novos == 0
        assert outbox(con) == []
    finally:
        con.close()


def test_attrs_invalido_com_filter_expr_nulo(caplog):
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        cur = con.execute(
            "INSERT INTO alert_subscription"
            " (kind, channel, target, filter_expr, enabled, created_at, attrs)"
            " VALUES ('filtro', 'telegram', 'usuario-teste', NULL, 1, ?, ?)",
            (AGORA, "{attrs quebrado"),
        )
        con.commit()
        sub_id = int(cur.lastrowid)
        run_id = abrir_run(con, sid)
        criar_processo(con, sid, run_id)
        with caplog.at_level(logging.WARNING, logger="licitamais.alerts.user"):
            subs = subscriptions_ativas(con)
        assert all(s.user_id != sub_id for s in subs)
        assert avisos_da_assinatura(caplog, sub_id)
        assert gerar_alertas_do_run(con, sid, run_id) == 0
        assert outbox(con) == []
    finally:
        con.close()


VAZIOS = [None, "", "   ", "{}", '{"keywords": [], "modalities": [], "entities": []}']


@pytest.mark.parametrize("filtro", VAZIOS)
def test_filtro_ausente_ou_vazio_mantem_match_all(filtro):
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        sub_id = criar_assinatura(con, filtro)
        run_id = abrir_run(con, sid)
        criar_processo(con, sid, run_id)
        subs = subscriptions_ativas(con)
        casadas = [s for s in subs if s.user_id == sub_id]
        assert len(casadas) == 1
        assert casadas[0].keywords == ()
        assert casadas[0].modalities == ()
        assert casadas[0].entities == ()
        assert gerar_alertas_do_run(con, sid, run_id) == 1
        linhas = outbox(con)
        assert len(linhas) == 1
        assert linhas[0][2] == "pending"
        carga = json.loads(linhas[0][1])
        assert carga["event_kind"] == "new"
    finally:
        con.close()


def test_assinatura_invalida_nao_bloqueia_valida():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con, "{corrompido")
        sub_ok = criar_assinatura(con, json.dumps({"keywords": ["notebook"], "modalities": [], "entities": []}))
        run_id = abrir_run(con, sid)
        criar_processo(con, sid, run_id)
        assert gerar_alertas_do_run(con, sid, run_id) == 1
        linhas = outbox(con)
        assert len(linhas) == 1
        carga = json.loads(linhas[0][1])
        assert carga["user_id"] == sub_ok
    finally:
        con.close()
