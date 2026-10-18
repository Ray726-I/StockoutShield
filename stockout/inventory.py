from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta
import math
import numpy as np
import pandas as pd
from .data import Dataset


@dataclass
class Projection:
    curves: pd.DataFrame
    risks: pd.DataFrame
    expiries: pd.DataFrame


def project_pair(ds, key, demand, transfers=(), mode="mean", opening=None, incoming=None):
    """The shared day-by-day FEFO projector. Day 0 is the snapshot date; expiry date is exclusive."""
    f, m = key
    lots = [dict(r) for r in (opening if opening is not None else ds.tables["batches"].to_dict("records"))
            if r["facility_id"] == f and r["medicine_id"] == m and r["status"] == "available"]
    arrivals = [dict(r) for r in (incoming if incoming is not None else ds.tables["orders"].to_dict("records"))
                if r["facility_id"] == f and r["medicine_id"] == m]
    for t in transfers:
        if t["medicine_id"] != m:
            continue
        if t["from_facility"] == f:
            found = next((b for b in lots if b["batch_id"] == t["batch_id"]), None)
            if found is None or found["quantity"] < t["quantity"]:
                raise ValueError("Transfer exceeds available batch quantity.")
            found["quantity"] -= t["quantity"]
        if t["to_facility"] == f:
            arrivals.append(dict(batch_id="TRANSFER-"+t.get("recommendation_id", t["batch_id"]),
                facility_id=f, medicine_id=m, quantity=t["quantity"], expiry=t["expiry"],
                arrival=str(ds.as_of+timedelta(days=int(t["travel_days"])))) )
    rows, expired_rows = [], []
    for day, need in enumerate(demand):
        today = str(ds.as_of+timedelta(days=day))
        for a in arrivals:
            if str(a["arrival"]) == today:
                lots.append(dict(batch_id=a.get("batch_id", a.get("order_id", "ARRIVAL")),
                    quantity=float(a["quantity"]), expiry=str(a["expiry"])))
        expired = 0.0
        for b in lots:
            if str(b["expiry"]) <= today and b["quantity"] > 0:
                expired += b["quantity"]
                expired_rows.append(dict(facility_id=f, medicine_id=m, batch_id=b["batch_id"],
                                         expiry=today, quantity=float(b["quantity"])))
                b["quantity"] = 0
        opening_units = sum(float(b["quantity"]) for b in lots)
        remaining = float(need)
        for b in sorted(lots, key=lambda x: (str(x["expiry"]), x["batch_id"])):
            take = min(float(b["quantity"]), remaining)
            b["quantity"] -= take
            remaining -= take
        rows.append(dict(facility_id=f, medicine_id=m, date=today, day=day, demand=float(need),
            opening=opening_units, stock=sum(float(b["quantity"]) for b in lots),
            unmet=max(0, remaining), expired=expired))
    return rows, expired_rows


def project_inventory(ds, predictions, transfers=(), mode="mean"):
    curves, expiries, risks = [], [], []
    opening, incoming = ds.tables["batches"].to_dict("records"), ds.tables["orders"].to_dict("records")
    for (f,m), p in predictions.items():
        rows, expired = project_pair(ds, (f,m), getattr(p, mode), transfers, mode, opening, incoming)
        curves.extend(rows)
        expiries.extend(expired)
        shortages = [r for r in rows if r['unmet'] > 1e-6]
        first = shortages[0] if shortages else None
        risk = 'High' if first is not None and first['day'] < 7 else 'Watch' if first is not None else 'Stable'
        risks.append(dict(facility_id=f, medicine_id=m, risk=risk,
            first_shortage=first['date'] if first else 'None in 30 days',
            shortage_day=first['day'] if first else 999,
            unmet7=sum(r['unmet'] for r in rows[:7]), unmet30=sum(r['unmet'] for r in rows),
            expiry_units=sum(r['expired'] for r in rows), stock_now=rows[0]['opening'] if rows else 0,
            demand7=sum(r['demand'] for r in rows[:7]), actionable=p.actionable))
    return Projection(pd.DataFrame(curves), pd.DataFrame(risks), pd.DataFrame(expiries,
        columns=["facility_id", "medicine_id", "batch_id", "expiry", "quantity"]))


def apply_scenario(ds, facility="All facilities", medicine="All medicines", surge=1.0, delay=0, route_closed=False):
    """Fork the dataset. History stays intact; shocks are explicit forecast assumptions."""
    fork = ds.copy()
    if delay:
        orders = fork.tables["orders"]
        mask = np.ones(len(orders), dtype=bool)
        if facility != "All facilities": mask &= orders.facility_id.eq(facility).to_numpy()
        if medicine != "All medicines": mask &= orders.medicine_id.eq(medicine).to_numpy()
        orders.loc[mask, "arrival"] = [(pd.Timestamp(x)+pd.Timedelta(days=delay)).date().isoformat()
                                       for x in orders.loc[mask, "arrival"]]
    if route_closed:
        routes = fork.tables["routes"]
        if facility == "All facilities":
            fork.tables["routes"] = routes.iloc[:0].copy()
        else:
            fork.tables["routes"] = routes[~(routes.from_facility.eq(facility)|routes.to_facility.eq(facility))].copy()
    return fork


def scale_predictions(predictions, facility="All facilities", medicine="All medicines", surge=1.0):
    from dataclasses import replace
    result = {}
    for (f,m), p in predictions.items():
        factor = surge if (facility in ["All facilities", f] and medicine in ["All medicines", m]) else 1.0
        result[(f,m)] = replace(p, mean=[x*factor for x in p.mean], upper=[x*factor for x in p.upper],
            lower7=None if p.lower7 is None else p.lower7*factor,
            upper7=None if p.upper7 is None else p.upper7*factor)
    return result
