CREATE INDEX IF NOT EXISTS idx_contract_supersedes ON contract(supersedes_id);
CREATE INDEX IF NOT EXISTS idx_contract_process ON contract(process_id);
CREATE INDEX IF NOT EXISTS idx_award_supersedes ON award(supersedes_id);
CREATE INDEX IF NOT EXISTS idx_award_process ON award(process_id);

DROP VIEW v_licitacao;

-- Mesma regra de licitamais.web.licitacao.situacao(); teste de paridade em tests/test_r48_situacao_sql.py.
-- Exclui pseudo-processos do SENAC criados so para pendurar contrato de parceria
-- (source_native_id contem ':sem-origem:'): nao sao licitacoes.
CREATE VIEW v_licitacao AS
SELECT p.id, s.code AS fonte, p.number AS numero, p.object AS objeto, p.modality_raw AS modalidade,
       p.opening_at_source AS abertura, p.published_at_source AS publicacao, p.phase_current_label AS rotulo,
       o.id AS orgao_id, o.name_raw AS orgao,
       CASE
         WHEN p.phase_current_group IN ('cancelada', 'suspensa', 'encerrada') THEN p.phase_current_group
         WHEN p.phase_current_group = 'andamento' THEN
              CASE WHEN substr(p.opening_at_source, 1, 10) >= date('now') THEN 'aberta' ELSE 'andamento' END
         WHEN p.phase_current_group = 'aberta' THEN
              CASE WHEN substr(p.opening_at_source, 1, 10) < date('now') THEN 'andamento' ELSE 'aberta' END
         WHEN p.opening_at_source IS NULL THEN 'desconhecida'
         WHEN substr(p.opening_at_source, 1, 10) >= date('now') THEN 'aberta'
         ELSE 'encerrada'
       END AS situacao
FROM process p
JOIN source s ON s.id = p.source_id
LEFT JOIN organization o ON o.id = p.org_id
WHERE p.deleted_at IS NULL
  AND p.source_native_id NOT LIKE '%:sem-origem:%';
