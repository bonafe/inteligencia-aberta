# Decisão-006: Implantação por instância, HTTPS em todos os hosts e exposição mínima

**Status:** Aceito  
**Data:** 2026-10-03

## Contexto

O sistema passa a ser implantado em uma rede própria ("rede_papagaio": cinco hosts numa tailnet, de sistemas diferentes), por automação (Ansible, em outro repositório): uma instância independente por host, cada uma com seus segredos. No futuro as instâncias conversarão entre si e compartilharão armazenamento e GPU, então o deploy precisa ser previsível e parametrizável. Um dos hosts tem IP público e domínio e deve ser usado também pela internet, inclusive para capturar páginas com a extensão fora da VPN.

O repositório só tinha o modo de desenvolvimento: `docker compose up` carregava o override com `runserver` e `--reload`, as portas ficavam em `0.0.0.0`, `production.py` forçava HTTPS sem proxy (loop de redirect em HTTP), nenhum serviço tinha healthcheck ou restart, o registro público nunca fechava e segredos vazios ou de exemplo subiam sem aviso.

## Decisão

1. **Produção é um arquivo de compose explícito** (`docker-compose.prod.yml`, usado com `-f` sobre o base, sem o override de dev), com `restart`, healthchecks e `depends_on` em `service_healthy`. Portas só do portal e do orchestrator, presas a `BIND_ADDR` (padrão `127.0.0.1`); MCP, bancos e MinIO ficam internos.
2. **HTTPS em todos os hosts.** Nos hosts só-tailnet, `tailscale serve` termina o TLS na frente do portal. O tráfego da tailnet já é cifrado (WireGuard), mas o HTTPS dá defesa em profundidade e é necessário para `wss://` e cookies seguros; custa pouco com certificado automático. `TLS_MODE=proxy` é o padrão; `none` é exceção consciente. Um flag único substitui cinco flags soltos.
3. **Host público: Caddy** (profile `publico`, domínio próprio, Let's Encrypt), publicando **apenas** o portal e `/api/v1/capture/*`. Os canais serviço-a-serviço (`X-Internal-Token`) são bloqueados no proxy; o orchestrator inteiro e o MCP nunca saem.
4. **Registro público fecha após o primeiro usuário**, e em produção o dono nasce de um `bootstrap_instancia` idempotente (serviço one-shot do compose) antes de qualquer porta abrir. Isso elimina a corrida do "primeiro cadastro vira superusuário".
5. **Segredos obrigatórios são validados na subida**: vazio ou `CHANGE_ME` impede o serviço de iniciar, listando tudo de uma vez.
6. **Cada instância se identifica** em `/health` (`INSTANCIA_NOME`, `IA_VERSION`), base para a futura comunicação entre instâncias.
7. **A extensão aceita a URL da instância**, em vez de `localhost` fixo.

## Alternativas consideradas

- **HTTP puro na tailnet, HTTPS só no host público.** Mais simples hoje, mas deixa dois modos de operação como regra e cookies sem `Secure`. Rejeitado: `tailscale serve` torna o HTTPS quase gratuito.
- **Tailscale Funnel no host público.** Dispensa domínio e Caddy, mas o nome é o do `*.ts.net` e só aceita certas portas. Rejeitado porque há domínio próprio.
- **Compose base "seguro por padrão" no lugar de um arquivo de produção.** Esconderia a diferença dev/prod; o arquivo explícito deixa claro o que muda.
- **Publicar imagens em registry (CI) já agora.** Não há CI e `build:` por host é aceitável para cinco hosts; adiado.

## Consequências

- Existe um único comando de produção, e usar `docker compose up` sem `-f` num servidor sobe a configuração de desenvolvimento — o `CLAUDE.md` e o `docs/deploy.md` insistem nisso.
- Segredos mudam por host e `POSTGRES_PASSWORD`/`MINIO_ROOT_PASSWORD` só valem na criação dos dados.
- Ficam pendentes: backup/restore, imagens em registry, rate limit na borda, armazenamento S3 e Postgres externos, reserva de GPU, healthcheck de worker e beat.

Ver [`../../deploy.md`](../../deploy.md).
