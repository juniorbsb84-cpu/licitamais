"""Adaptador da API JSON aberta de transparencia do SEST/SENAT.

Fonte: https://transparencia.sestsenat.org.br/api/ (SPA Angular; sem login,
token ou cookie). Descoberta:

- ``POST /edital/dadosAbertos`` com corpo ``{}`` devolve **o dump historico
  completo** (array JSON, ~335 mil linhas de licitante por edital, 2016-2026,
  ~319 MB, resposta chunked). Cada linha e um participante com
  ``filial, empresa (SEST|SENAT), modalidade, codigoEdital, numeroProcesso,
  objeto, dataHomologacao, dataProposta, licitante, valorProposta,
  valorVencido, situacao``. Nao ha paginacao de servidor nesta rota
  (conferido em 2026-09-24 com ``PAGINA``/``QtdItensPagina``/``pagina``/
  ``page``/``size``: todos ignorados; a unica reducao e por ``cO1_FILIAL``
  e/ou ``cO1_NUMPRO``). Por isso a descoberta e UMA chamada, como no BRB.

O vencedor oficial com **CNPJ** nao vem no dump; vem no detalhe por processo:

- ``GET /edital/detalhe/{codEdital}/{filial}/{numPro}`` devolve
  ``fornecedores[]`` com ``a2_NOME``, ``a2_CGC`` (CNPJ), ``vlR_VENCIDO``,
  ``qtD_VENCIDO``, ``vlR_TOTAL`` e ``vlR_HOMOLOGADO`` no raiz. Os codigos vao
  com ``/`` trocado por ``_`` (trocas do bundle Angular).

O "paginar" desta fonte e o **backfill da hidratacao**: o run so busca o
detalhe de um processo novo/alterado, e o teto de chamadas
(``max_calls_per_run``) corta o restante, que o runner persiste em
``hydration_pending`` e retoma no proximo run (ver runner.py). Cada detalhe
vira um ``ResultRecord`` (total homologado do processo) + ``AwardRecord`` por
vencedor, com ``supplier_cnpj`` e o processo pai obrigatorio.

strategy=full_diff, auth=none, listing_order_stable=true,
deletion_semantics='absence' com carencia de 2 runs. Decode UTF-8 estrito
explicito; a fonte usa strings com espacos de preenchimento e NUL.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests

from licitamais.adapter import Adapter
from licitamais.types import (
    AwardRecord,
    Capabilities,
    FetchedPage,
    FetchFailure,
    FetchRequest,
    NormalizedBatch,
    ParseResult,
    PlanContext,
    ProcessRecord,
    QuarantineItem,
    ResultRecord,
    Session,
    SourceConfig,
)

TIMEOUT_SEGUNDOS = 60

# campos obrigatorios do payload de descoberta (dump); a sonda canaria exige
# que o primeiro registro do array os tenha
CAMPOS_SONDA = ("filial", "codigoEdital", "numeroProcesso")

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

_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _limpar(valor: object) -> str | None:
    """Remove NUL/controle e espacos de preenchimento do dump."""
    if valor is None:
        return None
    texto = _CONTROLE.sub(" ", str(valor)).strip()
    return texto or None


def _data_iso(valor: object) -> str | None:
    """Converte dd/mm/aaaa (dump) ou ja-ISO (detalhe) para ISO-8601."""
    texto = _limpar(valor)
    if not texto:
        return None
    achado = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if achado is not None:
        dia, mes, ano = achado.groups()
        return f"{ano}-{mes}-{dia}"
    return texto


def _ano(valor: object) -> int | None:
    texto = _limpar(valor)
    if not texto:
        return None
    achado = re.search(r"(\d{4})", texto)
    return int(achado.group(1)) if achado is not None else None


def _valor_centavos(valor: object) -> int | None:
    """Converte valor monetario (BR 'R$ 45.000,00' ou .NET '45000'/'.96')."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int | float | Decimal):
        try:
            quantia = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            return None
        return int((quantia * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    bruto = _limpar(valor)
    if not bruto:
        return None
    bruto = bruto.replace("R$", "").replace(" ", "")
    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    try:
        quantia = Decimal(bruto)
    except (InvalidOperation, ValueError):
        return None
    return int((quantia * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _trecho(valor: object) -> str:
    try:
        return json.dumps(valor, ensure_ascii=False)[:500]
    except (TypeError, ValueError):
        return repr(valor)[:500]


def _quarentena(motivo: str, ponteiro: str | None, valor: object) -> QuarantineItem:
    return QuarantineItem(raw_excerpt=_trecho(valor), reason=motivo, pointer=ponteiro)


def _resultado_erro(motivo: str, ponteiro: str | None = None, valor: object = None) -> ParseResult:
    return ParseResult(
        batch=_BATCH_VAZIO,
        quarantine=(_quarentena(motivo, ponteiro, valor if valor is not None else motivo),),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": 0},
        fatal=motivo,
    )


def _decodificar(corpo: bytes) -> object:
    return json.loads(bytes(corpo).decode("utf-8", errors="strict"))


def _chave(filial: str, numpro: str) -> str:
    return f"sestsenat:{filial}:{numpro}"


def _trecho_url(valor: str) -> str:
    return valor.replace("/", "_")


def _pedido_descoberta() -> FetchRequest:
    return FetchRequest(
        endpoint="/edital/dadosAbertos",
        method="POST",
        params={},
        body=b"{}",
        headers_extra={"Accept": "application/json", "Content-Type": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _pedido_detalhe(pid: str, codigo_edital: str, filial: str, numero_processo: str) -> FetchRequest:
    endpoint = f"/edital/detalhe/{_trecho_url(codigo_edital)}/{_trecho_url(filial)}/{_trecho_url(numero_processo)}"
    return FetchRequest(
        endpoint=endpoint,
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint="result",
        parent_native_id=pid,
        cost_weight=1,
        cursor_out=None,
    )


def _parse_dump(raw: FetchedPage) -> ParseResult:
    try:
        dados = _decodificar(raw.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _resultado_erro(f"payload nao decodifica como JSON UTF-8: {exc}")
    if not isinstance(dados, list):
        return _resultado_erro("raiz do dump nao e uma lista")
    if not dados:
        return _resultado_erro("dump de dados abertos vazio")

    agregados: dict[str, dict] = {}
    quarantine: list[QuarantineItem] = []

    for idx, linha in enumerate(dados):
        ponteiro = f"[{idx}]"
        if not isinstance(linha, Mapping):
            quarantine.append(_quarentena("registro nao e um objeto", ponteiro, linha))
            continue
        filial = _limpar(linha.get("filial"))
        numpro = _limpar(linha.get("numeroProcesso"))
        if not filial or not numpro:
            quarantine.append(_quarentena("campos obrigatorios ausentes: filial ou numeroProcesso", ponteiro, linha))
            continue
        pid = _chave(filial, numpro)
        item = agregados.get(pid)
        if item is None:
            item = {
                "attrs": {
                    "number": numpro,
                    "numero_processo": numpro,
                    "codigo_edital": _limpar(linha.get("codigoEdital")),
                    "filial": filial,
                    "nome_filial": _limpar(linha.get("nomeFilial")),
                    "object": _limpar(linha.get("objeto")),
                    "org_native_id": _limpar(linha.get("empresa")),
                    "empresa": _limpar(linha.get("empresa")),
                    "modality_raw": _limpar(linha.get("modalidade")),
                    "phase_label": _limpar(linha.get("situacao")),
                    "natureza": _limpar(linha.get("natureza")),
                    "criterio_julgamento": _limpar(linha.get("criterioJulgamento")),
                    "uf": _limpar(linha.get("uf")),
                    "unidade": _limpar(linha.get("unidade")),
                    "year": _ano(linha.get("dataHomologacao")) or _ano(linha.get("dataProposta")),
                    "opening_at_source": _data_iso(linha.get("dataAbertura")) or _data_iso(linha.get("dataProposta")),
                    "data_homologacao": _data_iso(linha.get("dataHomologacao")),
                    "licitantes": 0,
                    "has_vencedor": False,
                    "valor_vencido_cents": 0,
                }
            }
            agregados[pid] = item
        attrs = item["attrs"]
        attrs["licitantes"] += 1
        vencido = _valor_centavos(linha.get("valorVencido"))
        if vencido is not None and vencido > 0:
            attrs["has_vencedor"] = True
            attrs["valor_vencido_cents"] += vencido

    processes = [ProcessRecord(source_native_id=pid, attrs=dict(item["attrs"])) for pid, item in agregados.items()]
    if not processes:
        return _resultado_erro("nenhum processo valido no dump", None, dados[:1])
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


def _parse_detalhe(raw: FetchedPage) -> ParseResult:
    pid = _limpar(raw.request.parent_native_id)
    if not pid:
        return _resultado_erro("detalhe sem processo pai (parent_native_id obrigatorio)")
    try:
        dados = _decodificar(raw.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _resultado_erro(f"detalhe nao decodifica como JSON UTF-8: {exc}")
    if not isinstance(dados, Mapping):
        return _resultado_erro("detalhe nao e um objeto", raw.request.endpoint, dados)
    if "fornecedores" not in dados and "cO1_NUMPRO" not in dados:
        return _resultado_erro("detalhe sem fornecedores nem processo", raw.request.endpoint, dados)

    result_native_id = f"{pid}:resultado"
    total = _valor_centavos(dados.get("vlR_HOMOLOGADO"))
    result = ResultRecord(
        source_native_id=result_native_id,
        attrs={
            "process_native_id": pid,
            "type": "homologacao",
            "decided_at_source": _data_iso(dados.get("daT_HOMOLOGACAO")),
            "value_total_cents": total,
            "value_total_raw": dados.get("vlR_HOMOLOGADO"),
            "numero_processo": _limpar(dados.get("cO1_NUMPRO")),
            "codigo_edital": _limpar(dados.get("coD_EDITAL")),
        },
    )

    fornecedores = dados.get("fornecedores")
    quarantine: list[QuarantineItem] = []
    awards: list[AwardRecord] = []
    if fornecedores is None:
        fornecedores = []
    if not isinstance(fornecedores, list):
        quarantine.append(_quarentena("fornecedores nao e uma lista", raw.request.endpoint, fornecedores))
        fornecedores = []
    for posicao, fornecedor in enumerate(fornecedores):
        ponteiro = f"fornecedores[{posicao}]"
        if not isinstance(fornecedor, Mapping):
            quarantine.append(_quarentena("fornecedor nao e um objeto", ponteiro, fornecedor))
            continue
        qtd_vencido = fornecedor.get("qtD_VENCIDO")
        try:
            qtd_int = int(str(qtd_vencido).strip()) if qtd_vencido not in (None, "") else 0
        except (TypeError, ValueError):
            qtd_int = 0
        vencido_cents = _valor_centavos(fornecedor.get("vlR_VENCIDO"))
        if (vencido_cents is None or vencido_cents <= 0) and qtd_int <= 0:
            continue  # participante perdedor; o dump ja registra a proposta
        cnpj = _limpar(fornecedor.get("a2_CGC"))
        if not cnpj:
            quarantine.append(_quarentena("vencedor sem CNPJ", ponteiro, fornecedor))
            continue
        codigo = _limpar(fornecedor.get("cO3_CODIGO")) or str(posicao)
        awards.append(
            AwardRecord(
                source_native_id=f"{pid}:award:{cnpj}:{codigo}",
                attrs={
                    "process_native_id": pid,
                    "result_native_id": result_native_id,
                    "supplier_name_raw": _limpar(fornecedor.get("a2_NOME")),
                    "supplier_cnpj": cnpj,
                    "amount_cents": vencido_cents,
                    "amount_raw": fornecedor.get("vlR_VENCIDO"),
                    "qty_awarded": qtd_int,
                    "qty_awarded_raw": qtd_vencido,
                    "vlr_total_raw": fornecedor.get("vlR_TOTAL"),
                },
            )
        )

    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=(),
            items=(),
            attachments=(),
            results=(result,),
            awards=tuple(awards),
            contracts=(),
            phases=(),
        ),
        quarantine=tuple(quarantine),
        next=(),
        cursor_out=None,
        signals={},
        fatal=None,
    )


class SestSenatAdapter(Adapter):
    source_code: str = "sestsenat"
    adapter_version: str = "0.1.0"

    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "result", "award"),
        hydration={"enabled": True, "trigger": "new_or_changed", "refresh_per_run": 0},
        listing_order_stable=True,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={
            "min_delay_ms": 250,
            "jitter_ms": 100,
            "max_calls_per_run": 2000,
            "max_concurrency": 1,
        },
        auth="none",
        probes={"absence_confirm_runs": 2, "rows_count_min_ratio": 0.85},
    )

    def open(self, cfg: SourceConfig) -> Session:
        http = requests.Session()
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "licitamais-sestsenat/0.1.0",
            }
        )
        return Session(
            source_code=self.source_code,
            dados={"base_url": cfg.base_url, "http": http},
        )

    def close(self, s: Session) -> None:
        http = s.dados.get("http")
        if http is not None:
            http.close()

    def plan(self, ctx: PlanContext) -> Iterator[FetchRequest]:
        # dump unico (a API nao pagina esta rota); a paginacao por run e o
        # backfill da hidratacao, controlado pelo teto de chamadas do runner.
        yield _pedido_descoberta()

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        attrs = process.attrs
        codigo_edital = _limpar(attrs.get("codigo_edital"))
        filial = _limpar(attrs.get("filial"))
        numero_processo = _limpar(attrs.get("numero_processo"))
        if not codigo_edital or not filial or not numero_processo:
            return ()
        return (_pedido_detalhe(str(process.source_native_id), codigo_edital, filial, numero_processo),)

    def fetch(self, s: Session, req: FetchRequest) -> FetchedPage | FetchFailure:
        dados = getattr(s, "dados", None)
        if not isinstance(dados, Mapping):
            dados = {}
        http = dados.get("http") or s
        base_url = str(dados.get("base_url") or "")
        url = base_url.rstrip("/") + req.endpoint

        inicio = time.perf_counter()
        resposta = http.request(
            req.method,
            url,
            params=dict(req.params),
            data=req.body,
            headers=dict(req.headers_extra),
            timeout=TIMEOUT_SEGUNDOS,
        )
        duracao_ms = int((time.perf_counter() - inicio) * 1000)

        corpo = resposta.content
        if isinstance(corpo, str):
            corpo = corpo.encode("utf-8")

        agora = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

        if not 200 <= resposta.status_code < 300:
            return FetchFailure(
                request=req,
                error=f"HTTP {resposta.status_code} em {req.endpoint}",
                fetched_at=agora,
                duration_ms=duracao_ms,
                status=resposta.status_code,
            )

        return FetchedPage(
            request=req,
            status=resposta.status_code,
            headers=dict(resposta.headers),
            body=corpo,
            fetched_at=agora,
            duration_ms=duracao_ms,
        )

    def parse(self, raw: FetchedPage) -> ParseResult:
        if raw.request.phase == "hydrate" or raw.request.entity_hint == "result":
            return _parse_detalhe(raw)
        return _parse_dump(raw)
