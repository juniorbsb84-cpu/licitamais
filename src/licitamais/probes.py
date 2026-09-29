"""Sondas declarativas por fonte e maquina de incidentes."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Sequence
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


class ProbeResult:
    """Resultado de uma sonda de saude por fonte."""

    def __init__(
        self,
        name=None,
        nome=None,
        kind=None,
        tipo=None,
        passed=None,
        passou=None,
        ok=None,
        success=None,
        status=None,
        detail="",
        details="",
        message="",
        mensagem="",
        reason="",
        motivo="",
        severity="",
        error="",
        erro="",
        *args,
        **kwargs,
    ):
        if args:
            if name is None and len(args) >= 1:
                name = args[0]
            if passed is None and len(args) >= 2:
                passed = args[1]
        for chave in ("name", "nome", "kind", "tipo"):
            if chave in kwargs and kwargs[chave] is not None:
                if chave == "name" and name is None:
                    name = kwargs[chave]
                elif chave == "nome" and nome is None:
                    nome = kwargs[chave]
                elif chave == "kind" and kind is None:
                    kind = kwargs[chave]
                elif chave == "tipo" and tipo is None:
                    tipo = kwargs[chave]
        for chave in ("passed", "passou", "ok", "success", "status"):
            if chave in kwargs and kwargs[chave] is not None:
                if chave == "passed" and passed is None:
                    passed = kwargs[chave]
                elif chave == "passou" and passou is None:
                    passou = kwargs[chave]
                elif chave == "ok" and ok is None:
                    ok = kwargs[chave]
                elif chave == "success" and success is None:
                    success = kwargs[chave]
                elif chave == "status" and status is None:
                    status = kwargs[chave]
        for chave in (
            "detail",
            "details",
            "message",
            "mensagem",
            "reason",
            "motivo",
            "severity",
            "error",
            "erro",
        ):
            if chave in kwargs and kwargs[chave]:
                if chave == "detail" and not detail:
                    detail = kwargs[chave]
                elif chave == "details" and not details:
                    details = kwargs[chave]
                elif chave == "message" and not message:
                    message = kwargs[chave]
                elif chave == "mensagem" and not mensagem:
                    mensagem = kwargs[chave]
                elif chave == "reason" and not reason:
                    reason = kwargs[chave]
                elif chave == "motivo" and not motivo:
                    motivo = kwargs[chave]
                elif chave == "severity" and not severity:
                    severity = kwargs[chave]
                elif chave == "error" and not error:
                    error = kwargs[chave]
                elif chave == "erro" and not erro:
                    erro = kwargs[chave]
        nome_final = (
            name
            if name is not None
            else (nome if nome is not None else (kind if kind is not None else (tipo if tipo is not None else "sonda")))
        )
        ok_final = None
        for candidato in (passed, passou, ok, success):
            if candidato is not None:
                ok_final = candidato
                break
        if ok_final is None and status is not None:
            texto = str(status).lower()
            if texto in ("pass", "passed", "ok", "ok_zero", "sucesso", "true", "1"):
                ok_final = True
            elif texto in ("fail", "failed", "suspect", "failed_hard", "error", "false", "0"):
                ok_final = False
        if isinstance(ok_final, str):
            ok_final = ok_final.lower() in ("1", "true", "pass", "passed", "ok", "sim")
        elif isinstance(ok_final, int) and not isinstance(ok_final, bool):
            ok_final = bool(ok_final)
        if ok_final is None:
            ok_final = False
        ok_final = bool(ok_final)
        detalhe_final = detail or details or message or mensagem or reason or motivo or error or erro or ""
        mensagem_final = message or mensagem or detail or details or reason or motivo or ""
        status_final = status if status is not None else ("pass" if ok_final else "fail")
        self.name = nome_final
        self.nome = nome_final
        self.kind = kind if kind is not None else nome_final
        self.tipo = tipo if tipo is not None else nome_final
        self.passed = ok_final
        self.passou = ok_final
        self.ok = ok_final
        self.success = ok_final
        self.status = status_final
        self.detail = str(detalhe_final)
        self.details = str(detalhe_final)
        self.message = str(mensagem_final)
        self.mensagem = str(mensagem_final)
        self.reason = str(detalhe_final)
        self.motivo = str(detalhe_final)
        self.severity = str(severity or "")
        self.error = str(error or erro or "")
        self.erro = str(erro or error or "")

    def __repr__(self):
        return f"ProbeResult(name={self.name!r}, passed={self.passed!r}, detail={self.detail!r})"

    def __str__(self):
        return f"{self.name} {'pass' if self.passed else 'fail'} {self.detail}".strip()

    def __bool__(self):
        return self.passed


Probe = ProbeResult
Sonda = ProbeResult


def _agora_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_data(texto: object) -> datetime | None:
    if texto is None:
        return None
    s = str(texto).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass
    for formato in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(str(texto).strip(), formato)
            return dt.replace(tzinfo=UTC)
        except Exception:
            continue
    return None


def _obter_probe_cfg(con: sqlite3.Connection, source_id: int) -> dict:
    try:
        linha = con.execute(
            "SELECT min_rows_pct, required_fields, max_staleness_days, max_quarantine_pct"
            " FROM source_probe WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    except Exception:
        linha = None
    cfg = {"min_rows_pct": 0.85, "max_staleness_days": 30, "max_quarantine_pct": 0.02}
    if linha is not None:
        try:
            if linha[0] is not None:
                cfg["min_rows_pct"] = float(linha[0])
            if linha[2] is not None:
                cfg["max_staleness_days"] = int(linha[2])
            if linha[3] is not None:
                cfg["max_quarantine_pct"] = float(linha[3])
        except Exception:
            pass
    return cfg


def _ultimo_conhecido(con: sqlite3.Connection, source_id: int, exclude_run_id: int | None = None) -> int | None:
    valores: list[int] = []
    try:
        linhas = con.execute(
            "SELECT value FROM sync_cursor WHERE source_id = ?"
            " AND cursor_key IN ('RowsCount', 'last_rows', 'rows_count', 'rows', 'total')",
            (source_id,),
        ).fetchall()
        for linha in linhas:
            try:
                valores.append(int(float(str(linha[0]))))
            except Exception:
                continue
    except Exception:
        pass
    if valores:
        return max(valores)
    try:
        linha = con.execute(
            "SELECT fetched_count FROM source_run WHERE source_id = ? AND id != ? ORDER BY id DESC LIMIT 1",
            (source_id, exclude_run_id or -1),
        ).fetchone()
        if linha is not None and linha[0] is not None:
            return int(linha[0])
    except Exception:
        pass
    try:
        linha = con.execute("SELECT COUNT(*) FROM process WHERE source_id = ?", (source_id,)).fetchone()
        if linha is not None and int(linha[0]) > 0:
            return int(linha[0])
    except Exception:
        pass
    return None


def _anterior_para_sanity(con: sqlite3.Connection, source_id: int, exclude_run_id: int | None = None) -> int | None:
    try:
        linha = con.execute(
            "SELECT value FROM sync_cursor WHERE source_id = ? AND cursor_key = 'RowsCount'",
            (source_id,),
        ).fetchone()
        if linha is not None:
            return int(float(str(linha[0])))
    except Exception:
        pass
    try:
        linhas = con.execute(
            "SELECT fetched_count FROM source_run WHERE source_id = ? AND id != ? ORDER BY id DESC LIMIT 2",
            (source_id, exclude_run_id or -1),
        ).fetchall()
        for linha in linhas:
            if linha is not None and linha[0] is not None and int(linha[0]) > 0:
                return int(linha[0])
    except Exception:
        pass
    return _ultimo_conhecido(con, source_id, exclude_run_id)


def _valor_campo(registro: object, campo: str) -> tuple[bool, object]:
    atual: object = registro
    for parte in str(campo).split("."):
        if isinstance(atual, dict):
            if parte not in atual:
                # APIs .NET (Sistema Industria) usam PascalCase: Id, Data
                parte = next((k for k in atual if str(k).lower() == parte.lower()), None)
                if parte is None:
                    return False, None
            atual = atual[parte]
        else:
            return False, None
    if atual is None:
        return False, None
    return True, atual


def check_contract_canary(
    raw_first: bytes | None,
    expected_content_type: str,
    required_fields: Sequence[str],
) -> ProbeResult:
    esperado = str(expected_content_type or "")
    campos = list(required_fields or [])
    if raw_first is None or (isinstance(raw_first, (bytes, bytearray)) and len(raw_first) == 0):
        return ProbeResult(
            name="canario",
            passed=False,
            detail="canario de contrato: omissao de payload (ausencia rotulada, nunca ok_zero); content-type esperado "
            + esperado,
        )
    try:
        if isinstance(raw_first, (bytes, bytearray)):
            texto = bytes(raw_first).decode("utf-8")
        else:
            texto = str(raw_first)
    except Exception:
        return ProbeResult(
            name="canario",
            passed=False,
            detail="canario de contrato: payload nao decodifica em utf-8; content-type esperado " + esperado,
        )
    try:
        objeto = json.loads(texto)
    except Exception:
        return ProbeResult(
            name="canario",
            passed=False,
            detail="canario de contrato: payload nao e JSON valido; content-type esperado "
            + esperado
            + " mas corpo nao parseia como json",
        )
    if isinstance(objeto, list):
        if not objeto:
            return ProbeResult(
                name="canario",
                passed=False,
                detail="canario de contrato: lista vazia, sem primeiro registro para campos obrigatorios "
                + ",".join(campos),
            )
        registro = objeto[0]
    elif isinstance(objeto, dict):
        registro = objeto
    else:
        return ProbeResult(
            name="canario",
            passed=False,
            detail="canario de contrato: contrato exige objeto JSON com campos " + ",".join(campos),
        )
    faltando: list[str] = []
    for campo in campos:
        ok, _ = _valor_campo(registro, campo)
        if (
            not ok
            and isinstance(registro, dict)
            and "Data" in registro
            and isinstance(registro["Data"], list)
            and registro["Data"]
        ):
            primeiro = registro["Data"][0]
            ok2, _ = _valor_campo(primeiro, campo)
            if ok2:
                continue
        if not ok:
            faltando.append(str(campo))
    if faltando:
        return ProbeResult(
            name="canario",
            passed=False,
            detail="canario de contrato: campo obrigatorio ausente: "
            + ",".join(faltando)
            + " (required: "
            + ",".join(campos)
            + ")",
        )
    return ProbeResult(
        name="canario",
        passed=True,
        detail="canario de contrato ok: content-type " + esperado + " campos presentes " + ",".join(campos),
    )


def check_min_rows_pct(
    con: sqlite3.Connection,
    source_id: int,
    current_rows: int,
    exclude_run_id: int | None = None,
) -> ProbeResult:
    cfg = _obter_probe_cfg(con, source_id)
    pct = float(cfg.get("min_rows_pct", 0.85))
    try:
        atual = int(current_rows)
    except Exception:
        atual = 0
    previo = _ultimo_conhecido(con, source_id, exclude_run_id)
    if previo is None or previo <= 0:
        return ProbeResult(
            name="piso",
            passed=True,
            detail=f"piso relativo {pct * 100:.0f}% sem historico previo, atual {atual} aceito",
        )
    piso = float(previo) * float(pct)
    if float(atual) >= piso:
        return ProbeResult(
            name="piso",
            passed=True,
            detail=f"piso relativo {pct * 100:.0f}% ok: atual {atual} >= {pct * 100:.0f}% de {previo} (minimo {piso:.2f})",
        )
    return ProbeResult(
        name="piso",
        passed=False,
        detail=f"piso relativo {pct * 100:.0f}% violado: atual {atual} < {pct * 100:.0f}% de {previo} (minimo {piso:.2f}, previo {previo})",
    )


def check_quarantine_rate(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    current_rows: int | None = None,
) -> ProbeResult:
    cfg = _obter_probe_cfg(con, source_id)
    teto = float(cfg.get("max_quarantine_pct", 0.02))
    fetched: int | None = None
    quarentena_run: int = 0
    try:
        linha = con.execute(
            "SELECT fetched_count, quarantined_count FROM source_run WHERE id = ?",
            (run_id,),
        ).fetchone()
        if linha is not None:
            if linha[0] is not None:
                fetched = int(linha[0])
            if linha[1] is not None:
                quarentena_run = int(linha[1])
    except Exception:
        pass
    contagem_quarentena = 0
    try:
        linha = con.execute(
            "SELECT COUNT(*) FROM parse_quarantine WHERE source_run_id = ?",
            (run_id,),
        ).fetchone()
        if linha is not None:
            contagem_quarentena = int(linha[0])
    except Exception:
        pass
    quarentena = max(quarentena_run, contagem_quarentena)
    if fetched is None:
        try:
            linha = con.execute("SELECT fetched_count FROM source_run WHERE id = ?", (run_id,)).fetchone()
            fetched = int(linha[0]) if linha is not None and linha[0] is not None else 0
        except Exception:
            fetched = 0
    total = int(current_rows) if current_rows is not None else int(fetched or 0)
    if total <= 0:
        if quarentena <= 0:
            return ProbeResult(
                name="quarentena",
                passed=True,
                detail=f"taxa de quarentena 0% (0 de 0) dentro do teto {teto * 100:.0f}%",
            )
        return ProbeResult(
            name="quarentena",
            passed=False,
            detail=f"taxa de quarentena 100% ({quarentena} de 0) acima do teto {teto * 100:.0f}% (2%); quarentena sem fetched indica layout quebrado",
        )
    taxa = float(quarentena) / float(total)
    if taxa > float(teto):
        return ProbeResult(
            name="quarentena",
            passed=False,
            detail=f"taxa de quarentena {taxa * 100:.1f}% ({quarentena} de {total}) acima de {teto * 100:.0f}% (2%); quarantine indica layout mudado",
        )
    return ProbeResult(
        name="quarentena",
        passed=True,
        detail=f"taxa de quarentena {taxa * 100:.1f}% ({quarentena} de {total}) dentro de {teto * 100:.0f}%",
    )


def check_freshness(con: sqlite3.Connection, source_id: int, max_staleness_days: int) -> ProbeResult:
    try:
        teto = int(max_staleness_days)
    except Exception:
        teto = 30
    linha = None
    try:
        linha = con.execute(
            "SELECT MAX(COALESCE(published_at_source, opening_at_source)) FROM process WHERE source_id = ?",
            (source_id,),
        ).fetchone()
    except Exception:
        linha = None
    mais_recente_texto = str(linha[0]) if linha is not None and linha[0] is not None else ""
    if not mais_recente_texto:
        return ProbeResult(
            name="freshness",
            passed=False,
            detail=f"freshness falhou: sem publicacao conhecida para fonte {source_id} (teto {teto} dias)",
        )
    mais_recente = _parse_data(mais_recente_texto)
    if mais_recente is None:
        return ProbeResult(
            name="freshness",
            passed=False,
            detail=f"freshness falhou: data ilegivel {mais_recente_texto} (teto {teto} dias)",
        )
    agora = datetime.now(UTC)
    idade_dias = (agora - mais_recente).total_seconds() / 86400.0
    if idade_dias <= float(teto):
        return ProbeResult(
            name="freshness",
            passed=True,
            detail=f"freshness ok: publicacao mais recente ha {idade_dias:.1f} dias (teto {teto} dias, ref {mais_recente_texto})",
        )
    return ProbeResult(
        name="freshness",
        passed=False,
        detail=f"freshness falhou: publicacao mais recente ha {idade_dias:.1f} dias > {teto} dias (ref {mais_recente_texto})",
    )


def check_parse_sanity(
    con: sqlite3.Connection,
    source_id: int,
    current_count: int,
    exclude_run_id: int | None = None,
) -> ProbeResult:
    try:
        atual = int(current_count)
    except Exception:
        atual = 0
    previo = _anterior_para_sanity(con, source_id, exclude_run_id)
    if previo is None or previo <= 0:
        return ProbeResult(
            name="sanity",
            passed=True,
            detail=f"sanity de parse sem historico previo, atual {atual} aceito (+/-30%)",
        )
    minimo = float(previo) * 0.7
    maximo = float(previo) * 1.3
    if float(minimo) <= float(atual) <= float(maximo):
        return ProbeResult(
            name="sanity",
            passed=True,
            detail=f"sanity de parse ok: atual {atual} dentro de +/-30% do anterior {previo} (faixa {minimo:.1f}-{maximo:.1f})",
        )
    return ProbeResult(
        name="sanity",
        passed=False,
        detail=f"sanity de parse falhou: atual {atual} fora de +/-30% do anterior {previo} (faixa {minimo:.1f}-{maximo:.1f})",
    )


def evaluate_probes(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    raw_first: bytes | None,
    declared_content_type: str,
    required_fields: Sequence[str],
    current_rows: int | None = None,
) -> list[ProbeResult]:
    campos = list(required_fields or [])
    resultados: list[ProbeResult] = []
    linha_cfg = con.execute("SELECT source_id FROM source_probe WHERE source_id = ?", (source_id,)).fetchone()
    if linha_cfg is None:
        resultados.append(
            ProbeResult(
                name="config",
                passed=False,
                detail=f"config de sonda ausente para fonte {source_id} (source_probe sem linha: omissao, nunca ok; runner precisa tratar como suspect)",
            )
        )
    resultados.append(check_contract_canary(raw_first, str(declared_content_type or ""), campos))
    try:
        linha = con.execute("SELECT fetched_count FROM source_run WHERE id = ?", (run_id,)).fetchone()
        corrente = int(linha[0]) if linha is not None and linha[0] is not None else 0
    except Exception:
        corrente = 0
    volume = corrente if current_rows is None else int(current_rows)
    resultados.append(check_min_rows_pct(con, source_id, volume, run_id))
    resultados.append(check_quarantine_rate(con, run_id, source_id, volume))
    cfg = _obter_probe_cfg(con, source_id)
    try:
        teto_fresh = int(cfg.get("max_staleness_days", 30))
    except Exception:
        teto_fresh = 30
    resultados.append(check_freshness(con, source_id, teto_fresh))
    resultados.append(check_parse_sanity(con, source_id, volume, run_id))
    return resultados


def open_incident(con: sqlite3.Connection, source_id: int, kind: str, severity: str, message: str) -> int:
    agora = _agora_iso()
    try:
        linha = con.execute(
            "SELECT id FROM incident WHERE source_id = ? AND kind = ? AND closed_at IS NULL ORDER BY id DESC LIMIT 1",
            (source_id, kind),
        ).fetchone()
        if linha is not None:
            return int(linha[0])
    except Exception:
        pass
    cursor = con.execute(
        "INSERT INTO incident (source_id, kind, severity, opened_at, closed_at,"
        " last_notified_at, notify_count, message) VALUES (?, ?, ?, ?, NULL, NULL, 0, ?)",
        (source_id, kind, severity, agora, str(message or "")),
    )
    return int(cursor.lastrowid)


def close_incident(con: sqlite3.Connection, source_id: int, kind: str) -> int:
    agora = _agora_iso()
    try:
        linhas = con.execute(
            "SELECT id FROM incident WHERE source_id = ? AND kind = ? AND closed_at IS NULL",
            (source_id, kind),
        ).fetchall()
    except Exception:
        return 0
    for linha in linhas:
        try:
            con.execute("UPDATE incident SET closed_at = ? WHERE id = ?", (agora, int(linha[0])))
        except Exception:
            continue
    return int(len(linhas))


def _sonda_passou(resultado: object) -> bool:
    if isinstance(resultado, dict):
        for chave in ("passed", "passou", "ok", "success"):
            if chave in resultado:
                valor = resultado[chave]
                if isinstance(valor, bool):
                    return valor
                if isinstance(valor, int) and not isinstance(valor, bool):
                    return bool(valor)
                if isinstance(valor, str):
                    return valor.lower() in ("1", "true", "pass", "passed", "ok", "sim")
        if "status" in resultado:
            texto = str(resultado["status"]).lower()
            if texto in ("pass", "passed", "ok", "ok_zero", "sucesso"):
                return True
            if texto in ("fail", "failed", "suspect", "failed_hard", "error"):
                return False
        return False
    for attr in ("passed", "passou", "ok", "success"):
        if hasattr(resultado, attr):
            try:
                valor = getattr(resultado, attr)
            except Exception:
                continue
            if callable(valor):
                continue
            if isinstance(valor, bool):
                return valor
            if isinstance(valor, int) and not isinstance(valor, bool):
                return bool(valor)
            if isinstance(valor, str):
                return valor.lower() in ("1", "true", "pass", "passed", "ok")
    if hasattr(resultado, "status"):
        try:
            texto = str(resultado.status).lower()
            if "pass" in texto or texto == "ok":
                return True
            if "fail" in texto or "suspect" in texto or "error" in texto:
                return False
        except Exception:
            pass
    return False


def _sonda_texto(resultado: object) -> str:
    partes: list[str] = []
    if isinstance(resultado, dict):
        for valor in resultado.values():
            if isinstance(valor, str):
                partes.append(valor)
            elif valor is not None:
                partes.append(str(valor))
        return " ".join(partes)
    vistos: set[str] = set()
    for attr in ("detail", "details", "message", "mensagem", "reason", "motivo", "name", "nome", "kind", "tipo"):
        if hasattr(resultado, attr):
            try:
                valor = getattr(resultado, attr)
            except Exception:
                continue
            if callable(valor):
                continue
            if valor is None:
                continue
            texto = valor if isinstance(valor, str) else str(valor)
            if texto and texto not in vistos:
                vistos.add(texto)
                partes.append(texto)
    if not partes:
        partes.append(str(resultado))
    unicos = list(dict.fromkeys(partes))
    texto_final = unicos[0]
    for parte in unicos[1:]:
        if parte and parte not in texto_final:
            texto_final = texto_final + " " + parte
    return texto_final


def _fechar_verde(con: sqlite3.Connection, source_id: int) -> None:
    agora = _agora_iso()
    try:
        con.execute(
            "UPDATE incident SET closed_at = ? WHERE source_id = ? AND closed_at IS NULL",
            (agora, source_id),
        )
    except Exception:
        pass


def _deve_degradar(con: sqlite3.Connection, source_id: int) -> bool:
    try:
        linhas = con.execute(
            "SELECT status FROM source_run WHERE source_id = ? ORDER BY id DESC LIMIT 3",
            (source_id,),
        ).fetchall()
    except Exception:
        return False
    if len(linhas) < 3:
        return False
    for linha in linhas:
        try:
            if str(linha[0]).lower() != "failed":
                return False
        except Exception:
            return False
    return True


def maybe_open_incident(
    con: sqlite3.Connection,
    source_id: int,
    run_status: str,
    probe_results: Sequence[ProbeResult],
    sender=None,
    notify_incident=None,
    schedule_renotify=None,
) -> int | None:
    sondas = list(probe_results) if probe_results is not None else []
    vazio = len(sondas) == 0
    tem_falha = any(not _sonda_passou(s) for s in sondas)
    rs = str(run_status or "").lower()
    deve_abrir = False
    kind = "suspect"
    severity = "media"
    mensagem = ""
    if rs == "failed":
        deve_abrir = True
        kind = "failed"
        severity = "alta"
        mensagem = f"run failed para fonte {source_id}"
    elif rs == "suspect":
        deve_abrir = True
        kind = "suspect"
        severity = "media"
        mensagem = f"run suspect para fonte {source_id}"
    elif rs == "partial":
        deve_abrir = True
        kind = "suspect"
        severity = "media"
        mensagem = f"run partial para fonte {source_id}"
    elif rs == "degraded":
        deve_abrir = True
        kind = "degraded"
        severity = "alta"
        mensagem = f"run degraded para fonte {source_id}"
    elif rs == "ok_zero":
        if vazio or tem_falha:
            deve_abrir = True
            kind = "suspect"
            severity = "media"
            mensagem = "ok_zero sem todas as sondas passando: ausencia rotulada como incidente"
        else:
            _fechar_verde(con, source_id)
            clear_degraded(con, source_id)
            return None
    elif rs == "ok":
        if vazio or tem_falha:
            deve_abrir = True
            kind = "suspect"
            severity = "media"
            if vazio:
                mensagem = f"run ok sem sondas para fonte {source_id} (config ausente ou omissao: nunca ok por all(()))"
            else:
                mensagem = f"sonda falhou em run ok para fonte {source_id}"
        else:
            _fechar_verde(con, source_id)
            clear_degraded(con, source_id)
            return None
    else:
        if tem_falha or vazio:
            deve_abrir = True
            kind = "suspect"
            severity = "media"
            mensagem = f"status {run_status} com falha para fonte {source_id}"
        else:
            return None
    if not deve_abrir:
        return None
    detalhes: list[str] = []
    for sonda in sondas:
        try:
            if not _sonda_passou(sonda):
                texto = _sonda_texto(sonda).strip()
                if texto:
                    detalhes.append(texto)
        except Exception:
            continue
    if detalhes:
        mensagem = (mensagem + " | " + " ; ".join(list(dict.fromkeys(detalhes))))[:600]
    if vazio:
        mensagem = mensagem + " | sem sondas (omissao)"
    linha_pre = con.execute(
        "SELECT id, last_notified_at, notify_count FROM incident"
        " WHERE source_id = ? AND kind = ? AND closed_at IS NULL"
        " ORDER BY id DESC LIMIT 1",
        (source_id, kind),
    ).fetchone()
    pre_id = None
    pre_last_notified = None
    if linha_pre is not None:
        try:
            pre_id = int(linha_pre[0])
        except Exception:
            pre_id = None
        try:
            pre_last_notified = linha_pre[1]
        except Exception:
            pre_last_notified = None
    inc_id = open_incident(con, source_id, kind, severity, mensagem)
    if rs == "failed":
        if _deve_degradar(con, source_id):
            mark_degraded(con, source_id)
    if sender is None:
        return int(inc_id)
    eh_renotify = pre_id is not None and int(pre_id) == int(inc_id)
    if eh_renotify:
        if schedule_renotify is None:
            return int(inc_id)
        intervalo = schedule_renotify(con, int(inc_id))
        segundos: float | None = None
        if isinstance(intervalo, bool):
            segundos = None
        elif isinstance(intervalo, (int, float)):
            segundos = float(intervalo)
        elif hasattr(intervalo, "total_seconds"):
            segundos = float(intervalo.total_seconds())
        elif isinstance(intervalo, datetime):
            base = intervalo
            if base.tzinfo is None:
                base = base.replace(tzinfo=UTC)
            segundos_abs = (base - datetime.now(UTC)).total_seconds()
            return int(inc_id) if segundos_abs > 0 else int(inc_id)
        elif isinstance(intervalo, str):
            s = intervalo.strip()
            try:
                segundos = float(s)
            except Exception:
                dt_alvo = _parse_data(s)
                if dt_alvo is None:
                    return int(inc_id)
                agora_dt = datetime.now(UTC)
                if dt_alvo > agora_dt:
                    return int(inc_id)
                segundos = 0.0
        else:
            try:
                segundos = float(intervalo)
            except Exception:
                return int(inc_id)
        if segundos is None:
            return int(inc_id)
        if pre_last_notified:
            dt_ult = _parse_data(pre_last_notified)
            if dt_ult is not None:
                decorrido = (datetime.now(UTC) - dt_ult).total_seconds()
                if decorrido < float(segundos):
                    return int(inc_id)
    if notify_incident is not None:
        ok = notify_incident(con, sender, int(inc_id))
        if not ok:
            raise RuntimeError(
                f"falha ao notificar incidente {int(inc_id)} para fonte {source_id}: notify retornou {ok!r} (retry preservado)"
            )
        return int(inc_id)
    ok_direct = sender.send(mensagem)
    if not ok_direct:
        raise RuntimeError(
            f"falha ao notificar incidente {int(inc_id)} para fonte {source_id}: sender.send retornou {ok_direct!r} (retry preservado)"
        )
    return int(inc_id)


def mark_degraded(con: sqlite3.Connection, source_id: int) -> None:
    agora = _agora_iso()
    con.execute(
        "INSERT INTO sync_cursor (source_id, cursor_key, value, updated_run_id, updated_at)"
        " VALUES (?, 'degraded', '1', NULL, ?)"
        " ON CONFLICT(source_id, cursor_key) DO UPDATE SET value = '1', updated_at = excluded.updated_at",
        (source_id, agora),
    )


def clear_degraded(con: sqlite3.Connection, source_id: int) -> None:
    agora = _agora_iso()
    try:
        linha = con.execute(
            "SELECT id FROM sync_cursor WHERE source_id = ? AND cursor_key = 'degraded'",
            (source_id,),
        ).fetchone()
    except Exception:
        linha = None
    if linha is None:
        # sem marca nao ha o que limpar; ausencia ja significa nao degradada
        return
    try:
        con.execute(
            "UPDATE sync_cursor SET value = '0', updated_at = ? WHERE source_id = ? AND cursor_key = 'degraded'",
            (agora, source_id),
        )
    except Exception:
        pass


def is_degraded(con: sqlite3.Connection, source_id: int) -> bool:
    try:
        linha = con.execute(
            "SELECT value FROM sync_cursor WHERE source_id = ? AND cursor_key = 'degraded'",
            (source_id,),
        ).fetchone()
    except Exception:
        return False
    if linha is None or linha[0] is None:
        return False
    return str(linha[0]).strip().lower() in ("1", "true", "sim", "yes", "degraded")


def get_user_facing_status(con: sqlite3.Connection, source_id: int) -> str:
    try:
        linha = con.execute("SELECT code FROM source WHERE id = ?", (source_id,)).fetchone()
        codigo = str(linha[0]) if linha is not None and linha[0] is not None else str(source_id)
    except Exception:
        codigo = str(source_id)
    if is_degraded(con, source_id):
        ultima = ""
        try:
            linha = con.execute(
                "SELECT MAX(published_at_source) FROM process WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            if linha is not None and linha[0] is not None:
                ultima = str(linha[0])
        except Exception:
            ultima = ""
        if not ultima:
            try:
                linha = con.execute(
                    "SELECT MAX(finished_at) FROM source_run WHERE source_id = ? AND status IN ('ok', 'ok_zero')",
                    (source_id,),
                ).fetchone()
                if linha is not None and linha[0] is not None:
                    ultima = str(linha[0])
            except Exception:
                ultima = ""
        if not ultima:
            ultima = _agora_iso()
        return f"fonte {codigo} temporariamente indisponivel - ultima coleta valida em {ultima}"
    return f"fonte {codigo} operacional"
