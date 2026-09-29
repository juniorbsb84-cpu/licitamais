import json
import logging
import sqlite3
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertSubscription:
    user_id: int
    keywords: tuple[str, ...]
    modalities: tuple[str, ...]
    entities: tuple[str, ...]


def enqueue_alert(con: sqlite3.Connection, user_id: int, process_id: int, event_kind: str, event_ref: str) -> int:
    dedup_key = f"{user_id}:{process_id}:{event_kind}:{event_ref}"
    payload = json.dumps(
        {"user_id": user_id, "process_id": process_id, "event_kind": event_kind, "event_ref": event_ref},
        ensure_ascii=False,
    )
    outbox_cols = {r[1] for r in con.execute("PRAGMA table_info(alert_outbox)").fetchall()}
    if "dedup_key" in outbox_cols:
        insert_cols: list[str] = []
        params: list[object] = []
        if "subscription_id" in outbox_cols:
            insert_cols.append("subscription_id")
            params.append(user_id)
        if "user_id" in outbox_cols:
            insert_cols.append("user_id")
            params.append(user_id)
        if "process_id" in outbox_cols:
            insert_cols.append("process_id")
            params.append(process_id)
        if "event_kind" in outbox_cols:
            insert_cols.append("event_kind")
            params.append(event_kind)
        if "event_ref" in outbox_cols:
            insert_cols.append("event_ref")
            params.append(event_ref)
        insert_cols.append("dedup_key")
        params.append(dedup_key)
        if "payload" in outbox_cols:
            insert_cols.append("payload")
            params.append(payload)
        if "status" in outbox_cols:
            insert_cols.append("status")
            params.append("pending")
        placeholders = ", ".join(["?"] * len(insert_cols))
        cols_sql = ", ".join(insert_cols)
        con.execute(f"INSERT OR IGNORE INTO alert_outbox ({cols_sql}) VALUES ({placeholders})", params)
        row = con.execute("SELECT id FROM alert_outbox WHERE dedup_key = ?", (dedup_key,)).fetchone()
        if row is None or row[0] is None:
            raise RuntimeError(f"enqueue_alert failed to persist dedup_key={dedup_key!r}")
        return int(row[0])
    cur = con.execute(
        """
        INSERT OR IGNORE INTO alert_outbox (user_id, process_id, event_kind, event_ref, status)
        VALUES (?, ?, ?, ?, 'pending')
    """,
        (user_id, process_id, event_kind, event_ref),
    )
    row = con.execute(
        "SELECT id FROM alert_outbox WHERE user_id = ? AND process_id = ? AND event_kind = ? AND event_ref = ?",
        (user_id, process_id, event_kind, event_ref),
    ).fetchone()
    if row is None or row[0] is None:
        if cur.lastrowid:
            return int(cur.lastrowid)
        raise RuntimeError(f"enqueue_alert failed to persist {(user_id, process_id, event_kind, event_ref)!r}")
    return int(row[0])


def match_subscription(con: sqlite3.Connection, sub: AlertSubscription, process_id: int) -> bool:
    proc_cols = {r[1] for r in con.execute("PRAGMA table_info(process)").fetchall()}
    wanted: list[str] = ["id"]
    for c in ("title", "object", "text_content", "modality", "modality_code", "modality_raw", "entity", "source_id"):
        if c in proc_cols:
            wanted.append(c)
    cols_sql = ", ".join(f'"{c}"' for c in wanted)
    cur = con.execute(f"SELECT {cols_sql} FROM process WHERE id = ?", (process_id,))
    row = cur.fetchone()
    if not row:
        return False
    col_names = [d[0] for d in cur.description]
    try:
        values = tuple(row)
    except Exception:
        values = row
    rec = dict(zip(col_names, values, strict=False))
    parts: list[str] = []
    if "text_content" in proc_cols:
        v = rec.get("text_content")
        if v:
            parts.append(str(v))
    if "title" in proc_cols:
        v = rec.get("title")
        if v:
            parts.append(str(v))
    if "object" in proc_cols:
        v = rec.get("object")
        if v:
            parts.append(str(v))
    text_content = " ".join(parts).strip()
    modality_candidates: list[str] = []
    for c in ("modality", "modality_code", "modality_raw"):
        if c in proc_cols:
            v = rec.get(c)
            if v is not None and str(v).strip():
                modality_candidates.append(str(v))
    if "entity" in proc_cols:
        entity_raw = rec.get("entity")
        entity = str(entity_raw) if entity_raw is not None else ""
    else:
        entity = ""
        try:
            tbl = con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'source'").fetchone()
            if tbl is not None and "source_id" in proc_cols:
                source_id_val = rec.get("source_id")
                if source_id_val is not None:
                    srow = con.execute("SELECT code FROM source WHERE id = ?", (source_id_val,)).fetchone()
                    if srow is not None and srow[0] is not None:
                        entity = str(srow[0])
        except sqlite3.Error:
            entity = ""
    text_content = text_content or ""
    entity = entity or ""
    if sub.keywords:
        keywords_norm = [str(k).strip().lower() for k in sub.keywords if str(k).strip()]
        if keywords_norm:
            text_lower = text_content.lower()
            if not any(k in text_lower for k in keywords_norm):
                return False
    if sub.modalities:
        norm_cands = {(c or "").strip().lower() for c in modality_candidates if (c or "").strip()}
        norm_sub = [str(m).strip().lower() for m in sub.modalities if str(m).strip()]
        if norm_sub:
            if not any(m in norm_cands for m in norm_sub):
                return False
        else:
            if modality_candidates:
                return False
    if sub.entities:
        norm_entity = entity.strip().lower()
        norm_sub_entities = {str(e).strip().lower() for e in sub.entities if str(e).strip()}
        if norm_sub_entities:
            if norm_entity not in norm_sub_entities:
                return False
    return True


def _filtros(raw: object) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]] | None:
    """Devolve tripla (keywords, modalities, entities) ou None se invalido.

    Filtro ausente/vazio legitimo (None, string vazia/so-espacos, '{}',
    dict sem chaves de filtro) devolve filtros vazios (match-all, comportamento
    atual). JSON invalido ou nao-dict devolve None: a assinatura deve ser
    ignorada (nao casar nada) e o chamador registra o aviso."""
    if raw is None:
        return (), (), ()
    texto = str(raw).strip()
    if not texto:
        return (), (), ()
    try:
        dados = json.loads(texto)
    except Exception:
        return None
    if not isinstance(dados, dict):
        return None

    def lista(chave: str) -> tuple[str, ...]:
        valor = dados.get(chave)
        if valor is None:
            return ()
        if not isinstance(valor, (list, tuple)):
            return ()
        return tuple(str(item) for item in valor)

    return lista("keywords"), lista("modalities"), lista("entities")


def subscriptions_ativas(con: sqlite3.Connection) -> list[AlertSubscription]:
    linhas = con.execute(
        "SELECT id, filter_expr, attrs FROM alert_subscription WHERE enabled = 1 ORDER BY id"
    ).fetchall()
    subs: list[AlertSubscription] = []
    for linha in linhas:
        sid = int(linha[0])
        raw = linha[1]
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            try:
                raw = linha[2]
            except Exception:
                raw = None
        tripla = _filtros(raw)
        if tripla is None:
            logger.warning("alert_subscription %d com filtro invalido: assinatura ignorada", sid)
            continue
        keywords, modalities, entities = tripla
        subs.append(
            AlertSubscription(
                user_id=sid,
                keywords=keywords,
                modalities=modalities,
                entities=entities,
            )
        )
    return subs


def gerar_alertas_do_run(con: sqlite3.Connection, source_id: int, run_id: int) -> int:
    sid = int(source_id)
    rid = int(run_id)
    subs = subscriptions_ativas(con)
    if not subs:
        return 0
    procs = con.execute(
        "SELECT id, first_seen_run_id, last_changed_run_id FROM process"
        " WHERE source_id = ? AND last_changed_run_id = ?",
        (sid, rid),
    ).fetchall()
    if not procs:
        return 0
    ref = str(rid)
    novos = 0
    for proc in procs:
        pid = int(proc[0])
        primeiro = proc[1]
        try:
            primeiro_int = int(primeiro) if primeiro is not None else None
        except Exception:
            primeiro_int = None
        kind = "new" if primeiro_int == rid else "changed"
        for sub in subs:
            if not match_subscription(con, sub, pid):
                continue
            dedup = f"{sub.user_id}:{pid}:{kind}:{ref}"
            existente = con.execute("SELECT id FROM alert_outbox WHERE dedup_key = ?", (dedup,)).fetchone()
            enqueue_alert(con, sub.user_id, pid, kind, ref)
            if existente is None:
                novos += 1
    return novos
