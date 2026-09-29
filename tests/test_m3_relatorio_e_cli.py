"""M3/M4: relatorio diario por fonte, assinatura pelo CLI e migracoes em banco existente."""

import sqlite3

from licitamais.__main__ import main
from licitamais.scheduler import relatorio_diario


def test_relatorio_lista_ultimo_run_de_cada_fonte(tmp_path):
    db = str(tmp_path / "x.db")
    assert main(["--db", db, "--init"]) == 0
    con = sqlite3.connect(db)
    con.execute(
        'INSERT INTO source_run (source_id, "trigger", adapter_version, status, started_at, finished_at,'
        " fetched_count, new_count, changed_count, quarantined_count)"
        " VALUES (1, 'cron', 'v1', 'partial', '2026-09-23T10:00:00', '2026-09-23T10:01:00', 60, 5, 1, 0)"
    )
    con.commit()
    texto = relatorio_diario(con)
    assert "brb" in texto and "partial" in texto and "novos 5" in texto
    assert "sistema_industria" in texto and "nunca rodou" in texto


def test_cli_cria_assinatura(tmp_path):
    db = str(tmp_path / "x.db")
    main(["--db", db, "--init"])
    assert main(["--db", db, "--assinar", "12345", "--palavras", "notebook,papel"]) == 0
    linha = (
        sqlite3.connect(db).execute("SELECT channel, target, filter_expr, enabled FROM alert_subscription").fetchone()
    )
    assert linha[0] == "telegram" and linha[1] == "12345" and "notebook" in linha[2] and linha[3] == 1
