"""R24b: contrato muda sem alterar o hash da listagem de processos."""

import json

from licitamais.runner import run_source
from licitamais.types import ContractRecord, FetchRequest, NormalizedBatch, ParseResult
from tests.test_r13_runner_alertas import (
    AdaptadorSintetico,
    fonte_id,
    montar_cfg,
    montar_fonte,
    nova_conexao,
    sem_espera,
)
from tests.test_r24_hidratacao_persistente import VAZIO


class AdaptadorContratoMutavel(AdaptadorSintetico):
    def __init__(self):
        super().__init__()
        self.valor = 100
        self.hydrated = []
        self.second_process = False
        self.capabilities = self.capabilities.__class__(
            **{
                **self.capabilities.__dict__,
                "hydration": {"enabled": True, "trigger": "new_or_changed", "refresh_per_run": 1},
                "deletion_semantics": "none",
            }
        )

    def hydration_requests(self, process):
        return (
            FetchRequest(
                endpoint=f"/api/contrato/{process.attrs['contratos_ids'][0]}",
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
            self.hydrated.append(req.endpoint)
        return super().fetch(sessao, req)

    def parse(self, pagina):
        if pagina.request.phase == "hydrate":
            native_id = pagina.request.parent_native_id
            contract_id = pagina.request.endpoint.rsplit("/", 1)[1]
            contrato = ContractRecord(
                source_native_id=f"{native_id}:contrato:{contract_id}",
                attrs={
                    "process_native_id": native_id,
                    "number": contract_id,
                    "supplier_name_raw": "Fornecedor",
                    "value_cents": self.valor,
                },
            )
            return ParseResult(
                batch=NormalizedBatch(**{**VAZIO, "contracts": (contrato,)}),
                quarantine=(),
                next=(),
                cursor_out=None,
                signals={},
                fatal=None,
            )
        parsed = super().parse(pagina)
        process = parsed.batch.processes[0]
        process = process.__class__(
            source_native_id=process.source_native_id,
            attrs={**process.attrs, "contratos_ids": [7]},
        )
        processes = (process,)
        if self.second_process:
            processes += (
                process.__class__(
                    source_native_id="edital-2",
                    attrs={**process.attrs, "contratos_ids": [8]},
                ),
            )
        return parsed.__class__(**{**parsed.__dict__, "batch": NormalizedBatch(**{**VAZIO, "processes": processes})})


def test_contrato_alterado_rehidratado_com_processo_inalterado(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        source_id = fonte_id(con)
        adaptador = AdaptadorContratoMutavel()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(source_id)

        primeiro = run_source(con, fonte, cfg)
        original = con.execute("SELECT id, value_cents FROM contract").fetchone()
        hash_original = con.execute("SELECT record_hash FROM process").fetchone()[0]
        assert primeiro.error is None
        assert original["value_cents"] == 100

        adaptador.valor = 200
        segundo = run_source(con, fonte, cfg)
        versoes = con.execute("SELECT id, value_cents, supersedes_id FROM contract ORDER BY id").fetchall()

        assert segundo.error is None
        assert con.execute("SELECT record_hash FROM process").fetchone()[0] == hash_original
        assert [(row["value_cents"], row["supersedes_id"]) for row in versoes] == [
            (100, None),
            (200, original["id"]),
        ]
    finally:
        con.close()


def test_novo_contrato_tem_prioridade_e_refresh_cortado_fica_pendente(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        source_id = fonte_id(con)
        adaptador = AdaptadorContratoMutavel()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(source_id)
        run_source(con, fonte, cfg)

        adaptador.second_process = True
        cfg.max_calls_per_run = 2
        segundo = run_source(con, fonte, cfg)
        assert "teto max_calls_per_run atingido" in (segundo.error or "")
        assert adaptador.hydrated == ["/api/contrato/7", "/api/contrato/8"]
        pendentes = json.loads(
            con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_pending'").fetchone()[0]
        )
        assert [req["endpoint"] for req in pendentes] == ["/api/contrato/7"]

        cfg.max_calls_per_run = 100
        run_source(con, fonte, cfg)
        assert adaptador.hydrated.count("/api/contrato/7") == 2
        assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 2
    finally:
        con.close()


def test_refresh_roda_em_rodizio_e_recomeca_apos_ultimo_id(monkeypatch):
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        source_id = fonte_id(con)
        adaptador = AdaptadorContratoMutavel()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(source_id)
        run_source(con, fonte, cfg)
        primeiro_id = con.execute("SELECT id FROM process").fetchone()[0]
        segundo_id = con.execute(
            "INSERT INTO process (source_id, source_native_id, record_hash, attrs, year)"
            " VALUES (?, 'edital-2', 'hash-v1', ?, 2026)",
            (source_id, json.dumps({"contratos_ids": [8]})),
        ).lastrowid
        # rodizio so visita processo que ja tem contrato gravado
        con.execute(
            "INSERT INTO contract (source_id, source_native_id, process_id, supplier_name_raw)"
            " VALUES (?, 'edital-2:contrato:8', ?, 'Fornecedor')",
            (source_id, segundo_id),
        )
        con.commit()

        run_source(con, fonte, cfg)
        assert con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_refresh_after'").fetchone()[
            0
        ] == str(primeiro_id)
        run_source(con, fonte, cfg)
        assert con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_refresh_after'").fetchone()[
            0
        ] == str(segundo_id)
        run_source(con, fonte, cfg)
        assert adaptador.hydrated == [
            "/api/contrato/7",
            "/api/contrato/7",
            "/api/contrato/8",
            "/api/contrato/7",
        ]
    finally:
        con.close()


def test_refresh_pula_processo_sem_contrato(monkeypatch):
    """BRB: ~2/3 dos processos nao tem contrato; rodizio nao pode gastar a vez neles."""
    sem_espera(monkeypatch)
    con = nova_conexao()
    try:
        source_id = fonte_id(con)
        adaptador = AdaptadorContratoMutavel()
        cfg = montar_cfg(adaptador)
        fonte = montar_fonte(source_id)
        run_source(con, fonte, cfg)
        primeiro_id = con.execute("SELECT id FROM process").fetchone()[0]
        con.execute(
            "INSERT INTO process (source_id, source_native_id, record_hash, attrs, year)"
            " VALUES (?, 'sem-contrato', 'hash-v1', ?, 2026)",
            (source_id, json.dumps({"contratos_ids": [9]})),
        )
        con.commit()

        for _ in range(3):
            run_source(con, fonte, cfg)
        assert "/api/contrato/9" not in adaptador.hydrated
        assert con.execute("SELECT value FROM sync_cursor WHERE cursor_key = 'hydration_refresh_after'").fetchone()[
            0
        ] == str(primeiro_id)
    finally:
        con.close()
