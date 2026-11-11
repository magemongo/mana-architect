# Diretrizes iniciais de deckbuilding

Este documento descreve objetivos e compromissos do gerador; nao e uma lista
de regras de legalidade. O validador continua sendo a autoridade para tamanho,
identidade de cor, singleton, estoque e legalidade conforme o snapshot local.

## Principios

- A colecao fisica e a unica fonte de cartas selecionaveis.
- Preferencias estrategicas alteram a prioridade; nao excluem cartas por si
  so. Estoque, identidade de cor e legalidade continuam sendo filtros duros.
- Quantidades de categorias sao metas configuraveis, nao proporcoes universais.
- Cada sinal estrategico usado pelo motor deve ser apresentado como heuristica
  e poder ser rastreado ate o texto Oracle, tipo ou dado estrutural.
- A base de mana deve primeiro cobrir as cores do comandante, favorecendo
  fontes que ajudam mais de uma cor; terrenos utilitarios entram como uma
  preferencia configuravel e competem por espaco com fontes coloridas.
- O power level deve ser contextual e explicavel. Nunca deve ser uma nota
  absoluta que remova automaticamente uma carta da colecao.

## Perfis estruturados

`config/strategy_profiles.toml` armazena sinais, pesos iniciais e preferencias
especificas de comandante em formato legivel por pessoas e maquinas. Pesos sao
prioridades relativas para ordenar candidatos, nao pontuacoes de qualidade.
Os padroes de texto sao aproximacoes e podem gerar falsos positivos; revise os
perfis com decks de referencia e testes.

O perfil inicial de Anowon cobre Rogues tribais, mill e dano de combate. Um
comandante sem perfil continua usando a pontuacao geral de funcoes e curva.

## Evolucao

Antes de adicionar metas numericas de ramp, compra, remocao, protecao ou
terrenos, comparar decks de referencia e colecoes distintas. Futuramente,
preferencias do usuario e contexto do deck podem ajustar esses objetivos sem
alterar regras de legalidade.
