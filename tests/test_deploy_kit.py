import pathlib

DEPLOY_DIR = pathlib.Path(__file__).resolve().parent.parent / "deploy"


def test_deploy_folder_and_files_exist():
    assert DEPLOY_DIR.is_dir()
    expected_files = [
        "licitamais-coleta.service",
        "licitamais-coleta.timer",
        "licitamais-web.service",
        "Caddyfile",
        "backup.sh",
        ".env.example",
        "README.md",
    ]
    for filename in expected_files:
        filepath = DEPLOY_DIR / filename
        assert filepath.is_file(), f"Arquivo obrigatorio {filename} nao encontrado em {DEPLOY_DIR}"
        assert filepath.stat().st_size > 0, f"Arquivo {filename} esta vazio"


def test_systemd_coleta_service():
    content = (DEPLOY_DIR / "licitamais-coleta.service").read_text(encoding="utf-8")
    assert "Type=oneshot" in content
    assert "python -m licitamais" in content
    assert "--trigger cron" in content
    assert "${LICITAMAIS_DB}" in content
    assert "${LICITAMAIS_BACKUP_DIR}" in content


def test_systemd_coleta_timer():
    content = (DEPLOY_DIR / "licitamais-coleta.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 12:00:00" in content
    assert "licitamais-coleta.service" in content
    assert "Persistent=true" in content


def test_systemd_web_service():
    content = (DEPLOY_DIR / "licitamais-web.service").read_text(encoding="utf-8")
    assert "Type=simple" in content
    assert "python -m licitamais.api" in content
    assert "--porta 8090" in content
    assert "${LICITAMAIS_DB}" in content
    assert "Restart=always" in content


def test_caddyfile():
    content = (DEPLOY_DIR / "Caddyfile").read_text(encoding="utf-8")
    assert "reverse_proxy" in content
    assert "8090" in content


def test_backup_script():
    content = (DEPLOY_DIR / "backup.sh").read_text(encoding="utf-8")
    assert "#!/usr/bin/env bash" in content
    assert "sqlite3" in content
    assert ".backup" in content
    assert "rclone" in content
    assert "KEEP_DAYS" in content or "7" in content


def test_env_example():
    content = (DEPLOY_DIR / ".env.example").read_text(encoding="utf-8")
    assert "LICITAMAIS_DB" in content
    assert "LICITAMAIS_BACKUP_DIR" in content
    assert "LICITAMAIS_TELEGRAM_TOKEN" in content
    assert "LICITAMAIS_TELEGRAM_OPERADOR" in content


def test_readme():
    content = (DEPLOY_DIR / "README.md").read_text(encoding="utf-8")
    assert "Ubuntu 24.04" in content
    assert "2 GB RAM" in content
    assert "600" in content
    assert "systemctl" in content
