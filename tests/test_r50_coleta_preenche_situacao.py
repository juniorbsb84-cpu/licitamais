"""r50: processos gravados antes da 006 ficavam sem grupo de situacao e a view os
classificava pela data ('Licitacao suspensa' com abertura futura virava 'aberta'). A coleta preenche sozinha."""

import sqlite3

from licitamais.__main__ import main
from licitamais.runner import RunCounts, RunOutcome
from licitamais.schema import init_schema


def test_coleta_preenche_grupo_de_processo_antigo(tmp_path, monkeypatch):
    for variavel in ("LICITAMAIS_TELEGRAM_TOKEN", "LICITAMAIS_TELEGRAM_OPERADOR"):
        monkeypatch.delenv(variavel, raising=False)
    db = tmp_path / "licitamais.db"
    con = sqlite3.connect(db)
    init_schema(con)
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual) VALUES (1, 'brb', 'api_json', 'https://x', 'v1')"
    )
    con.execute(
        'INSERT INTO source_run (id, source_id, "trigger", adapter_version, status, started_at) VALUES (1, 1, "cron", "v1", "ok", "2026-09-01")'
    )
    con.execute(
        "INSERT INTO process (id, source_id, source_native_id, opening_at_source, phase_current_label, record_hash, attrs,"
        " first_seen_run_id, last_seen_run_id, last_changed_run_id)"
        " VALUES (1, 1, 'p1', '2099-01-01', 'Licitação suspensa', 'h', '{\"phase_label\": \"Licitação suspensa\"}', 1, 1, 1)"
    )
    con.commit()
    assert con.execute("SELECT situacao FROM v_licitacao").fetchone()[0] == "aberta"  # o defeito
    con.close()
    monkeypatch.setattr(
        "licitamais.runner.run_source",
        lambda con, source, cfg: RunOutcome(source.id, source.id, source.code, "ok", RunCounts()),
    )
    assert main(["--db", str(db)]) == 0
    with sqlite3.connect(db) as con:
        assert con.execute("SELECT situacao FROM v_licitacao").fetchone()[0] == "suspensa"
