-- M5: historico de preco read-only, com proveniencia (run que viu/mudou a linha).
-- Versao vigente = linha que nenhuma outra substitui (supersedes_id).

CREATE VIEW v_preco_contrato AS
SELECT
    s.code                                   AS fonte,
    p.number                                 AS processo_numero,
    p.year                                   AS processo_ano,
    COALESCE(json_extract(c.attrs, '$.object'), p.object) AS objeto,
    COALESCE(c.supplier_name_raw, o.name_raw) AS fornecedor,
    o.cnpj                                   AS fornecedor_cnpj,
    c.number                                 AS contrato_numero,
    c.value_cents / 100.0                    AS valor,
    c.signed_at_source                       AS assinado_em,
    c.vigency_start                          AS vigencia_inicio,
    c.vigency_end                            AS vigencia_fim,
    c.first_seen_run_id                      AS visto_no_run,
    c.last_changed_run_id                    AS mudou_no_run,
    c.id                                     AS contract_id
FROM contract c
JOIN source s ON s.id = c.source_id
LEFT JOIN process p ON p.id = c.process_id
LEFT JOIN organization o ON o.id = c.supplier_org_id
WHERE c.value_cents IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM contract n WHERE n.supersedes_id = c.id);

CREATE VIEW v_preco_item AS
SELECT
    s.code                                   AS fonte,
    p.number                                 AS processo_numero,
    i.description                            AS item,
    i.unit                                   AS unidade,
    a.qty_awarded                            AS quantidade,
    a.amount_cents / 100.0                   AS valor_total,
    CASE WHEN a.qty_awarded > 0 THEN a.amount_cents / 100.0 / a.qty_awarded END AS valor_unitario,
    COALESCE(a.supplier_name_raw, o.name_raw) AS fornecedor,
    a.first_seen_run_id                      AS visto_no_run,
    a.id                                     AS award_id
FROM award a
JOIN process p ON p.id = a.process_id
JOIN source s ON s.id = p.source_id
LEFT JOIN item i ON i.id = a.item_id
LEFT JOIN organization o ON o.id = a.supplier_org_id
WHERE a.amount_cents IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id);

CREATE VIEW v_fornecedor_resumo AS
SELECT fonte, fornecedor, COUNT(*) AS contratos, SUM(valor) AS valor_total,
       MIN(vigencia_inicio) AS primeiro, MAX(vigencia_inicio) AS ultimo
FROM v_preco_contrato
GROUP BY fonte, fornecedor;
