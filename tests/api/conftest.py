from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from licitamais.api import create_app
from licitamais.contas import auth
from licitamais.schema import init_schema

SESSAO = "sessao-de-teste"


def semear(con: sqlite3.Connection) -> None:
    con.executescript("""
    INSERT INTO source (id, code, transport, base_url, adapter_version_atual) VALUES
      (1, 'brb', 'api_json', 'https://x', 'v1'), (2, 'senac', 'api_json', 'https://y', 'v1');
    INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at, finished_at) VALUES
      (1, 1, 'manual', 'v1', 'ok', '2026-09-24T10:00:00Z', '2026-09-24T10:05:00Z'),
      (2, 2, 'manual', 'v1', 'ok', '2026-09-24T10:00:00Z', '2026-09-24T10:06:00Z');
    INSERT INTO organization (id, cnpj, name_raw, name_norm, kind_hint) VALUES
      (1, '12345678000199', 'RIVERA MOVEIS LTDA', 'rivera moveis ltda', 'fornecedor'),
      (2, NULL, 'SENAC DF', 'senac df', 'comprador');
    """)
    linhas = [
        (
            1,
            1,
            "p1",
            None,
            "Manutenção preventiva de frota de ônibus",
            "Pregão eletrônico",
            "2099-10-06",
            "Edital Aberto",
            "aberta",
        ),
        (
            2,
            2,
            "p2",
            2,
            "Manutenção predial em fachadas",
            "Pregão eletrônico",
            "2099-09-01",
            "Em processo",
            "andamento",
        ),
        (3, 2, "p3", 2, "Limpeza hospitalar", "Concorrência", "2020-01-10", "Finalizada", "encerrada"),
        (4, 1, "p4", None, "Aquisição de pneus", "Pregão eletrônico", "2021-05-01", "Licitação revogada", "cancelada"),
    ]
    for pid, sid, nat, org, obj, mod, abertura, rotulo, grupo in linhas:
        con.execute(
            "INSERT INTO process (id, source_id, source_native_id, org_id, number, object, modality_raw,"
            " opening_at_source, phase_current_label, phase_current_group, record_hash,"
            " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'h', 1, 1, 1)",
            (pid, sid, nat, org, f"{pid:03d}/2026", obj, mod, abertura, rotulo, grupo),
        )
    con.execute(
        "INSERT INTO contract (id, source_id, source_native_id, process_id, number, supplier_org_id,"
        " supplier_name_raw, value_cents, signed_at_source, vigency_start, first_seen_run_id, last_seen_run_id,"
        " last_changed_run_id) VALUES (1, 2, 'p3:c1', 3, '450', 1, 'RIVERA MOVEIS LTDA', 5000000,"
        " '2020-02-02', '2020-02-02', 1, 1, 1)"
    )
    con.execute(
        "INSERT INTO phase_event (process_id, phase_code, phase_label, started_at, ended_at)"
        " VALUES (3, 'em processo', 'Em processo', '2020-01-01', '2020-03-01'),"
        " (3, 'finalizada', 'Finalizada', '2020-03-01', NULL)"
    )
    con.execute("INSERT INTO account (id, email, created_at) VALUES (1, 'cliente@exemplo.com', '2026-09-24')")
    con.execute(
        "INSERT INTO session (account_id, session_hash, expires_at, created_at)"
        " VALUES (1, ?, '2099-01-01', '2026-09-24')",
        (auth.hash_token(SESSAO),),
    )
    con.commit()


@pytest.fixture()
def db_path(tmp_path):
    caminho = str(tmp_path / "api.db")
    con = sqlite3.connect(caminho)
    init_schema(con)
    semear(con)
    con.close()
    return caminho


@pytest.fixture()
def anonimo(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture()
def cliente(db_path):
    c = TestClient(create_app(db_path))
    c.cookies.set("licitamais_session", SESSAO)
    c.headers["X-CSRF"] = auth.csrf(SESSAO)
    return c
