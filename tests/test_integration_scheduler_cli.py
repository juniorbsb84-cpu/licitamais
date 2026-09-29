"""Costura real CLI -> scheduler -> contrato do runner, sem rede externa."""

import sqlite3

from licitamais.__main__ import main
from licitamais.runner import RunConfig, RunCounts, RunOutcome, SourceRecord, run_source
from licitamais.schema import init_schema
from licitamais.types import Capabilities, FetchedPage, FetchRequest, NormalizedBatch, ParseResult, ProcessRecord


def _banco_com_fonte(tmp_path):
    db = tmp_path / "licitamais.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    init_schema(con)
    con.execute(
        "INSERT INTO source (code, transport, base_url, adapter_version_atual) "
        "VALUES ('brb', 'api_json', 'https://brb.test', 'v1')"
    )
    con.commit()
    return db, con


def _source(con):
    row = con.execute("SELECT id, code, base_url, adapter_version_atual FROM source").fetchone()
    return SourceRecord(*row)


def test_cli_passa_fonte_e_config_validas_ao_runner(tmp_path, monkeypatch):
    db, con = _banco_com_fonte(tmp_path)
    con.close()

    chamadas = []

    def run_source(con: sqlite3.Connection, source: SourceRecord, cfg: RunConfig):
        assert con.row_factory is sqlite3.Row
        assert con.execute("SELECT COUNT(*) FROM source").fetchone()[0] == 1
        assert isinstance(source, SourceRecord)
        assert isinstance(cfg, RunConfig)
        chamadas.append((source.code, cfg.trigger, cfg.capture_only))
        return RunOutcome(1, source.id, source.code, "ok", RunCounts())

    monkeypatch.setattr("licitamais.runner.run_source", run_source)
    assert main(["--db", str(db), "--source", "brb", "--capture-only"]) == 0
    assert chamadas == [("brb", "manual", True)]


def test_falha_de_abertura_fecha_run_no_banco(tmp_path):
    db, con = _banco_com_fonte(tmp_path)

    class Adaptador:
        def open(self, _cfg):
            raise RuntimeError("handshake recusado")

    try:
        outcome = run_source(con, _source(con), RunConfig(adapters={"brb": Adaptador()}))
        assert outcome.status == "failed"
        con.close()
        with sqlite3.connect(db) as verificador:
            row = verificador.execute("SELECT status, finished_at, error FROM source_run").fetchone()
            assert row[0] == "failed"
            assert row[1]
            assert "handshake recusado" in row[2]
    finally:
        if con:
            con.close()


def test_primeiro_rollback_preserva_run_e_capture_only_conta_resposta(tmp_path, monkeypatch):
    _db, con = _banco_com_fonte(tmp_path)
    req = FetchRequest("/api", "GET", {}, None, {}, "discover", "process", None, 1, None)

    class Adaptador:
        def open(self, _cfg):
            return object()

        def plan(self, _ctx):
            return (req,)

        def fetch(self, _session, request):
            return FetchedPage(request, 200, {"content-type": "application/json"}, b"{}", "2026-09-22T00:00:00Z", 1)

        def close(self, _session):
            pass

    cfg = RunConfig(adapters={"brb": Adaptador()}, capture_only=True)
    try:

        def falha_captura(*_args):
            raise RuntimeError("disco indisponivel")

        with monkeypatch.context() as patch:
            patch.setattr("licitamais.runner.record_capture", falha_captura)
            outcome = run_source(con, _source(con), cfg)
        assert outcome.status == "failed"
        assert con.execute("SELECT status, finished_at FROM source_run").fetchone()[0] == "failed"

        outcome = run_source(con, _source(con), cfg)
        assert outcome.counts.fetched == 1
        row = con.execute("SELECT fetched_count FROM source_run WHERE id = ?", (outcome.run_id,)).fetchone()
        assert row[0] == 1
    finally:
        con.close()


def test_runner_aplica_sonda_configurada_e_abre_incidente(tmp_path):
    _db, con = _banco_com_fonte(tmp_path)
    source = _source(con)
    con.execute(
        "INSERT INTO source_probe (source_id, required_fields) VALUES (?, ?)",
        (source.id, '["id"]'),
    )
    con.commit()
    req = FetchRequest("/api", "GET", {}, None, {}, "discover", "process", None, 1, None)

    class Adaptador:
        def open(self, _cfg):
            return object()

        def plan(self, _ctx):
            return (req,)

        def fetch(self, _session, request):
            return FetchedPage(request, 200, {"content-type": "application/json"}, b"{}", "2026-09-22T00:00:00Z", 1)

        def close(self, _session):
            pass

    try:
        outcome = run_source(con, source, RunConfig(adapters={"brb": Adaptador()}, capture_only=True))
        assert outcome.status == "suspect"
        incident = con.execute(
            "SELECT kind, message FROM incident WHERE source_id = ? AND closed_at IS NULL",
            (source.id,),
        ).fetchone()
        assert incident is not None
        assert incident[0] == "suspect"
        assert "canario" in incident[1]
    finally:
        con.close()


def test_sonda_piso_compara_com_run_anterior_nao_com_o_atual(tmp_path):
    _db, con = _banco_com_fonte(tmp_path)
    source = _source(con)
    con.execute(
        "INSERT INTO source_probe (source_id, min_rows_pct, required_fields) VALUES (?, ?, ?)",
        (source.id, 0.85, '["id"]'),
    )
    con.execute(
        'INSERT INTO source_run (source_id, "trigger", adapter_version, status, '
        "started_at, finished_at, fetched_count) VALUES (?, 'manual', 'v1', 'ok', "
        "'2026-09-21T00:00:00Z', '2026-09-21T00:01:00Z', 10)",
        (source.id,),
    )
    con.commit()
    req = FetchRequest("/api", "GET", {}, None, {}, "discover", "process", None, 1, None)

    class Adaptador:
        def open(self, _cfg):
            return object()

        def plan(self, _ctx):
            return (req,)

        def fetch(self, _session, request):
            return FetchedPage(
                request, 200, {"content-type": "application/json"}, b'{"id":"x"}', "2026-09-22T00:00:00Z", 1
            )

        def close(self, _session):
            pass

    try:
        outcome = run_source(con, source, RunConfig(adapters={"brb": Adaptador()}, capture_only=True))
        assert outcome.status == "suspect"
        detail = con.execute(
            "SELECT message FROM incident WHERE source_id = ? AND closed_at IS NULL",
            (source.id,),
        ).fetchone()[0]
        assert "piso relativo" in detail
        assert "previo 10" in detail
    finally:
        con.close()


def test_piso_usa_rows_count_declarado_nao_numero_de_respostas_http(tmp_path):
    _db, con = _banco_com_fonte(tmp_path)
    source = _source(con)
    con.execute(
        "INSERT INTO source_probe (source_id, min_rows_pct, required_fields) VALUES (?, ?, ?)",
        (source.id, 0.85, '["id"]'),
    )
    con.execute(
        "INSERT INTO sync_cursor (source_id, cursor_key, value, updated_at) "
        "VALUES (?, 'RowsCount', '100', '2026-09-22T00:00:00Z')",
        (source.id,),
    )
    con.commit()
    req = FetchRequest("/api", "GET", {}, None, {}, "discover", "process", None, 1, None)

    class Adaptador:
        def open(self, _cfg):
            return object()

        def plan(self, _ctx):
            return (req,)

        def fetch(self, _session, request):
            return FetchedPage(
                request, 200, {"content-type": "application/json"}, b'{"id":"x"}', "2026-09-22T00:00:00Z", 1
            )

        def parse(self, _page):
            empty = NormalizedBatch((), (), (), (), (), (), (), ())
            return ParseResult(empty, (), (), None, {"row_count_declared": 100})

        def close(self, _session):
            pass

    try:
        outcome = run_source(con, source, RunConfig(adapters={"brb": Adaptador()}))
        assert outcome.counts.fetched == 1
        assert outcome.status == "suspect"  # freshness ainda falha sem processos
        detail = con.execute(
            "SELECT message FROM incident WHERE source_id = ? AND closed_at IS NULL",
            (source.id,),
        ).fetchone()[0]
        assert "piso relativo" not in detail
    finally:
        con.close()


def test_hidratacao_so_para_processo_novo_ou_mudado(tmp_path):
    _db, con = _banco_com_fonte(tmp_path)
    source = _source(con)
    discover = FetchRequest("/lista", "GET", {}, None, {}, "discover", "process", None, 1, None)

    class Adaptador:
        capabilities = Capabilities(
            "full_diff",
            ("process", "item"),
            {"enabled": True, "trigger": "new_or_changed"},
            True,
            False,
            "none",
            {},
            "none",
            {},
        )

        def __init__(self):
            self.fases = []
            self.title = "Teste"

        def open(self, _cfg):
            return object()

        def plan(self, _ctx):
            return (discover,)

        def hydration_requests(self, process):
            return (
                FetchRequest(
                    f"/detalhe/{process.source_native_id}",
                    "GET",
                    {},
                    None,
                    {},
                    "hydrate",
                    "item",
                    process.source_native_id,
                    1,
                    None,
                ),
            )

        def fetch(self, _session, request):
            self.fases.append(request.phase)
            return FetchedPage(request, 200, {"content-type": "application/json"}, b"{}", "2026-09-22T00:00:00Z", 1)

        def parse(self, page):
            process = (
                (ProcessRecord("p-1", {"number": "1/2026", "year": 2026, "title": self.title}),)
                if page.request.phase == "discover"
                else ()
            )
            return ParseResult(
                NormalizedBatch((), process, (), (), (), (), (), ()),
                (),
                (),
                None,
                {"row_count_declared": 1},
            )

        def close(self, _session):
            pass

    adapter = Adaptador()
    cfg = RunConfig(adapters={"brb": adapter})
    try:
        run_source(con, source, cfg)
        assert adapter.fases == ["discover", "hydrate"]
        run_source(con, source, cfg)
        assert adapter.fases == ["discover", "hydrate", "discover"]
        adapter.title = "Teste alterado"
        third = run_source(con, source, cfg)
        row = con.execute("SELECT record_hash, attrs FROM process WHERE source_native_id = 'p-1'").fetchone()
        assert adapter.fases == ["discover", "hydrate", "discover", "discover", "hydrate"], (row, third)
    finally:
        con.close()


def test_adapters_mapeiam_requisicoes_auxiliares_sem_rede():
    from licitamais.adapters.brb import BRBAdapter
    from licitamais.adapters.sistema_industria import SistemaIndustriaAdapter

    process = ProcessRecord("p-1")
    # BRB real (2026-09-23): anexos embutidos; so o contrato pede detalhe
    brb = BRBAdapter().hydration_requests(ProcessRecord("p-1", {"contratos_ids": ["9"]}))
    industria = SistemaIndustriaAdapter().hydration_requests(process)
    assert [(r.phase, r.entity_hint, r.endpoint) for r in brb] == [("hydrate", "contract", "/api/contrato/9")]
    # SI real (2026-09-24): detalhe vem na descoberta; hidratacao busca itens por
    # lote e a sala de disputa publica (vencedor + propostas, sem login)
    assert [(r.phase, r.entity_hint) for r in industria] == [("items", "item"), ("hydrate", "award")]
