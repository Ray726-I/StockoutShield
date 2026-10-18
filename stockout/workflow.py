"""Plain-language, presentation-independent workflow state."""
from datetime import date

TERMINAL = {'received', 'rejected', 'cancelled', 'expired'}


def action_state(action, today):
    state, p = action['status'], action['payload']
    if state == 'approved':
        return ('Awaiting dispatch', 'Record dispatch', 'stable') if p['kind'] == 'transfer' else ('Ready to order', 'Record order', 'stable')
    if state in {'dispatched', 'ordered'}:
        arrival = date.fromisoformat(p['arrival'])
        if arrival < today:
            return ('Overdue · confirm delivery', 'Record receipt', 'urgent')
        if arrival == today:
            return ('Due today · confirm delivery', 'Record receipt', 'watch')
        return ('In transit' if state == 'dispatched' else 'On order', None, 'transit')
    return ({'received': 'Received', 'rejected': 'Rejected', 'cancelled': 'Cancelled', 'expired': 'Closed · expired in transit'}.get(state, state.title()), None, 'urgent' if state == 'expired' else 'quiet')


def workflow_steps(action):
    p, state = action['payload'], action['status']
    stages = ['Suggested', 'Approved', 'Dispatched', 'In transit', 'Received'] if p['kind'] == 'transfer' else ['Suggested', 'Approved', 'Ordered', 'Expected', 'Received']
    current = {'approved': 1, 'dispatched': 3, 'ordered': 3, 'received': 4}.get(state, -1)
    return stages, current


def event_description(event, actions, facilities, medicines, units):
    payload = event['payload']
    action = next((a for a in actions if a['id'] == payload.get('action_id', payload.get('recommendation_id'))), None)
    p = action['payload'] if action else payload
    name = medicines.get(p.get('medicine_id'), 'Inventory')
    facility = facilities.get(p.get('facility_id'), '')
    quantity = p.get('quantity')
    amount = f"{quantity:,} {units.get(p.get('medicine_id'), 'units')}" if quantity is not None else ''
    labels = {'approved': 'Action approved', 'rejected': 'Recommendation rejected', 'dispatched': 'Transfer dispatched', 'ordered': 'Order recorded', 'received': 'Delivery received', 'cancelled': 'Action cancelled', 'expired': 'Delivery closed', 'supplier_received': 'Supplier delivery received', 'inventory_receive': 'Stock received', 'inventory_dispense': 'Dispensing recorded', 'inventory_adjust': 'Stock count adjusted', 'inventory_quarantine': 'Batch quarantined', 'inventory_release': 'Batch released', 'inventory_usage': 'Usage record updated', 'arrival_updated': 'Expected arrival updated', 'clock_advanced': 'Planning date advanced', 'dataset_imported': 'Inventory imported', 'expired_in_transit': 'Stock expired in transit', 'expiry_writeoff': 'Expired stock written off'}
    detail = ' · '.join(str(x) for x in [name, facility, amount] if x)
    return labels.get(event['event'], event['event'].replace('_', ' ').capitalize()), detail
