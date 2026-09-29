"""Adaptador do SESC Nacional (WordPress REST, sem login).

Fonte: https://www.sesc.com.br/licitacoes/ (base ``.../licitacoes``).

- Listagem: ``GET /wp-json/wp/v2/pages?per_page=100&page=N`` com
  ``_fields=id,date,slug,link,title``. Total real em 2026-09-24:
  ``X-WP-Total=517`` em 6 folhas (``X-WP-TotalPages=6``). 512 registros sao
  ``termo-de-licitacao-*`` (em escopo); 5 paginas estaticas
  (``licitacoes-em-andamento-v2`` etc.) vao para quarentena, sem fatal.
- Detalhe: ``GET /wp-json/wp/v2/pages/{id}``; texto livre em
  ``content.rendered`` (HTML). Vencedor em texto livre, sem CNPJ e sem valor
  estruturado (sem ``R$`` nas amostras reais). Padroes reais: ``homologada em
  DD/MM/AAAA ao(s) seguinte(s) licitante(s):`` seguido de linhas
  ``Lote NN: NOME.``; modalidade em ``modalidade PREGÃO ELETRÔNICO``,
  ``CONSULTA PÚBLICA SESC/DN`` ou ``CREDENCIAMENTO SESC/DN``; abertura em
  ``Abertura das propostas`` / ``Sessão Pública de Lances`` /
  ``Recebimento de propostas``.
- O ano vem do sufixo do numero (``0041/25-PG`` -> 2025); a ``date`` do WP e
  data de migracao (2025-11) e nunca e usada.
- Detalhes sem homologacao (consulta publica, credenciamento por etapas) geram
  so o processo, sem resultado e sem award.

strategy=two_phase, auth=none, listing_order_stable=true,
deletion_semantics='absence' com carencia de 2 runs. Decode BOM-tolerante
(utf-8-sig); os arquivos de amostra tem BOM.
"""

from __future__ import annotations

import html
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

# campos obrigatorios da listagem WP; a sonda canaria exige que cada registro
# os tenha
CAMPOS_SONDA = ("id", "slug", "title")

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

_PREFIXO_TERMO = "termo-de-licitacao-"

# \w no miolo: numeros reais tem letras (18D/0087-PG, 19D/0016-CC);
# \d{2,} sem teto: ha sequencias de 5 digitos (18D/00015-PG).
# Guloso para incluir o sufixo (-PG); sem ancora de espaco porque o titulo
# as vezes cola o sufixo.
_RE_NUMERO = re.compile(r"(\d[\w./-]*\d?/\d{2,}(?:-\w+)?)")
_RE_MODALIDADE = re.compile(r"modalidade\s+([^,.;]{1,80})", re.IGNORECASE)
_RE_HOMOLOGACAO = re.compile(r"homologad[ao]\s+em\s+(\d{2}/\d{2}/\d{4})", re.IGNORECASE)
_RE_VALOR_LOTE = re.compile(r"R\$\s*(\d{1,3}(?:\.\d{3})*,\d{2}|\d+,\d{2}|\d+(?:\.\d{3})+|\d+)")
_RE_OBJETO_FALLBACK = re.compile(
    r"Registro de pre[^.]{0,300}|Contrata[^.]{0,300}|Aquisi[^.]{0,300}"
    r"|Presta[^.]{0,300}|Fornecimento[^.]{0,300}",
    re.IGNORECASE,
)

_ROTULOS_ABERTURA = (
    "Abertura das propostas",
    "Sess.o P.blica de Lances",
    "Recebimento de propostas",
)

_MARCADORES_CORTE_OBJETO = (
    "resultado",
    "aviso de",
    "adendo",
    "credenciamento sesc",
    "consulta p",
    "comunica a realiza",
)

_MARCADORES_FIM_BLOCO = (
    "rio de janeiro",
    "aviso",
    "adendo",
    "ades",
    "vide arquivo",
    "anexo",
)


def valor_br_para_centavos(valor: object) -> int | None:
    """Converte valor monetario BR ('R$ 1.234,56') em centavos."""
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, int | float | Decimal):
        try:
            quantia = Decimal(str(valor))
        except (InvalidOperation, ValueError):
            return None
        return int((quantia * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    bruto = str(valor).replace("R$", "").replace(" ", "").strip()
    if not bruto:
        return None
    if "," in bruto and "." in bruto:
        if bruto.rfind(",") > bruto.rfind("."):
            bruto = bruto.replace(".", "").replace(",", ".")
        else:
            bruto = bruto.replace(",", "")
    elif "," in bruto:
        bruto = bruto.replace(",", ".")
    elif "." in bruto:
        if re.fullmatch(r"\d{1,3}(\.\d{3})+", bruto):
            bruto = bruto.replace(".", "")
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
    # utf-8-sig tolera o BOM das amostras e do WP; estrito fora isso
    return json.loads(bytes(corpo).decode("utf-8-sig"))


def _cabecalho(headers: object, nome: str) -> object:
    if not isinstance(headers, Mapping):
        return None
    alvo = str(nome).lower()
    for chave, valor in headers.items():
        if str(chave).lower() == alvo:
            return valor
    return None


def _inteiro(valor: object, padrao: int | None = None) -> int | None:
    try:
        if valor is None:
            return padrao
        if isinstance(valor, str) and not valor.strip():
            return padrao
        return int(str(valor).strip())
    except (TypeError, ValueError):
        return padrao


def _renderizado(valor: object) -> str:
    if isinstance(valor, Mapping):
        rend = valor.get("rendered")
        return str(rend).strip() if rend is not None else ""
    if isinstance(valor, str):
        return valor.strip()
    return ""


def _extrair_numero(texto: object) -> str | None:
    achado = _RE_NUMERO.search(str(texto or ""))
    if achado is None:
        return None
    return achado.group(1).strip() or None


def _ano_do_numero(numero: str) -> int | None:
    achado = re.search(r"/(\d{4})(?:-|$|\s)", numero)
    if achado is not None:
        return int(achado.group(1))
    achado = re.search(r"/(\d{2})(?:-|$|\s)", numero)
    if achado is not None:
        return 2000 + int(achado.group(1))
    return None


def _numero_do_slug(slug: str) -> str | None:
    if not slug.startswith(_PREFIXO_TERMO):
        return None
    nucleo = slug[len(_PREFIXO_TERMO) :].strip("-")
    partes = [p for p in nucleo.split("-") if p]
    if len(partes) >= 3:
        return f"{'-'.join(partes[:-2])}/{partes[-2]}-{partes[-1]}".upper()
    if len(partes) == 2:
        return f"{partes[0]}/{partes[1]}".upper()
    return None


def _iso_data_br(valor: object) -> str | None:
    achado = re.fullmatch(r"(\d{2})/(\d{2})/(\d{4})", str(valor or "").strip())
    if achado is None:
        return None
    dia, mes, ano = achado.groups()
    return f"{ano}-{mes}-{dia}"


def _html_para_texto(html_bruto: object) -> str:
    texto = re.sub(r"<[^>]+>", " ", str(html_bruto))
    texto = html.unescape(texto)
    return re.sub(r"\s+", " ", texto).strip()


def _extrair_objeto(texto: str) -> str | None:
    if not texto:
        return None
    baixo = texto.lower()
    corte = len(texto)
    for marcador in _MARCADORES_CORTE_OBJETO:
        idx = baixo.find(marcador)
        if idx != -1 and idx < corte:
            corte = idx
    lider = texto[:corte].strip(" \t-–—;:,.")
    if lider and len(lider) >= 10:
        return lider[:2000]
    # fallback: sentenca de objeto em qualquer ponto do texto; ignora linhas
    # de adesao ("REGISTRO DE PREÇOS Vide arquivo anexo") e prefere a
    # sentenca com "para ..." (objeto real: "...para aquisição de ...")
    candidatos = [m.group(0).strip() for m in _RE_OBJETO_FALLBACK.finditer(texto or "")]
    candidatos = [
        c for c in candidatos if len(c) >= 20 and not re.search(r"vide\s+arquivo|vide\s+anexo", c, re.IGNORECASE)
    ]
    for candidato in candidatos:
        if re.search(r"\bpara\b", candidato, re.IGNORECASE):
            return candidato[:2000]
    if candidatos:
        return max(candidatos, key=len)[:2000]
    if lider and len(lider) >= 10:
        return lider[:2000]
    return None


def _extrair_modalidade(titulo: str, texto: str) -> str | None:
    achado = _RE_MODALIDADE.search(texto or "")
    if achado is not None:
        bruto = achado.group(1).strip().lower()
        bruto = re.sub(r"\s+do tipo.*$", "", bruto).strip()
        bruto = re.sub(r"\s+regida.*$", "", bruto).strip()
        if "preg" in bruto:
            return "pregão eletrônico"
        if "concorr" in bruto:
            return "concorrência"
        if "convite" in bruto:
            return "convite"
        if "concurso" in bruto:
            return "concurso"
        if "leil" in bruto:
            return "leilão"
        if bruto:
            return bruto[:60]
    combinado = f"{titulo or ''} {(texto or '')[:2000]}"
    if re.search(r"credenciamento", combinado, re.IGNORECASE):
        return "credenciamento"
    if re.search(r"consulta\s*p", combinado, re.IGNORECASE):
        return "consulta pública"
    return None


def _extrair_homologacao(texto: str) -> str | None:
    achado = _RE_HOMOLOGACAO.search(texto or "")
    return _iso_data_br(achado.group(1)) if achado is not None else None


def _extrair_abertura(texto: str) -> str | None:
    texto = texto or ""
    for rotulo in _ROTULOS_ABERTURA:
        achado = re.search(rotulo, texto, re.IGNORECASE)
        if achado is None:
            continue
        data = re.search(r"(\d{2}/\d{2}/\d{4})", texto[achado.end() : achado.end() + 300])
        if data is not None:
            return _iso_data_br(data.group(1))
    return None


def _extrair_vencedores(texto: str, homologada_em: str | None) -> list[tuple[str, str, str | None, int | None]]:
    if not homologada_em:
        return []
    if not re.search(r"resultad\w*\s+final|homologad", texto or "", re.IGNORECASE):
        return []
    marca = _RE_HOMOLOGACAO.search(texto)
    inicio = marca.end() if marca is not None else 0
    baixo = texto.lower()
    fim = len(texto)
    for marcador in _MARCADORES_FIM_BLOCO:
        idx = baixo.find(marcador, inicio)
        if idx != -1 and idx < fim:
            fim = idx
    bloco = texto[inicio:fim]
    inicios = list(re.finditer(r"Lote\s+(\d+)\s*:", bloco, re.IGNORECASE))
    vencedores: list[tuple[str, str, str | None, int | None]] = []
    vistos: set[tuple[str, str]] = set()
    for pos, achado in enumerate(inicios):
        lote = achado.group(1).zfill(2)
        proximo = inicios[pos + 1].start() if pos + 1 < len(inicios) else len(bloco)
        segmento = bloco[achado.end() : proximo].strip()
        valor = _RE_VALOR_LOTE.search(segmento)
        if valor is not None:
            bruto_valor: str | None = valor.group(0)
            centavos = valor_br_para_centavos(bruto_valor)
            nome_bruto = segmento[: valor.start()]
        else:
            bruto_valor = None
            centavos = None
            nome_bruto = segmento
        nome = re.sub(r"\s+", " ", nome_bruto).strip(" \t-–—;:,.")
        nome = nome.rstrip(".").strip()
        if len(nome) < 5:
            continue
        if re.match(r"^(vide|arquivo|anexo)", nome, re.IGNORECASE):
            continue
        chave = (lote, nome.lower())
        if chave in vistos:
            continue
        vistos.add(chave)
        vencedores.append((lote, nome, bruto_valor, centavos))
    if not vencedores:
        unico = re.search(r"licitante:\s*([A-ZÀ-Ú0-9][^.]{5,200})", bloco)
        if unico is not None:
            nome = re.sub(r"\s+", " ", unico.group(1)).strip(" \t-–—;:,.")
            nome = nome.rstrip(".").strip()
            if len(nome) >= 5 and not re.match(r"^(vide|arquivo|anexo)", nome, re.IGNORECASE):
                vencedores.append(("01", nome, None, None))
    return vencedores


def pedido_listagem(pagina: int) -> FetchRequest:
    return FetchRequest(
        endpoint="/wp-json/wp/v2/pages",
        method="GET",
        params={
            "per_page": "100",
            "page": str(int(pagina)),
            "_fields": "id,date,slug,link,title",
        },
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def pedido_detalhe(pid: str, wp_id: int) -> FetchRequest:
    return FetchRequest(
        endpoint=f"/wp-json/wp/v2/pages/{int(wp_id)}",
        method="GET",
        params={},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="hydrate",
        entity_hint="result",
        parent_native_id=str(pid),
        cost_weight=1,
        cursor_out=None,
    )


def _parse_listagem(raw: FetchedPage) -> ParseResult:
    try:
        dados = _decodificar(raw.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _resultado_erro(f"lista nao decodifica como JSON: {exc}")
    if not isinstance(dados, list):
        return _resultado_erro("lista de paginas nao e uma lista")
    declarado = _inteiro(_cabecalho(raw.headers, "X-WP-Total"), None)
    total_paginas = _inteiro(_cabecalho(raw.headers, "X-WP-TotalPages"), None)
    total = declarado if declarado is not None else len(dados)
    pagina = _inteiro(raw.request.params.get("page"), 1) or 1
    por_pagina = _inteiro(raw.request.params.get("per_page"), 100) or 100

    processos: list[ProcessRecord] = []
    quarantine: list[QuarantineItem] = []
    for idx, registro in enumerate(dados):
        ponteiro = f"[{idx}]"
        if not isinstance(registro, Mapping) or not registro.get("id") or not registro.get("slug"):
            quarantine.append(_quarentena("registro sem id ou slug", ponteiro, registro))
            continue
        slug = str(registro.get("slug"))
        if not slug.startswith(_PREFIXO_TERMO):
            quarantine.append(
                _quarentena(
                    "fora de escopo: pagina nao e termo de licitacao",
                    ponteiro,
                    registro,
                )
            )
            continue
        titulo = _renderizado(registro.get("title"))
        numero = _extrair_numero(titulo)
        if not numero or "/" not in numero:
            quarantine.append(_quarentena("numero do processo ausente no titulo", ponteiro, registro))
            continue
        try:
            wp_id = int(registro.get("id"))  # type: ignore[arg-type]
        except (TypeError, ValueError):
            quarantine.append(_quarentena("registro sem id ou slug", ponteiro, registro))
            continue
        if not wp_id:
            quarantine.append(_quarentena("registro sem id ou slug", ponteiro, registro))
            continue
        pid = f"sesc:{wp_id}"
        processos.append(
            ProcessRecord(
                source_native_id=pid,
                attrs={
                    "number": numero,
                    "year": _ano_do_numero(numero),
                    "wp_id": wp_id,
                    "slug": slug,
                    "link": registro.get("link"),
                    "title": titulo,
                    "object": None,
                    "org_native_id": "SESC-DN",
                    "modality_raw": None,
                    "modality_code": None,
                    "sufixo_modalidade": (numero.rsplit("-", 1)[-1].upper() if "-" in numero else None),
                    "published_at_source": None,
                    "opening_at_source": None,
                    "source_updated_at_source": None,
                },
            )
        )

    if total_paginas is not None:
        proximos = (pedido_listagem(pagina + 1),) if pagina < total_paginas else ()
    else:
        proximos = (pedido_listagem(pagina + 1),) if len(dados) >= por_pagina else ()
    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=tuple(processos),
            items=(),
            attachments=(),
            results=(),
            awards=(),
            contracts=(),
            phases=(),
        ),
        quarantine=tuple(quarantine),
        next=proximos,
        cursor_out=None,
        signals={"row_count_declared": total},
        fatal=None,
    )


def _parse_detalhe(raw: FetchedPage) -> ParseResult:
    pid_bruto = raw.request.parent_native_id
    pid = str(pid_bruto).strip() if pid_bruto is not None else ""
    if not pid:
        return _resultado_erro("detalhe sem processo pai (parent_native_id obrigatorio)")
    try:
        dados = _decodificar(raw.body)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return _resultado_erro(f"detalhe nao decodifica como JSON: {exc}")
    if not isinstance(dados, Mapping):
        return _resultado_erro("detalhe sem id ou conteudo")
    wp_id: int | None = None
    try:
        bruto_id = dados.get("id")
        if bruto_id is not None and str(bruto_id).strip() != "":
            wp_id = int(bruto_id)
    except (TypeError, ValueError):
        wp_id = None
    conteudo = dados.get("content")
    if isinstance(conteudo, Mapping):
        html_bruto = conteudo.get("rendered")
    elif isinstance(conteudo, str):
        html_bruto = conteudo
    else:
        html_bruto = None
    if not wp_id or not html_bruto:
        return _resultado_erro("detalhe sem id ou conteudo")

    slug = str(dados.get("slug") or "")
    titulo = _renderizado(dados.get("title"))
    numero = _extrair_numero(titulo)
    if not numero or "/" not in numero:
        numero = _numero_do_slug(slug)
    if not numero or "/" not in numero:
        return _resultado_erro("numero do processo ausente")

    texto = _html_para_texto(html_bruto)
    objeto = _extrair_objeto(texto)
    modalidade = _extrair_modalidade(titulo, texto)
    abertura = _extrair_abertura(texto)
    homologada_em = _extrair_homologacao(texto)

    processo = ProcessRecord(
        source_native_id=pid,
        attrs={
            "number": numero,
            "year": _ano_do_numero(numero),
            "wp_id": wp_id,
            "slug": slug,
            "link": dados.get("link"),
            "title": titulo,
            "object": objeto,
            "org_native_id": "SESC-DN",
            "modality_raw": modalidade,
            "modality_code": None,
            "sufixo_modalidade": (numero.rsplit("-", 1)[-1].upper() if "-" in numero else None),
            "published_at_source": None,
            "opening_at_source": abertura,
            "source_updated_at_source": None,
        },
    )

    if not homologada_em:
        return ParseResult(
            batch=NormalizedBatch(
                orgs=(),
                processes=(processo,),
                items=(),
                attachments=(),
                results=(),
                awards=(),
                contracts=(),
                phases=(),
            ),
            quarantine=(),
            next=(),
            cursor_out=None,
            signals={},
            fatal=None,
        )

    resultado_id = f"{pid}:resultado"
    resultado = ResultRecord(
        source_native_id=resultado_id,
        attrs={
            "process_native_id": pid,
            "type": "homologacao",
            "decided_at_source": homologada_em,
            "value_total_raw": None,
            "value_total_cents": None,
        },
    )
    awards = [
        AwardRecord(
            source_native_id=f"{pid}:award:{lote}:{pos}",
            attrs={
                "process_native_id": pid,
                "result_native_id": resultado_id,
                "supplier_name_raw": nome,
                "amount_cents": centavos,
                "amount_raw": bruto_valor,
                "lote": lote,
            },
        )
        for pos, (lote, nome, bruto_valor, centavos) in enumerate(_extrair_vencedores(texto, homologada_em))
    ]
    return ParseResult(
        batch=NormalizedBatch(
            orgs=(),
            processes=(processo,),
            items=(),
            attachments=(),
            results=(resultado,),
            awards=tuple(awards),
            contracts=(),
            phases=(),
        ),
        quarantine=(),
        next=(),
        cursor_out=None,
        signals={},
        fatal=None,
    )


class SescAdapter(Adapter):
    source_code: str = "sesc"
    adapter_version: str = "0.1.0"

    capabilities = Capabilities(
        strategy="two_phase",
        entities=("process", "result", "award"),
        hydration={"enabled": True, "trigger": "new_or_changed"},
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
        http.headers.update(
            {
                "Accept": "application/json",
                "User-Agent": "licitamais-sesc/0.1.0",
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
        yield pedido_listagem(1)

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        attrs = getattr(process, "attrs", None) or {}
        if isinstance(attrs, Mapping):
            bruto = attrs.get("wp_id")
        else:
            bruto = None
        try:
            wp_id = int(bruto) if bruto is not None and str(bruto).strip() != "" else None
        except (TypeError, ValueError):
            wp_id = None
        if not wp_id:
            return ()
        return (pedido_detalhe(str(process.source_native_id), wp_id),)

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
        return _parse_listagem(raw)
