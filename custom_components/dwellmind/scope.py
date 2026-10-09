"""Explicit exclusions override reviewed selection, including helper dependencies."""
from .selection import effective_area


def blocked(entity_id, excluded_areas, excluded_entities, entities, devices, members, seen=None, budget=None):
    if entity_id in excluded_entities:
        return True
    if not excluded_areas and not excluded_entities:
        return False
    seen = set() if seen is None else seen
    budget = [0] if budget is None else budget
    budget[0] += 1
    if budget[0] > 1024:
        return True
    if entity_id in seen or len(seen) >= 64:
        return True
    seen = seen | {entity_id}
    row = entities.get(entity_id)
    area = effective_area(row, devices) if row else None
    if excluded_areas and (area is None or area in excluded_areas):
        # Unresolved membership cannot prove a device isn't in an excluded room.
        return True
    children = members.get(entity_id, ())
    if isinstance(children, str):
        children = (children,)
    if not isinstance(children, (list, tuple)) or len(children) > 64:
        return True
    return any(not isinstance(child, str) or blocked(child, excluded_areas, excluded_entities,
               entities, devices, members, seen, budget) for child in children)
