# ADR 012 — Controle de instâncias entre pares: canal assinado, `Par` e modelos do Ollama

**Status:** aceito — 2026-10-04. **Implementado** (marcos A a F do plano de controle das instâncias): `Par`, enrolamento, canal assinado, pull de estado, operações de modelo do Ollama e a tela. **Não exercitado ainda com duas instâncias reais**, só com a outra ponta simulada em teste.

## Contexto

O dono quer **ver as máquinas e suas capacidades** e **listar, instalar e remover os modelos do Ollama** delas pelos painéis do Inteligência Aberta — com o Ollama **nativo** (Macs) ou em **container** — e, quando as instâncias são do mesmo dono, fazer isso **a partir de qualquer uma** sobre as outras. Pontos de partida, verificados no código:

- a tela do cluster era somente leitura e o cliente do Ollama não tinha instalar, remover nem `ps`;
- cada instância tem **banco próprio** (a topologia `compute` saiu): os dados das **outras** máquinas nunca chegavam ao banco local, então nem a tela nem o roteador de LLM enxergavam os pares;
- o gateway entre nós só fazia `POST /v1/chat/completions` com um segredo **único do cluster**, sem identidade de quem pede;
- a infra de assinatura (`ChaveInstancia`, Ed25519) existia, mas nenhuma rota a verificava; o `Par` e o enrolamento (ADR 011) não existiam;
- nenhuma tela exigia papel de usuário; o portal só isolava por organização.

## Decisão

**1. `Maquina` é o `Par`** (sem renomear): `did`, `tipo` (próprio/terceiro), `estado` (pendente/confirmado/revogado), `eh_local`, endereço do canal e último pull. O **enrolamento é por convite, sem confiar no primeiro contato**: o administrador de cada lado confere a **impressão digital** (80 bits do SHA-256 do `did:key`) por fora do canal; só então o par vira `confirmado`. O roteador de LLM e o canal só enxergam pares confirmados.

**2. Canal assinado, fora do `/v1/` do gateway** (`/federacao/…`, bloqueado no Caddy: é VPN/LAN). Cada requisição leva a assinatura Ed25519 da instância de origem sobre uma mensagem canônica `ia-ctrl-v1` que cobre **método, rota+query, timestamp, nonce, DID de origem, DID de destino e o hash do corpo**; janela de ±60 s; nonce no cache Redis, **queimado só por requisição autêntica**; resposta de sucesso **assinada e presa ao nonce**. Recusas devolvem sempre o mesmo 404 (assinatura ruim, DID desconhecido, par pendente ou revogado); o motivo vai só ao log local. O `LLM_GATEWAY_TOKEN` (segredo único do cluster) **não** é reaproveitado.

**3. Quem pode o quê.** Rotas de leitura (`ping` a qualquer par confirmado; `estado`, o inventário, **só a par próprio**). Rotas de comando: par **próprio** + `ator` afirmado **dono/administrador**. Na tela, só dono/administrador instala, remove e cancela; qualquer membro vê. O servidor confere sempre; o botão desabilitado é cortesia.

**4. "Próprio" é um rótulo que cada lado atribui ao outro e dá poder.** Não há prova de mesmo dono (não existe identidade de organização ou de usuário entre instâncias). Por isso: marcar como próprio exige confirmação explícita, promover é auditado, a tela repete o aviso nos pontos de decisão e o receptor só obedece se **ele** marcou o par como próprio.

**5. O estado dos pares vem por pull.** Uma task periódica pergunta o `estado` a cada par próprio confirmado (falha isolada por par, backoff até 5 min, eventos só nas transições inacessível/voltou) e guarda o **último estado conhecido**, que alimenta a tela e o roteador. Para um par, `ultimo_heartbeat_em` passa a ser o instante do último **pull**; é isso que define **online/offline**. Tudo que chega é **saneado** (tipos, faixas, tamanhos), e o endereço de gateway que o par anuncia só vale se o **host** for o do `endpoint` que o administrador conferiu — um par comprometido não redireciona o tráfego de LLM (e o token do gateway) para um terceiro.

**6. Operações de modelo.** `OperacaoModeloOllama` (instalar/remover), no máximo **uma ativa por (máquina, modelo)** (garantido por índice parcial no banco), teto de 3 ativas por máquina. Local: task Celery em **fila própria** (`ollama_admin`, worker dedicado de concorrência 2), progresso agregado por camada com throttling, **sem timeout total** (só por pedaço lido), cancelamento cooperativo mais `revoke`. Num par: **máquina offline falha na hora, sem criar nada**; online, o comando vai pelo canal e fica um **espelho** local, atualizado por pull. Nomes de modelo validados (só o registry oficial por padrão); espaço livre medido por um volume montado somente leitura (a API do Ollama não informa disco) e **desconhecido não bloqueia**.

**7. O Ollama continua sem autenticação e fechado.** Só é alcançado por dentro da própria instância (`ollama_admin`, HTTP). **Nenhuma rota do canal repassa tráfego ao Ollama** (um teste confere que o módulo das rotas não tem cliente HTTP).

**8. Reconciliação com `OLLAMA_MODELOS`:** vira só a **semente do primeiro `up`**. Remover pelo painel um modelo que está nessa lista faz o `ollama-pull` trazê-lo de volta no próximo `up`; a tela avisa antes de remover.

## Alternativas consideradas

- **Reaproveitar o `LLM_GATEWAY_TOKEN` para os comandos.** Descartada: segredo único do cluster, sem identidade; qualquer nó com o token comandaria qualquer outro.
- **Abrir o Ollama na VPN.** Descartada: sem autenticação, com poder de instalar e remover.
- **Atalho antes do `Par`.** Descartada pelo dono: tudo junto, depois do `Par`.
- **Push do estado (cada máquina envia aos pares).** Descartada em favor do pull: o mesmo canal já carrega os comandos, então o par precisa ser alcançável de qualquer jeito.
- **Comando para máquina offline fica em fila.** Descartada pelo dono: **falha na hora** (e a tela mostra Offline com as ações desabilitadas).
- **Autorização automática por "mesma organização".** Impossível: `Organization` é local de cada banco. O critério é o tipo `próprio`, atribuído por quem administra.
- **Catálogo de modelos consultado ao registry.** Adiada: não há API oficial estável e uma instância pode não ter saída para a internet. Fica uma lista curada versionada mais entrada livre.
- **Exigir HTTPS sempre entre instâncias.** Descartada: HTTP é aceito só em rede privada/VPN (inclui o CGNAT do Tailscale); fora disso, HTTPS.

## Consequências

- **O poder de um par próprio comprometido é real.** O receptor **não verifica** o papel que a origem afirma: quem controla a chave de um par próprio manda comandos. O dano fica limitado a instalar/remover modelos de nome válido, no teto de operações, sem executar código nem ler dados; ator afirmado e par de origem ficam no `AuditLog` e na operação, para forense. Um teste documenta esse limite.
- **A chave da instância fica no servidor** (ADR 010): comprometer o portal vaza a chave. Rotação = revogar e reenrolar. Não há solução completa nesta etapa.
- **Relógio:** a janela de ±60 s exige NTP nas duas pontas.
- **O nome do par é validado no cadastro, não na conexão** (DNS rebinding não é pego).
- **Um par offline mostra o último estado conhecido**, marcado como tal; um espelho de operação sem notícias por 10 min falha, embora a operação possa ter continuado no par.
- **Operações que nunca começam** (não há worker da fila `ollama_admin`) falham sozinhas depois de 10 min, com a pista da causa.
- **A medição de disco** depende de montar o volume do Ollama; sem ele o espaço é "desconhecido". No modo nativo é preciso apontar `OLLAMA_DATA_DIR_HOST` para o `~/.ollama`.
- **Remover um modelo em uso** só avisa (carregado, e na lista de `OLLAMA_MODELOS`); não há como saber se há chamada em voo. Também **não** há aviso de "modelo padrão do projeto": nenhuma configuração identifica um.
- **A tela só foi testada com um DOM simulado**, não num navegador real.
- Esta ADR **remove** o `EventoReplicacao`, o endpoint de replicação, o token por máquina e o `registrar_maquina` (já superados pela ADR 011).

## Referências

- ADR 009 (Ollama e gateway), ADR 010 (federação, chaves), ADR 011 (política de replicação, `Par`).
- `docs/operacao/escala-multimaquina.md` (enrolamento e modelos), `docs/deploy.md` (rede, worker, volume), `docs/arquitetura/federacao.md`.
- Código: `apps/federacao/canal.py`, `views_controle.py`, `endpoints.py`; `apps/cluster/pares.py`, `pull.py`, `operacoes.py`, `ollama_admin.py`, `views_modelos.py`; `apps/accounts/permissoes.py`.
