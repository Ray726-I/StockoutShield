from datetime import date,timedelta
import pandas as pd
import pytest
from stockout.data import validate_import
@pytest.fixture
def tiny():
    today=date(2026,10,18)
    fs=pd.DataFrame([dict(facility_id=f,name=f,lead_days=5,reserve_days=7) for f in ['A','B','C']])
    ms=pd.DataFrame([dict(medicine_id=m,ingredient='Paracetamol',strength=str(s)+' mg',formulation='tablet',unit='tablet',pack_size=10,unit_cost=1) for m,s in [('P',500),('Q',250)]])
    bs=pd.DataFrame([dict(batch_id=f+'-'+m,facility_id=f,medicine_id=m,quantity=q,expiry=str(today+timedelta(days=90)),status=status) for f,m,q,status in [('A','P',10,'available'),('B','P',600,'available'),('C','P',10,'available'),('B','Q',600,'quarantined')]])
    us=pd.DataFrame([dict(date=str(today-timedelta(days=d)),facility_id=f,medicine_id=m,dispensed=10,availability='available') for d in range(1,121) for f in ['A','B','C'] for m in ['P','Q']])
    orders=pd.DataFrame(columns=['order_id','facility_id','medicine_id','quantity','arrival','expiry'])
    routes=pd.DataFrame([dict(from_facility='B',to_facility=f,travel_days=1,cost_per_pack=2) for f in ['A','C']])
    ds,issues=validate_import(dict(facilities=fs,medicines=ms,batches=bs,usage=us,orders=orders,routes=routes),today,synthetic=True)
    assert ds is not None,issues
    return ds
