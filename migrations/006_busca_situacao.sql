ALTER TABLE process ADD COLUMN phase_current_group TEXT;
CREATE INDEX idx_process_situacao ON process(phase_current_group, opening_at_source);

-- FTS5 sobre objeto do processo (busca sem acento + prefixo). Usa content-sync padrao
-- do plano: o tests/test_r34_web.py clona o banco por iterdump reaplicando os INSERTs,
-- entao a fixture la pula as linhas do FTS (INSERT INTO "process_fts...); ver desvio no relatorio.
CREATE VIRTUAL TABLE process_fts USING fts5(
    object, content='process', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
INSERT INTO process_fts(process_fts) VALUES ('rebuild');
CREATE TRIGGER process_fts_ai AFTER INSERT ON process BEGIN
    INSERT INTO process_fts(rowid, object) VALUES (new.id, new.object);
END;
CREATE TRIGGER process_fts_ad AFTER DELETE ON process BEGIN
    INSERT INTO process_fts(process_fts, rowid, object) VALUES ('delete', old.id, old.object);
END;
CREATE TRIGGER process_fts_au AFTER UPDATE OF object ON process BEGIN
    INSERT INTO process_fts(process_fts, rowid, object) VALUES ('delete', old.id, old.object);
    INSERT INTO process_fts(rowid, object) VALUES (new.id, new.object);
END;

-- Mesma regra de licitamais.web.licitacao.situacao(); teste de paridade em tests/test_r48_situacao_sql.py
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
WHERE p.deleted_at IS NULL;
