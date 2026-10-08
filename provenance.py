"""Project logbook evidence without IDs/secrets; never invent manual feedback."""
from policy import instant


def project_logbook(entries, engine_entities=()):
    projected = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("entity_id"), str):
            continue
        instant(entry.get("when"))
        source = entry.get("context_entity_id")
        domain = entry.get("context_domain")
        if source in engine_entities:
            actor = "engine"
        elif domain == "automation":
            actor = "automation"
        elif domain == "script":
            actor = "script"
        elif entry.get("context_user_id"):
            actor = "user_associated"
        else:
            actor = "unattributed"
        projected.append({"time": entry["when"], "entity_id": entry["entity_id"],
                          "state": entry.get("state"), "actor": actor,
                          "source_entity": source if isinstance(source, str) else None,
                          "eligible_preference_label": False})
    return projected


def annotate_candidates(candidates, evidence, entity_id="light.office_ceiling"):
    """Exact state/time match only; nearby automation events are not proof."""
    result = []
    for candidate in candidates:
        t = instant(candidate["start"])
        matches = [e for e in evidence if e["entity_id"] == entity_id and e["state"] == "off"
                   and instant(e["time"]) == t]
        # Ambiguous evidence fails closed rather than selecting a convenient source.
        attribution = matches[0] if len(matches) == 1 else None
        result.append({**candidate, "actor": attribution["actor"] if attribution else "unattributed",
                       "source_entity": attribution["source_entity"] if attribution else None,
                       "eligible_preference_label": False})
    return result
