"""Revisao 1 (C1): migracao 007 -- indices de preco + v_licitacao sem pseudo-processos."""

import sqlite3

from licitamais import current_version, init_schema


def _con():
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    init_schema(con)
    return con


def test_versao_7():
    con = _con()
    assert current_version(con) >= 7
    assert con.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] >= 7


def test_indices_preco_existem_e_supersedes_e_usado():
    con = _con()
    indices = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"idx_contract_supersedes", "idx_contract_process", "idx_award_supersedes", "idx_award_process"} <= indices
    plano = "\n".join(str(r[-1]) for r in con.execute("EXPLAIN QUERY PLAN SELECT 1 FROM v_preco_contrato"))
    assert "idx_contract_supersedes" in plano


def test_v_licitacao_exclui_sem_origem():
    con = _con()
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual)"
        " VALUES (1, 'f', 'api_json', 'https://f.test', 'v1')"
    )
    con.execute(
        'INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at)'
        " VALUES (1, 1, 'manual', 'v1', 'ok', '2026-09-24')"
    )
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, object, record_hash,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id) VALUES"
        " (1, 1, 'X:sem-origem:1', 'Parceria', 'h', 1, 1, 1),"
        " (2, 1, 'LIC-1', 'Limpeza', 'h', 1, 1, 1)"
    )
    ids = [r[0] for r in con.execute("SELECT id FROM v_licitacao ORDER BY id")]
    assert ids == [2]
