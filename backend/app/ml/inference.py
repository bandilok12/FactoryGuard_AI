"""Model loading & inference helpers used by the API and the simulator."""
import json
import threading
import numpy as np
import pandas as pd
import joblib

from ..config import ML_DIR

_lock = threading.Lock()
_models: dict = {}

FRIENDLY = {
    "temperature": "Temperature", "vibration": "Vibration", "pressure": "Pneumatic pressure",
    "rpm": "Spindle speed", "torque": "Torque", "tool_wear": "Tool wear", "power_w": "Power draw",
    "vib_slope": "Vibration trend", "temp_slope": "Temperature trend",
    "seal_temp": "Sealing temperature", "seal_pressure": "Sealing pressure", "line_speed": "Line speed",
    "fill_weight_dev": "Fill-weight deviation", "humidity": "Humidity",
}
MODE_TEXT = {
    "bearing": "Bearing / drive wear (rising vibration)",
    "overheat": "Overheating (heater / cooling problem)",
    "pneumatic": "Pneumatic pressure loss (leak / regulator)",
    "overstrain": "Mechanical overstrain (high torque x wear)",
    "tool_wear": "Tooling worn out (punch/die/cutter)",
    "power": "Drive power fault",
    "random": "Random / unclassified failure",
    "none": "No failure expected",
}


def _load():
    with _lock:
        if _models:
            return _models
        def load_all():
            _models["failure"] = joblib.load(ML_DIR / "failure_model.joblib")
            _models["anomaly"] = joblib.load(ML_DIR / "anomaly_model.joblib")
            _models["defect"] = joblib.load(ML_DIR / "defect_model.joblib")
        try:
            load_all()
        except Exception:   # missing artefacts or saved with another scikit-learn version -> retrain (~15 s)
            from .train import train_all
            train_all(verbose=False)
            load_all()
        for k in ("failure", "defect"):     # single-row inference is much faster without worker pools
            for c in ("clf", "mode_clf", "type_clf"):
                if hasattr(_models[k].get(c), "n_jobs"):
                    _models[k][c].n_jobs = 1
        try:
            _models["metrics"] = json.loads((ML_DIR / "metrics.json").read_text())
        except Exception:
            _models["metrics"] = {}
        return _models


def metrics():
    return _load().get("metrics", {})


def risk_level(p: float) -> str:
    return "critical" if p >= 0.75 else "high" if p >= 0.5 else "medium" if p >= 0.25 else "low"


# ----------------------------------------------------------------------- failure prediction
def build_failure_features(readings: list[dict]) -> dict:
    """readings: chronological list of dicts with sensor fields (most recent last)."""
    df = pd.DataFrame(readings)
    recent = df.tail(10)
    feat = {c: float(recent[c].mean()) for c in ["temperature", "vibration", "pressure", "rpm", "torque"]}
    feat["tool_wear"] = float(df["tool_wear"].iloc[-1])
    feat["power_w"] = feat["torque"] * feat["rpm"] * 2 * np.pi / 60
    trend = df.tail(20)                       # longer window => stable trend estimate
    n = len(trend)
    if n >= 5:
        x = np.arange(n)
        feat["vib_slope"] = float(np.polyfit(x, trend["vibration"].values, 1)[0])
        feat["temp_slope"] = float(np.polyfit(x, trend["temperature"].values, 1)[0])
    else:
        feat["vib_slope"] = feat["temp_slope"] = 0.0
    return feat


def predict_failure(features: dict) -> dict:
    m = _load()["failure"]
    X = pd.DataFrame([features])[m["features"]]
    p = float(m["clf"].predict_proba(X)[0, 1])
    mode = "none"
    if p >= 0.25:
        mode = str(m["mode_clf"].predict(X)[0])
    # explanation: deviation from healthy baseline weighted by global importance
    imp = dict(zip(m["features"], m["clf"].feature_importances_))
    contrib = []
    for f in m["features"]:
        z = (features[f] - m["baseline_mean"][f]) / (m["baseline_std"][f] or 1)
        contrib.append((f, abs(z) * imp[f], z))
    contrib.sort(key=lambda t: -t[1])
    factors = [{"feature": f, "label": FRIENDLY.get(f, f), "value": round(features[f], 3),
                "z_score": round(z, 2), "direction": "high" if z > 0 else "low"}
               for f, s, z in contrib[:3] if abs(z) > 1]
    hours = None
    if p >= 0.25:  # rough remaining-useful-life heuristic for the UI
        hours = int(max(2, (1 - p) * 72))
    return {"probability": round(p, 4), "risk_level": risk_level(p), "predicted_mode": mode,
            "mode_description": MODE_TEXT.get(mode, mode), "top_factors": factors,
            "est_hours_to_failure": hours, "features": {k: round(v, 3) for k, v in features.items()}}


# ----------------------------------------------------------------------- anomaly detection
def detect_anomaly_batch(readings: list[dict], limits_list: list[dict | None]) -> list[dict]:
    """Hybrid detector: IsolationForest score + z-score + machine-specific limits (vectorised)."""
    if not readings:
        return []
    m = _load()["anomaly"]
    X = pd.DataFrame([{f: r[f] for f in m["features"]} for r in readings])
    raw = m["iso"].score_samples(X)                       # one pass over the forest
    # stricter than the training contamination cut-off so live streams do not flood alerts
    iso_flags = (raw - m["iso"].offset_) < -0.03
    out = []
    for reading, limits, s, iso_anom in zip(readings, limits_list, raw, iso_flags):
        score = float(-s)                                 # higher => more anomalous
        reasons = []
        for f in m["features"]:
            z = (reading[f] - m["mean"][f]) / (m["std"][f] or 1)
            if abs(z) > 3.5:
                reasons.append(f"{FRIENDLY[f]} {'high' if z > 0 else 'low'} (z={z:+.1f})")
        if limits:
            if reading["temperature"] > limits.get("temp_max", 1e9):
                reasons.append(f"Temperature {reading['temperature']:.1f}°C > limit {limits['temp_max']}°C")
            if reading["vibration"] > limits.get("vibration_max", 1e9):
                reasons.append(f"Vibration {reading['vibration']:.2f} mm/s > ISO limit {limits['vibration_max']}")
            if reading["pressure"] < limits.get("pressure_min", -1):
                reasons.append(f"Pressure {reading['pressure']:.2f} bar < min {limits['pressure_min']}")
            if reading["pressure"] > limits.get("pressure_max", 1e9):
                reasons.append(f"Pressure {reading['pressure']:.2f} bar > max {limits['pressure_max']}")
        if iso_anom and not reasons:
            reasons.append("Unusual combination of readings (Isolation Forest)")
        is_anom = bool(iso_anom) or bool(reasons)
        sev = "critical" if len(reasons) >= 2 or score > 0.62 else "warning"
        out.append({"is_anomaly": is_anom, "score": round(score, 4), "severity": sev if is_anom else "normal",
                    "reasons": reasons})
    return out


def detect_anomaly(reading: dict, limits: dict | None = None) -> dict:
    return detect_anomaly_batch([reading], [limits])[0]


# ----------------------------------------------------------------------- batch defect risk
DEFECT_ADVICE = {
    "seal_temp": "Bring sealing temperature back to 145-155 °C",
    "seal_pressure": "Re-set sealing pressure to 4.0-5.0 bar",
    "line_speed": "Reduce line speed below 52 strokes/min",
    "fill_weight_dev": "Re-calibrate dosing / feeder (keep fill deviation within ±1.5%)",
    "humidity": "Check dehumidifier — keep room RH below 55%",
    "vibration": "Inspect machine for vibration before running the batch",
}
OPT = {"seal_temp": (145, 155), "seal_pressure": (4.0, 5.0), "line_speed": (0, 52),
       "fill_weight_dev": (-1.5, 1.5), "humidity": (0, 55), "vibration": (0, 5)}


def defect_risk_batch(rows: list[dict]) -> list[float]:
    """Vectorised defect probability for many batches (used by seeding/analytics)."""
    m = _load()["defect"]
    X = pd.DataFrame([{f: (r.get(f) if r.get(f) is not None else 3.0) for f in m["features"]} for r in rows])
    return m["clf"].predict_proba(X)[:, 1].round(4).tolist()


def predict_defect(params: dict) -> dict:
    m = _load()["defect"]
    row = {f: params.get(f) for f in m["features"]}
    if row.get("vibration") is None:
        row["vibration"] = 3.0
    X = pd.DataFrame([row])[m["features"]]
    p = float(m["clf"].predict_proba(X)[0, 1])
    dtype = str(m["type_clf"].predict(X)[0]) if p >= 0.3 else "none"
    issues = []
    for f, (lo, hi) in OPT.items():
        v = row[f]
        if v < lo or v > hi:
            issues.append({"parameter": FRIENDLY[f], "value": v, "optimal": f"{lo} – {hi}",
                           "action": DEFECT_ADVICE[f]})
    return {"defect_probability": round(p, 4), "risk_level": risk_level(p),
            "likely_defect_type": dtype, "out_of_spec": issues,
            "recommendation": "HOLD batch for 100% inspection" if p >= 0.6 else
                              "Increase sampling (AQL tightened)" if p >= 0.3 else "Proceed with normal sampling"}
