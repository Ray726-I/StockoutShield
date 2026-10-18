"""Entirely synthetic fixtures. Future latent demand never enters the application dataset."""
from datetime import date, timedelta
import numpy as np
import pandas as pd
from .data import Dataset, validate_import, workbook_bytes

AS_OF = date(2026, 10, 18)


def make_sample(seed=8, days=None, profile='compact'):
    if profile not in {'compact', 'medium', 'expanded'}:
        raise ValueError('Choose compact, medium or expanded.')
    days = days if days is not None else (1095 if profile == 'expanded' else 540 if profile == 'medium' else 900)
    rng = np.random.default_rng(seed)
    facilities = pd.DataFrame([
        dict(facility_id=f"F{i+1}", name=name, lead_days=lead, reserve_days=7)
        for i, (name, lead) in enumerate([
            ("North Community Centre", 7), ("Riverside Health Centre", 5),
            ("East District Clinic", 7), ("Hillview Primary Centre", 10),
            ("Central Medical Store", 4),
            ("Lakeside Health Centre", 6), ("Westgate Community Clinic", 8),
            ("Greenfield Primary Centre", 9), ("Old Town Health Centre", 5),
            ("South District Clinic", 7), ("Meadow Rural Centre", 12),
            ("Valley Community Centre", 10)][:12 if profile == 'expanded' else 8 if profile == 'medium' else 5])])
    specs = [
        ("Paracetamol", "500 mg", "tablet", "tablet", 10, 0.6),
        ("Oral rehydration salts", "20.5 g", "powder", "sachet", 1, 8.0),
        ("Metformin", "500 mg", "tablet", "tablet", 10, 1.2),
        ("Amlodipine", "5 mg", "tablet", "tablet", 10, 0.9),
        ("Cetirizine", "10 mg", "tablet", "tablet", 10, 0.8),
        ("Zinc sulfate", "20 mg elemental zinc", "dispersible tablet", "tablet", 10, 1.0),
        ("Ferrous sulfate + folic acid", "60 mg + 500 mcg", "tablet", "tablet", 10, 1.1),
        ("Omeprazole", "20 mg", "capsule", "capsule", 10, 1.5),
        ("Calcium carbonate", "500 mg elemental calcium", "tablet", "tablet", 10, 1.3),
        ("Clotrimazole", "1% w/w, 15 g", "cream", "tube", 1, 12.0),
        ("Paracetamol", "250 mg / 5 mL, 60 mL", "oral suspension", "bottle", 1, 18.0),
        ("Ibuprofen", "200 mg", "tablet", "tablet", 10, 1.0),
        ("Folic acid", "5 mg", "tablet", "tablet", 10, .5),
        ("Losartan", "50 mg", "tablet", "tablet", 10, 1.8),
        ("Atenolol", "50 mg", "tablet", "tablet", 10, 1.0),
        ("Levothyroxine", "50 mcg", "tablet", "tablet", 10, 1.2),
        ("Albendazole", "400 mg", "chewable tablet", "tablet", 1, 5.0),
        ("Salbutamol", "100 mcg / actuation, 200 doses", "inhaler", "inhaler", 1, 85.0),
        ("Povidone iodine", "5% w/v, 100 mL", "topical solution", "bottle", 1, 35.0),
        ("Vitamin C", "500 mg", "tablet", "tablet", 10, 1.5)]
    specs = specs[:20 if profile == 'expanded' else 14 if profile == 'medium' else 10]
    medicines = pd.DataFrame([dict(medicine_id=f"M{i+1:02}", ingredient=s[0], strength=s[1],
        formulation=s[2], unit=s[3], pack_size=s[4], unit_cost=s[5]) for i, s in enumerate(specs)])
    rows, batches, orders = [], [], []
    dates = [AS_OF - timedelta(days=days-i) for i in range(days)]
    for fi in range(len(facilities)):
        for mi in range(len(medicines)):
            fid, mid = f"F{fi+1}", f"M{mi+1:02}"
            base = [10,7,9,6,4,4,8,5,6,.7,1.2,5,5,6,4,3,.8,.4,.5,4][mi] * [1,.8,1.1,.6,1.2,.9,.7,.65,1.05,1.15,.5,.75][fi]
            seasonal = np.array([1 + .22*np.sin(2*np.pi*d.timetuple().tm_yday/365.25)
                                 + .1*(d.weekday() < 5) for d in dates])
            latent = rng.poisson(np.maximum(.05, base*seasonal))
            if mi in [9,16,17,18]:
                latent *= (rng.random(days) < .28)
            for di, day in enumerate(dates):
                if rng.random() < .015:
                    continue  # Deliberately missing observation, never a zero.
                availability = "stockout" if rng.random() < .012 else "available"
                qty = int(latent[di]) if availability == "available" else int(latent[di] * .25)
                rows.append(dict(date=str(day), facility_id=fid, medicine_id=mid,
                                 dispensed=qty, availability=availability))
            cover = [2,35,8,4,26,30,5,18,32,7,3,24][fi] if mi in [0,1,5,10,12,16] else [12,24,10,8,22,28,9,20,30,11,6,23][fi]
            total = int(np.ceil(base*cover/medicines.iloc[mi].pack_size)*medicines.iloc[mi].pack_size)
            for part, share, expiry in [("A", .65, 12 if fi in [1,4,5,8] else 90), ("B", .35, 180)]:
                amount = int(total * share) if part == "A" else total - int(total * .65)
                batches.append(dict(batch_id=f"{fid}-{mid}-{part}", facility_id=fid, medicine_id=mid,
                    quantity=amount, expiry=str(AS_OF+timedelta(days=expiry)), status="available"))
            if fi in [0,2,3,6,9,10]:
                orders.append(dict(order_id=f"PO-{fid}-{mid}", facility_id=fid, medicine_id=mid,
                    quantity=max(10, int(base*10)), arrival=str(AS_OF+timedelta(days=int(facilities.iloc[fi].lead_days))),
                    expiry=str(AS_OF+timedelta(days=200))))
    # An explicit quarantine example, excluded from usable stock.
    batches.append(dict(batch_id="QUARANTINE", facility_id="F1", medicine_id="M01", quantity=500,
                        expiry=str(AS_OF+timedelta(days=100)), status="quarantined"))
    routes = [dict(from_facility=f"F{i+1}", to_facility=f"F{j+1}",
                   travel_days=1 + int(abs(i-j)>2), cost_per_pack=float(1+abs(i-j)))
              for i in range(len(facilities)) for j in range(len(facilities)) if i != j]
    tables = dict(facilities=facilities, medicines=medicines, batches=pd.DataFrame(batches),
                  usage=pd.DataFrame(rows), orders=pd.DataFrame(orders), routes=pd.DataFrame(routes))
    label = "Regional care network" if profile in {'medium', 'expanded'} else "Five-centre demonstration"
    ds, issues = validate_import(tables, AS_OF, synthetic=True, label=label)
    if ds is None:
        raise ValueError(issues.to_string())
    return ds


def write_sample(directory, profile='compact'):
    from pathlib import Path
    import json
    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    ds = make_sample(profile=profile)
    (dest/"sample.json").write_text(json.dumps(ds.payload()))
    (dest/"sample.xlsx").write_bytes(workbook_bytes(ds))
    for name, frame in ds.tables.items():
        frame.to_csv(dest/f"{name}.csv", index=False)
    return ds
