from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from datetime import date
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Record(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Facility(Record):
    facility_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    lead_days: int = Field(ge=1, le=21)
    reserve_days: int = Field(default=7, ge=1, le=21)


class Medicine(Record):
    medicine_id: str = Field(min_length=1)
    ingredient: str = Field(min_length=1)
    strength: str = Field(min_length=1)
    formulation: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    pack_size: int = Field(ge=1)
    unit_cost: float = Field(ge=0)


class Batch(Record):
    batch_id: str = Field(min_length=1)
    facility_id: str
    medicine_id: str
    quantity: int = Field(ge=0)
    expiry: date
    status: Literal["available", "quarantined"] = "available"


class Usage(Record):
    date: date
    facility_id: str
    medicine_id: str
    dispensed: int = Field(ge=0)
    availability: Literal["available", "stockout", "unknown"]


class Order(Record):
    order_id: str
    facility_id: str
    medicine_id: str
    quantity: int = Field(ge=1)
    arrival: date
    expiry: date


class Route(Record):
    from_facility: str
    to_facility: str
    travel_days: int = Field(ge=1, le=7)
    cost_per_pack: float = Field(ge=0)


MODELS = {"facilities": Facility, "medicines": Medicine, "batches": Batch,
          "usage": Usage, "orders": Order, "routes": Route}
KEYS = {"facilities": ["facility_id"], "medicines": ["medicine_id"],
        "batches": ["batch_id"], "usage": ["date", "facility_id", "medicine_id"],
        "orders": ["order_id"], "routes": ["from_facility", "to_facility"]}


@dataclass
class Dataset:
    tables: dict[str, pd.DataFrame]
    as_of: date
    synthetic: bool = False
    label: str = "Imported inventory"

    def records(self):
        return {name: json.loads(frame.to_json(orient="records", date_format="iso"))
                for name, frame in self.tables.items()}

    def payload(self):
        return {"tables": self.records(), "as_of": str(self.as_of),
                "synthetic": self.synthetic, "label": self.label}

    @property
    def version(self):
        canonical = {name: sorted(rows, key=lambda r: json.dumps(r, sort_keys=True))
                     for name, rows in self.records().items()}
        encoded = json.dumps({"tables": canonical, "as_of": str(self.as_of)}, sort_keys=True)
        return hashlib.sha256(encoded.encode()).hexdigest()[:16]

    def copy(self):
        return Dataset({n: f.copy(deep=True) for n, f in self.tables.items()},
                       self.as_of, self.synthetic, self.label)

    @classmethod
    def from_payload(cls, p):
        frames = {n: pd.DataFrame(p["tables"].get(n, []), columns=list(m.model_fields))
                  for n, m in MODELS.items()}
        return cls(frames, date.fromisoformat(p["as_of"]), p.get("synthetic", False),
                   p.get("label", "Imported inventory"))


def validate_import(tables, as_of, mapping=None, synthetic=False, label="Imported inventory"):
    """Validate atomically. Return (Dataset | None, issue DataFrame). No implicit name matching."""
    issues, clean = [], {}
    def issue(table, row, message, severity="error"):
        issues.append(dict(table=table, row=row, severity=severity, message=message))
    as_of = date.fromisoformat(str(as_of))
    for name, model in MODELS.items():
        if name not in tables:
            issue(name, 0, "Required sheet/file is missing (empty orders sheet is allowed).")
            continue
        frame = tables[name].copy().dropna(how="all")
        required = [k for k, field in model.model_fields.items() if field.is_required()]
        missing = set(required) - set(frame.columns)
        if missing:
            issue(name, 0, "Missing columns: " + ", ".join(sorted(missing)))
            continue
        rows = []
        for i, raw in enumerate(frame.to_dict("records"), 2):
            raw = {k: v for k, v in raw.items() if pd.notna(v)}
            if mapping and "medicine_id" in raw:
                raw["medicine_id"] = mapping.get(str(raw["medicine_id"]), raw["medicine_id"])
            try:
                obj = model.model_validate(raw)
                rows.append(obj.model_dump(mode="json"))
            except ValidationError as exc:
                for e in exc.errors():
                    issue(name, i, f"{'.'.join(map(str, e['loc']))}: {e['msg']}")
        clean[name] = pd.DataFrame(rows, columns=list(model.model_fields))
        if not clean[name].empty and clean[name].duplicated(KEYS[name]).any():
            issue(name, 0, "Duplicate key: " + ", ".join(KEYS[name]))
    if len(clean) == len(MODELS):
        facilities = set(clean["facilities"].facility_id)
        meds = set(clean["medicines"].medicine_id)
        if not facilities or not meds:
            issue("facilities", 0, "At least one facility and medicine are required.")
        for name, frame in clean.items():
            for col in ["facility_id", "from_facility", "to_facility"]:
                if col in frame:
                    for unknown in sorted(set(frame[col]) - facilities):
                        issue(name, 0, f"Unknown facility: {unknown}")
            if "medicine_id" in frame and name != "medicines":
                for unknown in sorted(set(frame.medicine_id) - meds):
                    issue(name, 0, f"Unknown medicine identifier '{unknown}': provide an explicit mapping.")
        medkey = ["ingredient", "strength", "formulation", "unit"]
        if clean["medicines"].duplicated(medkey).any():
            issue("medicines", 0, "Duplicate catalogue specification: consolidate its medicine identifiers.")
        for name in ["batches", "orders"]:
            for i, row in clean[name].iterrows():
                if name == "orders" and row["arrival"] < str(as_of):
                    issue(name, i + 2, "Incoming order arrives before snapshot date; reconcile it into batches.")
                if name == "orders" and row["expiry"] <= row["arrival"]:
                    issue(name, i + 2, "Order expires on or before arrival.")
                if name == "batches" and row["expiry"] <= str(as_of):
                    issue(name, i + 2, "Expired batch will be excluded and written off in projections.", "warning")
        for i, row in clean["usage"].iterrows():
            if row["date"] >= str(as_of):
                issue("usage", i + 2, "Usage must precede the snapshot date (prevents future-data leakage).")
        routes = clean["routes"]
        if not routes.empty and (routes.from_facility == routes.to_facility).any():
            issue("routes", 0, "A transfer route must connect different facilities.")
        # Extra supplied specifications must agree with the catalogue, rather than being ignored.
        catalogue = clean["medicines"].set_index("medicine_id")
        for name in ["batches", "usage", "orders"]:
            for i, raw in enumerate(tables[name].to_dict("records"), 2):
                mid = str(raw.get("medicine_id", ""))
                mid = (mapping or {}).get(mid, mid)
                if mid in catalogue.index:
                    for col in medkey:
                        if col in raw and pd.notna(raw[col]) and str(raw[col]) != str(catalogue.loc[mid, col]):
                            issue(name, i, f"{col} conflicts with catalogue for {mid}.")
    report = pd.DataFrame(issues, columns=["table", "row", "severity", "message"])
    if not report.empty and (report.severity == "error").any():
        return None, report
    return Dataset(clean, as_of, synthetic, label), report


def workbook_bytes(ds):
    stream = io.BytesIO()
    with pd.ExcelWriter(stream, engine="openpyxl") as writer:
        for name, frame in ds.tables.items():
            frame.to_excel(writer, sheet_name=name, index=False)
        pd.DataFrame([{"as_of": str(ds.as_of), "synthetic": ds.synthetic,
                       "label": ds.label}]).to_excel(writer, sheet_name="metadata", index=False)
    return stream.getvalue()


def read_workbook(content):
    sheets = pd.read_excel(io.BytesIO(content), sheet_name=None)
    meta = sheets.pop("metadata", pd.DataFrame()).to_dict("records")
    return sheets, (meta[0] if meta else {})


def empty_templates():
    return {name: pd.DataFrame(columns=list(model.model_fields)) for name, model in MODELS.items()}
