# Interface: Ultima Agora (workspaces colaborativos)

Em `/agora/` (menu "Investigação (Agora)"). Especificação do produto: projeto `ultima-agora`, `docs/especificacao/`. Decisões: ADR 013 e 014.

## O que o usuário faz

1. Abre um workspace (ou cria um) e **adiciona componentes** pela paleta: `Busca de entidades`, `Entidade`, `Tabela`, `Grafo`, `Notas`, `Texto`.
2. **Liga** um componente a outro pelo inspetor (Conexões) ou deixa o Agora criar a ligação: em `Entidade`, "Mostrar relações" cria um `Grafo` ao lado, já conectado, num passo desfazível.
3. Trabalha com outras pessoas no mesmo workspace: edição simultânea, presença (quem está em qual aba), papéis.

## Componentes do pacote `ia-*`

| Componente | Entradas | Saídas | Observações |
|---|---|---|---|
| `ia-search` | — | `results: Entity[]`, `selected: Entity` | Busca textual nos artefatos **da organização do usuário**. Consulta vazia lista os mais recentes. |
| `ia-entity` | `entity: Entity` | `entity`, `relations: Graph` | Mostra tipo, nível, o que as fontes **dizem** (alegações com produtor e confiança — não são fatos) e as fontes. A entidade escolhida fica **gravada** no componente, para todos verem. Ação: `showRelations`. |

`Entity` é um DTO mínimo (`id`, `label`, `kind`, `classification`). Conversores: `Entity → String`, `Entity[] → Table`, `Row → Entity`.

## API (sessão + CSRF)

| Rota | Função |
|---|---|
| `GET/POST /agora/api/v1/workspaces/` | lista (com o papel de quem pede) / cria |
| `GET/PATCH /agora/api/v1/workspaces/<id>/` | metadados / título, classificação, espaço (só o dono) |
| `POST .../archive/` | arquiva (dono) |
| `POST .../token/` | token curto para o `agora-sync`; `503` se a colaboração não está configurada |
| `GET/POST/PATCH/DELETE .../members/` | papéis explícitos (dono); o papel efetivo respeita o teto da organização |
| `GET /agora/api/v1/dominio/artefatos/?q=&limite=&workspace=` | busca (≤ 50) |
| `GET .../dominio/artefatos/<id>/` | detalhe com alegações e fontes |
| `GET .../dominio/artefatos/<id>/relacoes/` | vizinhança como `Graph` (linhagem + `vinculos`/`enderecos`) |

Workspace ou objeto sem acesso é **404** (não revela que existe).

## Offline (ADR 015)

Depois do primeiro acesso, o Agora abre e funciona **sem rede**: o aplicativo (service worker), a lista de workspaces, os documentos e o que você já consultou. Criar, renomear e arquivar offline ficam pendentes e vão ao servidor quando a rede volta (o `id` do workspace é gerado no navegador, então o mesmo id chega ao servidor). O que fica guardado de dados do domínio depende de `AGORA_OFFLINE_CACHE_NIVEL` (padrão `interno`); itens acima do nível **nunca** são gravados e a interface diz quantos ficaram de fora.

## Operação

- Sem `AGORA_SYNC_URL` o Agora funciona, mas cada workspace fica só no navegador. Com ele e o profile `agora`: colaboração em tempo real.
- Atualizar o Agora: `scripts/sincronizar_agora.sh [caminho-do-ultima-agora]` e commitar o resultado.
- Testes: `scripts/testar_agora_ia.sh` (front, Chrome headless) e `pytest tests/test_agora_*.py` (backend).
