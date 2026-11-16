"""Re-extract native facts when a parser paragraph spans independent activations."""

from dataclasses import replace

from mana_architect.ability_boundaries import resolve_ability_boundaries
from mana_architect.oracle_text import parse_oracle_text
from mana_architect.ramp_evaluator import evaluate_ramp
from mana_architect.synergy_capabilities import extract_mechanical_capabilities


def extract_crossing_activations(card, parsed, mechanics, ramp, catalog):
    """Return CPA-local profiles and the provenance of newly extracted spans."""
    spans = tuple(cap.evidence for cap in mechanics.capabilities if cap.evidence.source_field == "oracle_text")
    spans += tuple(line.event.evidence for line in ramp.lines)
    if not spans or " // " in parsed.oracle_text or "//" in card.type_line:
        return mechanics, ramp, {}

    boundaries = resolve_ability_boundaries(parsed, card_id=card.card_id, type_line=card.type_line)
    scoped = {}
    capabilities = list(mechanics.capabilities)
    unknowns = list(mechanics.unknowns)
    lines = list(ramp.lines)
    for paragraph in parsed.abilities:
        parts = [item for item in boundaries if paragraph.start <= item.start < item.end <= paragraph.end]
        if (len(parts) < 2 or len({part.face_index for part in parts}) != 1
                or any(part.status != "resolved" or part.kind != "activated" for part in parts)
                or any(character in paragraph.text for character in '\"“”()')):
            continue
        if any(parsed.oracle_text[left:right].strip() for left, right in zip(
            (paragraph.start, *(part.end for part in parts)),
            (*(part.start for part in parts), paragraph.end), strict=True,
        )):
            continue

        def crosses_boundary(span):
            return (span.ability_id == paragraph.id and paragraph.start <= span.start < span.end <= paragraph.end
                    and any(span.start < part.start < span.end for part in parts))

        if not any(crosses_boundary(span) for span in spans):
            continue
        replaced_spans = {span for span in spans if crosses_boundary(span)}
        for part in parts:
            # Parse the entire ability, including its cost and all instructions.
            # This path deliberately uses no parser or evaluation cache.
            local = parse_oracle_text(part.source_text, catalog.oracle_lexicon, catalog.oracle_mechanical_patterns)
            local_mechanics = extract_mechanical_capabilities(local, name=card.name, type_line=card.type_line)
            local_ramp = evaluate_ramp(card.card_id, card.type_line, card.mana_cost, local)

            def rebase(span, source, record_id):
                start, end = part.start + span.start, part.start + span.end
                if not (part.start <= start < end <= part.end
                        and parsed.oracle_text[start:end] == span.oracle_span):
                    raise ValueError("Scoped evidence does not match the original Oracle text.")
                scoped[source, record_id, start, end] = (part.ability_id, local.parser_version)
                return replace(span, ability_id=paragraph.id, start=start, end=end)

            capabilities.extend(
                replace(cap, evidence=rebase(cap.evidence, "mechanical", cap.id))
                for cap in local_mechanics.capabilities if cap.evidence.source_field == "oracle_text"
            )
            unknowns.extend(rebase(span, "mechanical", "unresolved") for span in local_mechanics.unknowns)
            lines.extend(
                replace(line, event=replace(line.event, evidence=rebase(line.event.evidence, "ramp", paragraph.id)))
                for line in local_ramp.lines
            )
        capabilities = [cap for cap in capabilities if cap.evidence not in replaced_spans]
        lines = [line for line in lines if line.event.evidence not in replaced_spans]
    return replace(mechanics, capabilities=tuple(dict.fromkeys(capabilities)), unknowns=tuple(dict.fromkeys(unknowns))), replace(
        ramp, lines=tuple(dict.fromkeys(lines)),
    ), scoped
