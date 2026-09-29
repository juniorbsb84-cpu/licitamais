"""Adaptador do IGES-DF (source_code='iges').

Fonte WordPress (base https://igesdf.org.br, sem www: o www e GLPI e devolve
403): a descoberta consulta o indice de midias wp-json e segue para o CSV de
contratacoes mais recente via ParseResult.next. O CSV e servido direto como
text/csv, com BOM, delimitador ";" e 36 colunas (latin-1 na origem; UTF-8 com
acentos apos decodificacao). Cada linha vira um ContractRecord com
supplier_cnpj / supplier_name_raw / value_raw (formato BR "1.234,56") /
vigencia; o processo pai (id "iges:{PROCESSO SEI}") e emitido junto, pois a
fonte nao liga contrato a licitacao e o loader poe contrato sem pai em
quarentena. Valor "R$ -" e placeholder de ausente: vira value_raw None sem
quarentena (o loader tambem trata value_raw None como ausente).
strategy=full_diff, auth=none, listing_order_stable=true,
deletion_semantics='absence' com carencia de 2 runs.
"""

from __future__ import annotations

import csv
import json
import pathlib
import re
import tempfile
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import certifi
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

TIMEOUT_SEGUNDOS = 30

INDICE_ENDPOINT = "/wp-json/wp/v2/media"
INDICE_PARAMS = {
    "search": "CONTRAT",
    "mime_type": "text/csv",
    "orderby": "date",
    "order": "desc",
    "per_page": "100",
}

# campos obrigatorios do payload de descoberta (indice wp-json de midias);
# o registro de fontes exige a declaracao para a sonda canaria
CAMPOS_SONDA = ("id", "date", "source_url", "mime_type")

_CAMPOS_OBRIGATORIOS = CAMPOS_SONDA

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


def _pedido_indice() -> FetchRequest:
    return FetchRequest(
        endpoint=INDICE_ENDPOINT,
        method="GET",
        params=dict(INDICE_PARAMS),
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="index",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _pedido_csv(endpoint: str) -> FetchRequest:
    return FetchRequest(
        endpoint=endpoint,
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "text/csv"},
        phase="discover",
        entity_hint="contract",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _e_contrato(nome: str) -> bool:
    return "CONTRAT" in nome.upper()


def _mais_recente(itens: list[object]) -> Mapping | None:
    candidatos = [
        item
        for item in itens
        if isinstance(item, Mapping)
        and isinstance(item.get("source_url"), str)
        and item.get("source_url")
        and _e_contrato(str(item["source_url"]).rsplit("/", 1)[-1])
    ]
    if not candidatos:
        return None
    return max(
        candidatos,
        key=lambda item: (
            str(item.get("date") or ""),
            str(item.get("source_url") or ""),
        ),
    )


def _caminho_csv(source_url: str, base_url: str) -> str | None:
    url = source_url.strip()
    if "://" in url:
        hospedeiro = url.split("://", 1)[1].split("/", 1)
        if len(hospedeiro) < 2 or hospedeiro[0].lower() != "igesdf.org.br":
            return None
        return "/" + hospedeiro[1]
    if url.startswith("/"):
        return url
    base = (base_url or "").strip().rstrip("/")
    if base and url.startswith(base):
        resto = url[len(base) :]
        return resto if resto.startswith("/") else "/" + resto
    return None


def _parse_indice(registros: list[object], base_url: str) -> ParseResult:
    signals = {"row_count_declared": len(registros)}
    escolhido = _mais_recente(registros)
    if escolhido is None:
        return ParseResult(
            batch=_BATCH_VAZIO,
            quarantine=(
                _quarentena(
                    "indice sem csv de contratacoes",
                    None,
                    registros[:1],
                ),
            ),
            next=(),
            cursor_out=None,
            signals=signals,
            fatal="indice sem csv de contratacoes",
        )
    caminho = _caminho_csv(str(escolhido["source_url"]), base_url)
    if caminho is None:
        return ParseResult(
            batch=_BATCH_VAZIO,
            quarantine=(
                _quarentena(
                    "csv fora do dominio igesdf.org.br",
                    None,
                    escolhido.get("source_url"),
                ),
            ),
            next=(),
            cursor_out=None,
            signals=signals,
            fatal="csv fora do dominio igesdf.org.br",
        )
    return ParseResult(
        batch=_BATCH_VAZIO,
        quarantine=(),
        next=(_pedido_csv(caminho),),
        cursor_out=None,
        signals=signals,
        fatal=None,
    )


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    return str(valor).replace(" ", " ").strip()


def _valor_raw(valor: object) -> str | None:
    texto = _texto(valor)
    if texto in ("", "R$ -", "-", "N/A"):
        return None
    return texto


def _valor_centavos(valor: object) -> int | None:
    bruto = _texto(valor).replace("R$", "").replace(" ", "")
    if bruto in ("", "-", "N/A"):
        return None
    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    try:
        quantia = Decimal(bruto)
        return int((quantia * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, ValueError, ArithmeticError):
        return None


def _data_iso(valor: object) -> str | None:
    texto = _texto(valor)
    if not texto:
        return None
    encontro = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", texto)
    if encontro is None:
        return None
    dia, mes, ano = encontro.groups()
    return f"{ano}-{mes}-{dia}"


def _id_processo(processo: str) -> str:
    return f"iges:{processo}"


def _id_contrato(processo: str, tipo: str, numero: str) -> str:
    return f"{_id_processo(processo)}:{tipo}:{numero}"


def _parse_csv(texto: str) -> ParseResult:
    try:
        leitor = csv.reader(texto.splitlines(), delimiter=";")
        linhas = [linha for linha in leitor if any(celula.strip() for celula in linha)]
    except csv.Error as exc:
        return _resultado_erro(f"csv nao parseia com delimitador ';': {exc}")
    if not linhas:
        return _resultado_erro("csv vazio")
    cabecalho = [celula.replace("﻿", "").strip() for celula in linhas[0]]
    if len(cabecalho) != 36:
        return _resultado_erro(f"csv com {len(cabecalho)} colunas, esperado 36")
    pos = {nome.upper(): i for i, nome in enumerate(cabecalho)}
    for esperado in (
        "PROCESSO SEI",
        "TIPO DE INSTRUMENTO",
        "N DO INSTRUMENTO",
        "CONTRATADA",
        "CNPJ",
        "VALOR TOTAL",
        "OBJETO",
        "MODALIDADE",
        "DT ASS INSTRUMENTO",
        "DATA INICIO",
        "DATA FIM",
    ):
        if esperado not in pos:
            return _resultado_erro(f"csv sem coluna {esperado}")
    dados = [linha for linha in linhas[1:] if len(linha) == 36]
    if not dados:
        return _resultado_erro("csv sem linhas de dados")

    vistos: set[str] = set()
    processes: list[ProcessRecord] = []
    contracts: list[ContractRecord] = []
    quarantine: list[QuarantineItem] = []

    for idx, linha in enumerate(dados):
        ponteiro = f"[{idx}]"
        processo = _texto(linha[pos["PROCESSO SEI"]])
        tipo = _texto(linha[pos["TIPO DE INSTRUMENTO"]])
        numero = _texto(linha[pos["N DO INSTRUMENTO"]])
        contratada = _texto(linha[pos["CONTRATADA"]])
        cnpj = _texto(linha[pos["CNPJ"]])
        if not processo or not tipo or not contratada or not cnpj:
            faltando = ", ".join(
                nome
                for nome, ok in (
                    ("PROCESSO SEI", processo),
                    ("TIPO DE INSTRUMENTO", tipo),
                    ("CONTRATADA", contratada),
                    ("CNPJ", cnpj),
                )
                if not ok
            )
            quarantine.append(
                _quarentena(
                    f"campos obrigatorios ausentes: {faltando}",
                    ponteiro,
                    linha,
                )
            )
            continue
        pid = _id_processo(processo)
        if pid not in vistos:
            vistos.add(pid)
            processes.append(
                ProcessRecord(
                    source_native_id=pid,
                    attrs={
                        "number": processo,
                        "object": _texto(linha[pos["OBJETO"]]) or None,
                        "org_native_id": "IGESDF",
                        "modality_raw": _texto(linha[pos["MODALIDADE"]]) or None,
                        "phase_label": _texto(linha[0]) or None,
                        "opening_at_source": _data_iso(linha[pos["DT ASS INSTRUMENTO"]]),
                    },
                )
            )
        valor_raw = _valor_raw(linha[pos["VALOR TOTAL"]])
        contracts.append(
            ContractRecord(
                source_native_id=_id_contrato(processo, tipo, numero),
                attrs={
                    "process_native_id": pid,
                    "number": numero,
                    "object": _texto(linha[pos["OBJETO"]]) or None,
                    "org_native_id": "IGESDF",
                    "modality_raw": _texto(linha[pos["MODALIDADE"]]) or None,
                    "tipo_instrumento": tipo,
                    "supplier_name_raw": contratada,
                    "supplier_cnpj": cnpj,
                    "value_raw": valor_raw,
                    "value_cents": (None if valor_raw is None else _valor_centavos(valor_raw)),
                    "signed_at_source": _data_iso(linha[pos["DT ASS INSTRUMENTO"]]),
                    "vigency_start": _data_iso(linha[pos["DATA INICIO"]]),
                    "vigency_end": _data_iso(linha[pos["DATA FIM"]]),
                },
            )
        )

    if not contracts:
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
            fatal="nenhum contrato valido no csv",
        )
    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=tuple(processes),
            items=(),
            attachments=(),
            results=(),
            awards=(),
            contracts=tuple(contracts),
            phases=(),
        ),
        quarantine=tuple(quarantine),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(dados)},
        fatal=None,
    )


def _decodificar_csv(corpo: bytes) -> str:
    for codificacao in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return bytes(corpo).decode(codificacao)
        except (UnicodeDecodeError, ValueError):
            continue
    return bytes(corpo).decode("latin-1", errors="replace")


def cadeia_certificados() -> str:
    """igesdf.org.br nao envia o intermediario Sectigo DV R36 (medido 2026-09-24:
    openssl "unable to verify the first certificate"). Raizes do certifi + esse
    intermediario, baixado do AIA oficial da Sectigo; verificacao TLS segue ligada."""
    destino = pathlib.Path(tempfile.gettempdir()) / "licitamais_iges_cadeia.pem"
    intermediario = pathlib.Path(__file__).resolve().parents[1] / "certs" / "sectigo_dv_r36.pem"
    destino.write_text(
        pathlib.Path(certifi.where()).read_text(encoding="ascii") + "\n" + intermediario.read_text(encoding="ascii"),
        encoding="ascii",
    )
    return str(destino)


class IgesAdapter(Adapter):
    source_code: str = "iges"
    adapter_version: str = "0.1.0"

    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "contract"),
        hydration={"enabled": False},
        listing_order_stable=True,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={
            "min_delay_ms": 250,
            "jitter_ms": 100,
            "max_calls_per_run": 600,
            "max_concurrency": 1,
        },
        auth="none",
        probes={"absence_confirm_runs": 2, "rows_count_min_ratio": 0.85},
    )

    def open(self, cfg: SourceConfig) -> Session:
        http = requests.Session()
        http.verify = cadeia_certificados()
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "licitamais-iges/0.1.0",
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
        yield _pedido_indice()

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        return ()

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
        if raw.request.entity_hint == "contract":
            return _parse_csv(_decodificar_csv(raw.body))
        try:
            dados = json.loads(bytes(raw.body).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _resultado_erro(f"payload nao decodifica como JSON UTF-8: {exc}")
        if not isinstance(dados, list):
            return _resultado_erro("raiz do payload nao e uma lista")
        if not dados:
            return _resultado_erro("lista vazia de midias")
        dados_mapeados = getattr(raw.request, "dados", None)
        base_url = ""
        if isinstance(dados_mapeados, Mapping):
            base_url = str(dados_mapeados.get("base_url") or "")
        else:
            sessao_base = getattr(raw.request, "parent_native_id", None)
            base_url = str(sessao_base) if isinstance(sessao_base, str) else ""
        return _parse_indice(dados, "https://igesdf.org.br" if not base_url else base_url)
