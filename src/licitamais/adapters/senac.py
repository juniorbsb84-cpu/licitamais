"""Adaptador da API JSON aberta do SENAC, por regional."""

from __future__ import annotations

import json
import time
from collections import Counter
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests

from licitamais.adapter import Adapter
from licitamais.types import (
    Capabilities,
    ContractRecord,
    FetchedPage,
    FetchFailure,
    FetchRequest,
    NormalizedBatch,
    ParseResult,
    PlanContext,
    ProcessRecord,
    QuarantineItem,
    Session,
    SourceConfig,
)

REGIONAIS = tuple("AC AL AM AP BA CE DF ES GO MA MG MS MT PA PB PE PI PR RJ RN RO RR RS SC SE SP TO DN".split())
# sonda canario: check_contract_canary verifica campos no 1o nivel do objeto
# raiz; o payload real e um envelope {"success", "data"}, e campos de grupo
# (modalidade etc.) vivem em data[0]. A sonda usa as chaves do envelope.
CAMPOS_SONDA = ("success", "data")  # 1o nivel do objeto raiz (envelope)
TIMEOUT_SEGUNDOS = 30


def _vazio(processes=(), contracts=()):
    return NormalizedBatch((), tuple(processes), (), (), (), (), tuple(contracts), ())


def _texto(valor):
    return str(valor).strip() if valor is not None else ""


def _centavos(valor):
    try:
        return int((Decimal(str(valor)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _erro(motivo):
    return ParseResult(_vazio(), (QuarantineItem(motivo, motivo),), (), None, {"row_count_declared": 0}, motivo)


class SenacAdapter(Adapter):
    source_code = "senac"
    adapter_version = "0.1.0"
    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "contract"),
        hydration={"enabled": False},
        listing_order_stable=True,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={"min_delay_ms": 250, "jitter_ms": 100, "max_calls_per_run": 600, "max_concurrency": 1},
        auth="none",
        probes={"absence_confirm_runs": 2, "rows_count_min_ratio": 0.85},
    )

    def open(self, cfg: SourceConfig) -> Session:
        http = requests.Session()
        http.headers.update({"Accept": "application/json", "User-Agent": "licitamais-senac/0.1.0"})
        return Session(self.source_code, {"base_url": cfg.base_url, "http": http})

    def close(self, s: Session) -> None:
        http = s.dados.get("http")
        if http is not None:
            http.close()

    def plan(self, ctx: PlanContext) -> Iterator[FetchRequest]:
        for uf in REGIONAIS:
            for endpoint, params, hint in (
                (f"/service/api/licitacoes/regional/{uf}", {}, "process"),
                ("/service/api/contratos-parcerias", {"regional": uf}, "contract"),
            ):
                yield FetchRequest(
                    endpoint, "GET", params, None, {"Accept": "application/json"}, "discover", hint, None, 1, None
                )

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        return ()

    def fetch(self, s: Session, req: FetchRequest) -> FetchedPage | FetchFailure:
        http = s.dados.get("http")
        inicio = time.perf_counter()
        resposta = http.request(
            req.method,
            str(s.dados["base_url"]).rstrip("/") + req.endpoint,
            params=dict(req.params),
            headers=dict(req.headers_extra),
            timeout=TIMEOUT_SEGUNDOS,
        )
        duracao = int((time.perf_counter() - inicio) * 1000)
        agora = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        if not 200 <= resposta.status_code < 300:
            return FetchFailure(
                req, f"HTTP {resposta.status_code} em {req.endpoint}", agora, duracao, resposta.status_code
            )
        return FetchedPage(req, resposta.status_code, dict(resposta.headers), resposta.content, agora, duracao)

    def parse(self, raw: FetchedPage) -> ParseResult:
        try:
            dados = json.loads(raw.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _erro(f"json invalido: {exc}")
        if raw.request.entity_hint == "contract":
            uf = str(raw.request.params.get("regional") or "")
            linhas = dados if isinstance(dados, list) else dados.get("data") if isinstance(dados, Mapping) else None
            return _parse_contratos(uf, linhas)
        uf = raw.request.endpoint.rstrip("/").rsplit("/", 1)[-1]
        grupos = dados.get("data") if isinstance(dados, Mapping) else None
        return _parse_licitacoes(uf, grupos)


def _link_edital(uf, item):
    for doc in (item or {}).get("documentos") or ():
        if not isinstance(doc, Mapping):
            continue
        tipo = doc.get("tipoDocumento")
        descricao = tipo.get("descricao") if isinstance(tipo, Mapping) else None
        if str(descricao or "").strip().lower() != "edital":
            continue
        doc_id = str(doc.get("id") or "").strip()
        lic_id = str(doc.get("idLicitacao") or item.get("id") or "").strip()
        if not doc_id or not lic_id:
            continue
        return (
            f"https://transparencia.senac.br/service/api/licitacoes/{lic_id}/regional/{uf}/documento/{doc_id}/download"
        )
    return None


def _link_processo(item):
    link = str((item or {}).get("linkPregaoEletronico") or "").strip()
    return link or None


def _parse_licitacoes(uf, grupos):
    if not isinstance(grupos, list):
        return _erro("licitacoes: campo data nao e lista")
    processos = []
    for grupo in grupos:
        for item in (grupo or {}).get("dadosModalidadeLicitacao") or ():
            link_edital = _link_edital(uf, item)
            attrs = {
                "number": item.get("numeroProcesso"),
                "object": item.get("objeto"),
                "modality_raw": grupo.get("modalidade"),
                "opening_at_source": item.get("dataAbertura"),
                "data_situacao": item.get("dataSituacao"),
                "phase_label": item.get("situacao"),
                "org_native_id": f"SENAC-{uf}",
            }
            processo_url = _link_processo(item)
            if processo_url:
                attrs["link_processo"] = processo_url
            if link_edital:
                attrs["link_edital"] = link_edital
            processos.append(ProcessRecord(str(item["id"]), attrs))
    return ParseResult(_vazio(processos), (), (), None, {"row_count_declared": len(processos)}, None)


def _parse_contratos(uf, linhas):
    if not isinstance(linhas, list):
        return _erro("contratos: resposta nao e lista")

    def chave(linha):
        origem, numero = _texto(linha.get("numeroOrigem")), _texto(linha.get("numero"))
        if origem:
            return f"{uf}:{origem}:{numero}"
        # r29b: parceria (tipo 3) vem sem numeroOrigem; o contrato e o proprio processo
        return f"{uf}:sem-origem:{numero}:{_texto(linha.get('cpfCnpj')) or 'sem-cnpj'}"

    validas = [ln for ln in linhas if isinstance(ln, Mapping) and _texto(ln.get("numero"))]
    quarentena = tuple(
        QuarantineItem("contrato sem numero", str(ln)[:500])
        for ln in linhas
        if not (isinstance(ln, Mapping) and _texto(ln.get("numero")))
    )
    repetidas = {k for k, n in Counter(chave(ln) for ln in validas).items() if n > 1}
    usados: Counter = Counter()
    processos: dict[str, ProcessRecord] = {}
    contratos = []
    for linha in validas:
        base = chave(linha)
        native = base
        if base in repetidas:
            # r29b: so a chave repetida ganha desempate; chave unica mantem o id ja gravado
            for extra in (_texto(linha.get("cpfCnpj")), _texto(linha.get("dataContratacao"))[:10]):
                if extra:
                    native += f":{extra}"
                    if native not in usados:
                        break
            if native in usados:
                native += f":{usados[native]}"
        usados[native] += 1
        origem = _texto(linha.get("numeroOrigem"))
        pai = f"{uf}:origem:{origem}" if origem else base
        if pai not in processos:
            processos[pai] = ProcessRecord(
                pai,
                {
                    "number": origem or _texto(linha.get("numero")),
                    "object": linha.get("objeto"),
                    "modality_raw": None,
                    "org_native_id": f"SENAC-{uf}",
                },
            )
        contratos.append(
            ContractRecord(
                native,
                {
                    "process_native_id": pai,
                    "number": linha.get("numero"),
                    "object": linha.get("objeto"),
                    "supplier_name_raw": linha.get("favorecido"),
                    "supplier_cnpj": linha.get("cpfCnpj"),
                    "value_raw": linha.get("valorTotal"),
                    "value_cents": _centavos(linha.get("valorTotal")),
                    "valor_pago": linha.get("valorPago"),
                    "signed_at_source": linha.get("dataContratacao"),
                    "vigency_end": linha.get("dataFim"),
                    "situacao": linha.get("situacao"),
                    "modalidade_origem": linha.get("modalidadeOrigem"),
                },
            )
        )
    return ParseResult(
        _vazio(processos.values(), contratos), quarentena, (), None, {"row_count_declared": len(linhas)}, None
    )
