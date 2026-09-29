#!/usr/bin/env bash
# ==============================================================================
# LicitamAIs - Script de Backup Diario do SQLite com Rotacao e Copia Externa
# ==============================================================================
set -euo pipefail

# Carrega variaveis do ambiente se existir arquivo .env
ENV_FILE="${ENV_FILE:-/opt/licitamais/.env}"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a
    source "$ENV_FILE"
    set +a
fi

DB_PATH="${LICITAMAIS_DB:-/opt/licitamais/data/licitamais.db}"
BACKUP_DIR="${LICITAMAIS_BACKUP_DIR:-/opt/licitamais/data/backups}"
KEEP_DAYS="${LICITAMAIS_KEEP_DAYS:-7}"
RCLONE_DEST="${LICITAMAIS_RCLONE_DEST:-}"

DATA_HOJE="$(date +%Y%m%d)"
BACKUP_FILE="${BACKUP_DIR}/backup-${DATA_HOJE}.db"

echo "[$(date -Iseconds)] [BACKUP] Iniciando rotina de backup..."

if [ ! -f "$DB_PATH" ]; then
    echo "[$(date -Iseconds)] [ERRO] Banco SQLite nao encontrado em: $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"

# 1. Backup consistente usando sqlite3 .backup (online, sem corromper transacoes concorrentes)
echo "[$(date -Iseconds)] [BACKUP] Executando 'sqlite3 $DB_PATH \".backup $BACKUP_FILE\"'..."
sqlite3 "$DB_PATH" ".backup '${BACKUP_FILE}'"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "[$(date -Iseconds)] [ERRO] Falha ao gerar arquivo de backup: $BACKUP_FILE" >&2
    exit 2
fi

BACKUP_SIZE="$(du -h "$BACKUP_FILE" | cut -f1)"
echo "[$(date -Iseconds)] [BACKUP] Backup gerado com sucesso: $BACKUP_FILE ($BACKUP_SIZE)"

# 2. Rotacao: mantem os ultimos N dias (padrao 7)
echo "[$(date -Iseconds)] [ROTACAO] Aplicando retencao de $KEEP_DAYS dias em $BACKUP_DIR..."
# Lista backups ordenados por nome descrescente, pula os primeiros $KEEP_DAYS e apaga os excedentes
ARQUIVOS_PARA_APAGAR="$(find "$BACKUP_DIR" -maxdepth 1 -name "backup-*.db" | sort -r | tail -n +$((KEEP_DAYS + 1)))"

if [ -n "$ARQUIVOS_PARA_APAGAR" ]; then
    while IFS= read -r arquivo; do
        if [ -f "$arquivo" ]; then
            echo "[$(date -Iseconds)] [ROTACAO] Removendo backup antigo: $arquivo"
            rm -f "$arquivo"
        fi
    done <<< "$ARQUIVOS_PARA_APAGAR"
else
    echo "[$(date -Iseconds)] [ROTACAO] Nenhum backup antigo para expirar (total <= $KEEP_DAYS)."
fi

# 3. Copia externa opcional via rclone
if [ -n "$RCLONE_DEST" ]; then
    if command -v rclone &> /dev/null; then
        echo "[$(date -Iseconds)] [RCLONE] Copiando backup para destino externo: $RCLONE_DEST..."
        if rclone copy "$BACKUP_FILE" "$RCLONE_DEST"; then
            echo "[$(date -Iseconds)] [RCLONE] Copia externa concluida com sucesso."
        else
            echo "[$(date -Iseconds)] [RCLONE] [AVISO] Falha ao enviar para $RCLONE_DEST via rclone." >&2
        fi
    else
        echo "[$(date -Iseconds)] [RCLONE] [AVISO] Destino '$RCLONE_DEST' configurado, mas binario 'rclone' nao esta instalado." >&2
    fi
else
    echo "[$(date -Iseconds)] [RCLONE] Destino externo nao configurado (LICITAMAIS_RCLONE_DEST vazio). Ignorando envio remoto."
fi

echo "[$(date -Iseconds)] [BACKUP] Rotina finalizada com sucesso."
exit 0
