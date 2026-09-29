"""R37 TDD: adaptador SESC Nacional contra amostras reais (WordPress REST).

Fonte: https://www.sesc.com.br/licitacoes/ (sem login). Listagem paginada
GET /wp-json/wp/v2/pages?per_page=100&page=N (X-WP-Total=517,
X-WP-TotalPages=6); detalhe GET /wp-json/wp/v2/pages/{id} com texto livre em
content.rendered. Amostras reais em tests/fixtures/amostras/sesc/ (pages_p1..p6 +
detalhe_4628/4651/4698/4736), re-verificadas em 2026-09-24 (byte-identicas a
API viva; arquivos com BOM, ler corpos como bytes crus / utf-8-sig)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from licitamais.adapters import sesc as MOD
from licitamais.adapters.sesc import CAMPOS_SONDA, SescAdapter
from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import FetchedPage, FetchRequest, PlanContext

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures" / "amostras" / "sesc"


def _contexto() -> PlanContext:
    return PlanContext(cursors={}, seen_request_keys=frozenset(), parsed_so_far=0, source_id=1)


def _pagina_listagem(pagina: int, nome: str) -> FetchedPage:
    pedido = MOD.pedido_listagem(pagina)
    return FetchedPage(
        request=pedido,
        status=200,
        headers={"X-WP-Total": "517", "X-WP-TotalPages": "6"},
        body=(AMOSTRAS / nome).read_bytes(),
        fetched_at="2026-09-24T00:00:00Z",
        duration_ms=1,
    )


def _pagina_detalhe(pid: str, wp_id: int, nome: str) -> FetchedPage:
    pedido = MOD.pedido_detalhe(pid, wp_id)
    return FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": "application/json; charset=utf-8"},
        body=(AMOSTRAS / nome).read_bytes(),
        fetched_at="2026-09-24T00:00:00Z",
        duration_ms=1,
    )


def _por_id(resultado):
    return {p.source_native_id: p for p in resultado.batch.processes}


# ---------------------------------------------------------------- plano/sonda


def test_plano_comeca_na_folha_1_com_paginacao_wp():
    adaptador = SescAdapter()
    pedidos = list(adaptador.plan(_contexto()))

    assert len(pedidos) == 1
    pedido = pedidos[0]
    assert pedido.method == "GET"
    assert pedido.endpoint == "/wp-json/wp/v2/pages"
    assert pedido.params["per_page"] == "100"
    assert pedido.params["page"] == "1"
    assert pedido.phase == "discover"
    assert pedido.entity_hint == "process"
    assert adaptador.capabilities.hydration["enabled"] is True
    assert {"process", "result", "award"} <= set(adaptador.capabilities.entities)
    assert CAMPOS_SONDA


def test_campos_sonda_presentes_nos_517_registros_reais():
    total = 0
    for folha in range(1, 7):
        registros = json.loads((AMOSTRAS / f"pages_p{folha}.json").read_bytes().decode("utf-8-sig"))
        assert isinstance(registros, list)
        total += len(registros)
        for registro in registros:
            for campo in CAMPOS_SONDA:
                assert registro.get(campo), (folha, campo)
    assert total == 517


# ---------------------------------------------------------------- listagem real


def test_folha_1_real_100_processos_termo_e_proxima_folha():
    resultado = SescAdapter().parse(_pagina_listagem(1, "pages_p1.json"))

    assert resultado.fatal is None
    assert resultado.signals["row_count_declared"] == 517
    assert len(resultado.batch.processes) == 100
    assert len(resultado.next) == 1
    assert resultado.next[0].params["page"] == "2"

    pai = _por_id(resultado)["sesc:4736"]
    assert pai.attrs["number"] == "0171/26-IN"
    assert pai.attrs["year"] == 2026
    assert pai.attrs["wp_id"] == 4736


def test_folha_6_real_17_processos_sem_proxima():
    resultado = SescAdapter().parse(_pagina_listagem(6, "pages_p6.json"))

    assert resultado.fatal is None
    assert len(resultado.batch.processes) + len(resultado.quarantine) == 17
    assert resultado.next == ()


def test_seis_folhas_reais_512_termos_e_5_paginas_fora_de_escopo():
    processos = 0
    quarentena = 0
    for folha in range(1, 7):
        resultado = SescAdapter().parse(_pagina_listagem(folha, f"pages_p{folha}.json"))
        assert resultado.fatal is None
        processos += len(resultado.batch.processes)
        quarentena += len(resultado.quarantine)

    assert processos == 512
    assert quarentena == 5


def test_data_do_wp_e_de_migracao_ano_vem_do_numero():
    resultado = SescAdapter().parse(_pagina_listagem(1, "pages_p1.json"))
    pai = _por_id(resultado)["sesc:4651"]

    assert pai.attrs["number"] == "0041/25-PG"
    assert pai.attrs["year"] == 2025
    assert pai.attrs["published_at_source"] is None


def test_listagem_que_nao_e_lista_e_fatal():
    pedido = MOD.pedido_listagem(1)
    pagina = FetchedPage(pedido, 200, {}, b'{"success": false}', "2026-09-24T00:00:00Z", 1)
    assert SescAdapter().parse(pagina).fatal is not None


# ---------------------------------------------------------------- detalhe real


def test_detalhe_4651_dois_vencedores_sem_cnpj_sem_valor():
    resultado = SescAdapter().parse(_pagina_detalhe("sesc:4651", 4651, "detalhe_4651.json"))

    assert resultado.fatal is None
    (processo,) = resultado.batch.processes
    assert processo.source_native_id == "sesc:4651"
    assert processo.attrs["number"] == "0041/25-PG"
    assert processo.attrs["year"] == 2025
    assert processo.attrs["modality_raw"] == "pregão eletrônico"
    assert processo.attrs["opening_at_source"] == "2026-03-20"
    assert "aliment" in (processo.attrs["object"] or "").lower()

    (res,) = resultado.batch.results
    assert res.source_native_id == "sesc:4651:resultado"
    assert res.attrs["process_native_id"] == "sesc:4651"
    assert res.attrs["type"] == "homologacao"
    assert res.attrs["decided_at_source"] == "2026-07-20"

    assert [a.attrs["supplier_name_raw"] for a in resultado.batch.awards] == [
        "GB ALIMENTOS DISTRIBUIDORA LTDA",
        "GRANA 298 DISTRIB. DE ALIMENTOS LTDA",
    ]
    for award in resultado.batch.awards:
        assert award.attrs.get("supplier_cnpj") is None
        assert award.attrs["amount_cents"] is None
        assert award.attrs["result_native_id"] == "sesc:4651:resultado"
        assert award.attrs["process_native_id"] == "sesc:4651"


def test_detalhe_4628_um_vencedor_homologacao_e_abertura():
    resultado = SescAdapter().parse(_pagina_detalhe("sesc:4628", 4628, "detalhe_4628.json"))

    assert resultado.fatal is None
    (processo,) = resultado.batch.processes
    assert processo.attrs["number"] == "0037/25-PG"
    assert processo.attrs["opening_at_source"] == "2026-03-19"

    (res,) = resultado.batch.results
    assert res.attrs["type"] == "homologacao"
    assert res.attrs["decided_at_source"] == "2026-07-13"

    (award,) = resultado.batch.awards
    assert award.attrs["supplier_name_raw"] == "MANUTESP COMERCIO E SERVICOS LTDA"


def test_detalhe_4698_consulta_sem_resultado_so_processo():
    resultado = SescAdapter().parse(_pagina_detalhe("sesc:4698", 4698, "detalhe_4698.json"))

    assert resultado.fatal is None
    (processo,) = resultado.batch.processes
    assert processo.attrs["number"] == "0001/26"
    assert processo.attrs["year"] == 2026
    assert processo.attrs["modality_raw"] == "consulta pública"
    assert resultado.batch.results == ()
    assert resultado.batch.awards == ()


def test_detalhe_4736_credenciamento_etapa_sem_lotes_sem_award():
    resultado = SescAdapter().parse(_pagina_detalhe("sesc:4736", 4736, "detalhe_4736.json"))

    assert resultado.fatal is None
    (processo,) = resultado.batch.processes
    assert processo.attrs["number"] == "0171/26-IN"
    assert processo.attrs["modality_raw"] == "credenciamento"
    assert resultado.batch.awards == ()


def test_detalhe_sem_id_e_fatal():
    pedido = MOD.pedido_detalhe("sesc:1", 1)
    pagina = FetchedPage(pedido, 200, {}, b'{"slug": "x"}', "2026-09-24T00:00:00Z", 1)
    resultado = SescAdapter().parse(pagina)

    assert resultado.fatal is not None
    assert resultado.batch.processes == ()


# ---------------------------------------------------------------- valor e sintetico


def test_valor_br_para_centavos():
    assert MOD.valor_br_para_centavos("R$ 1.234,56") == 123456
    assert MOD.valor_br_para_centavos("R$ 10.655.957,09") == 1065595709
    assert MOD.valor_br_para_centavos("R$ 45000,00") == 4500000
    assert MOD.valor_br_para_centavos("R$ 12,5") == 1250
    assert MOD.valor_br_para_centavos("sem valor") is None
    assert MOD.valor_br_para_centavos(None) is None


def test_lote_sintetico_com_valor_em_reais_vira_award_com_centavos():
    corpo = json.dumps(
        {
            "id": 9999,
            "slug": "termo-de-licitacao-9999-26-pg",
            "link": "https://www.sesc.com.br/licitacoes/termo-de-licitacao-9999-26-pg",
            "title": {"rendered": "Termo de Licitação 9999/26-PG"},
            "content": {
                "rendered": (
                    "<p>Contratação de serviços de exemplo.</p>"
                    "<p><strong>RESULTADO FINAL</strong></p>"
                    "<p>O SESC comunica que a licitação foi homologada em "
                    "01/08/2026 ao seguinte licitante:</p>"
                    "<p><strong>Lote 01: EMPRESA EXEMPLO LTDA - R$ 1.234,56.</strong></p>"
                    "<p><strong>Abertura das propostas:</strong> às 10h00 do dia "
                    "10/06/2026.</p>"
                    "<p>Modalidade PREGÃO ELETRÔNICO, do tipo menor preço.</p>"
                )
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")
    pedido = MOD.pedido_detalhe("sesc:9999", 9999)
    pagina = FetchedPage(pedido, 200, {}, corpo, "2026-09-24T00:00:00Z", 1)
    resultado = SescAdapter().parse(pagina)

    assert resultado.fatal is None
    (award,) = resultado.batch.awards
    assert award.attrs["supplier_name_raw"] == "EMPRESA EXEMPLO LTDA"
    assert award.attrs["amount_cents"] == 123456
    assert award.attrs["amount_raw"] == "R$ 1.234,56"


# ---------------------------------------------------------------- hidratacao


def test_hidratacao_aponta_detalhe_da_pagina_wp():
    adaptador = SescAdapter()
    pai = _por_id(adaptador.parse(_pagina_listagem(1, "pages_p1.json")))["sesc:4651"]
    (req,) = adaptador.hydration_requests(pai)

    assert req.endpoint == "/wp-json/wp/v2/pages/4651"
    assert req.phase == "hydrate"
    assert req.entity_hint == "result"
    assert req.parent_native_id == "sesc:4651"


def test_hidratacao_sem_wp_id_nao_emite_pedido():
    adaptador = SescAdapter()
    pai = type("P", (), {"source_native_id": "sesc:x", "attrs": {}})()
    assert adaptador.hydration_requests(pai) == ()


# ---------------------------------------------------------------- loader


def test_loader_grava_processo_resultado_e_awards_sem_cnpj(tmp_path):
    detalhe = SescAdapter().parse(_pagina_detalhe("sesc:4651", 4651, "detalhe_4651.json"))

    con = sqlite3.connect(tmp_path / "sesc_4651.db")
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys = ON")
        init_schema(con)
        source_id = con.execute(
            "INSERT INTO source (code, transport, base_url, adapter_version_atual, enabled)"
            " VALUES ('sesc', 'api_json',"
            " 'https://www.sesc.com.br/licitacoes', '0.1.0', 1)"
        ).lastrowid
        con.commit()

        run_id = open_source_run(con, source_id, "manual", "0.1.0")
        carga = load_batch(con, run_id, source_id, detalhe.batch)
        con.commit()

        assert carga.new == 4
        assert carga.quarantined == 0
        assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM result").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM parse_quarantine").fetchone()[0] == 0

        linhas = con.execute("SELECT supplier_name_raw, amount_cents FROM award ORDER BY id").fetchall()
        assert [tuple(linha) for linha in linhas] == [
            ("GB ALIMENTOS DISTRIBUIDORA LTDA", None),
            ("GRANA 298 DISTRIB. DE ALIMENTOS LTDA", None),
        ]
        orgs = con.execute("SELECT supplier_org_id FROM award").fetchall()
        assert all(linha[0] is not None for linha in orgs)

        linha = con.execute("SELECT type, decided_at_source FROM result").fetchone()
        assert tuple(linha) == ("homologacao", "2026-07-20")
    finally:
        con.close()


def test_requisicao_invalida_nao_quebra_chave_canonica():
    pedido = FetchRequest(
        endpoint="/wp-json/wp/v2/pages/1",
        method="GET",
        params={},
        body=b"{}",
        headers_extra={},
        phase="hydrate",
        entity_hint="result",
        parent_native_id=None,
        cost_weight=1,
        cursor_out=None,
    )
    pagina = FetchedPage(pedido, 200, {}, b"{}", "2026-09-24T00:00:00Z", 1)
    assert SescAdapter().parse(pagina).fatal is not None
