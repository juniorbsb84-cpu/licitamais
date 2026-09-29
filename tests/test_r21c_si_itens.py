"""R21c: itens do Sistema Industria via edital/{id}/lote/listarpaginadorportalpublico (real, 2026-09-23)."""

import json

from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter
from licitamais.types import FetchedPage, ProcessRecord

PID = "78fb7814-eb8b-4a24-84b6-0177e143113e"
LOTE = {
    "Data": [
        {
            "Codigo": 48323,
            "DescricaoStatus": "Fechado",
            "Numero": 1,
            "Itens": [
                {
                    "Nome": "PRODUCAO DE EVENTOS",
                    "DescricaoItemEdital": "PRODUCAO DE EVENTOS",
                    "CodigoLoteEdital": 48323,
                    "CodigoItemEdital": 49893,
                    "CodigoItem": 215954,
                    "SequencialItem": 1,
                    "QuantidadeItem": 1.0,
                    "DescricaoUnidadeMedida": "SERVIÇO",
                    "Lote": 1,
                }
            ],
        }
    ],
    "Pages": 1,
    "RowsCount": 1,
    "Page": 1,
    "PageSize": 20,
    "FirstRow": 0,
}


def test_hidratacao_pede_lotes_do_edital():
    reqs = SistemaIndustriaAdapter().hydration_requests(ProcessRecord(PID))
    assert len(reqs) == 2  # r26: 2a e a sala de disputa publica (vencedores)
    assert reqs[1].endpoint == f"/api/salaDisputaPublica/{PID}/listarSalaDisputaPublica"
    assert reqs[0].endpoint == f"/api/edital/{PID}/lote/listarpaginadorportalpublico"
    assert reqs[0].params["IdEdital"] == PID
    assert reqs[0].parent_native_id == PID


def test_lote_vira_itens_ligados_ao_processo():
    req = SistemaIndustriaAdapter().hydration_requests(ProcessRecord(PID))[0]
    pagina = FetchedPage(
        request=req,
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(LOTE).encode("utf-8"),
        fetched_at="2026-09-23T12:00:00Z",
        duration_ms=1,
    )
    r = SistemaIndustriaAdapter().parse(pagina)
    assert r.fatal is None
    assert len(r.batch.items) == 1
    it = r.batch.items[0].attrs
    assert it["description"] == "PRODUCAO DE EVENTOS"
    assert it["qty"] == 1.0 and it["unit"] == "SERVIÇO" and it["lot_number"] == 1
    assert str(it["process_native_id"]) == PID
