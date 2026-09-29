"""R15: runner nao engole falha de commit, falha na geracao de alertas nem
fonte sem sonda configurada (achado D2 da auditoria)."""

from __future__ import annotations

import licitamais.runner as runner_mod
from licitamais.runner import run_source
from tests.test_r13_runner_alertas import (
    AdaptadorSintetico,
    criar_assinatura,
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
)


class ConexaoQueFalhaCommit:
    """Delega ao sqlite3 real; falha o proximo commit quando armado."""

    def __init__(self, con):
        self._con = con
        self.falhar_proximo_commit = False

    def commit(self):
        if self.falhar_proximo_commit:
            self.falhar_proximo_commit = False
            raise RuntimeError("disco cheio no commit")
        return self._con.commit()

    def __getattr__(self, nome):
        return getattr(self._con, nome)


def erro_do_run(con, run_id):
    return con.execute("SELECT error FROM source_run WHERE id = ?", (run_id,)).fetchone()[0] or ""


def incidentes(con, sid):
    return con.execute("SELECT kind, message FROM incident WHERE source_id = ?", (sid,)).fetchall()


def test_commit_do_lote_falha_nao_some(monkeypatch):
    real = nova_conexao()
    try:
        sid = fonte_id(real)
        con = ConexaoQueFalhaCommit(real)
        load_original = runner_mod.load_batch

        def load_e_arma(*a, **k):
            resultado = load_original(*a, **k)
            con.falhar_proximo_commit = True
            return resultado

        monkeypatch.setattr(runner_mod, "load_batch", load_e_arma)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "failed"
        assert "disco cheio no commit" in (resultado.error or "")
        assert "disco cheio no commit" in erro_do_run(real, resultado.run_id)
    finally:
        real.close()


def test_falha_em_gerar_alertas_nao_derruba_run(monkeypatch):
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        criar_assinatura(con)

        def explode(*a, **k):
            raise RuntimeError("filtro corrompido")

        monkeypatch.setattr(runner_mod, "gerar_alertas_do_run", explode)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "ok"
        assert "filtro corrompido" in (resultado.error or "")
        assert "filtro corrompido" in erro_do_run(con, resultado.run_id)
        status_gravado = con.execute("SELECT status FROM source_run WHERE id = ?", (resultado.run_id,)).fetchone()[0]
        assert status_gravado == "ok"
    finally:
        con.close()


def test_commit_dos_alertas_falha_nao_some(monkeypatch):
    real = nova_conexao()
    try:
        sid = fonte_id(real)
        criar_assinatura(real)
        con = ConexaoQueFalhaCommit(real)
        gerar_original = runner_mod.gerar_alertas_do_run

        def gera_e_arma(*a, **k):
            n = gerar_original(*a, **k)
            con.falhar_proximo_commit = True
            return n

        monkeypatch.setattr(runner_mod, "gerar_alertas_do_run", gera_e_arma)
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "ok"
        assert "disco cheio no commit" in (resultado.error or "")
        assert "disco cheio no commit" in erro_do_run(real, resultado.run_id)
    finally:
        real.close()


def test_fonte_sem_sonda_configurada_e_suspect_com_incidente():
    con = nova_conexao()
    try:
        sid = fonte_id(con)
        con.execute("DELETE FROM source_probe WHERE source_id = ?", (sid,))
        con.commit()
        resultado = run_source(con, montar_fonte(sid), montar_cfg(AdaptadorSintetico()))

        assert resultado.status == "suspect"
        assert "sem sonda configurada" in (resultado.error or "")
        assert "sem sonda configurada" in erro_do_run(con, resultado.run_id)
        assert any(linha[0] == "suspect" for linha in incidentes(con, sid))
    finally:
        con.close()
