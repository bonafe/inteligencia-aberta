# Saúde — resultados de exames (extensão de domínio)

A segunda extensão do núcleo (ADR 022, etapa 5), `apps/saude/`, tela `/saude/`. Existe para provar
o contrato de extensão antes de fixá-lo; por isso é pequena: resultados de exames de laboratório
lidos dos laudos que a pessoa capturou.

## Fluxo

1. **Leitura** — uma extração **válida** do parser de página de um laboratório (automática quando o
   schema tem o consumidor `saude.exames` ligado: o núcleo chama `exames.registrar_extracao`) ou uma
   estruturação por LLM escolhida no visualizador ("Registrar exames").
2. **Alegações** no vocabulário `saude:` (`https://w3id.org/inteligencia-aberta/saude/v1#`):
   `saude:analito`, `saude:resultado`, `saude:unidade`, `saude:referencia`, `saude:material` em cada
   `mencao:<blob>#…/exame/<n>`; `saude:dataColeta` e `saude:laboratorio` no sujeito-base.
3. **Piso** — `saude:Exame` é `confidencial` com `eleva_fonte`: as alegações nascem confidenciais e o
   **laudo** (o artefato e as extrações de página dele) sobe a confidencial na primeira alegação
   (`artifacts/elevacao.py`, com `AuditLog` `classificacao.elevada`, fechando o LLM externo).
4. **Projeção** — `ResultadoExame` (uma linha por resultado, com o valor numérico quando o resultado
   é um número) a partir das alegações ativas da leitura escolhida (`LeituraExames`).
5. **Tela** — os exames por analito, o último resultado, a referência e o histórico entre laudos
   (o mesmo analito junta por `analito_chave`: sem acento, maiúsculo). Cada abertura grava um
   `AuditLog` `saude.ver_resultados`: confidencial exige auditoria.

## Parsing (sem LLM)

Um resultado é qualquer objeto de uma lista com o nome do exame (`exame`, `analito`, `parametro`,
`determinacao`, `teste`, `nome`, `item`) e o resultado (`resultado`, `valor`). Unidade, referência
(`referencia*`, `valores_de_referencia`, `intervalo`) e material quando houver; a data da coleta e o
laboratório vêm do documento. Resultado que não é número ("Não reagente", "< 0,5") fica sem valor.

## Limites

- Sem gráfico nem alerta de fora da referência (a referência é texto do laudo, não intervalo).
- O schema de página do laboratório não declara papéis como o dos Gastos: a leitura é por forma.
- Eventos: `saude.exames` (`ok`, `vazio`); falha do registro automático: `extensao.consumidor`.
