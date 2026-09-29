"""R24: hidratacao cortada pelo teto de chamadas continua no run seguinte.
Caso real BRB 2026-09-23: 3.200 contratos, teto 60/run; sem isso so os 59 primeiros entravam."""

from licitamais.runner import run_source
from licitamais.types import (
    ContractRecord,
    FetchRequest,
    NormalizedBatch,
    ParseResult,
)
from tests.test_r13_runner_alertas import (
    AdaptadorSintetico,
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
    sem_espera,
)

VAZIO = dict(orgs=(), processes=(), items=(), attachments=(), results=(), awards=(), contracts=(), phases=())


class AdaptadorComContratos(AdaptadorSintetico):
    def __init__(self):
        super().__init__()
        self.capabilities = self.capabilities.__class__(
            **{
                **self.capabilities.__dict__,
                "hydration": {"enabled": True, "trigger": "new_or_changed"},
                "rate": {**self.capabilities.rate, "max_calls_per_run": 2},
            }
        )

    def hydration_requests(self, process):
        return tuple(
            FetchRequest(
                endpoint=f"/api/contrato/{i}",
                method="GET",
                params={},
                body=None,
                headers_extra={},
                phase="hydrate",
                entity_hint="contract",
                parent_native_id=process.source_native_id,
                cost_weight=1,
                cursor_out=None,
            )
            for i in (1, 2, 3)
        )

    def parse(self, pagina):
        if pagina.request.phase == "hydrate":
            cid = pagina.request.endpoint.rsplit("/", 1)[1]
            c = ContractRecord(
                source_native_id=f"edital-1:contrato:{cid}",
                attrs={"process_native_id": "edital-1", "number": cid, "value_cents": 100},
            )
            return ParseResult(
                batch=NormalizedBatch(**{**VAZIO, "contracts": (c,)}),
                quarantine=(),
                next=(),
                cursor_out=None,
                signals={},
                fatal=None,
            )
        return super().parse(pagina)


def test_hidratacao_cortada_continua_no_run_seguinte(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        cfg = montar_cfg(AdaptadorComContratos())
        cfg.max_calls_per_run = cfg.max_calls = 2
        fonte = montar_fonte(sid)
        for _ in range(4):
            run_source(con, fonte, cfg)
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 3
    finally:
        con.close()


def test_run_partial_grava_total_declarado_para_a_sanidade(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        ad = AdaptadorComContratos()
        orig = ad.parse

        def parse_com_total(pagina):
            res = orig(pagina)
            if pagina.request.phase == "discover":
                return res.__class__(**{**res.__dict__, "signals": {"row_count_declared": 1}})
            return res

        ad.parse = parse_com_total
        cfg = montar_cfg(ad)
        cfg.max_calls_per_run = cfg.max_calls = 2
        r = run_source(con, montar_fonte(sid), cfg)
        assert r.status == "partial"
        linha = con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'RowsCount'").fetchone()
        assert linha is not None and int(float(linha[0])) == 1
    finally:
        con.close()
