from __future__ import annotations
from dataclasses import dataclass, field
from datetime import timedelta
import math
import time
import uuid
import numpy as np
import pandas as pd
from scipy.optimize import milp, Bounds, LinearConstraint
from .data import Dataset
from .inventory import project_inventory, project_pair


@dataclass
class Plan:
    dataset_version: str
    recommendations: list[dict]
    solver: str
    notes: list[str]
    assumptions: dict = field(default_factory=dict)
    plan_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def payload(self):
        from dataclasses import asdict
        return asdict(self)


def _pair_arrays(ds, key, p, transfers, mode, opening, incoming):
    rows, _ = project_pair(ds, key, getattr(p, mode), transfers, opening=opening, incoming=incoming)
    return np.array([[r['stock'], r['unmet'], r['expired']] for r in rows])


def _safe_add(ds, predictions, base, accepted, candidate, opening, incoming):
    """Mandatory FEFO verification against BOTH central and upper demand trajectories."""
    donor = (candidate['from_facility'], candidate['medicine_id'])
    recipient = (candidate['to_facility'], candidate['medicine_id'])
    for mode in ['mean', 'upper']:
        for key in [donor, recipient]:
            try:
                arr = _pair_arrays(ds, key, predictions[key], accepted+[candidate], mode, opening, incoming)
            except ValueError:
                return False
            baseline = base[(key, mode)]
            if np.any(arr[:, 1] > baseline[:, 1] + 1e-6):
                return False
            if key == donor:
                reserve_days = base['reserves'][key[0]]
                demand = np.array(getattr(predictions[key], mode))
                extended = np.pad(demand, (0,reserve_days), constant_values=float(np.mean(demand[-7:])))
                reserve = np.array([sum(extended[i+1:i+1+reserve_days]) for i in range(len(demand))])
                if np.any(arr[:, 0] + 1e-6 < np.minimum(baseline[:, 0], reserve)):
                    return False
            else:
                if arr[:, 2].sum() > baseline[:, 2].sum() + 1e-6:
                    return False
                # Any received transfer that reaches expiry must be completely consumed.
                rows, expired = project_pair(ds, key, getattr(predictions[key], mode), accepted+[candidate],
                                             opening=opening, incoming=incoming)
                if any(str(x['batch_id']).startswith('TRANSFER-') and x['quantity'] > 1e-6 for x in expired):
                    return False
    return True


def generate_plan(ds: Dataset, predictions, use_milp=True, time_limit=10, allow_transfers=True, excluded_transfers=()):
    opening, incoming = ds.tables['batches'].to_dict('records'), ds.tables['orders'].to_dict('records')
    meds = ds.tables['medicines'].set_index('medicine_id')
    facilities = ds.tables['facilities'].set_index('facility_id')
    central = project_inventory(ds, predictions)
    base = {'reserves':dict(zip(ds.tables['facilities'].facility_id,ds.tables['facilities'].reserve_days))}
    grouped_curves = {key:g for key,g in central.curves.groupby(['facility_id','medicine_id'],sort=False)}
    for key,p in predictions.items():
        base[(key,'mean')] = grouped_curves[key][['stock','unmet','expired']].to_numpy()
        if allow_transfers: base[(key,'upper')] = _pair_arrays(ds,key,p,[],'upper',opening,incoming)
    risk = central.risks.set_index(['facility_id', 'medicine_id'])
    candidates = []
    for b in (opening if allow_transfers else []):
        f, m = b['facility_id'], b['medicine_id']
        if b['status'] != 'available' or str(b['expiry']) <= str(ds.as_of) or b['quantity'] <= 0:
            continue
        if not predictions[(f,m)].actionable:
            continue
        pack = int(meds.loc[m, 'pack_size'])
        expiry_day = (pd.Timestamp(b['expiry']).date()-ds.as_of).days
        donor_expiring = float(central.expiries.loc[central.expiries.batch_id.eq(b['batch_id']), 'quantity'].sum())
        # To avoid claiming safe use beyond the planning horizon, require recipient demand within it.
        for route in ds.tables['routes'].to_dict('records'):
            if route['from_facility'] != f:
                continue
            to = route['to_facility']
            if (f,to,m,b['batch_id']) in excluded_transfers: continue
            if not predictions[(to,m)].actionable:
                continue
            travel = int(route['travel_days'])
            if expiry_day - travel < 2:
                continue
            stop = min(30, expiry_day)
            recipient_base = base[((to,m), 'mean')]
            need = float(recipient_base[travel:stop, 1].sum())
            if need < pack:
                continue
            quantity = min(int(b['quantity']//pack), int(need//pack))*pack
            c = dict(kind='transfer', recommendation_id=uuid.uuid4().hex[:12],
                from_facility=f, to_facility=to, facility_id=to, medicine_id=m, batch_id=b['batch_id'],
                expiry=str(b['expiry']), quantity=quantity, pack_size=pack,
                travel_days=travel, arrival=str(ds.as_of+timedelta(days=travel)),
                cost_per_pack=float(route['cost_per_pack']), donor_expiring=donor_expiring,
                shortage_day=int(risk.loc[(to,m), 'shortage_day']))
            # Find the largest individually verified pack quantity.
            lo, hi = 0, quantity//pack
            while lo < hi:
                mid = (lo+hi+1)//2
                c['quantity'] = mid*pack
                if _safe_add(ds, predictions, base, [], c, opening, incoming): lo = mid
                else: hi = mid-1
            if lo:
                c['quantity'] = lo*pack
                candidates.append(c.copy())
    notes, accepted = [], []
    solver_label = 'Deterministic heuristic · FEFO verified'
    quantities = None
    if use_milp and candidates:
        started = time.monotonic()
        n = len(candidates)
        constraints = []
        bounds = []
        # Batch sharing and recipient demand caps. FEFO validation below checks timing and reserves.
        for bid in sorted({c['batch_id'] for c in candidates}):
            row = [c['pack_size'] if c['batch_id'] == bid else 0 for c in candidates]
            constraints.append(row)
            bounds.append(next(b['quantity'] for b in opening if b['batch_id'] == bid))
        for recipient in sorted({(c['to_facility'], c['medicine_id']) for c in candidates}):
            constraints.append([c['pack_size'] if (c['to_facility'],c['medicine_id']) == recipient else 0 for c in candidates])
            bounds.append(float(base[(recipient, 'mean')][:, 1].sum()))
        A = np.array(constraints, float)
        upper_bounds = np.array([c['quantity']//c['pack_size'] for c in candidates], float)
        benefit = np.array([c['pack_size'] for c in candidates], float)
        expiry_benefit = np.array([c['pack_size'] if c['donor_expiring'] > 0 else 0 for c in candidates], float)
        cost = np.array([c['cost_per_pack'] for c in candidates], float)
        cons = [LinearConstraint(A, np.zeros(len(bounds)), np.array(bounds))]
        incumbent = None
        try:
            # Sequential priorities; total solver allowance is ten seconds, not ten per pass.
            for objective in [-benefit, -expiry_benefit, cost]:
                remaining = time_limit-(time.monotonic()-started)
                if remaining <= .05: break
                sol = milp(objective, integrality=np.ones(n), bounds=Bounds(np.zeros(n), upper_bounds),
                           constraints=cons, options={'time_limit': max(.01, remaining/3)})
                if sol.x is None: break
                incumbent = np.rint(sol.x).astype(int)
                optimum = float(objective@incumbent)
                cons.append(LinearConstraint(objective, optimum-1e-6, optimum+1e-6))
            if incumbent is not None:
                quantities = incumbent
                solver_label = 'MILP allocation · FEFO verified'
            else:
                notes.append('Solver found no feasible incumbent; deterministic fallback used.')
        except Exception as exc:
            notes.append(f'Solver unavailable ({type(exc).__name__}); deterministic fallback used.')
    ranked = sorted(enumerate(candidates), key=lambda item: (
        item[1]['shortage_day'], item[1]['expiry'], item[1]['cost_per_pack'],
        item[1]['from_facility'], item[1]['to_facility']))
    trimmed = False
    for idx, c in ranked:
        wanted = c['quantity'] if quantities is None else int(quantities[idx])*c['pack_size']
        if not wanted: continue
        lo, hi = 0, wanted//c['pack_size']
        while lo < hi:
            mid = (lo+hi+1)//2
            c['quantity'] = mid*c['pack_size']
            if _safe_add(ds, predictions, base, accepted, c, opening, incoming): lo=mid
            else: hi=mid-1
        if not lo:
            trimmed = True
            continue
        if lo*c['pack_size'] < wanted: trimmed=True
        c = dict(c, quantity=lo*c['pack_size'])
        accepted.append(c)
    if trimmed and quantities is not None:
        solver_label = 'MILP proposal + safety trimming · FEFO verified'
        notes.append('Timing/reserve verification trimmed the allocation; global optimality is not claimed.')
    after = project_inventory(ds, predictions, accepted) if accepted else central
    after_groups = {key:g for key,g in after.curves.groupby(['facility_id','medicine_id'],sort=False)}
    after_risk = after.risks.set_index(['facility_id', 'medicine_id'])
    for c in accepted:
        donor, recipient = (c['from_facility'],c['medicine_id']), (c['to_facility'],c['medicine_id'])
        c['transfer_cost'] = c['quantity']/c['pack_size']*c['cost_per_pack']
        c['arrives_too_late'] = c['travel_days'] > c['shortage_day']
        c['donor_before'] = [round(x,2) for x in base[(donor, 'mean')][:,0]]
        c['donor_after'] = [round(r['stock'],2) for r in project_pair(ds, donor, predictions[donor].mean, accepted,
                                         opening=opening,incoming=incoming)[0]]
        c['recipient_before_unmet7'] = round(float(risk.loc[recipient,'unmet7']),2)
        c['recipient_after_unmet7'] = round(float(after_risk.loc[recipient,'unmet7']),2)
        c['explanation'] = (f"Move {c['quantity']} {meds.loc[c['medicine_id'],'unit']}(s) of "
            f"{meds.loc[c['medicine_id'],'ingredient']} {meds.loc[c['medicine_id'],'strength']} "
            f"from {facilities.loc[c['from_facility'],'name']} to {facilities.loc[c['to_facility'],'name']}. "
            f"Batch {c['batch_id']} expires {c['expiry']}; arrival {c['arrival']}. "
            "The combined plan preserves the donor reserve relative to its baseline and does not add donor shortages "
            "under central or upper-demand assumptions. Recipient expiry is checked using FEFO. "
            + ("Arrival is after the first predicted shortage: earlier demand remains uncovered. " if c['arrives_too_late'] else "")
            + f"Estimated transfer cost: INR {c['transfer_cost']:.2f}. Forecast estimates are not guarantees.")
    reorders=[]
    for key, p in predictions.items():
        f,m=key
        if not p.actionable: continue
        lead=int(facilities.loc[f,'lead_days'])
        target=lead+7
        # Target stock includes robust demand and existing deliveries/transfers by the review horizon.
        valid_stock=sum(float(b['quantity']) for b in opening if b['facility_id']==f and b['medicine_id']==m
                        and b['status']=='available' and str(b['expiry'])>str(ds.as_of+timedelta(days=target)))
        arrivals=sum(float(o['quantity']) for o in incoming if o['facility_id']==f and o['medicine_id']==m
                     and str(o['arrival'])<=str(ds.as_of+timedelta(days=target)) and str(o['expiry'])>str(ds.as_of))
        net=sum(c['quantity']*(1 if c['to_facility']==f else -1 if c['from_facility']==f else 0)
                for c in accepted if c['medicine_id']==m)
        # Actual projection credits usable short-dated stock consumed before expiry, unlike a naive stock sum.
        g=after_groups[key]
        usable_open=float(risk.loc[key,'stock_now'])
        lost=float(g[g.day<target].expired.sum())
        target_need=sum(p.upper[:target])
        deficit=max(0, target_need - usable_open - arrivals - net + lost)
        pack=int(meds.loc[m,'pack_size'])
        quantity=int(math.ceil(deficit/pack)*pack)
        if quantity:
            first=int(after_risk.loc[key,'shortage_day'])
            reorders.append(dict(kind='reorder', recommendation_id=uuid.uuid4().hex[:12],facility_id=f,
                medicine_id=m,quantity=quantity,pack_size=pack,travel_days=lead,
                arrival=str(ds.as_of+timedelta(days=lead)),expiry=str(ds.as_of+timedelta(days=lead+180)),
                procurement_cost=round(quantity*float(meds.loc[m,'unit_cost']),2),
                arrives_too_late=first<lead,
                explanation=f"Order {quantity} {meds.loc[m,'unit']}(s) for {facilities.loc[f,'name']}. "
                  f"Covers the remaining upper-demand gap over {lead} lead-time days plus 7 review days, "
                  "after existing supplies and the proposed transfers. "
                  +(f"Routine delivery in {lead} days cannot cover the first shortage; urgent sourcing requires review."
                    if first<lead else f"Expected arrival: {ds.as_of+timedelta(days=lead)}.")))
    blocked=sum(not p.actionable for p in predictions.values())
    if blocked: notes.append(f'{blocked} facility–medicine pairs blocked until a daily estimate is reviewed.')
    if not accepted: notes.append('No safe transfer found. Review reorder suggestions and uncovered early shortages.')
    notes.append('Reorder quantities assume the listed transfers are accepted; regenerate after any rejection or inventory change.')
    return Plan(ds.version, accepted+reorders, solver_label, notes,
                {'horizon_days':30,'review_days':7,'minimum_shelf_life_on_arrival':2,
                 'upper_demand':'Empirical seven-day upper error spread across days; +25% where unavailable.'})
