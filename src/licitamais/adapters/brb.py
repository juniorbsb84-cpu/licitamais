"""Adapter da API aberta do BRB (source_code='brb').

Contrato real: GET {base_url}/api/licitacao, com base_url da fonte igual a
https://pdd.brb.com.br/PLC. Responde 200 sem cookie nem token, JSON UTF-8
com uma LISTA de licitacoes (sem paginacao). Cada registro traz as chaves:
anexos, ano, contratos, fase, id, numero, objeto, observacao, realizacao,
tipo, titulo. Anexos e contratos vem embutidos no registro: nenhuma chamada
extra por processo.
strategy=full_diff, auth=none, listing_order_stable=true,
deletion_semantics='absence' com carencia de 2 runs.
Decode UTF-8 estrito explicito — a propriedade e do leitor,
nunca cp1252 do sistema.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

import requests

from licitamais.adapter import Adapter
from licitamais.types import (
    AttachmentRecord,
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

_CAMPOS_OBRIGATORIOS = (
    "id",
    "numero",
    "ano",
    "objeto",
    "titulo",
    "fase",
)
# realizacao falta em ~110 processos antigos (medido 2026-09-23): opcional

_HINTS_SUB = {"arquivo": "attachment", "contratos": "contract"}

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


def _kind(valor: object) -> str:
    normalizado = str(valor or "").strip().lower()
    return normalizado if normalizado in {"edital", "aviso", "resultado", "contrato"} else "outro"


def _valor_centavos(valor: object) -> int | None:
    if valor is None or str(valor).strip() == "":
        return None
    bruto = str(valor).strip().replace("R$", "").replace(" ", "")
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


def decode_brb_bytes(raw: bytes) -> str:
    """Decodifica bytes do BRB em UTF-8 estrito, nunca cp1252."""
    return raw.decode("utf-8", errors="strict")


def _pedido_descoberta() -> FetchRequest:
    return FetchRequest(
        endpoint="/api/licitacao",
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


def brb_sub_request(process_native_id: str, sub: str) -> FetchRequest:
    """Mapeamento puro e sem estado para sub-endpoint de arquivo/contratos.

    Compatibilidade legada: o contrato real embute anexos e contratos no
    registro, entao o adaptador nao emite mais esses pedidos.
    """
    return FetchRequest(
        endpoint=f"/api/processos/{process_native_id}/{sub}",
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint=_HINTS_SUB.get(sub, sub),
        parent_native_id=process_native_id,
        cost_weight=1,
        cursor_out=None,
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


def _extrair_rotulo(valor: object, chaves: tuple[str, ...]) -> str | None:
    if isinstance(valor, Mapping):
        for chave in chaves:
            conteudo = valor.get(chave)
            if conteudo is not None and str(conteudo).strip() != "":
                return str(conteudo)
        return None
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _anexo_para_registro(
    pid: str,
    posicao: int,
    anexo: object,
    ponteiro: str,
    quarantine: list[QuarantineItem],
) -> AttachmentRecord | None:
    if isinstance(anexo, str):
        url = anexo.strip()
        if not url:
            quarantine.append(_quarentena("anexo vazio", ponteiro, anexo))
            return None
        nome = url.rsplit("/", 1)[-1].strip() or f"anexo-{posicao}"
        return AttachmentRecord(
            source_native_id=f"{pid}:anexo:{posicao}:{nome}",
            attrs={
                "processo_id": pid,
                "process_native_id": pid,
                "nome": nome,
                "filename": nome,
                "url_download": url,
                "kind": "outro",
                "kind_raw": None,
                "mime": None,
                "size_bytes": None,
                "sha256": None,
            },
        )
    if not isinstance(anexo, Mapping):
        quarantine.append(_quarentena("anexo nao e um objeto", ponteiro, anexo))
        return None
    nome = _extrair_rotulo(anexo, ("nome", "titulo", "arquivo", "filename"))
    # API real (2026-09-23): {id, arquivo, file, descricao, cadastro}, sem URL;
    # download fica atras de WAF. Guardamos o metadado; url e opcional.
    url = _extrair_rotulo(anexo, ("url", "link", "href", "download", "caminho"))
    if not nome:
        quarantine.append(_quarentena("campos obrigatorios ausentes: nome", ponteiro, anexo))
        return None
    tamanho = anexo.get("tamanho_bytes") or anexo.get("size_bytes")
    digest = anexo.get("hash_sha256") or anexo.get("sha256")
    return AttachmentRecord(
        source_native_id=(f"{pid}:anexo:{anexo['id']}" if anexo.get("id") else f"{pid}:anexo:{posicao}:{nome}"),
        attrs={
            "processo_id": pid,
            "process_native_id": pid,
            "nome": nome,
            "filename": nome,
            "url_download": url,
            "kind": _kind(anexo.get("tipo")),
            "kind_raw": anexo.get("tipo"),
            "mime": anexo.get("mime"),
            "size_bytes": tamanho if isinstance(tamanho, int) else None,
            "sha256": digest if isinstance(digest, str) else None,
            "tamanho_bytes": anexo.get("tamanho_bytes"),
            "hash_sha256": anexo.get("hash_sha256"),
        },
    )


def _contrato_para_registro(
    pid: str,
    contrato: object,
    ponteiro: str,
    quarantine: list[QuarantineItem],
) -> ContractRecord | None:
    if not isinstance(contrato, Mapping):
        quarantine.append(_quarentena("contrato nao e um objeto", ponteiro, contrato))
        return None
    numero = contrato.get("numero")
    # credenciamento vem com numero vazio mas id e valor (medido 2026-09-23)
    if (numero is None or str(numero).strip() == "") and contrato.get("id") is not None:
        numero = ""
    elif numero is None or str(numero).strip() == "":
        quarantine.append(_quarentena("campos obrigatorios ausentes: numero", ponteiro, contrato))
        return None
    numero_txt = str(numero).strip()

    valor = contrato.get("valor")
    if valor is None:
        valor = contrato.get("valor_global")

    nome_fornecedor: str | None = None
    empresa = contrato.get("empresa")
    if isinstance(empresa, str) and empresa.strip():
        nome_fornecedor = empresa.strip()
    elif isinstance(empresa, Mapping):
        nome_fornecedor = _extrair_rotulo(empresa, ("nome", "razao_social", "fantasia"))
    if nome_fornecedor is None:
        fornecedor = contrato.get("fornecedor")
        if isinstance(fornecedor, Mapping):
            nome_fornecedor = _extrair_rotulo(fornecedor, ("nome", "razao_social", "fantasia"))
        elif isinstance(fornecedor, str) and fornecedor.strip():
            nome_fornecedor = fornecedor.strip()
    if nome_fornecedor is None:
        alternativo = contrato.get("fornecedor_nome") or contrato.get("supplier_name_raw")
        if alternativo is not None and str(alternativo).strip():
            nome_fornecedor = str(alternativo).strip()
    cnpj = contrato.get("cnpj") or contrato.get("CNPJ") or contrato.get("fornecedor_cnpj")

    return ContractRecord(
        # numero repete dentro do processo; o id do contrato e unico (2026-09-23)
        source_native_id=(
            f"{pid}:contrato:{contrato['id']}" if contrato.get("id") is not None else f"{pid}:contrato:{numero_txt}"
        ),
        attrs={
            "processo_id": pid,
            "process_native_id": pid,
            "numero": numero_txt,
            "number": numero_txt,
            "ano": contrato.get("ano"),
            "data_assinatura": contrato.get("assinadoEm") or contrato.get("data_assinatura"),
            "signed_at_source": contrato.get("assinadoEm") or contrato.get("data_assinatura"),
            "vigencia_inicio": contrato.get("inicioVigencia") or contrato.get("vigencia_inicio"),
            "vigency_start": contrato.get("inicioVigencia") or contrato.get("vigencia_inicio"),
            "vigencia_fim": contrato.get("fimVigencia") or contrato.get("vigencia_fim"),
            "vigency_end": contrato.get("fimVigencia") or contrato.get("vigencia_fim"),
            "valor": valor,
            "valor_global": contrato.get("valor_global"),
            "value_raw": valor,
            "value_cents": _valor_centavos(valor),
            "empresa": contrato.get("empresa"),
            "fornecedor_cnpj": cnpj,
            "fornecedor_nome": nome_fornecedor,
            "supplier_name_raw": nome_fornecedor,
            "supplier_org_id": contrato.get("supplier_org_id"),
            "objeto": contrato.get("objeto"),
            "object": contrato.get("objeto"),
        },
    )


def _parse_lista(registros: list[object]) -> ParseResult:
    processes: list[ProcessRecord] = []
    attachments: list[AttachmentRecord] = []
    contracts: list[ContractRecord] = []
    quarantine: list[QuarantineItem] = []

    for idx, registro in enumerate(registros):
        ponteiro = f"[{idx}]"
        if not isinstance(registro, dict):
            quarantine.append(_quarentena("registro nao e um objeto", ponteiro, registro))
            continue

        ausentes = [campo for campo in _CAMPOS_OBRIGATORIOS if not registro.get(campo)]
        if ausentes:
            quarantine.append(
                _quarentena(
                    "campos obrigatorios ausentes: " + ", ".join(ausentes),
                    ponteiro,
                    registro,
                )
            )
            continue

        pid = str(registro["id"])
        numero = registro["numero"]
        try:
            ano: int | None = int(str(registro["ano"]).strip())
        except (TypeError, ValueError):
            ano = None
        objeto = registro["objeto"]
        titulo = registro["titulo"]
        realizacao = registro["realizacao"]

        fase = registro.get("fase")
        fase_rotulo = _extrair_rotulo(fase, ("fase", "nome", "descricao"))
        fase_codigo = _extrair_rotulo(fase, ("id", "codigo")) or fase_rotulo

        tipo = registro.get("tipo")
        tipo_rotulo = _extrair_rotulo(tipo, ("tipo", "nome", "descricao"))

        processes.append(
            ProcessRecord(
                source_native_id=pid,
                attrs={
                    # listagem traz so o esqueleto do contrato; o detalhe vem
                    # por hidratacao em /api/contrato/{id}
                    "contratos_ids": [
                        str(c["id"])
                        for c in (registro.get("contratos") or [])
                        if isinstance(c, Mapping) and c.get("id") is not None
                    ],
                    "numero": numero,
                    "number": numero,
                    "ano": ano,
                    "year": ano,
                    "objeto": objeto,
                    "object": objeto,
                    "titulo": titulo,
                    "title": titulo,
                    "modalidade": titulo,
                    "modality_code": titulo,
                    "modality_raw": titulo,
                    "tipo": tipo_rotulo,
                    "tipo_raw": tipo,
                    "fase": fase_rotulo,
                    "phase": fase_rotulo,
                    "phase_code": fase_codigo,
                    "phase_label": fase_rotulo,
                    "fase_raw": fase,
                    "realizacao": realizacao,
                    "opening_at_source": realizacao,
                    "observacao": registro.get("observacao"),
                },
            )
        )

        anexos = registro.get("anexos")
        if anexos is None:
            anexos = []
        if not isinstance(anexos, list):
            quarantine.append(_quarentena("campo anexos nao e uma lista", f"{ponteiro}.anexos", anexos))
        else:
            for posicao, anexo in enumerate(anexos):
                item = _anexo_para_registro(pid, posicao, anexo, f"{ponteiro}.anexos[{posicao}]", quarantine)
                if item is not None:
                    attachments.append(item)

        contratos = registro.get("contratos")
        if contratos is None:
            contratos = []
        if not isinstance(contratos, list):
            quarantine.append(
                _quarentena(
                    "campo contratos nao e uma lista",
                    f"{ponteiro}.contratos",
                    contratos,
                )
            )
        else:
            for posicao, contrato in enumerate(contratos):
                if isinstance(contrato, Mapping) and all(
                    contrato.get(k) is None for k in ("valor", "empresa", "objeto")
                ):
                    continue  # esqueleto: o contrato real chega pela hidratacao
                item_contrato = _contrato_para_registro(pid, contrato, f"{ponteiro}.contratos[{posicao}]", quarantine)
                if item_contrato is not None:
                    contracts.append(item_contrato)

    signals = {"row_count_declared": len(registros)}
    if not processes:
        return ParseResult(
            batch=NormalizedBatch(
                orgs=(),
                processes=(),
                items=(),
                attachments=tuple(attachments),
                results=(),
                awards=(),
                contracts=tuple(contracts),
                phases=(),
            ),
            quarantine=tuple(quarantine),
            next=(),
            cursor_out=None,
            signals=signals,
            fatal="nenhum registro valido na lista de licitacoes",
        )

    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=tuple(processes),
            items=(),
            attachments=tuple(attachments),
            results=(),
            awards=(),
            contracts=tuple(contracts),
            phases=(),
        ),
        quarantine=tuple(quarantine),
        next=(),
        cursor_out=None,
        signals=signals,
        fatal=None,
    )


class BRBAdapter(Adapter):
    source_code: str = "brb"
    adapter_version: str = "0.1.0"

    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "attachment", "contract"),
        hydration={"enabled": True, "trigger": "new_or_changed", "refresh_per_run": 20},
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
                "User-Agent": "licitamais-brb/0.1.0",
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
        # full_diff sobre a lista unica: uma chamada cobre todos os registros.
        yield _pedido_descoberta()

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        # Anexos vem embutidos; contrato vem esqueleto e precisa do detalhe.
        pid = str(process.source_native_id)
        return tuple(
            FetchRequest(
                endpoint=f"/api/contrato/{cid}",
                method="GET",
                params={},
                body=None,
                headers_extra={"Accept": "application/json"},
                phase="hydrate",
                entity_hint="contract",
                parent_native_id=pid,
                cost_weight=1,
                cursor_out=None,
            )
            for cid in (process.attrs.get("contratos_ids") or [])
        )

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
        try:
            dados = json.loads(decode_brb_bytes(raw.body))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            return _resultado_erro(f"payload nao decodifica como JSON UTF-8: {exc}")

        if raw.request.phase == "hydrate" and raw.request.endpoint.startswith("/api/contrato/"):
            pid = str(raw.request.parent_native_id or "")
            if not isinstance(dados, Mapping) or not pid:
                return _resultado_erro("detalhe de contrato nao e objeto ou sem processo pai")
            quarentena_det: list[QuarantineItem] = []
            contrato = _contrato_para_registro(pid, dados, raw.request.endpoint, quarentena_det)
            return ParseResult(
                batch=NormalizedBatch(
                    orgs=(),
                    processes=(),
                    items=(),
                    attachments=(),
                    results=(),
                    awards=(),
                    contracts=(contrato,) if contrato else (),
                    phases=(),
                ),
                quarantine=tuple(quarentena_det),
                next=(),
                cursor_out=None,
                signals={},
                fatal=None,
            )
        if not isinstance(dados, list):
            return _resultado_erro("raiz do payload nao e uma lista")
        if not dados:
            return _resultado_erro("lista vazia de licitacoes")
        return _parse_lista(dados)
