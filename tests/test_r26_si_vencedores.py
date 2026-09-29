"""Reprodução offline das respostas públicas da sala de disputa."""

import json
import sqlite3
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter
from licitamais.loader import load_batch
from licitamais.runner import open_source_run
from licitamais.schema import init_schema
from licitamais.types import FetchedPage, ProcessRecord

AMOSTRAS = Path(__file__).parents[0] / "fixtures" / "amostras" / "si_vencedores"


def _amostra(nome, request):
    captura = json.loads((AMOSTRAS / nome).read_text(encoding="utf-8"))
    assert captura["status"] == 200
    assert urlsplit(captura["url"]).path.endswith(request.endpoint)
    return FetchedPage(request, 200, {}, json.dumps(captura["resposta"]).encode(), "2026-09-24T00:00:00Z", 1)


@pytest.mark.parametrize(
    "numero,codigo,valor,fornecedor,cnpj",
    [
        ("000030-2026", "46579", 115000000, "FULSTANDIG SHOWS E EVENTOS MC LTDA", "05231625000188"),
        ("000004-2026", "49585", 1065595709, "M/CHECON DESIGN E CENOGRAFIA LTDA", "15392953000110"),
    ],
)
def test_amostra_publica_persiste_resultado_vencedor_e_todas_propostas(
    tmp_path,
    numero,
    codigo,
    valor,
    fornecedor,
    cnpj,
):
    con = sqlite3.connect(tmp_path / "si.db")
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    init_schema(con)
    source_id = con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('sistema_industria', 'api_json', 'https://si.test', 'v1')"
    ).lastrowid
    run_id = open_source_run(con, source_id, "manual", "v1")
    adapter = SistemaIndustriaAdapter()
    captura = json.loads((AMOSTRAS / f"amostra_{numero}_listarSalaDisputaPublica.json").read_text(encoding="utf-8"))
    pid = urlsplit(captura["url"]).path.split("/")[-2]
    processo = ProcessRecord(pid, {"number": numero.replace("-", "/"), "org_nome": "SENAI"})
    try:
        load_batch(con, run_id, source_id, adapter.parse(_amostra_dummy_process(processo)).batch)
        requests = adapter.hydration_requests(processo)
        sala = next(r for r in requests if r.endpoint.endswith("/listarSalaDisputaPublica"))
        assert sala.phase == "hydrate"
        assert sala.params["IdEdital"] == pid
        lotes = adapter.parse(_amostra(f"amostra_{numero}_listarSalaDisputaPublica.json", sala))
        assert lotes.fatal is None
        proposta_req = next(r for r in lotes.next if r.params.get("codigoLoteEdital") == codigo)
        assert proposta_req.endpoint.endswith("/listarPorLoteEdital")
        proposta = adapter.parse(_amostra(f"amostra_{numero}_lote{codigo}_listarPorLoteEdital.json", proposta_req))
        assert proposta.fatal is None
        assert len(proposta.batch.results) == 1
        assert len(proposta.batch.awards) == 1
        assert proposta.batch.results[0].source_native_id == f"{pid}:lote:{codigo}"
        esperado = json.loads(
            (AMOSTRAS / f"amostra_{numero}_lote{codigo}_listarPorLoteEdital.json").read_text(encoding="utf-8")
        )["resposta"]
        assert len(proposta.batch.results[0].attrs["propostas"]) == len(esperado) - 1
        assert sorted(item["valor"] for item in proposta.batch.results[0].attrs["propostas"]) == sorted(
            item["Valor"] for item in esperado if item["Vencedor"] is False
        )
        if numero == "000030-2026":
            assert min(item["valor"] for item in proposta.batch.results[0].attrs["propostas"]) == 100000.0
        load_batch(con, run_id, source_id, proposta.batch)
        award = con.execute("SELECT supplier_name_raw, amount_cents, attrs FROM award").fetchone()
        assert award["supplier_name_raw"] == fornecedor
        assert award["amount_cents"] == valor
        assert json.loads(award["attrs"])["supplier_cnpj"] == cnpj
        resultado = con.execute("SELECT source_native_id, attrs FROM result").fetchone()
        assert resultado["source_native_id"] == f"{pid}:lote:{codigo}"
        assert len(json.loads(resultado["attrs"])["propostas"]) == len(esperado) - 1
        # r28: proposta gravada como objeto JSON (nao repr Python) e vencedor ligado ao CNPJ
        assert all(isinstance(p, dict) and "cnpj" in p for p in json.loads(resultado["attrs"])["propostas"])
        org = con.execute("SELECT o.cnpj FROM award a JOIN organization o ON o.id = a.supplier_org_id").fetchone()
        assert org is not None and org["cnpj"] == cnpj
    finally:
        con.close()


def test_lote_sem_propostas_gera_resultado_sem_vencedor():
    from licitamais.types import FetchRequest

    request = FetchRequest(
        "/api/salaDisputaPublica/pid/listarPorLoteEdital",
        "GET",
        {"codigoLoteEdital": "42"},
        None,
        {},
        "hydrate",
        "award",
        "pid",
        1,
        None,
    )
    page = FetchedPage(request, 200, {}, b"[]", "2026-09-24T00:00:00Z", 1)
    parsed = SistemaIndustriaAdapter().parse(page)
    assert parsed.fatal is None
    assert parsed.batch.results[0].source_native_id == "pid:lote:42"
    assert parsed.batch.results[0].attrs["propostas"] == ()
    assert parsed.batch.awards == ()


def _amostra_dummy_process(processo):
    from licitamais.types import FetchRequest

    request = FetchRequest("/api/test", "GET", {}, None, {}, "discover", "process", None, 1, None)
    body = json.dumps(
        {
            "processos": [
                {
                    "id": processo.source_native_id,
                    "numero": processo.attrs["number"],
                    "orgao": processo.attrs["org_nome"],
                    "fase": "Finalizado",
                }
            ]
        }
    ).encode()
    return FetchedPage(request, 200, {}, body, "2026-09-24T00:00:00Z", 1)
