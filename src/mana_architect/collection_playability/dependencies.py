"""Conservative, evidence-linked card-local modifier detection."""

import re

from mana_architect.collection_playability.contracts import CardDependency, SemanticCoverageState


def assess_dependencies(boundaries, assessments, groups):
    dependencies = []
    affected = set()
    by_id = {item.ability_id: item for item in boundaries}
    for source in boundaries:
        assessment = assessments[source.ability_id]
        if assessment.state == SemanticCoverageState.VERIFIED:
            continue
        text = source.source_text.casefold()
        if source.kind == "static" and re.fullmatch(r"(?:flying|vigilance|haste|trample)\.?", text.strip()):
            continue
        if re.fullmatch(
            r"(?:players|you|opponents) (?:can't|cannot) draw cards(?: this turn)?\.?",
            text.strip(),
        ) or re.fullmatch(
            r"(?:whenever [^,.]+, |\{[^{}]+\}: )(?:players|you|opponents) "
            r"(?:can't|cannot) draw cards(?: this turn)?\.?",
            text.strip(),
        ):
            category = "draw_restriction"
            candidates = {key for key, caps in groups.items() if any(cap.id == "draw" for cap in caps)}
            status = "potentially_affected"
        elif re.fullmatch(
            r"(?:mana abilities (?:can't|cannot) be activated|"
            r"(?:until end of turn, |whenever [^,.]+, )?"
            r"(?:players|you|opponents) (?:can't|cannot) activate mana abilities(?: this turn)?)\.?",
            text.strip(),
        ):
            category = "mana_activation_restriction"
            candidates = {key for key, caps in groups.items() if any(cap.id == "add_mana" for cap in caps)}
            status = "potentially_affected"
        else:
            categories = (
                ("replacement", r"\binstead\b"),
                ("prevention", r"\bprevent\b"),
                ("activation_restriction", r"\bactivat(?:e|ed|ion)\b"),
                ("casting_restriction", r"\b(?:cast|casting)\b"),
                ("cost_modification", r"\bcosts?\b"),
                ("timing_or_frequency", r"\b(?:once|each turn|only|during)\b"),
            )
            category = next((name for name, pattern in categories if re.search(pattern, text)), "unmodeled_static_or_keyword")
            candidates = set(groups)
            status = "unknown"
        candidates.discard(source.ability_id)
        if not candidates:
            continue
        affected.update(candidates)
        dependencies.append(CardDependency(
            source.ability_id, tuple(candidates), category, source.start, source.end,
            source.source_text, status, ("card_dependency_unresolved",), "low",
            "conditional" if source.kind in {"activated", "triggered", "spell"} else (
                "static" if source.status == "resolved" and source.kind == "static" else "unknown"
            ),
            ("mixed" if len({by_id[key].face_index == source.face_index for key in candidates}) > 1
             else "same_face" if all(by_id[key].face_index == source.face_index for key in candidates)
             else "cross_face"),
        ))
    for source in boundaries:
        if source.ability_id in groups and source.ability_id not in affected:
            dependencies.append(CardDependency(
                source.ability_id, (source.ability_id,), "detection_scope",
                source.start, source.end, source.source_text, "independent",
                ("no_detected_modifier_in_supported_scope",), "unchanged",
            ))
    return tuple(dependencies), frozenset(affected)
