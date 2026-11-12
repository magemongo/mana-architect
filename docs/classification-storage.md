# Catalog and Classification Persistence

## Schema Migration

`initialize_database(engine)` creates classification tables additively through
`Base.metadata.create_all` and records `classification-storage-v1` and
`card-classification-overrides-v1` in `schema_migrations`. The additive
`oracle-parser-results-v1` migration adds the parser document to classification
runs and source offsets to evidence rows. The second migration
adds manual per-card overrides linked to an Oracle ID without changing the
existing global overrides table. `cards.oracle_id` remains the canonical card
key; printings and physical quantities remain in `collection_printings`.
Initialization does not classify cards, change existing results, or make
network requests.

The migration was checked against empty temporary SQLite databases and a legacy
test database containing card data. Never use the real collection database for
tests. Back up the SQLite file before a real reclassification.

## Reclassification and Transactions

`reclassify_cards(session, catalog, config_dir, oracle_ids=None)` classifies all
cards or only the specified Oracle IDs. It does not commit; call it inside a
transaction owned by the caller:

```python
with Session(engine) as session, session.begin():
    summary = reclassify_cards(session, catalog, config_dir)
```

An exception before commit should roll back the caller's transaction.
Reclassification stores a new run and its evidence without deleting history.
Overrides and affinities are stored separately. A change to card text or
another field used by rules reclassifies only the affected card. Rule or
classification-catalog changes invalidate affected results when
reclassification is requested. Changes limited to `scoring.json` register a
new catalog version without repeating classification. Each registered version
stores the hash, declared versions, normalizer version, and contents of the
strategy, tag, role, rule, scoring, Oracle lexicon, and Oracle mechanics
configuration files. New classification runs store their parser output and
parser version; older runs remain readable without a parser document.

On the Catalog page, saving a strategy or role increments catalog and ruleset
versions as applicable. A role's Oracle terms participate in classification
and its fingerprint; editing them makes earlier results stale until explicit
reclassification. Deleting a strategy or role requires confirmation and removes
its current affinities, manual overrides, rules, and configured relationships.
Historical classifications and their configuration snapshots are retained for
auditing.

`save_card_classification_override` requires a reason and stores one decision
per card and target. Automatic evidence is neither changed nor deleted; the
Suggestions service combines it with the manual decision when resolving the
result. `remove_card_classification_override` removes only the manual decision
and restores the automatic classification.

## Rollback

Schema changes are additive. Reverting application code does not require
dropping tables; they can remain unused while preserving overrides, affinities,
and history. Classification, global overrides, per-card overrides, and
affinity writes should be grouped in `session.begin()`. An exception rolls back
all writes in that transaction. Do not manually drop tables as a rollback step;
that would delete persisted data.
