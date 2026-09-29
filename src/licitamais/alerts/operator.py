"""Alertas ao operador via Telegram: incidentes, renotify e relatorio diario.

Alertas de saude de fonte vao exclusivamente para o chat do operador,
configurado via TELEGRAM_OPERATOR_BOT_TOKEN e TELEGRAM_OPERATOR_CHAT_ID.
Nunca escrevem na outbox de usuario.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from .telegram import TelegramSender

logger = logging.getLogger(__name__)


__all__ = [
    "IncidentRecord",
    "SourceRecord",
    "SourceStats",
    "TelegramSender",
    "format_daily_report",
    "format_incident_message",
    "load_operator_config",
    "notify_incident",
    "notify_resolved",
    "schedule_renotify",
    "send_daily_report",
]

INTERVALO_PRIMEIRO_RENOTIFY = 6 * 3600
INTERVALO_RENOTIFY_DIARIO = 24 * 3600


@dataclass
class IncidentRecord:
    id: int = 0
    source_id: int = 0
    source_code: str = ""
    code: str = ""
    kind: str = "failed"
    severity: str = "alta"
    message: str = ""
    opened_at: str = ""
    closed_at: str | None = None
    last_notified_at: str | None = None
    notify_count: int = 0


@dataclass
class SourceRecord:
    id: int = 0
    code: str = ""
    source_code: str = ""
    base_url: str = ""
    adapter_version: str = ""
    enabled: int = 1


@dataclass
class SourceStats:
    source_code: str = ""
    code: str = ""
    status: str = "sem runs"
    fetched: int = 0
    new: int = 0
    changed: int = 0
    tempo_execucao: str = "0.0s"
    duration_ms: int = 0
    day: str = ""


def load_operator_config():
    """Le token e chat do operador das variaveis de ambiente."""
    return {
        "token": os.environ.get("TELEGRAM_OPERATOR_BOT_TOKEN", ""),
        "chat_id": os.environ.get("TELEGRAM_OPERATOR_CHAT_ID", ""),
    }


def _agora_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _linhas_campos(obj) -> dict:
    dados: dict = {}
    try:
        chaves = list(obj.keys())
    except Exception:
        chaves = []
    for chave in chaves:
        try:
            dados[str(chave).lower()] = obj[chave]
        except Exception:
            continue
    try:
        nomes = dir(obj)
    except Exception:
        nomes = []
    for nome in nomes:
        if nome.startswith("_"):
            continue
        baixo = str(nome).lower()
        if baixo in dados:
            continue
        try:
            valor = getattr(obj, nome)
        except Exception:
            continue
        if callable(valor):
            continue
        dados[baixo] = valor
    return dados


def _pegar(obj, *candidatos):
    if obj is None:
        return None
    dados = _linhas_campos(obj)
    for cand in candidatos:
        chave = str(cand).lower()
        if chave in dados and dados[chave] not in (None, ""):
            return dados[chave]
    for cand in candidatos:
        chave = str(cand).lower()
        for nome, valor in dados.items():
            if chave in nome and valor not in (None, ""):
                return valor
    return None


def _numero(valor) -> int:
    try:
        if valor is None or isinstance(valor, bool):
            return 0
        if isinstance(valor, (int, float)):
            return int(valor)
        return int(float(str(valor).strip()))
    except Exception:
        logger.warning("falha ao converter valor para numero: %r", valor)
        return 0


def _parse_data(texto):
    if texto is None:
        return None
    s = str(texto).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        logger.warning("falha ao parsear data no operador: %r", texto)
        return None


def _duracao_segundos(inicio, fim) -> float:
    a = _parse_data(inicio)
    b = _parse_data(fim)
    if a is None or b is None:
        return 0.0
    try:
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return 0.0


def _carregar_incidente(con: sqlite3.Connection, incident_id: int):
    linha = con.execute("SELECT * FROM incident WHERE id = ?", (int(incident_id),)).fetchone()
    if linha is None:
        raise ValueError(f"incidente {incident_id} nao encontrado")
    try:
        fonte = con.execute("SELECT * FROM source WHERE id = ?", (int(linha["source_id"]),)).fetchone()
    except Exception:
        fonte = None
    return linha, fonte


def _marcar_notificado(con: sqlite3.Connection, incident_id: int, notify_atual) -> None:
    try:
        total = int(notify_atual or 0)
    except Exception:
        total = 0
    con.execute(
        "UPDATE incident SET notify_count = ?, last_notified_at = ? WHERE id = ?",
        (total + 1, _agora_iso(), int(incident_id)),
    )
    con.commit()


def format_incident_message(incident: IncidentRecord, source: SourceRecord) -> str:
    codigo = _pegar(source, "source_code", "code", "codigo", "source", "name", "fonte")
    tipo = _pegar(incident, "kind", "tipo") or "failed"
    severidade = _pegar(incident, "severity", "severidade", "gravidade") or "alta"
    motivo = _pegar(incident, "message", "motivo", "reason", "texto", "detail", "mensagem", "error", "erro") or ""
    aberto = _pegar(incident, "opened_at", "opened", "aberto_em", "created_at") or ""
    ident = _pegar(incident, "id")
    codigo_txt = str(codigo) if codigo not in (None, "") else "desconhecida"
    sufixo = f" (id {ident})" if ident not in (None, "") else ""
    return f"incidente {tipo} [severidade {severidade}] fonte {codigo_txt}{sufixo}: {motivo} (aberto em {aberto})"


def format_daily_report(stats: Sequence[SourceStats]) -> str:
    itens = list(stats or [])
    linhas = [f"relatorio diario do operador: {len(itens)} fonte(s)"]
    for item in itens:
        codigo = _pegar(item, "source_code", "code", "codigo", "source", "name", "fonte")
        estado = _pegar(item, "status", "estado", "state", "situacao")
        buscados = _numero(_pegar(item, "fetched", "fetch", "total", "count", "coletados"))
        novos = _numero(_pegar(item, "new", "novo", "novos"))
        mudados = _numero(_pegar(item, "changed", "mudado", "mudados", "alterados"))
        tempo = _pegar(item, "tempo_execucao", "duration_ms", "duration", "duracao", "tempo", "exec")
        codigo_txt = str(codigo) if codigo not in (None, "") else "desconhecida"
        estado_txt = str(estado) if estado not in (None, "") else "sem dados"
        tempo_txt = str(tempo) if tempo not in (None, "") else "0.0s"
        linhas.append(
            f"- {codigo_txt}: status {estado_txt}, counts fetched={buscados} "
            f"novos={novos} mudados={mudados}, tempo de execucao {tempo_txt}"
        )
    return "\n".join(linhas)


def notify_incident(con: sqlite3.Connection, sender: TelegramSender, incident_id: int) -> bool:
    linha, fonte = _carregar_incidente(con, incident_id)
    texto = format_incident_message(linha, fonte)
    try:
        ok = sender.send(texto)
    except Exception:
        logger.exception("falha ao enviar notificacao de incidente %s ao operador", incident_id)
        _marcar_notificado(con, incident_id, linha["notify_count"])
        return False
    _marcar_notificado(con, incident_id, linha["notify_count"])
    return bool(ok)


def notify_resolved(con: sqlite3.Connection, sender: TelegramSender, incident_id: int) -> bool:
    linha, fonte = _carregar_incidente(con, incident_id)
    texto = "resolved: " + format_incident_message(linha, fonte)
    try:
        fechado = linha["closed_at"]
    except Exception:
        fechado = None
    if fechado:
        texto = f"{texto} (encerrado em {fechado})"
    try:
        ok = sender.send(texto)
    except Exception:
        logger.exception("falha ao enviar notificacao de resolucao de incidente %s ao operador", incident_id)
        _marcar_notificado(con, incident_id, linha["notify_count"])
        return False
    _marcar_notificado(con, incident_id, linha["notify_count"])
    return bool(ok)


def schedule_renotify(con: sqlite3.Connection, incident_id: int) -> int | None:
    linha = con.execute("SELECT notify_count FROM incident WHERE id = ?", (int(incident_id),)).fetchone()
    if linha is None:
        return None
    try:
        total = int(linha["notify_count"] or 0)
    except Exception:
        total = 0
    if total <= 0:
        return INTERVALO_PRIMEIRO_RENOTIFY
    return INTERVALO_RENOTIFY_DIARIO


def send_daily_report(con: sqlite3.Connection, sender: TelegramSender, day: date) -> bool:
    if isinstance(day, datetime):
        dia = day.date().isoformat()
    elif isinstance(day, date):
        dia = day.isoformat()
    else:
        dia = str(day).strip()[:10]
    fontes = con.execute("SELECT id, code FROM source WHERE enabled = 1 ORDER BY id").fetchall()
    stats: list = []
    for fonte in fontes:
        fonte_id = int(fonte["id"])
        codigo = str(fonte["code"])
        runs = con.execute(
            "SELECT status, fetched_count, new_count, changed_count,"
            " unchanged_count, quarantined_count, started_at, finished_at"
            " FROM source_run WHERE source_id = ?"
            " AND (substr(started_at, 1, 10) = ? OR substr(finished_at, 1, 10) = ?)"
            " ORDER BY id",
            (fonte_id, dia, dia),
        ).fetchall()
        if runs:
            estado = str(runs[-1]["status"] or "desconhecido")
            buscados = sum(_numero(linha["fetched_count"]) for linha in runs)
            novos = sum(_numero(linha["new_count"]) for linha in runs)
            mudados = sum(_numero(linha["changed_count"]) for linha in runs)
            total_seg = sum(_duracao_segundos(linha["started_at"], linha["finished_at"]) for linha in runs)
        else:
            estado = "sem runs no dia"
            buscados = 0
            novos = 0
            mudados = 0
            total_seg = 0.0
        stats.append(
            {
                "source_code": codigo,
                "code": codigo,
                "status": estado,
                "fetched": buscados,
                "new": novos,
                "changed": mudados,
                "tempo_execucao": f"{total_seg:.1f}s",
                "duration_ms": int(total_seg * 1000),
                "day": dia,
            }
        )
    texto = format_daily_report(stats)
    try:
        ok = sender.send(texto)
    except Exception:
        logger.exception("falha ao enviar relatorio diario ao operador para o dia %s", dia)
        return False
    return bool(ok)
