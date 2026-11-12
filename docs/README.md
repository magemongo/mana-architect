# Índice da documentação

## Comece por aqui

- [Estado do projeto e roadmap](./project-status.md) — visão canônica do
  objetivo, estado implementado, lacunas conhecidas, próximos candidatos e
  evidências de auditoria.
- [README do repositório](../README.md) — instalação, quickstart e comandos de
  uso.

## Referência técnica atual

- [Diretrizes de deckbuilding](./deckbuilding-guidelines.md) — princípios e
  heurísticas usados pelo gerador.
- [Scoring de sinergia](./synergy-scoring.md) — fórmula, filtros, integração e
  limitações do scorer.
- [Persistência do catálogo](./classification-storage.md) — tabelas, versões,
  transações e cuidados com reclassificação.
- [Integração do catálogo à geração de decks](./catalog-guided-deck-generation.md)
  — escopo, critérios de aceite e evidências da integração.

## Documentos históricos

Os documentos em [`history/`](./history/README.md) preservam as diretrizes e
planos originais. Eles ajudam a entender decisões e requisitos de origem, mas
seus roadmaps e prompts não são instruções de trabalho atuais.

## Hierarquia de fontes

1. O código, os testes e os arquivos versionados em `config/` determinam o
   comportamento executável.
2. [`project-status.md`](./project-status.md) é a referência para o estado do
   produto e para o roadmap atualmente reconhecido; não substitui a inspeção do
   código.
3. Os documentos técnicos desta página explicam contratos e operação de áreas
   específicas.
4. Documentos históricos servem como contexto, não como backlog aprovado.

Ao registrar uma mudança, atualize o documento técnico diretamente relacionado
e, se ela alterar o escopo, as lacunas ou o roadmap, atualize também o estado do
projeto com a evidência correspondente.
