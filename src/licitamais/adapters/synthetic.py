"""Adapter fixture sintetico (source_code='synthetic')."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from licitamais.types import (
    Capabilities,
    FetchedPage,
    FetchRequest,
    ItemRecord,
    NormalizedBatch,
    OrgRecord,
    ParseResult,
    PhaseRecord,
    PlanContext,
    ProcessRecord,
    QuarantineItem,
    Session,
    SourceConfig,
)

_ENDPOINT = "/synthetic/processos"

_BATCH_VAZIO = NormalizedBatch(
    orgs=(),
    processes=(),
    items=(),
    attachments=(),
    results=(),
    awards=(),
    contracts=(),
    phases=(),
)


def _raiz_projeto() -> Path:
    aqui = Path(__file__).resolve()
    try:
        return aqui.parents[3]
    except IndexError:
        return Path.cwd()


def load_fixture(name: str) -> bytes:
    base_nomes: list[str] = [name]
    if name.endswith(".json"):
        sem_ext = name[: -len(".json")]
        if sem_ext and sem_ext not in base_nomes:
            base_nomes.append(sem_ext)
    else:
        base_nomes.append(name + ".json")
    bases: list[Path] = []
    try:
        bases.append(_raiz_projeto() / "tests" / "golden" / "synthetic")
    except Exception:
        pass
    bases.append(Path.cwd() / "tests" / "golden" / "synthetic")
    vistos: set[str] = set()
    for base in bases:
        for candidato in base_nomes:
            chave = str(base / candidato)
            if chave in vistos:
                continue
            vistos.add(chave)
            caminho = base / candidato
            if caminho.is_file():
                return caminho.read_bytes()
    detalhe = ", ".join(base_nomes)
    raise FileNotFoundError(f"Fixture {detalhe} nao encontrada no diretorio synthetic.")


def _trecho(valor: object) -> str:
    try:
        return json.dumps(valor, ensure_ascii=False, sort_keys=True)[:500]
    except (TypeError, ValueError):
        return repr(valor)[:500]


def _quarentena(motivo: str, ponteiro: str | None, valor: object) -> QuarantineItem:
    return QuarantineItem(
        raw_excerpt=_trecho(valor),
        reason=motivo,
        pointer=ponteiro,
    )


def _resultado_erro(motivo: str) -> ParseResult:
    return ParseResult(
        batch=_BATCH_VAZIO,
        quarantine=(_quarentena(motivo, None, motivo),),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": 0},
        fatal=None,
    )


def _validar_registro(registro: dict) -> list[str]:
    falhas: list[str] = []
    identificador = registro.get("id")
    if not isinstance(identificador, str) or not identificador.strip():
        falhas.append("id")
    numero = registro.get("numero")
    if not isinstance(numero, str) or not numero.strip():
        falhas.append("numero")
    ano = registro.get("ano")
    if not isinstance(ano, int) or isinstance(ano, bool):
        falhas.append("ano")
    orgao = registro.get("orgao")
    if not isinstance(orgao, dict):
        falhas.append("orgao")
    else:
        cnpj = orgao.get("cnpj")
        if not isinstance(cnpj, str) or not cnpj.strip():
            falhas.append("orgao.cnpj")
        nome = orgao.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            falhas.append("orgao.nome")
    objeto = registro.get("objeto")
    if not isinstance(objeto, str) or not objeto.strip():
        falhas.append("objeto")
    fase = registro.get("fase")
    if not isinstance(fase, dict):
        falhas.append("fase")
    else:
        codigo = fase.get("codigo")
        if codigo is None or (isinstance(codigo, str) and not codigo.strip()):
            falhas.append("fase.codigo")
        rotulo = fase.get("label")
        if rotulo is None or (isinstance(rotulo, str) and not rotulo.strip()):
            falhas.append("fase.label")
    itens = registro.get("itens")
    if itens is not None and not isinstance(itens, list):
        falhas.append("itens")
    return falhas


def _texto_ou_nulo(valor: object) -> str | None:
    if isinstance(valor, str):
        return valor
    return None


def _processar_registro(
    registro: object,
    ponteiro: str,
    orgs: list[OrgRecord],
    processos: list[ProcessRecord],
    itens_saida: list[ItemRecord],
    fases: list[PhaseRecord],
    quarentena: list[QuarantineItem],
) -> None:
    if not isinstance(registro, dict):
        quarentena.append(_quarentena("registro nao e um objeto", ponteiro, registro))
        return
    falhas = _validar_registro(registro)
    if falhas:
        quarentena.append(
            _quarentena(
                "campos obrigatorios ausentes ou invalidos: " + ", ".join(falhas),
                ponteiro,
                registro,
            )
        )
        return
    pid = str(registro["id"])
    numero = str(registro["numero"])
    ano = registro["ano"]
    orgao = registro["orgao"]
    assert isinstance(orgao, dict)
    cnpj = str(orgao["cnpj"])
    nome = str(orgao["nome"])
    modalidade = registro.get("modalidade")
    if not isinstance(modalidade, dict):
        modalidade = {}
    mod_codigo = modalidade.get("codigo")
    mod_codigo_txt = str(mod_codigo) if mod_codigo is not None else None
    mod_desc = modalidade.get("descricao")
    if not isinstance(mod_desc, str):
        mod_desc = None
    objeto = str(registro["objeto"])
    fase = registro.get("fase")
    if not isinstance(fase, dict):
        fase = {}
    fase_codigo = str(fase.get("codigo"))
    fase_rotulo = str(fase.get("label"))
    publicado = _texto_ou_nulo(registro.get("publicado_em"))
    abertura = _texto_ou_nulo(registro.get("abertura_em"))
    atualizado_raw = registro.get("atualizado_em")
    atualizado = atualizado_raw if isinstance(atualizado_raw, str) else None
    orgs.append(
        OrgRecord(
            source_native_id=cnpj,
            attrs={"cnpj": cnpj, "name_raw": nome, "kind_hint": None},
        )
    )
    processos.append(
        ProcessRecord(
            source_native_id=pid,
            attrs={
                "number": numero,
                "year": ano,
                "modality_code": mod_codigo_txt,
                "modality_raw": mod_desc,
                "title": objeto,
                "object": objeto,
                "published_at_source": publicado,
                "opening_at_source": abertura,
                "source_updated_at_source": atualizado,
                "org_cnpj": cnpj,
                "org_nome": nome,
            },
        )
    )
    itens = registro.get("itens")
    if itens is None:
        itens = []
    assert isinstance(itens, list)
    for posicao, item in enumerate(itens):
        item_ponteiro = f"{ponteiro}.itens[{posicao}]"
        if not isinstance(item, dict):
            quarentena.append(_quarentena("item nao e um objeto", item_ponteiro, item))
            continue
        seq = item.get("seq")
        if not isinstance(seq, int) or isinstance(seq, bool):
            seq = posicao + 1
        descricao = item.get("descricao")
        quantidade = item.get("quantidade")
        unidade = item.get("unidade")
        v_unit = item.get("valor_unitario_estimado")
        v_total = item.get("valor_total_estimado")
        itens_saida.append(
            ItemRecord(
                source_native_id=f"{pid}:item:{seq}",
                attrs={
                    "process_id": None,
                    "processo_id": pid,
                    "lot_number": None,
                    "seq": seq,
                    "description": descricao if isinstance(descricao, str) else None,
                    "qty": quantidade if isinstance(quantidade, (int, float)) else None,
                    "unit": unidade if isinstance(unidade, str) else None,
                    "unit_price_estimated_cents": v_unit if isinstance(v_unit, int) else None,
                    "total_price_estimated_cents": v_total if isinstance(v_total, int) else None,
                },
            )
        )
    fases.append(
        PhaseRecord(
            source_native_id=pid,
            attrs={"phase_code": fase_codigo, "phase_label": fase_rotulo},
        )
    )


def _parse_dados(dados: dict) -> ParseResult:
    registros = dados.get("processos")
    if not isinstance(registros, list):
        return _resultado_erro("campo processos ausente ou nao-lista")
    orgs: list[OrgRecord] = []
    processos: list[ProcessRecord] = []
    itens_saida: list[ItemRecord] = []
    fases: list[PhaseRecord] = []
    quarentena: list[QuarantineItem] = []
    for idx, registro in enumerate(registros):
        _processar_registro(registro, f"processos[{idx}]", orgs, processos, itens_saida, fases, quarentena)
    malformado = dados.get("malformed")
    if isinstance(malformado, dict):
        _processar_registro(malformado, "malformed", orgs, processos, itens_saida, fases, quarentena)
    lote = NormalizedBatch(
        orgs=tuple(orgs),
        processes=tuple(processos),
        items=tuple(itens_saida),
        attachments=(),
        results=(),
        awards=(),
        contracts=(),
        phases=tuple(fases),
    )
    return ParseResult(
        batch=lote,
        quarantine=tuple(quarentena),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(registros)},
        fatal=None,
    )


def _request_descoberta() -> FetchRequest:
    return FetchRequest(
        endpoint=_ENDPOINT,
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


class SyntheticAdapter:
    source_code: str = "synthetic"
    adapter_version: str = "0.1.0"
    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "item", "phase"),
        hydration={"enabled": False, "trigger": "new_or_changed"},
        listing_order_stable=True,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={
            "min_delay_ms": 0,
            "jitter_ms": 0,
            "max_calls_per_run": 10,
            "max_concurrency": 1,
        },
        auth="none",
        probes={"rows_count_min_ratio": 0.85},
    )

    def open(self, cfg: SourceConfig) -> Session:
        return Session(
            source_code=self.source_code,
            dados={"base_url": cfg.base_url},
        )

    def close(self, s: Session) -> None:
        return None

    def plan(self, ctx: PlanContext) -> Iterator[FetchRequest]:
        yield _request_descoberta()

    def fetch(self, s: Session, req: FetchRequest) -> FetchedPage:
        corpo = load_fixture("processos.json")
        agora = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        return FetchedPage(
            request=req,
            status=200,
            headers={"content-type": "application/json; charset=utf-8"},
            body=corpo,
            fetched_at=agora,
            duration_ms=1,
        )

    def parse(self, raw: FetchedPage) -> ParseResult:
        try:
            texto = raw.body.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            return _resultado_erro(f"corpo nao e UTF-8 valido: {exc}")
        try:
            dados = json.loads(texto)
        except json.JSONDecodeError as exc:
            return _resultado_erro(f"payload nao e JSON valido: {exc}")
        if not isinstance(dados, dict):
            return _resultado_erro("raiz do payload nao e um objeto")
        return _parse_dados(dados)
