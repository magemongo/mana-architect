# Mana Architect --- Diretrizes iniciais do projeto

**Versão:** 0.1\
**Status:** Documento inicial de orientação para o agente de
desenvolvimento (Codex)

## 1. Visão do projeto

Desenvolver uma aplicação desktop local chamada **Mana Architect**, um
assistente para construção, análise e otimização de decks de **Magic:
The Gathering --- Commander / EDH**, usando exclusivamente as cartas que
o usuário possui fisicamente.

O sistema poderá conhecer cartas existentes no universo de Magic, mas
**nenhuma carta poderá ser incluída em um deck gerado se não estiver
registrada na coleção do usuário em quantidade suficiente**.

A aplicação será executada localmente no computador do usuário. O
primeiro objetivo é construir uma base confiável de coleção e regras;
recursos de IA e interface avançada virão depois.

## 2. Objetivos do MVP

O MVP deverá permitir:

1.  Importar uma coleção a partir de CSV exportado do Moxfield ou
    Archidekt.
2.  Armazenar a coleção em banco de dados local.
3.  Enriquecer os registros com dados de cartas do Scryfall.
4.  Pesquisar e filtrar cartas da coleção.
5.  Identificar comandantes elegíveis que o usuário possui.
6.  Construir uma lista de Commander com 100 cartas, incluindo o
    comandante.
7.  Respeitar identidade de cor, regras de singleton, banlist e
    quantidades disponíveis.
8.  Analisar a composição do deck, curva de mana e funções das cartas.
9.  Exportar a decklist.
10. Em uma etapa posterior, permitir interação em linguagem natural por
    meio de um agente LLM.

## 3. Princípios obrigatórios

O agente de desenvolvimento deve seguir estes princípios em todas as
decisões:

### 3.1 Coleção como fonte de verdade

-   A coleção local do usuário é a única fonte autorizada para
    selecionar cartas para decks.
-   Dados externos, como Scryfall, servem para enriquecer e validar
    informações, não para afirmar que o usuário possui uma carta.
-   Nunca adicionar cartas automaticamente à coleção a partir de
    sugestões do LLM.
-   Antes de incluir uma carta em um deck, verificar sua quantidade
    disponível.
-   Uma carta com quantidade zero ou ausente da coleção não pode ser
    selecionada.
-   O sistema deve distinguir identidade da carta de impressão física.

### 3.2 Regras determinísticas

-   Regras de Commander, identidade de cor, singleton, quantidades e
    tamanho do deck devem ser verificadas por código Python, não apenas
    pelo LLM.
-   O LLM pode sugerir estratégias e explicar escolhas, mas não é a
    autoridade final sobre legalidade.
-   Toda decklist gerada deve passar pelo validador antes de ser
    apresentada como válida.
-   Erros e avisos devem ser explícitos; não ocultar falhas de
    validação.

### 3.3 Desenvolvimento incremental

-   Priorizar um MVP simples, funcional e testável.
-   Não implementar infraestrutura distribuída, microsserviços ou
    serviços em nuvem sem necessidade comprovada.
-   Não introduzir frameworks complexos prematuramente.
-   Evitar abstrações genéricas antes de existir um caso de uso
    concreto.
-   Cada etapa deve terminar com código executável e testes.

### 3.4 Transparência

-   Explicar por que uma carta foi selecionada, considerando sinergia,
    função no deck, curva de mana e estratégia.
-   Quando não houver cartas suficientes para cumprir um pedido,
    informar a limitação em vez de inventar alternativas.
-   Distinguir recomendações estratégicas de verificações oficiais de
    legalidade.

## 4. Stack tecnológica inicial

Utilizar inicialmente:

  -----------------------------------------------------------------------
  Componente              Tecnologia              Finalidade
  ----------------------- ----------------------- -----------------------
  Linguagem               Python                  Lógica principal

  Banco local             SQLite                  Persistência da coleção
                                                  e decks

  ORM                     SQLAlchemy              Acesso ao banco

  Manipulação de CSV      Pandas (ou módulo csv   Importação
                          quando suficiente)      

  Dados de cartas         Scryfall Bulk Data /    Enriquecimento dos
                          API                     dados

  Interface               Streamlit               Aplicação desktop local
                                                  no navegador

  Testes                  Pytest                  Testes automatizados

  Configuração            python-dotenv           Variáveis de ambiente

  Controle de versão      Git                     Histórico do projeto

  LLM                     OpenAI API, em etapa    Interpretação de
                          posterior               linguagem natural
  -----------------------------------------------------------------------

Não integrar o LLM no primeiro sprint. Não exigir Docker para o MVP.
Manter a aplicação executável localmente por meio de um ambiente virtual
Python.

## 5. Arquitetura inicial

Adotar uma arquitetura modular e simples:

``` text
mana-architect/
├── app/
│   ├── main.py
│   └── pages/
├── src/
│   ├── database/
│   │   ├── connection.py
│   │   ├── models.py
│   │   └── repositories.py
│   ├── ingestion/
│   │   └── csv_importer.py
│   ├── integrations/
│   │   └── scryfall.py
│   ├── intelligence/
│   ├── deckbuilding/
│   ├── validation/
│   └── agent/
├── data/
│   ├── imports/
│   ├── cache/
│   └── mana_architect.db
├── tests/
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

A estrutura poderá evoluir conforme o projeto crescer, mas mudanças
devem ter justificativa técnica. Não criar módulos vazios sem
necessidade.

## 6. Modelo de dados inicial

Separar a identidade lógica da carta de suas impressões físicas.

### 6.1 Entidade `cards`

Representa a carta lógica, preferencialmente identificada pelo
`oracle_id` do Scryfall.

Campos iniciais sugeridos:

-   `oracle_id` --- identificador lógico da carta.
-   `name`
-   `mana_cost`
-   `cmc`
-   `colors`
-   `color_identity`
-   `type_line`
-   `oracle_text`
-   `keywords`
-   `legalities`
-   `image_uri`
-   `scryfall_updated_at` ou metadado equivalente de sincronização.

### 6.2 Entidade `collection_printings`

Representa cada impressão física que o usuário possui.

Campos iniciais sugeridos:

-   `id`
-   `scryfall_id`
-   `oracle_id` --- relacionamento com `cards`.
-   `set_code`
-   `collector_number`
-   `language`
-   `finish`
-   `quantity`
-   `condition`

Uma carta pode ter várias impressões físicas. Para construir decks,
agregar as quantidades por `oracle_id`, sem perder os detalhes das
impressões.

### 6.3 Entidades futuras

Não implementar todas no primeiro sprint, mas projetar sem bloquear a
evolução:

-   `decks`
-   `deck_cards`
-   `card_tags`
-   `card_synergies`
-   `import_history`
-   `validation_results`

## 7. Integração com Scryfall

-   Preferir o arquivo Bulk Data para cargas e atualizações extensas.
-   Usar a API individual apenas quando necessário.
-   Respeitar as orientações e limites de uso publicados pelo Scryfall.
-   Implementar cache local para evitar requisições repetidas.
-   Tratar indisponibilidade da API sem perder dados já importados.
-   Não sobrescrever informações de propriedade do usuário com dados
    externos.
-   Registrar a data da última sincronização.
-   Usar `oracle_id` como principal elo entre carta lógica e impressão,
    quando disponível.
-   Tratar cartas sem `oracle_id`, cartas de dupla face, layouts
    especiais e registros incompletos de forma explícita, sem descartar
    silenciosamente os dados.

Referência: https://scryfall.com/docs/api

## 8. Importação da coleção

A primeira fonte será um CSV real exportado pelo usuário do Moxfield ou
Archidekt.

Antes de implementar o parser:

1.  Inspecionar os nomes e tipos das colunas do arquivo real.
2.  Identificar como o export representa nome, edição, número de
    coletor, quantidade, idioma e acabamento.
3.  Documentar o mapeamento entre colunas de origem e modelo interno.
4.  Validar linhas ausentes ou inconsistentes.
5.  Produzir um resumo da importação: linhas lidas, cartas reconhecidas,
    quantidades importadas, duplicatas e erros.

Requisitos:

-   Não assumir que Moxfield e Archidekt usam o mesmo esquema.
-   Manter parsers separados quando os formatos forem diferentes.
-   Permitir reimportação sem duplicar indevidamente registros.
-   Definir uma estratégia de atualização explícita para registros já
    existentes.
-   Não descartar linhas com erro sem reportá-las.
-   Preservar o arquivo original em `data/imports/` ou orientar o
    usuário a armazená-lo ali.
-   Não versionar dados pessoais da coleção no Git por padrão.

## 9. Motor de deckbuilding --- visão futura

O motor deve ser predominantemente determinístico e independente do LLM.

Fluxo desejado:

1.  Identificar comandantes elegíveis na coleção.
2.  Interpretar a estratégia solicitada.
3.  Selecionar candidatos somente da coleção.
4.  Filtrar por identidade de cor e legalidade.
5.  Avaliar sinergias e funções.
6.  Montar a lista respeitando quantidades.
7.  Avaliar curva de mana, terrenos, ramp, compra de cartas, interação,
    proteção e condições de vitória.
8.  Executar validação completa.
9.  Explicar escolhas, limitações e compromissos estratégicos.

As categorias funcionais iniciais podem incluir:

-   Terrenos
-   Ramp
-   Compra de cartas
-   Remoção pontual
-   Remoção global
-   Proteção
-   Recursão
-   Tutores
-   Sinergia com o comandante
-   Condições de vitória
-   Utilidade

Não impor proporções rígidas e universais para essas categorias. A
distribuição deve variar de acordo com comandante, estratégia, curva,
disponibilidade da coleção e preferências do usuário.

## 10. Validador Commander

Implementar regras em funções testáveis, separadas da interface e do
LLM.

Verificações mínimas:

-   Deck com exatamente 100 cartas, incluindo o comandante.
-   Elegibilidade do comandante.
-   Identidade de cor de todas as cartas compatível com o comandante.
-   Regra singleton e exceções aplicáveis.
-   Banlist vigente.
-   Quantidade física disponível na coleção.
-   Tratamento correto de terrenos básicos e outras exceções de regras.
-   Cartas com nomes ou identidades especiais, como cartas de dupla face
    e parceiros, quando aplicável.

As regras e a banlist podem mudar. Registrar a fonte e a data da última
atualização. Não declarar legalidade atualizada se os dados estiverem
desatualizados.

Referências: - https://mtgcommander.net/index.php/rules/ -
https://magic.wizards.com/pt-br/formats/commander

## 11. Agente LLM --- etapa posterior

O LLM deverá atuar como orquestrador e consultor estratégico, não como
banco de dados nem como validador.

Ferramentas Python planejadas:

-   `get_collection()`
-   `search_collection()`
-   `get_card_details()`
-   `find_commanders()`
-   `find_synergies()`
-   `build_deck()`
-   `analyze_deck()`
-   `validate_deck()`
-   `suggest_replacements()`

Regras para o agente:

-   Consultar ferramentas para obter dados da coleção.
-   Não inventar quantidade, propriedade, texto Oracle ou legalidade.
-   Não criar cartas ou registros de coleção.
-   Não contornar o motor de seleção ou o validador.
-   Informar quando não houver cartas suficientes para atender ao
    pedido.
-   Usar contexto recuperado sob demanda, evitando enviar toda a coleção
    ao LLM.
-   Manter a chave de API em variável de ambiente; nunca gravá-la no
    código ou no Git.
-   Registrar consumo de tokens e permitir configurar limites de gasto.
-   Permitir futuramente trocar o provedor ou usar Ollama sem reescrever
    as regras centrais.

## 12. Segurança, configuração e qualidade

-   Criar `.env.example`, nunca incluir credenciais reais no
    repositório.
-   Incluir `.env`, banco de dados pessoal, arquivos de coleção e caches
    grandes no `.gitignore`, conforme apropriado.
-   Validar entradas de CSV e tratar erros de forma informativa.
-   Usar transações para operações de banco que alterem a coleção.
-   Criar testes para importação, agregação de quantidades e regras de
    validação.
-   Não executar operações destrutivas ou apagar dados importados sem
    confirmação explícita.
-   Não instalar dependências sem necessidade; justificar novas
    dependências.
-   Fixar ou limitar versões das dependências no arquivo de projeto.

## 13. Plano de implementação

### Sprint 1 --- Fundação e importação

**Objetivo:** importar e consultar a coleção local.

Tarefas:

1.  Verificar a versão do Python e preparar o ambiente.
2.  Criar a estrutura mínima do projeto.
3.  Configurar SQLAlchemy e SQLite.
4.  Criar os modelos `cards` e `collection_printings`.
5.  Inspecionar um CSV real do usuário.
6.  Implementar o parser correspondente.
7.  Implementar importação idempotente ou uma estratégia segura de
    atualização.
8.  Criar testes de importação e agregação.
9.  Criar comandos ou uma interface mínima para consultar a coleção.

**Critério de conclusão:** o usuário consegue importar um CSV real,
consultar cartas e conferir quantidades sem duplicações indevidas.

### Sprint 2 --- Dados de cartas

**Objetivo:** enriquecer a coleção com dados confiáveis do Scryfall.

-   Implementar download/importação do Bulk Data.
-   Associar impressões a cartas lógicas.
-   Criar cache e estratégia de atualização.
-   Pesquisar por nome, cor, tipo, identidade de cor e custo de mana.

### Sprint 3 --- Regras e classificação

**Objetivo:** identificar comandantes e funções das cartas.

-   Identificar comandantes elegíveis.
-   Criar classificação inicial de funções.
-   Implementar filtros e regras básicas de Commander.
-   Criar testes unitários para casos comuns e exceções.

### Sprint 4 --- Deckbuilding

**Objetivo:** construir decks a partir da coleção.

-   Implementar seleção de cartas.
-   Respeitar quantidade disponível.
-   Implementar pontuação de sinergia e equilíbrio funcional.
-   Criar validação final.
-   Exportar decklist.

### Sprint 5 --- Interface

**Objetivo:** tornar o fluxo acessível em uma aplicação local.

-   Criar interface Streamlit.
-   Priorizar o fluxo de deck builder, análise e revisão visual; a pesquisa
    detalhada da coleção permanece disponível pela CLI.
-   Separar a geração e a visualização do deck em páginas navegáveis; a lista
    do deck deve oferecer categorias e prévia visual de cartas.
-   Exibir cartas, quantidades e justificativas.
-   Permitir exportação.

### Sprint 5.1 --- Estratégia e base de mana

**Objetivo:** aumentar a coerência dos rascunhos antes de integrar um LLM.

-   Criar perfis TOML explicáveis para estratégias de comandantes.
-   Priorizar sinais de sinergia sem substituir filtros de coleção ou regras.
-   Selecionar terrenos considerando cores produzidas, fontes multicoloridas
    e terrenos utilitários.
-   Mostrar imagens das cartas e os sinais usados para priorizar cada uma.
-   Documentar guidelines como metas flexíveis, não regras universais.

Scores de power level ficam para uma etapa posterior: devem ser contextuais,
explicáveis e não excluir cartas automaticamente.

### Sprint 6 --- Agente LLM

**Objetivo:** permitir solicitações em linguagem natural.

-   Integrar API de LLM.
-   Implementar ferramentas e chamadas de função.
-   Criar prompts e limites de atuação.
-   Medir tokens e custos.
-   Testar se o agente respeita integralmente as restrições da coleção.

## 14. Instruções de trabalho para o Codex

Ao trabalhar neste repositório:

1.  Primeiro leia este documento e o `README.md`.
2.  Inspecione o código existente antes de propor alterações.
3.  Não recrie arquivos ou módulos que já existam sem necessidade.
4.  Trabalhe em uma tarefa pequena por vez.
5.  Antes de codificar, apresente brevemente o plano e os arquivos que
    pretende alterar.
6.  Se faltar uma informação essencial --- especialmente o formato real
    do CSV --- pergunte em vez de presumir.
7.  Implemente testes junto com a funcionalidade.
8.  Execute os testes relevantes após cada alteração.
9.  Ao concluir, informe:
    -   O que foi implementado.
    -   Quais arquivos foram criados ou alterados.
    -   Quais comandos foram executados.
    -   Resultado dos testes.
    -   Limitações ou próximos passos.
10. Não avançar para o próximo sprint automaticamente sem verificar os
    critérios de conclusão do sprint atual.

### Restrições importantes

-   Não integrar LLM antes de a camada de coleção e as regras centrais
    estarem funcionais.
-   Não implementar funcionalidades futuras apenas por parecerem
    interessantes.
-   Não usar cartas externas para completar decks.
-   Não assumir que uma carta é legal no Commander sem consultar os
    dados/regras vigentes.
-   Não realizar alterações destrutivas em dados do usuário.
-   Não afirmar que testes passaram sem executá-los.

## 15. Primeira tarefa a executar

**Comece somente pela Sprint 1.**

A primeira ação deve ser verificar o estado do repositório e perguntar
pelo CSV de exportação da coleção caso ele ainda não esteja disponível.

Em seguida:

1.  Inspecionar o CSV sem modificar o arquivo original.
2.  Apresentar um diagnóstico de colunas, exemplos de registros e campos
    relevantes.
3.  Propor o mapeamento para `cards` e `collection_printings`.
4.  Só então implementar o primeiro importador e os testes.

**Não implemente ainda Streamlit, integração LLM, embeddings, LangGraph,
otimização avançada ou infraestrutura Docker.**

------------------------------------------------------------------------

## Definição de sucesso do primeiro marco

O primeiro marco estará concluído quando:

-   [ ] O projeto executar localmente em ambiente virtual.
-   [ ] O banco SQLite for criado automaticamente.
-   [ ] O CSV real for importado.
-   [ ] As quantidades forem preservadas corretamente.
-   [ ] Reimportar o mesmo arquivo não gerar duplicações indevidas.
-   [ ] For possível pesquisar uma carta e consultar sua quantidade.
-   [ ] Os testes automatizados relevantes passarem.
-   [ ] O procedimento estiver documentado no README.
