import pathlib
import sqlite3

import pytest

from licitamais import (
    apply_migrations,
    current_version,
    init_schema,
    table_exists,
)

TABELAS_ESPERADAS = {
    "source",
    "source_run",
    "raw_capture",
    "payload_store",
    "sync_cursor",
    "source_probe",
    "incident",
    "organization",
    "organization_ref",
    "process",
    "item",
    "attachment",
    "result",
    "award",
    "contract",
    "phase_event",
    "process_alias",
    "alert_subscription",
    "alert_outbox",
    "schema_migration",
}


@pytest.fixture
def con():
    conexao = sqlite3.connect(":memory:")
    conexao.execute("PRAGMA foreign_keys = ON")
    yield conexao
    conexao.close()


def escrever_migracao(
    diretorio: pathlib.Path,
    nome: str,
    sql: str,
) -> None:
    (diretorio / nome).write_text(sql, encoding="utf-8")


def sql_inicial(tabela: str) -> str:
    return f"""
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY
);
CREATE TABLE {tabela} (
    id INTEGER PRIMARY KEY
);
"""


def test_init_schema_cria_todas_as_tabelas_do_schema(con):
    init_schema(con)

    faltantes = sorted(tabela for tabela in TABELAS_ESPERADAS if not table_exists(con, tabela))

    assert not faltantes, f"Tabelas ausentes no schema inicial: {faltantes}"


def test_apply_migrations_e_idempotente_e_reflete_versao_final(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_primeira.sql", sql_inicial("ta"))
    escrever_migracao(
        diretorio,
        "002_segunda.sql",
        "CREATE TABLE tb (id INTEGER PRIMARY KEY);",
    )

    assert apply_migrations(con, diretorio) == 2
    assert current_version(con) == 2
    assert table_exists(con, "ta")
    assert table_exists(con, "tb")

    assert apply_migrations(con, diretorio) == 0
    assert current_version(con) == 2


def test_schema_permite_result_award_e_contract_sem_process_aberto(con):
    init_schema(con)

    con.execute(
        """
        INSERT INTO source (
            code,
            transport,
            base_url,
            adapter_version_atual,
            enabled
        )
        VALUES ('fonte_teste', 'api_json', 'https://exemplo.test', '1', 1)
        """
    )
    source_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    con.execute(
        """
        INSERT INTO organization (cnpj, name_raw, name_norm, kind_hint)
        VALUES (
            '12345678000199',
            'Fornecedor Teste',
            'fornecedor teste',
            'fornecedor'
        )
        """
    )
    fornecedor_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    con.execute(
        """
        INSERT INTO result (
            source_id,
            source_native_id,
            type,
            decided_at_source,
            value_total_cents
        )
        VALUES (?, 'resultado-1', 'homologacao', '2026-09-22T12:00:00Z', 150000)
        """,
        (source_id,),
    )
    result_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

    con.execute(
        """
        INSERT INTO award (
            result_id,
            process_id,
            item_id,
            supplier_org_id,
            supplier_name_raw,
            amount_cents,
            qty_awarded
        )
        VALUES (?, NULL, NULL, ?, 'Fornecedor Teste', 150000, 1)
        """,
        (result_id, fornecedor_id),
    )

    con.execute(
        """
        INSERT INTO contract (
            source_id,
            source_native_id,
            supplier_org_id,
            supplier_name_raw,
            value_cents,
            signed_at_source
        )
        VALUES (
            ?,
            'contrato-1',
            ?,
            'Fornecedor Teste',
            150000,
            '2026-09-22T12:00:00Z'
        )
        """,
        (source_id, fornecedor_id),
    )

    assert con.execute("SELECT COUNT(*) FROM result").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM award").fetchone()[0] == 1
    assert con.execute("SELECT COUNT(*) FROM contract").fetchone()[0] == 1


def test_user_version_evolui_apos_cada_migracao(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_primeira.sql", sql_inicial("ta"))

    assert current_version(con) == 0
    assert apply_migrations(con, diretorio) == 1
    assert current_version(con) == 1

    escrever_migracao(
        diretorio,
        "002_segunda.sql",
        "CREATE TABLE tb (id INTEGER PRIMARY KEY);",
    )

    assert apply_migrations(con, diretorio) == 1
    assert current_version(con) == 2


def test_indices_unicos_parciais_existem(con):
    init_schema(con)

    indices = {
        nome: sql
        for nome, sql in con.execute(
            """
            SELECT name, sql
            FROM sqlite_master
            WHERE type = 'index'
              AND name IN ('ux_org_cnpj', 'ux_phase_event_current')
            """
        )
    }

    assert "ux_org_cnpj" in indices
    assert "WHERE CNPJ IS NOT NULL" in indices["ux_org_cnpj"].upper()

    assert "ux_phase_event_current" in indices
    assert "WHERE ENDED_AT IS NULL" in indices["ux_phase_event_current"].upper()


def test_politica_additive_only_esta_documentada_e_sem_sql_destrutivo():
    diretorio = pathlib.Path(__file__).resolve().parents[1] / "migrations"
    readme = diretorio / "README.md"

    assert readme.is_file()

    conteudo = readme.read_text(encoding="utf-8").lower()
    assert "additive-only" in conteudo or "aditivo" in conteudo

    for migracao in diretorio.glob("*.sql"):
        sql = migracao.read_text(encoding="utf-8").upper()
        assert "DROP TABLE" not in sql
        assert " RENAME " not in sql


@pytest.mark.parametrize("criar_diretorio", [False, True])
def test_diretorio_inexistente_ou_sem_migracoes_levanta_excecao(
    con,
    tmp_path,
    criar_diretorio,
):
    diretorio = tmp_path / "migrations"

    if criar_diretorio:
        diretorio.mkdir()
        (diretorio / "README.md").write_text(
            "Politica additive-only.",
            encoding="utf-8",
        )

    with pytest.raises((FileNotFoundError, ValueError)):
        apply_migrations(con, diretorio)


def test_versao_duplicada_falha_antes_de_aplicar_qualquer_migracao(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_a.sql", sql_inicial("ta"))
    escrever_migracao(
        diretorio,
        "001_b.sql",
        "CREATE TABLE tb (id INTEGER PRIMARY KEY);",
    )

    with pytest.raises(ValueError, match="duplicada"):
        apply_migrations(con, diretorio)

    assert not table_exists(con, "schema_migration")
    assert not table_exists(con, "ta")
    assert not table_exists(con, "tb")


def test_lacuna_de_versao_falha_antes_de_aplicar_qualquer_migracao(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_primeira.sql", sql_inicial("ta"))
    escrever_migracao(
        diretorio,
        "003_terceira.sql",
        "CREATE TABLE tc (id INTEGER PRIMARY KEY);",
    )

    with pytest.raises(ValueError, match="lacuna"):
        apply_migrations(con, diretorio)

    assert not table_exists(con, "schema_migration")
    assert not table_exists(con, "ta")
    assert not table_exists(con, "tc")


def test_migracao_com_erro_faz_rollback_integral_e_fecha_transacao(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(
        diretorio,
        "001_falha.sql",
        """
CREATE TABLE schema_migration (version INTEGER PRIMARY KEY);
CREATE TABLE ta (id INTEGER PRIMARY KEY);
ESTE_NAO_E_SQL_VALIDO;
CREATE TABLE tb (id INTEGER PRIMARY KEY);
""",
    )

    with pytest.raises(sqlite3.Error):
        apply_migrations(con, diretorio)

    assert current_version(con) == 0
    assert not table_exists(con, "schema_migration")
    assert not table_exists(con, "ta")
    assert not table_exists(con, "tb")
    assert not con.in_transaction


def test_falha_ao_registrar_migracao_nao_avanca_versao_nem_persiste_schema(
    con,
    tmp_path,
):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_primeira.sql", sql_inicial("ta"))
    assert apply_migrations(con, diretorio) == 1

    con.execute("INSERT INTO schema_migration (version) VALUES (2)")
    con.commit()

    escrever_migracao(
        diretorio,
        "002_segunda.sql",
        "CREATE TABLE tb (id INTEGER PRIMARY KEY);",
    )

    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(con, diretorio)

    assert current_version(con) == 1
    assert not table_exists(con, "tb")
    assert not con.in_transaction


def test_migracao_com_duas_sentencas_na_mesma_linha_aplica_ambas(con, tmp_path):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(
        diretorio,
        "001_duas.sql",
        "CREATE TABLE schema_migration (version INTEGER PRIMARY KEY); "
        "CREATE TABLE t_duas_na_linha (id INTEGER PRIMARY KEY);",
    )

    assert apply_migrations(con, diretorio) == 1
    assert current_version(con) == 1
    assert table_exists(con, "schema_migration")
    assert table_exists(con, "t_duas_na_linha")


def test_arquivo_com_prefixo_de_versao_sem_extensao_sql_levanta_erro(
    con,
    tmp_path,
):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(diretorio, "001_primeira.sql", sql_inicial("ta"))
    escrever_migracao(
        diretorio,
        "002_segunda",
        "CREATE TABLE tb (id INTEGER PRIMARY KEY);",
    )

    with pytest.raises(ValueError):
        apply_migrations(con, diretorio)

    assert not table_exists(con, "schema_migration")
    assert not table_exists(con, "ta")
    assert not table_exists(con, "tb")


def test_falha_no_commit_mantem_user_version_e_schema_migration_consistentes(
    con,
    tmp_path,
):
    diretorio = tmp_path / "migrations"
    diretorio.mkdir()

    escrever_migracao(
        diretorio,
        "001_primeira.sql",
        """
CREATE TABLE schema_migration (version INTEGER PRIMARY KEY);
CREATE TABLE pai (id INTEGER PRIMARY KEY);
CREATE TABLE filho (
    pai_id INTEGER REFERENCES pai(id) DEFERRABLE INITIALLY DEFERRED
);
""",
    )
    assert apply_migrations(con, diretorio) == 1

    escrever_migracao(
        diretorio,
        "002_falha_no_commit.sql",
        """
CREATE TABLE nao_deve_persistir (id INTEGER PRIMARY KEY);
INSERT INTO filho (pai_id) VALUES (999);
""",
    )

    with pytest.raises(sqlite3.IntegrityError):
        apply_migrations(con, diretorio)

    assert current_version(con) == 1
    assert con.execute("SELECT MAX(version) FROM schema_migration").fetchone()[0] == 1
    assert not table_exists(con, "nao_deve_persistir")
    assert not con.in_transaction
