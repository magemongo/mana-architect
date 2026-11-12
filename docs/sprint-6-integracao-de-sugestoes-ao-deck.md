# Sprint 6 — Integrar o catálogo estratégico ao gerador

## Objetivo

Permitir que a geração automática use uma estratégia explícita do catálogo para
priorizar cartas da coleção, sem abrir mão da estrutura do deck, das restrições
de Commander ou do comportamento atual quando a pessoa não escolhe uma
estratégia do catálogo.

Esta sprint liga o catálogo ao gerador. A edição manual do deck a partir da
página **Sugestões** fica para uma etapa posterior.

## Contexto atual

- `build_deck` filtra cartas da coleção por legalidade e identidade de cor,
  escolhe terrenos para cobrir as cores e completa os não-terrenos equilibrando
  funções e curva de mana.
- A prioridade estratégica atual do gerador vem do perfil legado
  `strategy_profiles.toml`; o score de seleção também considera função e curva.
- O catálogo novo é usado pela página **Sugestões**, cujo scorer calcula
  afinidade de uma carta com uma estratégia e um comandante. Ele ainda não é
  chamado pelo gerador.
- O scorer é por carta. Não avalia sozinho a qualidade nem a composição global
  de um deck.

## Experiência esperada

1. No **Construtor**, a pessoa escolhe o comandante e pode optar pelo modo
   existente ou pelo modo **Estratégia do catálogo**.
2. No modo do catálogo, escolhe uma estratégia ativa. A interface confirma que
   há uma afinidade cadastrada para esse comandante e essa estratégia.
3. O gerador usa o catálogo para priorizar candidatos, mas continua montando
   uma lista completa segundo as metas de terrenos, funções e curva.
4. O resultado apresenta a estratégia usada e continua passando pela validação
   atual antes de permitir exportação.
5. Se nenhuma estratégia do catálogo for escolhida, o gerador mantém o
   comportamento legado. Essa escolha não deve ocorrer silenciosamente quando
   a pessoa pediu o modo do catálogo e há dados ou configurações faltantes.

O fluxo deve estar disponível na interface Streamlit e no comando `build-deck`
da CLI por meio de uma opção explícita de estratégia. O comando sem essa opção
mantém o comportamento atual.

## Regras de integração

- **Filtros duros antes do ranking:** coleção disponível, legalidade Commander
  conhecida e válida, identidade de cor compatível e limites de cópias continuam
  sendo restrições. Score não pode tornar uma carta inelegível em elegível.
- **Afinidade explícita:** a estratégia escolhida precisa ter afinidade
  cadastrada para o comandante. Se não houver, explicar como cadastrá-la e não
  substituir a estratégia por um perfil ou score implícito.
- **Classificação rastreável:** usar classificações atuais do catálogo e seus
  overrides manuais sem apagar evidências automáticas. Não persistir
  reclassificações automaticamente ao gerar.
- **Classificação ausente ou obsoleta:** informar a quantidade afetada e
  orientar a reclassificação explícita. Não tratar uma carta sem score como se
  tivesse score zero nem mudar silenciosamente para o modo legado.
- **Composição estrutural:** combinar o score estratégico com o balanceamento de
  funções e curva do gerador. Normalizar o score pela faixa configurada e
  limitar sua contribuição a `0.25`, junto ao score dinâmico de funções e curva.
  Não selecionar simplesmente as cartas com maior score até completar 99
  cartas; a cobertura de funções do scorer fica desativada nessa chamada porque
  o gerador já atualiza funções durante a seleção.
- **Base de mana:** preservar a cobertura de cores, os limites de terrenos
  utilitários, estoque e demais regras atuais. O score estratégico pode
  desempatar escolhas com o mesmo resultado estrutural, mas não superar a
  cobertura de cores ou a prioridade por fontes multicoloridas.
- **Determinismo e explicabilidade:** com as mesmas cartas, configuração,
  comandante, estratégia e afinidade, a seleção e seus desempates devem ser
  reproduzíveis. A interface e os arquivos gerados identificam a estratégia
  usada; scores continuam heurísticos, não uma promessa de qualidade
  competitiva.
- **Validação final:** validar a lista produzida com o validador atual. Não
  exportar um rascunho com violações determinísticas conhecidas; informar
  claramente quando dados ausentes tornarem a validação incompleta.

## Escopo

### Incluído

- Uma opção explícita entre geração legada e geração guiada por estratégia do
  catálogo, preservando o modo legado como padrão.
- Seleção de estratégia no **Construtor** e opção correspondente na CLI.
- Carregamento de afinidade, classificações atuais e overrides para pontuar os
  candidatos elegíveis.
- Integração determinística do score com a seleção de não-terrenos, mantendo o
  balanceamento de funções e curva.
- Integração subordinada às restrições existentes na escolha de terrenos.
- Exibição da estratégia usada, avisos sobre afinidade/classificação e
  validação final.
- Testes unitários e de integração para os dois modos de geração.

### Fora de escopo

- Adicionar ou substituir cartas manualmente na decklist a partir de
  **Sugestões**.
- Persistir decks ou criar gerenciamento de múltiplas listas.
- Alterar coleção, regras de legalidade, catálogo de estratégias ou fórmula do
  scorer sem necessidade demonstrada.
- Calibrar pesos com dados externos, avaliar power level competitivo ou usar
  IA/serviços remotos.
- Suporte a comandantes parceiros.

## Critérios de aceite

- [x] Sem estratégia selecionada, a geração atual continua funcionando com os
      mesmos filtros e regras.
- [x] No modo do catálogo, a estratégia selecionada é usada explicitamente pelo
      gerador e aparece no resultado.
- [x] A geração guiada é recusada com mensagem acionável se faltar afinidade
      para o par comandante/estratégia.
- [x] Classificações ausentes ou obsoletas são informadas; a geração não as
      persiste nem troca de modo silenciosamente.
- [x] A contribuição normalizada do score é limitada a `0.25` e combinada com
      função e curva, sem substituir o balanceamento estrutural.
- [x] Legalidade, identidade de cor, disponibilidade, singleton e cobertura de
      cores dos terrenos continuam respeitados.
- [x] O rascunho gerado continua passando pelo validador antes dos downloads.
- [x] Repetir a geração com os mesmos dados e configurações produz a mesma
      decklist e ordem de desempate.
- [x] Testes demonstram que o modo do catálogo altera a priorização em um caso
      controlado sem enfraquecer as restrições duras.
- [x] A CLI sem a nova opção e a interface no modo legado preservam o
      comportamento anterior.

## Sequência sugerida de implementação

1. Definir a composição determinística entre score estratégico, equilíbrio de
   funções, curva e critérios de desempate; normalizar escalas e cobrir a regra
   com testes antes de integrá-la ao fluxo.
2. Estender o serviço de geração com um modo de estratégia opcional, carregando
   catálogo, afinidade, classificações e overrides sem efeitos colaterais de
   persistência.
3. Conectar a opção ao **Construtor** e à CLI, incluindo os estados de
   configuração ausente, estratégia sem afinidade e classificações desatualizadas.
4. Verificar a validação, os downloads e a regressão do modo legado; validar
   manualmente os dois fluxos.

## Limite para considerar a sprint concluída

A sprint termina quando o catálogo influencia de forma observável e
determinística a seleção automática, preservando as restrições e a composição
estrutural, e quando o modo legado continua disponível. A edição manual de
decklists permanece como trabalho posterior.
