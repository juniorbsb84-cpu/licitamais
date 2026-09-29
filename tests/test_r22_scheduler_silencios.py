import sqlite3
from datetime import UTC, datetime, timedelta

from licitamais.__main__ import main
from licitamais.alerts.digest import flush_outbox
from licitamais.alerts.user import enqueue_alert
from licitamais.runner import RunCounts, RunOutcome
from licitamais.scheduler import SchedulerConfig, generate_task_scheduler_bat, run_scheduler
from licitamais.schema import init_schema


def banco(path):
    con = sqlite3.connect(path)
    init_schema(con)
    return con


def test_degraded_grava_proximo_agendamento_e_cron_respeita_intervalo(tmp_path, monkeypatch):
    con = banco(tmp_path / "db.sqlite")
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) VALUES ('x', 'api_json', 'https://x.test', 'v1')"
    )
    con.commit()
    chamadas = []

    def executar(_con, source, _cfg):
        chamadas.append(source.code)
        return RunOutcome(1, source.id, source.code, "degraded", RunCounts())

    monkeypatch.setattr("licitamais.runner.run_source", executar)
    try:
        cfg = SchedulerConfig(adapters={"x": object()}, trigger="cron")
        assert len(run_scheduler(con, cfg)) == 1
        valor = con.execute(
            "SELECT value FROM sync_cursor WHERE source_id = 1 AND cursor_key = 'next_run_at'"
        ).fetchone()
        assert valor is not None
        delta = datetime.fromisoformat(valor[0].replace("Z", "+00:00")) - datetime.now(UTC)
        assert timedelta(minutes=119) <= delta <= timedelta(hours=2)
        assert run_scheduler(con, cfg) == []
        assert chamadas == ["x"]
    finally:
        con.close()


def test_sem_fonte_habilitada_lista_vazia_e_cli_erro(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    con = banco(path)
    try:
        assert run_scheduler(con, SchedulerConfig()) == []
    finally:
        con.close()
    assert main(["--db", str(path)]) == 1
    assert "nenhuma fonte" in capsys.readouterr().err.lower()


def test_cli_source_inexistente_retorna_um(tmp_path, capsys):
    path = tmp_path / "db.sqlite"
    con = banco(path)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) VALUES ('x', 'api_json', 'https://x.test', 'v1')"
    )
    con.commit()
    con.close()
    assert main(["--db", str(path), "--source", "inexistente"]) == 1
    assert "nenhuma fonte" in capsys.readouterr().err.lower()


def test_bat_aspas_em_db_e_source_com_espacos(tmp_path):
    cfg = SchedulerConfig(db_path=str(tmp_path / "meu banco.sqlite"), source_code="fonte teste")
    path = tmp_path / "tarefa.bat"
    generate_task_scheduler_bat(cfg, path)
    texto = path.read_text(encoding="utf-8")
    assert f'--db "{cfg.db_path}"' in texto
    assert '--source "fonte teste"' in texto


def test_abertura_z_urgente_com_agora_brt(tmp_path, monkeypatch):
    import licitamais.alerts.digest as digest

    class Relogio(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat("2026-09-23T12:00:00-03:00").astimezone(tz)

    con = banco(tmp_path / "db.sqlite")
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) VALUES ('x', 'api_json', 'https://x.test', 'v1')"
    )
    con.execute(
        "INSERT INTO process (source_id, source_native_id, title, object, opening_at_source) VALUES (1, 'p', 'P', 'O', '2026-09-24T10:00:00Z')"
    )
    enqueue_alert(con, 1, 1, "new", "r")
    con.commit()
    monkeypatch.setattr(digest, "datetime", Relogio)
    try:
        result = flush_outbox(con, 1, 1)
        assert [a.process_id for a in result.urgent_alerts] == [1]
    finally:
        con.close()
