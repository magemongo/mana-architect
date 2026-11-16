"""Functional role templates expressed in the Phase 2 rule vocabulary."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class PackageTemplate:
    archetype: str
    seed_rules: tuple[str, ...]
    source_role: str
    target_role: str
    extension: Literal["none", "sacrifice_fodder", "food_recursion", "life_feedback"] = "none"


TEMPLATE_CATALOG_VERSION = "packages-v2"
PACKAGE_TEMPLATES = (
    PackageTemplate("artifact_value", ("artifact_etb_triggers_payoff",), "generator", "payoff"),
    PackageTemplate("blink_etb", ("blink_retriggers_etb", "recursion_reuses_etb"), "enabler", "payoff"),
    PackageTemplate("counters_growth", ("counter_placement_triggers_payoff",), "generator", "payoff"),
    PackageTemplate("counters_growth", (
        "counter_multiplier_amplifies_placement", "counter_multiplier_amplifies_doubling",
        "counter_increase_amplifies_placement", "counter_increase_amplifies_doubling",
    ), "amplifier", "generator"),
    PackageTemplate("graveyard_loop", ("food_enables_self_return",), "outlet", "fodder", "food_recursion"),
    PackageTemplate("lands_matter", ("extra_land_play_enables_landfall", "land_entry_triggers_payoff"),
                    "enabler", "payoff"),
    PackageTemplate("life_drain_loop", ("lifeloss_triggers_payoff",), "life_loss", "life_gain", "life_feedback"),
    PackageTemplate("sacrifice_value", ("sacrifice_triggers_death_payoff", "sacrifice_triggers_payoff"),
                    "outlet", "payoff", "sacrifice_fodder"),
    PackageTemplate("spell_chain", ("casting_triggers_payoff", "casting_or_copying_triggers_payoff",
                                    "spell_copy_triggers_payoff"), "enabler", "payoff"),
    PackageTemplate("token_amplification", ("token_multiplier_amplifies_creation",), "amplifier", "generator"),
    PackageTemplate("tokens_wide", ("token_etb_triggers_payoff", "token_receives_tribal_buff",
                                    "token_enables_tribal_combat_payoff"), "generator", "payoff"),
    PackageTemplate("voltron_support", ("equipment_enables_voltron",), "support", "attacker"),
)
