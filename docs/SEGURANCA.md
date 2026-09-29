# Segurança

Controles que o código aplica e que qualquer mudança precisa manter.

## Controles

| Área | Como funciona |
|---|---|
| **Isolamento por conta** | Dados de licitação são públicos. `alert_subscription`, `session`, `login_token`, `telegram_link` e `account` são sempre filtrados pelo `account_id` da sessão. |
| **Autorização no servidor** | `/operador` e ações administrativas validam `LICITAMAIS_OPERADORES` no servidor. Toda rota que recebe id confere que o objeto pertence à conta (sem IDOR). |
| **Segredos** | Nada em código, configuração ou histórico. Token de login nunca vai ao log fora do modo dev. A chave HMAC do vínculo com o Telegram fica ao lado do banco (`*.telegram-link.key`), fora do SQLite e do Git. Erros de rede do Telegram registram só o tipo da exceção, porque a mensagem traz a URL com o token do bot. |
| **Login** | Link mágico: `token_urlsafe(32)`, só o hash SHA-256 é guardado, validade de 15 min. O GET mostra a confirmação sem consumir o token; o POST consome uma única vez (`BEGIN IMMEDIATE`). Sessão de 30 dias em cookie `HttpOnly`, `SameSite=Lax`, `Secure` sob HTTPS; CSRF nas mutações. Resposta idêntica para e-mail existente e inexistente. |
| **Limites** | Pedido de link: 5 por e-mail/h e 20 por IP/h. Vínculo Telegram: 5 erros por chat/h. Consultas pesadas: 60/min por conta; visitantes 30/min por IP e 600/min no total. POST acima de 16 KB → 413 antes de ler o corpo. |
| **Cabeçalhos HTTP** | `Content-Security-Policy` sem script inline, `Strict-Transport-Security`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin`. |
| **XSS** | Dado de fonte externa (objeto, fornecedor) é tratado como não confiável e sempre escapado. Links de origem só `http(s)`. |
| **Coleta** | TLS sempre verificado (a cadeia intermediária que um portal não envia vem em `src/licitamais/certs/`). Limite de tamanho de resposta; payload bruto nunca renderizado sem escape. |
| **Dependências** | Versões exatas em `requirements.lock`; rodar `pip-audit` antes de publicar. |

Os limites de requisição valem por processo. Com mais de um processo ou máquina, o limite global precisa ir para o proxy.

## Modo aberto

Desligado por padrão: sem estas variáveis o site exige login em todas as telas.

| Variável | Efeito |
|---|---|
| `LICITAMAIS_ABERTO=1` | Visitante sem conta lê resumo, licitações, detalhe, evidências e preços. Alertas, conta, operador e toda escrita continuam exigindo sessão e CSRF. |
| `LICITAMAIS_FONTES_OCULTAS=iges` | As fontes listadas somem de todas as telas e da API (detalhe responde 404). Use para fontes cujos termos vedam uso comercial. |
| `LICITAMAIS_CONFIA_CLOUDFLARE=1` | O limite de visitantes usa o IP de `CF-Connecting-IP`. Ligue **somente** atrás do Cloudflare Tunnel; fora dele o cabeçalho pode ser forjado. |

## Antes de publicar

- [ ] Endpoints com id validam o dono; listagens de conta filtram pela sessão
- [ ] Nenhum segredo no repositório; o que vazou foi revogado, não só apagado
- [ ] Cabeçalhos presentes na resposta real (`curl -I`)
- [ ] SMTP configurado (modo dev do e-mail desligado)
- [ ] `pip-audit` sem advisory aplicável
- [ ] Backup criptografado e restauração testada
