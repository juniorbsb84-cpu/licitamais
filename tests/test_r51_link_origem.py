"""r51: link direto para o processo no portal de origem, em toda licitacao."""

from __future__ import annotations

import json
import sqlite3

from licitamais.loader import canonical_hash
from licitamais.schema import init_schema
from licitamais.types import ProcessRecord


def test_sistema_industria_monta_url_processo():
    from licitamais.origem import link_origem

    out = link_origem("sistema_industria", "3f2a84b4-ce1c-4bd6-9e6e-0c415ba493c4", {}, "000074/2026")
    assert out == {
        "url": "https://compras.sistemaindustria.com.br/compras/app/portalpublico/edital/3f2a84b4-ce1c-4bd6-9e6e-0c415ba493c4/detalhes/itens",
        "tipo": "processo",
        "rotulo": "Abrir o processo no portal da fonte",
    }


def test_sescoop_monta_url_processo():
    from licitamais.origem import link_origem

    out = link_origem("sescoop", "abc-123", {}, "000001/2026")
    assert out is not None
    assert (
        out["url"]
        == "https://compras.somoscooperativismo.coop.br/compras/app/portalpublico/edital/abc-123/detalhes/itens"
    )
    assert out["tipo"] == "processo"
    assert out["rotulo"] == "Abrir o processo no portal da fonte"


def test_brb_extrai_primeira_url_da_observacao():
    from licitamais.origem import link_origem

    attrs = {
        "observacao": "Para acessar o edital, clique abaixo:\nhttps://www.portaldecompraspublicas.com.br/processos/df/brb-banco-de-brasilia-sa-3635/pe-063-2026-2026-512773"
    }
    out = link_origem("brb", "1", attrs, "063/2026")
    assert out is not None
    assert (
        out["url"]
        == "https://www.portaldecompraspublicas.com.br/processos/df/brb-banco-de-brasilia-sa-3635/pe-063-2026-2026-512773"
    )
    assert out["tipo"] == "processo"
    assert out["rotulo"] == "Abrir o processo no portal da fonte"


def test_brb_sem_url_na_observacao_nao_tem_link():
    from licitamais.origem import link_origem

    assert link_origem("brb", "1", {"observacao": "sem link aqui"}, "001/2026") is None
    assert link_origem("brb", "1", {}, "001/2026") is None
    assert link_origem("brb", "1", {"observacao": None}, "001/2026") is None


def test_senac_com_link_processo():
    from licitamais.origem import link_origem

    attrs = {
        "org_native_id": "SENAC-SP",
        "link_processo": "https://licitacao.sp.senac.br/licitacoes/x/",
        "link_edital": "https://transparencia.senac.br/service/api/licitacoes/y/regional/SP/documento/z/download",
    }
    out = link_origem("senac", "algum-id", attrs, "001/2026")
    assert out is not None
    assert out["url"] == "https://licitacao.sp.senac.br/licitacoes/x/"
    assert out["tipo"] == "processo"
    assert out["rotulo"] == "Abrir o processo no portal da fonte"


def test_senac_sem_link_processo_usa_edital():
    from licitamais.origem import link_origem

    attrs = {
        "org_native_id": "SENAC-SP",
        "link_edital": "https://transparencia.senac.br/service/api/licitacoes/y/regional/SP/documento/z/download",
    }
    out = link_origem("senac", "algum-id", attrs, "001/2026")
    assert out is not None
    assert out["tipo"] == "edital"
    assert out["rotulo"] == "Baixar o edital no portal da fonte"
    assert out["url"].endswith("/download")


def test_senac_sem_nada_cai_na_lista():
    from licitamais.origem import link_origem

    out = link_origem("senac", "algum-id", {"org_native_id": "SENAC-SP"}, "001/2026")
    assert out == {
        "url": "https://transparencia.senac.br/#/sp/licitacoes",
        "tipo": "lista",
        "rotulo": "Abrir a lista de processos no portal da fonte",
    }


def test_sestsenat_sempre_lista():
    from licitamais.origem import link_origem

    out = link_origem("sestsenat", "sestsenat:A:1", {}, "001/2026")
    assert out == {
        "url": "https://transparencia.sestsenat.org.br/licitacoes-contratos/processos-compras-contratacao",
        "tipo": "lista",
        "rotulo": "Abrir a lista de processos no portal da fonte",
    }


def test_iges_nunca_tem_link():
    from licitamais.origem import link_origem

    assert link_origem("iges", "x", {}, "001/2026") is None
    assert link_origem("iges", "x", {"org_native_id": "IGES"}, None) is None


def test_caixa_monta_url_do_contrato_no_pncp():
    from licitamais.origem import link_origem

    attrs = {
        "cnpj_orgao": "00360305000104",
        "ano_contrato": 2026,
        "sequencial_contrato": 35,
        "uf": "DF",
        "unidade": "CEFOR",
    }
    out = link_origem("caixa", "caixa:00360305000104-1-001073/2024", attrs, "00360305000104-2-000035/2026")
    assert out == {
        "url": "https://pncp.gov.br/app/contratos/00360305000104/2026/35",
        "tipo": "processo",
        "rotulo": "Abrir o processo no portal da fonte",
    }


def test_caixa_sem_ano_ou_sequencial_nao_tem_link():
    from licitamais.origem import link_origem

    assert link_origem("caixa", "caixa:x", {}, "001/2026") is None
    assert link_origem("caixa", "caixa:x", {"ano_contrato": 2026}, "001/2026") is None


def test_fonte_desconhecida_nao_tem_link():
    from licitamais.origem import link_origem

    assert link_origem("pncp", "x", {}, "001/2026") is None


def test_senac_guarda_link_processo_e_edital():
    from licitamais.adapters.senac import _parse_licitacoes

    grupos = [
        {
            "modalidade": "Pregao",
            "dadosModalidadeLicitacao": [
                {
                    "id": "a",
                    "numeroProcesso": "001/2026",
                    "objeto": "x",
                    "dataAbertura": "2026-01-01",
                    "dataSituacao": "2026-01-02",
                    "situacao": "Aberta",
                    "linkPregaoEletronico": "https://licitacao.sp.senac.br/licitacoes/x/",
                    "documentos": [{"id": "d1", "idLicitacao": "a", "tipoDocumento": {"descricao": "Edital"}}],
                },
                {
                    "id": "b",
                    "numeroProcesso": "002/2026",
                    "objeto": "y",
                    "linkPregaoEletronico": "",
                    "documentos": [{"id": "d2", "idLicitacao": "b", "tipoDocumento": {"descricao": "Edital"}}],
                },
                {"id": "c", "numeroProcesso": "003/2026", "objeto": "z", "linkPregaoEletronico": "", "documentos": []},
            ],
        }
    ]
    r = _parse_licitacoes("SP", grupos)
    por_id = {p.source_native_id: p.attrs for p in r.batch.processes}
    assert por_id["a"]["link_processo"] == "https://licitacao.sp.senac.br/licitacoes/x/"
    assert por_id["a"]["link_edital"] == (
        "https://transparencia.senac.br/service/api/licitacoes/a/regional/SP/documento/d1/download"
    )
    assert "link_processo" not in por_id["b"]
    assert por_id["b"]["link_edital"] == (
        "https://transparencia.senac.br/service/api/licitacoes/b/regional/SP/documento/d2/download"
    )
    assert "link_processo" not in por_id["c"] and "link_edital" not in por_id["c"]


def test_senac_amostra_sp_tem_os_tres_niveis():
    import pathlib

    from licitamais.adapters.senac import SenacAdapter
    from licitamais.types import PlanContext

    amostra = pathlib.Path(__file__).resolve().parents[0] / "fixtures/amostras/senac/lic_sp.json"
    assert amostra.exists()
    adapter = SenacAdapter()
    req = next(p for p in adapter.plan(PlanContext({}, frozenset(), 0, 1)) if p.endpoint.endswith("/SP"))
    r = adapter.parse(
        __import__("licitamais.types", fromlist=["FetchedPage"]).FetchedPage(
            req, 200, {}, amostra.read_bytes(), "2026-09-25T00:00:00Z", 1
        )
    )
    assert r.fatal is None and len(r.batch.processes) > 300
    attrs = [p.attrs for p in r.batch.processes]
    assert any(a.get("link_processo", "").startswith("https://") for a in attrs)
    assert any("link_processo" not in a and a.get("link_edital", "").endswith("/download") for a in attrs)
    assert any("link_processo" not in a and "link_edital" not in a for a in attrs)


def test_hash_ignora_chaves_de_link():
    base = {"number": "001/2026", "object": "x", "org_native_id": "SENAC-SP"}
    sem = ProcessRecord("abc", dict(base))
    com = ProcessRecord("abc", {**base, "link_processo": "https://x/", "link_edital": "https://y/download"})
    assert canonical_hash(sem) == canonical_hash(com)


def test_loader_grava_link_sem_marcar_mudanca():
    from licitamais.loader import load_batch
    from licitamais.types import NormalizedBatch

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    init_schema(con)
    con.execute(
        "INSERT INTO source (id, code, transport, base_url, adapter_version_atual) VALUES (1, 'senac', 'api_json', 'https://x', 'v1')"
    )
    con.execute(
        "INSERT INTO source_run (id, source_id, \"trigger\", adapter_version, status, started_at) VALUES (1, 1, 'manual', 'v1', 'ok', '2026-09-25T00:00:00Z')"
    )
    con.execute(
        "INSERT INTO source_run (id, source_id, \"trigger\", adapter_version, status, started_at) VALUES (2, 1, 'manual', 'v1', 'ok', '2026-09-25T00:00:00Z')"
    )
    lote1 = NormalizedBatch(
        (),
        (ProcessRecord("p1", {"number": "001/2026", "org_native_id": "SENAC-SP", "object": "x"}),),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    r1 = load_batch(con, 1, 1, lote1)
    assert (r1.new, r1.changed, r1.unchanged) == (1, 0, 0)
    hash_antes = con.execute("SELECT record_hash FROM process WHERE source_native_id = 'p1'").fetchone()[0]
    mudanca_antes = con.execute("SELECT last_changed_run_id FROM process WHERE source_native_id = 'p1'").fetchone()[0]
    lote2 = NormalizedBatch(
        (),
        (
            ProcessRecord(
                "p1",
                {
                    "number": "001/2026",
                    "org_native_id": "SENAC-SP",
                    "object": "x",
                    "link_processo": "https://licitacao.sp.senac.br/licitacoes/x/",
                },
            ),
        ),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    r2 = load_batch(con, 2, 1, lote2)
    assert (r2.new, r2.changed, r2.unchanged) == (0, 0, 1)
    linha = con.execute(
        "SELECT record_hash, last_changed_run_id, attrs FROM process WHERE source_native_id = 'p1'"
    ).fetchone()
    assert linha[0] == hash_antes
    assert linha[1] == mudanca_antes
    assert json.loads(linha[2])["link_processo"] == "https://licitacao.sp.senac.br/licitacoes/x/"
    con.close()


def test_detalhe_traz_link_origem():
    import sys

    sys.path.insert(0, "tests/api")
    import os
    import tempfile

    from fastapi.testclient import TestClient  # noqa: E402

    from licitamais.api import create_app  # noqa: E402
    from licitamais.contas import auth  # noqa: E402
    from tests.api.conftest import SESSAO, semear  # noqa: E402

    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        con = sqlite3.connect(caminho)
        init_schema(con)
        semear(con)
        con.execute("UPDATE process SET source_native_id = 'abc-123', attrs = '{}' WHERE id = 1")
        sid = con.execute("SELECT source_id FROM process WHERE id = 1").fetchone()[0]
        code = con.execute("SELECT code FROM source WHERE id = ?", (sid,)).fetchone()[0]
        assert code == "brb"
        con.execute(
            "UPDATE process SET attrs = ? WHERE id = 1",
            (json.dumps({"observacao": "veja https://exemplo.test/edital/1"}),),
        )
        con.commit()
        con.close()
        c = TestClient(create_app(caminho))
        c.cookies.set("licitamais_session", SESSAO)
        c.headers["X-CSRF"] = auth.csrf(SESSAO)
        d = c.get("/api/v2/licitacoes/1").json()
        assert d["link_origem"]["url"] == "https://exemplo.test/edital/1"
        assert d["link_origem"]["tipo"] == "processo"
    finally:
        os.unlink(caminho)
