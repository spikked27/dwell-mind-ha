"""Read HA's area, device and entity registries through supported APIs."""
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er

from .selection import discover


def candidates(hass, area_ids):
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
    return discover(set(area_ids), rows, device_rows, states)


def rooms(hass, area_ids, entity_ids):
    mapping = candidates(hass, area_ids)
    if not 1 <= len(entity_ids) <= 40 or len(set(entity_ids)) != len(entity_ids) or any(e not in mapping for e in entity_ids):
        raise ValueError('Select one to 40 discovered entities')
    registry = ar.async_get(hass)
    return [{'area_id':a, 'name':registry.async_get_area(a).name,
             'entities':sorted(e for e in entity_ids if mapping[e] == a)} for a in area_ids]

