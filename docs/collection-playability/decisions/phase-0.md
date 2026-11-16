# Phase 0 decisions

Collection analysis uses the immutable deck pipeline collection snapshot and Oracle identities. Owned printing rows and unlinked inventory remain visible. Bind a `PlayabilityContext` and a `SynergyCatalogContext` to a snapshot with `CollectionInputs`, then call `card(oracle_id)` to retrieve native engine profiles and a serializable `PlayabilityProfile`. The adapter invokes the existing parser, capability extractor, ramp, card advantage, and interaction engines without a commander. Strategy Synergy requires a commander and is recorded as `not_evaluated`.

Snapshot, parser, extractor, native evaluator, and catalog provenance bind the input to a reproducible result. Evidence references identify the source version, card, record, and exact span in the snapshot. Native limitations and unresolved clauses are recorded per engine. Missing card identity or Oracle data yields `insufficient_evidence`. Engine failure yields `evaluation_failed`. Binding mismatches raise `IncompatibleInputError` with an `incompatible_contract` status. Successful input collection yields `not_evaluated` for the overall profile until an evaluator assigns a playability classification.

An explicitly supplied `PairwiseSynergyAnalysis` can contribute evidence for the named commander. The publisher records `artifact_digest(analysis.to_json())` alongside the input-based analysis ID. The consumer supplies that independently recorded digest as `PlayabilityContext.synergy_artifact_fingerprint`. The adapter compares it with the received payload and checks the artifact ID, commander, snapshot, engine and rule versions, catalog fingerprint, and evidence spans. The existing Phase 2 and 3 analyses are bounded by their candidate pool, work limits, and supported mechanics. Missing relations leave niche viability open. Package ingestion and other deck overlays belong to later phases. Phase 4 composition and Phase 5 refinement consume the existing pipeline artifacts independently.

Collection comparisons use owned identities. An asserted comparison cites evidence from both cards. Coverage records cite their native source and the profile card. Pairwise coverage may cite both endpoints of an edge involving that card. Global reference coverage requires a separate versioned index. The local Scryfall bulk cache can supply pinned fixtures, and the collection database holds owned card metadata. An asserted global uniqueness value requires matching reference universe versions in the context and provenance. The current profile contract reserves bulk classification and review eligibility for the later classifier.

## Golden fixture plan

Start with 48 independently reviewed cards. Include the seven canonical examples in these groups.

| Cards | Properties to verify |
| --- | --- |
| 10 ramp and card advantage | Printed and additional costs, repeatability, effect bundles, and conditional draws or mana |
| 10 interaction | Target and zone restrictions, timing, and applicability when a legal target exists |
| 12 strategy and niche cases | Recursion, fodder, specialized setup, and high-impact effects with material conditions |
| 10 comparative cases | Exact and conditional substitutes, relevant type differences, tradeoffs, and collection versus global evidence |
| 6 uncertain or unsupported cases | Unusual outcomes, unresolved modes, and unsupported mechanics that require abstention |

Reuse applicable records from `tests/fixtures/phase23/cards.json`. Pin new cards by Oracle ID, source snapshot checksum, printed metadata, and rules interpretation. Author expectations independently of evaluator output and record unresolved expectations explicitly. Keep a holdout for later calibration. The Phase 0 smoke check read all 58 existing Golden card records through the adapter.

## Baseline

Run `.venv/Scripts/python.exe -B -m pytest -q -p no:cacheprovider --ignore=tests/test_streamlit_app.py`. The pre-change run passed 468 tests. The full-suite attempt during discovery stalled in Streamlit tests. The Phase 0 gate uses the contract, adapter, and existing deck pipeline suites. The Phase 2–3 Golden baseline recovered 35 of 37 expected relations and 27 of 30 expected packages.

The initial post-change run passed 480 tests, including 12 Phase 0 cases. Both focused Phase 2–3 Golden gates passed with the same recovery counts. The 58-card adapter smoke check had zero input failures. The independent audit later identified five public contract defects, which the contract and adapter now reject. Playability accuracy is measured in the later CPA phases.

After the audit fixes, the Phase 0 gate passed. The rerun passed 496 tests, including 16 Phase 0 cases and the existing Phase 2–3 Golden gates. The audit rejected all 12 invalid inputs while accepting the unchanged published artifact. The seven canonical cards retained 15 of 15 exact evidence references and produced identical profiles with reversed input order. The rerun deselected the two Streamlit AppTests that had previously timed out in Windows socket initialization.
