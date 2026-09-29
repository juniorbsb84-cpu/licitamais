"""Fase vermelha do TDD para o adapter do Sistema Industria."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from collections.abc import Iterator

import pytest
import requests

from licitamais.types import FetchedPage, FetchRequest, PlanContext, SourceConfig

MODULO = "licitamais.adapters.sistema_industria"
BASE_URL = "https://sistema-industria.exemplo.test"


def carregar_producao():
    """Falha pela ausencia do adapter, sem abortar a coleta."""
    spec = importlib.util.find_spec(MODULO)
    assert spec is not None, f"O modulo de producao {MODULO} ainda nao existe."
    return importlib.import_module(MODULO)


@pytest.fixture(scope="module")
def sistema_industria():
    return carregar_producao()


def contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=7,
    )


def request_listagem(pagina_numero: str = "1") -> FetchRequest:
    return FetchRequest(
        endpoint="/api/processos",
        method="GET",
        params={"pagina": pagina_numero},
        body=None,
        headers_extra={"Accept": "application/json"},
        phase="discover",
        entity_hint="process",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )


def pagina(request: FetchRequest, payload: object) -> FetchedPage:
    return FetchedPage(
        request=request,
        status=200,
        headers={"Content-Type": "application/json; charset=utf-8"},
        body=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        fetched_at="2026-09-22T12:00:00Z",
        duration_ms=1,
    )


def processo(
    native_id: str,
    *,
    fase: str = "aberto",
    numero: str | None = None,
) -> dict[str, object]:
    return {
        "id": native_id,
        "numero": numero or f"PE-{native_id}",
        "orgao": "Federacao das Industrias",
        "fase": fase,
        "data_publicacao": "2026-09-01T10:00:00Z",
        "data_abertura": "2026-10-01T10:00:00Z",
    }


def payload_listagem(registros: list[dict[str, object]], *, proxima: int | None) -> dict[str, object]:
    return {"processos": registros, "proxima_pagina": proxima}


def test_capabilities_declaram_duas_fases_e_listagem_instavel(
    sistema_industria,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()

    assert adapter.source_code == "sistema_industria"
    assert adapter.adapter_version == "0.1.0"
    assert adapter.capabilities.strategy == "two_phase"
    assert adapter.capabilities.auth == "csrf_handshake"
    assert adapter.capabilities.listing_order_stable is False
    assert adapter.capabilities.hydration["trigger"] == "new_or_changed"


def test_plan_varre_todas_as_paginas_sem_early_stop(
    sistema_industria,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()
    paginas = {
        "1": payload_listagem([processo("P-1")], proxima=2),
        "2": payload_listagem([processo("P-1")], proxima=3),
        "3": payload_listagem([processo("P-2")], proxima=None),
    }

    pendentes = list(adapter.plan(contexto()))
    visitadas: list[str] = []
    encontrados: list[str] = []

    while pendentes:
        request = pendentes.pop(0)
        assert request.phase == "discover"
        pagina_numero = str(request.params["pagina"])
        visitadas.append(pagina_numero)

        resultado = adapter.parse(pagina(request, paginas[pagina_numero]))
        encontrados.extend(registro.source_native_id for registro in resultado.batch.processes)
        pendentes.extend(resultado.next)

    assert visitadas == ["1", "2", "3"]
    assert encontrados == ["P-1", "P-1", "P-2"]


def test_parse_devolve_pagina_seguinte_e_termina_com_next_vazio(
    sistema_industria,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()
    inicial = next(adapter.plan(contexto()))

    com_proxima = adapter.parse(pagina(inicial, payload_listagem([processo("P-1")], proxima=2)))

    assert len(com_proxima.next) == 1
    assert com_proxima.next[0].phase == "discover"
    assert com_proxima.next[0].params["pagina"] == "2"

    fim = adapter.parse(pagina(com_proxima.next[0], payload_listagem([], proxima=None)))

    assert fim.batch.processes == ()
    assert fim.next == ()
    assert fim.quarantine == ()
    assert fim.fatal is None


def test_item_sem_id_nativo_recebe_identidade_fabricada_estavel(
    sistema_industria,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()
    request = sistema_industria.hydrate_request("P-77")
    raw = pagina(
        request,
        {
            "processo": processo("P-77"),
            "itens": [
                {"lote": "L-3", "descricao": "cimento", "sequencia": 9},
            ],
        },
    )

    ids = [tuple(item.source_native_id for item in adapter.parse(raw).batch.items) for _ in range(2)]

    assert sistema_industria.fabricate_item_native_id("P-77", "L-3", 9) == "P-77:L-3:9"
    assert ids == [("P-77:L-3:9",), ("P-77:L-3:9",)]


def test_fetch_faz_handshake_antes_da_chamada_de_dados(
    sistema_industria,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chamadas: list[str] = []

    class RespostaDublada:
        def __init__(
            self,
            status: int,
            corpo: bytes,
            cookies: dict[str, str] | None = None,
        ) -> None:
            self.status_code = status
            self.content = corpo
            self.headers = {"Content-Type": "application/json"}
            self.cookies = requests.utils.cookiejar_from_dict(cookies or {})

    def request_dublada(
        _sessao: requests.Session,
        _method: str,
        url: str,
        **_kwargs: object,
    ) -> RespostaDublada:
        if "portalpublico" in url:
            chamadas.append("portalpublico")
            return RespostaDublada(200, b"{}", {"JSESSIONID": "run-1"})

        if "csrf/token" in url:
            chamadas.append("csrf/token")
            return RespostaDublada(
                200,
                b'{"token":"token%2Fcsrf"}',
                {"XSRF-TOKEN": "token%2Fcsrf"},
            )

        chamadas.append("dados")
        return RespostaDublada(
            200,
            b'{"processos":[],"proxima_pagina":null}',
        )

    monkeypatch.setattr(requests.Session, "request", request_dublada)

    adapter = sistema_industria.SistemaIndustriaAdapter()
    sessao = adapter.open(SourceConfig("sistema_industria", BASE_URL))
    resposta = adapter.fetch(sessao, next(adapter.plan(contexto())))

    assert chamadas == ["portalpublico", "csrf/token", "dados"]
    assert resposta.status == 200


def test_registro_sem_campo_obrigatorio_vai_para_quarentena_sem_levantar(
    sistema_industria,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()
    request = next(adapter.plan(contexto()))
    invalido = processo("P-INCOMPLETO")
    del invalido["numero"]

    resultado = adapter.parse(pagina(request, payload_listagem([invalido], proxima=None)))

    assert resultado.fatal is None
    assert resultado.batch.processes == ()
    assert len(resultado.quarantine) == 1
    assert "numero" in resultado.quarantine[0].reason
    assert resultado.quarantine[0].pointer


def _record_hash(registro: object) -> str:
    valores = {
        "source_native_id": registro.source_native_id,
        "numero": registro.numero,
        "orgao": registro.orgao,
        "fase": registro.fase,
        "data_publicacao": registro.data_publicacao,
        "data_abertura": registro.data_abertura,
    }
    serializado = json.dumps(
        valores,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(serializado).hexdigest()


def _hidratar_novos_ou_alterados(
    modulo: object,
    processos: Iterator[object],
    hashes_anteriores: dict[str, str],
) -> list[FetchRequest]:
    requests: list[FetchRequest] = []

    for registro in processos:
        native_id = registro.source_native_id
        if hashes_anteriores.get(native_id) != _record_hash(registro):
            requests.append(modulo.hydrate_request(native_id))

    return requests


def test_segundo_run_inalterado_nao_solicita_hidratacao(
    sistema_industria,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = sistema_industria.SistemaIndustriaAdapter()
    request = next(adapter.plan(contexto()))
    raw = pagina(request, payload_listagem([processo("P-1")], proxima=None))
    processos_primeiro = adapter.parse(raw).batch.processes

    chamadas_hidratacao: list[str] = []
    original = sistema_industria.hydrate_request

    def hydrate_espiao(native_id: str) -> FetchRequest:
        chamadas_hidratacao.append(native_id)
        return original(native_id)

    monkeypatch.setattr(sistema_industria, "hydrate_request", hydrate_espiao)

    hashes_anteriores = {registro.source_native_id: _record_hash(registro) for registro in processos_primeiro}

    primeiro = _hidratar_novos_ou_alterados(
        sistema_industria,
        iter(processos_primeiro),
        {},
    )
    processos_segundo = adapter.parse(raw).batch.processes
    segundo = _hidratar_novos_ou_alterados(
        sistema_industria,
        iter(processos_segundo),
        hashes_anteriores,
    )

    assert [request.phase for request in primeiro] == ["hydrate"]
    assert segundo == []
    assert chamadas_hidratacao == ["P-1"]
