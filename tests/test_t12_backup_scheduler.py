"""Fase vermelha do TDD para backup VACUUM + scheduler sequencial (T12)."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pathlib
import sqlite3
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from licitamais.schema import init_schema

MODULO_BACKUP = "licitamais.backup"
MODULO_SCHEDULER = "licitamais.scheduler"
MODULO_MAIN = "licitamais.__main__"
MODULOS = (MODULO_BACKUP, MODULO_SCHEDULER, MODULO_MAIN)

FUNCOES_CONTRATO = (
    "vacuum_backup",
    "rotate_backups",
    "copy_external",
    "schedule_next",
    "run_scheduler",
    "main",
    "generate_task_scheduler_bat",
)


class Registro(dict):
    """Duble flexivel para configs ainda inexistentes."""

    def __getattr__(self, nome):
        try:
            return self[nome]
        except KeyError as exc:
            raise AttributeError(nome) from exc

    def __setattr__(self, nome, valor):
        self[nome] = valor


def carregar_modulo(nome):
    """Falha pela ausencia do modulo, sem abortar a coleta."""
    spec = importlib.util.find_spec(nome)
    assert spec is not None, f"O modulo de producao {nome} ainda nao existe."
    return importlib.import_module(nome)


def obter_funcao(nome, modulos=MODULOS):
    for nome_modulo in modulos:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if hasattr(modulo, nome):
            return getattr(modulo, nome)
    assert False, f"A funcao de producao {nome} ainda nao existe em {modulos}."


def modulo_de(nome, modulos=MODULOS):
    for nome_modulo in modulos:
        spec = importlib.util.find_spec(nome_modulo)
        if spec is None:
            continue
        modulo = importlib.import_module(nome_modulo)
        if hasattr(modulo, nome):
            return modulo
    assert False, f"A funcao de producao {nome} ainda nao existe em {modulos}."


def criar_frequencia_base(modulo_scheduler):
    """Constroi frequencia normal de ~30min no tipo que o scheduler expuser."""
    from enum import Enum

    for nome_cls in ("Frequency", "Frequencia", "FrequenciaColeta"):
        cls = getattr(modulo_scheduler, nome_cls, None)
        if cls is None:
            continue
        if inspect.isclass(cls) and issubclass(cls, Enum):
            membros = list(cls)
            assert membros, "enum Frequency sem membros"
            for membro in membros:
                texto = str(getattr(membro, "value", membro)).lower()
                if "30" in texto or "half" in texto or "min" in texto:
                    return membro
            return membros[0]
        tentativas_kw = [
            {"minutes": 30},
            {"minutos": 30},
            {"interval_minutes": 30},
            {"seconds": 1800},
            {"interval_seconds": 1800},
            {"hours": 0.5},
            {"horas": 0.5},
            {"interval": timedelta(minutes=30)},
            {"delta": timedelta(minutes=30)},
            {"periodo": timedelta(minutes=30)},
        ]
        for kw in tentativas_kw:
            try:
                return cls(**kw)
            except Exception:
                continue
        for pos in (timedelta(minutes=30), 1800, "30m"):
            try:
                return cls(pos)
            except Exception:
                continue
        try:
            return cls()
        except Exception:
            continue
    return timedelta(minutes=30)


def montar_cfg_scheduler(modulo_scheduler, **extras):
    """Constroi SchedulerConfig real quando existir, senao duble compativel."""
    base = {
        "trigger": "cron",
        "capture_only": False,
        "captureOnly": False,
        "keep_days": 7,
        "frequencies": {},
        "frequencias": {},
        "base_frequency": timedelta(minutes=30),
        "db_path": "",
        "backup_dir": "",
        "external_dest": "",
    }
    base.update(extras)
    for nome_cls in ("SchedulerConfig", "SchedulerCfg", "ConfiguracaoScheduler"):
        cls = getattr(modulo_scheduler, nome_cls, None)
        if cls is None:
            continue
        try:
            sig = inspect.signature(cls)
        except Exception:
            continue
        kwargs = {}
        for nome_param in sig.parameters:
            if nome_param == "self":
                continue
            for chave, valor in base.items():
                if nome_param == chave:
                    kwargs[nome_param] = valor
                    break
        try:
            return cls(**kwargs)
        except Exception:
            continue
        break
    return Registro(base)


def semear_fonte(con, code, base_url="https://fonte.test"):
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
        " VALUES (?, 'api_json', ?, 'teste-1', 1)",
        (code, base_url),
    )


def texto_de_chamadas(chamadas):
    partes = []
    for args, kwargs in chamadas:
        for obj in list(args) + list(kwargs.values()):
            partes.append(str(obj))
            for attr in (
                "code",
                "source_code",
                "source",
                "source_id",
                "trigger",
                "capture_only",
                "captureOnly",
            ):
                try:
                    partes.append(str(getattr(obj, attr, "")))
                except Exception:
                    continue
            try:
                if isinstance(obj, dict):
                    for chave, valor in obj.items():
                        partes.append(str(chave))
                        partes.append(str(valor))
            except Exception:
                continue
        for chave in kwargs:
            partes.append(str(chave))
    return " ".join(partes).lower()


def tem_capture_only(chamadas):
    for args, kwargs in chamadas:
        candidatos = list(args) + list(kwargs.values())
        for obj in candidatos:
            for nome in ("capture_only", "captureOnly"):
                try:
                    if isinstance(obj, dict) and obj.get(nome) is True:
                        return True
                    if getattr(obj, nome, False) is True:
                        return True
                except Exception:
                    continue
        texto = texto_de_chamadas([(args, kwargs)])
        if "capture" in texto and ("true" in texto or "1" in texto):
            return True
    return False


@pytest.fixture()
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    semear_fonte(conexao, "brb", "https://brb.test")
    semear_fonte(conexao, "fonte_a", "https://a.test")
    semear_fonte(conexao, "fonte_b", "https://b.test")
    conexao.commit()
    yield conexao
    conexao.close()


def test_modulos_de_producao_backup_scheduler_existem():
    for nome in MODULOS:
        spec = importlib.util.find_spec(nome)
        assert spec is not None, f"O modulo de producao {nome} ainda nao existe."


def test_contrato_expoe_sete_funcoes():
    for nome in FUNCOES_CONTRATO:
        func = obter_funcao(nome)
        assert callable(func), f"{nome} deveria ser chamavel"


def test_vacuum_backup_cria_arquivo_valido(tmp_path):
    vacuum_backup = obter_funcao("vacuum_backup")
    db_path = tmp_path / "orig.db"
    con = sqlite3.connect(str(db_path))
    try:
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        semear_fonte(con, "brb", "https://brb.test")
        con.commit()
        total_antes = con.execute("SELECT COUNT(*) FROM source").fetchone()[0]
    finally:
        con.close()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    dia = date(2026, 9, 22)
    resultado = vacuum_backup(db_path, backup_dir, dia)
    esperado = backup_dir / "backup-20260922.db"
    assert pathlib.Path(resultado) == esperado
    assert esperado.is_file(), "VACUUM INTO deveria criar backup-YYYYMMDD.db"
    assert esperado.stat().st_size > 0
    with open(str(esperado), "rb") as arquivo:
        cabecalho = arquivo.read(16)
    assert cabecalho.startswith(b"SQLite format 3")
    con2 = sqlite3.connect(str(esperado))
    try:
        total = con2.execute("SELECT COUNT(*) FROM source").fetchone()[0]
        assert total == total_antes
    finally:
        con2.close()


def test_rotate_backups_mantem_7_dias(tmp_path):
    rotate_backups = obter_funcao("rotate_backups")
    try:
        params = inspect.signature(rotate_backups).parameters
        if "keep_days" in params:
            assert params["keep_days"].default == 7
    except AssertionError:
        raise
    except Exception:
        pass
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    base = date(2026, 9, 22)
    for i in range(10):
        dia = base - timedelta(days=i)
        (backup_dir / f"backup-{dia.strftime('%Y%m%d')}.db").write_bytes(b"bytes-falsos")
    ret = rotate_backups(backup_dir, keep_days=7)
    assert isinstance(ret, int)
    restantes = sorted(p.name for p in backup_dir.glob("backup-*.db"))
    assert len(restantes) == 7, f"rotacao deveria manter 7, manteve {len(restantes)}"
    esperados = sorted(f"backup-{(base - timedelta(days=i)).strftime('%Y%m%d')}.db" for i in range(7))
    assert restantes == esperados
    assert ret in (3, 7), "retorno deveria ser apagados (3) ou mantidos (7)"


def test_schedule_next_degraded_2h_vs_normal():
    schedule_next = obter_funcao("schedule_next")
    modulo = modulo_de("schedule_next")
    base = criar_frequencia_base(modulo)
    agora = datetime.now(UTC)
    prox_ok = schedule_next(1, "ok", base)
    prox_deg = schedule_next(1, "degraded", base)
    assert isinstance(prox_ok, datetime)
    assert isinstance(prox_deg, datetime)
    if prox_ok.tzinfo is None:
        prox_ok = prox_ok.replace(tzinfo=UTC)
    if prox_deg.tzinfo is None:
        prox_deg = prox_deg.replace(tzinfo=UTC)
    delta_ok = (prox_ok - agora).total_seconds()
    delta_deg = (prox_deg - agora).total_seconds()
    assert delta_ok > 0, "proximo agendamento normal deveria ser no futuro"
    assert 110 * 60 <= delta_deg <= 130 * 60, f"degraded deveria ser ~2h, foi {delta_deg / 60:.1f}min"
    assert delta_deg > delta_ok, "degraded deveria adiar em relacao ao normal"
    assert (delta_deg - delta_ok) >= 30 * 60


def test_schedule_next_normal_ancora_no_inicio_do_run():
    schedule_next = obter_funcao("schedule_next")
    inicio = datetime.now(UTC) - timedelta(minutes=20)
    assert schedule_next(1, "ok", timedelta(minutes=30), inicio) == inicio + timedelta(minutes=30)


def test_cli_source_trigger_capture_only_dispara_run_source(monkeypatch, tmp_path):
    main = obter_funcao("main")
    import licitamais.runner as modulo_runner

    mod_scheduler = None
    spec_sched = importlib.util.find_spec(MODULO_SCHEDULER)
    if spec_sched is not None:
        mod_scheduler = importlib.import_module(MODULO_SCHEDULER)
    mod_main = None
    spec_main = importlib.util.find_spec(MODULO_MAIN)
    if spec_main is not None:
        mod_main = importlib.import_module(MODULO_MAIN)
    chamadas_run = []
    chamadas_sched = []

    def falso_run_source(*args, **kwargs):
        chamadas_run.append((args, kwargs))
        return Registro(run_id=1, status="ok", source_code="brb")

    def falso_run_scheduler(*args, **kwargs):
        chamadas_sched.append((args, kwargs))
        # T22: run_scheduler nunca devolve None (None fazia o CLI sair 0 sem rodar nada)
        return [Registro(run_id=1, status="ok", source_code="brb")]

    monkeypatch.setattr(modulo_runner, "run_source", falso_run_source)
    for mod in (mod_scheduler, mod_main):
        if mod is None:
            continue
        if hasattr(mod, "run_source"):
            monkeypatch.setattr(mod, "run_source", falso_run_source)
        if hasattr(mod, "run_scheduler"):
            monkeypatch.setattr(mod, "run_scheduler", falso_run_scheduler)
        if hasattr(mod, "run_all"):
            monkeypatch.setattr(mod, "run_all", lambda *a, **k: [falso_run_source(*a, **k)])
    banco = tmp_path / "licitamais.db"
    con_seed = sqlite3.connect(str(banco))
    try:
        con_seed.execute("PRAGMA foreign_keys = ON")
        init_schema(con_seed)
        semear_fonte(con_seed, "brb", "https://brb.test")
        semear_fonte(con_seed, "outra_fonte", "https://outra.test")
        con_seed.commit()
    finally:
        con_seed.close()
    for variavel in ("LICITAMAIS_DB", "LICITAMAIS_DB_PATH", "DB_PATH"):
        monkeypatch.setenv(variavel, str(banco))
    monkeypatch.chdir(tmp_path)
    ret = main(["--source=brb", "--trigger=manual", "--capture-only"])
    assert ret == 0
    assert chamadas_run or chamadas_sched, "CLI deveria disparar run_source/run_scheduler"
    texto_run = texto_de_chamadas(chamadas_run)
    texto_sched = texto_de_chamadas(chamadas_sched)
    texto = texto_run + " " + texto_sched
    assert "brb" in texto, f"CLI deveria filtrar source=brb, viu: {texto}"
    assert "manual" in texto, f"CLI deveria repassar trigger=manual, viu: {texto}"
    assert tem_capture_only(chamadas_run + chamadas_sched), f"CLI deveria repassar capture-only, viu: {texto}"
    assert "outra_fonte" not in texto_run, "CLI com --source=brb nao deveria rodar outra fonte"


def test_scheduler_sequencial_a_depois_b_sem_paralelismo(monkeypatch):
    run_scheduler = obter_funcao("run_scheduler")
    modulo = modulo_de("run_scheduler")
    import licitamais.runner as modulo_runner

    conexao = sqlite3.connect(":memory:")
    conexao.row_factory = sqlite3.Row
    conexao.execute("PRAGMA foreign_keys = ON")
    init_schema(conexao)
    semear_fonte(conexao, "fonte_a", "https://a.test")
    semear_fonte(conexao, "fonte_b", "https://b.test")
    conexao.commit()
    eventos = []
    estado = {"em_execucao": False, "sobrepos": False}

    def extrair_codigo(args, kwargs):
        for obj in list(args) + list(kwargs.values()):
            for attr in ("code", "source_code"):
                try:
                    valor = getattr(obj, attr, None)
                    if isinstance(valor, str) and valor:
                        return valor
                except Exception:
                    continue
            try:
                if isinstance(obj, dict):
                    for chave in ("code", "source_code", "source"):
                        if isinstance(obj.get(chave), str):
                            return str(obj[chave])
            except Exception:
                continue
            if isinstance(obj, str) and obj.startswith("fonte_"):
                return obj
        texto = texto_de_chamadas([(args, kwargs)])
        if "fonte_a" in texto:
            return "fonte_a"
        if "fonte_b" in texto:
            return "fonte_b"
        return "desconhecida"

    def falso_run_source(*args, **kwargs):
        codigo = extrair_codigo(args, kwargs)
        if estado["em_execucao"]:
            estado["sobrepos"] = True
        estado["em_execucao"] = True
        eventos.append(f"inicio:{codigo}")
        time.sleep(0.01)
        eventos.append(f"fim:{codigo}")
        estado["em_execucao"] = False
        return Registro(run_id=1, status="ok", source_code=codigo)

    monkeypatch.setattr(modulo_runner, "run_source", falso_run_source)
    if hasattr(modulo, "run_source"):
        monkeypatch.setattr(modulo, "run_source", falso_run_source)
    cfg = montar_cfg_scheduler(
        modulo,
        trigger="cron",
        adapters={"fonte_a": object(), "fonte_b": object()},
        adapter_map={"fonte_a": object(), "fonte_b": object()},
    )
    try:
        run_scheduler(conexao, cfg)
    finally:
        conexao.close()
    assert eventos == [
        "inicio:fonte_a",
        "fim:fonte_a",
        "inicio:fonte_b",
        "fim:fonte_b",
    ], f"scheduler deveria rodar A depois B em sequencia, viu: {eventos}"
    assert not estado["sobrepos"], "fontes nao deveriam rodar em paralelo"


def test_copy_external_copia_local_sem_rede(tmp_path):
    copy_external = obter_funcao("copy_external")
    origem = tmp_path / "backup-20260922.db"
    origem.write_bytes(b"bytes-validos-de-banco")
    destino_dir = tmp_path / "externo"
    destino_dir.mkdir()
    destino = str(destino_dir / "copia.db")
    assert copy_external(origem, destino) is True
    assert pathlib.Path(destino).is_file()
    assert pathlib.Path(destino).read_bytes() == b"bytes-validos-de-banco"
    assert copy_external(origem, "") is False


def test_generate_bat_windows_sem_dependencia_externa(tmp_path):
    gerar = obter_funcao("generate_task_scheduler_bat")
    modulo = modulo_de("generate_task_scheduler_bat")
    cfg = montar_cfg_scheduler(modulo, trigger="cron")
    saida = tmp_path / "licitamais_diario.bat"
    gerar(cfg, saida)
    assert saida.is_file(), ".bat do Task Scheduler deveria ser gerado"
    conteudo = saida.read_text(encoding="utf-8")
    assert len(conteudo.strip()) > 0
    baixo = conteudo.lower()
    assert "python" in baixo
    assert "licitamais" in baixo or "scheduler" in baixo or "schtasks" in baixo or "trigger" in baixo
    sched_path = pathlib.Path(str(getattr(modulo, "__file__", "")))
    if sched_path.is_file():
        fonte = sched_path.read_text(encoding="utf-8").lower()
        assert "win32com" not in fonte
        assert "pywin32" not in fonte
        assert "celery" not in fonte
