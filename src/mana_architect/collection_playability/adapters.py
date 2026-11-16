"""Read owned card inputs without requiring a commander."""

from collections import defaultdict
from dataclasses import asdict, dataclass, replace

from mana_architect.card_advantage import Profile, compile_profile
from mana_architect.collection_playability.boundary_extraction import extract_crossing_activations
from mana_architect.collection_playability.contracts import (
    Coverage, EvaluationStatus, EvidenceReference, PlayabilityContext,
    PlayabilityProfile, PlayabilityProvenance,
)
from mana_architect.collection_playability.mechanics import evaluate_mechanics
from mana_architect.deck_pipeline.adapters import ENGINE_VERSIONS, parse_card_snapshot
from mana_architect.deck_pipeline.contracts import PairwiseSynergyAnalysis, artifact_digest
from mana_architect.deck_pipeline.inventory import CardSnapshot, CollectionSnapshot, OwnedPrinting
from mana_architect.deck_pipeline.pairwise_synergy import pairwise_catalog_fingerprint
from mana_architect.interaction_evaluation_engine import build_interaction_profile
from mana_architect.interaction_profile import InteractionProfile
from mana_architect.oracle_text import PARSER_VERSION
from mana_architect.oracle_text import OracleParse
from mana_architect.ramp_evaluator import RampProfile, evaluate_ramp
from mana_architect.strategy_synergy import SynergyCatalogContext
from mana_architect.synergy_capabilities import CAPABILITY_VERSION, MechanicalProfile, extract_mechanical_capabilities


class IncompatibleInputError(ValueError):
    status = EvaluationStatus.INCOMPATIBLE_CONTRACT


@dataclass(frozen=True)
class CollectionCardInputs:
    profile: PlayabilityProfile
    card: CardSnapshot | None
    printings: tuple[OwnedPrinting, ...] = ()
    mechanics: MechanicalProfile | None = None
    ramp: RampProfile | None = None
    card_advantage: Profile | None = None
    interaction: InteractionProfile | None = None
    parsed: OracleParse | None = None


class CollectionInputs:
    def __init__(
        self,
        context: PlayabilityContext,
        snapshot: CollectionSnapshot,
        catalog: SynergyCatalogContext,
        *,
        pairwise: PairwiseSynergyAnalysis | None = None,
    ):
        if context.scope != "collection" or context.collection_snapshot_id != snapshot.snapshot_id:
            raise IncompatibleInputError("Collection context and snapshot must match.")
        if context.engine_versions != ENGINE_VERSIONS:
            raise IncompatibleInputError("Engine versions do not match the installed evaluators.")
        known = {strategy.id for strategy in catalog.strategies.strategies}
        if set(context.strategy_ids) - known:
            raise IncompatibleInputError("Unknown strategy ID.")
        self._context = context
        self._snapshot = snapshot
        self._catalog = catalog
        self._rule_catalog_fingerprint = pairwise_catalog_fingerprint(catalog)
        self._catalog_fingerprint = artifact_digest({
            "rule_catalog": self._rule_catalog_fingerprint,
            "rule_version": catalog.rule_version,
            "scoring": asdict(catalog.scoring),
        })
        self._cards = {item.card_id: item for item in snapshot.cards}
        self._printings = defaultdict(list)
        for row in snapshot.printings:
            self._printings[row.card_id].append(row)
        self._pairwise_evidence = defaultdict(list)
        self._pairwise_fingerprint = None
        if pairwise is not None:
            self._bind_pairwise(pairwise)

    @property
    def unlinked_printings(self) -> tuple[OwnedPrinting, ...]:
        return tuple(self._printings.get(None, ()))

    def analyze(self, card_id: str) -> CollectionCardInputs:
        """Return native inputs together with Phase 1 mechanical and applicability findings."""
        inputs = self.card(card_id)
        return replace(inputs, profile=evaluate_mechanics(inputs, self._catalog.strategies))

    def _bind_pairwise(self, pairwise: PairwiseSynergyAnalysis) -> None:
        provenance = pairwise.provenance
        if (
            not self._context.commander_id
            or self._context.synergy_artifact_version != pairwise.analysis_id
            or provenance.commander_card_ids != (self._context.commander_id,)
            or provenance.collection_snapshot_id != self._snapshot.snapshot_id
            or provenance.engine_versions != ENGINE_VERSIONS
            or provenance.oracle_parser_version != PARSER_VERSION
            or provenance.capability_extractor_version != CAPABILITY_VERSION
            or provenance.strategy_catalog_version != self._catalog.strategies.version
            or provenance.rule_catalog_fingerprint != self._rule_catalog_fingerprint
            or pairwise.rule_catalog_version != self._catalog.rule_version
        ):
            raise IncompatibleInputError("Published pairwise provenance differs from the collection context.")
        if len({edge.edge_id for edge in pairwise.edges}) != len(pairwise.edges):
            raise IncompatibleInputError("Published pairwise edge IDs must be unique.")
        fingerprint = artifact_digest(pairwise.to_json())
        if self._context.synergy_artifact_fingerprint != fingerprint:
            raise IncompatibleInputError("Published pairwise payload differs from the expected fingerprint.")
        self._pairwise_fingerprint = fingerprint
        for edge in pairwise.edges:
            endpoints = {edge.source_card_id, edge.target_card_id}
            if endpoints - self._cards.keys() or {item.card_id for item in edge.evidence} != endpoints:
                raise IncompatibleInputError("Published edge lacks owned endpoints or their evidence.")
            references = []
            for item in edge.evidence:
                source_card = self._cards[item.card_id]
                source = getattr(source_card, item.source_field, None)
                if (item.span_start is None or item.span_end is None or source is None
                    or source[item.span_start:item.span_end] != item.source_text):
                    raise IncompatibleInputError("Published edge evidence does not match the snapshot.")
                references.append(EvidenceReference(
                    item.card_id, "pairwise", self._pairwise_fingerprint,
                    edge.edge_id, item.source_field, item.span_start, item.span_end, item.source_text,
                ))
            for card_id in endpoints:
                self._pairwise_evidence[card_id].extend(references)

    def card(self, card_id: str) -> CollectionCardInputs:
        card = self._cards.get(card_id)
        printings = tuple(self._printings.get(card_id, ()))
        provenance = PlayabilityProvenance(
            self._snapshot.snapshot_id, self._snapshot.legality_data_as_of,
            artifact_digest(asdict(card)) if card else None, PARSER_VERSION,
            CAPABILITY_VERSION, self._catalog_fingerprint, self._pairwise_fingerprint,
        )
        if card is None or card.oracle_text is None or not card.type_line:
            reason = "unlinked_card_identity" if card is None else "missing_oracle_data"
            profile = PlayabilityProfile(
                card_id, self._context, EvaluationStatus.INSUFFICIENT_EVIDENCE,
                provenance=provenance, reasons=(reason,),
            )
            return CollectionCardInputs(profile, card, printings)

        try:
            parsed = parse_card_snapshot(card, self._catalog)
            mechanics = extract_mechanical_capabilities(parsed, name=card.name, type_line=card.type_line)
            ramp = evaluate_ramp(card_id, card.type_line, card.mana_cost, parsed)
            mechanics, ramp, scoped = extract_crossing_activations(card, parsed, mechanics, ramp, self._catalog.oracle)
            advantage = compile_profile(card_id, card.type_line, card.mana_cost, parsed)
            interaction = build_interaction_profile(
                card_id, parsed, type_line=card.type_line, mana_cost=card.mana_cost,
                mana_value=card.mana_value, oracle_keywords=card.keywords, layout=card.layout,
            )
            evidence, coverage = self._coverage(card, mechanics, ramp, advantage, interaction, scoped)
        except Exception as error:
            profile = PlayabilityProfile(
                card_id, self._context, EvaluationStatus.EVALUATION_FAILED,
                provenance=provenance, reasons=(type(error).__name__,),
            )
            return CollectionCardInputs(profile, card, printings)

        profile = PlayabilityProfile(
            card_id, self._context, coverage=coverage, provenance=provenance, evidence=evidence,
        )
        return CollectionCardInputs(profile, card, printings, mechanics, ramp, advantage, interaction, parsed)

    def _coverage(self, card, mechanics, ramp, advantage, interaction, scoped):
        evidence = []
        coverage = []

        def record(source, version, spans, limitations=()):
            references = tuple(
                EvidenceReference(
                    card.card_id, source, version, record_id, field, start, end, text,
                    *scoped.get((source, record_id, start, end), (None, None)),
                )
                for record_id, field, start, end, text in spans
            )
            if any((getattr(card, item.source_field) or "")[item.start:item.end] != item.text for item in references):
                raise ValueError("Native evidence does not match the Oracle snapshot.")
            evidence.extend(references)
            coverage.append(Coverage(
                source, EvaluationStatus.UNSUPPORTED if limitations else EvaluationStatus.EVALUATED,
                tuple(sorted({item.evidence_id for item in references})), limitations,
            ))

        record("mechanical", CAPABILITY_VERSION, (
            (record_id, span.source_field, span.start, span.end, span.oracle_span)
            for record_id, span in (
                *((cap.id, cap.evidence) for cap in mechanics.capabilities),
                *(("unresolved", span) for span in mechanics.unknowns),
            )
        ), ("unresolved_oracle_spans",) if mechanics.unknowns else ())
        record("ramp", ENGINE_VERSIONS.ramp, (
            (line.event.evidence.ability_id, "oracle_text", line.event.evidence.start,
             line.event.evidence.end, line.event.evidence.oracle_span)
            for line in ramp.lines
        ), ("multiple_faces",) if "//" in card.type_line else ())
        record("card_advantage", ENGINE_VERSIONS.card_advantage, (
            (line.draw.ability_id, "oracle_text", start, end, card.oracle_text[start:end])
            for line in advantage.lines for start, end in (line.draw.source_span,)
        ), (advantage.unsupported_reason,) if advantage.unsupported_reason else ())
        record("interaction", ENGINE_VERSIONS.interaction, (
            (effect.effect_id, "oracle_text", effect.evidence.start,
             effect.evidence.end, effect.evidence.oracle_span)
            for variant in interaction.variants for effect in variant.effects
        ), interaction.warnings)
        pairwise = self._pairwise_evidence.get(card.card_id, ())
        evidence.extend(pairwise)
        coverage.extend((
            Coverage("strategy_synergy", EvaluationStatus.NOT_EVALUATED, limitations=("requires_deck_context",)),
            Coverage(
                "pairwise", EvaluationStatus.EVALUATED if pairwise else EvaluationStatus.NOT_EVALUATED,
                tuple(sorted({item.evidence_id for item in pairwise})),
                ("published_positive_evidence_only",) if self._pairwise_fingerprint else ("artifact_unavailable",),
            ),
        ))
        return tuple(evidence), tuple(coverage)
