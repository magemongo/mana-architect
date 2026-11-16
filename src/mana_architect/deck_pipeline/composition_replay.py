"""Persist the inputs needed to refine the exact deck from a compose JSON file."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path

from mana_architect.deck_pipeline.candidate_pool import CandidatePoolConfig, PoolConstraints, RolePolicy
from mana_architect.deck_pipeline.composition_utility import CompositionConfig
from mana_architect.deck_pipeline.contracts import (
    CandidatePool, CompositionCapacity, CompositionProvenance, DeckBuildingContext, DeckEntry, DeckProposal,
    EngineVersions, PackageAnalysis, PairwiseSynergyAnalysis, PrintingAllocation, Role, SCHEMA_VERSION,
    StrategyPreference, ValidationIssue, artifact_digest,
)
from mana_architect.deck_pipeline.eligibility import EligibilityGate
from mana_architect.deck_pipeline.pairwise_synergy import PairwiseConfig
from mana_architect.deck_pipeline.preliminary_composer import build_preliminary_artifacts
from mana_architect.deck_pipeline.synergy_packages import PackageConfig
from mana_architect.strategy_synergy import SynergyCatalogContext


class ReplayError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.issue = ValidationIssue(code, message)


@dataclass(frozen=True)
class CompositionReplay:
    context: DeckBuildingContext
    constraints: PoolConstraints
    composition_config: CompositionConfig
    pool_config: CandidatePoolConfig = CandidatePoolConfig()
    pairwise_config: PairwiseConfig = PairwiseConfig()
    package_config: PackageConfig = PackageConfig()
    version: str = "composition-replay-v1"

    def __post_init__(self) -> None:
        if self.version != "composition-replay-v1":
            raise ValueError(f"Unsupported replay version {self.version!r}.")

    def build(
        self, gate: EligibilityGate, catalog: SynergyCatalogContext,
    ) -> tuple[CandidatePool, PairwiseSynergyAnalysis, PackageAnalysis] | DeckProposal:
        return build_preliminary_artifacts(
            gate, catalog, constraints=self.constraints, pool_config=self.pool_config,
            pairwise_config=self.pairwise_config, package_config=self.package_config,
        )

    def manifest(self, proposal: DeckProposal) -> dict[str, object]:
        return {"inputs": asdict(self), "fingerprint": self._fingerprint(proposal)}

    def _fingerprint(self, proposal: DeckProposal) -> str:
        return artifact_digest((
            asdict(self), proposal.schema_version, proposal.proposal_id, proposal.run_id, proposal.proposal_status,
            proposal.commander_card_ids, asdict(proposal.provenance), asdict(proposal.capacity),
            [asdict(entry) for entry in proposal.main_deck], [asdict(entry) for entry in proposal.command_zone],
        ))


def _load_entries(entries: list[dict]) -> tuple[DeckEntry, ...]:
    result = []
    for item in entries:
        card_id = item["card_id"]
        if not isinstance(card_id, str) or not card_id.strip():
            raise ValueError("A deck entry requires an Oracle ID.")
        allocations = tuple(sorted(
            (PrintingAllocation(**row) for row in item["printings"]), key=lambda row: row.printing_id,
        ))
        if any(type(row.printing_id) is not int for row in allocations):
            raise ValueError("Printing IDs must be integers.")
        result.append(DeckEntry(card_id, item["quantity"], allocations, item.get("name")))
    return tuple(sorted(result, key=lambda item: item.card_id))


def read_saved_proposal(path: Path) -> tuple[DeckProposal, CompositionReplay]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise ReplayError("unreadable_proposal", f"Could not read proposal JSON: {error}") from error
    if not isinstance(payload, dict):
        raise ReplayError("invalid_proposal_json", "Expected a Phase 4 proposal object.")
    if "replay" not in payload:
        raise ReplayError("missing_replay_manifest", "Run compose again to create a proposal with replay inputs.")
    try:
        if payload["schema_version"] != SCHEMA_VERSION or payload["proposal_status"] != "valid":
            raise ValueError("Expected a valid Phase 4 proposal using the current schema.")
        manifest = payload["replay"]
        inputs = manifest["inputs"]
        context = dict(inputs["context"])
        context["commander_card_ids"] = tuple(context["commander_card_ids"])
        context["engine_versions"] = EngineVersions(**context["engine_versions"])
        context["strategy_preferences"] = tuple(StrategyPreference(**item) for item in context["strategy_preferences"])
        pool = dict(inputs["pool_config"])
        pool["policies"] = tuple(RolePolicy(**{**item, "role": Role(item["role"])}) for item in pool["policies"])
        config = dict(inputs["composition_config"])
        config["role_targets"] = tuple((Role(role), target) for role, target in config["role_targets"])
        replay = CompositionReplay(
            DeckBuildingContext(**context), PoolConstraints(**inputs["constraints"]), CompositionConfig(**config),
            CandidatePoolConfig(**pool), PairwiseConfig(**inputs["pairwise_config"]),
            PackageConfig(**inputs["package_config"]), inputs["version"],
        )
        proposal = DeckProposal(
            payload["proposal_id"], payload["run_id"], tuple(sorted(payload["commander_card_ids"])),
            _load_entries(payload["main_deck"]), command_zone=_load_entries(payload["command_zone"]),
            proposal_status="valid", provenance=CompositionProvenance(**payload["provenance"]),
            capacity=CompositionCapacity(**payload["capacity"]),
        )
        fingerprint = replay._fingerprint(proposal)
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ReplayError("invalid_proposal_json", f"Invalid saved proposal or replay inputs: {error}") from error
    if fingerprint != manifest.get("fingerprint"):
        raise ReplayError("altered_proposal", "The saved deck or replay inputs differ from the recorded fingerprint.")
    return proposal, replay
