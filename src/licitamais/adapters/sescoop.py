"""Adapter do SESCOOP (source_code='sescoop').

O SESCOOP roda a MESMA engine GoBuyer/UniCompras do Sistema Industria em
https://compras.somoscooperativismo.coop.br/compras, entao todo o motor e
reaproveitado por heranca: handshake CSRF de 3 passos (portalpublico +
csrf/token, X-XSRF-TOKEN url-decoded, Content-Type e Referer), listagem do
painelpublicacao paginada, detalhe por IdsEdital e a sala de disputa publica.

Cadeia medida com chamada real em 2026-09-24 (~1 req/s, so rotas publicas;
amostras em tests/fixtures/amostras/sescoop/):

1. listarpaginadoritensgrupospainelpublicacaoportal (pageSize=8) ->
   envelope {Data:[{Id}], RowsCount:12, Pages:2, Page, PageSize}.
2. listaritensgrupospainelpublicacaoporempresamaster?IdsEdital=... -> LISTA
   com NumeroProcesso, Objeto, Modalidade, NomeEmpresa, DescricaoStatusEdital,
   DataAbertura (12 ids em pageSize=10 nao trunca).
3. Hidratacao por processo, as DUAS rotas redundantes com pedido de proposta
   de chave identica: /api/painelpublicacao/portal/edital/{Id}/lotes (rota
   publica; sem o prefixo painelpublicacao volta 401) e
   /api/salaDisputaPublica/{Id}/listarSalaDisputaPublica?IdEdital=...
   ambas devolvem {Data:[{Codigo,...}]} (medido 4/4 processos; o RELATORIO m6
   classificou a segunda de armadilha com RowsCount 0 - hoje ela responde, e
   ainda que volte a vir vazia a rota de lotes cobre; pedidos duplicados caem
   no dedup por request.key do runner).
4. listarPorLoteEdital?codigoLoteEdital=... -> LISTA de propostas com Nome
   ("Proponente N (RAZAO - 14 digitos)"), Valor e flag Vencedor; vencedor vira
   AwardRecord com CNPJ, perdedores ficam em result.attrs.propostas. CPF (11
   digitos) nao casa e a proposta fica sem cnpj com o nome bruto, sem
   quarentena.

A rota de itens do Sistema Industria responde 200 neste tenant porem SEM o
campo Itens nos lotes, entao nenhum item e emitido e capabilities nao declara
'item'. Probe canario usa CAMPOS_SONDA (a listagem do painel so garante Id).
"""

from __future__ import annotations

import dataclasses
import json

from licitamais.adapters.sistema_industria import (
    TTL_TOKEN_SEGUNDOS,
    SistemaIndustriaAdapter,
    _parse_sala_disputa,
    _resultado_erro,
)
from licitamais.sessions.csrf import (
    CsrfSession,
    CsrfSessionManager,
    handshake_3_steps,
)
from licitamais.types import (
    FetchedPage,
    FetchRequest,
    ParseResult,
    ProcessRecord,
    SourceConfig,
)

USER_AGENT = "licitamais-sescoop/0.1.0 (contato: operador@exemplo.test)"

# sonda da API real: a listagem do painel so garante Id (medido 2026-09-24)
CAMPOS_SONDA = ("id",)


class SescoopAdapter(SistemaIndustriaAdapter):
    source_code: str = "sescoop"
    adapter_version: str = "0.1.0"

    # mesmo motor do Sistema Industria; sem 'item' porque a rota de itens do SI
    # responde 200 neste tenant porem sem campo Itens nos lotes (medido)
    capabilities = dataclasses.replace(
        SistemaIndustriaAdapter.capabilities,
        entities=("process", "result", "award"),
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

    def hydration_requests(self, process: ProcessRecord) -> tuple[FetchRequest, ...]:
        # duas rotas de lote redundantes, ambas medidas com 200 em 4/4
        # processos; o pedido de proposta emitido tem a mesma chave nas duas
        # e o runner deduplica por request.key
        pid = str(process.source_native_id)
        comum = dict(
            method="GET",
            body=None,
            headers_extra={"Accept": "application/json"},
            phase="hydrate",
            entity_hint="award",
            parent_native_id=pid,
            cost_weight=1,
            cursor_out=None,
        )
        return (
            FetchRequest(
                endpoint=f"/api/painelpublicacao/portal/edital/{pid}/lotes",
                params={},
                **comum,
            ),
            FetchRequest(
                endpoint=(f"/api/salaDisputaPublica/{pid}/listarSalaDisputaPublica"),
                params={"IdEdital": pid},
                **comum,
            ),
        )

    def parse(self, raw: FetchedPage) -> ParseResult:
        # /lotes devolve o MESMO envelope {Data:[{Codigo,...}]} da sala de
        # disputa: reaproveita o parser que emite o pedido listarPorLoteEdital
        if raw.request.endpoint.endswith("/lotes"):
            try:
                dados = json.loads(raw.body.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                return _resultado_erro(f"payload nao decodifica como JSON UTF-8: {exc}")
            return _parse_sala_disputa(raw, dados)
        # listagem, detalhe (LISTA), listarSalaDisputaPublica e
        # listarPorLoteEdital (vencedor+CNPJ+valor) seguem o motor herdado
        return super().parse(raw)
