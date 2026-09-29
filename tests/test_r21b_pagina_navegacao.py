"""R21b: pagina so de navegacao (lista de Ids -> requests de detalhe, lote vazio)
nao pode derrubar o run. Caso real do Sistema Industria (2026-09-23)."""

from licitamais.runner import run_source
from licitamais.types import FetchRequest, NormalizedBatch, ParseResult
from tests.test_r13_runner_alertas import (
    AdaptadorSintetico,
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
)

VAZIO = NormalizedBatch(orgs=(), processes=(), items=(), attachments=(), results=(), awards=(), contracts=(), phases=())


class AdaptadorListaDetalhe(AdaptadorSintetico):
    def parse(self, pagina):
        if pagina.request.phase == "discover":
            detalhe = FetchRequest(
                endpoint="/api/detalhe",
                method="GET",
                params={"ids": "edital-1"},
                body=None,
                headers_extra={},
                phase="detail",
                entity_hint="process",
                parent_native_id=None,
                cost_weight=1,
                cursor_out=None,
            )
            return ParseResult(batch=VAZIO, quarantine=(), next=(detalhe,), cursor_out=None, signals={}, fatal=None)
        return super().parse(pagina)


def test_pagina_de_navegacao_nao_derruba_o_run():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        r = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorListaDetalhe()))
        assert "lote totalmente vazio" not in (r.error or "")
        assert con.execute("SELECT COUNT(*) FROM process").fetchone()[0] == 1
    finally:
        con.close()


def test_processo_vindo_da_pagina_de_detalhe_tambem_hidrata():
    from licitamais.types import ItemRecord

    class ComItens(AdaptadorListaDetalhe):
        def __init__(self):
            super().__init__()
            self.capabilities = self.capabilities.__class__(
                **{**self.capabilities.__dict__, "hydration": {"enabled": True, "trigger": "new_or_changed"}}
            )

        def hydration_requests(self, process):
            return (
                FetchRequest(
                    endpoint="/api/itens",
                    method="GET",
                    params={},
                    body=None,
                    headers_extra={},
                    phase="items",
                    entity_hint="item",
                    parent_native_id=process.source_native_id,
                    cost_weight=1,
                    cursor_out=None,
                ),
            )

        def parse(self, pagina):
            if pagina.request.phase == "items":
                it = ItemRecord("edital-1:item:1", {"process_native_id": "edital-1", "description": "papel"})
                return ParseResult(
                    batch=VAZIO.__class__(**{**VAZIO.__dict__, "items": (it,)}),
                    quarantine=(),
                    next=(),
                    cursor_out=None,
                    signals={},
                    fatal=None,
                )
            return super().parse(pagina)

    con = nova_conexao()
    try:
        sid = fonte_id(con)
        run_source(con, montar_fonte(sid), montar_cfg(ComItens()))
        assert con.execute("SELECT COUNT(*) FROM item").fetchone()[0] == 1
    finally:
        con.close()


def test_hidratacao_sem_itens_nao_derruba_o_run():
    class SemItens(AdaptadorListaDetalhe):
        def __init__(self):
            super().__init__()
            self.capabilities = self.capabilities.__class__(
                **{**self.capabilities.__dict__, "hydration": {"enabled": True, "trigger": "new_or_changed"}}
            )

        def hydration_requests(self, process):
            return (
                FetchRequest(
                    endpoint="/api/itens",
                    method="GET",
                    params={},
                    body=None,
                    headers_extra={},
                    phase="items",
                    entity_hint="item",
                    parent_native_id=process.source_native_id,
                    cost_weight=1,
                    cursor_out=None,
                ),
            )

        def parse(self, pagina):
            if pagina.request.phase == "items":
                return ParseResult(batch=VAZIO, quarantine=(), next=(), cursor_out=None, signals={}, fatal=None)
            return super().parse(pagina)

    con = nova_conexao()
    try:
        sid = fonte_id(con)
        r = run_source(con, montar_fonte(sid), montar_cfg(SemItens()))
        assert "lote totalmente vazio" not in (r.error or "")
    finally:
        con.close()
