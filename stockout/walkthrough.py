"""A guided, repeatable presentation using the actual planner and saved ledger."""
from copy import deepcopy
from datetime import date
from html import escape
import json
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from . import ui
from .data import Dataset
from .inventory import project_pair, project_inventory, apply_scenario, scale_predictions
from .planning import generate_plan

STEPS = ['Find a shortage', 'Review a transfer', 'Track delivery', 'Test a disruption', 'Explain the outcome']


def choose_case(ds, predictions, plan):
    """Display one transfer's consequences, rather than attributing the full plan to it."""
    candidates = [r for r in plan.recommendations if r['kind'] == 'transfer' and r['recipient_before_unmet7'] > 0]
    candidates.sort(key=lambda r: (r.get('arrives_too_late', False), -r['recipient_before_unmet7'], r['medicine_id']))
    if not candidates:
        return None
    rec = deepcopy(candidates[0])
    donor = (rec['from_facility'], rec['medicine_id'])
    recipient = (rec['to_facility'], rec['medicine_id'])
    before, _ = project_pair(ds, recipient, predictions[recipient].mean)
    after, _ = project_pair(ds, recipient, predictions[recipient].mean, [rec])
    donor_before, _ = project_pair(ds, donor, predictions[donor].mean)
    donor_after, _ = project_pair(ds, donor, predictions[donor].mean, [rec])
    rec.update(donor_before=[r['stock'] for r in donor_before], donor_after=[r['stock'] for r in donor_after],
        recipient_before_unmet7=sum(r['unmet'] for r in before[:7]),
        recipient_after_unmet7=sum(r['unmet'] for r in after[:7]))
    return dict(rec=rec, plan_id=plan.plan_id, dataset=ds.copy(), predictions=deepcopy(predictions),
                before=before, after=after, donor_before=donor_before, donor_after=donor_after)


def set_step(step):
    st.session_state['walk_step'] = step
    st.session_state['walk_stage'] = step


def remember_step():
    st.session_state['walk_stage'] = st.session_state['walk_step']


def curve_chart(case, donor=False):
    before = case['donor_before'] if donor else case['before']
    after = case['donor_after'] if donor else case['after']
    fig = go.Figure()
    for values, name, color, dash in [(before, 'Without this transfer', '#8797a4', 'dot'),
                                      (after, 'With this transfer', '#005c55', 'solid')]:
        fig.add_scatter(x=[r['date'] for r in values], y=[r['stock'] for r in values], name=name,
                        line=dict(color=color, dash=dash, width=2.5))
    fig.update_layout(height=280, margin=dict(l=0,r=0,t=10,b=0),
        paper_bgcolor='rgba(0,0,0,0)', plot_bgcolor='rgba(0,0,0,0)',
        font=dict(family='Inter',color='#536171',size=13),
        legend=dict(orientation='h',y=-.2), yaxis_title='Available units')
    fig.update_yaxes(rangemode='tozero',gridcolor='#e3e8f0')
    st.plotly_chart(fig, width='stretch', config={'displayModeBar':False})


@st.cache_data(show_spinner=False, max_entries=8)
def stress_case(payload, predictions, facility, medicine, multiplier, delay):
    ds=Dataset.from_payload(json.loads(payload))
    scenario=apply_scenario(ds,facility,medicine,multiplier,delay,False)
    pp=scale_predictions(predictions,facility,medicine,multiplier)
    baseline=project_inventory(scenario,pp)
    plan=generate_plan(scenario,pp)
    moved=project_inventory(scenario,pp,[r for r in plan.recommendations if r['kind']=='transfer'])
    return baseline,moved,plan


def render(store, wid, ds, predictions, projection, get_plan, facilities, medicines, units):
    st.title('Walk through a supply decision')
    st.markdown('<div class="subcopy">One medicine. Two facilities. Follow the decision from an early warning to a recorded delivery.</div>',unsafe_allow_html=True)
    if 'walk_case' not in st.session_state:
        with st.spinner('Finding an explained transfer for your walkthrough…'):
            case=choose_case(ds,predictions,get_plan())
            st.session_state['walk_case']=case
    case=st.session_state['walk_case']
    if not case:
        st.info('There is no suitable transfer in this workspace. Open the action plan to inspect reorders, or start a fresh walkthrough from Workspace settings.')
        if st.button('Look for a transfer again'):
            st.session_state.pop('walk_case',None);st.rerun()
        return
    rec=case['rec']; mid=rec['medicine_id']; donor=rec['from_facility']; recipient=rec['to_facility']
    step=st.session_state.setdefault('walk_stage',0)
    st.session_state.setdefault('walk_step',step)
    states={a['id']:a['status'] for a in store.action_history(wid)}
    state=states.get(rec['recommendation_id'],'not approved')
    st.radio('Your walkthrough',range(len(STEPS)),format_func=lambda i:f'{i+1}. {STEPS[i]}',
             horizontal=True,key='walk_step',label_visibility='collapsed',on_change=remember_step)
    step=st.session_state['walk_step']
    st.progress((step+1)/len(STEPS),text=f'Step {step+1} of {len(STEPS)} · {STEPS[step]}')
    ui.render(f'<section class="ss-case-heading"><div><strong>{escape(medicines[mid])}</strong><p>{escape(facilities[donor])} <span aria-hidden="true">→</span> {escape(facilities[recipient])}</p></div>{ui.badge("Delivery recorded" if state=="received" else "In transit" if state=="dispatched" else "Ready for review" if state=="not approved" else state.title(),"transit" if state=="dispatched" else "stable")}</section>')
    notes=[
        'A stock count tells us what is on the shelf. The forecast tells us how long it will last. This warning uses historical dispensing, incoming deliveries and expiry dates.',
        'We check the exact batch, arrival date, pack size and recipient consumption. We then re-run the donor under higher demand. The quantity comes from these constraints.',
        'Approval reserves the batch. Dispatch removes it from the donor. Receipt adds it at the destination. Each transition is saved and protected against duplicate clicks.',
        'A plan is only useful if it can respond to change. This isolated scenario increases demand and delays incoming supply, then runs the planner again.',
        'Our focus is explaining the consequence of a supply decision. These are projected outcomes for this example; measured model experiments are available in Scenario Lab.'
    ]
    if step==0:
        st.subheader(f'{facilities[recipient]} needs attention')
        first=next((r['date'] for r in case['before'] if r['unmet']>1e-6),None)
        st.write(f'The first projected shortage is **{date.fromisoformat(first):%d %B}**.' if first else 'The planner is checking a future supply gap.')
        left,right=st.columns(2)
        left.metric('Expected shortage over 7 days',f'{rec["recipient_before_unmet7"]:,.0f} {units[mid]}')
        right.metric('Transfer under review',f'{rec["quantity"]:,} {units[mid]}')
        st.write('The sending facility has a batch that can arrive before it expires. Next, check what happens to both facilities if it is shared.')
        curve_chart(case)
        st.caption('Projection for the receiving facility. Incoming supply and expiry are included in both lines.')
    elif step==1:
        st.subheader('Check the recommendation')
        # The heading stays inside its own bordered card, avoiding negative margins into Streamlit wrappers.
        with st.container(border=True,key='walk_recommendation'):
            ui.action_header(rec,facilities,medicines,units)
        c1,c2=st.columns(2)
        c1.metric('Arrival',f'{date.fromisoformat(rec["arrival"]):%d %b}',f'{rec["travel_days"]} day travel',delta_color='off')
        c2.metric('Transport estimate',f'INR {rec["transfer_cost"]:,.0f}')
        if rec.get('arrives_too_late'):
            st.warning('This transfer arrives after the first shortage. Earlier demand still needs urgent sourcing.')
        st.write(f'**Why this batch?** {rec["batch_id"]} expires on {date.fromisoformat(rec["expiry"]):%d %B}. The recipient is projected to consume the shared stock before expiry.')
        st.write('**Why this quantity?** It respects whole packs, available batch stock, the recipient’s need and the donor’s reserve checks under central and higher demand.')
        st.subheader('The donor stays protected')
        curve_chart(case,donor=True)
        st.caption('Lowest projected donor stock is a 30-day minimum, not its current balance. Reserve protection is checked relative to the donor’s baseline; existing future gaps are not hidden.')
        with st.expander('Read the complete planner explanation'):
            st.write(rec['explanation'])
            st.caption('The planner explanation describes the full allocation. Charts in this walkthrough isolate this one transfer.')
    elif step==2:
        st.subheader('Move from a decision to a delivery')
        timeline=[('Approve','Stock reserved'),('Dispatch','Removed from donor'),('Receive','Added to destination')]
        completed={'not approved':0,'approved':1,'dispatched':2,'received':3}.get(state,0)
        ui.render('<ol class="ss-delivery-flow">'+''.join(f'<li class="{"is-complete" if i<completed else ""}"><strong>{"✓ " if i<completed else ""}{title}</strong><span>{description}</span></li>' for i,(title,description) in enumerate(timeline))+'</ol>')
        st.caption('These controls record a simulated transfer in this workspace. They do not contact a facility or supplier.')
        try:
            if state=='not approved':
                st.write('Approve this recommendation to reserve the batch. You can inspect it again in the previous step.')
                if st.button('Approve this transfer',type='primary',key='walk_approve'):
                    store.decide(wid,case['plan_id'],rec['recommendation_id'],True,'Guided walkthrough')
                    st.session_state.pop('current_plan',None);st.rerun()
            elif state=='approved':
                st.success('Approved. This quantity is reserved and cannot be allocated again.')
                if st.button('Record dispatch',type='primary',key='walk_dispatch'):
                    store.advance_action(wid,rec['recommendation_id']);st.session_state.pop('current_plan',None);st.rerun()
            elif state=='dispatched':
                current=next(a['payload'] for a in store.action_history(wid) if a['id']==rec['recommendation_id'])
                days=max(0,(date.fromisoformat(current['arrival'])-ds.as_of).days)
                if days:
                    st.info(f'In transit. Arrival is {date.fromisoformat(current["arrival"]):%d %B}. Advance the planning clock to continue.')
                    if st.button(f'Advance to arrival · {days} {"day" if days==1 else "days"}',type='primary',key='walk_advance'):
                        store.advance_clock(wid,days,predictions);st.session_state.pop('current_plan',None);st.rerun()
                else:
                    st.info('The transfer has reached its expected arrival date. Record receipt after checking the quantity.')
                    if st.button('Record receipt',type='primary',key='walk_receive'):
                        store.advance_action(wid,rec['recommendation_id']);st.session_state.pop('current_plan',None);st.rerun()
            elif state=='received':
                st.success(f'Delivery recorded: {rec["quantity"]:,} {units[mid]} added at {facilities[recipient]}.')
                st.write('The next step tests how the plan responds when conditions change.')
            else:
                st.warning('This action was rejected. Start a fresh walkthrough from Workspace settings to repeat the story.')
        except ValueError as exc:
            st.warning(f'{exc} Start a fresh walkthrough to use a separate inventory, or review pending actions in the Action Plan.')
        with st.expander('Inspect the saved audit trail'):
            events=[e for e in store.events(wid) if e['payload'].get('action_id',e['payload'].get('recommendation_id'))==rec['recommendation_id'] or e['event']=='clock_advanced']
            if events: st.dataframe(pd.DataFrame([{'Date':e['date'],'Event':e['event'].replace('_',' ').title()} for e in events]),hide_index=True,width='stretch')
            else: st.write('Approval, dispatch and receipt will appear here as you complete them.')
    elif step==3:
        st.subheader('What if demand rises tomorrow?')
        st.write('Change conditions for the receiving facility and this medicine. The comparison uses the original walkthrough snapshot, so it is repeatable after delivery.')
        c1,c2=st.columns(2)
        rise=c1.selectbox('Demand increase',[25,50,100],index=1,format_func=lambda n:f'{n}% more demand',key='walk_rise')
        delay=c2.selectbox('Supplier delay',[0,2,3,5],index=2,format_func=lambda n:f'{n} extra days',key='walk_delay')
        if st.button('Recalculate for this disruption',type='primary',key='walk_stress'):
            with st.spinner('Checking the changed demand, donor reserves and delivery dates…'):
                result=stress_case(json.dumps(case['dataset'].payload()),case['predictions'],recipient,mid,1+rise/100,delay)
                st.session_state['walk_stress_result']=(rise,delay,result)
        stored=st.session_state.get('walk_stress_result')
        if stored and stored[:2]==(rise,delay):
            before,after,plan=stored[2]
            b=before.risks.query('facility_id == @recipient and medicine_id == @mid').iloc[0]
            a=after.risks.query('facility_id == @recipient and medicine_id == @mid').iloc[0]
            c1,c2=st.columns(2)
            c1.metric('7-day shortage before sharing',f'{b.unmet7:,.0f} {units[mid]}')
            c2.metric('7-day shortage after sharing',f'{a.unmet7:,.0f} {units[mid]}')
            relevant=[r for r in plan.recommendations if r['facility_id']==recipient and r['medicine_id']==mid]
            st.write(f'The updated plan proposes **{sum(r["kind"]=="transfer" for r in relevant)} transfers** and **{sum(r["quantity"] for r in relevant if r["kind"]=="reorder")} units to order** for this facility and medicine.')
            if a.unmet7>0: st.warning('Some early demand remains uncovered. The plan makes this gap visible; it needs urgent sourcing or further review.')
            st.caption('Forecast projection under selected assumptions. Inventory and saved actions are unchanged by this test.')
            with st.expander('Inspect the changed recommendations'):
                for r in relevant: st.write(r['explanation'])
        elif stored:
            st.info('The inputs changed. Recalculate to see matching results.')
    else:
        st.subheader('The story to tell the judges')
        avoided=max(0,rec['recipient_before_unmet7']-rec['recipient_after_unmet7'])
        st.write(f'“We spotted a likely shortage of {medicines[mid]} at {facilities[recipient]}. Stockout Shield found a usable batch at {facilities[donor]} and recommended {rec["quantity"]} {units[mid]}. We inspected the donor’s supply, tracked the transfer and tested a demand disruption.”')
        c1,c2=st.columns(2)
        c1.metric('Projected 7-day shortage avoided',f'{avoided:,.0f} {units[mid]}')
        c2.metric('Recorded delivery status',state.title())
        st.caption('The shortage reduction is a forecast comparison for this transfer, not an observed patient outcome. The original 20-seed policy benchmark is available in Scenario Lab.')
        st.write('**What makes the project useful:** each recommendation explains the medicine, source, batch, quantity and impact on both facilities.')
        stats=case['dataset'].tables
        st.write(f'This workspace includes **{len(stats["facilities"])} facilities**, **{len(stats["medicines"])} medicines**, **{len(stats["usage"]):,} dispensing records** and **{len(stats["batches"])} batches**.')
        brief=f'''# Stockout Shield walkthrough\n\nSynthetic inventory and dispensing records; all movements are simulated.\n\nMedicine: {medicines[mid]}\nFrom: {facilities[donor]}\nTo: {facilities[recipient]}\nQuantity: {rec['quantity']} {units[mid]}\nBatch: {rec['batch_id']}\nArrival: {rec['arrival']}\nTransport: INR {rec['transfer_cost']:.2f}\nProjected seven-day shortage: {rec['recipient_before_unmet7']:.1f} to {rec['recipient_after_unmet7']:.1f} units for this transfer alone.\nSaved status: {state}\n\n## Presentation\n1. Explain the shortage warning.\n2. Inspect the batch and donor chart.\n3. Approve, dispatch, advance and receive.\n4. Increase demand and recalculate.\n5. Explain projected benefit and any remaining shortage.\n\nProjected benefits are not real-world outcome evidence.\n'''
        st.download_button('Download presentation notes',brief,'stockout-walkthrough.md',mime='text/markdown')
    with st.expander('Presenter notes · what to say'):
        st.write(notes[step])
    back,space,next_=st.columns([1,1,2])
    back.button('Previous step',disabled=step==0,on_click=set_step,args=(max(0,step-1),),key='walk_back')
    if step<4:
        next_.button(f'Next: {STEPS[step+1].lower()}',type='primary',on_click=set_step,args=(step+1,),key='walk_next',width='stretch')
    else:
        next_.button('Review the full action plan',type='primary',on_click=lambda:st.session_state.update(page='Action Plan'),width='stretch')
