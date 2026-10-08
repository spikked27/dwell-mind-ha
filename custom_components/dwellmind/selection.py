"""Pure area membership/discovery rules. No writes to HA registries."""
SUPPORTED = {'light', 'binary_sensor', 'sensor', 'media_player'}


def effective_area(entity, devices):
    if entity.get('area_id'):
        return entity['area_id']
    device = devices.get(entity.get('device_id'), {})
    seen = set()
    while device:
        if device.get('area_id'):
            return device['area_id']
        parent = device.get('via_device_id')
        if not parent or parent in seen:
            break
        seen.add(parent)
        device = devices.get(parent, {})
    return None


def discover(areas, entities, devices, states):
    result = {}
    for entity in entities:
        entity_id = entity['entity_id']
        domain = entity_id.split('.')[0]
        area = effective_area(entity, devices)
        device = devices.get(entity.get('device_id'), {})
        if (area not in areas or domain not in SUPPORTED or entity.get('disabled_by')
                or device.get('disabled_by') or entity.get('entity_category')):
            continue
        state = states.get(entity_id, {})
        device_class = entity.get('device_class') or state.get('device_class')
        if domain == 'binary_sensor' and device_class not in {'motion','occupancy','presence'}:
            continue
        if domain == 'sensor' and device_class not in {'illuminance','temperature','humidity'}:
            continue
        result[entity_id] = area
    return dict(sorted(result.items()))

