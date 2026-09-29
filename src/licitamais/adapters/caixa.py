"""Adaptador da Caixa Economica Federal via PNCP (source_code='caixa').

Fonte: GET {base_url}/api/consulta/v1/contratos com dataInicial/dataFinal
AAAAMMDD, cnpjOrgao=00360305000104, pagina e tamanhoPagina=500. Responde
200 com envelope {data, totalRegistros, totalPaginas} ou 204 com corpo
vazio quando nao ha registros. O filtro de data da API e pela PUBLICACAO
no PNCP, nao pela assinatura: o adaptador so emite contrato com
dataAssinatura maior ou igual a 2023-01-01 e classificado como TI por
eh_ti; o resto e contado como filtrado, sem quarentena.

Coleta incremental diaria: GET /api/consulta/v1/contratos/atualizacao com
os mesmos parametros (mesmo envelope, mesmos campos). Na primeira carga o
plano emite as janelas trimestrais de 2023 a 2026; com cursor caixa_modo =
incremental, emite a janela dos ultimos 10 dias no /atualizacao.

Valor: valorGlobal vem 0.0 na maioria dos registros; usa valorInicial e
valorGlobal so se maior que zero (medido 2026-09-25). Cada contrato TI
vira um ContractRecord com processo pai, pois a fonte nao liga contrato a
licitacao e o loader poe contrato sem pai em quarentena. Textos chegam em
UTF-8 por vezes com caractere invalido: decodifica com replace.

strategy=full_diff, auth=none, listing_order_stable=true,
deletion_semantics='absence' com carencia de 2 runs.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
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

CNPJ_CAIXA = "00360305000104"
ANO_MINIMO = 2023
ANO_MINIMO_ASSINATURA = "2023-01-01"
TIMEOUT_SEGUNDOS = 60

CONSULTA_ENDPOINT = "/api/consulta/v1/contratos"
ATUALIZACAO_ENDPOINT = "/api/consulta/v1/contratos/atualizacao"

# campos obrigatorios do payload de descoberta: totalRegistros e data vivem
# no envelope; numeroControlePNCP, objetoContrato e dataAssinatura vivem em
# cada registro de data[]. A sonda canaria lê o envelope, entao o primeiro
# nivel usa ("data",); os campos de registro sao conferidos no teste r52.
CAMPOS_SONDA = ("data",)
_CAMPOS_REGISTRO = ("numeroControlePNCP", "objetoContrato", "dataAssinatura")

_JANELAS_PRIMEIRA_CARGA = tuple(
    (f"{ano}{ini}", f"{ano}{fim}")
    for ano in range(ANO_MINIMO, 2027)
    for ini, fim in (("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231"))
)

_TAMANHO_PAGINA = "500"

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

_TERMOS_TI = (
    "tecnologia da informacao",
    "tecnologia de informacao",
    "sistema de informacao",
    "seguranca da informacao",
    "fabrica de software",
    "desenvolvimento de software",
    "desenvolvimento de sistema",
    "sustentacao de sistema",
    "manutencao de sistema",
    "alocacao de profissional",
    "service desk",
    "help desk",
    "operacao de infraestrutura",
    "infraestrutura de rede",
    "infraestrutura de ti",
    "data center",
    "suporte tecnico de ti",
    "suporte de ti",
    "consultoria de ti",
    "consultoria em ti",
    "licenca de software",
    "licenca de uso",
    "subscricao de software",
    "assinatura de software",
    "software como servico",
    "computacao em nuvem",
    "servico em nuvem",
    "telecomunicacao de dados",
    "servico de telecomunicacao",
    "servicos de telecomunicacao",
    "servicos de telecomunicacoes",
    "rede de telecomunicacao",
    "rede de telecomunicacoes",
    "link de dados",
    "circuito de dados",
    "rede de dados",
    "fibra otica",
    "portal eletronico",
    "portal de governanca",
    "plataforma eletronica",
    "sistema informatizado",
    "solucao de ti",
    "solucao tecnologica",
    "solucao de controle de acesso",
    "controle de acesso",
    "atualizacao tecnologica",
    "suporte tecnico",
    "suporte tecnologico",
    "hardware e software",
    "equipamento de informatica",
    "equipamento de ti",
    "microinformatica",
    "authorization server",
    "fido server",
    "fornecimento de dados",
    "dados cadastrais",
    "licenca de uso do sistema",
    "licencas de uso do sistema",
    "banco de dados",
    "governanca de dados",
    "tratamento de dados",
    "seguranca cibernetica",
    "ciberseguranca",
    "red team",
    "teste de intrusao",
    "testes de intrusao",
    "impressao distribuida",
    "impressao gerenciada",
    " mips ",
    "mainframe",
    "solucao de armazenamento",
    "storage",
    "roteadores",
    "licenciamento de uso",
    "licenciamento de software",
    # r57: vocabulario do BB/BBTS
    "conectividade",
    "comunicacao de dados",
    "rede internet",
    "acesso a internet",
    "outsourcing de impressao",
    "software",
    "nuvem",
    "cloud",
    "(erp)",
    "certificado digital",
    "certificados digitais",
    "tablets",
    "smartphones",
    "via api",
    "base de dados",
    "network packet broker",
    "mensagens curtas",
    "administracao de identidade",
    "disco rigido",
)

# r57: vetam sempre, ate com termo forte ("ar-condicionado no data center" nao e TI)
_VETOS = (
    "ar-condicionado",
    "ar condicionado",
    "compressor",
    "construcao civil",
    "impermeabilizacao",
    "combustiveis",
    "catracas",
    "patrocinio",
    "alarme monitorado",
    "ensino de idiomas",
    "portal eletronico de consignacao",  # r58: um contrato por convenio, fora do recorte
)

_EXCLUSOES = (
    "ar condicionado",
    "sistema de ar",
    "climatizacao",
    "sistema de sinalizacao",
    "sinalizacao interna",
    "sinalizacao externa",
    "carenagem",
    "sistema de combate a incendio",
    "rede eletrica",
    "rede hidraulica",
    "manutencao predial",
    "vigilancia",
    "limpeza",
    "jardinagem",
    "controle de pragas",
    "obra",
    "engenharia civil",
    "servico comum de engenharia",
    "servicos comuns de engenharia",
    "mobiliario",
    "imobiliaria",
    "curso",
    "palestra",
    "evento",
    "treinamento",
    "transporte de valores",
    "custodia de valores",
    "material de expediente",
)

_TERMOS_TI_FORTES = (
    "tecnologia da informacao",
    "tecnologia de informacao",
    "sistema de informacao",
    "seguranca da informacao",
    "fabrica de software",
    "desenvolvimento de software",
    "desenvolvimento de sistema",
    "sustentacao de sistema",
    "data center",
    "authorization server",
    "fido server",
)

_PADRAO_TI = re.compile("|".join(re.escape(t) for t in _TERMOS_TI))
_PADRAO_TI_FORTE = re.compile("|".join(re.escape(t) for t in _TERMOS_TI_FORTES))
# palavra inteira: "curso" nao pode casar dentro de "recursos" (r57)
_PADRAO_EXCLUSAO = re.compile(r"\b(?:" + "|".join(re.escape(t) for t in _EXCLUSOES) + r")\b")
_PADRAO_VETO = re.compile("|".join(re.escape(t) for t in _VETOS))


def _sem_acento(texto: str) -> str:
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def eh_ti(objeto: str, complemento: str | None) -> bool:
    """Diz se o contrato e de tecnologia da informacao pelo objeto.

    Regex por termos sobre objeto + informacaoComplementar, sem acento e em
    minusculo. Fornecedor tipico de TI nao basta: decide pelo objeto.
    Exclusoes claras (ar condicionado, sinalizacao, obra, limpeza etc.)
    vetam mesmo com termo de TI fraco; termo de TI inequivoco
    (tecnologia da informacao, fabrica de software, data center e afins)
    vence a exclusao.
    """
    texto = _sem_acento(f"{objeto or ''} {complemento or ''}")
    if not texto.strip():
        return False
    if _PADRAO_TI.search(texto) is None or _PADRAO_VETO.search(texto) is not None:
        return False
    if not _eh_exclusao_clara(texto):
        return True
    return _PADRAO_TI_FORTE.search(texto) is not None


def _eh_exclusao_clara(texto_normalizado: str) -> bool:
    return _PADRAO_EXCLUSAO.search(texto_normalizado) is not None


def _texto(valor: object) -> str:
    if valor is None:
        return ""
    return str(valor).replace(" ", " ").strip()


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


def _valor_raw(registro: Mapping) -> float | None:
    try:
        inicial = registro.get("valorInicial")
        if isinstance(inicial, bool):
            inicial = None
        if inicial is not None and float(inicial) > 0:
            return float(inicial)
    except (TypeError, ValueError):
        pass
    try:
        total = registro.get("valorGlobal")
        if isinstance(total, bool):
            return None
        if total is not None and float(total) > 0:
            return float(total)
    except (TypeError, ValueError):
        pass
    return None


def _valor_centavos(valor: float | None) -> int | None:
    if valor is None:
        return None
    try:
        quantia = Decimal(str(valor))
    except (InvalidOperation, ValueError):
        return None
    return int((quantia * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _data_iso(valor: object) -> str | None:
    texto = _texto(valor)
    if not texto:
        return None
    encontro = re.match(r"(\d{4})-(\d{2})-(\d{2})", texto)
    if encontro is None:
        return None
    return f"{encontro.group(1)}-{encontro.group(2)}-{encontro.group(3)}"


def _ano_assinatura(valor: object) -> int | None:
    iso = _data_iso(valor)
    if iso is None:
        return None
    try:
        return int(iso[:4])
    except ValueError:
        return None


def _fornecedor_cnpj(registro: Mapping) -> str | None:
    if str(registro.get("tipoPessoa") or "").strip().upper() != "PJ":
        return None
    digitos = re.sub(r"\D", "", _texto(registro.get("niFornecedor")))
    return digitos or None


# r57: o mesmo adaptador serve BB e BBTS; o orgao vem do proprio registro
_ORGAOS = {
    CNPJ_CAIXA: ("caixa", "CAIXA"),
    "00000000000191": ("bb", "BB"),
    "42318949001318": ("bbts", "BBTS"),
}


def _orgao(registro: Mapping) -> tuple[str, str, str]:
    entidade = registro.get("orgaoEntidade")
    cnpj = _texto(entidade.get("cnpj")) if isinstance(entidade, Mapping) else ""
    cnpj = cnpj or CNPJ_CAIXA
    prefixo, org = _ORGAOS.get(cnpj, ("pncp", cnpj))
    return prefixo, org, cnpj


def _id_processo(registro: Mapping) -> str:
    prefixo = _orgao(registro)[0]
    compra = _texto(registro.get("numeroControlePncpCompra"))
    if compra:
        return f"{prefixo}:{compra}"
    return f"{prefixo}:contrato:{_texto(registro.get('numeroControlePNCP'))}"


def _numero_processo(registro: Mapping) -> str | None:
    compra = _texto(registro.get("numeroControlePncpCompra"))
    if compra:
        return compra
    return _texto(registro.get("numeroControlePNCP")) or None


def _tipo_nome(registro: Mapping) -> str | None:
    tipo = registro.get("tipoContrato")
    if isinstance(tipo, Mapping):
        nome = _texto(tipo.get("nome"))
        return nome or None
    return None


def _unidade(registro: Mapping) -> tuple[str | None, str | None]:
    unidade = registro.get("unidadeOrgao")
    if not isinstance(unidade, Mapping):
        return None, None
    uf = _texto(unidade.get("ufSigla")) or None
    nome = _texto(unidade.get("nomeUnidade")) or None
    return uf, nome


def _contrato_para_registros(
    registro: Mapping,
) -> tuple[ProcessRecord, ContractRecord]:
    controle = _texto(registro.get("numeroControlePNCP"))
    objeto = _texto(registro.get("objetoContrato")) or None
    pid = _id_processo(registro)
    uf, unidade = _unidade(registro)
    ano_contrato = registro.get("anoContrato")
    try:
        ano_contrato_int = int(ano_contrato) if ano_contrato is not None else None
    except (TypeError, ValueError):
        ano_contrato_int = None
    try:
        sequencial = registro.get("sequencialContrato")
        sequencial_int = int(sequencial) if sequencial is not None else None
    except (TypeError, ValueError):
        sequencial_int = None
    processo = ProcessRecord(
        source_native_id=pid,
        attrs={
            "number": _numero_processo(registro),
            "object": objeto,
            "org_native_id": _orgao(registro)[1],
            "modality_raw": _tipo_nome(registro),
            "year": _ano_assinatura(registro.get("dataAssinatura")) or ANO_MINIMO,
            "published_at_source": _data_iso(registro.get("dataPublicacaoPncp")),
            "opening_at_source": _data_iso(registro.get("dataAssinatura")),
            "cnpj_orgao": _orgao(registro)[2],
            "ano_contrato": ano_contrato_int,
            "sequencial_contrato": sequencial_int,
            "uf": uf,
            "unidade": unidade,
        },
    )
    bruto = _valor_raw(registro)
    contrato = ContractRecord(
        source_native_id=controle,
        attrs={
            "process_native_id": pid,
            "number": controle,
            "object": objeto,
            "org_native_id": _orgao(registro)[1],
            "modality_raw": _tipo_nome(registro),
            "supplier_cnpj": _fornecedor_cnpj(registro),
            "supplier_name_raw": _texto(registro.get("nomeRazaoSocialFornecedor")) or None,
            "value_raw": bruto,
            "value_cents": _valor_centavos(bruto),
            "signed_at_source": _data_iso(registro.get("dataAssinatura")),
            "vigency_start": _data_iso(registro.get("dataVigenciaInicio")),
            "vigency_end": _data_iso(registro.get("dataVigenciaFim")),
        },
    )
    return processo, contrato


def _parse_lista(registros: list[object], janela: str) -> ParseResult:
    vistos: set[str] = set()
    processes: list[ProcessRecord] = []
    contracts: list[ContractRecord] = []
    filtrados = 0
    for idx, registro in enumerate(registros):
        _ponteiro = f"[{idx}]"
        if not isinstance(registro, Mapping):
            filtrados += 1
            continue
        controle = _texto(registro.get("numeroControlePNCP"))
        if not controle:
            filtrados += 1
            continue
        assinatura = _data_iso(registro.get("dataAssinatura"))
        if assinatura is None or assinatura < ANO_MINIMO_ASSINATURA:
            filtrados += 1
            continue
        if not eh_ti(
            str(registro.get("objetoContrato") or ""),
            str(registro.get("informacaoComplementar") or ""),
        ):
            filtrados += 1
            continue
        processo, contrato = _contrato_para_registros(registro)
        if processo.source_native_id not in vistos:
            vistos.add(processo.source_native_id)
            processes.append(processo)
        contracts.append(contrato)
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
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={"row_count_declared": len(contracts), "janela": janela, "filtrados": filtrados},
        fatal=None,
    )


def _pedido_consulta(data_inicial: str, data_final: str, pagina: str, cnpj: str = CNPJ_CAIXA) -> FetchRequest:
    return FetchRequest(
        endpoint=CONSULTA_ENDPOINT,
        method="GET",
        params={
            "dataInicial": data_inicial,
            "dataFinal": data_final,
            "cnpjOrgao": cnpj,
            "pagina": pagina,
            "tamanhoPagina": _TAMANHO_PAGINA,
        },
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="contract",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def _pedido_atualizacao(data_inicial: str, data_final: str, pagina: str, cnpj: str = CNPJ_CAIXA) -> FetchRequest:
    pedido = _pedido_consulta(data_inicial, data_final, pagina, cnpj)
    return FetchRequest(
        endpoint=ATUALIZACAO_ENDPOINT,
        method=pedido.method,
        params=dict(pedido.params),
        body=None,
        headers_extra=dict(pedido.headers_extra),
        phase=pedido.phase,
        entity_hint=pedido.entity_hint,
        parent_native_id=None,
        cost_weight=pedido.cost_weight,
        cursor_out=None,
    )


class CaixaAdapter(Adapter):
    source_code: str = "caixa"
    adapter_version: str = "0.1.0"
    cnpj: str = CNPJ_CAIXA

    capabilities = Capabilities(
        strategy="full_diff",
        entities=("process", "contract"),
        hydration={"enabled": False},
        listing_order_stable=True,
        supports_conditional_get=False,
        deletion_semantics="absence",
        rate={
            "min_delay_ms": 1000,
            "jitter_ms": 200,
            "max_calls_per_run": 400,
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
                "User-Agent": "licitamais-caixa/0.1.0",
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
        if dict(ctx.cursors).get("caixa_modo") == "incremental":
            hoje = datetime.now(UTC).date()
            inicio = (hoje - timedelta(days=10)).strftime("%Y%m%d")
            fim = hoje.strftime("%Y%m%d")
            yield _pedido_atualizacao(inicio, fim, "1", self.cnpj)
            return
        for data_inicial, data_final in _JANELAS_PRIMEIRA_CARGA:
            yield _pedido_consulta(data_inicial, data_final, "1", self.cnpj)

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
        tentativa = 0
        while True:
            resposta = http.request(
                req.method,
                url,
                params=dict(req.params),
                headers=dict(req.headers_extra),
                timeout=TIMEOUT_SEGUNDOS,
            )
            duracao_ms = int((time.perf_counter() - inicio) * 1000)
            agora = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            if resposta.status_code == 204:
                return FetchedPage(
                    request=req,
                    status=200,
                    headers={"content-type": "application/json", "x-pncp-204": "1"},
                    body=b'{"data": [], "totalRegistros": 0, "totalPaginas": 1}',
                    fetched_at=agora,
                    duration_ms=duracao_ms,
                )
            corpo = resposta.content
            if isinstance(corpo, str):
                corpo = corpo.encode("utf-8")
            if resposta.status_code == 200 and (not corpo or not corpo.strip()):
                if tentativa >= 3:
                    return FetchFailure(
                        request=req,
                        error=f"HTTP 200 com corpo vazio em {req.endpoint} apos 4 tentativas",
                        fetched_at=agora,
                        duration_ms=duracao_ms,
                        status=resposta.status_code,
                    )
                tentativa += 1
                time.sleep(1)
                continue
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
            dados = json.loads(bytes(raw.body).decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            return _resultado_erro(f"payload nao decodifica como JSON: {exc}")
        if isinstance(dados, dict) and raw.request.params.get("pagina") == "1" and not dados.get("data"):
            pass
        if not isinstance(dados, Mapping):
            return _resultado_erro("raiz do payload nao e um objeto")
        registros = dados.get("data")
        if registros is None:
            return _resultado_erro("envelope sem campo data")
        if not isinstance(registros, list):
            return _resultado_erro("campo data nao e uma lista")
        total = dados.get("totalRegistros")
        try:
            total_int = int(total) if total is not None else len(registros)
        except (TypeError, ValueError):
            total_int = len(registros)
        try:
            total_paginas = int(dados.get("totalPaginas") or 1)
        except (TypeError, ValueError):
            total_paginas = 1
        janela = f"{raw.request.params.get('dataInicial')}-{raw.request.params.get('dataFinal')}"
        resultado = _parse_lista(registros, janela)
        proximos: tuple[FetchRequest, ...] = ()
        try:
            pagina_atual = int(str(raw.request.params.get("pagina") or "1"))
        except ValueError:
            pagina_atual = 1
        if pagina_atual < total_paginas:
            base = _pedido_atualizacao if raw.request.endpoint == ATUALIZACAO_ENDPOINT else _pedido_consulta
            proximos = (
                base(
                    str(raw.request.params.get("dataInicial") or ""),
                    str(raw.request.params.get("dataFinal") or ""),
                    str(pagina_atual + 1),
                    str(raw.request.params.get("cnpjOrgao") or self.cnpj),
                ),
            )
        if not resultado.batch.contracts and not resultado.batch.processes:
            # pagina sem TI 2026: lote vazio com sinal pagina_filtrada; o runner
            # trata como pagina so de navegacao (nada a carregar, sem erro).
            return ParseResult(
                batch=resultado.batch,
                quarantine=resultado.quarantine,
                next=proximos,
                cursor_out=None,
                signals={"row_count_declared": total_int, "janela": janela, "pagina_filtrada": True},
                fatal=None,
            )
        return ParseResult(
            batch=resultado.batch,
            quarantine=resultado.quarantine,
            next=proximos,
            cursor_out=None,
            signals={"row_count_declared": total_int, "janela": janela},
            fatal=None,
        )


class BBAdapter(CaixaAdapter):
    """Banco do Brasil (r57): mesmos contratos TI via PNCP, CNPJ proprio."""

    source_code: str = "bb"
    cnpj: str = "00000000000191"


class BBTSAdapter(CaixaAdapter):
    """BB Tecnologia e Servicos; CNPJ confirmado na API do PNCP."""

    source_code: str = "bbts"
    cnpj: str = "42318949001318"
