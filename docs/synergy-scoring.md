# Scoring determinístico de sinergia

## Fórmula

`score_card_for_strategy` calcula um resultado para uma carta, um comandante e
uma estratégia informada explicitamente. Os componentes usam escala `[0, 1]`:

- `strategy_evidence`: soma, limitada a 1, de `weight * confidence` por alvo
  distinto, considerando evidências da estratégia e tags associadas no catálogo.
  Evidências repetidas para o mesmo alvo contam apenas uma vez, pela mais forte.
- `shared_synergy_tags`: Jaccard ponderado entre tags observadas na carta e tags
  declaradas para o comandante, limitado às tags da estratégia e com pesos da
  configuração da estratégia.
- `role_coverage`: fração das funções necessárias encontradas. Só participa se
  o chamador fornecer funções necessárias; sem esse contexto o peso é
  renormalizado entre os demais componentes disponíveis.
- `commander_text_support`: usa o `strength_score` do perfil validado de
  afinidade da estratégia. Esse valor é uma escala heurística curada, não uma
  probabilidade.

Os componentes ativos são combinados pelos pesos de `scoring.json`,
renormalizados pelo total dos pesos disponíveis. O resultado é multiplicado
pelo fator de nível do comandante (`primary`, `secondary` ou `complementary`),
recebe um ajuste de preferência opcional dentro da faixa configurada e é
limitado/arredondado segundo a configuração. O tier usa os limiares da mesma
versão de configuração.

Uma estratégia sem afinidade declarada para o comandante não recebe fator
implícito: retorna sem score e não pode ser ranqueada como sinergia específica;
essa condição fica separada das razões de inelegibilidade.

## Elegibilidade e disponibilidade

Legalidade no formato e identidade de cor são filtros, não pontos. Dados de
legalidade ou identidade ausentes são tratados como inelegíveis até que sejam
conhecidos. No modo somente coleção, disponibilidade ausente ou zero também
torna a carta inelegível. Cópias já usadas são subtraídas da quantidade física,
mas nunca alteram a sinergia intrínseca da carta.

O score não substitui quantidade disponível, curva, legalidade ou identidade de
cor. A ordenação estável usa elegibilidade, existência de afinidade, score
decrescente, disponibilidade decrescente, nome normalizado e Oracle ID.

## Explicação e limites

O retorno inclui os componentes, fator de afinidade, tier, motivos com
contribuições e as razões de inelegibilidade. A soma das contribuições explica
o score arredondado, dentro da precisão configurada. Caps de evidência genérica,
linha de tipo e regra fraca são aplicados após a fórmula e explicitados nos
motivos.

## Integração com a coleção

A página **Sugestões** consulta as cartas e quantidades físicas da coleção,
exclui o comandante e aplica legalidade, identidade de cor, cartas já usadas e
funções necessárias antes de apresentar o ranking. O resultado mostra score,
tier, funções detectadas, evidências e quantidade restante. Só entram no score
classificações cujo fingerprint ainda corresponde aos dados da carta e à
versão atual das regras; a tela oferece uma ação explícita para reclassificar
somente as cartas da coleção. Abrir o app não dispara reclassificação.

Afinidades de comandante podem ser editadas nessa página e são persistidas com
origem manual. Correções de estratégia, tag ou função são vinculadas ao Oracle
ID da carta, exigem justificativa e ficam separadas das evidências automáticas.
Uma exclusão contraditória é exibida como conflito; remover a correção retorna
à classificação automática.

O gerador também aceita uma estratégia explícita do catálogo. Nesse modo,
`build_deck` pontua os candidatos legais, compatíveis com a identidade de cor e
disponíveis na coleção usando classificações atuais e overrides. A pontuação é
normalizada para `[0, 1]` conforme a escala configurada e contribui com peso
`0.25` ao lado do balanceamento dinâmico de funções e curva. A cobertura de
funções do scorer não é ativada nessa chamada: o próprio gerador já atualiza
funções conforme seleciona cartas. Para terrenos, o score só desempata escolhas
com o mesmo resultado estrutural de cobertura de cores, utilidade e terreno
entrando virado.

O modo guiado exige afinidade explícita entre comandante e estratégia. Cartas
sem classificação atual não são pontuadas nem selecionadas; sua quantidade é
informada, e classificações não são persistidas automaticamente. A CLI usa o
modo legado quando `--strategy` é omitido, assim como o **Construtor** quando o
modo do catálogo não é escolhido. A geração final continua sujeita à validação
Commander existente.

O ajuste de prioridade do usuário é uma preferência, não evidência da carta.
Os pesos e caps são iniciais, precisam de validação com uso real e não
representam calibração empírica. A tela de sugestões não altera a decklist e
não oferece edição manual da decklist.
