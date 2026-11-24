# Working on Mana Architect

- **Stay within the approved task.** Make routine implementation and verification
  decisions autonomously within that scope. Get explicit owner approval before
  changing architecture, public contracts, persistence schemas, or compatibility.
  Public contracts include Python APIs, CLI options and exit codes, serialized
  fields and evidence IDs, configuration schemas, and documented defaults. Explain
  the impact on existing callers, stored data, and replay before editing.
- **Use the project references.** Start with the [documentation index](docs/README.md).
  The [deck pipeline architecture](docs/deck-building/ARCHITECTURE.md) defines
  component boundaries. Use the implementation guides and applicable decision
  records for phase-specific behavior. Check discrepancies against current code
  and tests and flag unresolved conflicts for the owner.
- **Keep results reproducible and evidence-linked.** Use immutable, versioned
  snapshots, stable ordering, and deterministic tie-breaks. Preserve exact Oracle
  spans, source IDs, rule and catalog versions, prerequisites, and replay provenance.
  Derive golden expectations independently of evaluator output. Report incomplete
  or unsupported results when ownership, legality, mechanics, or prerequisites
  cannot be established.
- **Protect local data.** Get explicit owner approval before accessing or modifying
  real collection data, persistent SQLite databases and sidecars, local decks,
  application caches, or `.env`, and before destructive operations. Test with
  synthetic or pinned inputs, temporary SQLite, copied catalogs, and temporary
  outputs. Routine cleanup of task-owned scratch files needs no separate
  approval. Edit tracked configuration only within the approved task.
- **Verify and hand off.** Follow the [offline baseline](docs/development-baseline.md)
  using the task's actual base revision and reviewed edits. Exercise the
  affected entry points, run focused tests and the applicable broad suite, and
  review the diff for scope and data safety. Report the change, exact test
  commands and outcomes, deviations from the baseline, unresolved issues, and
  any approved compatibility or rollback steps.
