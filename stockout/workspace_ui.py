"""Operational forms and shipment tracking using the existing stock ledger."""
from datetime import date, timedelta
from html import escape
import uuid
import pandas as pd
import streamlit as st
from . import ui
from .workflow import TERMINAL, action_state, workflow_steps, event_description


def saved(message):
    st.session_state.pop('current_plan', None)
    st.session_state['mutation_token'] = uuid.uuid4().hex
    st.session_state['flash'] = message
    st.rerun()


def token():
    return st.session_state.setdefault('mutation_token', uuid.uuid4().hex)


def progress(action):
    stages, current = workflow_steps(action)
    pieces = []
    for i, label in enumerate(stages):
        state = 'complete' if i < current else 'current' if i == current else 'upcoming'
        pieces.append(f'<li class="ss-step-{state}" {"aria-current=step" if i == current else ""}><span>{"✓" if i < current else i + 1}</span><b>{escape(label)}</b></li>')
    ui.render('<ol class="ss-progress" aria-label="Delivery progress">' + ''.join(pieces) + '</ol>')


def activity(events, actions, facilities, medicines, units, limit=6):
    ui.section_title('Recent activity', 'Saved decisions, stock updates and deliveries.')
    if not events:
        st.caption('Your first inventory update or action decision will appear here.')
    for e in list(reversed(events))[:limit]:
        title, detail = event_description(e, actions, facilities, medicines, units)
        recorded = e['payload'].get('_recorded_at')
        when = pd.Timestamp(recorded).tz_convert('Asia/Kolkata').strftime('%d %b, %H:%M') if recorded else e['date']
        ui.render(f'<div class="ss-feed-item"><span class="ss-feed-dot" aria-hidden="true"></span><div><strong>{escape(title)}</strong><p>{escape(detail)}</p></div><time>{escape(when)}</time></div>')


def stock_editor(store, wid, physical, facilities, medicines, units, selected_f, selected_m):
    with st.expander('Update inventory', icon=':material/edit_square:'):
        operation = st.selectbox('What would you like to record?', ['Receive unlisted stock', 'Record dispensing', 'Adjust a stock count', 'Quarantine a batch', 'Release a batch', 'Correct daily usage history'], key='inventory_operation')
        ops = {'Receive unlisted stock':'receive', 'Record dispensing':'dispense', 'Adjust a stock count':'adjust', 'Quarantine a batch':'quarantine', 'Release a batch':'release', 'Correct daily usage history':'usage'}
        op = ops[operation]
        payload = {}
        if op in ['receive', 'usage', 'dispense']:
            a, b = st.columns(2)
            fs, ms = list(facilities), list(medicines)
            if selected_f in fs:
                f=selected_f
                a.caption('Facility for this update')
                a.write(facilities[f])
            else:
                f=a.selectbox('Facility for this update',fs,format_func=facilities.get,key='edit_facility')
            if selected_m in ms:
                m=selected_m
                b.caption('Medicine for this update')
                b.write(medicines[m])
            else:
                m=b.selectbox('Medicine for this update',ms,format_func=medicines.get,key='edit_medicine')
            payload.update(facility_id=f, medicine_id=m)
        else:
            batches = physical.tables['batches']
            if selected_f != 'All': batches = batches[batches.facility_id.eq(selected_f)]
            if selected_m != 'All': batches = batches[batches.medicine_id.eq(selected_m)]
            if op == 'quarantine': batches = batches[batches.status.eq('available')]
            if op == 'release': batches = batches[batches.status.eq('quarantined')]
            if batches.empty:
                st.info('No eligible batches in this selection. Choose another facility or medicine above.')
                return
            records = batches.set_index('batch_id').to_dict('index')
            bid = st.selectbox('Batch to update', list(records), format_func=lambda x:f'{x} · {medicines[records[x]["medicine_id"]]} · {facilities[records[x]["facility_id"]]}', key='edit_batch_'+op)
            batch = records[bid]; m = batch['medicine_id']
            payload['batch_id'] = bid
            st.caption(f'Current count: {batch["quantity"]:,} {units[m]}. Expiry: {batch["expiry"]}. Status: {batch["status"]}.')
        if op == 'receive':
            st.info('For a delivery already listed in Incoming supply or Actions, record its receipt there. This form adds a separate batch.')
        if op == 'usage':
            st.info('Record the total dispensed on a completed reporting day. This updates demand history; the current stock count stays unchanged because the snapshot already includes that dispensing.')
        if op == 'dispense':
            st.info(f'This records a stock movement on {physical.as_of}. Usable batches are consumed in expiry order and transfer reservations stay protected. Current-day movements are kept in the ledger; only completed-day history is used for forecasting.')
        with st.form('stock_update_'+op+'_'+token()):
            if op == 'receive':
                payload['batch_id'] = st.text_input('Unique stock batch reference', placeholder='Supplier batch or local stock reference').strip()
                a, b = st.columns(2)
                payload['quantity'] = a.number_input(f'Quantity received ({units[m]})', min_value=1, step=1)
                payload['expiry'] = str(b.date_input('Expiry date', value=physical.as_of+timedelta(days=365), min_value=physical.as_of+timedelta(days=1)))
            elif op == 'adjust':
                payload['quantity'] = st.number_input(f'New total stock count ({units[m]})', min_value=0, value=int(batch['quantity']), step=1)
                st.caption('Enter the counted total, rather than the amount to add or remove. Approved transfer reservations remain protected.')
            elif op == 'dispense':
                payload['dispensed'] = st.number_input(f'Quantity dispensed ({units[m]})', min_value=1, step=1)
            elif op == 'usage':
                payload['date'] = str(st.date_input('Completed reporting day', value=physical.as_of-timedelta(days=1), max_value=physical.as_of-timedelta(days=1)))
                payload['dispensed'] = st.number_input(f'Total dispensed that day ({units[m]})', min_value=0, step=1)
                payload['availability'] = st.selectbox('Medicine availability that day', ['available','stockout','unknown'], format_func={'available':'Available throughout the day','stockout':'Availability restricted dispensing','unknown':'Availability is unknown'}.get)
                st.caption('An existing record for this facility, medicine and date will be replaced by this daily total; its previous value remains in the ledger.')
            payload['reason'] = st.text_input('Reason or delivery reference', placeholder='Explain the count, reporting correction or delivery')
            confirm = st.checkbox('I have checked this update')
            submitted = st.form_submit_button('Save inventory update', type='primary')
        if submitted:
            if not confirm: st.error('Check the update confirmation before saving. Your entries have been kept.')
            else:
                try:
                    store.record_inventory(wid, op, payload, expected_version=physical.version, request_id=token())
                    saved('Inventory update saved. Stock risks and recommendations will use the updated records.')
                except ValueError as exc: st.error(str(exc))


def arrival_form(store, wid, reference, arrival, ds, key):
    with st.expander('Update expected arrival'):
        with st.form('arrival_'+key):
            value=max(date.fromisoformat(arrival),ds.as_of)
            updated = st.date_input('New expected arrival', value=value, min_value=ds.as_of, key='arrival_date_'+key)
            reason = st.text_input('Reason for the date change', key='arrival_reason_'+key)
            submit = st.form_submit_button('Save arrival date')
        if submit:
            try:
                store.update_arrival(wid, reference, updated, reason, expected_version=ds.version, request_id=token())
                saved('Expected arrival updated. Supply options will be recalculated with the new date.')
            except ValueError as exc: st.error(str(exc))


def track_actions(store, wid, ds, physical, actions, facilities, medicines, units, selected_f, selected_m, navigate):
    st.caption('Approval reserves stock. Dispatch removes donor stock. Receipt adds usable stock at the destination.')
    focus = st.session_state.pop('focus_action', None)
    filters=['Active actions','Awaiting dispatch or order','In transit or on order','Due or overdue','Completed','All actions']
    next_status=st.session_state.pop('tracking_status_next',None)
    if next_status in filters:st.session_state['tracking_status']=next_status
    status = st.selectbox('Delivery status', filters, key='tracking_status')
    visible = [a for a in actions if (selected_f=='All' or selected_f in [a['payload'].get('from_facility'),a['payload']['facility_id']]) and (selected_m=='All' or a['payload']['medicine_id']==selected_m)]
    if focus: visible = [a for a in visible if a['id']==focus]
    elif status=='Active actions': visible=[a for a in visible if a['status'] not in TERMINAL]
    elif status=='Awaiting dispatch or order': visible=[a for a in visible if a['status']=='approved']
    elif status=='In transit or on order': visible=[a for a in visible if a['status'] in ['dispatched','ordered']]
    elif status=='Due or overdue': visible=[a for a in visible if a['status'] in ['dispatched','ordered'] and a['payload']['arrival']<=str(ds.as_of)]
    elif status=='Completed': visible=[a for a in visible if a['status'] in TERMINAL]
    visible.sort(key=lambda a:(a['status'] in TERMINAL,a['payload']['arrival'],a['id']))
    if not visible:
        st.info('No deliveries match this selection. Review a recommendation to approve a transfer or reorder, or change the status filter.')
        st.button('Review recommendations', on_click=navigate, args=('Action Plan', None, None, 'Recommendations'))
    pages=max(1,(len(visible)+4)//5)
    st.session_state['tracking_page']=min(st.session_state.get('tracking_page',1),pages)
    page=st.selectbox('Delivery page',range(1,pages+1),format_func=lambda n:f'Page {n} of {pages}',key='tracking_page') if pages>1 else 1
    for action in visible[(page-1)*5:page*5]:
        r=action['payload']; state=action['status']; label,next_action,tone=action_state(action,ds.as_of)
        with st.container(border=True,key='delivery_'+action['id']):
            title='Transfer' if r['kind']=='transfer' else 'Reorder'
            route=f'{facilities[r["from_facility"]]} → {facilities[r["facility_id"]]}' if r['kind']=='transfer' else facilities[r['facility_id']]
            ui.render(f'<div class="ss-delivery-head"><div><h3>{escape(medicines[r["medicine_id"]])}</h3><p>{escape(route)}</p><span>{title} · {r["quantity"]:,} {escape(units[r["medicine_id"]])}</span></div>{ui.badge(label,tone)}</div>')
            progress(action)
            a,b=st.columns([2,1],vertical_alignment='center')
            a.caption(f'Expected arrival: {pd.Timestamp(r["arrival"]):%d %b %Y} · Reference {action["id"][:8]}')
            if state not in TERMINAL:
                if next_action:
                    if b.button(next_action,key='advance-'+action['id'],type='primary',width='stretch'):
                        try:
                            new=store.advance_action(wid,action['id'])
                            if new in ['received','expired']:
                                st.session_state['tracking_status_next']='Completed'
                                st.session_state['focus_action']=action['id']
                            saved({'dispatched':'Dispatch recorded. Donor stock has moved into transit.','ordered':'Order recorded. Incoming supply has been updated.','received':'Receipt recorded. Destination stock has been updated.','expired':'Delivery closed: expired stock was not added to usable inventory.'}[new])
                        except ValueError as exc: st.error(str(exc))
                else:
                    b.button('Receipt not due yet',key='waiting-'+action['id'],disabled=True,width='stretch',help=f'Receipt becomes available on {r["arrival"]}, using the workspace inventory date.')
                    st.caption(f'Receipt is available from {r["arrival"]}. Current inventory date: {ds.as_of}.')
                if state in ['dispatched','ordered']:
                    arrival_form(store,wid,action['id'],r['arrival'],ds,action['id'])
                if state=='approved':
                    with st.expander('Cancel before dispatch or ordering'):
                        with st.form('cancel_'+action['id']):
                            reason=st.text_input('Cancellation reason')
                            confirm=st.checkbox('Cancel this action and release its reservation')
                            cancel=st.form_submit_button('Cancel action')
                        if cancel:
                            if not confirm: st.error('Confirm the cancellation before saving.')
                            else:
                                try:
                                    store.cancel_action(wid,action['id'],reason)
                                    saved('Action cancelled. Its reservation has been released for planning.')
                                except ValueError as exc: st.error(str(exc))
            st.button('View this medicine',key='view-'+action['id'],on_click=navigate,args=('Inventory & Data',r['facility_id'],r['medicine_id']))


def incoming_supply(store,wid,ds,physical,facilities,medicines,units,selected_f,selected_m,navigate):
    orders=physical.tables['orders'].copy()
    # Approved action orders have their own stateful receipt flow.
    orders=orders[~orders.order_id.str.startswith('ACTION-')]
    if selected_f!='All': orders=orders[orders.facility_id.eq(selected_f)]
    if selected_m!='All': orders=orders[orders.medicine_id.eq(selected_m)]
    orders=orders.sort_values(['arrival','order_id'])
    st.caption('Receive listed supplier deliveries here. Transfers and orders approved in Actions are received through action tracking.')
    if orders.empty: st.info('No supplier deliveries in this selection. Check Actions for approved transfers and reorders.')
    pages=max(1,(len(orders)+4)//5)
    st.session_state['supply_page']=min(st.session_state.get('supply_page',1),pages)
    page=st.selectbox('Incoming supply page',range(1,pages+1),format_func=lambda n:f'Page {n} of {pages}',key='supply_page') if pages>1 else 1
    for item in orders.iloc[(page-1)*5:page*5].to_dict('records'):
        oid=item['order_id'];due=item['arrival']<=str(ds.as_of)
        with st.container(border=True):
            st.markdown(f'**{medicines[item["medicine_id"]]}**')
            st.write(f'{item["quantity"]:,} {units[item["medicine_id"]]} to {facilities[item["facility_id"]]}')
            st.caption(f'Reference {oid} · Expected {item["arrival"]} · Expires {item["expiry"]}')
            if due: st.warning('Due for receipt — confirm the delivery has physically arrived.')
            if st.button('Record supplier receipt',key='receive-supply-'+oid,disabled=not due,help=None if due else 'Receipt is available on the expected arrival date.'):
                try:
                    store.receive_supply(wid,oid,expected_version=physical.version)
                    saved('Supplier receipt recorded. Stock and incoming supply have been updated.')
                except ValueError as exc:st.error(str(exc))
            arrival_form(store,wid,oid,item['arrival'],ds,'supply_'+oid)
    st.button('Open transfer and reorder tracking',on_click=navigate,args=('Action Plan',None,None,'Tracking'))
