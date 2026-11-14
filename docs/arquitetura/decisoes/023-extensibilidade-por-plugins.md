# ADR 023 — Extensibilidade por plugins

**Status:** aceita — 2026-11-14. **Fases 0 a 2 implementadas.** Fases 3 e 4 desenhadas (abaixo).

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

## Fases seguintes (cada uma validada por um plugin de exemplo real)

- **3. UI:** descoberta de packs do Agora a partir das extensões (hoje `IA_MANIFESTS` é uma lista fixa em
  `static/agora-ia/pack/pack.js`), rota de domínio por extensão sem o núcleo citá-la, precache que inclui
  os estáticos de extensão. Caso: componente de mapa.
- **4. MCP e agentes:** registro de ferramentas com schema, `MCP_URL` no orchestrator e contrato de
  chamada com a política. Caso: ferramenta de consulta.

## Consequências

- Uma extensão externa precisa, além do manifesto, de templates e estáticos próprios (os do núcleo ficam em
  `templates/<label>/`); migrations rodam com o `migrate` global, no mesmo banco.
- Mudar `EXTENSOES` exige reiniciar portal, workers e beat (todos carregam os mesmos apps).
- Não há versão de extensão para compatibilidade além de `requer_api`; sem sandbox, uma extensão com defeito
  derruba o que o núcleo isola só por `try/except` (consumidores).
