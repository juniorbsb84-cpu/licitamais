import logging
import pathlib
import sqlite3
from datetime import date

from licitamais.__main__ import main
from licitamais.backup import verificar_backup
from licitamais.scheduler import SchedulerConfig, run_scheduler
from licitamais.schema import init_schema


def _criar_banco(path: pathlib.Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    con.execute(
        "INSERT OR IGNORE INTO source (id, code, transport, base_url, adapter_version_atual, enabled) "
        "VALUES (1, 'brb', 'api_json', 'https://example.com', '1.0.0', 1)"
    )
    con.commit()
    return con


def test_verificar_backup_valido_e_invalido(tmp_path):
    banco_valido = tmp_path / "valido.db"
    con = sqlite3.connect(str(banco_valido))
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, nome TEXT)")
    con.execute("INSERT INTO t VALUES (1, 'teste')")
    con.commit()
    con.close()

    assert verificar_backup(banco_valido) is True

    banco_inexistente = tmp_path / "inexistente.db"
    assert verificar_backup(banco_inexistente) is False

    banco_corrompido = tmp_path / "corrompido.db"
    banco_corrompido.write_bytes(b"SQLite format 3\x00" + b"\xff" * 1000)
    assert verificar_backup(banco_corrompido) is False


def test_cli_external_dest_arg_and_env(tmp_path, monkeypatch):
    banco = tmp_path / "cli_test.db"
    con = _criar_banco(banco)
    con.close()

    capturas = {}

    def falso_run_scheduler(con, cfg):
        capturas["external_dest"] = cfg.external_dest
        return [object()]

    monkeypatch.setattr("licitamais.__main__.scheduler.run_scheduler", falso_run_scheduler)
    monkeypatch.setattr("licitamais.__main__._codigo_saida", lambda res: 0)

    # 1. Via argumento CLI
    main(["--db", str(banco), "--external-dest", "/caminho/externo/cli"])
    assert capturas.get("external_dest") == "/caminho/externo/cli"

    # 2. Via variavel de ambiente
    monkeypatch.setenv("LICITAMAIS_BACKUP_EXTERNO", "/caminho/externo/env")
    main(["--db", str(banco)])
    assert capturas.get("external_dest") == "/caminho/externo/env"


def test_copia_externa_tentada_mesmo_com_backup_local_existente(tmp_path):
    banco = tmp_path / "banco.db"
    con = _criar_banco(banco)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dia = date.today()
    backup_local = backup_dir / f"backup-{dia.strftime('%Y%m%d')}.db"

    # Cria backup local existente previamente
    backup_con = sqlite3.connect(str(backup_local))
    backup_con.execute("CREATE TABLE t (id INTEGER)")
    backup_con.commit()
    backup_con.close()

    destino_externo = tmp_path / "externo"
    destino_externo.mkdir()

    cfg = SchedulerConfig(
        db_path=str(banco),
        backup_dir=str(backup_dir),
        external_dest=str(destino_externo),
        adapters={"brb": object()},
    )

    try:
        run_scheduler(con, cfg)
    finally:
        con.close()

    arquivo_externo = destino_externo / backup_local.name
    assert arquivo_externo.is_file(), "Backup externo deveria ter sido gerado mesmo com backup local existente"
    assert arquivo_externo.stat().st_size == backup_local.stat().st_size


def test_copia_externa_pula_se_mesmo_tamanho(tmp_path, monkeypatch):
    banco = tmp_path / "banco.db"
    con = _criar_banco(banco)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dia = date.today()
    backup_local = backup_dir / f"backup-{dia.strftime('%Y%m%d')}.db"
    backup_local.write_bytes(b"conteudo-de-backup-exato")

    destino_externo = tmp_path / "externo"
    destino_externo.mkdir()
    arquivo_externo = destino_externo / backup_local.name
    arquivo_externo.write_bytes(b"conteudo-de-backup-exato")

    chamadas_copy = []
    import licitamais.backup

    orig_copy = licitamais.backup.copy_external

    def spy_copy_external(src, dst):
        chamadas_copy.append((src, dst))
        return orig_copy(src, dst)

    monkeypatch.setattr("licitamais.backup.copy_external", spy_copy_external)

    cfg = SchedulerConfig(
        db_path=str(banco),
        backup_dir=str(backup_dir),
        external_dest=str(destino_externo),
        adapters={"brb": object()},
    )

    try:
        run_scheduler(con, cfg)
    finally:
        con.close()

    assert len(chamadas_copy) == 0, "Nao deveria chamar copy_external se o destino ja tem o arquivo com mesmo tamanho"


def test_copia_externa_falha_gera_log_e_incidente(tmp_path, caplog):
    banco = tmp_path / "banco.db"
    con = _criar_banco(banco)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dia = date.today()
    backup_local = backup_dir / f"backup-{dia.strftime('%Y%m%d')}.db"
    backup_local.write_bytes(b"conteudo-valido")

    # Destino inexistente/invalido que forca copy_external a retornar False
    destino_invalido = tmp_path / "pasta_inexistente" / "sub" / "destino_invalido.db"

    cfg = SchedulerConfig(
        db_path=str(banco),
        backup_dir=str(backup_dir),
        external_dest=str(destino_invalido),
        adapters={"brb": object()},
    )

    # Forcar retorno False
    def falso_copy_external(src, dst):
        return False

    with caplog.at_level(logging.ERROR):
        from unittest.mock import patch

        with patch("licitamais.backup.copy_external", side_effect=falso_copy_external):
            try:
                run_scheduler(con, cfg)
            finally:
                pass

    # Verifica log de erro
    assert any("falha ao copiar backup para destino externo" in r.message for r in caplog.records)

    # Verifica incidente registrado
    linhas = con.execute("SELECT * FROM incident WHERE message LIKE '%falha ao copiar backup%'").fetchall()
    assert len(linhas) >= 1
    incidente = linhas[0]
    assert incidente["kind"] in ("backup", "failed")
    assert incidente["severity"] == "alta"
    con.close()


def test_integridade_falha_gera_log_e_incidente(tmp_path, caplog):
    banco = tmp_path / "banco.db"
    con = _criar_banco(banco)

    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()

    cfg = SchedulerConfig(
        db_path=str(banco),
        backup_dir=str(backup_dir),
        adapters={"brb": object()},
    )

    from unittest.mock import patch

    with caplog.at_level(logging.ERROR):
        # Simula que verificar_backup retorna False para o novo backup gerado
        with patch("licitamais.backup.verificar_backup", return_value=False):
            try:
                run_scheduler(con, cfg)
            finally:
                pass

    assert any("integridade do backup falhou" in r.message for r in caplog.records)

    linhas = con.execute("SELECT * FROM incident WHERE message LIKE '%integridade do backup%'").fetchall()
    assert len(linhas) >= 1
    incidente = linhas[0]
    assert incidente["kind"] in ("backup", "failed")
    assert incidente["severity"] == "alta"
    con.close()


def test_destino_externo_guarda_so_3_backups(tmp_path):
    """OneDrive: um backup por dia sem rotacao enche a cota da nuvem."""
    import sqlite3
    from datetime import date

    from licitamais.scheduler import SchedulerConfig, run_scheduler
    from licitamais.schema import init_schema

    db = tmp_path / "db.sqlite"
    con = sqlite3.connect(db)
    init_schema(con)
    con.commit()
    externo = tmp_path / "onedrive"
    externo.mkdir()
    for dia in ("20260901", "20260902", "20260903", "20260904"):
        (externo / f"backup-{dia}.db").write_bytes(b"x")
    run_scheduler(
        con,
        SchedulerConfig(trigger="cron", db_path=str(db), backup_dir=str(tmp_path / "bk"), external_dest=str(externo)),
    )
    con.close()
    nomes = sorted(p.name for p in externo.glob("backup-*.db"))
    assert len(nomes) == 3
    assert f"backup-{date.today():%Y%m%d}.db" in nomes
