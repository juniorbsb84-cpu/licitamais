-- 004: inteligencia de preco (r39).
-- Views analiticas read-only sobre award/result vigentes.
-- Vigente = linha que nenhuma outra substitui via supersedes_id (criterio da 003).
-- Perdedoras em result.attrs.propostas: array JSON nome/cnpj/valor em reais.
-- Vencedor em award com amount_cents. Medias/descontos usam so valor numerico;
-- linha sem valor conta em n_propostas mas nao entra em media/max.
-- O CASE json_valid protege o json_each de attrs fora do padrao do loader.

CREATE VIEW v_vencedor_por_orgao AS
SELECT
    s.code AS fonte,
    p.org_id AS comprador_org_id,
    comp.name_raw AS comprador,
    COALESCE(a.supplier_name_raw, forn.name_raw) AS fornecedor,
    COALESCE(forn.cnpj, json_extract(a.attrs, '$.supplier_cnpj')) AS fornecedor_cnpj,
    COUNT(*) AS vitorias,
    SUM(a.amount_cents) / 100.0 AS valor_total
FROM award a
JOIN result r ON r.id = a.result_id
JOIN source s ON s.id = r.source_id
LEFT JOIN process p ON p.id = a.process_id
LEFT JOIN organization comp ON comp.id = p.org_id
LEFT JOIN organization forn ON forn.id = a.supplier_org_id
WHERE NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id)
GROUP BY s.code, p.org_id, comp.name_raw,
    COALESCE(a.supplier_name_raw, forn.name_raw),
    COALESCE(forn.cnpj, json_extract(a.attrs, '$.supplier_cnpj'));

CREATE VIEW v_concorrencia AS
SELECT
    s.code AS fonte,
    p.number AS processo_numero,
    r.source_native_id AS lote,
    r.id AS result_id,
    (SELECT COUNT(*) FROM json_each(
        CASE WHEN json_valid(r.attrs) THEN r.attrs ELSE '{}' END,
        '$.propostas')) AS n_propostas,
    (SELECT COUNT(*) FROM award a WHERE a.result_id = r.id
        AND NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id)) AS n_vencedores,
    (SELECT COUNT(*) FROM json_each(
        CASE WHEN json_valid(r.attrs) THEN r.attrs ELSE '{}' END,
        '$.propostas'))
    + (SELECT COUNT(*) FROM award a WHERE a.result_id = r.id
        AND NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id)) AS n_total
FROM result r
JOIN source s ON s.id = r.source_id

LEFT JOIN process p ON p.id = r.process_id;

CREATE VIEW v_desconto_disputa AS
SELECT
    s.code AS fonte,
    p.number AS processo_numero,
    r.source_native_id AS lote,
    r.id AS result_id,
    a.id AS award_id,
    COALESCE(a.supplier_name_raw, forn.name_raw) AS fornecedor,
    a.amount_cents / 100.0 AS valor_vencedor,
    prop.n_propostas AS n_propostas,
    prop.media_propostas AS media_propostas,
    prop.max_proposta AS max_proposta,
    CASE WHEN prop.media_propostas > 0
        THEN (prop.media_propostas - a.amount_cents / 100.0) / prop.media_propostas END AS desconto_vs_media,
    CASE WHEN prop.max_proposta > 0
        THEN (prop.max_proposta - a.amount_cents / 100.0) / prop.max_proposta END AS desconto_vs_max
FROM result r
JOIN source s ON s.id = r.source_id
LEFT JOIN process p ON p.id = r.process_id
JOIN (
    SELECT
        r2.id AS result_id,
        COUNT(*) AS n_propostas,
        AVG(CASE WHEN typeof(json_extract(j.value, '$.valor')) IN ('real', 'integer') THEN CAST(json_extract(j.value, '$.valor') AS REAL) END) AS media_propostas,
        MAX(CASE WHEN typeof(json_extract(j.value, '$.valor')) IN ('real', 'integer') THEN CAST(json_extract(j.value, '$.valor') AS REAL) END) AS max_proposta
    FROM result r2, json_each(CASE WHEN json_valid(r2.attrs) THEN r2.attrs ELSE '{}' END, '$.propostas') AS j
    GROUP BY r2.id
) prop ON prop.result_id = r.id
JOIN award a ON a.result_id = r.id
    AND NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id)
LEFT JOIN organization forn ON forn.id = a.supplier_org_id;

CREATE VIEW v_fornecedor_ranking AS
SELECT
    MAX(COALESCE(a.supplier_name_raw, forn.name_raw)) AS fornecedor,
    chave AS fornecedor_cnpj,
    COUNT(*) AS vitorias,
    SUM(a.amount_cents) / 100.0 AS valor_total,
    COUNT(DISTINCT s.code) AS n_fontes,
    GROUP_CONCAT(DISTINCT s.code) AS fontes
FROM (
    SELECT a.*, r.source_id AS rid,
        COALESCE(forn.cnpj, json_extract(a.attrs, '$.supplier_cnpj'), a.supplier_name_raw) AS chave
    FROM award a
    JOIN result r ON r.id = a.result_id
    LEFT JOIN organization forn ON forn.id = a.supplier_org_id
    WHERE NOT EXISTS (SELECT 1 FROM award n WHERE n.supersedes_id = a.id)
) a
JOIN source s ON s.id = a.rid
LEFT JOIN organization forn ON forn.id = a.supplier_org_id
GROUP BY chave;
