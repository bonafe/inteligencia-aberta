# ADR 019 — IPED como capacidade opcional do nó: runner isolado, entrada somente leitura

**Status:** **proposta**, com a **prova de conceito implementada e exercitada em 2026-10-11** (pedido → worker → runner → IPED → estado, numa pasta local, em contêiner). Falta a **importação** do resultado para o IA (próxima etapa) e o upload de arquivos avulsos.

## Contexto

O [IPED](https://github.com/sepinf-inc/IPED) (Polícia Federal, Java, GPLv3) processa pastas, imagens de disco e extrações de celular: indexa conteúdo e metadados, faz OCR, hashes, parsers de mensageria, linha do tempo. O IA captura páginas; o IPED cobre o que está em **dispositivos e arquivos**. A ideia: rodar o IPED **dentro do IA**, para a pessoa mandar pastas ou arquivos avulsos — e, em hosts como a Antares, que já têm o disco com os arquivos, processar **sem copiar**.

Restrições que moldam a decisão:

- o IPED **abre arquivo hostil** (é o trabalho dele), e quem manda arquivos aqui não é necessariamente perito;
- o portal **não pode ter o `docker.sock`** (seria a chave-mestra do host);
- quem já tem um IPED (nativo, ou de outra pessoa) não deve ser obrigado a rodar o nosso;
- o IPED é pesado e não pode disputar CPU/memória com o pipeline normal;
- dado forense é sensível por natureza.

## O que a prova de conceito mostrou (fatos, não suposições)

- O `-d` do IPED **aceita pasta** (`-d`: "…folder or *.iped file"), então pasta local e arquivos avulsos (numa pasta) entram pelo mesmo caminho. Flags usadas: `--nogui -d <entrada> -o <saida> -profile <perfil>`. Perfis existentes: `forensic`, `triage`, `fastmode`, `pedo`, `blind`.
- **No Linux o IPED não roda só com o zip do release** (4.3.1, 749 MB): exige o fork do Sleuthkit compilado (`tskJarPath`). A saída prática é a imagem da **comunidade** `ipeddocker/iped:processor` (não é do projeto IPED; ~6,6 GB em disco), que na data trazia a **4.4.0-SNAPSHOT** — por isso **fixar por digest** é obrigatório antes de produção.
- Rodando **sem rede, sem root, com raiz somente leitura e sem capacidades**, o IPED só funcionou depois de três ajustes, todos no compose: nome do host fixado em `127.0.0.1` (sem rede o log4j não resolve o próprio nome); `/tmp` como `tmpfs` **com `exec`** (o Java extrai `zstd-jni` para lá e o `tmpfs` padrão é `noexec`); e `HOME` num volume (o `user.home` do usuário 1000 da imagem não é gravável).
- Uma pasta de **7 arquivos** processou em ~20 s. A saída tem o índice **Lucene** em `iped/index`, `iped/data` e o relatório; mas **cada caso carrega a própria instalação do IPED** (jre, lib, tools): **~750 MB para 7 arquivos**. O custo de disco por caso é fixo e alto — a importação deve ler o que precisa e o caso poder ser descartado.

## Decisão

**1. O IPED é uma capacidade opcional do nó**, como o Ollama (ADR 009): perfil `iped` do compose, que cada instância liga ou não. Nada muda para quem não liga.

**2. O IPED roda em um contêiner `iped` isolado e ninguém do IA fala com o Docker.** Protocolo **por arquivos** num volume `/trabalho`: o `worker-iped` (fila `iped`, concorrência 1, mesma imagem do portal) escreve `pedidos/<id>.pedido` (`perfil=`, `entrada=`) e lê `estado/<id>`, `codigo/<id>`, `logs/<id>.log`; cancelar é criar `cancelar/<id>`. O `iped-runner` (`infra/iped/runner.sh`) é o único que executa o IPED. **O mesmo script serve a um IPED nativo no host**, e o conector da importação só precisa dos arquivos do caso — não de quem rodou o IPED.

**3. Endurecimento do contêiner do IPED:** `network_mode: none`, usuário sem privilégio (uid do dono das pastas do host), raiz somente leitura, `cap_drop: ALL`, `no-new-privileges`, `/entrada` **somente leitura**, memória limitada, `/tmp` em `tmpfs`. O runner **não usa `eval`**: valida o perfil contra lista fechada e a entrada (relativa, sem `..`, sem `-` inicial, `realpath` dentro de `/entrada`) **de novo**, mesmo que o portal já tenha validado — quem escreve no volume `/trabalho` não ganha execução arbitrária.

**4. Entrada: só relativa a uma raiz permitida.** O administrador define a raiz (`IPED_ENTRADA_HOST` → `/entrada`, `IPED_RAIZES_PERMITIDAS`); o pedido traz um caminho **relativo**. `apps/forense/caminhos.py` recusa absoluto, `..`, link simbólico que foge, NUL e raízes que expõem o host (`/`, `/etc`, `/proc`...). Ninguém digita um caminho livre do host.

**5. Registro:** `ProcessamentoIPED` (estado, perfil, código de saída, final do log), `PipelineEvent` `forense.iped.*`. O caso fica em `IPED_SAIDA_DIR/<id>`.

**6. Licença:** o IA **não distribui o IPED**; só referencia uma imagem externa fixada por digest e fala com o IPED por processo/arquivos (e, na importação, por HTTP). A GPLv3 não passa para o IA.

## Fora desta ADR (próximos passos, nesta ordem)

1. **Importação** do caso para o IA: ler pelo **Web API do IPED** (`search`, `docs/{id}`, `/text`, `/content`) apontando para a pasta do caso, importar o que estiver em **bookmark/filtro** (nunca o caso inteiro). Mapeamento: item → `Artifact`+`DocumentText`; SHA-256 → `blob_hash` (`ni:`); `hashDb:*`, NER, idioma, nudez → `Claim` com `produtor="iped"`; mensagens/e-mails → eventos. Tudo nasce `confidencial`/`restrito`, só LLM local. **Antes de implementar**, inspecionar as respostas reais da Web API (a wiki não descreve o formato nem a autenticação) neste mesmo caso de teste.
2. **Upload** de arquivos avulsos para a raiz de entrada (limites de tamanho, proteção a zip bomb, só dono/administrador), e tela no portal.
3. **Descartar a instalação embutida** no caso, ou usar um IPED compartilhado, para não custar ~750 MB por caso.
4. **Fixar o digest** da imagem e documentar o procedimento de atualização.

## Alternativas consideradas

- **`docker.sock` no portal/worker.** Descartada: dá ao portal o controle do host.
- **IPED dentro da imagem do worker** (Python + Java juntos). Descartada: mistura as superfícies de ataque (o worker tem acesso ao banco e à rede; o IPED lê arquivo hostil) e infla a imagem do portal.
- **Chamar o IPED por HTTP/serviço próprio sempre ligado.** Descartada nesta etapa: é uma porta a mais; o protocolo por arquivos dispensa rede e funciona igual para um IPED nativo.
- **Usar o IPED só nativo, nunca em contêiner.** Descartada: o contêiner é o que permite o isolamento acima; o nativo continua suportado pelo mesmo runner.
- **Ler o índice Lucene diretamente.** Descartada: formato interno que muda entre versões e exige Java.
- **Reimplementar parsers de disco/mensageria no IA.** Descartada: é exatamente o que o IPED já faz bem.

## Consequências

- Quem liga o perfil `iped` baixa uma imagem de vários GB e reserva CPU/memória (`IPED_MEM_LIMIT`); um processamento por vez.
- **Dependência de uma imagem da comunidade** que acompanha o `master` do IPED: sem fixar o digest, uma atualização pode quebrar o pipeline.
- O isolamento exigiu `exec` no `/tmp` do contêiner (necessário para o Java), o que enfraquece um pouco a barreira; o contêiner continua sem rede, sem root e sem escrita na entrada.
- Quem tem acesso de escrita ao volume `/trabalho` pode **pedir** processamentos de qualquer pasta dentro de `/entrada`; por isso a raiz deve conter só o que se aceita processar.
- Dado importado do IPED **não** pode ser tratado como "fato" do sistema: é o que o dispositivo continha, com a cadeia de custódia (hash, versão do IPED, caso de origem) preservada.
