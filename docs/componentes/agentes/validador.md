# Agente: Validador

## Responsabilidade

Auditar qualidade e veracidade dos artefatos e vínculos produzidos antes de chegarem ao analista. Atua como controle de qualidade do ciclo de inteligência.

## Verificações

- **Corroboração:** Fatos críticos têm mais de uma fonte? Se não, marca como `corroboração pendente`.
- **Consistência:** Datas, valores e nomes são consistentes entre fontes diferentes?
- **Atualidade:** Dado tem mais de 6 meses? Marca como `potencialmente desatualizado`.
- **Separação semântica:** Todo artefato tem `tipo_informacao` preenchido (`fato`, `opinião`, `inferência`)?
- **Confiança mínima:** Artefatos com `confiança < 0.5` são marcados para revisão.

## Saída

O validador não descarta artefatos — ele os anota. Cada artefato recebe marcações de validação. O orquestrador decide se continua com base no resultado.

## Critérios de Aceitação

- [ ] Nenhum artefato passa para o analista sem `tipo_informacao` definido.
- [ ] Fatos sem corroboração são marcados explicitamente no resultado final.
- [ ] Validação de 1000 artefatos em < 30 segundos.
- [ ] Relatório de validação disponível para revisão humana.
