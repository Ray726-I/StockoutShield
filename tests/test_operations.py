from datetime import timedelta
import pandas as pd
import pytest
from stockout.storage import Store
from stockout.forecasting import forecast
from stockout.planning import generate_plan
from stockout.workflow import action_state


def approve_transfer(store, wid):
    ds=store.planning_dataset(wid)
    plan=generate_plan(ds,forecast(ds))
    store.save_plan(wid,plan)
    rec=next(r for r in plan.recommendations if r['kind']=='transfer')
    store.decide(wid,plan.plan_id,rec['recommendation_id'])
    return plan,rec


def test_receipt_idempotence_validation_and_stale_edits(tiny,tmp_path):
    store=Store(tmp_path/'stock.db'); wid=store.create_workspace(tiny)
    payload=dict(batch_id='NEW-STOCK',facility_id='A',medicine_id='P',quantity=20,expiry=str(tiny.as_of+timedelta(days=90)),reason='Delivery note 123')
    original=tiny.version
    assert store.record_inventory(wid,'receive',payload,expected_version=original,request_id='one')=='recorded'
    assert store.record_inventory(wid,'receive',payload,expected_version=original,request_id='one')=='already_recorded'
    assert len(store.events(wid))==1
    ds=store.load(wid)
    assert ds.tables['batches'].loc[ds.tables['batches'].batch_id.eq('NEW-STOCK'),'quantity'].item()==20
    with pytest.raises(ValueError,match='another session'):
        store.record_inventory(wid,'receive',dict(payload,batch_id='SECOND'),expected_version=original,request_id='two')
    for edit in [dict(payload,batch_id='BAD',quantity=-1),dict(payload,batch_id='BAD',expiry=str(tiny.as_of)),dict(payload,batch_id='BAD',medicine_id='UNKNOWN'),payload]:
        with pytest.raises(ValueError):store.record_inventory(wid,'receive',edit,expected_version=ds.version,request_id='bad')
    assert store.load(wid).version==ds.version


def test_reserved_stock_guard_cancellation_and_quarantine(tiny,tmp_path):
    store=Store(tmp_path/'stock.db');wid=store.create_workspace(tiny)
    _,rec=approve_transfer(store,wid)
    for operation,payload in [('adjust',dict(batch_id=rec['batch_id'],quantity=0)),('quarantine',dict(batch_id=rec['batch_id']))]:
        with pytest.raises(ValueError,match='reserved'):
            store.record_inventory(wid,operation,dict(payload,reason='Count correction'),expected_version=tiny.version,request_id=operation)
    store.cancel_action(wid,rec['recommendation_id'],'Reservation no longer required')
    store.cancel_action(wid,rec['recommendation_id'],'Repeat request')
    assert store.planning_dataset(wid).version==tiny.version
    store.record_inventory(wid,'quarantine',dict(batch_id=rec['batch_id'],reason='Damaged packaging'),expected_version=tiny.version,request_id='q')
    quarantined=store.load(wid)
    assert quarantined.tables['batches'].set_index('batch_id').loc[rec['batch_id'],'status']=='quarantined'
    assert not [r for r in generate_plan(store.planning_dataset(wid),forecast(quarantined)).recommendations if r['kind']=='transfer']
    with pytest.raises(ValueError,match='Invalid action transition'):
        store.advance_action(wid,rec['recommendation_id'])


def test_usage_correction_preserves_stock_and_availability(tiny,tmp_path):
    store=Store(tmp_path/'stock.db');wid=store.create_workspace(tiny)
    payload=dict(facility_id='A',medicine_id='P',date=str(tiny.as_of-timedelta(days=1)),dispensed=0,availability='stockout',reason='Corrected daily register')
    store.record_inventory(wid,'usage',payload,expected_version=tiny.version,request_id='usage')
    ds=store.load(wid)
    assert ds.tables['batches'].equals(tiny.tables['batches'])
    row=ds.tables['usage'].query('facility_id=="A" and medicine_id=="P"').sort_values('date').iloc[-1]
    assert row.dispensed==0 and row.availability=='stockout'
    assert not ds.tables['usage'].duplicated(['date','facility_id','medicine_id']).any()
    event=store.events(wid)[0]
    assert event['payload']['stock_change']==0 and event['payload']['previous_record'][0]['dispensed']==10
    with pytest.raises(ValueError,match='precede'):
        store.record_inventory(wid,'usage',dict(payload,date=str(ds.as_of)),expected_version=ds.version,request_id='future')


def test_dispensing_uses_fefo_and_protects_reservations(tiny,tmp_path):
    store=Store(tmp_path/'stock.db');wid=store.create_workspace(tiny)
    _,rec=approve_transfer(store,wid)
    payload=dict(facility_id='B',medicine_id='P',dispensed=600,reason='Dispensing register')
    with pytest.raises(ValueError,match='unreserved'):
        store.record_inventory(wid,'dispense',payload,expected_version=tiny.version,request_id='too-many')
    payload['dispensed']=10
    store.record_inventory(wid,'dispense',payload,expected_version=tiny.version,request_id='dispense')
    store.record_inventory(wid,'dispense',payload,expected_version=tiny.version,request_id='dispense')
    ds=store.load(wid)
    assert ds.tables['batches'].set_index('batch_id').loc['B-P','quantity']==590
    assert ds.tables['usage'].equals(tiny.tables['usage'])
    assert store.events(wid)[-1]['payload']['movements']==[dict(batch_id='B-P',quantity=10)]
    early=dict(batch_id='EARLY',facility_id='B',medicine_id='P',quantity=5,expiry=str(ds.as_of+timedelta(days=2)),reason='Delivery register')
    store.record_inventory(wid,'receive',early,expected_version=ds.version,request_id='early')
    ds=store.load(wid)
    store.record_inventory(wid,'dispense',dict(payload,dispensed=7),expected_version=ds.version,request_id='fefo')
    batch=store.load(wid).tables['batches'].set_index('batch_id')
    assert batch.loc['EARLY','quantity']==0 and batch.loc['B-P','quantity']==588


def test_supplier_receipt_delay_and_workspace_isolation(tiny,tmp_path):
    tiny.tables['orders']=pd.DataFrame([dict(order_id='S1',facility_id='A',medicine_id='P',quantity=30,arrival=str(tiny.as_of+timedelta(days=1)),expiry=str(tiny.as_of+timedelta(days=60)))])
    store=Store(tmp_path/'stock.db');a,b=store.create_workspace(tiny),store.create_workspace(tiny)
    with pytest.raises(ValueError,match='not due'):
        store.receive_supply(a,'S1',expected_version=tiny.version)
    store.update_arrival(a,'S1',tiny.as_of,'Delivery arrived early',expected_version=tiny.version,request_id='arrival')
    updated=store.load(a)
    assert store.load(b).version==tiny.version
    store.receive_supply(a,'S1',expected_version=updated.version)
    store.receive_supply(a,'S1',expected_version=updated.version)
    ds=store.load(a)
    assert ds.tables['orders'].empty and len(ds.tables['batches'])==len(tiny.tables['batches'])+1
    assert ds.tables['batches'].set_index('batch_id').loc['SUPPLY-S1','quantity']==30


def test_transfer_delay_invalidates_plan_and_uses_arrival_gate(tiny,tmp_path):
    store=Store(tmp_path/'stock.db');wid=store.create_workspace(tiny)
    _,rec=approve_transfer(store,wid); aid=rec['recommendation_id']
    store.advance_action(wid,aid)
    ds=store.planning_dataset(wid);plan=generate_plan(ds,forecast(ds));store.save_plan(wid,plan)
    store.update_arrival(wid,aid,tiny.as_of+timedelta(days=3),'Road closure',expected_version=ds.version,request_id='delay')
    assert store.planning_dataset(wid).version!=ds.version
    assert action_state(store.action_history(wid)[0],tiny.as_of)[1] is None
    with pytest.raises(ValueError,match='stale'):
        store.decide(wid,plan.plan_id,plan.recommendations[0]['recommendation_id'])
    with pytest.raises(ValueError,match='arrival date'):store.advance_action(wid,aid)
    with pytest.raises(ValueError,match='Only an action awaiting'):
        store.cancel_action(wid,aid,'Cannot cancel stock in transit')
    store.advance_clock(wid,3,forecast(store.planning_dataset(wid)))
    assert action_state(store.action_history(wid)[0],store.load(wid).as_of)[1]=='Record receipt'
    assert store.advance_action(wid,aid)=='received'
    assert store.advance_action(wid,aid)=='received'
