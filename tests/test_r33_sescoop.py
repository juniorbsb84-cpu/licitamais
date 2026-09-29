"""R33 TDD: adaptador SESCOOP contra amostras reais (motor GoBuyer).

Fonte: https://compras.somoscooperativismo.coop.br/compras — mesma engine GoBuyer
do Sistema Industria. Chamadas reais em 2026-09-24 (~1 req/s, so rotas publicas),
amostras em tests/fixtures/amostras/sescoop/. Cadeia medida:

listarpaginadoritensgrupospainelpublicacaoportal (pageSize=8, Pages=2, RowsCount=12)
 -> listaritensgrupospainelpublicacaoporempresamaster?IdsEdital=... (lista de 12)
 -> hidratacao: painelpublicacao/portal/edital/{id}/lotes E listarSalaDisputaPublica
 -> listarPorLoteEdital?codigoLoteEdital=... (Nome com CNPJ, Valor, flag Vencedor)

O RELATORIO m6 chamou listarSalaDisputaPublica de armadilha (RowsCount 0); medicao
propria em 2026-09-24 devolveu os lotes nas 4/4 amostras — por isso as DUAS rotas de
lote sao emitidas e os pedidos duplicados caem no dedup por request.key do runner.
A rota de itens do SI responde 200 mas SEM campo Itens neste tenant: entities nao
declara 'item'.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
import requests

from licitamais.adapters.sescoop import CAMPOS_SONDA, SescoopAdapter
from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter
from licitamais.types import (
    FetchedPage,
    FetchRequest,
    PlanContext,
    ProcessRecord,
    SourceConfig,
)

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "sescoop"
BASE = "https://compras.somoscooperativismo.coop.br/compras"
PID_136 = "29997048-acc7-4ee3-ae68-361284968ce1"  # 136/2026 Finalizado
PID_018 = "9da9d392-c001-4d4c-89bd-a1f0a2d025ab"  # 018/2026 Finalizado, 6 lotes
PID_090 = "1647acdf-63ac-4a58-92be-6c88ac02fb45"  # 090/2026 em recurso


def _captura(nome: str) -> dict:
    doc = json.loads((AMOSTRAS / nome).read_text(encoding="utf-8"))
    assert doc["status"] == 200
    return doc


def _pagina(request: FetchRequest, nome: str) -> FetchedPage:
    corpo = json.dumps(_captura(nome)["resposta"], ensure_ascii=False).encode("utf-8")
    return FetchedPage(
        request,
        200,
        {"content-type": "application/json; charset=utf-8"},
        corpo,
        "2026-09-24T12:00:00Z",
        1,
    )


def _contexto() -> PlanContext:
    return PlanContext(
        cursors={},
        seen_request_keys=frozenset(),
        parsed_so_far=0,
        source_id=33,
    )


def _req_listagem() -> FetchRequest:
    return next(SescoopAdapter().plan(_contexto()))


def _req_detalhe() -> FetchRequest:
    r = SescoopAdapter().parse(_pagina(_req_listagem(), "painel_listagem_p1.json"))
    return next(x for x in r.next if "porempresamaster" in x.endpoint)


def test_classe_herdado_source_code_e_sonda() -> None:
    import licitamais.adapters.sescoop as modulo
    from licitamais.fontes import _campos_obrigatorios

    adapter = SescoopAdapter()
    assert isinstance(adapter, SistemaIndustriaAdapter)
    assert adapter.source_code == "sescoop"
    assert adapter.adapter_version == "0.1.0"
    assert CAMPOS_SONDA == ("id",)
    assert _campos_obrigatorios(modulo) == ["id"]
    caps = adapter.capabilities
    assert caps.strategy == "two_phase"
    assert caps.auth == "csrf_handshake"
    # sem item: a rota de itens do SI nao expoe campo Itens neste tenant
    assert caps.entities == ("process", "result", "award")
    assert caps.listing_order_stable is False
    assert caps.hydration["enabled"] is True
    assert caps.hydration["trigger"] == "new_or_changed"


def test_plan_e_a_listagem_herdada_do_sistema_industria() -> None:
    req = _req_listagem()
    assert "listarpaginadoritensgrupospainelpublicacaoportal" in req.endpoint
    assert dict(req.params)["pageSize"] == "8"
    assert dict(req.params)["page"] == "1"
    assert req.phase == "discover"


def test_listagem_real_p1_emite_detalhe_com_os_8_ids_e_pagina_2() -> None:
    adapter = SescoopAdapter()
    resultado = adapter.parse(_pagina(_req_listagem(), "painel_listagem_p1.json"))
    assert resultado.fatal is None
    assert resultado.signals["row_count_declared"] == 12
    assert resultado.signals["row_count_scope"] == "total"
    detalhe = [r for r in resultado.next if "porempresamaster" in r.endpoint]
    assert len(detalhe) == 1
    ids = dict(detalhe[0].params)["IdsEdital"]
    ids = [ids] if isinstance(ids, str) else list(ids)
    amostra = _captura("painel_listagem_p1.json")["resposta"]
    assert ids == [reg["Id"] for reg in amostra["Data"]]
    paginas = [r for r in resultado.next if "listarpaginador" in r.endpoint]
    assert len(paginas) == 1
    assert dict(paginas[0].params)["page"] == "2"


def test_detalhe_real_emite_12_processos_mapeados() -> None:
    adapter = SescoopAdapter()
    resultado = adapter.parse(_pagina(_req_detalhe(), "detalhe_todos_12ids_pageSize10.json"))
    assert resultado.fatal is None
    assert resultado.quarantine == ()
    assert len(resultado.batch.processes) == 12
    assert resultado.signals["row_count_declared"] == 12
    por_id = {p.source_native_id: p for p in resultado.batch.processes}
    alvo = por_id[PID_136]
    assert alvo.attrs["number"] == "136/2026"
    assert alvo.attrs["orgao"] == "SESCOOP/UN"
    assert alvo.attrs["fase"] == "Finalizado"
    assert alvo.attrs["year"] == 2026
    assert alvo.attrs["opening_at_source"] == "2026-09-04T08:59:00"
    assert "Preg" in alvo.attrs["modalidade"]
    assert "Recurso" in por_id[PID_090].attrs["fase"]
    assert all(p.attrs["orgao"] == "SESCOOP/UN" for p in resultado.batch.processes)


def test_hidratacao_pede_as_duas_rotas_de_lote_medidas() -> None:
    reqs = SescoopAdapter().hydration_requests(ProcessRecord(PID_136))
    assert len(reqs) == 2
    lotes = next(r for r in reqs if r.endpoint.endswith("/lotes"))
    assert lotes.endpoint == (f"/api/painelpublicacao/portal/edital/{PID_136}/lotes")
    assert lotes.parent_native_id == PID_136
    assert lotes.phase == "hydrate"
    assert lotes.entity_hint == "award"
    sala = next(r for r in reqs if "listarSalaDisputaPublica" in r.endpoint)
    assert sala.endpoint == (f"/api/salaDisputaPublica/{PID_136}/listarSalaDisputaPublica")
    assert dict(sala.params)["IdEdital"] == PID_136
    assert sala.parent_native_id == PID_136
    # rotas distintas => requests distintos (dedup so entre a mesma rota)
    assert lotes.key != sala.key


def _pedidos_de_lote(pid: str, amostra: str, request: FetchRequest) -> tuple:
    adapter = SescoopAdapter()
    resultado = adapter.parse(_pagina(request, amostra))
    assert resultado.fatal is None
    assert resultado.batch.processes == ()
    assert resultado.batch.awards == ()
    return resultado


def test_lotes_reais_geram_pedido_de_propostas_por_codigo() -> None:
    adapter = SescoopAdapter()
    reqs = dict()
    for r in adapter.hydration_requests(ProcessRecord(PID_018)):
        if r.endpoint.endswith("/lotes"):
            reqs["lotes"] = r
        else:
            reqs["sala"] = r
    esperados = ["46738", "46739", "46740", "46741", "46742", "46743"]
    fontes = (
        ("lotes", "lotes_018_2026.json"),
        ("sala", "trap_sala_listarSalaDisputaPublica_018_2026.json"),
    )
    for origem, amostra in fontes:
        resultado = _pedidos_de_lote(PID_018, amostra, reqs[origem])
        pedidos = [r for r in resultado.next if r.endpoint.endswith("/listarPorLoteEdital")]
        assert [dict(r.params)["codigoLoteEdital"] for r in pedidos] == esperados, origem
        for r in pedidos:
            assert r.parent_native_id == PID_018
            assert r.phase == "hydrate"
            assert r.entity_hint == "award"
    # as duas rotas de lote produzem pedidos CHAVE-A-CHAVE identicos => dedup do runner
    a = _pedidos_de_lote(PID_018, "lotes_018_2026.json", reqs["lotes"])
    b = _pedidos_de_lote(PID_018, "trap_sala_listarSalaDisputaPublica_018_2026.json", reqs["sala"])
    assert {r.key for r in a.next} == {r.key for r in b.next}


def test_lote_sem_lotes_ou_lotes_vazos_nao_fataliza() -> None:
    adapter = SescoopAdapter()
    req = next(r for r in adapter.hydration_requests(ProcessRecord(PID_136)) if r.endpoint.endswith("/lotes"))
    vazio = FetchedPage(
        req,
        200,
        {},
        b'{"Data": [], "RowsCount": 0}',
        "2026-09-24T12:00:00Z",
        1,
    )
    resultado = adapter.parse(vazio)
    assert resultado.fatal is None
    assert resultado.next == ()


def _req_proposta(pid: str, codigo: str) -> FetchRequest:
    adapter = SescoopAdapter()
    req_lotes = next(r for r in adapter.hydration_requests(ProcessRecord(pid)) if r.endpoint.endswith("/lotes"))
    resultado = adapter.parse(_pagina(req_lotes, f"lotes_{'136_2026' if pid == PID_136 else '018_2026'}.json"))
    return next(
        r
        for r in resultado.next
        if r.endpoint.endswith("/listarPorLoteEdital") and dict(r.params)["codigoLoteEdital"] == codigo
    )


def test_propostas_reais_geram_resultado_com_vencedor_cnpj_e_valor() -> None:
    adapter = SescoopAdapter()
    req = _req_proposta(PID_136, "50129")
    resultado = adapter.parse(_pagina(req, "propostas_136_2026_lote50129.json"))
    assert resultado.fatal is None
    assert resultado.quarantine == ()
    assert resultado.signals["row_count_declared"] == 2
    assert len(resultado.batch.results) == 1
    assert len(resultado.batch.awards) == 1
    res = resultado.batch.results[0]
    assert res.source_native_id == f"{PID_136}:lote:50129"
    assert res.attrs["process_native_id"] == PID_136
    assert res.attrs["type"] == "adjudicacao"
    assert len(res.attrs["propostas"]) == 1  # so o perdedor fica nas propostas
    perdedor = res.attrs["propostas"][0]
    assert perdedor["cnpj"] == "30948812000124"
    assert perdedor["valor"] == 97000.0
    assert perdedor["vencedor"] is False
    premio = resultado.batch.awards[0]
    assert premio.attrs["process_native_id"] == PID_136
    assert premio.attrs["result_native_id"] == f"{PID_136}:lote:50129"
    assert premio.attrs["supplier_name_raw"] == "ZIVASEC TECNOLOGIA E SOLUCOES LTDA"
    assert premio.attrs["supplier_cnpj"] == "05816526000168"
    assert premio.attrs["amount_raw"] == 119999.6


def test_lote_finalizado_sem_vencedor_declarado_so_resultado() -> None:
    adapter = SescoopAdapter()
    req = _req_proposta(PID_018, "46743")
    resultado = adapter.parse(_pagina(req, "propostas_018_2026_lote46743.json"))
    assert resultado.fatal is None
    assert resultado.batch.awards == ()
    assert len(resultado.batch.results) == 1
    assert len(resultado.batch.results[0].attrs["propostas"]) == 2
    assert all(p["cnpj"] for p in resultado.batch.results[0].attrs["propostas"])


def test_proposta_com_cpf_no_nome_nao_quebra_e_fica_sem_cnpj() -> None:
    adapter = SescoopAdapter()
    req_lotes = next(r for r in adapter.hydration_requests(ProcessRecord(PID_090)) if r.endpoint.endswith("/lotes"))
    resultado_l = adapter.parse(_pagina(req_lotes, "lotes_090_2026.json"))
    req = next(r for r in resultado_l.next if r.endpoint.endswith("/listarPorLoteEdital"))
    assert dict(req.params)["codigoLoteEdital"] == "49455"
    resultado = adapter.parse(_pagina(req, "propostas_090_2026_lote49455.json"))
    assert resultado.fatal is None
    assert resultado.quarantine == ()
    assert len(resultado.batch.results) == 1
    assert resultado.batch.awards == ()  # 090 esta em recurso: ninguem vencedor ainda
    cpf = next(p for p in resultado.batch.results[0].attrs["propostas"] if "NOVA S.A." in p["nome"])
    assert cpf["cnpj"] is None  # 11 digitos = CPF, nao CNPJ: fica sem cnpj
    assert "37222740104" in cpf["nome"]  # nome bruto preservado


def test_handshake_e_headers_de_dados_usam_referer_do_sescoop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    RAW = "abc%2Bdef%3D"
    chamadas: list[dict] = []

    class Resposta:
        def __init__(self, status: int, corpo: bytes = b"{}", cookies=None):
            self.status_code = status
            self.content = corpo
            self.headers = {"Content-Type": "application/json"}
            self.cookies = requests.utils.cookiejar_from_dict(cookies or {})

    def request_dublada(_sess, _method: str, url: str, **kwargs):
        chamadas.append({"url": str(url), "headers": dict(kwargs.get("headers") or {})})
        if "portalpublico" in url:
            return Resposta(200, cookies={"JSESSIONID": "s-1"})
        if "csrf/token" in url:
            return Resposta(200, b'{"token": "' + RAW.encode() + b'"}', {"XSRF-TOKEN": RAW})
        return Resposta(200, b'{"Data": [], "RowsCount": 0}')

    monkeypatch.setattr(requests.Session, "request", request_dublada)
    adapter = SescoopAdapter()
    sessao = adapter.open(SourceConfig("sescoop", BASE))
    pagina = adapter.fetch(sessao, _req_listagem())
    assert pagina.status == 200
    dados = chamadas[-1]
    assert dados["url"].startswith(BASE)
    assert dados["headers"]["X-XSRF-TOKEN"] == "abc+def="
    assert dados["headers"]["Content-Type"] == "application/json; charset=utf-8"
    assert dados["headers"]["Referer"] == BASE + "/app/portalpublico"


def test_loader_grava_processos_e_vencedor_com_org_por_cnpj(tmp_path) -> None:
    from licitamais.loader import load_batch
    from licitamais.runner import open_source_run
    from licitamais.schema import init_schema

    adapter = SescoopAdapter()
    con = sqlite3.connect(tmp_path / "sescoop.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual)"
        " VALUES ('sescoop', 'api_json', ?, '0.1.0')",
        (BASE,),
    ).lastrowid
    run_id = open_source_run(con, source_id, "manual", "0.1.0")

    det = adapter.parse(_pagina(_req_detalhe(), "detalhe_todos_12ids_pageSize10.json"))
    load_batch(con, run_id, source_id, det.batch)
    req = _req_proposta(PID_136, "50129")
    prop = adapter.parse(_pagina(req, "propostas_136_2026_lote50129.json"))
    carga = load_batch(con, run_id, source_id, prop.batch)
    con.commit()

    assert carga.quarantined == 0
    assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 12
    premio = con.execute("SELECT supplier_name_raw, amount_cents, attrs FROM award").fetchone()
    assert premio["supplier_name_raw"] == "ZIVASEC TECNOLOGIA E SOLUCOES LTDA"
    assert premio["amount_cents"] == 11999960
    assert json.loads(premio["attrs"])["supplier_cnpj"] == "05816526000168"
    org = con.execute("SELECT o.cnpj FROM award a JOIN organization o ON o.id = a.supplier_org_id").fetchone()
    assert org is not None and org["cnpj"] == "05816526000168"
    res = con.execute("SELECT source_native_id FROM result").fetchone()
    assert res["source_native_id"] == f"{PID_136}:lote:50129"
    con.close()
