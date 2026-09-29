"""R45: hidratacao (detalhe/contratos) so para processos de 2026 em diante.
Decisao do usuario 2026-09-25: a fila de historico antigo (103 mil SEST/SENAT, 2,8 mil BRB) nao compensa."""

import json

from licitamais.runner import run_source
from tests.test_r13_runner_alertas import fonte_id, montar_cfg, montar_fonte, nova_conexao, sem_espera
from tests.test_r24_hidratacao_persistente import AdaptadorComContratos


class AdaptadorAntigo(AdaptadorComContratos):
    def parse(self, pagina):
        res = super().parse(pagina)
        if pagina.request.phase != "discover":
            return res
        p = res.batch.processes[0]
        antigo = p.__class__(**{**p.__dict__, "attrs": {**p.attrs, "year": 2025, "number": "1/2025"}})
        return res.__class__(
            **{**res.__dict__, "batch": res.batch.__class__(**{**res.batch.__dict__, "processes": (antigo,)})}
        )


def _pendentes(con):
    linha = con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_pending'").fetchone()
    return json.loads(linha[0]) if linha and linha[0] else []


def test_processo_anterior_a_2026_nao_e_hidratado(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        cfg = montar_cfg(AdaptadorAntigo())
        cfg.max_calls_per_run = cfg.max_calls = 2
        for _ in range(3):
            run_source(con, montar_fonte(sid), cfg)
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 0
        assert _pendentes(con) == []
    finally:
        con.close()


def test_fila_antiga_persistida_e_descartada(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        cfg = montar_cfg(AdaptadorComContratos())
        cfg.max_calls_per_run = cfg.max_calls = 2
        run_source(con, montar_fonte(sid), cfg)
        assert _pendentes(con)  # teto cortou: sobrou fila do processo de 2026
        con.execute("UPDATE process SET year = 2019")
        con.commit()
        run_source(con, montar_fonte(sid), cfg)
        assert _pendentes(con) == []
    finally:
        con.close()
