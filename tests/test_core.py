from datetime import timedelta
from dataclasses import replace
import pandas as pd
import numpy as np
import pytest
from stockout.data import validate_import,read_workbook,workbook_bytes
from stockout.forecasting import forecast,causal_fill,predict_values,METHODS
from stockout.inventory import project_inventory,project_pair
from stockout.planning import generate_plan
from stockout.storage import Store
from stockout.simulation import run_experiment,POLICIES


def test_workbook_roundtrip(tiny):
    tables,meta=read_workbook(workbook_bytes(tiny));ds,issues=validate_import(tables,meta['as_of'])
    assert ds is not None and ds.version==tiny.version


def test_explicit_mapping_and_conflicting_spec(tiny):
    t={k:v.copy() for k,v in tiny.tables.items()};t['batches'].loc[0,'medicine_id']='local-name'
    ds,issues=validate_import(t,tiny.as_of)
    assert ds is None and issues.message.str.contains('explicit mapping').any()
    ds,issues=validate_import(t,tiny.as_of,mapping={'local-name':'P'});assert ds is not None
    t['batches']['strength']='250 mg'
    ds,issues=validate_import(t,tiny.as_of,mapping={'local-name':'P'})
    assert ds is None and issues.message.str.contains('conflicts').any()


def test_duplicates_future_and_nonfinite(tiny):
    t={k:v.copy() for k,v in tiny.tables.items()};t['batches']=pd.concat([t['batches'],t['batches'].iloc[:1]])
    ds,issues=validate_import(t,tiny.as_of);assert ds is None
    t={k:v.copy() for k,v in tiny.tables.items()};t['usage'].loc[0,'date']=str(tiny.as_of)
    ds,issues=validate_import(t,tiny.as_of);assert ds is None and issues.message.str.contains('leakage').any()
    t={k:v.copy() for k,v in tiny.tables.items()};t['medicines'].loc[0,'unit_cost']=float('inf')
    ds,issues=validate_import(t,tiny.as_of);assert ds is None


def test_causal_fill_and_zero():
    raw=np.array([5.,0.,np.nan,1000.]);reliable=np.array([True,True,False,True])
    filled=causal_fill(raw,reliable)
    assert filled[1]==0 and filled[2]==2.5 and np.isnan(raw[2])


def test_forecast_selection_and_bands(tiny):
    p=forecast(tiny)['A','P']
    assert p.method==METHODS[0] and p.mean==[10.]*30
    assert p.lower7==70 and p.upper7==70 and p.coverage==1 and p.dataset_version==tiny.version


def test_quality_gate_review_and_missing(tiny):
    use=tiny.tables['usage'];tiny.tables['usage']=use.drop(use[(use.facility_id=='A')&(use.medicine_id=='P')].index[:10])
    p=forecast(tiny)['A','P'];assert p.unreliable_fraction>0 and p.mean[0]==10
    tiny.tables['usage']=use.iloc[:20].copy();assert not forecast(tiny)['A','P'].actionable
    p=forecast(tiny,reviewed={'A|P':12})['A','P']
    assert p.actionable and p.mean[0]==12 and p.upper[0]==15 and p.upper7 is None


def test_croston_intermittent():
    assert np.all(predict_values(np.zeros(100),METHODS[3])==0)
    p=predict_values(np.tile([0,0,0,8],30),METHODS[3]);assert 1<p[0]<3 and np.all(p>=0)


def test_shared_donor_reserve_pack_and_identity(tiny):
    pp=forecast(tiny);plan=generate_plan(tiny,pp)
    transfers=[r for r in plan.recommendations if r['kind']=='transfer'];assert transfers
    assert sum(r['quantity'] for r in transfers)<=230
    assert all(r['quantity']%10==0 and r['medicine_id']=='P' and r['batch_id']!='B-Q' for r in transfers)
    before=project_inventory(tiny,pp);after=project_inventory(tiny,pp,transfers)
    assert after.curves.unmet.sum()<before.curves.unmet.sum()
    for mode in ['mean','upper']:
        rows,_=project_pair(tiny,('B','P'),getattr(pp['B','P'],mode),transfers)
        assert min(r['stock'] for r in rows)>=70-1e-6


def test_expired_quarantined_and_no_safe_donor(tiny):
    tiny.tables['batches'].loc[1,'expiry']=str(tiny.as_of+timedelta(days=1))
    plan=generate_plan(tiny,forecast(tiny))
    assert not [r for r in plan.recommendations if r['kind']=='transfer']
    assert any(r['kind']=='reorder' for r in plan.recommendations)
    assert any('No safe transfer' in n for n in plan.notes)


def test_quality_block_and_rejection_exclusion(tiny):
    pp=forecast(tiny);pp['A','P']=replace(pp['A','P'],actionable=False)
    plan=generate_plan(tiny,pp,excluded_transfers=[('B','C','P','B-P')])
    assert not [r for r in plan.recommendations if r['kind']=='transfer']
    assert not [r for r in plan.recommendations if r['facility_id']=='A' and r['medicine_id']=='P']


def test_fallback_and_fefo(tiny):
    plan=generate_plan(tiny,forecast(tiny),use_milp=False);assert 'heuristic' in plan.solver
    transfers=[r for r in plan.recommendations if r['kind']=='transfer']
    assert not project_inventory(tiny,forecast(tiny),transfers).expiries.batch_id.str.startswith('TRANSFER-').any()
    b=tiny.tables['batches'].copy();row=b.iloc[0].to_dict();row.update(batch_id='A-EARLY',quantity=5,expiry=str(tiny.as_of+timedelta(days=2)))
    tiny.tables['batches']=pd.concat([b,pd.DataFrame([row])],ignore_index=True)
    rows,expired=project_pair(tiny,('A','P'),[5,5,5]);assert not expired and rows[-1]['unmet']==0


def approve(store,wid):
    ds=store.planning_dataset(wid);plan=generate_plan(ds,forecast(ds));store.save_plan(wid,plan)
    rec=next(r for r in plan.recommendations if r['kind']=='transfer')
    store.decide(wid,plan.plan_id,rec['recommendation_id'],True,'Demo checked');return plan,rec


def test_reserve_dispatch_receive_idempotence(tiny,tmp_path):
    store=Store(tmp_path/'data.db');wid=store.create_workspace(tiny);plan,rec=approve(store,wid)
    aid=rec['recommendation_id'];bid=rec['batch_id']
    def qty(ds):return int(ds.tables['batches'].set_index('batch_id').loc[bid,'quantity'])
    assert qty(store.load(wid))==600 and qty(store.planning_dataset(wid))==600-rec['quantity']
    assert store.decide(wid,plan.plan_id,aid)=='approved'
    store.advance_action(wid,aid);assert qty(store.load(wid))==600-rec['quantity']
    with pytest.raises(ValueError,match='arrival date'):store.advance_action(wid,aid)
    assert qty(store.load(wid))==600-rec['quantity']
    store.advance_clock(wid,rec['travel_days'],forecast(store.planning_dataset(wid)))
    assert store.advance_action(wid,aid)=='received' and store.advance_action(wid,aid)=='received'
    received=store.load(wid).tables['batches'];assert (received.batch_id=='RECEIVED-'+aid).sum()==1
    assert received.loc[received.batch_id.eq('RECEIVED-'+aid),'quantity'].iloc[0]==rec['quantity']


def test_stale_dependencies_restart(tiny,tmp_path):
    path=tmp_path/'data.db';store=Store(path);wid=store.create_workspace(tiny)
    ds=store.planning_dataset(wid);plan=generate_plan(ds,forecast(ds));store.save_plan(wid,plan)
    order=next(r for r in plan.recommendations if r['kind']=='reorder')
    with pytest.raises(ValueError,match='depend'):store.decide(wid,plan.plan_id,order['recommendation_id'])
    rec=next(r for r in plan.recommendations if r['kind']=='transfer');store.decide(wid,plan.plan_id,rec['recommendation_id'])
    other=next(r for r in plan.recommendations if r['recommendation_id']!=rec['recommendation_id'])
    with pytest.raises(ValueError,match='stale'):store.decide(wid,plan.plan_id,other['recommendation_id'])
    restored=Store(path);assert len(restored.action_history(wid))==1
    assert restored.planning_dataset(wid).version==store.planning_dataset(wid).version


def test_workspace_isolation_and_rejection(tiny,tmp_path):
    store=Store(tmp_path/'data.db');a,b=store.create_workspace(tiny),store.create_workspace(tiny)
    approve(store,a);assert not store.action_history(b) and store.planning_dataset(b).version==tiny.version
    plan=generate_plan(tiny,forecast(tiny));store.save_plan(b,plan)
    store.decide(b,plan.plan_id,plan.recommendations[0]['recommendation_id'],False)
    with pytest.raises(ValueError,match='stale'):store.decide(b,plan.plan_id,plan.recommendations[1]['recommendation_id'])


def test_simulation_fairness_reproducibility(tiny):
    a=run_experiment(tiny,seeds=[3],scenarios=['Normal operations'],days=2)
    b=run_experiment(tiny,seeds=[3],scenarios=['Normal operations'],days=2)
    assert a.runs.equals(b.runs) and set(a.runs.policy)==set(POLICIES)
    assert a.daily_results.groupby(['seed','date','facility_id','medicine_id']).demand.nunique().max()==1
    assert (a.runs.procurement_cost<=20000).all() and a.settings['synthetic']
