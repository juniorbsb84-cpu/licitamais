"""Aceite M1: golden real do BRB truncado -> suspect + incidente, nunca ok/ok_zero."""

from pathlib import Path

from licitamais.adapters.brb import BRBAdapter
from licitamais.runner import run_source
from licitamais.types import FetchedPage
from tests.test_r13_runner_alertas import fonte_id, montar_cfg, montar_fonte, nova_conexao, sem_espera

REAL = (Path(__file__).resolve().parents[0] / "fixtures/amostras/brb_api_licitacao_2026-09-23.json").read_bytes()


class BRBTruncado(BRBAdapter):
    def open(self, cfg):
        return object()

    def close(self, s):
        return None

    def fetch(self, s, req):
        return FetchedPage(
            request=req,
            status=200,
            headers={"content-type": "application/json"},
            body=REAL[: len(REAL) // 2],
            fetched_at="2026-09-23T12:00:00Z",
            duration_ms=1,
        )


def test_golden_truncado_vira_suspect_com_incidente(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        r = run_source(con, montar_fonte(sid), montar_cfg(BRBTruncado()))
        assert r.status not in ("ok", "ok_zero")
        assert con.execute("SELECT COUNT(*) FROM incident WHERE source_id = ?", (sid,)).fetchone()[0] >= 1
    finally:
        con.close()
