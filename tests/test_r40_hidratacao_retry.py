"""Pedidos de detalhe falhos sobrevivem ao run sem mudança no processo."""

import json

import pytest

from licitamais.runner import run_source
from licitamais.types import ContractRecord, FetchFailure, FetchRequest, NormalizedBatch, ParseResult
from tests.test_r13_runner_alertas import (
    AGORA,
    AdaptadorSintetico,
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
    sem_espera,
)
from tests.test_r24_hidratacao_persistente import VAZIO


class AdaptadorDetalheInstavel(AdaptadorSintetico):
    def __init__(self, status):
        super().__init__()
        self.status = status
        self.tentativas = 0
        self.capabilities = self.capabilities.__class__(
            **{**self.capabilities.__dict__, "hydration": {"enabled": True, "trigger": "new_or_changed"}}
        )

    def hydration_requests(self, process):
        return (
            FetchRequest(
                endpoint="/api/contrato/7",
                method="GET",
                params={},
                body=None,
                headers_extra={},
                phase="hydrate",
                entity_hint="contract",
                parent_native_id=process.source_native_id,
                cost_weight=1,
                cursor_out=None,
            ),
        )

    def fetch(self, sessao, req):
        if req.phase == "hydrate":
            self.tentativas += 1
            if self.status is not None:
                return FetchFailure(
                    request=req, error=f"HTTP {self.status}", fetched_at=AGORA, duration_ms=1, status=self.status
                )
        return super().fetch(sessao, req)

    def parse(self, pagina):
        if pagina.request.phase == "hydrate":
            contrato = ContractRecord(
                source_native_id="edital-1:contrato:7",
                attrs={"process_native_id": "edital-1", "number": "7", "value_cents": 100},
            )
            return ParseResult(
                batch=NormalizedBatch(**{**VAZIO, "contracts": (contrato,)}),
                quarantine=(),
                next=(),
                cursor_out=None,
                signals={},
                fatal=None,
            )
        return super().parse(pagina)


@pytest.mark.parametrize("status,volta", [(503, True), (404, False)])
def test_detalhe_falho_no_primeiro_run(status, volta, monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        adaptador = AdaptadorDetalheInstavel(status)
        fonte, cfg = montar_fonte(sid), montar_cfg(adaptador)
        run_source(con, fonte, cfg)
        hash_antes = con.execute("SELECT record_hash FROM process").fetchone()[0]
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 0
        pendentes = json.loads(
            con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_pending'").fetchone()[0]
        )
        assert [req["endpoint"] for req in pendentes] == (["/api/contrato/7"] if volta else [])

        tentativas_antes = adaptador.tentativas
        adaptador.status = None
        run_source(con, fonte, cfg)
        assert con.execute("SELECT record_hash FROM process").fetchone()[0] == hash_antes
        assert adaptador.tentativas == tentativas_antes + int(volta)
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == int(volta)
    finally:
        con.close()


def test_detalhe_400_nao_derruba_run(monkeypatch):
    """sestsenat: API recusa id antigo com 400; o detalhe é descartado como o 404."""
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        run_source(con, montar_fonte(sid), montar_cfg(AdaptadorDetalheInstavel(400)))
        status, erro = con.execute("SELECT status, error FROM source_run ORDER BY id DESC LIMIT 1").fetchone()
        assert status != "failed", erro
        pendentes = json.loads(
            con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_pending'").fetchone()[0]
        )
        assert pendentes == []
    finally:
        con.close()
