ALTER TABLE raw_capture ADD COLUMN unchanged INTEGER NOT NULL DEFAULT 0;

DROP INDEX ux_award_identity;

CREATE UNIQUE INDEX ux_award_identity
ON award (
    result_id,
    COALESCE(item_id, 0),
    COALESCE(supplier_org_id, supplier_name_raw)
)
WHERE supersedes_id IS NULL;
