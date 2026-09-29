# Migracoes SQLite

Este diretorio e a unica fonte de verdade do schema. O banco novo e o banco
existente percorrem os mesmos arquivos de migracao.

A politica e **additive-only** (aditiva):

- cada arquivo segue `NNN_descricao.sql`, sem versoes duplicadas ou lacunas;
- cada migracao e aplicada em uma transacao propria;
- a versao fica registrada em `PRAGMA user_version` e `schema_migration`;
- novas colunas devem ser anulaveis ou ter `DEFAULT`;
- estrutura ou dados existentes nunca sao removidos;
- campos aposentados recebem sufixo `_deprecated`;
- fatos monetarios e eventos de fase sao append-only; correcao cria nova linha
  apontando para `supersedes_id`.
