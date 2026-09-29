from __future__ import annotations

import html
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from .telegram import TelegramSender

# Apenas tipos explicitamente urgentes podem furar a janela de silencio ou virar urgentes
URGENT_EVENT_KINDS = frozenset({"urgent", "retificacao", "abertura_proxima"})


@dataclass
class ProcessRecord:
    process_id: int
    opening_at_source: datetime | None
    bloco: str = ""


@dataclass
class OutboxRecord:
    id: int
    user_id: int
    process_id: int
    event_kind: str
    event_ref: str
    bloco: str = ""


@dataclass
class DigestPayload:
    user_id: int
    run_id: int
    top_processes: list[ProcessRecord]
    extra_count: int
    urgent_alerts: list[OutboxRecord]
    normal_alert_ids: list[int] | None = None


def _formatar_reais(centavos_ou_reais, eh_centavos: bool = True) -> str:
    try:
        val = float(centavos_ou_reais)
        if eh_centavos:
            val = val / 100.0
    except (TypeError, ValueError):
        return ""
    texto = f"{val:,.2f}"
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def _preco_historico(con: sqlite3.Connection, palavras: tuple[str, ...]) -> tuple[int, float | None]:
    """Calcula quantidade e mediana de valor em v_preco_contrato para palavras-chave."""
    try:
        check = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'view' AND name = 'v_preco_contrato'").fetchone()
        if not check:
            return 0, None
    except sqlite3.Error:
        return 0, None

    if not palavras:
        return 0, None

    filtros = []
    params = []
    for pal in palavras:
        p = str(pal).strip()
        if p:
            escaped = p.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            filtros.append("objeto LIKE ? ESCAPE '\\'")
            params.append(f"%{escaped}%")

    if not filtros:
        return 0, None

    where_clause = " OR ".join(filtros)
    sql = f"SELECT valor FROM v_preco_contrato WHERE valor IS NOT NULL AND ({where_clause}) ORDER BY valor ASC"
    try:
        rows = con.execute(sql, params).fetchall()
    except sqlite3.Error:
        return 0, None

    if not rows:
        return 0, None

    valores = [float(r[0]) for r in rows if r[0] is not None]
    n = len(valores)
    if n == 0:
        return 0, None

    if n % 2 == 1:
        mediana = valores[n // 2]
    else:
        mediana = (valores[n // 2 - 1] + valores[n // 2]) / 2.0

    return n, mediana


def _formatar_data_brt(dt_str: str | None) -> str:
    if not dt_str:
        return ""
    try:
        dt = datetime.fromisoformat(str(dt_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        dt_brt = dt.astimezone(ZoneInfo("America/Sao_Paulo"))
        return dt_brt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def _detalhe_processo(con: sqlite3.Connection, process_id: int, user_id: int | None = None) -> str:
    """Monta o bloco HTML detalhado de um processo para digest ou alerta urgente."""
    proc_cols = {row[1] for row in con.execute("PRAGMA table_info(process)").fetchall()}
    if not proc_cols:
        return f"Processo {process_id}"

    # Entidade / fonte
    fonte_code = ""
    has_source_table = (
        con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source'").fetchone() is not None
    )
    if has_source_table and "source_id" in proc_cols:
        row = con.execute(
            """
            SELECT s.code, p.number, p.object, p.opening_at_source, p.attrs
            FROM process p
            JOIN source s ON s.id = p.source_id
            WHERE p.id = ?
        """,
            (process_id,),
        ).fetchone()
    else:
        num_col = "number" if "number" in proc_cols else "NULL"
        obj_col = "object" if "object" in proc_cols else "NULL"
        ope_col = "opening_at_source" if "opening_at_source" in proc_cols else "NULL"
        att_col = "attrs" if "attrs" in proc_cols else "NULL"
        row = con.execute(
            f"SELECT '' as code, {num_col}, {obj_col}, {ope_col}, {att_col} FROM process WHERE id = ?", (process_id,)
        ).fetchone()

    if not row:
        return f"Processo {process_id}"

    fonte_code, number, obj, opening_str, attrs_json = row
    numero = str(number).strip() if number else ""
    if not numero:
        numero = f"Processo {process_id}"

    linhas: list[str] = []

    # 1. Cabecalho: Entidade e numero
    cabecalho_partes = []
    if fonte_code:
        cabecalho_partes.append(f"<b>{html.escape(str(fonte_code))}</b>")
    cabecalho_partes.append(f"Processo {html.escape(numero)}")
    linhas.append(" - ".join(cabecalho_partes))

    # 2. Objeto (ate 200 chars)
    if obj:
        obj_limpo = str(obj).strip()
        obj_truncado = obj_limpo[:200]
        linhas.append(f"Objeto: {html.escape(obj_truncado)}")

    # 3. Data de abertura em BRT dd/mm/aaaa hh:mm
    abertura_brt = _formatar_data_brt(opening_str)
    if abertura_brt:
        linhas.append(f"Abertura: {abertura_brt}")

    # 4. Valor estimado se houver (somando itens do processo)
    has_item_table = (
        con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'item'").fetchone() is not None
    )
    if has_item_table:
        try:
            val_row = con.execute(
                "SELECT SUM(total_price_estimated_cents) FROM item WHERE process_id = ? AND total_price_estimated_cents IS NOT NULL",
                (process_id,),
            ).fetchone()
            if val_row and val_row[0] is not None and val_row[0] > 0:
                valor_formatado = _formatar_reais(val_row[0], eh_centavos=True)
                linhas.append(f"Valor estimado: R$ {valor_formatado}")
        except sqlite3.Error:
            pass

    # 5. Link do edital se houver em attrs (ou attachment)
    edital_url = None
    if attrs_json:
        try:
            attrs = json.loads(attrs_json)
            if isinstance(attrs, dict):
                edital_url = attrs.get("edital_url") or attrs.get("link_edital") or attrs.get("url")
        except Exception:
            pass
    if not edital_url:
        has_att = (
            con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attachment'").fetchone()
            is not None
        )
        if has_att:
            try:
                att_row = con.execute(
                    "SELECT url_download FROM attachment WHERE process_id = ? AND kind = 'edital' LIMIT 1",
                    (process_id,),
                ).fetchone()
                if att_row and att_row[0]:
                    edital_url = att_row[0]
            except sqlite3.Error:
                pass
    if edital_url:
        linhas.append(f"Edital: {html.escape(str(edital_url))}")

    # 6. Linha de preco historico (mediana de v_preco_contrato para a palavra-chave)
    keywords: tuple[str, ...] = ()
    if user_id:
        has_sub = (
            con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'alert_subscription'").fetchone()
            is not None
        )
        if has_sub:
            sub_row = con.execute("SELECT filter_expr FROM alert_subscription WHERE id = ?", (user_id,)).fetchone()
            if sub_row and sub_row[0]:
                try:
                    fexpr = json.loads(sub_row[0])
                    if isinstance(fexpr, dict) and "keywords" in fexpr:
                        keywords = tuple(str(k).strip() for k in fexpr["keywords"] if str(k).strip())
                except Exception:
                    pass

    n_hist, mediana_hist = _preco_historico(con, keywords)
    if n_hist > 0 and mediana_hist is not None:
        mediana_fmt = _formatar_reais(mediana_hist, eh_centavos=False)
        contrato_plural = "contrato" if n_hist == 1 else "contratos"
        linhas.append(f"Preço histórico: mediana R$ {mediana_fmt} ({n_hist} {contrato_plural})")

    return "\n".join(linhas)


def flush_outbox(con: sqlite3.Connection, user_id: int, run_id: int) -> DigestPayload | None:
    outbox_cols = {row[1] for row in con.execute("PRAGMA table_info(alert_outbox)").fetchall()}
    proc_cols = {row[1] for row in con.execute("PRAGMA table_info(process)").fetchall()}
    has_opening = "opening_at_source" in proc_cols

    if "payload" in outbox_cols and "process_id" not in outbox_cols:
        rows = []
        for alert_id, payload in con.execute(
            "SELECT id, payload FROM alert_outbox WHERE status = 'pending' ORDER BY id"
        ):
            try:
                data = json.loads(payload)
            except Exception:
                continue
            if not isinstance(data, dict):
                continue
            if str(data.get("user_id")) != str(user_id):
                continue
            process_id = data.get("process_id")
            opening_val = None
            if has_opening:
                process = con.execute("SELECT opening_at_source FROM process WHERE id = ?", (process_id,)).fetchone()
                if process is None:
                    continue
                opening_val = process[0]
            else:
                p_exists = con.execute("SELECT 1 FROM process WHERE id = ?", (process_id,)).fetchone()
                if p_exists is None:
                    continue
            rows.append((alert_id, process_id, data.get("event_kind"), data.get("event_ref"), opening_val))
    else:
        if has_opening:
            rows = con.execute(
                """
                SELECT a.id, a.process_id, a.event_kind, a.event_ref, p.opening_at_source
                FROM alert_outbox a
                JOIN process p ON p.id = a.process_id
                WHERE a.user_id = ? AND a.status = 'pending'
            """,
                (user_id,),
            ).fetchall()
        else:
            rows = con.execute(
                """
                SELECT a.id, a.process_id, a.event_kind, a.event_ref, NULL
                FROM alert_outbox a
                JOIN process p ON p.id = a.process_id
                WHERE a.user_id = ? AND a.status = 'pending'
            """,
                (user_id,),
            ).fetchall()

    if not rows:
        return None

    now = datetime.now(UTC)
    urgent_alerts = []
    normal_alerts = []

    for row in rows:
        a_id, process_id, event_kind, event_ref, opening_str = row

        opening_at_source = None
        is_urgent = False
        if opening_str:
            try:
                opening_at_source = datetime.fromisoformat(str(opening_str).replace("Z", "+00:00"))
                if opening_at_source.tzinfo is None:
                    opening_at_source = opening_at_source.replace(tzinfo=UTC)
                opening_at_source = opening_at_source.astimezone(UTC)

                delta = opening_at_source - now
                if timedelta(hours=-48) <= delta <= timedelta(hours=48):
                    # Somente tipos explicitamente conhecidos/urgentes (new, changed) ou URGENT_EVENT_KINDS
                    # Tipos desconhecidos como 'teste_manual' nao sao urgentes e nao furam janela de silencio
                    if event_kind in URGENT_EVENT_KINDS or event_kind in ("new", "changed"):
                        is_urgent = True
            except ValueError:
                pass
        elif event_kind in URGENT_EVENT_KINDS:
            is_urgent = True

        try:
            bloco_item = _detalhe_processo(con, process_id, user_id)
        except Exception:
            bloco_item = f"Processo {process_id}"

        if is_urgent:
            urgent_alerts.append(OutboxRecord(a_id, user_id, process_id, event_kind, event_ref, bloco_item))
        else:
            normal_alerts.append((ProcessRecord(process_id, opening_at_source, bloco_item), a_id))

    if not urgent_alerts and not normal_alerts:
        return None

    normal_alerts.sort(
        key=lambda x: x[0].opening_at_source if x[0].opening_at_source else datetime.max.replace(tzinfo=UTC)
    )

    top_processes = [p for p, _ in normal_alerts[:10]]
    extra_count = max(0, len(normal_alerts) - 10)

    return DigestPayload(
        user_id=user_id,
        run_id=run_id,
        top_processes=top_processes,
        extra_count=extra_count,
        urgent_alerts=urgent_alerts,
        normal_alert_ids=[alert_id for _, alert_id in normal_alerts],
    )


def is_silent_now(now: datetime, tz_brazil: str = "America/Sao_Paulo") -> bool:
    brt_time = now.astimezone(ZoneInfo(tz_brazil))
    hour = brt_time.hour
    return hour >= 20 or hour < 8


def send_digest(sender: TelegramSender, digest: DigestPayload) -> bool:
    if not digest.top_processes:
        return True

    header = f"Licitações para usuário {digest.user_id}:\n\n"
    footer = f"\n\nE mais {digest.extra_count} processo(s)." if digest.extra_count else ""

    item_blocos = [p.bloco if p.bloco else f"Processo {p.process_id}" for p in digest.top_processes]

    mensagens: list[str] = []
    bloco_atual = header
    limite = 4096

    for idx, item in enumerate(item_blocos):
        separador = "\n\n---\n\n" if bloco_atual != header else ""
        candidato = bloco_atual + separador + item

        if idx == len(item_blocos) - 1 and footer:
            if len(candidato + footer) <= limite:
                bloco_atual = candidato
                continue
            else:
                if len(bloco_atual.strip()) > 0 and bloco_atual != header:
                    mensagens.append(bloco_atual.strip())
                bloco_atual = item
                continue

        if len(candidato) <= limite:
            bloco_atual = candidato
        else:
            if len(bloco_atual.strip()) > 0 and bloco_atual != header:
                mensagens.append(bloco_atual.strip())
            bloco_atual = item

    if footer:
        if len(bloco_atual + footer) <= limite:
            bloco_atual = bloco_atual + footer
            mensagens.append(bloco_atual.strip())
        else:
            if len(bloco_atual.strip()) > 0:
                mensagens.append(bloco_atual.strip())
            mensagens.append(footer.strip())
    else:
        if len(bloco_atual.strip()) > 0:
            mensagens.append(bloco_atual.strip())

    if not mensagens:
        mensagens = [header.strip()]

    sucesso = True
    for msg in mensagens:
        if not sender.send(msg):
            sucesso = False
    return sucesso


def send_urgent(sender: TelegramSender, alert: OutboxRecord, process: ProcessRecord) -> bool:
    bloco = getattr(alert, "bloco", "") or getattr(process, "bloco", "")
    if bloco and ("0" in bloco or "Processo" in bloco):
        texto = f"🚨 <b>Licitação Urgente</b> ({alert.event_kind})\n\n{bloco}"
        return sender.send(texto)
    return sender.send(f"Licitação urgente: processo {process.process_id} ({alert.event_kind}).")


def deliver_outbox(
    con: sqlite3.Connection, sender: TelegramSender, user_id: int, run_id: int, so_urgentes: bool = False
) -> int:
    """Envia pendências com conexão dedicada; falhas permanecem pending para retry."""
    digest = flush_outbox(con, user_id, run_id)
    if digest is None:
        return 0
    cols = {row[1] for row in con.execute("PRAGMA table_info(alert_outbox)").fetchall()}
    confirmados = 0

    def confirmar(ids: list[int]) -> None:
        nonlocal confirmados
        if not ids:
            return
        if "sent_at" in cols:
            con.executemany(
                "UPDATE alert_outbox SET status = 'sent', sent_at = ? WHERE id = ? AND status = 'pending'",
                [(datetime.now(UTC).isoformat(), alert_id) for alert_id in ids],
            )
        else:
            con.executemany(
                "UPDATE alert_outbox SET status = 'sent' WHERE id = ? AND status = 'pending'",
                [(alert_id,) for alert_id in ids],
            )
        con.commit()
        confirmados += len(ids)

    if digest.normal_alert_ids and not so_urgentes:
        try:
            enviado = send_digest(sender, digest)
        except Exception:
            enviado = False
        if enviado:
            confirmar(digest.normal_alert_ids)
    for alert in digest.urgent_alerts:
        try:
            enviado = send_urgent(sender, alert, ProcessRecord(alert.process_id, None, alert.bloco))
        except Exception:
            enviado = False
        if enviado:
            confirmar([alert.id])
    return confirmados
