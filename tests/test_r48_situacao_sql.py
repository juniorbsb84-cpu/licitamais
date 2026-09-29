"""r48: situacao calculada em SQL (v_licitacao) concorda com o Python; FTS acompanha o processo."""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from licitamais.loader import backfill_situacao_orgao
from licitamais.schema import init_schema
from licitamais.situacao import grupo_do_rotulo, situacao

HOJE = date.today()
FUTURO = (HOJE + timedelta(days=10)).isoformat()
PASSADO = (HOJE - timedelta(days=10)).isoformat()

CASOS = [
    ("Edital Aberto", None),
    ("Edital Aberto", PASSADO),
    ("Edital Aberto", FUTURO),
    ("Em processo", FUTURO),
    ("Em processo", PASSADO),
    ("Licitação homologada", FUTURO),
    ("Processo Licitatório Cancelado", None),
    ("Licitação suspensa", None),
    ("TERMOS ADITIVOS", FUTURO),
    ("TERMOS ADITIVOS", PASSADO),
    (None, None),
    (None, FUTURO),
]


@pytest.fixture()
def con():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    init_schema(c)
    c.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual)"
        " VALUES (1, 'f', 'api_json', 'https://f.test', 'v1')"
    )
    c.execute(
        'INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at)'
        " VALUES (1, 1, 'manual', 'v1', 'ok', '2026-09-24')"
    )
    return c


def _processo(con, pid, rotulo, abertura, objeto="servico"):
    attrs = f'{{"phase_label": "{rotulo}"}}' if rotulo else "{}"
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, object, opening_at_source, record_hash, attrs,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id) VALUES (?, 1, ?, ?, ?, 'h', ?, 1, 1, 1)",
        (pid, f"p{pid}", objeto, abertura, attrs),
    )


def test_grupo_do_rotulo():
    assert grupo_do_rotulo("Edital Encerrado") == "encerrada"
    assert grupo_do_rotulo("Licitação revogada") == "cancelada"
    assert grupo_do_rotulo("TERMOS ADITIVOS") is None
    assert grupo_do_rotulo(None) is None


def test_view_concorda_com_python(con):
    for pid, (rotulo, abertura) in enumerate(CASOS, 1):
        _processo(con, pid, rotulo, abertura)
    backfill_situacao_orgao(con, 1)
    for pid, (rotulo, abertura) in enumerate(CASOS, 1):
        sql = con.execute("SELECT situacao FROM v_licitacao WHERE id = ?", (pid,)).fetchone()[0]
        assert sql == situacao(rotulo, abertura)["grupo"], (rotulo, abertura)


def test_fts_acompanha_insert_update_delete(con):
    _processo(con, 1, None, None, "Manutenção de veículos pesados")
    busca = "SELECT rowid FROM process_fts WHERE process_fts MATCH ?"
    assert [r[0] for r in con.execute(busca, ('"manutencao"',))] == [1]  # sem acento acha
    con.execute("UPDATE process SET object = 'Limpeza predial' WHERE id = 1")
    assert con.execute(busca, ('"veiculos"',)).fetchall() == []
    assert [r[0] for r in con.execute(busca, ('"limp"*',))] == [1]
    con.execute("DELETE FROM process WHERE id = 1")
    assert con.execute(busca, ('"limpeza"',)).fetchall() == []
