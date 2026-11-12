# Estado do projeto e roadmap

- **Revisado em:** 2026-11-12
- **Revisão de código verificada:** `5533668`
- **Idioma da documentação de produto:** português

Este é o resumo canônico do produto e de seu roadmap conhecido. O estado abaixo
foi conferido contra o código, a configuração e os testes na revisão indicada;
não é uma promessa de qualidade competitiva nem substitui essas fontes.

## 1. Objetivo do projeto

O Mana Architect é uma aplicação local para organizar uma coleção física de
Magic: The Gathering e apoiar a construção e análise de decks Commander usando
somente cartas disponíveis nessa coleção. A coleção e o estoque são a fonte de
verdade sobre propriedade; dados externos enriquecem cartas e ajudam a verificar
legalidade, mas não autorizam incluir cartas que o usuário não possui.

O sistema deve manter regras de seleção e validação determinísticas, resultados
reproduzíveis e explicações rastreáveis. Classificações e scores estratégicos
são heurísticas revisáveis, não decisões finais de legalidade nem garantias de
força de um deck.

## 2. Roadmap

### Estado das entregas

As capacidades abaixo estão implementadas no código atual:

1. Importação e consulta local da coleção.
2. Sincronização de dados de cartas e seleção/validação básica de Commander.
3. Geração, análise e exportação determinística de rascunhos.
4. Interface local para construção, visualização e sugestões.
5. Catálogo versionado, classificação explicável, afinidades e scorer.
6. Integração opcional do catálogo à geração automática, mantendo o gerador
   legado como padrão.

A integração registrada em
[catalog-guided-deck-generation.md](./catalog-guided-deck-generation.md) está
concluída. **Não há uma próxima sprint aprovada nos documentos atuais.**
As opções abaixo são propostas de backlog para priorização, sem prazo nem
compromisso de implementação:

| Ordem sugerida | Trabalho candidato | Estado conhecido |
|---|---|---|
| 1 | Editar manualmente a lista gerada — incluir/remover/substituir cartas e revalidar | Explicitamente adiado; a lista não tem edição direta |
| 2 | Salvar e administrar múltiplos decks e suas versões | Não implementado; decks não têm persistência própria |
| 3 | Suportar comandantes Partner/Background | Não implementado |
| 4 | Avaliar e calibrar heurísticas com casos e decks de referência | Pendente; pesos atuais são iniciais e não calibrados empiricamente |
| 5 | Interface opcional em linguagem natural com LLM | Ideia do documento inicial; não implementada nem aprovada como próxima sprint |

A ordem é apenas uma sugestão para discussão. Não atribua números de sprint nem
trate esses itens como compromisso até que o usuário escolha o próximo escopo.

## 3. O que já está implementado

### Coleção, dados e CLI

- Coleção local em SQLite, com identidade lógica de carta (`oracle_id`) separada
  das impressões físicas e quantidades.
- Importação de CSV exportado pelo **ManaBox**, com atualização idempotente por
  impressão e resumo de linhas, duplicatas e quantidades. Parsers para Moxfield
  e Archidekt não estão documentados como suportados.
- Sincronização do Scryfall Bulk Data com cache local e associação de impressões
  quando há correspondência; pesquisa e processamento da coleção permanecem
  locais.
- CLI para `import-csv`, `sync-scryfall`, `search`, `commanders`,
  `validate-deck`, `build-deck` e `analyze-deck`.

Fontes: `src/mana_architect/{models,database,manabox,importer,scryfall,collection,commanders,cli}.py`;
testes de importação, sincronização, seleção de comandante e CLI.

### Regras e geração Commander

- Validação de tamanho (100 cartas incluindo comandante), comandante, identidade
  de cor, singleton com exceções reconhecidas, disponibilidade física e
  legalidade conforme o snapshot local.
- Estados de validação distinguem lista válida, inválida e incompleta quando
  faltam dados.
- Gerador determinístico de rascunhos; respeita estoque, legalidade, identidade
  de cor e composição, com seleção de terrenos orientada à cobertura de cores.
- Modo legado usa o perfil inicial de Anowon em `config/strategy_profiles.toml`
  e continua sendo o padrão.
- Modo opcional do catálogo está disponível na CLI (`build-deck --strategy`) e
  no Construtor. Exige afinidade explícita comandante/estratégia; usa
  classificações atuais e overrides, informa classificações ausentes e não
  reclassifica nem persiste classificações durante a geração.
- Análise da decklist, curva, funções heurísticas e exportação JSON/TXT.

Fontes: `src/mana_architect/{validation,deckbuilding,strategy,cli}.py`;
testes de validação e geração.

### Catálogo e interface

- Configuração versionada em JSON com **15 estratégias, 106 tags, 20 funções e
  10 regras de classificação**.
- Classificação por regras determinísticas com evidências, fingerprints e
  revisão indicada; overrides por carta e afinidades de comandante são
  persistidos separadamente.
- Persistência aditiva em SQLite de versões de configuração, execuções,
  classificações, evidências, correções manuais e afinidades.
- Scorer determinístico que explica componentes e motivos; sugestões filtram
  por coleção, legalidade e identidade de cor.
- Interface Streamlit local com páginas de Construtor, Deck e Sugestões;
  afinidades e correções de classificação podem ser revistas na interface.
- Reclassificação da coleção é ação explícita do usuário, não ocorre ao abrir a
  aplicação.

Fontes: `config/*.json`, `src/mana_architect/{catalog,classification,classification_repository,commander_affinity,overrides,synergy,suggestions,streamlit_app}.py`;
testes correspondentes.

## 4. O que falta implementar ou decidir

### Lacunas confirmadas

- Edição manual da decklist gerada na interface.
- Persistência e gerenciamento de decks salvos, múltiplas listas e histórico de
  versões.
- Regras de comandantes parceiros/backgrounds.
- Interface de linguagem natural/LLM.
- Validação empírica e calibração dos pesos, limites e resultados do scorer.

Os quatro primeiros itens não devem ser considerados implicitamente aprovados
para a próxima sprint. O último é necessário antes de tratar os scores como
calibrados; atualmente são heurísticas.

### Limitações operacionais atuais

- Legalidade e banlist dependem dos dados sincronizados localmente. Uma
  validação aprovada não garante que o snapshot corresponda à banlist vigente
  hoje; sincronize e confira a data exibida.
- Regras textuais não compreendem contexto completo nem sinergia entre cartas.
  Combos infinitos exigem curadoria explícita.
- Geração guiada depende de afinidade cadastrada e classificações atuais; cartas
  sem classificação atual são omitidas e informadas.
- O gerador monta um rascunho heurístico, não uma lista competitiva garantida.

## 5. Informações relevantes para manutenção e auditoria

### Stack e dados

- Python 3.11+, SQLAlchemy 2 e SQLite; Streamlit é dependência opcional da
  interface; Pytest é usado nos testes.
- Banco pessoal, arquivos CSV, `.env`, cache do Scryfall e decks exportados são
  excluídos do Git por padrão. Não usar nem alterar o banco real para testes;
  use fixtures e bancos temporários.
- `sync-scryfall` requer internet. O scorer e as sugestões operam localmente e
  não chamam um LLM ou serviço remoto.
- Operações persistentes de classificação devem respeitar transações e o
  procedimento em [classification-storage.md](./classification-storage.md).

### Orientação para agentes de IA

1. Leia este documento e o [índice da documentação](./README.md); depois leia o
   documento técnico relacionado ao escopo.
2. Confirme o comportamento no código, na configuração e nos testes. Este resumo
   não autoriza assumir que uma proposta está implementada.
3. Preserve coleção, legalidade, identidade de cor, disponibilidade,
   determinismo e proveniência de classificações.
4. Não faça alterações destrutivas, sincronizações externas ou reclassificação
   do banco pessoal sem autorização explícita.
5. Ao concluir uma mudança, registre os arquivos/evidências, testes executados,
   limitações e atualização documental necessária. Atualize o roadmap somente
   quando uma decisão de produto for tomada.

### Evidência desta revisão

- Revisão de código: `5533668` (`feat: integrate strategy catalog into deck generation`).
- Testes: `python -m pytest -q` — **131 passed**.
- Exercício manual em Streamlit com banco temporário: modo catálogo, estratégia
  Tokens, resultado **100/100**, 98 terrenos e validação aprovada. O banco
  temporário foi removido após o teste.

Uma mudança de comportamento invalida a evidência correspondente: atualize a
revisão, execute os testes relevantes e registre novamente o resultado.
