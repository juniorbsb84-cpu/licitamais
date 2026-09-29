import json
import logging
import pathlib
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .runner import RunConfig, RunOutcome, SourceRecord


@dataclass
class SchedulerConfig:
    trigger: str = "cron"
    capture_only: bool = False
    keep_days: int = 7
    frequencies: dict[str, timedelta] = field(default_factory=dict)
    base_frequency: timedelta = field(default_factory=lambda: timedelta(minutes=30))
    db_path: str = ""
    backup_dir: str = ""
    external_dest: str = ""
    adapters: dict[str, Any] = field(default_factory=dict)
    adapter_map: dict[str, Any] = field(default_factory=dict)
    source_code: str | None = None
    sender: Any = None
    # fabrica chat_id -> sender para alertas de usuario; sem ela usa `sender`
    sender_usuario: Any = None


def relatorio_diario(con: sqlite3.Connection) -> str:
    """M3: ultimo run de cada fonte habilitada, em texto para o operador."""
    linhas = ["Relatorio LicitamAIs por fonte:"]
    for sid, code in con.execute("SELECT id, code FROM source WHERE enabled = 1 ORDER BY code").fetchall():
        run = con.execute(
            "SELECT status, started_at, finished_at, fetched_count, new_count, changed_count,"
            " quarantined_count, error FROM source_run WHERE source_id = ? ORDER BY id DESC LIMIT 1",
            (sid,),
        ).fetchone()
        if run is None:
            linhas.append(f"- {code}: nunca rodou")
            continue
        status, ini, fim, buscado, novo, mudado, quar, erro = run
        linhas.append(
            f"- {code}: {status} | inicio {ini} fim {fim} | respostas {buscado} novos {novo}"
            f" mudados {mudado} quarentena {quar}" + (f" | {str(erro)[:120]}" if erro else "")
        )
    return chr(10).join(linhas)


def entregar_alertas(con: sqlite3.Connection, sender, agora: datetime) -> int:
    from .alerts.digest import deliver_outbox, is_silent_now

    if sender is None:
        logging.warning("Scheduler sem sender, alertas nao serao enviados")
        return 0

    so_urgentes = is_silent_now(agora)

    cols = {row[1] for row in con.execute("PRAGMA table_info(alert_outbox)").fetchall()}
    usuarios: list[int] = []
    if "user_id" in cols:
        for row in con.execute("SELECT DISTINCT user_id FROM alert_outbox WHERE status = 'pending'").fetchall():
            if row[0] is None:
                continue
            try:
                usuarios.append(int(row[0]))
            except Exception:
                continue
    elif "payload" in cols:
        vistos: set[int] = set()
        for (carga,) in con.execute("SELECT payload FROM alert_outbox WHERE status = 'pending'").fetchall():
            try:
                dados = json.loads(carga)
            except Exception:
                continue
            uid = dados.get("user_id") if isinstance(dados, dict) else None
            if uid is None:
                continue
            try:
                uid_int = int(uid)
            except Exception:
                continue
            if uid_int not in vistos:
                vistos.add(uid_int)
                usuarios.append(uid_int)
    else:
        return 0

    total = 0
    for user_id in usuarios:
        destino = sender
        if isinstance(sender, type) or (callable(sender) and not hasattr(sender, "send")):
            # fabrica: cada assinante recebe no proprio chat (alert_subscription.target)
            alvo = con.execute("SELECT target FROM alert_subscription WHERE id = ?", (user_id,)).fetchone()
            if alvo is None or not alvo[0]:
                logging.warning("assinatura %s sem target; alerta fica pendente", user_id)
                continue
            destino = sender(str(alvo[0]))
        try:
            total += deliver_outbox(con, destino, user_id, 0, so_urgentes=so_urgentes)
        except Exception:
            logging.getLogger(__name__).exception("falha ao entregar alertas da assinatura %s", user_id)
            continue

    return total


def notificar_operador(con: sqlite3.Connection, sender, agora: datetime) -> int:
    """Envia incidentes abertos novos ou vencidos e grava apenas confirmacoes."""
    if sender is None:
        logging.warning("Scheduler sem sender do operador; incidentes nao serao enviados")
        return 0

    from .alerts.operator import format_incident_message, schedule_renotify

    linhas = con.execute(
        "SELECT i.*, s.code AS source_code FROM incident AS i "
        "LEFT JOIN source AS s ON s.id = i.source_id "
        "WHERE i.closed_at IS NULL ORDER BY i.id"
    ).fetchall()
    total = 0
    for incidente in linhas:
        try:
            ultima = incidente["last_notified_at"]
            if ultima and int(incidente["notify_count"] or 0) > 0:
                texto_data = str(ultima)
                if texto_data.endswith("Z"):
                    texto_data = texto_data[:-1] + "+00:00"
                ultima_dt = datetime.fromisoformat(texto_data)
                if ultima_dt.tzinfo is None:
                    ultima_dt = ultima_dt.replace(tzinfo=UTC)
                # A primeira renotificacao ocorre 6h apos o envio inicial;
                # as posteriores usam o intervalo diario.
                if int(incidente["notify_count"] or 0) == 1:
                    intervalo = 6 * 3600
                else:
                    intervalo = schedule_renotify(con, int(incidente["id"]))
                if intervalo is None or agora.astimezone(UTC) < ultima_dt.astimezone(UTC) + timedelta(
                    seconds=intervalo
                ):
                    continue

            texto = format_incident_message(
                incidente,
                {"source_code": incidente["source_code"] or "desconhecida"},
            )
            if not sender.send(texto):
                continue
            instante = agora.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            con.execute(
                "UPDATE incident SET notify_count = COALESCE(notify_count, 0) + 1, "
                "last_notified_at = ? WHERE id = ? AND closed_at IS NULL",
                (instante, int(incidente["id"])),
            )
            con.commit()
            total += 1
        except Exception:
            try:
                con.rollback()
            except Exception:
                pass
    return total


def schedule_next(
    source_id: int, status: str, base_frequency: timedelta, started_at: datetime | None = None
) -> datetime:
    agora = datetime.now(UTC)
    if status == "degraded":
        return agora + timedelta(hours=2)
    # Frequencia e intervalo entre inicios, nao espera adicional apos o fim do run.
    return (started_at or agora) + base_frequency


def run_scheduler(con: sqlite3.Connection, cfg: SchedulerConfig) -> list[RunOutcome]:
    import licitamais.runner as runner

    adapters = cfg.adapters or cfg.adapter_map
    if not adapters:
        from .adapters.brb import BRBAdapter
        from .adapters.caixa import BBAdapter, BBTSAdapter, CaixaAdapter
        from .adapters.iges import IgesAdapter
        from .adapters.senac import SenacAdapter
        from .adapters.sescoop import SescoopAdapter
        from .adapters.sestsenat import SestSenatAdapter
        from .adapters.sistema_industria import SistemaIndustriaAdapter

        adapters = {
            "brb": BRBAdapter(),
            "sistema_industria": SistemaIndustriaAdapter(),
            "senac": SenacAdapter(),
            "iges": IgesAdapter(),
            "sestsenat": SestSenatAdapter(),
            "sescoop": SescoopAdapter(),
            "caixa": CaixaAdapter(),
            "bb": BBAdapter(),
            "bbts": BBTSAdapter(),
        }
    run_cfg = RunConfig(adapters=adapters, trigger=cfg.trigger, capture_only=cfg.capture_only)
    resultados: list[RunOutcome] = []
    for linha in con.execute(
        "SELECT id, code, base_url, adapter_version_atual FROM source WHERE enabled = 1 ORDER BY id"
    ).fetchall():
        code = str(linha[1])
        if getattr(cfg, "source_code", None) and code != cfg.source_code:
            continue
        if cfg.trigger == "cron":
            proxima = con.execute(
                "SELECT value FROM sync_cursor WHERE source_id = ? AND cursor_key = 'next_run_at'",
                (int(linha[0]),),
            ).fetchone()
            if proxima and proxima[0]:
                try:
                    instante = datetime.fromisoformat(str(proxima[0]).replace("Z", "+00:00"))
                    if instante.tzinfo is None:
                        instante = instante.replace(tzinfo=UTC)
                    if instante.astimezone(UTC) > datetime.now(UTC):
                        continue
                except ValueError:
                    pass
        # run da mesma fonte ainda aberto (disparo agendado durante run manual):
        # os dois leriam a mesma fila. Aberto ha mais de 2h = processo morto.
        limite = (datetime.now(UTC) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        if con.execute(
            "SELECT 1 FROM source_run WHERE source_id = ? AND finished_at IS NULL AND started_at > ?",
            (int(linha[0]), limite),
        ).fetchone():
            logging.warning("fonte %s com run em andamento; pulando", code)
            continue
        if code not in adapters:
            from .probes import maybe_open_incident
            from .runner import RunCounts, RunStatus, close_source_run, open_source_run

            source_id_falta = int(linha[0])
            adapter_version_falta = str(linha[3] or "")
            trigger_falta = str(getattr(cfg, "trigger", "cron") or "cron")
            run_id_falta = open_source_run(con, source_id_falta, trigger_falta, adapter_version_falta)
            con.commit()
            msg_falta = f"sem adaptador para a fonte {code}"
            close_source_run(con, run_id_falta, RunStatus.FAILED.value, msg_falta)
            try:
                maybe_open_incident(con, source_id_falta, RunStatus.FAILED.value, ())
            except Exception:
                logging.getLogger(__name__).exception("falha ao abrir incidente da fonte %s", code)
            con.commit()
            resultados.append(
                RunOutcome(
                    run_id_falta,
                    source_id_falta,
                    code,
                    RunStatus.FAILED.value,
                    RunCounts(),
                    msg_falta,
                )
            )
            continue
        source = SourceRecord(int(linha[0]), code, str(linha[2]), str(linha[3]))
        resultados.append(runner.run_source(con, source, run_cfg))
    for outcome in list(resultados):
        try:
            sid = int(getattr(outcome, "source_id", 0) or 0)
            st = str(getattr(outcome, "status", "ok") or "ok")
            scode = str(getattr(outcome, "source_code", "") or "")
            base = cfg.base_frequency
            freqs = getattr(cfg, "frequencies", None) or {}
            try:
                if isinstance(freqs, dict) and scode and scode in freqs:
                    base = freqs[scode]
            except Exception:
                pass
            inicio = con.execute(
                "SELECT started_at FROM source_run WHERE id = ? AND source_id = ?",
                (int(getattr(outcome, "run_id", 0) or 0), sid),
            ).fetchone()
            iniciado_em = None
            if inicio and inicio[0]:
                iniciado_em = datetime.fromisoformat(str(inicio[0]).replace("Z", "+00:00"))
                if iniciado_em.tzinfo is None:
                    iniciado_em = iniciado_em.replace(tzinfo=UTC)
            proxima = schedule_next(sid, st, base, iniciado_em)
            con.execute(
                "INSERT INTO sync_cursor (source_id, cursor_key, value, updated_at) "
                "VALUES (?, 'next_run_at', ?, ?) "
                "ON CONFLICT(source_id, cursor_key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (sid, proxima.astimezone(UTC).isoformat(), datetime.now(UTC).isoformat()),
            )
            con.commit()
        except Exception:
            logging.getLogger(__name__).exception("falha ao agendar a proxima coleta")

    try:
        sender = getattr(cfg, "sender", None)
        agora = datetime.now(ZoneInfo("America/Sao_Paulo"))
        notificar_operador(con, sender, agora)
        entregar_alertas(con, getattr(cfg, "sender_usuario", None) or sender, agora)
    except Exception:
        logging.getLogger(__name__).exception("falha ao avisar operador ou entregar alertas")

    try:
        db_path = str(getattr(cfg, "db_path", "") or "")
        backup_dir = str(getattr(cfg, "backup_dir", "") or "")
        if db_path and backup_dir:
            from datetime import date

            from .backup import copy_external, rotate_backups, vacuum_backup, verificar_backup

            caminho_banco = pathlib.Path(db_path)
            diretorio_backup = pathlib.Path(backup_dir)
            if caminho_banco.is_file():
                diretorio_backup.mkdir(parents=True, exist_ok=True)
                dia = date.today()
                caminho_backup = diretorio_backup / f"backup-{dia.strftime('%Y%m%d')}.db"
                novo_backup_gerado = False
                if not caminho_backup.is_file():
                    caminho_backup = vacuum_backup(caminho_banco, diretorio_backup, dia)
                    novo_backup_gerado = True
                    keep = int(getattr(cfg, "keep_days", 7) or 7)
                    rotate_backups(diretorio_backup, keep_days=keep)

                if novo_backup_gerado:
                    if not verificar_backup(caminho_backup):
                        logging.error("integridade do backup falhou: %s", caminho_backup)
                        try:
                            sid_row = con.execute("SELECT MIN(id) FROM source").fetchone()
                            sid = int(sid_row[0]) if sid_row and sid_row[0] is not None else 1
                            agora_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                            msg_erro = f"integridade do backup corrompida: {caminho_backup.name}"
                            try:
                                con.execute(
                                    "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) "
                                    "VALUES (?, 'backup', 'alta', ?, NULL, NULL, 0, ?)",
                                    (sid, agora_iso, msg_erro),
                                )
                            except sqlite3.IntegrityError:
                                con.execute(
                                    "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) "
                                    "VALUES (?, 'failed', 'alta', ?, NULL, NULL, 0, ?)",
                                    (sid, agora_iso, msg_erro),
                                )
                            con.commit()
                        except Exception:
                            logging.getLogger(__name__).exception(
                                "falha ao registrar incidente de integridade do backup"
                            )

                destino_externo = str(getattr(cfg, "external_dest", "") or "")
                if destino_externo and caminho_backup.is_file():
                    destino_path = pathlib.Path(destino_externo)
                    arquivo_dest = destino_path / caminho_backup.name if destino_path.is_dir() else destino_path
                    deve_copiar = True
                    if arquivo_dest.is_file():
                        try:
                            if arquivo_dest.stat().st_size == caminho_backup.stat().st_size:
                                deve_copiar = False
                        except Exception:
                            deve_copiar = True
                    if deve_copiar:
                        sucesso_copia = copy_external(caminho_backup, destino_externo)
                        if sucesso_copia and destino_path.is_dir():
                            # nuvem (OneDrive): 1 backup/dia sem rotacao estoura a cota
                            rotate_backups(destino_path, keep_days=3)
                        if not sucesso_copia:
                            logging.error("falha ao copiar backup para destino externo: %s", destino_externo)
                            try:
                                sid_row = con.execute("SELECT MIN(id) FROM source").fetchone()
                                sid = int(sid_row[0]) if sid_row and sid_row[0] is not None else 1
                                agora_iso = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                                msg_erro = f"falha ao copiar backup para destino externo: {destino_externo}"
                                try:
                                    con.execute(
                                        "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) "
                                        "VALUES (?, 'backup', 'alta', ?, NULL, NULL, 0, ?)",
                                        (sid, agora_iso, msg_erro),
                                    )
                                except sqlite3.IntegrityError:
                                    con.execute(
                                        "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at, last_notified_at, notify_count, message) "
                                        "VALUES (?, 'failed', 'alta', ?, NULL, NULL, 0, ?)",
                                        (sid, agora_iso, msg_erro),
                                    )
                                con.commit()
                            except Exception:
                                logging.getLogger(__name__).exception(
                                    "falha ao registrar incidente de copia externa de backup"
                                )
    except Exception:
        logging.getLogger(__name__).exception("falha no backup")
    return resultados


def generate_task_scheduler_bat(cfg: SchedulerConfig, out_path: pathlib.Path) -> None:
    trigger = str(getattr(cfg, "trigger", "cron") or "cron")
    import sys

    raiz = pathlib.Path(__file__).resolve().parents[2]
    comando = f'"{sys.executable}" -m licitamais --trigger {trigger}'
    db_path = str(getattr(cfg, "db_path", "") or "")
    if db_path:
        comando += f' --db "{db_path}"'
    source_code = str(getattr(cfg, "source_code", "") or "")
    if source_code:
        comando += f' --source "{source_code}"'
    backup_dir = str(getattr(cfg, "backup_dir", "") or "")
    if backup_dir:
        comando += f' --backup-dir "{backup_dir}"'
    external_dest = str(getattr(cfg, "external_dest", "") or "")
    if external_dest:
        comando += f' --external-dest "{external_dest}"'
    # Task Scheduler roda em System32 com PATH minimo: fixa pasta, src e interpretador
    conteudo = f'@echo off\nrem Licitamais Scheduler\ncd /d "{raiz}"\nset PYTHONPATH={raiz / "src"}\n{comando}\n'
    out_path.write_text(conteudo, encoding="utf-8")
