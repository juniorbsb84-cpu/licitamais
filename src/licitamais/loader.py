"""Loader transacional e idempotente do nucleo."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from .canonical import CaptureRecord
from .situacao import grupo_do_rotulo
from .types import (
    AttachmentRecord,
    AwardRecord,
    ContractRecord,
    ItemRecord,
    NormalizedBatch,
    OrgRecord,
    ProcessRecord,
    ResultRecord,
)


@dataclass(frozen=True)
class UpsertOutcome:
    id: int
    created: bool
    changed: bool


@dataclass(frozen=True)
class PhaseOutcome:
    id: int
    changed: bool


@dataclass(frozen=True)
class LoadResult:
    new: int
    changed: int
    unchanged: int
    quarantined: int


def _agora() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_padrao(valor: object) -> object:
    # attrs chegam congelados (MappingProxyType): sem isto viravam repr Python
    if isinstance(valor, Mapping):
        return dict(valor)
    if isinstance(valor, set | frozenset):
        return sorted(valor, key=str)
    return str(valor)


def _json(valor: object) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=_json_padrao)


_CHAVES_FORA_DO_HASH = frozenset({"link_processo", "link_edital"})


def _sem_links(dados: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dict(dados).items() if k not in _CHAVES_FORA_DO_HASH}


def canonical_hash(record: object) -> str:
    if hasattr(record, "source_native_id") and hasattr(record, "attrs"):
        valor: object = {
            "source_native_id": record.source_native_id,
            "attrs": _sem_links(dict(record.attrs)),
        }
    else:
        valor = record
    return hashlib.sha256(_json(valor).encode("utf-8")).hexdigest()


def _attrs(record: object) -> dict[str, Any]:
    return dict(getattr(record, "attrs", {}))


def _money_cents(value: object) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    raw = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in raw and "." in raw:
        raw = raw.replace(".", "").replace(",", ".") if raw.rfind(",") > raw.rfind(".") else raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif "." in raw:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", raw):
            raw = raw.replace(".", "")
    try:
        amount = Decimal(raw)
        return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError):
        return None


def _source_do_run(con: sqlite3.Connection, run_id: int) -> int:
    linha = con.execute("SELECT source_id FROM source_run WHERE id = ?", (run_id,)).fetchone()
    if linha is None:
        raise ValueError(f"source_run inexistente: {run_id}")
    return int(linha[0])


def _id(linha: sqlite3.Row | tuple[Any, ...] | None) -> int:
    if linha is None:
        raise RuntimeError("registro esperado nao encontrado")
    return int(linha[0])


def _nome_normalizado(nome: object) -> str:
    # tira o acento antes de filtrar: sem isso "Serviço" e "Servico" divergiam
    texto = unicodedata.normalize("NFKD", str(nome or "")).encode("ascii", "ignore").decode("ascii").lower()
    texto = re.sub(r"[^a-z0-9]+", " ", texto)
    texto = re.sub(r"\b(ltda|limitada|sa|s a|me|eireli)\b", "", texto)
    return " ".join(texto.split())


def _cnpj(cnpj: object) -> str | None:
    if cnpj is None:
        return None
    numeros = re.sub(r"\D", "", str(cnpj))
    return numeros or None


def _process_id(con: sqlite3.Connection, source_id: int, source_native_id: str) -> int | None:
    linha = con.execute(
        "SELECT id FROM process WHERE source_id = ? AND source_native_id = ?",
        (source_id, source_native_id),
    ).fetchone()
    return None if linha is None else int(linha[0])


def _parent_process_id(con: sqlite3.Connection, source_id: int, dados: dict[str, Any]) -> int | None:
    valor = dados.get("process_id")
    if isinstance(valor, int):
        return valor
    native_id = dados.get("process_native_id") or dados.get("processo_id") or valor
    if native_id is None:
        return None
    return _process_id(con, source_id, str(native_id))


def _referencia_processo(dados: dict[str, Any]) -> int | str | None:
    valor = dados.get("process_id")
    if isinstance(valor, int):
        return valor
    native_id = dados.get("process_native_id") or dados.get("processo_id") or valor
    if native_id is None:
        return None
    texto = str(native_id).strip()
    return texto or None


def _motivo_orfao_processo(con: sqlite3.Connection, source_id: int, dados: dict[str, Any]) -> str | None:
    ref = _referencia_processo(dados)
    if ref is None:
        return "sem referencia de processo"
    if isinstance(ref, int):
        existe = con.execute("SELECT 1 FROM process WHERE id = ?", (ref,)).fetchone()
        return None if existe is not None else f"processo inexistente id {ref}"
    if _process_id(con, source_id, ref) is not None:
        return None
    return f"processo inexistente para {ref}"


def _tem_processo(con: sqlite3.Connection, source_id: int) -> bool:
    linha = con.execute("SELECT 1 FROM process WHERE source_id = ? LIMIT 1", (source_id,)).fetchone()
    return linha is not None


def _referencia_resultado(dados: dict[str, Any]) -> int | str | None:
    valor = dados.get("result_id")
    if isinstance(valor, int):
        return valor
    native_id = dados.get("result_native_id") or valor
    if native_id is None:
        return None
    texto = str(native_id).strip()
    return texto or None


def _motivo_orfao_resultado(con: sqlite3.Connection, source_id: int, dados: dict[str, Any]) -> str | None:
    ref = _referencia_resultado(dados)
    if ref is None:
        return "sem referencia de resultado"
    if isinstance(ref, int):
        existe = con.execute("SELECT 1 FROM result WHERE id = ?", (ref,)).fetchone()
        return None if existe is not None else f"resultado inexistente id {ref}"
    linha = con.execute(
        "SELECT id FROM result WHERE source_id = ? AND source_native_id = ?",
        (source_id, ref),
    ).fetchone()
    if linha is not None:
        return None
    return f"resultado inexistente para {ref}"


def _org_id(con: sqlite3.Connection, source_id: int, dados: dict[str, Any]) -> int | None:
    valor = dados.get("org_id")
    if isinstance(valor, int):
        return valor
    native_id = dados.get("org_native_id") or dados.get("orgao_cnpj")
    if native_id is None:
        return None
    linha = con.execute(
        "SELECT org_id FROM organization_ref WHERE source_id = ? AND native_org_key = ?",
        (source_id, str(native_id)),
    ).fetchone()
    return None if linha is None else int(linha[0])


def _result_id(con: sqlite3.Connection, source_id: int, dados: dict[str, Any]) -> int | None:
    value = dados.get("result_id")
    if isinstance(value, int):
        return value
    native_id = dados.get("result_native_id") or value
    if native_id is None:
        return None
    row = con.execute(
        "SELECT id FROM result WHERE source_id = ? AND source_native_id = ?",
        (source_id, str(native_id)),
    ).fetchone()
    return None if row is None else int(row[0])


def upsert_process(con: sqlite3.Connection, run_id: int, p: ProcessRecord) -> UpsertOutcome:
    dados = _attrs(p)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    record_hash = str(dados.get("record_hash") or canonical_hash(p))
    anterior = con.execute(
        """
        SELECT id, record_hash, deleted_at
        FROM process
        WHERE source_id = ? AND source_native_id = ?
        """,
        (source_id, p.source_native_id),
    ).fetchone()

    campos = (
        _org_id(con, source_id, dados),
        dados.get("number"),
        dados.get("year"),
        dados.get("modality_code"),
        dados.get("modality_raw"),
        dados.get("title"),
        dados.get("object"),
        dados.get("published_at_source"),
        dados.get("opening_at_source"),
        dados.get("source_updated_at_source"),
        record_hash,
        _json(dados),
    )

    if anterior is None:
        cursor = con.execute(
            """
            INSERT INTO process (
                source_id, source_native_id, org_id, number, year, modality_code,
                modality_raw, title, object, published_at_source, opening_at_source,
                source_updated_at_source, record_hash, first_seen_run_id,
                last_seen_run_id, last_changed_run_id, attrs
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source_id,
                p.source_native_id,
                *campos[:-1],
                run_id,
                run_id,
                run_id,
                campos[-1],
            ),
        )
        return UpsertOutcome(cursor.lastrowid, True, True)

    process_id = int(anterior["id"])
    # reaparecer depois de tombstone e mudanca: gera alerta e run ok, nao ok_zero
    ressuscitou = anterior["deleted_at"] is not None
    mudou = anterior["record_hash"] != record_hash or ressuscitou
    if mudou:
        con.execute(
            """
            UPDATE process
            SET org_id = ?, number = ?, year = ?, modality_code = ?,
                modality_raw = ?, title = ?, object = ?,
                published_at_source = ?, opening_at_source = ?,
                source_updated_at_source = ?, record_hash = ?, attrs = ?,
                deleted_at = NULL, last_seen_run_id = ?,
                last_changed_run_id = ?
            WHERE id = ?
            """,
            (*campos, run_id, run_id, process_id),
        )
    else:
        _atualizar_links(con, process_id, dados)
        con.execute(
            """
            UPDATE process
            SET deleted_at = NULL, last_seen_run_id = ?
            WHERE id = ?
            """,
            (run_id, process_id),
        )
    return UpsertOutcome(process_id, False, mudou)


def _atualizar_links(con: sqlite3.Connection, process_id: int, dados: dict[str, Any]) -> None:
    """Grava link_processo/link_edital novos sem marcar mudanca (r51)."""
    linha = con.execute("SELECT attrs FROM process WHERE id = ?", (process_id,)).fetchone()
    try:
        atuais = json.loads(linha[0] or "{}") if linha is not None else {}
    except ValueError:
        atuais = {}
    if not isinstance(atuais, dict):
        atuais = {}
    novos = {k: dados[k] for k in _CHAVES_FORA_DO_HASH if k in dados}
    if not novos or all(atuais.get(k) == v for k, v in novos.items()):
        return
    atuais.update(novos)
    con.execute("UPDATE process SET attrs = ? WHERE id = ?", (_json(atuais), process_id))


def marcar_ausentes(con: sqlite3.Connection, source_id: int, run_id: int, carencia: int = 2) -> int:
    if int(carencia) < 1:
        return 0
    linhas = con.execute(
        """
        SELECT id FROM source_run
        WHERE source_id = ? AND status = 'ok' AND id <= ?
        ORDER BY id DESC LIMIT ?
        """,
        (int(source_id), int(run_id), int(carencia)),
    ).fetchall()
    if len(linhas) < int(carencia):
        return 0
    corte = int(linhas[-1][0])
    cursor = con.execute(
        """
        UPDATE process
        SET deleted_at = ?
        WHERE source_id = ? AND deleted_at IS NULL AND last_seen_run_id < ?
        """,
        (_agora(), int(source_id), corte),
    )
    return int(cursor.rowcount)


def upsert_phase(
    con: sqlite3.Connection,
    run_id: int,
    process_id: int,
    new_phase_code: str,
    new_phase_label: str,
    now: str,
) -> PhaseOutcome:
    atual = con.execute(
        """
        SELECT id, phase_code
        FROM phase_event
        WHERE process_id = ? AND ended_at IS NULL
        """,
        (process_id,),
    ).fetchone()

    if atual is not None and atual["phase_code"] == new_phase_code:
        con.execute(
            """
            UPDATE phase_event
            SET phase_label = ?, confirm_count = confirm_count + 1,
                last_seen_at = ?, last_seen_run_id = ?
            WHERE id = ?
            """,
            (new_phase_label, now, run_id, atual["id"]),
        )
        con.execute(
            """
            UPDATE process
            SET phase_current_code = ?, phase_current_label = ?, phase_current_group = ?
            WHERE id = ?
            """,
            (new_phase_code, new_phase_label, _grupo(new_phase_label), process_id),
        )
        return PhaseOutcome(int(atual["id"]), False)

    if atual is not None:
        con.execute(
            """
            UPDATE phase_event
            SET ended_at = ?, ended_run_id = ?
            WHERE id = ?
            """,
            (now, run_id, atual["id"]),
        )

    cursor = con.execute(
        """
        INSERT INTO phase_event (
            process_id, phase_code, phase_label, started_run_id, started_at,
            confirm_count, first_seen_at, last_seen_at, last_seen_run_id
        ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
        """,
        (process_id, new_phase_code, new_phase_label, run_id, now, now, now, run_id),
    )
    con.execute(
        """
        UPDATE process
        SET phase_current_code = ?, phase_current_label = ?, phase_current_group = ?
        WHERE id = ?
        """,
        (new_phase_code, new_phase_label, _grupo(new_phase_label), process_id),
    )
    return PhaseOutcome(cursor.lastrowid, True)


def upsert_organization(con: sqlite3.Connection, run_id: int, org: OrgRecord) -> int:
    dados = _attrs(org)
    cnpj = _cnpj(dados.get("cnpj"))
    if cnpj is not None:
        encontrada = con.execute("SELECT id FROM organization WHERE cnpj = ?", (cnpj,)).fetchone()
        if encontrada is not None:
            return _id(encontrada)
    else:
        # sem CNPJ a identidade e o nome normalizado (T23: antes duplicava a cada run)
        nome_norm = _nome_normalizado(dados.get("name_raw"))
        if nome_norm:
            encontrada = con.execute(
                "SELECT id FROM organization WHERE cnpj IS NULL AND name_norm = ? ORDER BY id LIMIT 1",
                (nome_norm,),
            ).fetchone()
            if encontrada is not None:
                return _id(encontrada)

    cursor = con.execute(
        """
        INSERT INTO organization (cnpj, name_raw, name_norm, kind_hint, attrs)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            cnpj,
            str(dados.get("name_raw") or ""),
            _nome_normalizado(dados.get("name_raw")),
            dados.get("kind_hint"),
            _json(dados),
        ),
    )
    return cursor.lastrowid


def _fornecedor_org_id(con: sqlite3.Connection, run_id: int, dados: Mapping[str, Any]) -> int | None:
    """Liga fornecedor a organization: CNPJ quando a fonte publica, senao nome normalizado."""
    if dados.get("supplier_org_id") is not None:
        return dados.get("supplier_org_id")
    cnpj = _cnpj(dados.get("supplier_cnpj"))
    nome = dados.get("supplier_name_raw")
    if cnpj is None and not _nome_normalizado(nome):
        return None
    org = OrgRecord(cnpj or str(nome), {"cnpj": cnpj, "name_raw": nome, "kind_hint": "fornecedor"})
    return upsert_organization(con, run_id, org)


def upsert_organization_ref(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    native_org_key: str,
    org_id: int,
) -> None:
    con.execute(
        """
        INSERT INTO organization_ref (
            source_id, native_org_key, org_id, first_seen_run_id, last_seen_run_id
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id, native_org_key) DO UPDATE SET
            org_id = excluded.org_id,
            last_seen_run_id = excluded.last_seen_run_id
        """,
        (source_id, native_org_key, org_id, run_id, run_id),
    )


def _rotulo_fase(dados: Mapping[str, Any]) -> str | None:
    """Situacao que a fonte publica; adaptadores usam phase_label, fase ou phase."""
    for chave in ("phase_label", "fase", "phase"):
        valor = dados.get(chave)
        if isinstance(valor, str) and valor.strip():
            return valor.strip()
    return None


def _grupo(rotulo):
    return grupo_do_rotulo(rotulo) or "-"


def _garantir_orgao(con: sqlite3.Connection, run_id: int, source_id: int, dados: Mapping[str, Any]) -> int | None:
    """Orgao comprador vem so em attrs (org_native_id + nome); cria organization e ref se faltar."""
    native = dados.get("org_native_id")
    if not native:
        return None
    linha = con.execute(
        "SELECT org_id FROM organization_ref WHERE source_id = ? AND native_org_key = ?",
        (source_id, str(native)),
    ).fetchone()
    if linha is not None:
        return int(linha[0])
    nome = dados.get("org_nome") or dados.get("nome_filial") or dados.get("entidade") or native
    org = OrgRecord(str(native), {"cnpj": dados.get("org_cnpj"), "name_raw": nome, "kind_hint": "comprador"})
    org_id = upsert_organization(con, run_id, org)
    upsert_organization_ref(con, run_id, source_id, str(native), org_id)
    return org_id


def _aplicar_fase(con: sqlite3.Connection, run_id: int, process_id: int, rotulo: str, agora: str) -> None:
    upsert_phase(con, run_id, process_id, _nome_normalizado(rotulo) or rotulo, rotulo, agora)


def backfill_situacao_orgao(con: sqlite3.Connection, run_id: int) -> int:
    """Preenche situacao e orgao de processos gravados antes do r46. Idempotente."""
    linhas = con.execute(
        """
        SELECT id, source_id, attrs, org_id, phase_current_label, phase_current_group FROM process
        WHERE (phase_current_label IS NULL AND COALESCE(json_extract(attrs, '$.phase_label'),
                   json_extract(attrs, '$.fase'), json_extract(attrs, '$.phase')) IS NOT NULL)
           OR (org_id IS NULL AND json_extract(attrs, '$.org_native_id') IS NOT NULL)
           OR (phase_current_label IS NOT NULL AND phase_current_group IS NULL)
        """
    ).fetchall()
    agora = _agora()
    alterados = 0
    for linha in linhas:
        dados = json.loads(linha["attrs"] or "{}")
        mudou = False
        if linha["org_id"] is None:
            org_id = _garantir_orgao(con, run_id, int(linha["source_id"]), dados)
            if org_id is not None:
                con.execute("UPDATE process SET org_id = ? WHERE id = ?", (org_id, linha["id"]))
                mudou = True
        rotulo = _rotulo_fase(dados)
        if linha["phase_current_label"] is None and rotulo:
            _aplicar_fase(con, run_id, int(linha["id"]), rotulo, agora)
            mudou = True
        if linha["phase_current_label"] is not None and linha["phase_current_group"] is None:
            con.execute(
                "UPDATE process SET phase_current_group = ? WHERE id = ?",
                (_grupo(linha["phase_current_label"]), linha["id"]),
            )
            mudou = True
        alterados += int(mudou)
    return alterados


def _upsert_simples(
    con: sqlite3.Connection,
    tabela: str,
    run_id: int,
    source_id: int,
    native_id: str,
    valores: dict[str, Any],
) -> UpsertOutcome:
    anterior = con.execute(
        f"SELECT id, {', '.join(valores)}, attrs FROM {tabela} WHERE source_id = ? AND source_native_id = ?",
        (source_id, native_id),
    ).fetchone()
    if anterior is not None:
        mudou = any(anterior[coluna] != valor for coluna, valor in valores.items()) or anterior["attrs"] != _json(
            valores
        )
        if mudou:
            atribuicoes = ", ".join(f"{coluna} = ?" for coluna in valores)
            con.execute(
                f"UPDATE {tabela} SET {atribuicoes}, attrs = ?, last_seen_run_id = ?, "
                "last_changed_run_id = ?, deleted_at = NULL WHERE id = ?",
                (*valores.values(), _json(valores), run_id, run_id, anterior[0]),
            )
        else:
            con.execute(
                f"UPDATE {tabela} SET last_seen_run_id = ?, deleted_at = NULL WHERE id = ?",
                (run_id, anterior[0]),
            )
        return UpsertOutcome(int(anterior[0]), False, mudou)

    colunas = [
        "source_id",
        "source_native_id",
        *valores,
        "first_seen_run_id",
        "last_seen_run_id",
        "last_changed_run_id",
        "attrs",
    ]
    parametros = [source_id, native_id, *valores.values(), run_id, run_id, run_id, _json(valores)]
    cursor = con.execute(
        f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join('?' for _ in colunas)})",
        parametros,
    )
    return UpsertOutcome(cursor.lastrowid, True, True)


def _carregar_item(con: sqlite3.Connection, run_id: int, item: ItemRecord) -> UpsertOutcome:
    dados = _attrs(item)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    valores = {
        "process_id": _parent_process_id(con, source_id, dados),
        "lot_number": dados.get("lot_number"),
        "seq": dados.get("seq"),
        "description": dados.get("description"),
        "qty": dados.get("qty"),
        "unit": dados.get("unit"),
        "unit_price_estimated_cents": dados.get("unit_price_estimated_cents"),
        "total_price_estimated_cents": dados.get("total_price_estimated_cents"),
    }
    return _upsert_simples(con, "item", run_id, source_id, item.source_native_id, valores)


def upsert_item(con: sqlite3.Connection, run_id: int, item: ItemRecord) -> int:
    return _carregar_item(con, run_id, item).id


def _carregar_attachment(con: sqlite3.Connection, run_id: int, att: AttachmentRecord) -> UpsertOutcome:
    dados = _attrs(att)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    valores = {
        "process_id": _parent_process_id(con, source_id, dados),
        "kind": dados.get("kind"),
        "url_download": dados.get("url_download"),
        "filename": dados.get("filename"),
        "mime": dados.get("mime"),
        "size_bytes": dados.get("size_bytes"),
        "sha256": dados.get("sha256"),
        "local_path": dados.get("local_path"),
        "published_at_source": dados.get("published_at_source"),
        "collected_at": dados.get("collected_at"),
    }
    anterior = con.execute(
        "SELECT id, process_id, kind, url_download, filename, mime, size_bytes, "
        "sha256, local_path, published_at_source, collected_at, attrs "
        "FROM attachment WHERE source_id = ? AND source_ref = ?",
        (source_id, att.source_native_id),
    ).fetchone()
    if anterior is not None:
        mudou = any(anterior[coluna] != valor for coluna, valor in valores.items()) or anterior["attrs"] != _json(dados)
        if mudou:
            atribuicoes = ", ".join(f"{coluna} = ?" for coluna in valores)
            con.execute(
                f"UPDATE attachment SET {atribuicoes}, attrs = ?, last_seen_run_id = ?, "
                "last_changed_run_id = ?, deleted_at = NULL WHERE id = ?",
                (*valores.values(), _json(dados), run_id, run_id, anterior[0]),
            )
        else:
            con.execute(
                "UPDATE attachment SET last_seen_run_id = ?, deleted_at = NULL WHERE id = ?",
                (run_id, anterior[0]),
            )
        return UpsertOutcome(int(anterior[0]), False, mudou)
    cursor = con.execute(
        """
        INSERT INTO attachment (
            source_id, source_ref, process_id, kind, url_download, filename, mime,
            size_bytes, sha256, local_path, published_at_source, collected_at,
            first_seen_run_id, last_seen_run_id, last_changed_run_id, attrs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            att.source_native_id,
            *valores.values(),
            run_id,
            run_id,
            run_id,
            _json(dados),
        ),
    )
    return UpsertOutcome(cursor.lastrowid, True, True)


def upsert_attachment(con: sqlite3.Connection, run_id: int, att: AttachmentRecord) -> int:
    return _carregar_attachment(con, run_id, att).id


def _carregar_result(con: sqlite3.Connection, run_id: int, result: ResultRecord) -> UpsertOutcome:
    dados = _attrs(result)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    valores = {
        "process_id": _parent_process_id(con, source_id, dados),
        "type": dados.get("type"),
        "decided_at_source": dados.get("decided_at_source"),
        "value_total_cents": dados.get("value_total_cents"),
        "value_total_raw": dados.get("value_total_raw"),
    }
    anterior = con.execute(
        "SELECT id, process_id, type, decided_at_source, value_total_cents, "
        "value_total_raw, attrs "
        "FROM result WHERE source_id = ? AND source_native_id = ?",
        (source_id, result.source_native_id),
    ).fetchone()
    if anterior is not None:
        mudou = any(anterior[coluna] != valor for coluna, valor in valores.items()) or anterior["attrs"] != _json(dados)
        if mudou:
            atribuicoes = ", ".join(f"{coluna} = ?" for coluna in valores)
            con.execute(
                f"UPDATE result SET {atribuicoes}, attrs = ?, last_seen_run_id = ?, "
                "last_changed_run_id = ? WHERE id = ?",
                (*valores.values(), _json(dados), run_id, run_id, anterior[0]),
            )
        else:
            con.execute(
                "UPDATE result SET last_seen_run_id = ? WHERE id = ?",
                (run_id, anterior[0]),
            )
        return UpsertOutcome(int(anterior[0]), False, mudou)
    cursor = con.execute(
        """
        INSERT INTO result (
            source_id, source_native_id, process_id, type, decided_at_source,
            value_total_cents, value_total_raw, first_seen_run_id,
            last_seen_run_id, last_changed_run_id, attrs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            result.source_native_id,
            *valores.values(),
            run_id,
            run_id,
            run_id,
            _json(dados),
        ),
    )
    return UpsertOutcome(cursor.lastrowid, True, True)


def upsert_result(con: sqlite3.Connection, run_id: int, result: ResultRecord) -> int:
    return _carregar_result(con, run_id, result).id


def _carregar_award(con: sqlite3.Connection, run_id: int, award: AwardRecord) -> UpsertOutcome:
    dados = _attrs(award)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    dados["supplier_org_id"] = _fornecedor_org_id(con, run_id, dados)
    result_id = _result_id(con, source_id, dados)
    amount_cents = dados.get("amount_cents")
    if amount_cents is None:
        amount_cents = _money_cents(dados.get("amount_raw") or dados.get("valor"))
    process_id = _parent_process_id(con, source_id, dados)
    qty_awarded = dados.get("qty_awarded")
    identidade = (
        result_id,
        dados.get("item_id"),
        dados.get("supplier_org_id"),
        dados.get("supplier_name_raw") or "",
    )
    anterior = con.execute(
        """
        SELECT id, process_id, amount_cents, qty_awarded
        FROM award
        WHERE result_id = ?
          AND COALESCE(item_id, 0) = COALESCE(?, 0)
          AND COALESCE(supplier_org_id, supplier_name_raw) =
              COALESCE(?, ?)
        ORDER BY id DESC
        LIMIT 1
        """,
        identidade,
    ).fetchone()
    if anterior is not None and (
        anterior["process_id"] == process_id
        and anterior["amount_cents"] == amount_cents
        and anterior["qty_awarded"] == qty_awarded
    ):
        con.execute(
            "UPDATE award SET last_seen_run_id = ? WHERE id = ?",
            (run_id, anterior["id"]),
        )
        return UpsertOutcome(int(anterior["id"]), False, False)

    cursor = con.execute(
        """
        INSERT INTO award (
            result_id, process_id, item_id, supplier_org_id, supplier_name_raw,
            amount_cents, amount_raw, qty_awarded, qty_awarded_raw, supersedes_id,
            first_seen_run_id, last_seen_run_id, last_changed_run_id, attrs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result_id,
            process_id,
            dados.get("item_id"),
            dados.get("supplier_org_id"),
            dados.get("supplier_name_raw") or "",
            amount_cents,
            dados.get("amount_raw") or dados.get("valor"),
            qty_awarded,
            dados.get("qty_awarded_raw"),
            None if anterior is None else anterior["id"],
            run_id,
            run_id,
            run_id,
            _json(dados),
        ),
    )
    return UpsertOutcome(cursor.lastrowid, anterior is None, anterior is not None)


def append_award(con: sqlite3.Connection, run_id: int, award: AwardRecord) -> int:
    return _carregar_award(con, run_id, award).id


def _escapar_like(texto: str) -> str:
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _carregar_contract(con: sqlite3.Connection, run_id: int, contract: ContractRecord) -> UpsertOutcome:
    dados = _attrs(contract)
    source_id = int(dados.get("source_id") or _source_do_run(con, run_id))
    dados["supplier_org_id"] = _fornecedor_org_id(con, run_id, dados)
    value_cents = dados.get("value_cents")
    if value_cents is None:
        value_cents = _money_cents(dados.get("value_raw") or dados.get("valor_global"))
    valores = {
        "process_id": _parent_process_id(con, source_id, dados),
        "number": dados.get("number"),
        "supplier_org_id": dados.get("supplier_org_id"),
        "supplier_name_raw": dados.get("supplier_name_raw") or "",
        "value_cents": value_cents,
        "signed_at_source": dados.get("signed_at_source"),
        "vigency_start": dados.get("vigency_start"),
        "vigency_end": dados.get("vigency_end"),
    }
    base = contract.source_native_id
    prefixo = base + "#retificacao-"
    padrao = _escapar_like(prefixo) + "%"
    candidatas = con.execute(
        """
        SELECT id, source_native_id, process_id, number, supplier_org_id,
               supplier_name_raw, value_cents, signed_at_source, vigency_start,
               vigency_end
        FROM contract
        WHERE source_id = ?
          AND (source_native_id = ? OR source_native_id LIKE ? ESCAPE '\\')
        ORDER BY id DESC
        """,
        (source_id, base, padrao),
    ).fetchall()
    anterior = None
    for linha in candidatas:
        nativa = str(linha["source_native_id"])
        if nativa == base:
            anterior = linha
            break
        if nativa.startswith(prefixo):
            sufixo = nativa[len(prefixo) :]
            if sufixo.isdigit():
                anterior = linha
                break
    if anterior is not None and all(anterior[coluna] == valor for coluna, valor in valores.items()):
        con.execute("UPDATE contract SET last_seen_run_id = ? WHERE id = ?", (run_id, anterior["id"]))
        return UpsertOutcome(int(anterior["id"]), False, False)

    native_id = base
    if anterior is not None:
        native_id = f"{base}#retificacao-{run_id}"
    cursor = con.execute(
        """
        INSERT INTO contract (
            source_id, source_native_id, process_id, number, supplier_org_id,
            supplier_name_raw, value_cents, value_raw, signed_at_source,
            vigency_start, vigency_end, supersedes_id, first_seen_run_id,
            last_seen_run_id, last_changed_run_id, attrs
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source_id,
            native_id,
            valores["process_id"],
            valores["number"],
            valores["supplier_org_id"],
            valores["supplier_name_raw"],
            valores["value_cents"],
            dados.get("value_raw") or dados.get("valor_global"),
            valores["signed_at_source"],
            valores["vigency_start"],
            valores["vigency_end"],
            None if anterior is None else anterior["id"],
            run_id,
            run_id,
            run_id,
            _json(dados),
        ),
    )
    return UpsertOutcome(cursor.lastrowid, anterior is None, anterior is not None)


def append_contract(con: sqlite3.Connection, run_id: int, contract: ContractRecord) -> int:
    return _carregar_contract(con, run_id, contract).id


def add_process_alias(
    con: sqlite3.Connection,
    canonical_id: int,
    member_id: int,
    rule: str,
    run_id: int,
) -> None:
    con.execute(
        """
        INSERT INTO process_alias (
            canonical_process_id, member_process_id, match_rule, created_run_id, created_at
        ) VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(canonical_process_id, member_process_id) DO NOTHING
        """,
        (canonical_id, member_id, rule, run_id, _agora()),
    )


def insert_payload(con: sqlite3.Connection, sha256: str, body: bytes) -> bool:
    cursor = con.execute(
        """
        INSERT INTO payload_store (
            sha256, storage_kind, size_bytes, inline_bytes, created_at
        ) VALUES (?, 'inline', ?, ?, ?)
        ON CONFLICT(sha256) DO NOTHING
        """,
        (sha256, len(body), body, _agora()),
    )
    return cursor.rowcount == 1


def record_capture(con: sqlite3.Connection, cap: CaptureRecord) -> int:
    insert_payload(con, cap.sha256, cap.body)
    payload_id = _id(con.execute("SELECT id FROM payload_store WHERE sha256 = ?", (cap.sha256,)).fetchone())
    anterior = con.execute(
        """
        SELECT 1
        FROM raw_capture
        WHERE source_id = ? AND endpoint = ? AND url = ? AND sha256 = ?
        LIMIT 1
        """,
        (cap.source_id, cap.endpoint, cap.url, cap.sha256),
    ).fetchone()
    cursor = con.execute(
        """
        INSERT INTO raw_capture (
            source_id, source_run_id, endpoint, url, payload_id, sha256, size_bytes,
            http_status, content_type, captured_at, unchanged
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            cap.source_id,
            cap.source_run_id,
            cap.endpoint,
            cap.url,
            payload_id,
            cap.sha256,
            len(cap.body),
            cap.http_status,
            cap.content_type,
            cap.captured_at,
            1 if anterior is not None else 0,
        ),
    )
    return cursor.lastrowid


def quarantine_record(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    excerpt: bytes,
    reason: str,
    pointer: str,
) -> None:
    con.execute(
        """
        INSERT INTO parse_quarantine (
            source_id, source_run_id, native_ref, error, raw_excerpt, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (source_id, run_id, pointer, reason, excerpt.decode("utf-8", "replace"), _agora()),
    )


def _quarentena_registro(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    record: object,
    motivo: str,
) -> None:
    dados = _attrs(record)
    trecho = _json({"source_native_id": record.source_native_id, "attrs": dados}).encode("utf-8")
    quarantine_record(con, run_id, source_id, trecho, motivo, str(record.source_native_id))


def update_cursor(con: sqlite3.Connection, source_id: int, key: str, value: str, run_id: int) -> None:
    con.execute(
        """
        INSERT INTO sync_cursor (source_id, cursor_key, value, updated_run_id, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(source_id, cursor_key) DO UPDATE SET
            value = excluded.value,
            updated_run_id = excluded.updated_run_id,
            updated_at = excluded.updated_at
        """,
        (source_id, key, value, run_id, _agora()),
    )


def increment_run_counts(
    con: sqlite3.Connection,
    run_id: int,
    *,
    fetched: int,
    new: int,
    changed: int,
    unchanged: int,
    quarantined: int,
) -> None:
    con.execute(
        """
        UPDATE source_run
        SET fetched_count = fetched_count + ?,
            new_count = new_count + ?,
            changed_count = changed_count + ?,
            unchanged_count = unchanged_count + ?,
            quarantined_count = quarantined_count + ?
        WHERE id = ?
        """,
        (fetched, new, changed, unchanged, quarantined, run_id),
    )


def _lote_totalmente_vazio(batch: NormalizedBatch) -> bool:
    return (
        not batch.orgs
        and not batch.processes
        and not batch.items
        and not batch.attachments
        and not batch.results
        and not batch.awards
        and not batch.contracts
        and not batch.phases
    )


def _ref_processo_contrato(dados: dict[str, Any]) -> str | None:
    for chave in ("process_native_id", "processo_id"):
        valor = dados.get(chave)
        if valor is not None and str(valor).strip() != "":
            return str(valor)
    valor = dados.get("process_id")
    if isinstance(valor, str) and valor.strip() != "":
        return valor.strip()
    return None


def load_batch(
    con: sqlite3.Connection,
    run_id: int,
    source_id: int,
    batch: NormalizedBatch,
) -> LoadResult:
    if _lote_totalmente_vazio(batch):
        raise ValueError("lote totalmente vazio: nada para carregar")
    novo = 0
    mudado = 0
    inalterado = 0
    quarantined = 0
    agora = _agora()

    con.execute("SAVEPOINT load_batch")
    try:
        for org in batch.orgs:
            org_id = upsert_organization(con, run_id, org)
            upsert_organization_ref(con, run_id, source_id, org.source_native_id, org_id)

        for process in batch.processes:
            _garantir_orgao(con, run_id, source_id, _attrs(process))
            resultado = upsert_process(con, run_id, process)
            rotulo = _rotulo_fase(_attrs(process))
            if rotulo:
                _aplicar_fase(con, run_id, resultado.id, rotulo, agora)
            if resultado.created:
                novo += 1
            elif resultado.changed:
                mudado += 1
            else:
                inalterado += 1

        for phase in batch.phases:
            process_id = _process_id(con, source_id, phase.source_native_id)
            if process_id is not None:
                dados = _attrs(phase)
                upsert_phase(
                    con,
                    run_id,
                    process_id,
                    str(dados.get("phase_code") or ""),
                    str(dados.get("phase_label") or ""),
                    agora,
                )
            else:
                dados = _attrs(phase)
                motivo = f"fase orfa: processo inexistente para {phase.source_native_id}"
                trecho = _json({"source_native_id": phase.source_native_id, "attrs": dados}).encode("utf-8")
                quarantine_record(con, run_id, source_id, trecho, motivo, phase.source_native_id)
                quarantined += 1

        for item in batch.items:
            motivo = _motivo_orfao_processo(con, source_id, _attrs(item))
            if motivo == "sem referencia de processo" and not _tem_processo(con, source_id):
                motivo = None
            if motivo is not None:
                _quarentena_registro(
                    con,
                    run_id,
                    source_id,
                    item,
                    f"item orfao: {motivo} para item {item.source_native_id}",
                )
                quarantined += 1
                continue
            desfecho = _carregar_item(con, run_id, item)
            if desfecho.created:
                novo += 1
            elif desfecho.changed:
                mudado += 1
            else:
                inalterado += 1
        for attachment in batch.attachments:
            motivo = _motivo_orfao_processo(con, source_id, _attrs(attachment))
            if motivo == "sem referencia de processo" and not _tem_processo(con, source_id):
                motivo = None
            if motivo is not None:
                _quarentena_registro(
                    con,
                    run_id,
                    source_id,
                    attachment,
                    f"attachment orfao: {motivo} para attachment {attachment.source_native_id}",
                )
                quarantined += 1
                continue
            desfecho = _carregar_attachment(con, run_id, attachment)
            if desfecho.created:
                novo += 1
            elif desfecho.changed:
                mudado += 1
            else:
                inalterado += 1
        for result in batch.results:
            motivo = _motivo_orfao_processo(con, source_id, _attrs(result))
            if motivo == "sem referencia de processo" and not _tem_processo(con, source_id):
                motivo = None
            if motivo is not None:
                _quarentena_registro(
                    con,
                    run_id,
                    source_id,
                    result,
                    f"resultado orfao: {motivo} para resultado {result.source_native_id}",
                )
                quarantined += 1
                continue
            desfecho = _carregar_result(con, run_id, result)
            if desfecho.created:
                novo += 1
            elif desfecho.changed:
                mudado += 1
            else:
                inalterado += 1
        for award in batch.awards:
            dados_aw = _attrs(award)
            bruto_aw = dados_aw.get("amount_raw") or dados_aw.get("valor")
            if dados_aw.get("amount_cents") is None and bruto_aw is not None and str(bruto_aw).strip() != "":
                if _money_cents(bruto_aw) is None:
                    motivo = f"valor monetario ilegivel: {bruto_aw!r} para award {award.source_native_id}"
                    trecho = _json({"source_native_id": award.source_native_id, "attrs": dados_aw}).encode("utf-8")
                    quarantine_record(con, run_id, source_id, trecho, motivo, award.source_native_id)
                    quarantined += 1
                    continue
            motivo = _motivo_orfao_resultado(con, source_id, dados_aw)
            if motivo is None:
                motivo = _motivo_orfao_processo(con, source_id, dados_aw)
                if motivo == "sem referencia de processo" and not _tem_processo(con, source_id):
                    motivo = None
            if motivo is not None:
                _quarentena_registro(
                    con,
                    run_id,
                    source_id,
                    award,
                    f"award orfao: {motivo} para award {award.source_native_id}",
                )
                quarantined += 1
                continue
            desfecho = _carregar_award(con, run_id, award)
            if desfecho.created:
                novo += 1
            elif desfecho.changed:
                mudado += 1
            else:
                inalterado += 1
        for contract in batch.contracts:
            dados_ctr = _attrs(contract)
            bruto_ctr = dados_ctr.get("value_raw") or dados_ctr.get("valor_global")
            if dados_ctr.get("value_cents") is None and bruto_ctr is not None and str(bruto_ctr).strip() != "":
                if _money_cents(bruto_ctr) is None:
                    motivo = f"valor monetario ilegivel: {bruto_ctr!r} para contrato {contract.source_native_id}"
                    trecho = _json({"source_native_id": contract.source_native_id, "attrs": dados_ctr}).encode("utf-8")
                    quarantine_record(con, run_id, source_id, trecho, motivo, contract.source_native_id)
                    quarantined += 1
                    continue
            pid_int = dados_ctr.get("process_id")
            if isinstance(pid_int, int):
                existe = con.execute("SELECT 1 FROM process WHERE id = ?", (pid_int,)).fetchone()
                if existe is None:
                    motivo = (
                        f"contrato orfao: processo inexistente id {pid_int} para contrato {contract.source_native_id}"
                    )
                    trecho = _json({"source_native_id": contract.source_native_id, "attrs": dados_ctr}).encode("utf-8")
                    quarantine_record(con, run_id, source_id, trecho, motivo, contract.source_native_id)
                    quarantined += 1
                    continue
                desfecho = _carregar_contract(con, run_id, contract)
                if desfecho.created:
                    novo += 1
                elif desfecho.changed:
                    mudado += 1
                else:
                    inalterado += 1
                continue
            ref = _ref_processo_contrato(dados_ctr)
            if ref is None:
                if not _tem_processo(con, source_id):
                    desfecho = _carregar_contract(con, run_id, contract)
                    if desfecho.created:
                        novo += 1
                    elif desfecho.changed:
                        mudado += 1
                    else:
                        inalterado += 1
                    continue
                motivo = f"contrato orfao: sem referencia de processo para contrato {contract.source_native_id}"
                trecho = _json({"source_native_id": contract.source_native_id, "attrs": dados_ctr}).encode("utf-8")
                quarantine_record(con, run_id, source_id, trecho, motivo, contract.source_native_id)
                quarantined += 1
                continue
            if _process_id(con, source_id, ref) is None:
                motivo = f"contrato orfao: processo inexistente para {ref} contrato {contract.source_native_id}"
                trecho = _json({"source_native_id": contract.source_native_id, "attrs": dados_ctr}).encode("utf-8")
                quarantine_record(con, run_id, source_id, trecho, motivo, contract.source_native_id)
                quarantined += 1
                continue
            desfecho = _carregar_contract(con, run_id, contract)
            if desfecho.created:
                novo += 1
            elif desfecho.changed:
                mudado += 1
            else:
                inalterado += 1

        buscados = (
            len(batch.orgs)
            + len(batch.processes)
            + len(batch.items)
            + len(batch.attachments)
            + len(batch.results)
            + len(batch.awards)
            + len(batch.contracts)
            + len(batch.phases)
        )
        increment_run_counts(
            con,
            run_id,
            fetched=buscados,
            new=novo,
            changed=mudado,
            unchanged=inalterado,
            quarantined=quarantined,
        )
        con.execute("RELEASE SAVEPOINT load_batch")
    except Exception:
        con.execute("ROLLBACK TO SAVEPOINT load_batch")
        con.execute("RELEASE SAVEPOINT load_batch")
        raise

    return LoadResult(novo, mudado, inalterado, quarantined)
