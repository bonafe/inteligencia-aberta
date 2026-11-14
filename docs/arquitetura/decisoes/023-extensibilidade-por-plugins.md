# ADR 023 — Extensibilidade por plugins

**Status:** aceita — 2026-11-14. **Fases 0 a 4 implementadas.** Camada 2 (plugin não confiável, isolado) fora desta rodada.

## Contexto

O ADR 022 tornou os domínios extensões, mas só quem edita o repositório consegue instalar uma:
`INSTALLED_APPS` é uma lista fixa em `config/settings/base.py` e o manifesto cobre só tipos de dado,
vocabulário, menu, rotas e consumidores de schema. Quem implanta o IA precisa poder estendê-lo com
autonomia — tratar novos tipos de dado, fontes de ingestão, etapas de processamento, ferramentas, telas —
sem alterar o núcleo. O levantamento (2026-11-14) achou as lacunas: instalação, política de LLM externo
só por convenção, ausência de registro de etapas e de fontes, enums fechados, MCP/orquestrador sem
registro, o pack do front com lista fixa, filas e agendamento centrais, e nenhum isolamento.

## Decisão

**Modelo de confiança: só código confiável.** Quem administra a instância escolhe o que instalar; a
extensão roda no processo do portal, com acesso total, como o código do próprio projeto. Isolamento
de processo para código não confiável (ferramentas e agentes como serviços sobre o canal assinado da
federação) fica fora desta rodada; o manifesto não fecha a porta para ele.

1. **A política de LLM externo é do ponto único de chamada.** `llm_common.gerar_texto` exige a
   `classificacao` do dado e recusa provider externo para `restrito`/`confidencial` antes de tocar em
   chave ou rede (`PoliticaLLMNegada`). Antes a barreira estava só nas views e tasks, e um plugin que
   chamasse `gerar_texto` direto a contornava. O `policy_engine` não muda.
2. **Instalar é escolher, não editar.** A variável `EXTENSOES` lista o que a instância liga além do
   repositório: o nome de um *entry point* do grupo `inteligencia_aberta.extensoes` (pacote instalado com
   `pip`) ou o caminho pontuado de uma `AppConfig` que já está no `PYTHONPATH` (pasta montada em volume).
   Estar instalado não liga: só o que o administrador nomeia entra em `INSTALLED_APPS`
   (`config/extensoes_externas.py`, lido antes de o Django carregar os apps).
3. **Manifesto v2.** `Extensao` ganha `requer_api` (mínimo de `API_EXTENSOES` de que a extensão precisa) e
   `dependencias` (nomes de outras extensões). O registro recusa, na subida e com a causa dita, extensão
   que exige API mais nova que o núcleo, que depende de extensão ausente ou que repete o nome de outra.
4. **O núcleo continua sem citar extensão** (ADR 022, regra 1), agora também para pacotes de fora de
   `apps/`: `tests/test_extensoes_externas.py` usa `services/portal/exemplos/ia_exemplo`, a menor extensão
   que cumpre o contrato, e confere que nenhum arquivo do núcleo a menciona.

5. **Fontes, etapas e agendamentos (API 3).** O manifesto declara:
   - `FonteIngestao(chave, rotulo, descricao, importar)`: `manage.py importar_fonte <chave> <caminho> --organizacao …`
     chama `importar(*, organizacao, usuario, caminho, nivel, simular, opcoes) -> dict`, que cria artefatos pelos serviços
     públicos do núcleo (`artifacts.acervo`, `registrar_alegacao`);
   - `EtapaProcessamento(chave, rotulo, executar, tipos, categorias, sufixos, fila)`: o núcleo enfileira a etapa, depois do
     commit, para cada artefato **novo** que combina com o filtro, na `fila` declarada (vazia = a padrão), e a executa dentro de
     `etapa()`: um evento `extensao.etapa.<chave>` por execução (`ok`/`vazio`/`ignorado`/`falhou`), com a correlação do artefato;
     exceção vira `falhou` e não derruba a captura nem as outras etapas. A etapa tem de ser idempotente:
     `manage.py rodar_etapa <chave> --organizacao …` a reexecuta para os artefatos existentes;
   - `Agendamento(chave, tarefa, a_cada_s)`: acrescentado ao Beat no `beat_init` (`config/celery.py`), quando o registro já pode ser lido.

   Toda chave começa pelo `nome` da extensão (`email.pasta`) e é única entre extensões. O que a etapa descobre vai para
   `content["extensoes"][<nome>]` (`apps.extensoes.dados`), um espaço por extensão; fato vira alegação. Não há tipo de artefato
   novo: o e-mail e o áudio são `arquivo` com `content["categoria"]`. **Fila própria precisa de worker próprio** (`celery -Q <fila>`):
   quem implanta o sobe (hoje o compose do projeto só traz as filas do núcleo). Validado por `exemplos/ia_email` (fonte + etapa por
   sufixo) e `exemplos/ia_transcricao` (etapa em fila `gpu` + varredura agendada).

6. **Componentes e rotas de leitura para o Agora (API 4).** O manifesto declara:
   - `PackUI(base, manifestos)`: a pasta estática da própria extensão (`<ext>/static/<base>`) com componentes `ia-*`. O `AppView` põe
     as URLs dos manifestos em `configuracao.packs`; o `boot.js` as registra (`pack/extensions.js`) depois do pack do projeto, e uma
     extensão com defeito vai para o Console sem derrubar o Agora. Sem rede vale a configuração que o `boot.js` já lembra, packs incluídos;
   - `dominio_urls`: módulo de rotas montado em `/agora/api/v1/dominio/<nome>/`, que o componente lê com `this.domain.get('/<nome>/…')`
     (mesmo cliente, mesmo cache offline e mesma classificação dos componentes do projeto). Isolar por organização é da view:
     `apps.extensoes.dominio.artefatos_visiveis(request)` é o ponto de partida.

   O precache offline passou a ser `/agora/precache.json` (aberto como o `sw.js`: só nomes de estáticos): a lista gerada do app mais a
   pasta de cada pack instalado, com a versão recalculada, então instalar ou mudar uma extensão troca o cache do service worker sozinho,
   sem regenerar nada à mão. O `precache.json` estático e seu `--check` seguem valendo para o app. Os módulos de um componente importam o
   Agora por caminho absoluto (`/static/agora/core/…`). Validado por `exemplos/ia_mapa` (componente `ia-map` + rota `mapa/pontos/`);
   os caminhos de um pack são relativos, sem `..`, e não podem cair em `agora/` ou `agora-ia/`.

7. **Ferramentas do MCP e agentes.** O MCP deixou de ter rotas escritas à mão (`/tools/*`): um registro (`services/mcp/ferramentas.py`)
   monta o catálogo com as ferramentas do projeto (`tools/`) e as de plugins listados em `FERRAMENTAS_MCP` (nome de *entry point* do
   grupo `inteligencia_aberta.ferramentas` ou módulo importável que expõe `FERRAMENTAS`). Uma ferramenta é um dicionário — o plugin não importa
   nada do serviço: `id`, `descricao`, `parametros` (tipo e obrigatoriedade), `executar` (async), `classificacao_maxima`, `fonte`, `timeout_s`.
   API: `GET /ferramentas` (catálogo com esquema) e `POST /ferramentas/<id>` com `{parametros, contexto}`, no contrato de
   `docs/componentes/mcp.md` (`ok` com `resultado`/`fonte`/`confianca`, ou `erro` com `codigo`: `FERRAMENTA_DESCONHECIDA`, `PARAMETRO_INVALIDO`,
   `BLOQUEADO_POR_POLITICA`, `TIMEOUT`, `FONTE_INDISPONIVEL`). Contrato inválido ou id repetido falha **na subida**.
   **Política em duas camadas:** o orchestrator chama `policy_engine.check` antes de sair (decisão no log; o motor não mudou) e o MCP recusa
   dado mais restrito que a `classificacao_maxima` da ferramenta, qualquer que seja o chamador; sem classificação o dado vale `confidencial`.
   Ferramenta que envia parâmetros para fora declara `publico`.
   O orchestrator ganhou `mcp_cliente.py` (`MCP_URL`; nunca levanta, devolve sempre o contrato), o **coletor** passou a executar o plano
   (uma entrada por etapa, na ordem, com o desfecho; falha de uma fonte não para as outras; concorrência limitada por `COLETOR_CONCORRENCIA`)
   e `GET /api/v1/ferramentas` / `POST /api/v1/ferramentas/<id>` (JWT, identidade das claims, padrão `restrito`). Ainda não há agente novo
   plugável: o planejador e o redator seguem stubs, e `/investigar` ainda não executa o grafo. Validado por `services/mcp/exemplos/ia_cep`
   (uma ferramenta externa e uma local) e pelos testes de `services/mcp/tests` e `services/orchestrator/tests`.

## Consequências

- Uma extensão externa precisa, além do manifesto, de templates e estáticos próprios (os do núcleo ficam em
  `templates/<label>/`); migrations rodam com o `migrate` global, no mesmo banco.
- Mudar `EXTENSOES` exige reiniciar portal, workers e beat (todos carregam os mesmos apps).
- Não há versão de extensão para compatibilidade além de `requer_api`; sem sandbox, uma extensão com defeito
  derruba o que o núcleo isola só por `try/except` (consumidores).
