"""R20c: contrato do BRB vem esqueleto na listagem; valor e fornecedor so no detalhe
GET /api/contrato/{id} (medido 2026-09-23, amostra tests/fixtures/amostras/brb_api_contrato_2026-09-23.json)."""

import json
from pathlib import Path

from licitamais.adapters.brb import BRBAdapter
from licitamais.types import FetchedPage
from tests.test_r20_brb_api_real import _pagina, _registros_fixture

DETALHE = json.loads(
    (Path(__file__).resolve().parents[0] / "fixtures/amostras/brb_api_contrato_2026-09-23.json").read_text(
        encoding="utf-8"
    )
)
ESQUELETO = {k: None for k in DETALHE}
ESQUELETO.update(id=DETALHE["id"], numero=DETALHE["numero"], ano=DETALHE["ano"])


def _processo_com_esqueleto():
    reg = dict(_registros_fixture()[0], contratos=[ESQUELETO])
    return BRBAdapter().parse(_pagina(json.dumps([reg], ensure_ascii=False).encode("utf-8"))), reg


def test_esqueleto_da_listagem_nao_vira_contrato_nulo():
    r, _ = _processo_com_esqueleto()
    assert r.batch.contracts == ()


def test_hidratacao_pede_detalhe_de_cada_contrato():
    r, reg = _processo_com_esqueleto()
    pedidos = BRBAdapter().hydration_requests(r.batch.processes[0])
    assert [p.endpoint for p in pedidos] == [f"/api/contrato/{DETALHE['id']}"]
    assert pedidos[0].phase == "hydrate"
    assert pedidos[0].parent_native_id == str(reg["id"])


def test_detalhe_vira_contrato_com_valor_e_fornecedor():
    r, reg = _processo_com_esqueleto()
    pedido = BRBAdapter().hydration_requests(r.batch.processes[0])[0]
    pagina = FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(DETALHE, ensure_ascii=False).encode("utf-8"),
        fetched_at="2026-09-23T12:00:00Z",
        duration_ms=1,
    )
    res = BRBAdapter().parse(pagina)
    assert res.fatal is None
    assert len(res.batch.contracts) == 1
    c = res.batch.contracts[0].attrs
    assert c["value_cents"] == 3199649
    assert c["supplier_name_raw"] == "RIVERA MOVEIS"
    assert c["vigency_end"] == "2026-12-31"
    assert str(c["process_native_id"]) == str(reg["id"])


def test_contratos_com_mesmo_numero_no_processo_tem_ids_distintos():
    r, reg = _processo_com_esqueleto()
    pedido = BRBAdapter().hydration_requests(r.batch.processes[0])[0]
    ids = set()
    for cid in (1, 2):
        det = dict(DETALHE, id=cid)
        pagina = FetchedPage(
            request=pedido,
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(det).encode("utf-8"),
            fetched_at="2026-09-23T12:00:00Z",
            duration_ms=1,
        )
        ids.add(BRBAdapter().parse(pagina).batch.contracts[0].source_native_id)
    assert len(ids) == 2


def test_contrato_sem_numero_mas_com_id_e_valor_nao_vai_para_quarentena():
    r, reg = _processo_com_esqueleto()
    pedido = BRBAdapter().hydration_requests(r.batch.processes[0])[0]
    det = dict(DETALHE, id=4400005360, numero="", valor=23976.43)
    pagina = FetchedPage(
        request=pedido,
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(det).encode("utf-8"),
        fetched_at="2026-09-23T12:00:00Z",
        duration_ms=1,
    )
    res = BRBAdapter().parse(pagina)
    assert not res.quarantine
    assert res.batch.contracts[0].attrs["value_cents"] == 2397643
