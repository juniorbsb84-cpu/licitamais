"""R25: agendamento real no Windows.

Defeitos que estes testes reproduzem:
- o .bat chamava `python -m licitamais` sem cd, sem PYTHONPATH=src e sem
  interpretador absoluto: no Task Scheduler (cwd System32, PATH mínimo) falha;
- a CLI nunca preenchia `backup_dir`, então o backup da T12 nunca rodava;
- o backup (VACUUM INTO do banco inteiro) rodaria a cada disparo do scheduler,
  não uma vez por dia.
"""

import pathlib
import sqlite3
import sys
from datetime import date

import licitamais.backup as backup
from licitamais.__main__ import main
from licitamais.scheduler import SchedulerConfig, generate_task_scheduler_bat, run_scheduler
from licitamais.schema import init_schema

RAIZ = pathlib.Path(__file__).resolve().parents[1]


def banco(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    init_schema(con)
    con.commit()
    return con


def test_bat_roda_fora_do_diretorio_do_projeto(tmp_path):
    cfg = SchedulerConfig(trigger="cron", db_path=str(tmp_path / "x.db"), backup_dir=str(tmp_path / "bk"))
    saida = tmp_path / "t.bat"
    generate_task_scheduler_bat(cfg, saida)
    texto = saida.read_text(encoding="utf-8")
    assert f'cd /d "{RAIZ}"' in texto
    assert f"PYTHONPATH={RAIZ / 'src'}" in texto
    assert f'"{sys.executable}" -m licitamais' in texto
    assert f'--backup-dir "{cfg.backup_dir}"' in texto


def test_bat_leva_destino_externo(tmp_path):
    cfg = SchedulerConfig(trigger="cron", db_path=str(tmp_path / "x.db"), external_dest=str(tmp_path / "nuvem"))
    saida = tmp_path / "t.bat"
    generate_task_scheduler_bat(cfg, saida)
    assert f'--external-dest "{cfg.external_dest}"' in saida.read_text(encoding="utf-8")


def test_cli_backup_dir_gera_backup(tmp_path):
    db = tmp_path / "db.sqlite"
    banco(db).close()
    bk = tmp_path / "bk"
    main(["--db", str(db), "--trigger", "cron", "--backup-dir", str(bk)])
    assert list(bk.glob("backup-*.db")), "CLI com --backup-dir deveria gerar backup"


def test_backup_uma_vez_por_dia(tmp_path, monkeypatch):
    db = tmp_path / "db.sqlite"
    con = banco(db)
    bk = tmp_path / "bk"
    chamadas = []
    original = backup.vacuum_backup

    def contar(*a, **k):
        chamadas.append(1)
        return original(*a, **k)

    monkeypatch.setattr(backup, "vacuum_backup", contar)
    cfg = SchedulerConfig(trigger="cron", db_path=str(db), backup_dir=str(bk))
    run_scheduler(con, cfg)
    run_scheduler(con, cfg)
    con.close()
    assert len(chamadas) == 1
    assert (bk / f"backup-{date.today():%Y%m%d}.db").is_file()
