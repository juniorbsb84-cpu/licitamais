"""R32 TDD: adaptador SEST/SENAT contra a API real de transparencia.

Amostras reais em tests/fixtures/amostras/sestsenat/ (chamadas conferidas em
2026-09-24). Descoberta = POST /edital/dadosAbertos {} (dump completo, sem
paginacao de servidor). Vencedor+CNPJ = GET /edital/detalhe/... por processo;
o backfill da hidratacao e paginado entre runs pelo teto do runner
(hydration_pending)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from licitamais.adapters.sestsenat import CAMPOS_SONDA, SestSenatAdapter
from licitamais.loader import load_batch
from licitamais.runner import open_source_run, run_source
from licitamais.schema import init_schema
from licitamais.types import (
    FetchedPage,
    FetchRequest,
    ParseResult,
    PlanContext,
    Session,
)
from tests.test_r13_runner_alertas import (
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
    sem_espera,
)

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "sestsenat"


def _contexto() -> PlanContext:
    return PlanContext(cursors={}, seen_request_keys=frozenset(), parsed_so_far=0, source_id=1)


def _pedido_descoberta():
    (pedido,) = SestSenatAdapter().plan(_contexto())
    return pedido


def _pagina(pedido, nome: str) -> FetchedPage:
    return FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=(AMOSTRAS / nome).read_bytes(),
        fetched_at="2026-09-24T00:00:00Z",
        duration_ms=1,
    )


def _detalhe(nome: str, pid: str, codigo: str, filial: str, numpro: str) -> ParseResult:
    pedido = SestSenatAdapter().hydration_requests(
        type(
            "P",
            (),
            {
                "source_native_id": pid,
                "attrs": {
                    "codigo_edital": codigo,
                    "filial": filial,
                    "numero_processo": numpro,
                },
            },
        )()
    )[0]
    return SestSenatAdapter().parse(_pagina(pedido, nome))


# ---------------------------------------------------------------- plano/descoberta


def test_plano_e_uma_descoberta_post_sem_paginacao_de_servidor():
    adaptador = SestSenatAdapter()
    pedidos = list(adaptador.plan(_contexto()))

    assert len(pedidos) == 1
    pedido = pedidos[0]
    assert pedido.method == "POST"
    assert pedido.endpoint == "/edital/dadosAbertos"
    assert pedido.body == b"{}"
    assert pedido.phase == "discover"
    assert pedido.entity_hint == "process"
    assert adaptador.capabilities.hydration["enabled"] is True
    assert {"process", "result", "award"} <= set(adaptador.capabilities.entities)
    assert CAMPOS_SONDA


def test_campos_sonda_presentes_em_todas_as_linhas_do_dump_real():
    linhas = json.loads((AMOSTRAS / "dadosAbertos_80.json").read_bytes().decode("utf-8"))
    assert len(linhas) == 80
    for linha in linhas:
        for campo in CAMPOS_SONDA:
            assert linha.get(campo)


def test_dump_real_agrega_processos_unicos_com_vencedor_e_licitantes():
    resultado = SestSenatAdapter().parse(_pagina(_pedido_descoberta(), "dadosAbertos_80.json"))

    assert resultado.fatal is None
    assert resultado.quarantine == ()
    assert resultado.signals["row_count_declared"] == 80
    assert len(resultado.batch.processes) == 15
    assert sum(p.attrs["licitantes"] for p in resultado.batch.processes) == 80
    assert {p.attrs["filial"] for p in resultado.batch.processes} == {"010001"}
    assert sum(1 for p in resultado.batch.processes if p.attrs["has_vencedor"]) == 13

    pai = next(p for p in resultado.batch.processes if p.source_native_id == "sestsenat:010001:000000000552017")
    assert pai.attrs["number"] == "000000000552017"
    assert pai.attrs["codigo_edital"] == "0000000055/2017"
    assert pai.attrs["org_native_id"] == "SEST"
    assert pai.attrs["phase_label"] == "Edital Encerrado"
    assert pai.attrs["year"] == 2017
    assert pai.attrs["opening_at_source"] == "2017-04-06"
    assert pai.attrs["has_vencedor"] is True
    assert pai.attrs["valor_vencido_cents"] == 4500000


def test_dump_filtrado_por_edital_ancora_um_processo():
    resultado = SestSenatAdapter().parse(_pagina(_pedido_descoberta(), "dadosAbertos_1_edital.json"))

    assert resultado.fatal is None
    (processo,) = resultado.batch.processes
    assert processo.source_native_id == "sestsenat:010001:000000000552017"
    assert processo.attrs["object"].startswith("CONTRATA")
    assert processo.attrs["has_vencedor"] is True


def test_dump_de_uma_filial_e_pagina_da_fonte():
    resultado = SestSenatAdapter().parse(_pagina(_pedido_descoberta(), "dadosAbertos_filial_020005.json"))

    assert resultado.fatal is None
    assert resultado.signals["row_count_declared"] == 860
    assert len(resultado.batch.processes) == 289
    assert {p.attrs["filial"] for p in resultado.batch.processes} == {"020005"}


def test_dump_que_nao_e_lista_e_fatal():
    pedido = _pedido_descoberta()
    pagina = FetchedPage(pedido, 200, {}, b'{"success": false}', "2026-09-24T00:00:00Z", 1)
    assert SestSenatAdapter().parse(pagina).fatal is not None


# ---------------------------------------------------------------- hidratacao


def test_hidratacao_aponta_detalhe_com_barras_trocadas_e_processo_pai():
    adaptador = SestSenatAdapter()
    pai = next(p for p in adaptador.parse(_pagina(_pedido_descoberta(), "dadosAbertos_1_edital.json")).batch.processes)
    (req,) = adaptador.hydration_requests(pai)

    assert req.method == "GET"
    assert req.endpoint == "/edital/detalhe/0000000055_2017/010001/000000000552017"
    assert req.phase == "hydrate"
    assert req.entity_hint == "result"
    assert req.parent_native_id == pai.source_native_id


def test_hidratacao_troca_barra_e_preserva_espaco_do_codigo_real():
    adaptador = SestSenatAdapter()
    pai = type(
        "P",
        (),
        {
            "source_native_id": "sestsenat:010001:0000000032017-1",
            "attrs": {
                "codigo_edital": "00003/2017 -1",
                "filial": "010001",
                "numero_processo": "0000000032017-1",
            },
        },
    )()
    (req,) = adaptador.hydration_requests(pai)
    assert req.endpoint == "/edital/detalhe/00003_2017 -1/010001/0000000032017-1"


def test_hidratacao_sem_dados_do_processo_nao_emite_pedido():
    adaptador = SestSenatAdapter()
    pai = type("P", (), {"source_native_id": "x", "attrs": {}})()
    assert adaptador.hydration_requests(pai) == ()


# ---------------------------------------------------------------- detalhe real


def test_detalhe_real_vencedor_com_cnpj_valor_e_processo_pai():
    resultado = _detalhe(
        "detalhe_0055_2017.json",
        "sestsenat:010001:000000000552017",
        "0000000055/2017",
        "010001",
        "000000000552017",
    )

    assert resultado.fatal is None
    (res,) = resultado.batch.results
    assert res.source_native_id == "sestsenat:010001:000000000552017:resultado"
    assert res.attrs["process_native_id"] == "sestsenat:010001:000000000552017"
    assert res.attrs["type"] == "homologacao"
    assert res.attrs["value_total_cents"] == 4500000
    assert res.attrs["decided_at_source"] == "2017-04-06T00:00:00"

    (award,) = resultado.batch.awards
    assert award.attrs["supplier_cnpj"] == "20705715000157"
    assert award.attrs["supplier_name_raw"] == "GOMES SPAGNOLO & VIELMO MIRANDA LTDA ME"
    assert award.attrs["amount_cents"] == 4500000
    assert award.attrs["qty_awarded"] == 1
    assert award.attrs["process_native_id"] == "sestsenat:010001:000000000552017"
    assert award.attrs["result_native_id"] == "sestsenat:010001:000000000552017:resultado"


def test_detalhe_real_so_o_vencedor_vira_award():
    resultado = _detalhe(
        "detalhe_0001_2017_real.json",
        "sestsenat:010001:000000000362017",
        "0001/2017",
        "010001",
        "000000000362017",
    )

    assert resultado.fatal is None
    (res,) = resultado.batch.results
    assert res.attrs["value_total_cents"] == 150075896
    (award,) = resultado.batch.awards
    assert award.attrs["supplier_cnpj"] == "06084401000153"
    assert award.attrs["amount_cents"] == 150075896


def test_detalhe_real_de_processo_com_sete_participantes_um_vencedor():
    resultado = _detalhe(
        "detalhe_00003_2017-1_real.json",
        "sestsenat:010001:0000000032017-1",
        "00003/2017 -1",
        "010001",
        "0000000032017-1",
    )

    assert resultado.fatal is None
    assert len(resultado.batch.awards) == 1
    assert resultado.batch.awards[0].attrs["supplier_cnpj"] == "02924831000185"
    assert resultado.batch.awards[0].attrs["amount_cents"] == 2010000


def test_detalhe_sem_processo_pai_e_fatal():
    pedido = FetchRequest(
        endpoint="/edital/detalhe/x/y/z",
        method="GET",
        params={},
        body=None,
        headers_extra={},
        phase="hydrate",
        entity_hint="result",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )
    pagina = FetchedPage(pedido, 200, {}, b"{}", "2026-09-24T00:00:00Z", 1)
    resultado = SestSenatAdapter().parse(pagina)
    assert resultado.fatal is not None
    assert resultado.batch.results == ()
    assert resultado.batch.awards == ()


def test_detalhe_sem_estrutura_conhecida_e_fatal():
    pedido = FetchRequest(
        endpoint="/edital/detalhe/x/y/z",
        method="GET",
        params={},
        body=None,
        headers_extra={},
        phase="hydrate",
        entity_hint="result",
        parent_native_id="sestsenat:010001:1",
        cost_weight=1,
        cursor_out=None,
    )
    pagina = FetchedPage(pedido, 200, {}, b'{"success": false}', "2026-09-24T00:00:00Z", 1)
    assert SestSenatAdapter().parse(pagina).fatal is not None


def test_vencedor_sem_cnpj_vai_para_quarentena_sem_award():
    corpo = json.dumps(
        {
            "cO1_NUMPRO": "000000000000001",
            "vlR_HOMOLOGADO": 100.0,
            "fornecedores": [
                {"a2_NOME": "EMPRE SA SEM DOC", "a2_CGC": None, "qtD_VENCIDO": "1", "vlR_VENCIDO": "100"},
            ],
        }
    ).encode("utf-8")
    pedido = FetchRequest(
        endpoint="/edital/detalhe/x/010001/000000000000001",
        method="GET",
        params={},
        body=None,
        headers_extra={},
        phase="hydrate",
        entity_hint="result",
        parent_native_id="sestsenat:010001:000000000000001",
        cost_weight=1,
        cursor_out=None,
    )
    resultado = SestSenatAdapter().parse(FetchedPage(pedido, 200, {}, corpo, "2026-09-24T00:00:00Z", 1))
    assert resultado.fatal is None
    assert resultado.batch.awards == ()
    assert len(resultado.quarantine) == 1


# ---------------------------------------------------------------- loader


def test_loader_grava_processo_result_award_e_org_por_cnpj(tmp_path):
    adaptador = SestSenatAdapter()
    descoberta = adaptador.parse(_pagina(_pedido_descoberta(), "dadosAbertos_1_edital.json"))
    detalhe = _detalhe(
        "detalhe_0055_2017.json",
        "sestsenat:010001:000000000552017",
        "0000000055/2017",
        "010001",
        "000000000552017",
    )

    con = sqlite3.connect(tmp_path / "sestsenat_real.db")
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        source_id = con.execute(
            "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
            " VALUES ('sestsenat', 'api_json',"
            " 'https://transparencia.sestsenat.org.br/api', '0.1.0', 1)"
        ).lastrowid
        con.commit()

        run_discover = open_source_run(con, source_id, "manual", "0.1.0")
        carga = load_batch(con, run_discover, source_id, descoberta.batch)
        assert carga.new == 1

        run_hidratacao = open_source_run(con, source_id, "manual", "0.1.0")
        load_batch(con, run_hidratacao, source_id, detalhe.batch)
        con.commit()

        assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM result").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM parse_quarantine").fetchone()[0] == 0

        linha = con.execute("SELECT amount_cents, supplier_name_raw FROM award").fetchone()
        assert tuple(linha) == (4500000, "GOMES SPAGNOLO & VIELMO MIRANDA LTDA ME")

        org = con.execute("SELECT o.cnpj FROM organization o JOIN award a ON a.supplier_org_id = o.id").fetchone()
        assert org is not None and org[0] == "20705715000157"
    finally:
        con.close()


# ---------------------------------------------------------------- backfill por teto


class _SessaoFake:
    def close(self):
        return None


class _AdaptadorAmostra(SestSenatAdapter):
    """Serve as amostras reais sem rede; detalhe devolve sempre o edital 0055."""

    def open(self, cfg):
        return Session(self.source_code, {"base_url": "https://fonte.test", "dummy": _SessaoFake()})

    def fetch(self, sessao, req):
        if req.phase == "hydrate":
            nome = "detalhe_0055_2017.json"
        else:
            nome = "dadosAbertos_1_edital.json"
        return FetchedPage(
            request=req,
            status=200,
            headers={"content-type": "application/json"},
            body=(AMOSTRAS / nome).read_bytes(),
            fetched_at="2026-09-24T00:00:00Z",
            duration_ms=1,
        )


def test_backfill_de_hidratacao_continua_no_run_seguinte_respeitando_teto(monkeypatch):
    sem_espera(monkeypatch)
    # a amostra real e de 2017; o recorte de ano (r45) tem teste proprio
    monkeypatch.setattr("licitamais.runner.ANO_MINIMO_HIDRATACAO", 2000)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        ad = _AdaptadorAmostra()
        fonte = montar_fonte(sid)

        cfg1 = montar_cfg(ad)
        cfg1.max_calls_per_run = cfg1.max_calls = 1
        run_source(con, fonte, cfg1)

        assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 0
        pendente = con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_pending'").fetchone()
        assert pendente is not None and json.loads(pendente[0])

        cfg2 = montar_cfg(ad)
        cfg2.max_calls_per_run = cfg2.max_calls = 2
        run_source(con, fonte, cfg2)

        assert con.execute("SELECT COUNT(*) FROM result").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    finally:
        con.close()
