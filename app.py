from __future__ import annotations
import io
import json
import os
from pathlib import Path
import time
import zipfile
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from stockout.data import Dataset, MODELS, empty_templates, read_workbook, validate_import, workbook_bytes
from stockout.sample import make_sample
from stockout.forecasting import forecast
from stockout.inventory import project_inventory, project_pair, apply_scenario, scale_predictions
from stockout.planning import generate_plan, Plan
from stockout.storage import Store
from stockout import ui
from stockout import workspace_ui as ops
from stockout.workflow import TERMINAL, action_state
from stockout.simulation import SCENARIOS, POLICIES, run_experiment

ROOT=Path(__file__).parent
st.set_page_config(page_title='Stockout Shield | Clinical supply workspace',page_icon='✚',layout='wide',initial_sidebar_state='auto')
st.markdown('<style>'+ (ROOT/'static'/'design.css').read_text() + '</style>',unsafe_allow_html=True)


@st.cache_resource
def get_store():
    return Store(os.environ.get('STOCKOUT_DB',str(ROOT/'runtime'/'stockout.db')))

@st.cache_data(show_spinner=False)
def starter_payload():
    return make_sample(profile='medium').payload()

@st.cache_data(show_spinner=False,max_entries=24)
def analysis(payload_json,reviewed_json):
    ds=Dataset.from_payload(json.loads(payload_json))
    preds=forecast(ds,reviewed=json.loads(reviewed_json))
    return preds,project_inventory(ds,preds)

@st.cache_data(show_spinner=False,max_entries=12)
def calculate_plan(payload_json,reviewed_json,excluded_json):
    ds=Dataset.from_payload(json.loads(payload_json))
    preds,_=analysis(payload_json,reviewed_json)
    return generate_plan(ds,preds,excluded_transfers=[tuple(x) for x in json.loads(excluded_json)])


@st.cache_data(show_spinner=False,max_entries=8)
def export_workbook(payload_json):
    return workbook_bytes(Dataset.from_payload(json.loads(payload_json)))


def table(df,**kwargs):
    st.dataframe(df,hide_index=True,width='stretch',**kwargs)


def clean_chart(fig,height=310,key=None):
    fig.update_layout(height=height,margin=dict(l=0,r=0,t=40,b=0),paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor='rgba(0,0,0,0)',font=dict(family='Inter',color='#536171',size=12),
        legend=dict(orientation='h',y=1.02,yanchor='bottom',x=0,font=dict(size=11)),colorway=['#005c55','#b77b27','#006781','#ba1a1a'])
    fig.update_xaxes(showgrid=False)
    fig.update_yaxes(gridcolor='#e3e8f0',zeroline=False)
    st.plotly_chart(fig,width='stretch',config={'displayModeBar':False},key=key)

store=get_store()
wid=st.query_params.get('workspace','')
if not wid or not store.exists(wid):
    wid=store.create_workspace(Dataset.from_payload(starter_payload()))
    st.query_params['workspace']=wid
if st.session_state.get('workspace')!=wid:
    st.session_state['workspace']=wid
    st.session_state['reviewed']={}
    st.session_state.pop('experiment',None)
    st.session_state.pop('current_plan',None)
    st.session_state.pop('scenario_preview',None)
    for name in ['walk_case','walk_step','walk_stage','walk_stress_result','walk_rise','walk_delay','prepared_export']:
        st.session_state.pop(name,None)
    st.session_state['page']='Overview'
    st.session_state['selected_facility']='All'
    st.session_state['selected_medicine']='All'
    st.session_state['action_view']='Recommendations'
reviewed=st.session_state.setdefault('reviewed',{})
ds=store.planning_dataset(wid)
physical=store.load(wid)
payload_json=json.dumps(ds.payload(),sort_keys=True)
reviewed_json=json.dumps(reviewed,sort_keys=True)
with st.spinner('Reading inventory and forecasting demand…'):
    preds,projection=analysis(payload_json,reviewed_json)
meds=ds.tables['medicines'].set_index('medicine_id')
facs=ds.tables['facilities'].set_index('facility_id')
med_label={m:f'{r.ingredient} · {r.strength} · {r.formulation}' for m,r in meds.iterrows()}
fac_label=facs.name.to_dict()
unit_label=meds.unit.to_dict()

def select_page(name,facility=None,medicine=None,view=None):
    st.session_state['page']=name
    if facility is not None: st.session_state['selected_facility']=facility
    if medicine is not None: st.session_state['selected_medicine']=medicine
    if view is not None: st.session_state['action_view']=view
    st.session_state['plan_page']=1
    st.session_state['tracking_page']=1
    st.session_state['supply_page']=1
    if name=='Action Plan': st.session_state['action_kind']='All actions'


def clear_context():
    st.session_state['selected_facility']='All'
    st.session_state['selected_medicine']='All'
    st.session_state['stock_search']=''
    st.session_state['stock_status']='All stock'
    st.session_state.pop('focus_action',None)
    reset_pages()


def reset_pages():
    for key in ['plan_page','tracking_page','supply_page']: st.session_state[key]=1


network_name={'Five-centre demonstration':'Care network','Imported demonstration':'Imported inventory'}.get(ds.label,ds.label)
navigation=[('Overview',':material/dashboard:'),('Inventory & Data',':material/inventory_2:'),
    ('Forecasts',':material/monitoring:'),('Action Plan',':material/local_shipping:'),('Scenario Analysis',':material/analytics:')]
nav_labels={'Inventory & Data':'Inventory','Action Plan':'Actions','Scenario Analysis':'Scenarios'}
page_names=[title for title,_ in navigation]
actions_state=store.action_history(wid)
events_state=store.events(wid)
active_count=sum(a['status'] not in TERMINAL for a in actions_state)
page=st.session_state.get('page','Overview')
if page not in page_names:
    page='Overview'
st.session_state['page']=page
with st.sidebar:
    ui.brand()
    ui.network_context(network_name,len(facs),len(meds),ds.as_of)
    with st.container(key='sidebar_nav'):
        for title,icon in navigation:
            label=nav_labels.get(title,title)
            if title=='Action Plan' and active_count: label+=f' ({active_count})'
            st.button(label,key='nav_'+title,icon=icon,type='primary' if page==title else 'secondary',
                width='stretch',on_click=select_page,args=(title,))
    st.divider()
    st.button('Refresh workspace',icon=':material/refresh:',width='stretch',help='Read the latest inventory and decisions saved by other sessions.')
    st.download_button('Export current stock',physical.tables['batches'].to_csv(index=False),
        'stockout-current-stock.csv',mime='text/csv',
        width='stretch',icon=':material/download:')
    with st.expander('Workspace details'):
        st.caption('Keep this link to return to this supply network and its saved decisions.')
        st.code(wid,language=None)
    st.divider()
    st.caption('Medicine supply planning\n\nPrepare · Redistribute · Reorder')

if st.session_state.get('flash'):
    message=st.session_state.pop('flash')
    st.toast(message,icon=':material/check_circle:')
    st.success(message)

ui.masthead(nav_labels.get(page,page),ds.as_of,network_name)
instructions={
    'Overview':'Monitor near-term shortages and expiry risk across the network.',
    'Inventory & Data':'Review current batches, incoming supply and source data. Import a new inventory snapshot when needed.',
    'Forecasts':'Inspect projected demand, stock availability and the evidence behind each estimate.',
    'Action Plan':'Review transfer recommendations, then record approvals and stock movements.',
    'Scenario Analysis':'Compare supply policies under changes to demand and delivery timing.'}
page_titles={'Overview':'Medicine supply overview','Inventory & Data':'Inventory & incoming supply','Forecasts':'Demand and stock forecasts','Action Plan':'Transfers & reorders','Scenario Analysis':'Scenario analysis'}
with st.container(key='page_heading'):
    st.title(page_titles[page])
    st.caption(instructions[page])

# One selection follows the user between stock, forecasts and recommendations.
for key,values in [('selected_facility',list(facs.index)),('selected_medicine',list(meds.index))]:
    if st.session_state.get(key) not in ['All']+values: st.session_state[key]='All'
    if page=='Forecasts' and st.session_state[key]=='All': st.session_state[key]=values[0]
with st.container(key='context_filters'):
    a,b=st.columns([1.25,2.2],vertical_alignment='bottom')
    options_f=list(facs.index) if page=='Forecasts' else ['All']+list(facs.index)
    options_m=list(meds.index) if page=='Forecasts' else ['All']+list(meds.index)
    selected_f=a.selectbox('Facility',options_f,format_func=lambda x:fac_label.get(x,'All facilities'),key='selected_facility',on_change=reset_pages)
    selected_m=b.selectbox('Medicine',options_m,format_func=lambda x:med_label.get(x,'All medicines'),key='selected_medicine',on_change=reset_pages)
    if page!='Forecasts' and (selected_f!='All' or selected_m!='All' or st.session_state.get('stock_search') or st.session_state.get('stock_status','All stock')!='All stock'):
        st.button('Show all',on_click=clear_context,help='Clear facility, medicine and stock filters.')
if selected_m!='All':
    spec=meds.loc[selected_m]
    st.caption(f'{spec.ingredient} · {spec.strength} · {spec.formulation} | Counted in {spec.unit} | Pack size: {spec.pack_size} {spec.unit}')




def enriched_risks():
    r=projection.risks.copy().round({'unmet7':1,'unmet30':1,'expiry_units':1})
    r['Facility']=r.facility_id.map(fac_label)
    r['Medicine']=r.medicine_id.map(med_label)
    r['Evidence']=r.actionable.map({True:'Ready to review',False:'Estimate needed'})
    return r


def get_plan(force=False):
    # Input-versioned caches stay valid for other browser sessions.
    excluded=[(a['payload']['from_facility'],a['payload']['to_facility'],a['payload']['medicine_id'],a['payload']['batch_id']) for a in store.action_history(wid) if a['status']=='rejected' and a['payload']['kind']=='transfer']
    p=calculate_plan(payload_json,reviewed_json,json.dumps(excluded))
    # A new stored identifier prevents an explicitly rejected plan from being reused by the UI.
    saved=st.session_state.get('current_plan')
    if force or saved is None or saved.dataset_version!=ds.version or saved.assumptions.get('reviewed')!=reviewed or saved.assumptions.get('_excluded')!=excluded:
        import copy,uuid
        p=copy.deepcopy(p)
        p.plan_id=uuid.uuid4().hex[:12]
        for rec in p.recommendations: rec['recommendation_id']=uuid.uuid4().hex[:12]
        p.assumptions['reviewed']=dict(reviewed)
        p.assumptions['_excluded']=excluded
        store.save_plan(wid,p)
        st.session_state['current_plan']=p
    return st.session_state['current_plan']


if page=='Overview':
    risk=projection.risks.copy()
    if selected_f!='All': risk=risk[risk.facility_id.eq(selected_f)]
    if selected_m!='All': risk=risk[risk.medicine_id.eq(selected_m)]
    h=int((risk.shortage_day<7).sum())
    expiry_batches=int(projection.expiries.batch_id.nunique())
    actions=actions_state
    pending=sum(a['status']=='approved' for a in actions)
    blocked=sum(not p.actionable for p in preds.values())
    ui.summary([
        ('Critical stock gaps',h,'Facility–medicine pairs within 7 days','urgent' if h else 'quiet'),
        ('Batches at expiry risk',expiry_batches,'Unused stock at expiry within 30 days','watch' if expiry_batches else 'quiet'),
        ('Approved actions',pending,'Awaiting dispatch or order recording','transit'),
        ('Data ready to plan',f'{len(preds)-blocked} / {len(preds)}','Facility–medicine pairs passing quality checks','quiet')])
    ui.section_title('Needs attention','Open an issue to inspect its medicine, facility and supply options.')
    attention=st.segmented_control('Attention category',['Shortages','Expiring stock','Deliveries'],default='Shortages',key='attention_category',label_visibility='collapsed')
    if attention=='Shortages':
        urgent=risk[risk.shortage_day<999].sort_values(['shortage_day','unmet7'],ascending=[True,False])
        if urgent.empty: st.success('No stock gaps projected for this selection over the next 30 days.')
        for r in urgent.head(5).to_dict('records'):
            with st.container(key='attention_'+r['facility_id']+'_'+r['medicine_id']):
                body,button=st.columns([4,1.2],vertical_alignment='center')
                day=int(r['shortage_day']);timing='today' if day==0 else f'in {day} '+('day' if day==1 else 'days')
                with body:
                    ui.attention_item(med_label[r['medicine_id']],f"{fac_label[r['facility_id']]} may run short {timing}",f"Estimated uncovered demand over 7 days: {r['unmet7']:,.0f} {unit_label[r['medicine_id']]}", 'urgent' if day<7 else 'watch')
                button.button('Review options' if r['actionable'] else 'Review demand',key='review-risk-'+r['facility_id']+r['medicine_id'],type='primary' if day<7 else 'secondary',width='stretch',on_click=select_page,args=('Action Plan' if r['actionable'] else 'Forecasts',r['facility_id'],r['medicine_id'],'Recommendations'))
        if len(urgent)>5: st.caption(f'Showing the first 5 of {len(urgent)} forecast stock gaps. Select a facility or medicine above to narrow the list.')
    elif attention=='Expiring stock':
        exp=projection.expiries.copy()
        if selected_f!='All':exp=exp[exp.facility_id.eq(selected_f)]
        if selected_m!='All':exp=exp[exp.medicine_id.eq(selected_m)]
        exp=exp.sort_values('expiry')
        if exp.empty:st.success('No unused stock is projected to expire in this selection over the next 30 days.')
        for r in exp.head(5).to_dict('records'):
            with st.container(key='attention_exp_'+r['batch_id']):
                body,button=st.columns([4,1.2],vertical_alignment='center')
                with body:ui.attention_item(med_label[r['medicine_id']],f"{fac_label[r['facility_id']]} · batch {r['batch_id']}",f"{r['quantity']:,.0f} {unit_label[r['medicine_id']]} may remain unused at expiry on {r['expiry']}",'watch')
                button.button('Review supply options',key='review-exp-'+r['batch_id'],width='stretch',on_click=select_page,args=('Action Plan',None,r['medicine_id'],'Recommendations'))
    else:
        incoming=[a for a in actions if a['status'] in ['dispatched','ordered'] and (selected_f=='All' or a['payload']['facility_id']==selected_f) and (selected_m=='All' or a['payload']['medicine_id']==selected_m)]
        incoming.sort(key=lambda a:a['payload']['arrival'])
        if not incoming:
            st.info('No transfers or reorders are currently in transit for this selection. Supplier deliveries are listed in Inventory.')
            st.button('Open incoming supply',on_click=select_page,args=('Inventory & Data',))
        for action in incoming[:5]:
            r=action['payload'];state,_,tone=action_state(action,ds.as_of)
            with st.container(key='attention_delivery_'+action['id']):
                body,button=st.columns([4,1.2],vertical_alignment='center')
                with body:ui.attention_item(med_label[r['medicine_id']],f"{fac_label[r['facility_id']]} · {state}",f"{r['quantity']:,} {unit_label[r['medicine_id']]} · expected {r['arrival']}",tone)
                button.button('Track delivery',key='track-attention-'+action['id'],width='stretch',on_click=select_page,args=('Action Plan',r['facility_id'],r['medicine_id'],'Tracking'))
    with st.expander('Availability across facilities',expanded=False):
        view_f={f:n for f,n in fac_label.items() if selected_f=='All' or f==selected_f}
        view_m={m:n for m,n in med_label.items() if selected_m=='All' or m==selected_m}
        ui.facility_board(risk,view_f,view_m)
    with st.container(key='overview_bottom'):
        left,right=st.columns([1,1],gap='medium')
        with left:
            ops.activity(events_state,actions,fac_label,med_label,unit_label)
            st.button('Open action tracking',width='stretch',on_click=select_page,args=('Action Plan',None,None,'Tracking'))
        with right:
            with st.container(key='network_exposure'):
                ui.section_title('Daily stock-gap exposure','Facility–medicine pairs with unmet demand under the current forecast.','30-day outlook')
                exposure=projection.curves.assign(gap=lambda frame:frame.unmet>1e-6).groupby('date',as_index=False).gap.sum()
                fig=go.Figure(go.Scatter(x=exposure.date,y=exposure.gap,mode='lines',fill='tozeroy',line=dict(color='#005c55',width=2),fillcolor='rgba(15,118,110,.07)',name='Pairs with a stock gap'))
                fig.update_yaxes(title='Pairs',rangemode='tozero',dtick=5)
                clean_chart(fig,220)
                st.caption(f'{blocked} demand estimates need review.' if blocked else 'All facility–medicine pairs currently pass the demand-quality gate.')
    last=ds.tables['usage'].date.max() if len(ds.tables['usage']) else 'No records'
    st.caption(f'Latest dispensing record: {last}. Forecast availability uses supplied records; review stock and demand before acting.')

elif page=='Inventory & Data':
    all_batches=physical.tables['batches']
    near_expiry=(all_batches.status.eq('available') & all_batches.expiry.gt(str(ds.as_of)) & all_batches.expiry.le(str(ds.as_of+pd.Timedelta(days=30))))
    ui.summary([
        ('Batches on record',len(all_batches),'Physical inventory across the network','quiet'),
        ('Near-expiry batches',int(near_expiry.sum()),'Usable batches expiring within 30 days','watch' if near_expiry.any() else 'quiet'),
        ('Quarantined batches',int(all_batches.status.ne('available').sum()),'Excluded from transfer allocation','urgent' if all_batches.status.ne('available').any() else 'quiet'),
        ('Incoming supplies',len(ds.tables['orders']),'Recorded deliveries and approved commitments','transit')])
    inspect,deliveries,upload,templates=st.tabs(['Stock register','Incoming supply','Import data','Templates & exports'])
    with inspect:
        history=physical.tables['usage']
        st.caption(f'{len(history):,} dispensing records · {history.date.min()} to {history.date.max()} · {len(facs)} facilities · {len(meds)} medicines')
        ops.stock_editor(store,wid,physical,fac_label,med_label,unit_label,selected_f,selected_m)
        search=st.text_input('Search stock',placeholder='Search a medicine, facility or batch number',key='stock_search')
        ff,mm=selected_f,selected_m
        stock_status=st.selectbox('Stock filter',['All stock','Usable','Expiring within 30 days','Quarantined','Expired'],key='stock_status')
        b=physical.tables['batches'].copy()
        if ff!='All': b=b[b.facility_id==ff]
        if mm!='All': b=b[b.medicine_id==mm]
        b['Facility']=b.facility_id.map(fac_label);b['Medicine']=b.medicine_id.map(med_label)
        if search.strip():
            term=search.strip()
            b=b[b['Facility'].str.contains(term,case=False,regex=False)|b['Medicine'].str.contains(term,case=False,regex=False)|b.batch_id.str.contains(term,case=False,regex=False)]
        b['Eligibility']=b.apply(lambda row:'Quarantined' if row.status!='available' else 'Expired' if row.expiry<=str(ds.as_of) else 'Usable',axis=1)
        if stock_status=='Expiring within 30 days':b=b[b.Eligibility.eq('Usable')&b.expiry.le(str(ds.as_of+pd.Timedelta(days=30)))]
        elif stock_status!='All stock':b=b[b.Eligibility.eq(stock_status)]
        b=b.sort_values(['expiry','batch_id'])
        b['Quantity']=b.quantity.astype(int)
        b['Unit']=b.medicine_id.map(unit_label)
        reservations={}
        for action in actions_state:
            if action['status']=='approved' and action['payload']['kind']=='transfer':
                rec=action['payload'];reservations[rec['batch_id']]=reservations.get(rec['batch_id'],0)+rec['quantity']
        b['Reserved']=b.batch_id.map(reservations).fillna(0).astype(int)
        ui.section_title('Batch stock register','Stock is ordered by expiry. Use column headings to sort and the table search to find a batch.',f'{len(b)} batches')
        table(b[['batch_id','Facility','Medicine','Quantity','Reserved','Unit','expiry','Eligibility']].rename(columns={'batch_id':'Batch','expiry':'Expiry date'}),height=min(460,38+35*max(1,len(b))))
        if b.empty:st.info('No stock matches these filters. Use Show all to clear the selection, or change the stock filter.')
        st.caption('Reserved stock remains physically present until dispatch. It cannot be allocated or reduced by a stock adjustment.')
        if mm!='All':
            a,c=st.columns(2)
            a.button('View demand forecast',on_click=select_page,args=('Forecasts',None,mm),width='stretch')
            c.button('Review supply options',on_click=select_page,args=('Action Plan',None,mm,'Recommendations'),width='stretch')
        with st.expander('Transfer routes and travel times'):
            table(ds.tables['routes'])
        with st.expander('Usage record sample and original availability flags'):
            g=physical.tables['usage']
            if ff!='All':g=g[g.facility_id==ff]
            if mm!='All':g=g[g.medicine_id==mm]
            table(g.tail(200))
    with deliveries:
        ops.incoming_supply(store,wid,ds,physical,fac_label,med_label,unit_label,selected_f,selected_m,select_page)
    with upload:
        ui.section_title('Import an inventory snapshot','Validate and review the records before creating a separate workspace.')
        st.caption('Use one workbook with six sheets, or six CSV files: facilities, medicines, batches, usage, orders and routes. Your current workspace is preserved.')
        upload_mode=st.radio('File format',['Excel workbook','Six CSV files'],horizontal=True)
        tables_in=None;metadata={}
        try:
            if upload_mode=='Excel workbook':
                file=st.file_uploader('Inventory workbook',type=['xlsx'])
                if file:tables_in,metadata=read_workbook(file.getvalue())
            else:
                files=st.file_uploader('CSV files',type=['csv'],accept_multiple_files=True,help='Name each file after its table, for example batches.csv.')
                if files:tables_in={Path(file.name).stem:pd.read_csv(file,dtype={'medicine_id':str,'facility_id':str}) for file in files}
            asof=st.date_input('Inventory snapshot date',value=pd.Timestamp(metadata.get('as_of',ds.as_of)).date())
            label=st.text_input('Network name',value='Imported inventory')
            mapping={}
            if tables_in is None:
                ui.import_steps(0)
                st.info('Choose your workbook or CSV files to begin. Templates are available in Templates & exports.')
            else:
                catalogue=tables_in.get('medicines',pd.DataFrame())
                known=set(catalogue.medicine_id.astype(str)) if 'medicine_id' in catalogue else set()
                used=set()
                for name in ['batches','usage','orders']:
                    frame=tables_in.get(name,pd.DataFrame())
                    if 'medicine_id' in frame:used.update(frame.medicine_id.dropna().astype(str))
                unknown=sorted(used-known)
                if unknown and known:
                    st.warning('Some medicine codes are not in the uploaded catalogue. Match each code explicitly; no automatic matching is applied.')
                    labels={str(r['medicine_id']):' | '.join(str(r.get(k,'')) for k in ['medicine_id','ingredient','strength','formulation','unit']) for r in catalogue.to_dict('records')}
                    choices={text:mid for mid,text in labels.items()}
                    import hashlib
                    mapping_key='mapping_'+hashlib.sha256(json.dumps([unknown,labels],sort_keys=True).encode()).hexdigest()[:12]
                    rows=pd.DataFrame({'Source code':unknown,'Catalogue medicine':['Choose medicine']*len(unknown)})
                    mapped=st.data_editor(rows,hide_index=True,width='stretch',disabled=['Source code'],num_rows='fixed',column_config={'Catalogue medicine':st.column_config.SelectboxColumn('Catalogue medicine',options=['Choose medicine']+list(choices),required=True)},key=mapping_key)
                    mapping={row['Source code']:choices[row['Catalogue medicine']] for row in mapped.to_dict('records') if row['Catalogue medicine'] in choices}
                candidate,issues=validate_import(tables_in,asof,mapping=mapping,synthetic=bool(metadata.get('synthetic',False)),label=label)
                ui.import_steps(1 if candidate is None else 2)
                if len(issues):
                    table(issues.rename(columns={'table':'Sheet','row':'Spreadsheet row','severity':'Severity','message':'What needs attention'}))
                if candidate is None:
                    st.error('Resolve the listed errors, then review the updated validation report. Uploaded data has not changed your workspace.')
                else:
                    st.success(f'Ready to review: {len(candidate.tables["facilities"])} facilities, {len(candidate.tables["medicines"])} medicines and {len(candidate.tables["batches"])} batches.')
                    st.write('Batch preview');table(candidate.tables['batches'].head(12))
                    checked=st.checkbox('I have checked the snapshot date, medicine specifications and batch quantities',key='confirm_import')
                    if st.button('Create workspace from this import',type='primary'):
                        if not checked:st.error('Check the import confirmation before creating the workspace.')
                        else:
                            new=store.create_workspace(candidate)
                            st.query_params['workspace']=new
                            st.session_state['flash']='Inventory imported into a separate saved workspace.'
                            st.session_state.pop('current_plan',None)
                            st.rerun()
        except (ValueError,KeyError,TypeError) as exc:
            st.error(f'This file could not be imported: {exc}. Check the table names and required columns using the templates.')
    with templates:
        st.write('Use medicine identifiers from the catalogue. Quantities are in the catalogue base unit; pack size controls transfer and reorder increments.')
        regional_workbook=ROOT/'data'/'medium'/'sample.xlsx'
        if regional_workbook.exists():
            st.download_button('Download example workspace workbook',regional_workbook.read_bytes(),'stockout-example-workspace.xlsx')
        if st.button('Prepare complete workspace workbook'):
            with st.spinner('Preparing the complete history and inventory workbook…'):
                st.session_state['prepared_export']=(physical.version,export_workbook(json.dumps(physical.payload(),sort_keys=True)))
        prepared=st.session_state.get('prepared_export')
        if prepared and prepared[0]==physical.version:
            st.download_button('Download complete workspace workbook',prepared[1],'stockout-workspace.xlsx')
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for name,frame in empty_templates().items():z.writestr(name+'.csv',frame.to_csv(index=False))
        st.download_button('Download empty CSV templates',buf.getvalue(),'stockout-templates.zip')
        for name,model in MODELS.items():
            with st.expander(name.replace('_',' ').title()):
                table(pd.DataFrame([{'Field':field,'Required':info.is_required(),'Type':str(info.annotation)} for field,info in model.model_fields.items()]))
        st.caption('Availability must be available, stockout, or unknown. A blank/missing day is unknown, never zero. Expiry dates are exclusive: a batch is unusable on its expiry date.')

elif page=='Forecasts':
    f,m=selected_f,selected_m
    stock_link,plan_link=st.columns(2)
    stock_link.button('Inspect this medicine’s stock',on_click=select_page,args=('Inventory & Data',f,m),width='stretch')
    plan_link.button('Review supply options',on_click=select_page,args=('Action Plan',f,m,'Recommendations'),type='primary',width='stretch')
    p=preds[f,m]
    pair_risk=projection.risks[(projection.risks.facility_id==f)&(projection.risks.medicine_id==m)].iloc[0]
    curve=projection.curves[(projection.curves.facility_id==f)&(projection.curves.medicine_id==m)].copy()
    ui.forecast_risk(pair_risk,fac_label[f],med_label[m],unit_label[m])
    ui.summary([
        ('Expected demand / 7 days',f'{sum(p.mean[:7]):,.0f}',unit_label[m],'quiet'),
        ('Empirical 80% range',f'{p.lower7:,.0f}–{p.upper7:,.0f}' if p.lower7 is not None else 'Unavailable','Seven-day total demand','quiet'),
        ('Reliable history',f'{p.reliable_days:,}','Days recorded with stock available','quiet'),
        ('Unreliable recent records',f'{p.unreliable_fraction:.0%}','Missing or availability-affected records','watch' if p.unreliable_fraction>.2 else 'stable')])
    with st.container(key='forecast_trajectory'):
        ui.section_title('Stock outlook for the next 30 days','Central forecast, upper-demand stress and expected supplier arrivals.','FEFO projection')
        upper_curve=pd.DataFrame(project_pair(ds,(f,m),p.upper)[0])
        fig=go.Figure()
        incoming=ds.tables['orders']
        incoming=incoming[(incoming.facility_id==f)&(incoming.medicine_id==m)]
        incoming=incoming[incoming.arrival.isin(curve.date)]
        if len(incoming):
            fig.add_bar(x=incoming.arrival,y=incoming.quantity,name='Incoming supply',marker_color='#a3d5df',opacity=.65)
        fig.add_scatter(x=curve.date,y=curve.stock,name='Central stock forecast',line=dict(color='#005c55',width=2.5),fill='tozeroy',fillcolor='rgba(15,118,110,.06)')
        fig.add_scatter(x=upper_curve.date,y=upper_curve.stock,name='Upper-demand stress stock',line=dict(color='#ba1a1a',width=1.5,dash='dot'))
        if pair_risk.shortage_day<999:
            fig.add_vrect(x0=pair_risk.first_shortage,x1=curve.date.iloc[-1],fillcolor='#ba1a1a',opacity=.045,line_width=0,layer='below')
        fig.update_yaxes(title=unit_label[m].title(),rangemode='tozero')
        clean_chart(fig,330)
        st.caption('The stress line is a planning assumption, not a daily prediction interval. Forecast stock is unavailable on the batch expiry date.')
    ui.depletion_table(curve,unit_label[m],int(facs.loc[f,'reserve_days']))
    a,b=st.columns(2,gap='medium')
    with a:
        with st.container(key='model_diagnostics'):
            ui.section_title('Model diagnostics','Method selection uses only earlier reliable observations.','Historical validation')
            st.markdown(f'**{p.method}**')
            st.caption(f'Rolling seven-day validation MAE: {p.validation_mae7:.2f} units.' if p.validation_mae7 is not None else 'No clean validation windows available.')
            st.caption('Candidates: 7/28-day averages, weekly seasonal naïve, Croston-SBA, and annual seasonal naïve with sufficient history. Ties favor the simpler method.')
            st.caption(f'Calibration windows: {p.calibration_windows}. Held-out coverage: {p.coverage:.0%} across 2 windows.' if p.coverage is not None else 'Uncertainty unavailable; +25% demand is an explicit stress assumption.')
            for warning in p.warnings: st.caption(warning)
    with b:
        with st.container(key='demand_review'):
            ui.section_title('Review a demand estimate','A staff-reviewed estimate can be used when history is incomplete.')
            if p.actionable: st.success('Eligible for a reviewed recommendation')
            else: st.warning('Quantity recommendations require a reviewed daily estimate.')
            with st.form('review_estimate'):
                val=st.number_input('Reviewed daily demand estimate',min_value=0.0,value=float(sum(p.mean[:7])/7),step=1.0)
                checked=st.checkbox('I have reviewed this daily demand estimate')
                if st.form_submit_button('Use reviewed estimate'):
                    if not checked:st.error('Confirm that you have reviewed the estimate before saving.')
                    else:
                        reviewed[f'{f}|{m}']=val
                        ops.saved('Reviewed demand estimate saved. Supply options will use this estimate.')
    with st.expander('Historical dispensing and daily demand forecast'):
        g=ds.tables['usage'];g=g[(g.facility_id==f)&(g.medicine_id==m)].sort_values('date').tail(90)
        dates=pd.date_range(ds.as_of,periods=len(p.mean))
        history_fig=go.Figure()
        history_fig.add_scatter(x=g.date,y=g.dispensed,name='Recorded dispensing',line=dict(color='#82919b',width=1.3))
        bad=g[g.availability!='available']
        history_fig.add_scatter(x=bad.date,y=bad.dispensed,name='Availability affected',mode='markers',marker=dict(color='#9b570d',size=6,symbol='diamond'))
        history_fig.add_scatter(x=dates,y=p.mean,name='Demand forecast',line=dict(color='#005c55',width=2))
        history_fig.add_scatter(x=dates,y=p.upper,name='Upper-demand stress assumption',line=dict(color='#9b570d',dash='dot'))
        history_fig.update_yaxes(title='Units per day')
        clean_chart(history_fig,300)
        st.caption('Missing and stockout-affected observations are estimated for modelling using earlier reliable history. Source records are preserved. The empirical range refers to total seven-day demand.')

elif page=='Action Plan':
    st.session_state.setdefault('action_view','Recommendations')
    action_view=st.segmented_control('Action workspace',['Recommendations','Tracking','History'],key='action_view')
    if action_view=='Recommendations':
        with st.spinner('Checking supply options and donor protection…'):
            plan=get_plan()
        ui.summary([
            ('Transfers proposed',sum(r['kind']=='transfer' for r in plan.recommendations),'Eligible batches with donor checks','stable'),
            ('Reorders proposed',sum(r['kind']=='reorder' for r in plan.recommendations),'Remaining supply needs','transit'),
            ('Transport estimate',f'INR {sum(r.get("transfer_cost",0) for r in plan.recommendations):,.0f}','For the full transfer plan','quiet'),
            ('Arrival warnings',sum(r.get('arrives_too_late',False) for r in plan.recommendations),'Earlier shortages still need attention','watch')])
        c1,c2=st.columns([3,1],vertical_alignment='bottom')
        target=selected_f
        selected_medicine=selected_m
        filter_kind=c1.selectbox('Action type',['All actions','Transfers','Reorders'],key='action_kind',on_change=reset_pages)
        if c2.button('Refresh supply options',width='stretch'):
            get_plan(force=True);st.rerun()
        st.caption(f'Checked for inventory dated {ds.as_of:%d %b %Y}. Recommendations update after stock changes or action decisions.')
        with st.expander('How this plan was checked'):
            st.caption(f'{plan.solver}. Dataset version {plan.dataset_version[:8]}.')
            for note in plan.notes:st.write('• '+note)
            st.json(plan.assumptions)
        pending_transfers=any(r['kind']=='transfer' for r in plan.recommendations)
        if filter_kind=='Reorders' and pending_transfers:
            st.info('These quantities assume the proposed transfers go ahead. Decide transfers and regenerate before approving reorders.')
        display=[r for r in plan.recommendations if filter_kind=='All actions' or r['kind']==('transfer' if filter_kind=='Transfers' else 'reorder')]
        if not display:st.info('No recommendations in this category. The plan will explain if stock cannot be shared safely.')
        if target!='All': display=[r for r in display if r['facility_id']==target]
        if selected_medicine!='All':display=[r for r in display if r['medicine_id']==selected_medicine]
        if not display and (target!='All' or selected_medicine!='All'):
            st.info('No recommendations match these filters. Choose All facilities or All medicines to see the complete plan.')
        total=len(display)
        if total>5:
            st.session_state['plan_page']=min(st.session_state.get('plan_page',1),(total+4)//5)
            page_number=st.selectbox('Recommendations page',list(range(1,(total+4)//5+1)),format_func=lambda x:f'Page {x} of {(total+4)//5}',key='plan_page')
        else: page_number=1
        if total: st.caption(f'Showing {(page_number-1)*5+1}–{min(page_number*5,total)} of {total} actions. Review one action at a time; the remaining plan refreshes after each decision.')
        for r in display[(page_number-1)*5:page_number*5]:
            with st.container(border=True,key='recommendation_'+r['recommendation_id']):
                ui.action_header(r,fac_label,med_label,unit_label)
                if r['kind']=='transfer':
                    st.caption(f'Arrival: {r["arrival"]} · Donor reserve checked under central and higher-demand assumptions.')
                with st.expander('Donor protection and recipient benefit',expanded=False):
                    ui.allocation_comparison(r,fac_label,unit_label)
                with st.expander('Why this recommendation'):
                    st.write(r['explanation'])
                if r.get('arrives_too_late'):st.warning('Earlier shortage remains uncovered. Routine delivery cannot address all urgency.')
                if r['kind']=='transfer':
                    with st.expander('Inspect donor protection and recipient benefit'):
                        graph=go.Figure()
                        dates=pd.date_range(ds.as_of,periods=30)
                        graph.add_scatter(x=dates,y=r['donor_before'],name='Donor before',line=dict(color='#91a3a9',dash='dot'))
                        graph.add_scatter(x=dates,y=r['donor_after'],name='Donor after combined plan',line=dict(color='#005c55'))
                        clean_chart(graph,240,key='donor_chart_'+r['recommendation_id'])
                        st.caption(f'Recipient unmet demand in first 7 days: {r["recipient_before_unmet7"]:.1f} → {r["recipient_after_unmet7"]:.1f} units for the combined plan. Both central and upper-demand scenarios checked.')
                with st.expander('Add a review note'):
                    comment=st.text_input('Note',key='comment-'+r['recommendation_id'],label_visibility='collapsed',placeholder='Add context for this decision')
                c,b,a=st.columns([2,1,1.5])
                disabled=r['kind']=='reorder' and pending_transfers
                if disabled:st.info('Decide the proposed transfers first, then refresh supply options. Reorder quantities depend on those transfers.')
                if a.button('Approve transfer' if r['kind']=='transfer' else 'Approve reorder',key='approve-'+r['recommendation_id'],type='primary',width='stretch',disabled=disabled):
                    try:
                        store.decide(wid,plan.plan_id,r['recommendation_id'],True,comment)
                        st.session_state.pop('current_plan',None)
                        st.session_state['flash']='Action approved and saved. The remaining recommendations have been recalculated.'
                        st.rerun()
                    except ValueError as exc:st.error(str(exc))
                c.button('View medicine',key='rec-medicine-'+r['recommendation_id'],on_click=select_page,args=('Inventory & Data',r['facility_id'],r['medicine_id']))
                if b.button('Reject',key='reject-'+r['recommendation_id'],width='stretch'):
                    try:
                        store.decide(wid,plan.plan_id,r['recommendation_id'],False,comment)
                        st.session_state.pop('current_plan',None)
                        st.session_state['flash']='Decision recorded. Rejected transfer routes/batches are excluded from subsequent plans in this workspace.'
                        st.rerun()
                    except ValueError as exc:st.error(str(exc))
        flat=pd.DataFrame([{k:v for k,v in r.items() if not isinstance(v,(list,dict))} for r in plan.recommendations])
        st.download_button('Download this action plan · CSV',flat.to_csv(index=False),'stockout-action-plan.csv')
        st.download_button('Download versioned plan · JSON',json.dumps(plan.payload(),indent=2),'stockout-action-plan.json')
    elif action_view=='Tracking':
        ops.track_actions(store,wid,ds,physical,actions_state,fac_label,med_label,unit_label,selected_f,selected_m,select_page)
        with st.expander('Advanced planning tools'):
            st.caption('Advancing the planning date consumes forecast demand and changes saved stock and usage. Use this only to evaluate a future inventory position; it does not record actual dispensing.')
            days=st.number_input('Days to advance',min_value=1,max_value=7,value=1)
            confirm=st.checkbox('Apply forecast-based stock changes to this workspace')
            if st.button('Advance planning date'):
                if not confirm:st.error('Confirm the forecast-based stock changes before advancing.')
                else:
                    try:
                        store.advance_clock(wid,int(days),preds)
                        ops.saved(f'Planning date advanced by {days} day(s) using forecast demand.')
                    except ValueError as exc:st.error(str(exc))
    elif action_view=='History':
        ops.activity(events_state,actions_state,fac_label,med_label,unit_label,limit=15)
        with st.expander('Saved plans'):
            saved_plans=store.list_plans(wid)
            table(pd.DataFrame([{'Plan':p['plan_id'],'Dataset version':p['dataset_version'],'Method':p['solver'],'Actions':len(p['recommendations'])} for p in saved_plans]))
        history_rows=pd.DataFrame([{'Action':a['id'],'Kind':a['payload']['kind'],'Medicine':med_label.get(a['payload']['medicine_id']),'Quantity':f"{a['payload']['quantity']:,} {unit_label[a['payload']['medicine_id']]}",'Status':action_state(a,ds.as_of)[0],'Comment':a['comment']} for a in actions_state])
        table(history_rows)
        st.download_button('Export audit ledger',json.dumps(events_state,indent=2),'stockout-ledger.json')

elif page=='Scenario Analysis':
    live,experiments=st.tabs(['What changes if…','Compare policies'])
    with live:
        with st.container(key='scenario_controls'):
            ui.section_title('Stress conditions','Change demand, delivery timing or route availability, then recalculate the plan.','Separate scenario')
            c1,c2=st.columns(2)
            target=c1.selectbox('Affected facility',['All facilities']+list(facs.index),index=0 if selected_f=='All' else list(facs.index).index(selected_f)+1,format_func=lambda x:fac_label.get(x,x))
            medicine=c2.selectbox('Affected medicine',['All medicines']+list(meds.index),index=0 if selected_m=='All' else list(meds.index).index(selected_m)+1,format_func=lambda x:med_label.get(x,x))
            c1,c2,c3=st.columns(3)
            surge=c1.slider('Demand multiplier',.5,3.0,1.5,.1)
            delay=c2.slider('Extra delivery days',0,10,3)
            closed=c3.checkbox('Close affected transfer routes')
            if st.button('Test these assumptions',type='primary'):
                with st.spinner('Forking the scenario and checking a fresh plan…'):
                    fork=apply_scenario(ds,target,medicine,surge,delay,closed)
                    pp=scale_predictions(preds,target,medicine,surge)
                    before=project_inventory(ds,preds)
                    after=project_inventory(fork,pp)
                    scenario_plan=generate_plan(fork,pp)
                    moved=project_inventory(fork,pp,[r for r in scenario_plan.recommendations if r['kind']=='transfer'])
                    st.session_state['scenario_preview']=(ds.version,after,moved,scenario_plan,target,medicine,surge,delay,closed)
        preview=st.session_state.get('scenario_preview')
        if preview and (preview[0]!=ds.version or preview[4:]!=(target,medicine,surge,delay,closed)):
            st.info('Conditions have changed. Test these assumptions again to see an updated result.')
        if preview and preview[0]==ds.version and preview[4:]==(target,medicine,surge,delay,closed):
            _,after,moved,sp,tf,tm,ts,td,tc=preview
            st.caption(f'Tested: {fac_label.get(tf,tf)} · {med_label.get(tm,tm)} · demand ×{ts:.1f} · +{td} delivery days · routes {"closed" if tc else "open"}. This is a separate scenario; saved inventory is unchanged.')
            cols=st.columns(3)
            cols[0].metric('Unmet units before transfers',f'{after.risks.unmet30.sum():,.0f}')
            cols[1].metric('Unmet units after transfers',f'{moved.risks.unmet30.sum():,.0f}')
            cols[2].metric('Safe transfer suggestions',sum(r['kind']=='transfer' for r in sp.recommendations))
            table(pd.DataFrame([{k:v for k,v in r.items() if k in ['kind','from_facility','facility_id','medicine_id','quantity','arrival','explanation']} for r in sp.recommendations]))
            st.caption('These are forecast projections, not a measured result. Use policy comparison below for an experiment with held-out demand.')
    with experiments:
        st.info('Compare three supply policies under the same starting inventory, demand assumptions, delivery conditions and budget. Results estimate outcomes under the selected scenarios.')
        scenarios=st.multiselect('Predefined scenarios',list(SCENARIOS),default=['Normal operations','Local demand surge','Little transferable surplus'])
        seeds=st.select_slider('Random seeds per scenario',[1,3,5,10,20],value=1)
        st.caption('One seed is a quick preview. Use all 20 seeds for the final report; larger runs can take several minutes.')
        with st.expander('What each experiment changes'):
            for name,desc in SCENARIOS.items():st.write(f'**{name}:** {desc}')
        if st.button('Run fair comparison',type='primary',disabled=not scenarios):
            progress=st.progress(0,text='Preparing forecasts…')
            start=time.perf_counter()
            def callback(done,total,scenario,seed):progress.progress(done/total,text=f'{done}/{total} runs · {scenario} · seed {seed}')
            experiment=run_experiment(physical,seeds=range(seeds),scenarios=scenarios,progress=callback)
            st.session_state['experiment']=(physical.version,experiment,time.perf_counter()-start)
            progress.empty()
        stored=st.session_state.get('experiment')
        if stored and stored[0]==physical.version:
            _,experiment,elapsed=stored
            st.success(f'Comparison complete · {len(experiment.runs)} policy runs · {elapsed:.1f}s')
            metric=st.selectbox('Compare outcome',['unmet_units','stockout_days','expired_units','expiry_value','procurement_cost','transfer_cost'],format_func=lambda x:x.replace('_',' ').title())
            fig=px.bar(experiment.summary,x='scenario',y=metric,color='policy',barmode='group',labels={metric:metric.replace('_',' ').title(),'scenario':'','policy':''})
            clean_chart(fig,360)
            table(experiment.summary.round(2))
            with st.expander('Check individual facilities'):
                grouped=experiment.facility_results.groupby(['scenario','policy','facility_id']).agg(
                    unmet_units=('unmet_units','mean'),stockout_days=('stockout_days','mean'),expired_units=('expired_units','mean')).reset_index()
                table(grouped.round(2))
                st.caption('The table averages facility–medicine observations. Download full results to inspect each seed and medicine.')
            buf=io.BytesIO()
            with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
                for name in ['summary','runs','facility_results','daily_results']:
                    z.writestr(name+'.csv',getattr(experiment,name).to_csv(index=False))
                z.writestr('settings.json',json.dumps(experiment.settings,indent=2))
            st.download_button('Download reproducible experiment results',buf.getvalue(),'stockout-experiment.zip')
        elif (ROOT/'reports'/'summary.csv').exists():
            ui.section_title('Original five-centre benchmark','Reference outcomes for the original 5-facility, 10-medicine fixture. Run a new comparison above for this workspace.','20 fixed seeds')
            reference=pd.read_csv(ROOT/'reports'/'summary.csv')
            chosen=st.selectbox('Reference scenario',list(SCENARIOS),key='reference_scenario')
            selected=reference[reference.scenario==chosen].copy()
            measure=st.selectbox('Reference outcome',['unmet_units','stockout_days','expired_units','procurement_cost','transfer_cost'],format_func=lambda x:x.replace('_',' ').title(),key='reference_outcome')
            fig=px.bar(selected,x='policy',y=measure,color='policy',color_discrete_sequence=['#8b99aa','#005c55','#006781'],labels={'policy':'',measure:measure.replace('_',' ').title()})
            fig.update_layout(showlegend=False)
            clean_chart(fig,280)
            names={'policy':'Supply policy','unmet_units':'Unmet units','stockout_days':'Stockout days','expired_units':'Expired units','procurement_cost':'Procurement INR','transfer_cost':'Transport INR'}
            table(selected[list(names)].rename(columns=names).round(1))
            st.caption('Values are means across 20 repeatable model runs. Stockout days count facility–medicine days with unmet demand. Costs use catalogue reference prices.')
            facility_path=ROOT/'reports'/'facility_results.csv'
            if facility_path.exists():
                with st.expander('Facility-level outcomes and donor verification'):
                    local=pd.read_csv(facility_path)
                    local=local[local.scenario==chosen]
                    per_seed=local.groupby(['policy','seed','facility_id'],as_index=False)[['unmet_units','stockout_days','expired_units']].sum()
                    local_means=per_seed.groupby(['policy','facility_id'],as_index=False)[['unmet_units','stockout_days','expired_units']].mean()
                    local_means['Facility']=local_means.facility_id.map(fac_label).fillna(local_means.facility_id)
                    table(local_means[['Facility','policy','unmet_units','stockout_days','expired_units']].rename(columns={'policy':'Supply policy','unmet_units':'Unmet units','stockout_days':'Stockout days','expired_units':'Expired units'}).round(1))
                    st.caption('Check each facility as well as network totals; comparisons do not establish performance at real facilities.')
            st.download_button('Export reference comparison',reference.to_csv(index=False),'stockout-reference-comparison.csv',icon=':material/download:')

st.markdown('<div class="footer">Stockout Shield · Medicine supply management</div>',unsafe_allow_html=True)
