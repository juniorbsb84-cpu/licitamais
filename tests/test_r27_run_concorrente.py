"""R27: dois runs da mesma fonte ao mesmo tempo (disparo agendado durante run manual,
medido 2026-09-24 runs 62/63) leem a mesma fila de hidratacao e repetem chamadas.
Fonte com run aberto e recente e pulada; run aberto velho (processo morto) nao trava."""

import sqlite3
from datetime import UTC, datetime, timedelta

from licitamais.runner import RunCounts, RunOutcome
from licitamais.scheduler import SchedulerConfig, run_scheduler
from licitamais.schema import init_schema


def preparar(tmp_path, monkeypatch, inicio_aberto):
    con = sqlite3.connect(tmp_path / "db.sqlite")
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) VALUES ('x', 'api_json', 'https://x.test', 'v1')"
    )
    con.execute(
        "INSERT INTO source_run (source_id, trigger, adapter_version, status, started_at) VALUES (1, 'cron', 'v1', 'failed', ?)",
        (inicio_aberto.strftime("%Y-%m-%dT%H:%M:%SZ"),),
    )
    con.commit()
    chamadas = []

    def executar(_con, source, _cfg):
        chamadas.append(source.code)
        return RunOutcome(2, source.id, source.code, "ok", RunCounts())

    monkeypatch.setattr("licitamais.runner.run_source", executar)
    return con, chamadas


def test_pula_fonte_com_run_em_andamento(tmp_path, monkeypatch):
    con, chamadas = preparar(tmp_path, monkeypatch, datetime.now(UTC) - timedelta(minutes=5))
    try:
        assert run_scheduler(con, SchedulerConfig(adapters={"x": object()}, trigger="manual")) == []
        assert chamadas == []
    finally:
        con.close()


def test_run_aberto_velho_nao_trava_a_fonte(tmp_path, monkeypatch):
    con, chamadas = preparar(tmp_path, monkeypatch, datetime.now(UTC) - timedelta(hours=3))
    try:
        assert len(run_scheduler(con, SchedulerConfig(adapters={"x": object()}, trigger="manual"))) == 1
        assert chamadas == ["x"]
    finally:
        con.close()
