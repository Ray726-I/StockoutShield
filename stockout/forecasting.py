from __future__ import annotations
from dataclasses import dataclass, asdict
import numpy as np
import pandas as pd
from .data import Dataset

METHODS = ("Average · 7 days", "Average · 28 days", "Weekly seasonal", "Croston-SBA", "Annual seasonal")


@dataclass
class Prediction:
    facility_id: str
    medicine_id: str
    mean: list[float]
    upper: list[float]
    lower7: float | None
    upper7: float | None
    method: str
    validation_mae7: float | None
    coverage: float | None
    calibration_windows: int
    reliable_days: int
    unreliable_fraction: float
    actionable: bool
    warnings: list[str]
    dataset_version: str

    def payload(self):
        return asdict(self)


def predict_values(values, method, horizon=30):
    x = np.asarray(values, dtype=float)
    if not len(x):
        return np.zeros(horizon)
    if method == METHODS[0]:
        return np.repeat(np.mean(x[-7:]), horizon)
    if method == METHODS[1]:
        return np.repeat(np.mean(x[-28:]), horizon)
    if method in [METHODS[2], METHODS[4]]:
        period = 7 if method == METHODS[2] else 365
        tail = x[-period:]
        return np.resize(tail, horizon)
    # Bias-corrected Croston. No native probabilistic intervals are claimed.
    positive = np.flatnonzero(x > 0)
    if not len(positive):
        return np.zeros(horizon)
    alpha = .1
    q, interval = float(x[positive[0]]), float(positive[0] + 1)
    previous = positive[0]
    for i in positive[1:]:
        q += alpha * (x[i] - q)
        interval += alpha * ((i - previous) - interval)
        previous = i
    return np.repeat((1 - alpha/2)*q/max(interval, 1), horizon)


def causal_fill(raw, reliable):
    """Only earlier reliable records may fill missing/censored dates; originals remain untouched."""
    result = np.zeros(len(raw), dtype=float)
    history = []
    for i, (v, ok) in enumerate(zip(raw, reliable)):
        if ok:
            result[i] = v
            history.append((i, float(v)))
        else:
            recent = [(j, q) for j, q in history if i-j <= 28]
            weekday = [q for j, q in recent if (i-j) % 7 == 0]
            estimates = weekday if len(weekday) >= 2 else [q for _, q in recent]
            estimate = float(np.mean(estimates)) if estimates else 0.0
            result[i] = max(0 if not np.isfinite(v) else v, estimate)
    return result


def forecast(ds: Dataset, horizon=30, reviewed=None):
    reviewed = reviewed or {}
    result = {}
    usage = ds.tables["usage"].copy()
    usage["date"] = pd.to_datetime(usage["date"])
    grouped = {(f, m): g for (f, m), g in usage.groupby(["facility_id", "medicine_id"])}
    version = ds.version
    for f in ds.tables["facilities"].facility_id:
        for m in ds.tables["medicines"].medicine_id:
            group = grouped.get((f, m), pd.DataFrame(columns=usage.columns))
            if len(group):
                dates = pd.date_range(group.date.min(), pd.Timestamp(ds.as_of)-pd.Timedelta(days=1))
                aligned = group.set_index("date").reindex(dates)
                raw = aligned.dispensed.to_numpy(dtype=float)
                reliable = (aligned.availability.eq("available") & aligned.dispensed.notna()).to_numpy()
            else:
                raw, reliable = np.array([]), np.array([], dtype=bool)
            clean = causal_fill(raw, reliable)
            window = reliable[-90:]
            bad = float(1 - window.mean()) if len(window) else 1.0
            reliable_days = int(reliable.sum())
            actionable = reliable_days >= 28 and bad <= .2
            warnings = []
            if len(raw) and (~reliable).any():
                warnings.append(f"{int((~reliable).sum())} missing or availability-affected days estimated using earlier observations.")
            if not actionable:
                warnings.append("Reviewed daily demand required: fewer than 28 reliable days or over 20% unreliable in the last 90 days.")
            methods = list(METHODS[:4])
            if reliable_days >= 730:
                methods.append(METHODS[4])
            # Each validation window follows its training data and does not overlap another target window.
            cuts = [len(clean) - 7*k for k in range(9, 0, -1) if len(clean)-7*k >= 28]
            valid = [c for c in cuts if reliable[c:c+7].all() and len(reliable[c:c+7]) == 7]
            scores, errors = {}, {}
            for method in methods:
                errs = []
                for cut in valid:
                    if method == METHODS[4] and reliable[:cut].sum() < 730:
                        continue
                    prediction = predict_values(clean[:cut], method, 7)
                    errs.append(float(raw[cut:cut+7].sum() - prediction.sum()))
                errors[method] = errs
                if errs:
                    scores[method] = float(np.mean(np.abs(errs[:-2] if len(errs)>=7 else errs)))
            method = min(scores, key=lambda k: (scores[k], methods.index(k))) if scores else METHODS[1]
            mean = predict_values(clean, method, horizon)
            low7 = high7 = coverage = None
            calibration_windows = 0
            errs = errors[method]
            # Calibrate on earlier windows, measure coverage on the final two windows.
            if len(errs) >= 7:
                calibration = errs[:-2]
                calibration_windows = len(calibration)
                lo, hi = np.quantile(calibration, [.1, .9])
                low7 = float(max(0, mean[:7].sum() + min(0, lo)))
                high7 = float(max(mean[:7].sum(), mean[:7].sum() + hi))
                coverage = float(np.mean([(min(0, lo) <= e <= max(0, hi)) for e in errs[-2:]]))
                uplift = (high7-float(mean[:7].sum()))/7
            else:
                uplift = float(mean.mean()*.25)  # Explicit stress assumption, not a confidence interval.
                warnings.append("Uncertainty unavailable: not enough clean validation windows. Stress test uses +25% demand.")
            if f"{f}|{m}" in reviewed:
                daily = float(reviewed[f"{f}|{m}"])
                if not np.isfinite(daily) or daily < 0:
                    raise ValueError("Reviewed demand must be finite and nonnegative.")
                mean = np.repeat(daily, horizon)
                uplift = daily*.25
                actionable = True
                method = "Reviewed daily estimate"
                low7 = high7 = coverage = None
                calibration_windows = 0
                warnings.append("Staff-reviewed estimate; upper-demand stress assumption is +25%, not a confidence interval.")
            result[(f, m)] = Prediction(f, m, mean.tolist(), (mean+uplift).tolist(),
                low7, high7, method, scores.get(method), coverage, calibration_windows,
                reliable_days, bad, actionable, warnings, version)
    return result
