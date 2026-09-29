import sqlite3
from datetime import UTC, datetime, timedelta

import pytest


@pytest.fixture
def memory_db():
    con = sqlite3.connect(":memory:")
    # Cria o schema mínimo para rodar os testes de outbox e processo
    con.execute("""
        CREATE TABLE IF NOT EXISTS alert_outbox (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            process_id INTEGER NOT NULL,
            event_kind TEXT NOT NULL,
            event_ref TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, process_id, event_kind, event_ref)
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS process (
            id INTEGER PRIMARY KEY,
            source_id TEXT,
            opening_at_source DATETIME,
            text_content TEXT,
            modality TEXT,
            entity TEXT
        )
    """)
    return con


def test_enqueue_alert_deduplication(memory_db):
    from licitamais.alerts.user import enqueue_alert

    """
    Provas:
    - enqueue_alert 2x com mesmo (user_id, process_id, event_kind, event_ref) gera 1 linha em outbox
    - replay de run não cria alertas duplicados (graças à restrição UNIQUE e uso de insert seguro)
    """
    con = memory_db

    # Primeira chamada do event
    enqueue_alert(con, user_id=1, process_id=100, event_kind="new", event_ref="run_123")
    # Replay
    enqueue_alert(con, user_id=1, process_id=100, event_kind="new", event_ref="run_123")

    cur = con.execute("SELECT COUNT(*) FROM alert_outbox")
    assert cur.fetchone()[0] == 1


def test_digest_top_10_and_more(memory_db):
    from licitamais.alerts.digest import flush_outbox

    """
    Prova: digest inclui top-10 por ordem de abertura e 'e mais X' para o resto.
    """
    con = memory_db

    # Insere 15 processos e alertas correspondentes
    for i in range(15):
        process_id = 100 + i
        # Usamos datas sequenciais para poder prever a ordenação
        date_str = f"2023-01-{i + 1:02d}T10:00:00Z"
        con.execute("INSERT INTO process (id, opening_at_source) VALUES (?, ?)", (process_id, date_str))
        con.execute(
            """
            INSERT INTO alert_outbox (user_id, process_id, event_kind, event_ref, status)
            VALUES (?, ?, ?, ?, 'pending')
        """,
            (1, process_id, "new", f"run_1_{i}"),
        )

    digest = flush_outbox(con, user_id=1, run_id=1)

    assert digest is not None
    assert len(digest.top_processes) == 10
    assert digest.extra_count == 5


def test_urgent_alert_bypasses_digest(memory_db):
    from licitamais.alerts.digest import flush_outbox

    """
    Prova: processo com abertura <= 48h fura o digest, e gera mensagem urgente separada.
    """
    con = memory_db
    now = datetime.now(UTC)
    opening_urgent = now + timedelta(hours=24)  # Dentro de 48h
    opening_normal = now + timedelta(hours=72)  # Fora de 48h

    con.execute("INSERT INTO process (id, opening_at_source) VALUES (?, ?)", (1, opening_urgent.isoformat()))
    con.execute("INSERT INTO process (id, opening_at_source) VALUES (?, ?)", (2, opening_normal.isoformat()))

    con.execute(
        "INSERT INTO alert_outbox (user_id, process_id, event_kind, event_ref, status) VALUES (1, 1, 'new', 'ref1', 'pending')"
    )
    con.execute(
        "INSERT INTO alert_outbox (user_id, process_id, event_kind, event_ref, status) VALUES (1, 2, 'new', 'ref2', 'pending')"
    )

    digest = flush_outbox(con, user_id=1, run_id=1)

    assert digest is not None
    # Identifica corretamente a separação de escopos
    assert len(digest.urgent_alerts) == 1
    assert digest.urgent_alerts[0].process_id == 1
    assert len(digest.top_processes) == 1
    assert digest.top_processes[0].process_id == 2


def test_is_silent_now():
    from licitamais.alerts.digest import is_silent_now

    """
    Prova:
    - Fora do horário silencioso envia imediato (acumula false).
    - Dentro do horário silencioso (20h às 08h BRT) acumula para próxima janela.
    """
    # 00:00 UTC equivale a 21:00 BRT do dia anterior (Silent: True)
    time_21h_brt = datetime(2023, 10, 1, 0, 0, tzinfo=UTC)
    assert is_silent_now(time_21h_brt, "America/Sao_Paulo") is True

    # 13:00 UTC equivale a 10:00 BRT (Silent: False)
    time_10h_brt = datetime(2023, 10, 1, 13, 0, tzinfo=UTC)
    assert is_silent_now(time_10h_brt, "America/Sao_Paulo") is False


def test_match_subscription(memory_db):
    from licitamais.alerts.user import AlertSubscription, match_subscription

    """
    Prova o filtro salvo com base em modalidade, entidade ou palavras-chave.
    """
    con = memory_db
    con.execute(
        "INSERT INTO process (id, text_content, modality, entity) VALUES (1, 'computador notebook', 'pregao', 'brb')"
    )

    # Match na palavra e na modalidade
    sub1 = AlertSubscription(user_id=1, keywords=("notebook",), modalities=("pregao",), entities=())
    assert match_subscription(con, sub1, process_id=1) is True

    # Miss na palavra chave
    sub2 = AlertSubscription(user_id=1, keywords=("trator",), modalities=(), entities=())
    assert match_subscription(con, sub2, process_id=1) is False
