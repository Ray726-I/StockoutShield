from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import timedelta
import math
import numpy as np
import pandas as pd
from .data import Dataset
from .forecasting import forecast, predict_values, causal_fill
from .planning import generate_plan

SCENARIOS = {
    'Normal operations': 'Regular demand and fixture deliveries.',
    'Local demand surge': 'F1 demand for M01/M02 doubles on days 4–11; policies only learn from their observed dispensing.',
    'Supplier delay': 'All supplier deliveries take five extra days.',
    'Short-dated surplus': 'Surplus batches at F2/F5 expire on day 7.',
    'Route disruption': 'Routes to and from F1 close from day 5.',
    'Little transferable surplus': 'Opening usable stock is reduced to five days of demand at every facility.',
}
POLICIES = ['Fixed reorder threshold', 'Forecast reorder', 'Forecast + redistribution']


@dataclass
class Experiment:
    summary: pd.DataFrame
    runs: pd.DataFrame
    facility_results: pd.DataFrame
    daily_results: pd.DataFrame
    settings: dict


def _histories(ds):
    groups = {}
    for key,g in ds.tables['usage'].groupby(['facility_id','medicine_id']):
        ix=pd.date_range(pd.Timestamp(g.date.min()), pd.Timestamp(ds.as_of)-pd.Timedelta(days=1))
        aligned=g.assign(date=pd.to_datetime(g.date)).set_index('date').reindex(ix)
        reliable=aligned.availability.eq('available')&aligned.dispensed.notna()
        groups[key]=causal_fill(aligned.dispensed.to_numpy(float),reliable.to_numpy()).tolist()
    return groups


def run_experiment(ds, seeds=range(20), scenarios=None, days=30, progress=None):
    scenarios=list(scenarios or SCENARIOS)
    if not set(scenarios)<=set(SCENARIOS): raise ValueError('Unknown predefined scenario.')
    seeds=list(seeds)
    initial=forecast(ds)
    histories=_histories(ds)
    keys=list(initial)
    meds=ds.tables['medicines'].set_index('medicine_id')
    fac=ds.tables['facilities'].set_index('facility_id')
    run_rows, facility_rows, daily_rows=[], [], []
    completed=0
    for scenario in scenarios:
        for seed in seeds:
            rng=np.random.default_rng(seed)
            # Generate once; the exact same latent demand is reused by all policies.
            latent={}
            for key in keys:
                base=np.mean(histories.get(key,[0])[-28:])
                lam=np.array([base*(1+.1*np.sin(2*np.pi*i/7)) for i in range(days)])
                if scenario=='Local demand surge' and key[0]=='F1' and key[1] in ['M01','M02']:
                    lam[3:11]*=2
                latent[key]=rng.poisson(np.maximum(0,lam))
            for policy in POLICIES:
                # No future demand or other policy state is passed to forecasting/planning.
                sim=ds.copy()
                sim.tables['usage']=sim.tables['usage'].iloc[:0].copy()
                hist={k:list(v) for k,v in histories.items()}
                if scenario=='Short-dated surplus':
                    mask=sim.tables['batches'].facility_id.isin(['F2','F5'])&sim.tables['batches'].batch_id.str.endswith('-A')
                    sim.tables['batches'].loc[mask,'expiry']=str(ds.as_of+timedelta(days=7))
                if scenario=='Little transferable surplus':
                    for idx,b in sim.tables['batches'].iterrows():
                        if b['status']=='available':
                            mean=np.mean(hist[b['facility_id'],b['medicine_id']][-28:])
                            sim.tables['batches'].loc[idx,'quantity']=int(mean*2.5)
                delay=5 if scenario=='Supplier delay' else 0
                if delay:
                    sim.tables['orders']['arrival']=[str(pd.Timestamp(x).date()+timedelta(days=delay))
                                                     for x in sim.tables['orders'].arrival]
                metrics={k:dict(unmet_units=0,stockout_days=0,expired_units=0,expiry_value=0.,
                                procurement_cost=0.,transfer_cost=0.) for k in keys}
                budget=20000.0
                transit=[]
                for day in range(days):
                    today=str(sim.as_of)
                    # Transport arrivals enter the same batch ledger as other supplies.
                    arriving=sim.tables['orders'][sim.tables['orders'].arrival<=today]
                    for o in arriving.to_dict('records'):
                        b=dict(batch_id='RCV-'+o['order_id'],facility_id=o['facility_id'],medicine_id=o['medicine_id'],
                            quantity=int(o['quantity']),expiry=o['expiry'],status='available')
                        sim.tables['batches']=pd.concat([sim.tables['batches'],pd.DataFrame([b])],ignore_index=True)
                    sim.tables['orders']=sim.tables['orders'][sim.tables['orders'].arrival>today].copy()
                    if scenario=='Route disruption' and day>=4:
                        sim.tables['routes']=sim.tables['routes'][~(sim.tables['routes'].from_facility.eq('F1')|
                                                                 sim.tables['routes'].to_facility.eq('F1'))].copy()
                    preds={}
                    for key in keys:
                        p=initial[key]
                        values=predict_values(hist[key],p.method,30)
                        # Preserve an explicit uncertainty/stress margin; it uses no latent demand.
                        margin=max(0, np.mean(p.upper)-np.mean(p.mean))
                        preds[key]=replace(p,mean=values.tolist(),upper=(values+margin).tolist())
                    if policy=='Forecast + redistribution':
                        proposal=generate_plan(sim,preds,use_milp=False)
                        transfers=[r for r in proposal.recommendations if r['kind']=='transfer']
                        reorders=[r for r in proposal.recommendations if r['kind']=='reorder']
                        for r in transfers:
                            mask=sim.tables['batches'].batch_id.eq(r['batch_id'])
                            sim.tables['batches'].loc[mask,'quantity']-=r['quantity']
                            arrival=dict(order_id=f'T-{seed}-{day}-{r["recommendation_id"]}',facility_id=r['to_facility'],
                                medicine_id=r['medicine_id'],quantity=r['quantity'],arrival=r['arrival'],expiry=r['expiry'])
                            sim.tables['orders']=pd.concat([sim.tables['orders'],pd.DataFrame([arrival])],ignore_index=True)
                            metrics[r['to_facility'],r['medicine_id']]['transfer_cost']+=r['transfer_cost']
                    else:
                        reorders=[]
                        stock_by={k:0 for k in keys};pending_by={k:0 for k in keys}
                        for item in sim.tables['batches'].to_dict('records'):
                            if item['status']=='available' and item['expiry']>today: stock_by[item['facility_id'],item['medicine_id']]+=item['quantity']
                        for item in sim.tables['orders'].to_dict('records'): pending_by[item['facility_id'],item['medicine_id']]+=item['quantity']
                        if policy == 'Forecast reorder':
                            reorders = [r for r in generate_plan(sim, preds, use_milp=False, allow_transfers=False).recommendations if r['kind']=='reorder']
                        for key in (keys if policy == 'Fixed reorder threshold' else []):
                            f,m=key
                            stock=stock_by[key]
                            pending=pending_by[key]
                            lead=int(fac.loc[f,'lead_days'])
                            if policy=='Fixed reorder threshold':
                                rate=np.mean(histories[key][-28:])
                                target=rate*(lead+7)
                                deficit=target-stock-pending if stock+pending<=rate*lead else 0
                            else:
                                # Forecast reorder uses precisely the same reorder calculation without transfers.
                                deficit=max(0,sum(preds[key].upper[:lead+7])-stock-pending)
                            pack=int(meds.loc[m,'pack_size'])
                            qty=int(math.ceil(max(0,deficit)/pack)*pack)
                            if qty: reorders.append(dict(facility_id=f,medicine_id=m,quantity=qty,travel_days=lead,
                                procurement_cost=qty*float(meds.loc[m,'unit_cost'])))
                    for r in sorted(reorders,key=lambda r:(r['facility_id'],r['medicine_id'])):
                        cost=float(r['procurement_cost'])
                        if cost>budget: continue
                        budget-=cost
                        key=(r['facility_id'],r['medicine_id'])
                        metrics[key]['procurement_cost']+=cost
                        a=dict(order_id=f'BUY-{day}-{key[0]}-{key[1]}',facility_id=key[0],medicine_id=key[1],
                            quantity=r['quantity'],arrival=str(sim.as_of+timedelta(days=r['travel_days']+delay)),
                            expiry=str(sim.as_of+timedelta(days=180)))
                        sim.tables['orders']=pd.concat([sim.tables['orders'],pd.DataFrame([a])],ignore_index=True)
                    # Only now is today's hidden demand revealed to dispensing, never to planning.
                    lots=sim.tables['batches'].to_dict('records')
                    lots_by={k:[] for k in keys}
                    expired_today={k:0 for k in keys}
                    for b in lots:
                        if b['status']=='available' and b['expiry']<=today and b['quantity']:
                            k=(b['facility_id'],b['medicine_id']); qty=int(b['quantity'])
                            metrics[k]['expired_units']+=qty
                            metrics[k]['expiry_value']+=qty*float(meds.loc[k[1],'unit_cost'])
                            expired_today[k]+=qty
                            b['quantity']=0
                        if b['status']=='available' and b['expiry']>today:
                            lots_by[b['facility_id'],b['medicine_id']].append(b)
                    for key in keys:
                        f,m=key; need=int(latent[key][day]); remaining=need
                        eligible=sorted(lots_by[key],key=lambda b:(b['expiry'],b['batch_id']))
                        for b in eligible:
                            take=min(int(b['quantity']),remaining)
                            b['quantity']-=take
                            remaining-=take
                        served=need-remaining
                        metrics[key]['unmet_units']+=remaining
                        metrics[key]['stockout_days']+=int(remaining>0)
                        # Censored dispensing is replaced with an earlier-history estimate for modelling only.
                        observed=max(served,float(np.mean(hist[key][-28:]))) if remaining else served
                        hist[key].append(observed)
                        daily_rows.append(dict(scenario=scenario,seed=seed,policy=policy,date=today,
                            facility_id=f,medicine_id=m,demand=need,dispensed=served,unmet=remaining,expired=expired_today[key]))
                    sim.tables['batches']=pd.DataFrame(lots,columns=sim.tables['batches'].columns)
                    sim.as_of+=timedelta(days=1)
                total={metric:sum(v[metric] for v in metrics.values()) for metric in next(iter(metrics.values()))}
                run_rows.append(dict(scenario=scenario,seed=seed,policy=policy,**total))
                for key,v in metrics.items():
                    facility_rows.append(dict(scenario=scenario,seed=seed,policy=policy,facility_id=key[0],medicine_id=key[1],**v))
            completed+=1
            if progress: progress(completed,len(seeds)*len(scenarios),scenario,seed)
    runs=pd.DataFrame(run_rows)
    numeric=['unmet_units','stockout_days','expired_units','expiry_value','procurement_cost','transfer_cost']
    summary=runs.groupby(['scenario','policy'],sort=False)[numeric].mean().reset_index()
    return Experiment(summary,runs,pd.DataFrame(facility_rows),pd.DataFrame(daily_rows),
        dict(seeds=seeds,scenarios=scenarios,days=days,procurement_budget_per_policy=20000,
             synthetic=True,allocator='Deterministic FEFO-verified fallback used in repeated experiments.',
             note='Demand is generated once per seed/scenario. Policies see past dispensing only; results are simulated.'))
