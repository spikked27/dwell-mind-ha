"""Read HA's area, device and entity registries through supported APIs."""
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .selection import discover, effective_area, SUPPORTED
from .scope import blocked


def candidates(hass, area_ids, excluded_areas=(), excluded_entities=(), reviewed_entities=()):
    areas = ar.async_get(hass)
    devices = dr.async_get(hass)
    entities = er.async_get(hass)
    if not area_ids or len(area_ids) > 8 or any(areas.async_get_area(a) is None for a in area_ids):
        raise ValueError('Select existing Home Assistant areas')
    rows = [{'entity_id':e.entity_id, 'area_id':e.area_id, 'device_id':e.device_id,
             'disabled_by':e.disabled_by, 'entity_category':e.entity_category,
             'device_class':e.device_class or e.original_device_class}
            for e in entities.entities.values()]
    device_rows = {d.id:{'area_id':d.area_id, 'via_device_id':d.via_device_id, 'disabled_by':d.disabled_by}
                   for d in devices.devices.values()}
    states = {e['entity_id']:{'device_class':s.attributes.get('device_class')}
              for e in rows if (s := hass.states.get(e['entity_id'])) is not None}
    found = discover(set(area_ids), rows, device_rows, states)
    # Startup may precede template/aggregate state publication. Preserve already
    # reviewed entities using registry membership; absence of a temporary state
    # or device_class is not grounds to silently remove them from observation.
    reviewed = set(reviewed_entities)
    for row in rows:
        entity = row['entity_id']
        device = device_rows.get(row.get('device_id'), {})
        area = effective_area(row, device_rows)
        if (entity in reviewed and area in area_ids and entity.split('.')[0] in SUPPORTED
                and not row.get('disabled_by') and not device.get('disabled_by')
                and not row.get('entity_category')):
            found[entity] = area
    members = {s.entity_id:s.attributes.get('entity_id', ()) for s in hass.states.async_all()
               if 'entity_id' in s.attributes}
    mapping = {row['entity_id']:row for row in rows}
    return {e:a for e,a in found.items() if not blocked(e,set(excluded_areas),set(excluded_entities),
                                                       mapping,device_rows,members)}


def rooms(hass, area_ids, entity_ids, excluded_areas=(), excluded_entities=(), retain_review=False):
    mapping = candidates(hass, area_ids, excluded_areas, excluded_entities, entity_ids if retain_review else ())
    if not 0 <= len(entity_ids) <= 40 or len(set(entity_ids)) != len(entity_ids):
        raise ValueError('Select one to 40 discovered entities')
    if not retain_review and any(e not in mapping for e in entity_ids):
        raise ValueError('Selection includes an excluded or undiscovered entity')
    if not entity_ids and not (excluded_areas or excluded_entities):
        raise ValueError('An empty observation selection requires explicit exclusions')
    registry = ar.async_get(hass)
    return [{'area_id':a, 'name':registry.async_get_area(a).name,
             'entities':sorted(e for e in entity_ids if mapping.get(e) == a)} for a in area_ids
            if any(mapping.get(e) == a for e in entity_ids)]
