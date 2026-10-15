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
| `GET .../dominio/llm/modelos/` | modelos usáveis agora, com as máquinas que os têm (só das organizações da pessoa) |
| `POST .../dominio/llm/chat/` | uma rodada de conversa: `{modelo, mensagens, maquina?}` → `{conteudo, modelo, maquina, tokens, tokens_por_segundo}`; `404` sem máquina com o modelo, `400` entrada inválida, `424` a máquina não respondeu |

Workspace ou objeto sem acesso é **404** (não revela que existe).

**Como duas pessoas entram no mesmo workspace:** as duas precisam ser membros vigentes da **mesma organização** (o papel nela dá o teto). Dono ou administrador da organização adiciona a segunda em **`/membros/`** (portal), pelo nome de usuário, e pode já lhe dar um papel no workspace; a pessoa então o vê em `/agora/`. O papel no workspace também pode ser mudado depois pela API `.../members/` (só o dono do workspace). Quem se cadastra sozinha em `/registro/` ganha uma organização própria e **não** vê o workspace de outra até ser adicionada.

## Chat (ADR 016)

O botão 💬 do topo abre o chat do workspace (o mesmo componente `Chat`, que também pode ser uma aba). Mensagens em tempo real, `@nome` para mencionar (o contador fica de outra cor e o título da aba mostra `(n)`), editar e remover as suas (o dono remove qualquer uma). Quem tem papel de leitura **lê** e não escreve. Sem rede, você lê o histórico e escreve: as mensagens ficam "Pendentes" e saem quando a conexão volta. O chat precisa de um `agora-sync` ligado ao workspace.

## Componentes de domínio (`ia-*`)

| `ia-news` | `entity: Entity` | `selected: Entity`, `results: Entity[]` | Documentos capturados que **mencionam o nome** da entidade (busca textual, `?tipo=documento`). O assunto fica gravado no componente. Escolher o nó de um grafo alimenta este componente (conversão `Node → Entity`). |

## Conversar com os modelos (`ia-llm-chat`, ADR 020)

O componente **`ia-llm-chat`** ("Conversar com um modelo", paleta `intelligence`) deixa a pessoa escolher um modelo instalado no cluster e conversar. É também o jeito de **testar pelo próprio sistema** os modelos instalados em `/cluster/`.

- Lista os modelos de máquinas **das organizações da pessoa** que estão ativas, confirmadas, online (heartbeat recente) e com o modelo visto há ≤ 180 s. Para cada modelo mostra as máquinas e, quando já medido, os tokens/s.
- **Máquina primeiro, depois o modelo**, porque é a máquina que tem os modelos. **Automático** lista todos os modelos do cluster (com "em N máquinas") e deixa o roteador escolher a mais rápida, ou a nunca testada; ao escolher uma máquina, a lista de modelos passa a ter só os dela, com os tokens/s já medidos, para comparar uma máquina com outra. Cada resposta mostra `modelo em máquina · tokens/s`. "Atualizar" busca de novo as máquinas e os modelos.
- O prompt é **só o que a pessoa digita**: nenhum artefato nem conteúdo do workspace é enviado. Fala **só** com o Ollama de máquinas do cluster, nunca com provedor externo.
- **O histórico fica só no navegador** (`sessionStorage`, só nesta aba; some ao fechar) e viaja inteiro a cada rodada. Nada é gravado no servidor nem no documento compartilhado. O servidor audita apenas `llm.chat` (quem, qual modelo e máquina), **sem conteúdo**, e a telemetria de LLM registra a chamada com a finalidade `chat`.
- Sem streaming: a resposta chega inteira (limite `OLLAMA_TIMEOUT_S`, 120 s). Limites de entrada: 50 mensagens, 8 000 caracteres por mensagem, 40 000 no total.
- Sem rede, o componente avisa e devolve a pergunta à caixa; não enfileira nem guarda cópia offline.
- É um componente do **pacote IA**, não o `ultima-chat` (o chat entre pessoas, ADR 016): aquele guarda a autoria vinda do token da pessoa e não tem noção de assistente.

## Offline (ADR 015)

Depois do primeiro acesso, o Agora abre e funciona **sem rede**: o aplicativo (service worker), a lista de workspaces, os documentos e o que você já consultou. Criar, renomear e arquivar offline ficam pendentes e vão ao servidor quando a rede volta (o `id` do workspace é gerado no navegador, então o mesmo id chega ao servidor). O que fica guardado de dados do domínio é **escolha de cada pessoa** em ⚙ Configurações → Dados neste dispositivo (até o teto `AGORA_OFFLINE_CACHE_NIVEL` da instância); itens acima do nível **nunca** são gravados e a interface diz quantos ficaram de fora. As mesmas Configurações têm **Sair** (com a opção de limpar o dispositivo), **Limpar este dispositivo** e o tema.

## Operação

- Implantação (profile, segredos, Caddy, volume e backup): seção "Ultima Agora" do [`deploy.md`](../../deploy.md).
- Sem `AGORA_SYNC_URL` o Agora funciona, mas cada workspace fica só no navegador. Com ele e o profile `agora`: colaboração em tempo real.
- Atualizar o Agora: `scripts/sincronizar_agora.sh [caminho-do-ultima-agora]` e commitar o resultado.
- Testes: `scripts/testar_agora_ia.sh` (front, Chrome headless) e `pytest tests/test_agora_*.py` (backend).
