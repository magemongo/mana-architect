# Persistência do catálogo e classificações

## Migração

`initialize_database(engine)` aplica de forma aditiva as tabelas da classificação
usando `Base.metadata.create_all` e registra `classification-storage-v1` e
`card-classification-overrides-v1` em `schema_migrations`. A segunda versão
cria a tabela de correções manuais vinculadas ao Oracle ID, sem alterar a tabela
de overrides preexistente. O `oracle_id` existente em `cards` continua sendo a
chave canônica; impressões e quantidades físicas permanecem em
`collection_printings`. A inicialização não classifica cartas, altera resultados
existentes nem faz chamadas de rede.

A atualização foi verificada em bancos SQLite temporários vazios e em um banco
legado de teste com dados de carta. O banco real da coleção não deve ser usado
para testes. Antes de executar uma reclassificação real, faça um backup do
arquivo SQLite.

## Reclassificação e transações

`reclassify_cards(session, catalog, config_dir, oracle_ids=None)` classifica
todas as cartas ou somente os Oracle IDs indicados. A função não faz commit:
chame-a dentro de uma transação do chamador:

```python
with Session(engine) as session, session.begin():
    summary = reclassify_cards(session, catalog, config_dir)
```

Uma falha antes do commit deve provocar rollback da transação do chamador. A
reclassificação grava uma nova execução e suas evidências; não apaga o histórico
anterior. Overrides e afinidades são armazenados separadamente. Uma mudança no
texto ou em outro campo usado por regras reclassifica apenas a carta afetada.
Mudanças nas regras ou no catálogo de classificação invalidam os resultados
afetados quando a reclassificação for solicitada. Alterações apenas em
`scoring.json` registram nova versão do catálogo, mas não repetem a classificação.
Cada versão registrada guarda o hash, as versões declaradas, a versão do
normalizador e os conteúdos dos cinco arquivos de configuração.

`save_card_classification_override` exige uma justificativa e grava uma decisão
por carta/alvo. A evidência automática não é modificada nem apagada; o serviço
de sugestão combina ambas ao calcular o resultado. `remove_card_classification_override`
remove somente a decisão manual e restaura a classificação automática.

## Rollback

As alterações do esquema são aditivas. Reverter o código da aplicação não exige
remover tabelas: elas podem permanecer sem uso, preservando overrides, afinidades
e histórico. As escritas da classificação, overrides globais, correções por
carta e afinidades devem ser
agrupadas em `session.begin()`; uma exceção na transação reverte todas as
alterações daquele lote. Não remova as tabelas manualmente como procedimento de
rollback, pois isso apagaria dados persistidos.
