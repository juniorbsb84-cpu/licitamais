import json
from pathlib import Path

from licitamais.adapters.senac import CAMPOS_SONDA, SenacAdapter
from licitamais.types import FetchedPage, PlanContext

AMOSTRAS = Path(__file__).resolve().parents[0] / "fixtures/amostras/senac"


def _page(req, name):
    return FetchedPage(req, 200, {}, (AMOSTRAS / name).read_bytes(), "2026-09-24T00:00:00Z", 1)


def test_plano_tem_56_pedidos_sem_hidratacao():
    adapter = SenacAdapter()
    pedidos = list(adapter.plan(PlanContext({}, frozenset(), 0, 1)))
    assert len(pedidos) == 56
    assert len({p.key for p in pedidos}) == 56
    assert {p.phase for p in pedidos} == {"discover"}
    assert sum("/licitacoes/regional/DF" in p.endpoint for p in pedidos) == 1
    assert sum(p.params.get("regional") == "DF" for p in pedidos) == 1
    assert adapter.capabilities.hydration["enabled"] is False
    assert CAMPOS_SONDA


def test_contratos_reais_com_pai_cnpj_e_centavos():
    adapter = SenacAdapter()
    req = next(p for p in adapter.plan(PlanContext({}, frozenset(), 0, 1)) if p.params.get("regional") == "DF")
    result = adapter.parse(_page(req, "ctr_df.json"))
    dados = json.loads((AMOSTRAS / "ctr_df.json").read_text(encoding="utf-8"))
    assert result.fatal is None
    assert len(dados) == 568
    assert len(result.batch.contracts) == 568
    assert len(result.quarantine) == 0
    assert {c.attrs["process_native_id"] for c in result.batch.contracts} == {
        p.source_native_id for p in result.batch.processes
    }
    primeiro = result.batch.contracts[0]
    assert primeiro.source_native_id == "DF:2023.000001705-49:86/2022"
    assert primeiro.attrs["supplier_cnpj"] == "02812468000106"
    assert primeiro.attrs["value_cents"] == 3503825723
    assert primeiro.attrs["signed_at_source"] == "2025-11-14T00:00:00Z"


def test_licitacoes_reais():
    adapter = SenacAdapter()
    req = next(p for p in adapter.plan(PlanContext({}, frozenset(), 0, 1)) if p.endpoint.endswith("/DF"))
    result = adapter.parse(_page(req, "lic_df.json"))
    dados = json.loads((AMOSTRAS / "lic_df.json").read_text(encoding="utf-8"))
    esperado = sum(len(x["dadosModalidadeLicitacao"]) for x in dados["data"])
    assert result.fatal is None
    assert len(result.batch.processes) == esperado
    assert all(p.attrs["org_native_id"] == "SENAC-DF" for p in result.batch.processes)
    assert {p.source_native_id for p in result.batch.processes} == {
        str(x["id"]) for m in dados["data"] for x in m["dadosModalidadeLicitacao"]
    }


def test_campos_sonda_existem_no_primeiro_payload_real():
    dados = json.loads((AMOSTRAS / "lic_df.json").read_text(encoding="utf-8"))
    primeiro = dados["data"][0]
    assert set(CAMPOS_SONDA) <= set(dados), (
        "sonda canario verifica o 1o nivel do objeto raiz (envelope); campos de data[0] nunca passam (run 78)"
    )
    assert set(("modalidade", "dadosModalidadeLicitacao")) <= set(primeiro)


def test_canario_passa_com_campos_do_envelope():
    """Task 3 Step 2 (correcao): chaves do 1o nivel do envelope real passam
    na sonda canario, provando o caminho de verificacao usado pelo runner."""
    from licitamais.probes import check_contract_canary

    resultado = check_contract_canary((AMOSTRAS / "lic_df.json").read_bytes(), "application/json", ["success", "data"])
    assert resultado.passed is True


def _parse_contratos(linhas):
    adapter = SenacAdapter()
    req = next(p for p in adapter.plan(PlanContext({}, frozenset(), 0, 1)) if p.params.get("regional") == "AL")
    return adapter.parse(FetchedPage(req, 200, {}, json.dumps(linhas).encode("utf-8"), "2026-09-24T00:00:00Z", 1))


def test_chave_repetida_desempata_so_as_repetidas():
    """r29b: API repete numeroOrigem/numero na mesma regional; UNIQUE revertia o lote inteiro."""
    base = {"numeroOrigem": "X-1", "numero": "1/2024", "favorecido": "A", "valorTotal": 10.0}
    r = _parse_contratos(
        [
            {**base, "cpfCnpj": "11111111000111"},
            {**base, "cpfCnpj": "22222222000122"},
            {**base, "numero": "2/2024", "cpfCnpj": "33333333000133"},
        ]
    )
    ids = [c.source_native_id for c in r.batch.contracts]
    assert len(set(ids)) == 3
    assert "AL:X-1:2/2024" in ids  # chave unica: formato antigo exato (9.103 ja gravados)
    assert "AL:X-1:1/2024:11111111000111" in ids and "AL:X-1:1/2024:22222222000122" in ids


def test_contrato_sem_numero_origem_vira_processo_sem_origem():
    """r29b: 1.506 contratos tipo 3 (parceria) vem sem numeroOrigem; iam para quarentena."""
    r = _parse_contratos(
        [{"numero": "002/2024", "favorecido": "BIO", "cpfCnpj": "44444444000144", "valorTotal": 681541.44, "tipo": 3}]
    )
    assert not r.quarantine
    c = r.batch.contracts[0]
    assert c.source_native_id == "AL:sem-origem:002/2024:44444444000144"
    assert c.attrs["process_native_id"] == "AL:sem-origem:002/2024:44444444000144"
    assert {p.source_native_id for p in r.batch.processes} == {c.attrs["process_native_id"]}
