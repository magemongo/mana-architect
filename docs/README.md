# Documentation Index

## Start Here

- [Project status and roadmap](./project-status.md) — canonical overview of the
  product, implemented capabilities, known gaps, and current roadmap.
- [Repository README](../README.md) — installation, quickstart, and usage.

## Current Technical References

- [Deckbuilding guidelines](./deckbuilding-guidelines.md) — generator goals and
  heuristics.
- [Synergy scoring](./synergy-scoring.md) — formula, filters, integration, and
  limitations.
- [Catalog persistence](./classification-storage.md) — schema, versions,
  transactions, and reclassification guidance.
- [Catalog-guided deck generation](./catalog-guided-deck-generation.md) — scope,
  acceptance criteria, and implementation record.

## Historical Documents

Documents in [`history/`](./history/README.md) preserve the original plans and
requirements. They provide context, but their roadmaps and prompts are not
current work instructions.

## Source of Truth

1. Code, tests, and versioned files in `config/` define executable behavior.
2. [`project-status.md`](./project-status.md) tracks product status and the
   recognized roadmap; it does not replace code inspection.
3. Technical references explain the contracts and operation of specific areas.
4. Historical documents provide context, not an approved backlog.

When a change is made, update its directly related technical reference. If it
changes product scope, known gaps, or the roadmap, update the project status
with supporting evidence as well.
