import pandas as pd
from stockout.sample import make_sample
from stockout.forecasting import forecast
from stockout.planning import generate_plan
from stockout.inventory import project_pair
from stockout.walkthrough import choose_case


def test_expanded_catalogue_and_repeatability():
    ds=make_sample(profile='expanded',days=45)
    other=make_sample(profile='expanded',days=45)
    assert ds.synthetic and ds.version==other.version
    assert len(ds.tables['facilities'])==12 and len(ds.tables['medicines'])==20
    assert len(ds.tables['batches'])==481 and len(ds.tables['routes'])==132
    assert 10000<len(ds.tables['usage'])<12*20*45
    assert set(ds.tables['usage'].availability)=={'available','stockout'}
    assert (ds.tables['usage'].dispensed==0).any()
    assert ds.tables['usage'].date.max()<str(ds.as_of)
    paracetamol=ds.tables['medicines'].query('ingredient == "Paracetamol"')
    assert len(paracetamol)==2 and paracetamol.formulation.nunique()==2
    assert ds.tables['batches'].batch_id.is_unique
    assert not ds.tables['usage'].duplicated(['date','facility_id','medicine_id']).any()
    assert len(make_sample(days=45).tables['facilities'])==5


def test_medium_walkthrough_size_and_repeatability():
    ds=make_sample(profile='medium')
    other=make_sample(profile='medium')
    assert ds.version==other.version
    assert len(ds.tables['facilities'])==8
    assert len(ds.tables['medicines'])==14
    assert 50000<len(ds.tables['usage'])<65000
    assert len(ds.tables['routes'])==56
    assert len(ds.tables['batches'])==225
    # Keep both paracetamol formulations distinct in the medium demo.
    paracetamol=ds.tables['medicines'].query('ingredient == "Paracetamol"')
    assert len(paracetamol)==2 and paracetamol.formulation.nunique()==2


def test_walkthrough_isolates_one_transfer_without_mutating_plan(tiny):
    pp=forecast(tiny);plan=generate_plan(tiny,pp)
    original=[dict(r) for r in plan.recommendations]
    case=choose_case(tiny,pp,plan)
    assert case is not None
    rec=case['rec'];key=(rec['to_facility'],rec['medicine_id'])
    expected,_=project_pair(tiny,key,pp[key].mean,[rec])
    assert rec['recipient_after_unmet7']==sum(r['unmet'] for r in expected[:7])
    assert rec['recipient_after_unmet7']<rec['recipient_before_unmet7']
    assert plan.recommendations==original
    case['dataset'].tables['batches'].loc[0,'quantity']=0
    assert tiny.tables['batches'].iloc[0].quantity!=0
    plan.recommendations=[r for r in plan.recommendations if r['kind']=='reorder']
    assert choose_case(tiny,pp,plan) is None
