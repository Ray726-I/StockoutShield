from pathlib import Path
import streamlit as st
from streamlit.testing.v1 import AppTest
from stockout.storage import Store
from stockout.forecasting import forecast

APP=Path(__file__).resolve().parents[1]/'app.py'


def start(tiny,tmp_path,monkeypatch):
    st.cache_resource.clear()
    path=tmp_path/'ui.db'
    monkeypatch.setenv('STOCKOUT_DB',str(path))
    store=Store(path);wid=store.create_workspace(tiny)
    at=AppTest.from_file(str(APP),default_timeout=30)
    at.query_params['workspace']=wid
    at.run()
    assert not at.exception
    return at,store,wid


def test_context_navigation_and_empty_stock(tiny,tmp_path,monkeypatch):
    at,store,wid=start(tiny,tmp_path,monkeypatch)
    at.selectbox(key='selected_facility').select('B').run()
    at.selectbox(key='selected_medicine').select('Q').run()
    at.button(key='nav_Inventory & Data').click().run()
    at.text_input(key='stock_search').input('No such batch').run()
    assert not at.exception and len(at.info)>0
    for page in ['Forecasts','Action Plan','Inventory & Data']:
        at.button(key='nav_'+page).click().run()
        assert not at.exception
        assert (at.session_state['selected_facility'],at.session_state['selected_medicine'])==('B','Q')
    next(b for b in at.button if b.label=='Show all').click().run()
    assert at.session_state['selected_facility']=='All' and at.session_state['selected_medicine']=='All'
    assert at.text_input(key='stock_search').value==''
    assert not store.action_history(wid)


def test_shortage_to_approval_dispatch_and_receipt_gate(tiny,tmp_path,monkeypatch):
    at,store,wid=start(tiny,tmp_path,monkeypatch)
    at.button(key='review-risk-AP').click().run()
    assert at.session_state['page']=='Action Plan' and not at.exception
    next(b for b in at.button if b.label=='Approve transfer').click().run()
    assert not at.exception
    assert len(store.action_history(wid))==1
    at.session_state['action_view']='Tracking';at.run()
    next(b for b in at.button if b.label=='Record dispatch').click().run()
    assert not at.exception
    waiting=next(b for b in at.button if b.label=='Receipt not due yet')
    assert waiting.disabled
    assert store.action_history(wid)[0]['status']=='dispatched'
    # A receipt cannot be recorded before the arrival date, even on another rerun.
    at.run()
    assert not at.exception and store.action_history(wid)[0]['status']=='dispatched'
    store.advance_clock(wid,1,forecast(store.planning_dataset(wid)))
    at.run()
    next(b for b in at.button if b.label=='Record receipt').click().run()
    assert not at.exception
    assert at.session_state['tracking_status']=='Completed'
    assert store.action_history(wid)[0]['status']=='received'


def test_dispensing_form_uses_selected_medicine_and_facility(tiny,tmp_path,monkeypatch):
    at,store,wid=start(tiny,tmp_path,monkeypatch)
    at.selectbox(key='selected_facility').select('B').run()
    at.selectbox(key='selected_medicine').select('P').run()
    at.button(key='nav_Inventory & Data').click().run()
    at.selectbox(key='inventory_operation').select('Record dispensing').run()
    next(n for n in at.number_input if n.label=='Quantity dispensed (tablet)').set_value(5)
    next(t for t in at.text_input if t.label=='Reason or delivery reference').input('Daily dispensing register')
    next(c for c in at.checkbox if c.label=='I have checked this update').check()
    next(b for b in at.button if b.label=='Save inventory update').click().run()
    assert not at.exception
    stock=store.load(wid).tables['batches'].set_index('batch_id')
    assert stock.loc['B-P','quantity']==595 and stock.loc['A-P','quantity']==10
    assert store.events(wid)[-1]['event']=='inventory_dispense'
