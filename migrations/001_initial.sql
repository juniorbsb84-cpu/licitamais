CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY
);

CREATE TABLE source (
    id INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL CHECK (
        transport IN ('api_json', 'api_html', 'wp_json')
    ),
    base_url TEXT NOT NULL,
    adapter_version_atual TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    attrs TEXT
);

CREATE TABLE source_run (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    "trigger" TEXT NOT NULL CHECK (
        "trigger" IN ('cron', 'manual', 'backfill')
    ),
    adapter_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('ok', 'ok_zero', 'suspect', 'partial', 'failed')
    ),
    started_at TEXT,
    finished_at TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    new_count INTEGER NOT NULL DEFAULT 0,
    changed_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    quarantined_count INTEGER NOT NULL DEFAULT 0,
    watermark_in TEXT,
    watermark_out TEXT,
    error TEXT
);

CREATE TABLE payload_store (
    id INTEGER PRIMARY KEY,
    sha256 TEXT NOT NULL UNIQUE,
    storage_kind TEXT NOT NULL CHECK (storage_kind IN ('inline', 'file')),
    size_bytes INTEGER NOT NULL,
    inline_bytes BLOB,
    file_path TEXT,
    created_at TEXT
);

CREATE TABLE raw_capture (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_run_id INTEGER REFERENCES source_run(id),
    endpoint TEXT,
    url TEXT,
    payload_id INTEGER NOT NULL REFERENCES payload_store(id),
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    http_status INTEGER,
    content_type TEXT,
    captured_at TEXT NOT NULL
);

CREATE INDEX ix_raw_capture_sha256 ON raw_capture (sha256);

CREATE TABLE parse_quarantine (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_run_id INTEGER REFERENCES source_run(id),
    raw_capture_id INTEGER REFERENCES raw_capture(id),
    native_ref TEXT,
    error TEXT NOT NULL,
    raw_excerpt TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE sync_cursor (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    cursor_key TEXT NOT NULL,
    "value" TEXT,
    updated_run_id INTEGER REFERENCES source_run(id),
    updated_at TEXT,
    UNIQUE (source_id, cursor_key)
);

CREATE TABLE source_probe (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    min_rows_pct REAL,
    required_fields TEXT,
    max_staleness_days INTEGER,
    max_quarantine_pct REAL,
    UNIQUE (source_id)
);

CREATE TABLE incident (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    kind TEXT NOT NULL CHECK (kind IN ('failed', 'suspect', 'degraded')),
    severity TEXT,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    last_notified_at TEXT,
    notify_count INTEGER NOT NULL DEFAULT 0,
    message TEXT
);

CREATE TABLE organization (
    id INTEGER PRIMARY KEY,
    cnpj TEXT,
    name_raw TEXT NOT NULL,
    name_norm TEXT NOT NULL,
    kind_hint TEXT CHECK (
        kind_hint IN ('comprador', 'fornecedor', 'ambos')
    ),
    attrs TEXT
);

CREATE UNIQUE INDEX ux_org_cnpj
ON organization (cnpj)
WHERE cnpj IS NOT NULL;

CREATE TABLE organization_ref (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    native_org_key TEXT NOT NULL,
    org_id INTEGER NOT NULL REFERENCES organization(id),
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    UNIQUE (source_id, native_org_key)
);

CREATE TABLE process (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_native_id TEXT NOT NULL,
    org_id INTEGER REFERENCES organization(id),
    number TEXT,
    year INTEGER,
    modality_code TEXT,
    modality_raw TEXT,
    title TEXT,
    object TEXT,
    phase_current_code TEXT,
    phase_current_label TEXT,
    published_at_source TEXT,
    opening_at_source TEXT,
    source_updated_at_source TEXT,
    record_hash TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_changed_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    deleted_at TEXT,
    attrs TEXT,
    UNIQUE (source_id, source_native_id)
);

CREATE TABLE item (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_native_id TEXT NOT NULL,
    process_id INTEGER REFERENCES process(id),
    lot_number TEXT,
    seq INTEGER,
    description TEXT,
    qty REAL,
    unit TEXT,
    unit_price_estimated_cents INTEGER,
    total_price_estimated_cents INTEGER,
    description_raw TEXT,
    qty_raw TEXT,
    unit_raw TEXT,
    unit_price_estimated_raw TEXT,
    total_price_estimated_raw TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_changed_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    deleted_at TEXT,
    attrs TEXT,
    UNIQUE (source_id, source_native_id)
);

CREATE TABLE attachment (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_ref TEXT NOT NULL,
    process_id INTEGER REFERENCES process(id),
    kind TEXT CHECK (
        kind IN ('edital', 'aviso', 'resultado', 'contrato', 'outro')
    ),
    url_download TEXT,
    filename TEXT,
    mime TEXT,
    size_bytes INTEGER,
    sha256 TEXT,
    local_path TEXT,
    published_at_source TEXT,
    collected_at TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    deleted_at TEXT,
    attrs TEXT,
    UNIQUE (source_id, source_ref)
);

CREATE TABLE result (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_native_id TEXT NOT NULL,
    process_id INTEGER REFERENCES process(id),
    type TEXT NOT NULL CHECK (
        type IN (
            'homologacao',
            'adjudicacao',
            'fracassada',
            'deserta',
            'revogada',
            'anulada'
        )
    ),
    decided_at_source TEXT,
    value_total_cents INTEGER,
    value_total_raw TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    attrs TEXT,
    UNIQUE (source_id, source_native_id)
);

CREATE TABLE award (
    id INTEGER PRIMARY KEY,
    result_id INTEGER NOT NULL REFERENCES result(id),
    process_id INTEGER REFERENCES process(id),
    item_id INTEGER REFERENCES item(id),
    supplier_org_id INTEGER REFERENCES organization(id),
    supplier_name_raw TEXT NOT NULL,
    amount_cents INTEGER,
    amount_raw TEXT,
    qty_awarded REAL,
    qty_awarded_raw TEXT,
    supersedes_id INTEGER REFERENCES award(id),
    first_seen_at TEXT,
    last_seen_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    attrs TEXT
);

CREATE UNIQUE INDEX ux_award_identity
ON award (
    result_id,
    COALESCE(item_id, 0),
    COALESCE(supplier_org_id, supplier_name_raw)
);

CREATE TABLE contract (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES source(id),
    source_native_id TEXT NOT NULL,
    process_id INTEGER REFERENCES process(id),
    number TEXT,
    supplier_org_id INTEGER REFERENCES organization(id),
    supplier_name_raw TEXT NOT NULL,
    value_cents INTEGER,
    value_raw TEXT,
    signed_at_source TEXT,
    vigency_start TEXT,
    vigency_end TEXT,
    supersedes_id INTEGER REFERENCES contract(id),
    first_seen_at TEXT,
    last_seen_at TEXT,
    first_seen_run_id INTEGER REFERENCES source_run(id),
    last_seen_run_id INTEGER REFERENCES source_run(id),
    last_changed_run_id INTEGER REFERENCES source_run(id),
    attrs TEXT,
    UNIQUE (source_id, source_native_id)
);

CREATE TABLE phase_event (
    id INTEGER PRIMARY KEY,
    process_id INTEGER NOT NULL REFERENCES process(id),
    phase_code TEXT NOT NULL,
    phase_label TEXT,
    started_run_id INTEGER REFERENCES source_run(id),
    started_at TEXT NOT NULL,
    ended_run_id INTEGER REFERENCES source_run(id),
    ended_at TEXT,
    confirm_count INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT,
    last_seen_at TEXT,
    last_seen_run_id INTEGER REFERENCES source_run(id),
    attrs TEXT
);

CREATE UNIQUE INDEX ux_phase_event_current
ON phase_event (process_id)
WHERE ended_at IS NULL;

CREATE TABLE process_alias (
    id INTEGER PRIMARY KEY,
    canonical_process_id INTEGER NOT NULL REFERENCES process(id),
    member_process_id INTEGER NOT NULL REFERENCES process(id),
    match_rule TEXT NOT NULL,
    created_run_id INTEGER REFERENCES source_run(id),
    created_at TEXT,
    UNIQUE (canonical_process_id, member_process_id)
);

CREATE TABLE alert_subscription (
    id INTEGER PRIMARY KEY,
    kind TEXT,
    channel TEXT,
    target TEXT,
    filter_expr TEXT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT,
    attrs TEXT
);

CREATE TABLE alert_outbox (
    id INTEGER PRIMARY KEY,
    subscription_id INTEGER REFERENCES alert_subscription(id),
    dedup_key TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (
        status IN ('pending', 'sent', 'failed')
    ),
    created_at TEXT,
    sent_at TEXT
);
