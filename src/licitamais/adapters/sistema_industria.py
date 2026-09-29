"""Adapter do Sistema Industria (source_code='sistema_industria').

Fonte de duas fases com handshake CSRF: a listagem (estagio 1) emite os
processos e a hidratacao busca itens/lotes. A ordem da listagem e instavel,
entao a varredura e completa, sem early-stop. Decode UTF-8 estrito.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime

from licitamais.adapter import Adapter
from licitamais.sessions.csrf import (
    CsrfSession,
    CsrfSessionManager,
    handshake_3_steps,
)
from licitamais.types import (
    AwardRecord,
    Capabilities,
    FetchedPage,
    FetchRequest,
    ItemRecord,
    NormalizedBatch,
    ParseResult,
    PlanContext,
    ProcessRecord,
    QuarantineItem,
    ResultRecord,
    SourceConfig,
)

USER_AGENT = "licitamais-sistema-industria/0.1.0 (contato: operador@exemplo.test)"
TTL_TOKEN_SEGUNDOS = 600
TIMEOUT_SEGUNDOS = 30

_CAMPOS_OBRIGATORIOS = ("id", "numero", "orgao", "fase")
# sonda da API real: listagem so garante Id (medido 2026-09-23)
CAMPOS_SONDA = ("id",)  # canario compara sem diferenciar caixa

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


class _Processo(ProcessRecord):
    """ProcessRecord que tambem expoe os attrs como atributos."""

    def __getattr__(self, nome: str) -> object:
        try:
            attrs = object.__getattribute__(self, "attrs")
        except AttributeError:
            raise AttributeError(nome) from None
        try:
            return attrs[nome]
        except KeyError:
            raise AttributeError(nome) from None


def _request_listagem(page: int) -> FetchRequest:
    return FetchRequest(
        endpoint="/api/painelpublicacao/listarpaginadoritensgrupospainelpublicacaoportal",
        method="GET",
        params={
            "page": str(page),
            "pagina": str(page),
            "pages": "0",
            "pageIndex": str(page - 1),
            "pageSize": "8",
            "length": "0",
            "options": ["5", "8", "10"],
        },
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _request_detalhes(ids: list[str]) -> FetchRequest:
    return FetchRequest(
        endpoint="/api/painelpublicacao/listaritensgrupospainelpublicacaoporempresamaster",
        method="GET",
        params={
            "page": "1",
            "pages": "0",
            "pageIndex": "0",
            "pageSize": "10",
            "length": "0",
            "options": ["5", "10"],
            "IdsEdital": ids,
        },
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=2,
        cursor_out=None,
    )


def hydrate_request(native_id: str) -> FetchRequest:
    return FetchRequest(
        endpoint=f"/api/processos/{native_id}",
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint="item",
        parent_native_id=native_id,
        cost_weight=2,
        cursor_out=None,
    )


def fabricate_item_native_id(processo_id: str, lote: object, sequencia: object) -> str:
    return f"{processo_id}:{lote}:{sequencia}"


def listar_vencedores_request(native_id: str) -> FetchRequest:
    """Compatibilidade com capturas legadas; vencedores fora da coleta atual."""
    return FetchRequest(
        endpoint="/api/fornecedores/listarvaloresvencedores",
        method="GET",
        params={"IdsEdital": native_id},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="winners",
        entity_hint="award",
        parent_native_id=native_id,
        cost_weight=2,
        cursor_out=None,
    )


def _agora() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trecho(valor: object) -> str:
    try:
        return json.dumps(valor, ensure_ascii=False)[:500]
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
        fatal=motivo,
    )


def _parse_listagem(dados: Mapping[str, object]) -> ParseResult:
    if "Data" not in dados:
        return _resultado_erro("envelope listagem sem campo Data")

    registros = dados.get("Data")
    if not isinstance(registros, list):
        return _resultado_erro("campo Data nao e lista")

    ids = []
    quarantine: list[QuarantineItem] = []

    for idx, registro in enumerate(registros):
        ponteiro = f"Data[{idx}]"
        if not isinstance(registro, dict):
            quarantine.append(_quarentena("registro nao e um objeto", ponteiro, registro))
            continue
        ident = registro.get("Id")
        if not ident:
            quarantine.append(_quarentena("registro sem Id", ponteiro, registro))
            continue
        ids.append(str(ident))

    page = dados.get("Page")
    pages = dados.get("Pages")

    proximo: list[FetchRequest] = []
    if ids:
        proximo.append(_request_detalhes(ids))

    if isinstance(page, int) and isinstance(pages, int) and page < pages:
        proximo.append(_request_listagem(page + 1))

    return ParseResult(
        batch=_BATCH_VAZIO,
        quarantine=tuple(quarantine),
        next=tuple(proximo),
        cursor_out=None,
        signals={
            "row_count_declared": dados.get("RowsCount") if isinstance(dados.get("RowsCount"), int) else len(registros),
            "row_count_scope": "total",
        },
        fatal=None,
    )


def _parse_hidratacao(raw: FetchedPage, dados: list[object]) -> ParseResult:
    processes: list[ProcessRecord] = []
    quarantine: list[QuarantineItem] = []

    for idx, registro in enumerate(dados):
        ponteiro = f"[{idx}]"
        if not isinstance(registro, dict):
            quarantine.append(_quarentena("registro detalhe nao e um objeto", ponteiro, registro))
            continue

        ident = registro.get("Id")
        if not ident:
            quarantine.append(_quarentena("registro detalhe sem Id", ponteiro, registro))
            continue

        numero = registro.get("NumeroProcesso") or registro.get("NumeroEdital")
        numero_txt = str(numero) if numero is not None else ""
        ano_extraido: int | None = None
        if "/" in numero_txt:
            try:
                ano_extraido = int(numero_txt.rsplit("/", 1)[-1].strip())
            except (TypeError, ValueError):
                pass

        data_abertura = registro.get("DataAbertura")
        if ano_extraido is None and data_abertura:
            try:
                candidato = str(data_abertura)[:4]
                if len(candidato) == 4 and candidato.isdigit():
                    ano_extraido = int(candidato)
            except (TypeError, ValueError):
                pass

        entidade = registro.get("NomeEmpresa")
        objeto = registro.get("Objeto")
        modalidade = registro.get("Modalidade")
        status = registro.get("DescricaoStatusEdital")

        processes.append(
            ProcessRecord(
                source_native_id=str(ident),
                attrs={
                    "numero": numero,
                    "number": numero,
                    "year": ano_extraido,
                    "ano": ano_extraido,
                    "orgao": entidade,
                    "entidade": entidade,
                    "orgao_nome": entidade,
                    "org_nome": entidade,
                    "org_native_id": str(entidade) if entidade else None,
                    "org_cnpj": None,
                    "orgao_cnpj": None,
                    "fase": status,
                    "phase_code": status,
                    "phase_label": status,
                    "modalidade": modalidade,
                    "modality_code": modalidade,
                    "modality_raw": modalidade,
                    "objeto": objeto,
                    "object": objeto,
                    "title": objeto,
                    "data_abertura": data_abertura,
                    "opening_at_source": data_abertura,
                },
            )
        )

    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=tuple(processes),
            items=(),
            attachments=(),
            results=(),
            awards=(),
            contracts=(),
            phases=(),
        ),
        quarantine=tuple(quarantine),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(dados)},
        fatal=None,
    )


def _parse_lotes(raw: FetchedPage, dados: object) -> ParseResult:
    pid = str(raw.request.parent_native_id or "")
    if not isinstance(dados, Mapping) or not isinstance(dados.get("Data"), list) or not pid:
        return _resultado_erro("lotes: envelope sem Data ou sem processo pai")
    itens: list[ItemRecord] = []
    for lote in dados["Data"]:
        if not isinstance(lote, Mapping):
            continue
        for item in lote.get("Itens") or []:
            if not isinstance(item, Mapping) or item.get("CodigoItemEdital") is None:
                continue
            itens.append(
                ItemRecord(
                    source_native_id=f"{pid}:item:{item['CodigoItemEdital']}",
                    attrs={
                        "process_native_id": pid,
                        "processo_id": pid,
                        "description": item.get("DescricaoItemEdital") or item.get("Nome"),
                        "qty": item.get("QuantidadeItem"),
                        "unit": item.get("DescricaoUnidadeMedida"),
                        "lot_number": lote.get("Numero"),
                        "seq": item.get("SequencialItem"),
                        "codigo_item": item.get("CodigoItem"),
                        "codigo_integracao": item.get("CodigoIntegracao"),
                    },
                )
            )
    return ParseResult(
        batch=NormalizedBatch(
            orgs=(), processes=(), items=tuple(itens), attachments=(), results=(), awards=(), contracts=(), phases=()
        ),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={},
        fatal=None,
    )


def _parse_sala_disputa(raw: FetchedPage, dados: object) -> ParseResult:
    pid = str(raw.request.parent_native_id or "")
    if not isinstance(dados, Mapping) or not isinstance(dados.get("Data"), list) or not pid:
        return _resultado_erro("sala publica: envelope sem Data ou sem processo pai")
    requests = []
    for lote in dados["Data"]:
        if not isinstance(lote, Mapping) or lote.get("Codigo") is None:
            continue
        requests.append(
            FetchRequest(
                endpoint=f"/api/salaDisputaPublica/{pid}/listarPorLoteEdital",
                method="GET",
                params={"codigoLoteEdital": str(lote["Codigo"])},
                body=None,
                headers_extra={"Accept": "application/json"},
                phase="hydrate",
                entity_hint="award",
                parent_native_id=pid,
                cost_weight=1,
                cursor_out=None,
            )
        )
    return ParseResult(
        batch=_BATCH_VAZIO,
        quarantine=(),
        next=tuple(requests),
        cursor_out=None,
        signals={},
        fatal=None,
    )


def _parse_propostas(raw: FetchedPage, dados: object) -> ParseResult:
    pid = str(raw.request.parent_native_id or "")
    codigo = raw.request.params.get("codigoLoteEdital")
    if not isinstance(dados, list) or not pid or not codigo:
        return _resultado_erro("propostas: resposta nao e lista ou lote sem identificador")
    resultado_id = f"{pid}:lote:{codigo}"
    propostas = []
    premios = []
    for proposta in dados:
        if not isinstance(proposta, Mapping):
            continue
        nome_bruto = str(proposta.get("Nome") or "")
        identidade = re.fullmatch(r"Proponente\s+\d+\s+\((.+)\s+-\s+(\d{14})\)", nome_bruto)
        nome = identidade.group(1) if identidade else nome_bruto
        cnpj = identidade.group(2) if identidade else None
        vencedor = proposta.get("Vencedor") is True
        if vencedor:
            premios.append(
                AwardRecord(
                    f"{resultado_id}:premio:{proposta.get('Codigo', len(premios))}",
                    {
                        "process_native_id": pid,
                        "result_native_id": resultado_id,
                        "supplier_name_raw": nome,
                        "supplier_cnpj": cnpj,
                        "amount_raw": proposta.get("Valor"),
                    },
                )
            )
        else:
            propostas.append({"nome": nome, "cnpj": cnpj, "valor": proposta.get("Valor"), "vencedor": False})
    resultado = ResultRecord(
        resultado_id,
        {
            "process_native_id": pid,
            "type": "adjudicacao",
            "propostas": propostas,
        },
    )
    return ParseResult(
        batch=NormalizedBatch((), (), (), (), (resultado,), tuple(premios), (), ()),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(dados)},
        fatal=None,
    )


def _parse_legado(dados: Mapping[str, object]) -> ParseResult:
    registros = dados.get("processos")
    if not isinstance(registros, list):
        return _resultado_erro("campo processos nao e lista")
    processos: list[ProcessRecord] = []
    quarentena: list[QuarantineItem] = []
    for indice, registro in enumerate(registros):
        if not isinstance(registro, dict):
            quarentena.append(_quarentena("registro nao e objeto", f"processos[{indice}]", registro))
            continue
        faltantes = [campo for campo in _CAMPOS_OBRIGATORIOS if not registro.get(campo)]
        if faltantes:
            quarentena.append(_quarentena(f"campos ausentes: {', '.join(faltantes)}", f"processos[{indice}]", registro))
            continue
        processos.append(
            _Processo(
                str(registro["id"]),
                {
                    "numero": registro["numero"],
                    "number": registro["numero"],
                    "orgao": registro["orgao"],
                    "org_nome": registro["orgao"],
                    "fase": registro["fase"],
                    "data_publicacao": registro.get("data_publicacao"),
                    "published_at_source": registro.get("data_publicacao"),
                    "data_abertura": registro.get("data_abertura"),
                    "opening_at_source": registro.get("data_abertura"),
                },
            )
        )
    proxima = dados.get("proxima_pagina")
    seguintes = (_request_listagem(proxima),) if isinstance(proxima, int) else ()
    return ParseResult(
        batch=NormalizedBatch((), tuple(processos), (), (), (), (), (), ()),
        quarantine=tuple(quarentena),
        next=seguintes,
        cursor_out=None,
        signals={"row_count_declared": len(registros)},
        fatal=None,
    )


def _parse_itens_legados(raw: FetchedPage, dados: Mapping[str, object]) -> ParseResult:
    processo_id = raw.request.parent_native_id or ""
    itens = dados.get("itens")
    if not isinstance(itens, list):
        return _resultado_erro("campo itens nao e lista")
    saida = []
    for indice, item in enumerate(itens):
        if not isinstance(item, dict):
            continue
        lote = item.get("lote", "")
        sequencia = item.get("sequencia", indice + 1)
        saida.append(
            ItemRecord(
                fabricate_item_native_id(processo_id, lote, sequencia),
                {
                    "process_native_id": processo_id,
                    "lot_number": lote,
                    "seq": sequencia,
                    "description": item.get("descricao"),
                },
            )
        )
    return ParseResult(
        batch=NormalizedBatch((), (), tuple(saida), (), (), (), (), ()),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(itens)},
        fatal=None,
    )


def _parse_vencedores(raw: FetchedPage, dados: Mapping[str, object]) -> ParseResult:
    vencedores = dados.get("vencedores")
    if not isinstance(vencedores, list):
        return _resultado_erro("campo vencedores nao e lista")
    processo_id = raw.request.parent_native_id or ""
    resultados = []
    premios = []
    for indice, vencedor in enumerate(vencedores):
        if not isinstance(vencedor, dict):
            continue
        result_native_id = f"{processo_id}:resultado"
        resultados.append(
            ResultRecord(
                result_native_id,
                {
                    "process_native_id": processo_id,
                    "type": "adjudicacao",
                },
            )
        )
        fornecedor = vencedor.get("fornecedor")
        nome = fornecedor.get("nome") if isinstance(fornecedor, dict) else None
        premios.append(
            AwardRecord(
                f"{processo_id}:premio:{indice}",
                {
                    "process_native_id": processo_id,
                    "result_native_id": result_native_id,
                    "supplier_name_raw": nome,
                    "amount_raw": vencedor.get("valor"),
                },
            )
        )
    return ParseResult(
        batch=NormalizedBatch((), (), (), (), tuple(resultados), tuple(premios), (), ()),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(vencedores)},
        fatal=None,
    )


class SistemaIndustriaAdapter(Adapter):
    source_code: str = "sistema_industria"
    adapter_version: str = "0.1.0"

    capabilities = Capabilities(
        strategy="two_phase",
        entities=("process", "item", "award"),
        hydration={"enabled": True, "trigger": "new_or_changed"},
        listing_order_stable=False,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={
            "min_delay_ms": 500,
            "jitter_ms": 200,
            "max_calls_per_run": 600,
            "max_concurrency": 1,
        },
        auth="csrf_handshake",
        probes={"rows_count_min_ratio": 0.85},
    )

    def open(self, cfg: SourceConfig) -> CsrfSession:
        manager = CsrfSessionManager(
            base_url=cfg.base_url,
            user_agent=USER_AGENT,
            ttl_seconds=TTL_TOKEN_SEGUNDOS,
        )
        sessao = manager.open()
        handshake_3_steps(manager, sessao)
        sessao.manager = manager
        return sessao

    def close(self, s: CsrfSession) -> None:
        if hasattr(s, "manager"):
            s.manager = None

    def plan(self, ctx: PlanContext) -> Iterator[FetchRequest]:
        yield _request_listagem(1)

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        pid = str(process.source_native_id)
        return (
            FetchRequest(
                endpoint=f"/api/edital/{pid}/lote/listarpaginadorportalpublico",
                method="GET",
                params={"page": "1", "pages": "0", "pageIndex": "0", "pageSize": "50", "length": "0", "IdEdital": pid},
                body=None,
                headers_extra={"Accept": "application/json"},
                phase="items",
                entity_hint="item",
                parent_native_id=pid,
                cost_weight=1,
                cursor_out=None,
            ),
            FetchRequest(
                endpoint=f"/api/salaDisputaPublica/{pid}/listarSalaDisputaPublica",
                method="GET",
                params={"IdEdital": pid},
                body=None,
                headers_extra={"Accept": "application/json"},
                phase="hydrate",
                entity_hint="award",
                parent_native_id=pid,
                cost_weight=1,
                cursor_out=None,
            ),
        )

    def fetch(self, s: CsrfSession, req: FetchRequest) -> FetchedPage:
        manager = getattr(s, "manager", None)
        if manager is None:
            raise RuntimeError("sessao CSRF sem manager associado")

        inicio = time.perf_counter()
        pagina = manager.call(s, req)
        duracao_ms = int((time.perf_counter() - inicio) * 1000)

        return FetchedPage(
            request=req,
            status=pagina.status_code,
            headers=pagina.headers,
            body=pagina.body,
            fetched_at=_agora(),
            duration_ms=duracao_ms,
        )

    def parse(self, raw: FetchedPage) -> ParseResult:
        try:
            dados = json.loads(raw.body.decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _resultado_erro(f"payload nao decodifica como JSON UTF-8: {exc}")

        fase = raw.request.phase
        if fase == "items":
            return _parse_lotes(raw, dados)
        if fase == "winners" and isinstance(dados, dict):
            return _parse_vencedores(raw, dados)
        if fase == "hydrate":
            if raw.request.endpoint.endswith("/listarSalaDisputaPublica"):
                return _parse_sala_disputa(raw, dados)
            if raw.request.endpoint.endswith("/listarPorLoteEdital"):
                return _parse_propostas(raw, dados)
            if isinstance(dados, dict) and "itens" in dados:
                return _parse_itens_legados(raw, dados)
            if not isinstance(dados, list):
                return _resultado_erro("detalhe nao e uma lista")
            return _parse_hidratacao(raw, dados)

        if not isinstance(dados, dict):
            return _resultado_erro("raiz do payload listagem nao e um objeto")
        if "processos" in dados:
            return _parse_legado(dados)
        return _parse_listagem(dados)
