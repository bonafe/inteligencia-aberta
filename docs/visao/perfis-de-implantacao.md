# Perfis de implantação

Estas descrições são complementares a [`personas.md`](./personas.md), não substitutas. `personas.md` descreve *quem usa* o sistema e *para quê*; este documento descreve *como o sistema está fisicamente implantado* para essa pessoa — quantas máquinas, de quem, com que papel cada uma, e com que conectividade. A mesma pessoa (mesma persona de uso) pode viver em qualquer um destes perfis, e pode migrar de um para outro conforme cresce.

Ver [ADR-006](../arquitetura/decisoes/006-cluster-adaptativo-multiproprietario.md) para a decisão de arquitetura que motivou nomear estes perfis, e [`docs/operacao/escala-multimaquina.md`](../operacao/escala-multimaquina.md) para o que já está implementado (hoje, só o perfil "Base pessoal única" e parte da "Malha pessoal heterogênea").

---

## D1 — Base pessoal única

**Contexto:** Uma máquina só. `docker compose up` e pronto — é o que o projeto já entrega hoje (Fase 0/1).

**Necessidade:** Nenhuma configuração de rede, nenhum conceito de cluster. Tudo local.

**Critério de sucesso:** O usuário nunca precisa saber que existe um conceito de "máquina que hospeda a infraestrutura" — para ele, só existe "o sistema".

**Critério de falha:** Qualquer exigência de configurar VPN, registrar máquina, ou entender topologia antes de usar o sistema pela primeira vez.

---

## D2 — Malha pessoal heterogênea

**Contexto:** A mesma pessoa tem mais de uma máquina, com perfis de hardware bem diferentes — uma com muito espaço de disco e pouca CPU/RAM (boa para guardar dados), outra com boa CPU e pouco disco (boa para rodar modelos de LLM). Nenhuma das duas é "a principal" no sentido de importância — cada uma tem um papel.

**Necessidade:** O sistema decidir sozinho onde processar (a máquina com CPU) e onde guardar (a máquina com disco), sem o usuário ter que apontar manualmente qual arquivo vai para qual máquina.

**Como isso já existe hoje:** `MaquinaStatus` (heartbeat de CPU/RAM/disco) e `apps/cluster/llm_router.py` já resolvem a parte de "qual máquina roda o modelo mais rápido". A parte de "qual máquina guarda o dado" (motor de posicionamento por espaço disponível) é o rumo do ADR-006, ainda não implementada.

**Critério de sucesso:** O usuário nota o sistema mais rápido e com mais espaço, sem ter configurado isso manualmente por arquivo.

**Critério de falha:** Uma máquina fica sem espaço enquanto outra do mesmo dono tem sobra, e o sistema não usa essa sobra sozinho.

---

## D3 — Nó móvel offline-first

**Contexto:** Uma máquina (tipicamente um notebook) se desconecta da rede — viagem, campo, sem sinal — e precisa continuar funcionando inteiramente sozinha: capturar, extrair, buscar, gerar relatório. Ao reconectar, sincroniza o que fez offline com o resto da malha.

**Necessidade:** Nenhuma operação essencial pode depender de outra máquina estar acessível. Sincronizar depois não pode gerar conflito nem perda do que foi feito offline.

**Por que já é viável com o desenho atual:** A escolha de replicar por log de eventos (não streaming replication do Postgres, ADR anterior a este) já suporta esse caso: todo artefato novo nasce com UUID e dono "quem capturou" — duas máquinas capturando coisas diferentes offline nunca colidem ao sincronizar depois, porque não estão escrevendo o mesmo registro.

**Como isso ainda não existe:** Falta um perfil de implantação que suba a stack completa (Postgres/Qdrant/MinIO próprios) preparado para operar desconectado por padrão — hoje só existe o perfil `compute` (worker fino, sem banco próprio, sempre precisa da máquina que hospeda a infra compartilhada acessível).

**Critério de sucesso:** O usuário fecha o notebook em viagem, sem internet, continua usando o sistema normalmente. Ao reconectar, o que fez aparece nas outras máquinas sem ele ter que fazer nada manual.

**Critério de falha:** Qualquer funcionalidade essencial (capturar, buscar no que já foi capturado) parar de funcionar por falta de rede.

---

## D4 — Borda pública de baixo recurso

**Contexto:** Uma máquina de recurso muito baixo (pouca RAM/CPU), mas com IP público — não processa nada relevante, mas é alcançável de fora da rede local/VPN do dono. Serve como ponte/ponto de entrada, não como nó de processamento ou armazenamento.

**Necessidade:** O sistema reconhecer que essa máquina tem um papel diferente das demais — não deveria receber tarefa pesada nem réplica de dado grande, mas é valiosa justamente por ser alcançável.

**Como isso se encaixa no modelo:** É o primeiro caso concreto que exige capacidades independentes por máquina (ADR-006) em vez de um rótulo único — essa máquina não é nem "armazenamento" nem "computação" no sentido dos outros perfis, é "borda pública", uma capacidade à parte.

**Critério de sucesso:** O motor de posicionamento e o roteador de LLM nunca escolhem essa máquina para processar ou guardar dado grande — só para o papel de alcançabilidade que ela tem.

**Critério de falha:** Uma tarefa pesada ou uma réplica grande é enviada para uma máquina de 1GB de RAM porque o sistema não distingue capacidade de mero endereço alcançável.

---

## D5 — Colaboração entre donos via Projeto

**Contexto:** Pessoas de organizações diferentes (colegas de trabalho, por exemplo) quiseram compartilhar seletivamente: algumas informações entre si, e capacidade de computação de LLM (usar a máquina do outro, e vice-versa) — sem que isso signifique confiança total entre as organizações inteiras.

**Necessidade:** Um espaço de colaboração que não seja "a mesma organização" (confiança total, isolamento por padrão entre organizações continua valendo) nem "um compartilhamento pontual de um documento" (`Sharing`, já existe, mas é por artefato e revogável, não uma relação contínua). É o `Projeto` do ADR-006.

**Critério de sucesso:** Dois colegas de organizações diferentes entram num mesmo projeto, cada um decide o que daquela organização entra no projeto, e as requisições de LLM de um podem ser atendidas pela máquina do outro quando ambos autorizarem.

**Critério de falha:** Participar de um projeto vaza, mesmo que sem querer, dado de uma organização inteira para a outra — a autorização tem que ser sempre explícita e por item/categoria, nunca implícita por fazer parte do mesmo projeto.
