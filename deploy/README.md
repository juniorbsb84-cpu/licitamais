# Kit de Deploy LicitamAIs em VPS Linux (Ubuntu 24.04 LTS)

Guia completo passo a passo para deploy em VPS Linux modesta (**2 GB RAM, 20 GB de disco**).

---

## 1. Topologia e Arquitetura

O sistema opera com:
1. **Coleta / Scheduler (CLI Python)**:
   - Disparo diário às 12:00 via `systemd timer` (`licitamais-coleta.timer` + `licitamais-coleta.service`).
   - Executa `python -m licitamais --trigger cron --db $LICITAMAIS_DB --backup-dir $LICITAMAIS_BACKUP_DIR`.
2. **Servidor Web**:
   - Rodando em porta interna `8090` gerenciado por `systemd` (`licitamais-web.service`).
   - Executa `python -m licitamais.api --db $LICITAMAIS_DB --porta 8090 --static /opt/licitamais/frontend/dist` (API v2 + site React; gerar antes com `cd frontend && npm ci && npm run build`).
3. **Proxy Reverso com HTTPS Automático**:
   - **Caddy Server** escutando portas 80/443, emitindo certificados Let's Encrypt automaticamente e repassando para `127.0.0.1:8090`.
4. **Rotina de Backup**:
   - Script `backup.sh` com `sqlite3 .backup` consistente (online), rotação de 7 dias e suporte a cópia remota (ex: rclone / S3 / Google Drive / B2).

---

## 2. Preparação do Servidor (Ubuntu 24.04)

### 2.1. Atualização do Sistema e Pacotes Base

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip sqlite3 curl git ufw fail2ban rclone
```

### 2.2. Criação do Usuário de Serviço

Nunca rode a aplicação como `root`. Crie um usuário dedicado `licitamais`:

```bash
sudo useradd -m -s /bin/bash licitamais
sudo usermod -aG licitamais licitamais
```

### 2.3. Swap de 2 GB (Essencial para VPS de 2 GB RAM)

Para evitar quedas por Out-Of-Memory (OOM) em picos de requisições ou coletas pesadas:

```bash
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

---

## 3. Instalação da Aplicação em `/opt/licitamais`

### 3.1. Clone ou Cópia do Código

```bash
sudo mkdir -p /opt/licitamais /opt/licitamais/data /opt/licitamais/data/backups
sudo chown -R licitamais:licitamais /opt/licitamais

# Como usuario licitamais (ou copie os arquivos do repositorio):
sudo -u licitamais git clone <REPO_URL> /opt/licitamais
cd /opt/licitamais
```

### 3.2. Configuração do Virtualenv

```bash
sudo -u licitamais python3.12 -m venv /opt/licitamais/.venv
sudo -u licitamais /opt/licitamais/.venv/bin/pip install --upgrade pip
sudo -u licitamais /opt/licitamais/.venv/bin/pip install -r /opt/licitamais/requirements.lock
```

### 3.3. Configuração do Arquivo `.env` (Permissões 600)

Crie o arquivo `/opt/licitamais/.env` com permissão estrita:

```bash
sudo -u licitamais cp deploy/.env.example /opt/licitamais/.env
chmod 600 /opt/licitamais/.env
```

Edite o arquivo `/opt/licitamais/.env`:
```ini
LICITAMAIS_DB=/opt/licitamais/data/licitamais.db
LICITAMAIS_BACKUP_DIR=/opt/licitamais/data/backups
LICITAMAIS_TELEGRAM_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyz
LICITAMAIS_TELEGRAM_OPERADOR=123456789
LICITAMAIS_KEEP_DAYS=7
# Opcional para rclone externo:
# LICITAMAIS_RCLONE_DEST=meudrive:licitamais-backups
```

> **IMPORTANTE**: O arquivo `.env` nunca deve ser commitado no repositório.

### 3.4. Inicialização do Banco de Dados

Se estiver subindo um banco novo (sem transferir `licitamais.db` já populado):

```bash
sudo -u licitamais PYTHONPATH=/opt/licitamais/src \
  /opt/licitamais/.venv/bin/python -m licitamais --init --db /opt/licitamais/data/licitamais.db
```

---

## 4. Configuração dos Serviços Systemd

Copie os arquivos de unidade para o diretório de serviços do systemd:

```bash
sudo cp /opt/licitamais/deploy/licitamais-coleta.service /etc/systemd/system/
sudo cp /opt/licitamais/deploy/licitamais-coleta.timer /etc/systemd/system/
sudo cp /opt/licitamais/deploy/licitamais-web.service /etc/systemd/system/

sudo systemctl daemon-reload
```

### 4.1. Ativar o Agendador de Coleta (diário, 12:00)

```bash
sudo systemctl enable --now licitamais-coleta.timer

# Para conferir o status e proximo disparo:
systemctl status licitamais-coleta.timer
systemctl list-timers --all | grep licitamais
```

Para disparar uma coleta de teste manualmente via systemd:
```bash
sudo systemctl start licitamais-coleta.service
journalctl -u licitamais-coleta.service -f
```

### 4.2. Ativar o Servidor Web

```bash
sudo systemctl enable --now licitamais-web.service

# Conferir status e logs:
systemctl status licitamais-web.service
journalctl -u licitamais-web.service -f
```

---

## 5. Configuração do Caddy (HTTPS Automático)

### 5.1. Instalação do Caddy no Ubuntu 24.04

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

### 5.2. Aplicar o Caddyfile

Substitua `licitamais.seudominio.com.br` no arquivo `deploy/Caddyfile` pelo seu domínio real configurado no DNS para o IP da sua VPS:

```bash
sudo cp /opt/licitamais/deploy/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

---

## 6. Rotina de Backup Diário

O script `/opt/licitamais/deploy/backup.sh` realiza `sqlite3 .backup` consistente online (não bloqueia leitura/escrita e não corrompe WAL), remove backups com mais de 7 dias e opcionalmente sincroniza com rclone.

Tornar executável:
```bash
chmod +x /opt/licitamais/deploy/backup.sh
```

### 6.1. Configurar Crontab Diário (03:00 da manhã)

Como usuário `licitamais` (`crontab -e -u licitamais`):
```cron
0 3 * * * /opt/licitamais/deploy/backup.sh >> /opt/licitamais/data/backups/backup.log 2>&1
```

### 6.2. Testar o Backup Manualmente

```bash
sudo -u licitamais /opt/licitamais/deploy/backup.sh
ls -lh /opt/licitamais/data/backups/
```

---

## 7. Firewall (UFW)

Para segurança da VPS:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow ssh
sudo ufw allow http
sudo ufw allow https
sudo ufw enable
```

A porta `8090` fica acessível apenas localmente via loopback `127.0.0.1`, protegida pelo Caddy.

