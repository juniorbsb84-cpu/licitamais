"""Orquestrador de runs: rate limit, retry, checkpoint por request e status."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from urllib.parse import urlencode

from .alerts.user import gerar_alertas_do_run
from .canonical import CaptureRecord
from .errors import ErrorClass, apply_retry_policy, classify_error
from .loader import canonical_hash, load_batch, marcar_ausentes, quarantine_record, record_capture, update_cursor
from .probes import evaluate_probes, maybe_open_incident
from .rate_limit import sleep_rate_limited
from .types import FetchFailure, FetchRequest, PlanContext, ProcessRecord, SourceConfig


class RunStatus(str, Enum):
    OK = "ok"
    OK_ZERO = "ok_zero"
    SUSPECT = "suspect"
    PARTIAL = "partial"
    FAILED = "failed"


@dataclass(frozen=True)
class RunCounts:
    fetched: int = 0
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    quarantined: int = 0


@dataclass(frozen=True)
class ProbeResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class SourceRecord:
    id: int
    code: str
    base_url: str
    adapter_version: str
    enabled: int = 1


@dataclass(frozen=True)
class RunConfig:
    adapters: Mapping[str, object] = field(default_factory=dict)
    trigger: str = "cron"
    min_delay_ms: int | None = None
    jitter_ms: int | None = None
    max_calls_per_run: int | None = None
    capture_only: bool = False


@dataclass(frozen=True)
class RunOutcome:
    run_id: int
    source_id: int
    source_code: str
    status: str
    counts: RunCounts
    error: str | None = None


def _agora() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_texto(status: object) -> str:
    if isinstance(status, RunStatus):
        return status.value
    return str(status)


def _sonda_passou(probe: object) -> bool:
    return bool(getattr(probe, "passed", False))


def open_source_run(con: sqlite3.Connection, source_id: int, trigger: str, adapter_version: str) -> int:
    # status NOT NULL no schema: abre como "failed" e close_source_run decide o final.
    cursor = con.execute(
        """
        INSERT INTO source_run (source_id, "trigger", adapter_version, status, started_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(source_id), trigger, adapter_version, RunStatus.FAILED.value, _agora()),
    )
    return int(cursor.lastrowid)


def close_source_run(con: sqlite3.Connection, run_id: int, status: str, error: str | None) -> None:
    con.execute(
        "UPDATE source_run SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        (_status_texto(status), _agora(), error, int(run_id)),
    )


# Decisao do usuario 2026-09-25: detalhe/contratos so de processos deste ano em diante.
ANO_MINIMO_HIDRATACAO = 2026


def _no_recorte(con: sqlite3.Connection, source_id: int, reqs) -> list[FetchRequest]:
    """Descarta hidratacao de processo anterior ao recorte (ou sem ano)."""
    validos = {
        r[0]
        for r in con.execute(
            "SELECT source_native_id FROM process WHERE source_id = ? AND year >= ?",
            (source_id, ANO_MINIMO_HIDRATACAO),
        )
    }
    return [req for req in reqs if req.parent_native_id is None or str(req.parent_native_id) in validos]


_CAMPOS_REQ = (
    "endpoint",
    "method",
    "params",
    "headers_extra",
    "phase",
    "entity_hint",
    "parent_native_id",
    "cost_weight",
)


def _hidratacao_pendente(con: sqlite3.Connection, source_id: int) -> list[FetchRequest]:
    """Fila de hidratacao que o teto do run anterior cortou (cursor hydration_pending)."""
    linha = con.execute(
        "SELECT value FROM sync_cursor WHERE source_id = ? AND cursor_key = 'hydration_pending'",
        (source_id,),
    ).fetchone()
    if linha is None or not linha[0]:
        return []
    return [
        FetchRequest(body=None, cursor_out=None, **{k: d.get(k) for k in _CAMPOS_REQ}) for d in json.loads(linha[0])
    ]


def _gravar_hidratacao_pendente(
    con: sqlite3.Connection, source_id: int, run_id: int, reqs: Sequence[FetchRequest]
) -> None:
    dados = [
        {k: (dict(v) if isinstance(v, Mapping) else v) for k, v in ((k, getattr(req, k)) for k in _CAMPOS_REQ)}
        for req in reqs
    ]
    update_cursor(con, source_id, "hydration_pending", json.dumps(dados, ensure_ascii=False), run_id)


def _refresh_hidratacao(
    con: sqlite3.Connection, source_id: int, after_id: int, limite: int, gerar
) -> tuple[list[FetchRequest], int]:
    """Seleciona processos existentes em rodizio, com retorno ao menor id."""
    linhas = con.execute(
        """
        SELECT id, source_native_id, attrs FROM process
        WHERE source_id = ? AND deleted_at IS NULL
          AND year >= ?
          AND EXISTS (SELECT 1 FROM contract c WHERE c.process_id = process.id)
        ORDER BY CASE WHEN id > ? THEN 0 ELSE 1 END, id
        LIMIT ?
        """,
        (source_id, ANO_MINIMO_HIDRATACAO, after_id, limite),
    ).fetchall()
    reqs = []
    for linha in linhas:
        process = ProcessRecord(
            source_native_id=str(linha[1]),
            attrs=json.loads(linha[2] or "{}"),
        )
        reqs.extend(gerar(process))
    return reqs, int(linhas[-1][0]) if linhas else after_id


def decide_status(counts: RunCounts, probe_results: Sequence[ProbeResult]) -> RunStatus:
    """Nenhum run zero-novidade e saudavel por omissao."""
    novo = int(counts.new)
    mudado = int(counts.changed)
    inalterado = int(counts.unchanged)
    quarentena = int(counts.quarantined)
    buscado = int(counts.fetched)
    registros = novo + mudado + inalterado + quarentena
    sondas_ok = all(_sonda_passou(probe) for probe in probe_results)

    if registros > 0 and quarentena * 100 > 2 * registros:
        return RunStatus.SUSPECT
    if novo + mudado > 0:
        return RunStatus.OK if sondas_ok else RunStatus.SUSPECT
    if not sondas_ok:
        return RunStatus.SUSPECT
    if buscado <= 0 and registros <= 0:
        return RunStatus.SUSPECT
    return RunStatus.OK_ZERO


def _adaptador_da_fonte(code: str, cfg: object) -> object | None:
    for nome in ("adapters", "adapters_por_fonte", "adapter_map"):
        mapa = getattr(cfg, nome, None)
        if mapa:
            adaptador = mapa.get(code)
            if adaptador is not None:
                return adaptador
    for nome in ("adapter", "adaptador"):
        adaptador = getattr(cfg, nome, None)
        if adaptador is not None:
            return adaptador
    return None


def _semantica_exclusao(adaptador: object) -> str | None:
    caps = getattr(adaptador, "capabilities", None)
    if caps is not None:
        if isinstance(caps, Mapping):
            valor = caps.get("deletion_semantics")
        else:
            valor = getattr(caps, "deletion_semantics", None)
        if isinstance(valor, str) and valor:
            return valor
    valor = getattr(adaptador, "deletion_semantics", None)
    return str(valor) if isinstance(valor, str) and valor else None


def _parametros_de_taxa(cfg: object, adaptador: object) -> tuple[int, int, int | None]:
    rate = getattr(getattr(adaptador, "capabilities", None), "rate", None) or {}
    min_delay = getattr(cfg, "min_delay_ms", None)
    if min_delay is None:
        min_delay = rate.get("min_delay_ms", 0)
    jitter = getattr(cfg, "jitter_ms", None)
    if jitter is None:
        jitter = rate.get("jitter_ms", 0)
    teto_cfg = getattr(cfg, "max_calls_per_run", None)
    if teto_cfg is None:
        teto_cfg = getattr(cfg, "max_calls", None)
    tetos = [t for t in (teto_cfg, rate.get("max_calls_per_run")) if t is not None]
    teto = min(int(t) for t in tetos) if tetos else None
    return int(min_delay or 0), int(jitter or 0), teto


def _cursors_atuais(con: sqlite3.Connection, source_id: int) -> dict[str, str]:
    linhas = con.execute("SELECT cursor_key, value FROM sync_cursor WHERE source_id = ?", (source_id,)).fetchall()
    return {str(linha[0]): str(linha[1] or "") for linha in linhas}


def _url(base_url: str, req: FetchRequest) -> str:
    if base_url:
        url = base_url.rstrip("/") + "/" + req.endpoint.lstrip("/")
    else:
        url = req.endpoint
    if req.params:
        url += "?" + urlencode(sorted(req.params.items()))
    return url


def _header(headers: Mapping[str, str] | None, nome: str) -> str | None:
    for chave, valor in dict(headers or {}).items():
        if str(chave).lower() == nome:
            return str(valor)
    return None


def run_source(con: sqlite3.Connection, source: SourceRecord, cfg: RunConfig) -> RunOutcome:
    source_id = int(getattr(source, "id", 0) or getattr(source, "source_id", 0))
    code = str(getattr(source, "code", "") or getattr(source, "source_code", ""))
    base_url = str(getattr(source, "base_url", "") or "")
    adaptador = _adaptador_da_fonte(code, cfg)
    if adaptador is None:
        raise ValueError(f"sem adaptador para a fonte {code}")
    adapter_version = str(
        getattr(source, "adapter_version", "")
        or getattr(source, "adapter_version_atual", "")
        or getattr(adaptador, "adapter_version", "")
    )
    trigger = str(getattr(cfg, "trigger", "cron") or "cron")
    min_delay_ms, jitter_ms, teto = _parametros_de_taxa(cfg, adaptador)
    capture_only = bool(getattr(cfg, "capture_only", False))

    run_id = open_source_run(con, source_id, trigger, adapter_version)
    # O registro do run precisa sobreviver ao rollback da primeira pagina.
    con.commit()
    try:
        sessao = adaptador.open(SourceConfig(source_code=code, base_url=base_url))
    except Exception as exc:
        erro = f"{type(exc).__name__}: {exc}"
        close_source_run(con, run_id, RunStatus.FAILED.value, erro)
        maybe_open_incident(con, source_id, RunStatus.FAILED.value, ())
        con.commit()
        return RunOutcome(run_id, source_id, code, RunStatus.FAILED.value, RunCounts(), erro)

    buscado = novo = mudado = inalterado = quarentena = 0
    chamadas = 0
    truncado_por_teto = False
    vistos: set[str] = set()
    permanentes: list[tuple[ErrorClass, str]] = []
    fila: deque[FetchRequest] = deque()
    fila_hidratacao: deque[FetchRequest] = deque(_no_recorte(con, source_id, _hidratacao_pendente(con, source_id)))
    fila_refresh: deque[FetchRequest] = deque()
    hidratacao_falha: dict[str, FetchRequest] = {}
    hidratacao_ausente: set[str] = set()
    refresh_after: int | None = None
    primeiro_raw: bytes | None = None
    primeiro_tipo = ""
    total_declarado: int | None = None
    linhas_por_pagina = 0

    def buscar(req: FetchRequest):
        """Fetch unico ponto de rede, com retry por classe e refresh de auth 1x."""
        nonlocal chamadas, truncado_por_teto
        tentativa = 0
        reautenticado = False
        while True:
            if chamadas > 0:
                sleep_rate_limited(min_delay_ms, jitter_ms)
            if teto is not None and chamadas >= teto:
                truncado_por_teto = True
                return None
            chamadas += 1
            try:
                resultado = adaptador.fetch(sessao, req)
            except Exception as exc:
                status_exc = getattr(exc, "status", None)
                if status_exc is None:
                    status_exc = getattr(exc, "status_code", None)
                if status_exc is None:
                    status_exc = getattr(exc, "http_status", None)
                headers_exc = getattr(exc, "headers", None)
                if headers_exc is None:
                    resp_exc = getattr(exc, "response", None)
                    if resp_exc is not None:
                        headers_exc = getattr(resp_exc, "headers", None)
                if headers_exc is None:
                    headers_exc = {}
                if status_exc is None:
                    texto_exc = str(exc)
                    for candidato in (429, 401, 403, 404, 500, 502, 503, 504):
                        if f"HTTP {candidato}" in texto_exc:
                            status_exc = candidato
                            break
                try:
                    mapa_headers = dict(headers_exc) if isinstance(headers_exc, Mapping) else {}
                except Exception:
                    mapa_headers = {}
                try:
                    status_int = int(status_exc) if status_exc is not None else None
                except Exception:
                    status_int = None
                classe = classify_error(exc, status_int, mapa_headers)
                mensagem = f"{type(exc).__name__}: {exc}"
            else:
                if isinstance(resultado, FetchFailure):
                    headers_falha = getattr(resultado, "headers", None)
                    if headers_falha is None:
                        headers_falha = getattr(resultado, "response_headers", None)
                    if headers_falha is None:
                        resp_falha = getattr(resultado, "response", None)
                        if resp_falha is not None:
                            headers_falha = getattr(resp_falha, "headers", None)
                    if headers_falha is None:
                        headers_falha = {}
                    try:
                        mapa_falha = dict(headers_falha) if isinstance(headers_falha, Mapping) else {}
                    except Exception:
                        mapa_falha = {}
                    classe = classify_error(None, resultado.status, mapa_falha)
                    mensagem = resultado.error
                    if resultado.status in (400, 410) and req.phase != "discover":
                        # id de detalhe que a fonte recusa (ex.: sestsenat, edital antigo): igual ao 404
                        classe = ErrorClass.NOT_FOUND
                else:
                    return resultado
            if classe is ErrorClass.AUTH and not reautenticado:
                refresh = getattr(sessao, "refresh", None)
                if callable(refresh):
                    # invalidacao reativa: re-handshake e retry exato uma unica vez
                    refresh()
                    reautenticado = True
                    continue
            if classe is ErrorClass.NOT_FOUND:
                if req.phase != "discover":
                    # 404 em hidratação não é erro de run
                    hidratacao_ausente.add(req.key)
                    return None
                permanentes.append((classe, mensagem))
                return None
            deve, atraso = apply_retry_policy(classe, tentativa)
            if deve:
                tentativa += 1
                time.sleep(atraso)
                continue
            permanentes.append((classe, mensagem))
            return None

    try:
        ctx = PlanContext(
            cursors=_cursors_atuais(con, source_id),
            seen_request_keys=frozenset(),
            parsed_so_far=0,
            source_id=source_id,
        )
        fila.extend(adaptador.plan(ctx))
        hydration = getattr(getattr(adaptador, "capabilities", None), "hydration", {})
        refresh_per_run = int(hydration.get("refresh_per_run", 0) or 0)
        if hydration.get("enabled") and refresh_per_run > 0 and not capture_only:
            gerar = getattr(adaptador, "hydration_requests", None)
            if not callable(gerar):
                raise ValueError("adaptador declara hidratacao sem hydration_requests")
            novos_refresh, refresh_after = _refresh_hidratacao(
                con,
                source_id,
                int(ctx.cursors.get("hydration_refresh_after") or 0),
                refresh_per_run,
                gerar,
            )
            chaves_pendentes = {req.key for req in fila_hidratacao}
            for req in novos_refresh:
                if req.key not in chaves_pendentes:
                    fila_refresh.append(req)
                    chaves_pendentes.add(req.key)
            # O cursor so avanca junto com a fila pendente no fim do run.

        while (fila or fila_hidratacao or fila_refresh) and (teto is None or chamadas < teto):
            if fila:
                req = fila.popleft()
            elif fila_hidratacao:
                req = fila_hidratacao.popleft()
            else:
                req = fila_refresh.popleft()
            if req.key in vistos:
                continue
            vistos.add(req.key)

            pagina = buscar(req)
            if pagina is None:
                if req.phase != "discover" and req.key not in hidratacao_ausente:
                    hidratacao_falha[req.key] = req
                continue
            buscado += 1

            # transacao por request em duas fases: captura com commit imediato
            # para preservar evidencia, depois parse/load em transacao propria.
            novos_para_hidratar = []
            try:
                corpo = bytes(pagina.body)
                if primeiro_raw is None and req.phase == "discover":
                    primeiro_raw = corpo
                    primeiro_tipo = _header(pagina.headers, "content-type") or ""
                record_capture(
                    con,
                    CaptureRecord(
                        source_id=source_id,
                        source_run_id=run_id,
                        endpoint=req.endpoint,
                        url=_url(base_url, req),
                        sha256=hashlib.sha256(corpo).hexdigest(),
                        body=corpo,
                        http_status=pagina.status,
                        content_type=_header(pagina.headers, "content-type") or "",
                        captured_at=pagina.fetched_at,
                    ),
                )
                con.commit()
            except Exception as exc_cap:
                try:
                    con.rollback()
                except Exception:
                    pass
                try:
                    st_cap = pagina.status
                except Exception:
                    st_cap = None
                try:
                    hd_cap = dict(pagina.headers) if isinstance(pagina.headers, Mapping) else {}
                except Exception:
                    hd_cap = {}
                classe_cap = classify_error(exc_cap, st_cap, hd_cap)
                permanentes.append((classe_cap, f"{type(exc_cap).__name__}: {exc_cap}"))
                if req.phase != "discover":
                    hidratacao_falha[req.key] = req
                continue
            if capture_only:
                if req.phase != "discover":
                    hidratacao_falha[req.key] = req
                continue
            try:
                parsed = adaptador.parse(pagina)
                if parsed.fatal:
                    permanentes.append((ErrorClass.CONTRACT, str(parsed.fatal)))
                    if req.phase != "discover":
                        hidratacao_falha[req.key] = req
                    continue
                lote_vazio = (
                    not parsed.batch.processes
                    and not parsed.batch.items
                    and not parsed.batch.attachments
                    and not parsed.batch.results
                    and not parsed.batch.awards
                    and not parsed.batch.contracts
                    and not parsed.batch.orgs
                    and not parsed.batch.phases
                )
                if lote_vazio and parsed.quarantine:
                    permanentes.append(
                        (
                            ErrorClass.CONTRACT,
                            f"pagina corrompida: {len(parsed.quarantine)} item(ns) em quarentena sem registros ({req.endpoint})",
                        )
                    )
                    if req.phase != "discover":
                        hidratacao_falha[req.key] = req
                if req.phase == "discover":
                    declarado = parsed.signals.get("row_count_declared")
                    if isinstance(declarado, int) and not isinstance(declarado, bool) and declarado >= 0:
                        if parsed.signals.get("row_count_scope") == "page":
                            linhas_por_pagina += declarado
                        else:
                            total_declarado = max(total_declarado or 0, declarado)
                # processo pode chegar em pagina de detalhe (SI), nao so na descoberta
                if hydration.get("enabled") and hydration.get("trigger") == "new_or_changed":
                    gerar = getattr(adaptador, "hydration_requests", None)
                    if not callable(gerar):
                        raise ValueError("adaptador declara hidratacao sem hydration_requests")
                    for process in parsed.batch.processes:
                        record_hash = str(process.attrs.get("record_hash") or canonical_hash(process))
                        anterior = con.execute(
                            "SELECT record_hash FROM process WHERE source_id = ? AND source_native_id = ?",
                            (source_id, process.source_native_id),
                        ).fetchone()
                        if anterior is None or anterior[0] != record_hash:
                            novos_para_hidratar.extend(gerar(process))
                lote = parsed.batch
                # vazio em hidratacao e legitimo (processo sem itens); na descoberta
                # continua erro (regra de ouro)
                so_navegacao = (
                    (bool(parsed.next) or req.phase != "discover" or parsed.signals.get("pagina_filtrada") is True)
                    and not parsed.quarantine
                    and not any(
                        (
                            lote.orgs,
                            lote.processes,
                            lote.items,
                            lote.attachments,
                            lote.results,
                            lote.awards,
                            lote.contracts,
                            lote.phases,
                        )
                    )
                )
                if not so_navegacao:
                    # pagina so de navegacao (Ids -> detalhe) nao tem o que carregar
                    carga = load_batch(con, run_id, source_id, parsed.batch)
                    novo += carga.new
                    mudado += carga.changed
                    inalterado += carga.unchanged
                quarentena += len(parsed.quarantine)
                for item in parsed.quarantine:
                    quarantine_record(
                        con,
                        run_id,
                        source_id,
                        item.raw_excerpt.encode("utf-8"),
                        item.reason,
                        item.pointer or "",
                    )
                for chave, valor in dict(parsed.cursor_out or {}).items():
                    update_cursor(con, source_id, str(chave), str(valor), run_id)
                fila.extend(parsed.next)
            except Exception as exc:
                try:
                    con.rollback()
                except Exception:
                    pass
                try:
                    hd_load = dict(pagina.headers) if isinstance(pagina.headers, Mapping) else {}
                except Exception:
                    hd_load = {}
                classe = classify_error(exc, pagina.status, hd_load)
                permanentes.append((classe, f"{type(exc).__name__}: {exc}"))
                if req.phase != "discover":
                    hidratacao_falha[req.key] = req
                continue
            try:
                con.commit()
            except Exception as exc:
                permanentes.append((ErrorClass.INTERNAL, f"commit: {type(exc).__name__}: {exc}"))
                try:
                    con.rollback()
                except Exception:
                    pass
                if req.phase != "discover":
                    hidratacao_falha[req.key] = req
                continue
            chaves_pendentes = {req.key for req in fila_hidratacao}
            for novo_req in _no_recorte(con, source_id, novos_para_hidratar):
                if novo_req.key not in vistos and novo_req.key not in chaves_pendentes:
                    fila_hidratacao.append(novo_req)
                    chaves_pendentes.add(novo_req.key)
            if chaves_pendentes:
                fila_refresh = deque(req for req in fila_refresh if req.key not in chaves_pendentes)
    except Exception as exc:
        permanentes.append((ErrorClass.INTERNAL, f"{type(exc).__name__}: {exc}"))
        try:
            con.rollback()
        except Exception:
            pass
    finally:
        try:
            adaptador.close(sessao)
        except Exception as exc:
            permanentes.append((ErrorClass.INTERNAL, f"{type(exc).__name__}: {exc}"))

    if teto is not None and chamadas >= teto and (fila or fila_hidratacao or fila_refresh):
        truncado_por_teto = True
    # o que o teto cortou continua no proximo run; fila vazia limpa o cursor
    pendentes_por_chave = {req.key: req for req in (*fila_hidratacao, *fila_refresh) if req.key not in vistos}
    pendentes_por_chave.update(hidratacao_falha)
    pendentes = list(pendentes_por_chave.values())
    _gravar_hidratacao_pendente(con, source_id, run_id, pendentes)
    if refresh_after is not None:
        update_cursor(con, source_id, "hydration_refresh_after", str(refresh_after), run_id)

    counts = RunCounts(
        fetched=buscado,
        new=novo,
        changed=mudado,
        unchanged=inalterado,
        quarantined=quarentena,
    )
    con.execute(
        "UPDATE source_run SET fetched_count = ?, new_count = ?, changed_count = ?, "
        "unchanged_count = ?, quarantined_count = ? WHERE id = ?",
        (buscado, novo, mudado, inalterado, quarentena, run_id),
    )
    linha_sonda = con.execute("SELECT required_fields FROM source_probe WHERE source_id = ?", (source_id,)).fetchone()
    sondas = ()
    if linha_sonda is not None:
        try:
            campos = json.loads(linha_sonda[0] or "[]")
            if not isinstance(campos, list) or not all(isinstance(campo, str) for campo in campos):
                raise ValueError("required_fields invalido")
            efetivos = int(novo + mudado + inalterado + quarentena)
            base_declarada = max(total_declarado or 0, linhas_por_pagina)
            if capture_only:
                volume = None
            else:
                # declarado conta processos; efetivos soma anexos e contratos
                volume = base_declarada if base_declarada else efetivos
            sondas = tuple(
                evaluate_probes(
                    con,
                    run_id,
                    source_id,
                    primeiro_raw,
                    primeiro_tipo,
                    campos,
                    current_rows=volume,
                )
            )
        except Exception as exc:
            permanentes.append((ErrorClass.CONTRACT, f"sonda: {type(exc).__name__}: {exc}"))
    else:
        # sem linha em source_probe nao ha como afirmar saude: all(()) aprovaria tudo
        sondas = (ProbeResult("config", False, "sem sonda configurada"),)
        permanentes.append((ErrorClass.CONTRACT, "sem sonda configurada"))
    classes = {classe for classe, _ in permanentes}
    if ErrorClass.AUTH in classes or ErrorClass.INTERNAL in classes:
        # auth nunca produz ok_zero: 403 e incidente, nao ausencia de edital
        status = RunStatus.FAILED
    elif ErrorClass.CONTRACT in classes:
        status = RunStatus.SUSPECT
    elif ErrorClass.RATE_LIMITED in classes or ErrorClass.TRANSIENT in classes or ErrorClass.NOT_FOUND in classes:
        status = RunStatus.PARTIAL
    else:
        status = decide_status(counts, sondas)

    if truncado_por_teto and (fila or fila_hidratacao or fila_refresh):
        if status in (RunStatus.OK, RunStatus.OK_ZERO):
            status = RunStatus.PARTIAL
        if not any("teto max_calls_per_run" in msg for _, msg in permanentes):
            permanentes.append(
                (
                    ErrorClass.RATE_LIMITED,
                    f"teto max_calls_per_run atingido ({chamadas} chamadas), coleta incompleta",
                )
            )

    erro = "; ".join(mensagem for _, mensagem in permanentes) or None
    close_source_run(con, run_id, status, erro)
    if linha_sonda is not None or status in (RunStatus.SUSPECT, RunStatus.PARTIAL, RunStatus.FAILED):
        maybe_open_incident(con, source_id, status.value, sondas)
    # partial por teto ainda tem o total declarado confiavel da descoberta;
    # sem gravar, a sanidade do run seguinte comparava com respostas HTTP
    if status in (RunStatus.OK, RunStatus.OK_ZERO, RunStatus.PARTIAL) and not capture_only:
        if total_declarado is not None or linhas_por_pagina:
            update_cursor(
                con,
                source_id,
                "RowsCount",
                str(max(total_declarado or 0, linhas_por_pagina)),
                run_id,
            )
    if status == RunStatus.OK and not capture_only:
        if _semantica_exclusao(adaptador) == "absence":
            marcar_ausentes(con, source_id, run_id)
    con.commit()
    if status in (RunStatus.OK, RunStatus.OK_ZERO, RunStatus.PARTIAL) and not capture_only:
        falha_alerta = None
        try:
            gerar_alertas_do_run(con, source_id, run_id)
            con.commit()
        except Exception as exc:
            falha_alerta = f"alertas: {type(exc).__name__}: {exc}"
            try:
                con.rollback()
            except Exception:
                pass
        if falha_alerta is not None:
            # o status do run ja foi fechado; so o erro e anexado
            erro = f"{erro}; {falha_alerta}" if erro else falha_alerta
            con.execute("UPDATE source_run SET error = ? WHERE id = ?", (erro, run_id))
            con.commit()
    return RunOutcome(
        run_id=run_id,
        source_id=source_id,
        source_code=code,
        status=status.value,
        counts=counts,
        error=erro,
    )


def run_all(con: sqlite3.Connection, cfg: RunConfig) -> list[RunOutcome]:
    linhas = con.execute(
        """
        SELECT id, code, base_url, adapter_version_atual
        FROM source
        WHERE enabled = 1
        ORDER BY id
        """
    ).fetchall()
    resultados: list[RunOutcome] = []
    for linha in linhas:
        code = str(linha[1])
        if _adaptador_da_fonte(code, cfg) is None:
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
        source = SourceRecord(
            id=int(linha[0]),
            code=code,
            base_url=str(linha[2] or ""),
            adapter_version=str(linha[3] or ""),
        )
        resultados.append(run_source(con, source, cfg))
    return resultados
