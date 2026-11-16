"""Evidence-backed deck diagnostics for refinement reports."""

from collections import Counter, defaultdict
from dataclasses import dataclass

from mana_architect.deck_pipeline.contracts import DeckProposal, Role
from mana_architect.deck_pipeline.preliminary_composer import PreparedComposition


@dataclass(frozen=True)
class CardEvidence:
    card_id: str
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class InteractionCoverage:
    action: str
    object_types: tuple[str, ...]
    zone: str
    timing: str
    cards: tuple[CardEvidence, ...]


@dataclass(frozen=True)
class PackageDependency:
    benefit_event_id: str
    package_ids: tuple[str, ...]
    shared_member_card_ids: tuple[str, ...]


@dataclass(frozen=True)
class RepeatedRoleSubtype:
    role: Role
    subtype: str
    cards: tuple[CardEvidence, ...]


@dataclass(frozen=True)
class RefinementDiagnostics:
    role_overlap: tuple[tuple[str, tuple[Role, ...]], ...]
    early_ramp: tuple[CardEvidence, ...]
    interaction_coverage: tuple[InteractionCoverage, ...]
    repeated_effects: tuple[InteractionCoverage, ...]
    repeated_role_subtypes: tuple[RepeatedRoleSubtype, ...]
    strategy_roles: tuple[tuple[str, str, tuple[str, ...]], ...]
    protection: tuple[CardEvidence, ...]
    recursion: tuple[CardEvidence, ...]
    package_dependencies: tuple[PackageDependency, ...]


def assess_deck(proposal: DeckProposal, prepared: PreparedComposition) -> RefinementDiagnostics:
    quantities = Counter({entry.card_id: entry.quantity for entry in proposal.main_deck})
    oracle = {card.card_id: card for card in prepared.gate.snapshot.cards}
    overlap = []
    early_ramp = []
    interaction = defaultdict(lambda: defaultdict(set))
    role_subtypes = defaultdict(list)
    strategy_roles = defaultdict(set)
    protection = []
    recursion = []
    for card_id in sorted(quantities):
        candidate = prepared.cards[card_id]
        roles = tuple(profile.role for profile in candidate.role_profiles if profile.qualifies)
        if len(roles) > 1:
            overlap.append((card_id, roles))
        for profile in candidate.role_profiles:
            if profile.qualifies and profile.role != Role.LANDS:
                for subtype in profile.subtypes:
                    role_subtypes[profile.role, subtype].append(CardEvidence(card_id, profile.evidence_ids))
        mv = oracle[card_id].mana_value
        if mv is not None and 0 <= mv <= 2 and Role.RAMP in roles:
            ramp = next(profile for profile in candidate.role_profiles if profile.role == Role.RAMP)
            early_ramp.append(CardEvidence(card_id, ramp.evidence_ids))

        evaluations = candidate.evaluations
        if evaluations is None:
            continue
        protection_ids = set()
        recursion_ids = set()
        if evaluations.interaction is not None:
            for variant in evaluations.interaction.variants:
                for effect in variant.effects:
                    if effect.interaction_type is not None:
                        key = (effect.action, tuple(sorted(effect.object_types)), effect.affected_zone, str(effect.timing))
                        interaction[key][card_id].add(effect.effect_id)
                    if effect.action == "protect" or "protection" in effect.mechanical_tags:
                        protection_ids.add(effect.effect_id)
        for profile in evaluations.commander_profiles:
            for affinity in profile.strategy_affinities:
                for role in affinity.roles:
                    strategy_roles[affinity.strategy_id, role].add(card_id)
            for capability in profile.mechanical_capabilities:
                if capability.id == "protect_creature":
                    protection_ids.add(capability.event_key)
                if capability.id in {"recover_to_hand", "return_from_graveyard"} and capability.source_zone == "graveyard":
                    recursion_ids.add(capability.event_key)
        if protection_ids:
            protection.append(CardEvidence(card_id, tuple(sorted(protection_ids))))
        if recursion_ids:
            recursion.append(CardEvidence(card_id, tuple(sorted(recursion_ids))))

    coverage = tuple(
        InteractionCoverage(
            *signature, tuple(CardEvidence(card_id, tuple(sorted(ids))) for card_id, ids in sorted(cards.items())),
        )
        for signature, cards in sorted(interaction.items())
    )
    paths = defaultdict(list)
    for package in proposal.package_assessments:
        if package.package_id is not None and package.modeled_requirements_supported:
            for event_id in package.benefit_event_ids:
                paths[event_id].append(package)
    dependencies = tuple(
        PackageDependency(
            event_id, tuple(sorted(package.package_id for package in packages)),
            tuple(sorted(set.intersection(*(set(package.member_card_ids) for package in packages)))),
        )
        for event_id, packages in sorted(paths.items())
    )
    return RefinementDiagnostics(
        tuple(overlap), tuple(early_ramp), coverage,
        tuple(effect for effect in coverage if sum(quantities[card.card_id] for card in effect.cards) > 1),
        tuple(RepeatedRoleSubtype(role, subtype, tuple(cards)) for (role, subtype), cards in sorted(role_subtypes.items())
              if sum(quantities[card.card_id] for card in cards) > 1),
        tuple((strategy, role, tuple(sorted(cards))) for (strategy, role), cards in sorted(strategy_roles.items())),
        tuple(protection), tuple(recursion), dependencies,
    )
