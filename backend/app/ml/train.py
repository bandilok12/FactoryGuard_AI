"""
Train all FactoryGuard ML models on SYNTHETIC data.

The failure data generator follows the physics-inspired rules of the public
AI4I 2020 Predictive Maintenance dataset (UCI / Kaggle: tool-wear, heat-dissipation,
power and overstrain failures) extended with bearing (vibration) and pneumatic
(pressure) failure modes typical of blister/strip packaging lines.
No real factory data is used.

Models produced (backend/app/ml/artifacts/):
  failure_model.joblib  RandomForest  -> P(failure in next 24h) + failure mode
  anomaly_model.joblib  IsolationForest + healthy baseline stats
  defect_model.joblib   GradientBoosting -> P(batch defect) + likely defect type
  metrics.json          hold-out evaluation metrics

Run:  python -m app.ml.train
"""
import json
import numpy as np
import pandas as pd
import joblib
from sklearn.ensemble import RandomForestClassifier, IsolationForest, GradientBoostingClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score, f1_score, roc_auc_score, precision_score, recall_score

from ..config import ML_DIR

FAILURE_FEATURES = ["temperature", "vibration", "pressure", "rpm", "torque", "tool_wear",
                    "power_w", "vib_slope", "temp_slope"]
SENSOR_FEATURES = ["temperature", "vibration", "pressure", "rpm", "torque", "tool_wear"]
DEFECT_FEATURES = ["seal_temp", "seal_pressure", "line_speed", "fill_weight_dev", "humidity", "vibration"]

RNG = np.random.default_rng(42)


# --------------------------------------------------------------------------- data generators
def healthy_sensors(n, rng=RNG):
    return pd.DataFrame({
        "temperature": rng.normal(55, 4, n),
        "vibration": np.clip(rng.normal(2.8, 0.8, n), 0.3, None),
        "pressure": rng.normal(6.0, 0.3, n),
        "rpm": rng.normal(1500, 90, n),
        "torque": np.clip(rng.normal(40, 8, n), 5, None),
        "tool_wear": rng.uniform(0, 180, n),
        # slope noise matches a 20-reading regression on real sensor noise (vib σ≈0.35, temp σ≈1.6)
        "vib_slope": rng.normal(0, 0.02, n),
        "temp_slope": rng.normal(0, 0.08, n),
    })


def generate_failure_data(n=24000, rng=RNG):
    df = healthy_sensors(n, rng)
    mode = rng.choice(["none", "bearing", "overheat", "pneumatic", "overstrain", "tool_wear"],
                      size=n, p=[0.55, 0.10, 0.09, 0.09, 0.08, 0.09])
    d = rng.beta(2, 2, n)  # degradation severity 0..1
    m = mode == "bearing"
    df.loc[m, "vibration"] += d[m] * 9
    df.loc[m, "temperature"] += d[m] * 8
    df.loc[m, "vib_slope"] += d[m] * 0.35
    m = mode == "overheat"
    df.loc[m, "temperature"] += d[m] * 35
    df.loc[m, "temp_slope"] += d[m] * 0.8
    df.loc[m, "rpm"] -= d[m] * 200
    m = mode == "pneumatic"
    df.loc[m, "pressure"] -= d[m] * 2.6
    m = mode == "overstrain"
    df.loc[m, "torque"] += d[m] * 35
    df.loc[m, "tool_wear"] += d[m] * 60
    m = mode == "tool_wear"
    df.loc[m, "tool_wear"] = rng.uniform(150, 250, m.sum())
    df["power_w"] = df["torque"] * df["rpm"] * 2 * np.pi / 60

    # failure labelling rules (AI4I-style thresholds)
    rules = {
        "bearing": (df.vibration > 7.5) | (df.vib_slope > 0.22),
        "overheat": (df.temperature > 80) | ((df.temperature > 72) & (df.temp_slope > 0.5)),
        "pneumatic": df.pressure < 4.4,
        "overstrain": df.tool_wear * df.torque > 11000,
        "tool_wear": df.tool_wear > 215,
        "power": (df.power_w < 3000) | (df.power_w > 11000),
    }
    label = np.zeros(n, dtype=int)
    fmode = np.array(["none"] * n, dtype=object)
    for name, cond in rules.items():
        c = cond.values & (label == 0)
        label[c] = 1
        fmode[c] = name
    flip = rng.random(n) < 0.02   # label noise
    label[flip] = 1 - label[flip]
    fmode[(label == 1) & (fmode == "none")] = "random"
    fmode[label == 0] = "none"
    df["failure"] = label
    df["mode"] = fmode
    return df


def generate_defect_data(n=16000, rng=RNG):
    df = pd.DataFrame({
        "seal_temp": rng.normal(150, 9, n),         # °C, optimum 145-155 (PVC/Alu blister)
        "seal_pressure": rng.normal(4.5, 0.45, n),  # bar
        "line_speed": rng.normal(45, 9, n),         # strokes/min
        "fill_weight_dev": rng.normal(0, 1.3, n),   # %
        "humidity": rng.normal(45, 9, n),           # %RH
        "vibration": np.clip(rng.normal(3, 1.3, n), 0.3, None),
    })
    logit = (-4.0
             + 0.45 * (np.abs(df.seal_temp - 150) - 6).clip(lower=0)
             + 5.0 * (np.abs(df.seal_pressure - 4.5) - 0.5).clip(lower=0)
             + 0.25 * (df.line_speed - 52).clip(lower=0)
             + 3.0 * (np.abs(df.fill_weight_dev) - 1.5).clip(lower=0)
             + 0.35 * (df.humidity - 55).clip(lower=0)
             + 1.5 * (df.vibration - 5).clip(lower=0))
    p = 1 / (1 + np.exp(-logit))
    df["defect"] = (rng.random(n) < p).astype(int)
    # dominant defect type
    scores = pd.DataFrame({
        "seal_leak": np.abs(df.seal_temp - 150) / 9 + np.abs(df.seal_pressure - 4.5) / 0.45,
        "underfill": (-df.fill_weight_dev).clip(lower=0) / 1.3,
        "overfill": df.fill_weight_dev.clip(lower=0) / 1.3,
        "crack": (df.humidity - 45).clip(lower=0) / 9 + (df.vibration - 3).clip(lower=0) / 1.3,
        "misprint": (df.line_speed - 45).clip(lower=0) / 9,
    })
    df["defect_type"] = np.where(df.defect == 1, scores.idxmax(axis=1), "none")
    return df


# --------------------------------------------------------------------------- training
def train_all(verbose=True):
    ML_DIR.mkdir(parents=True, exist_ok=True)
    metrics = {}

    # 1. failure prediction ---------------------------------------------------------------
    fd = generate_failure_data()
    X, y = fd[FAILURE_FEATURES], fd["failure"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=7, stratify=y)
    clf = RandomForestClassifier(n_estimators=200, max_depth=12, min_samples_leaf=3,
                                 class_weight="balanced", n_jobs=-1, random_state=7)
    clf.fit(Xtr, ytr)
    proba = clf.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics["failure_model"] = {
        "algorithm": "RandomForestClassifier(200 trees)",
        "train_rows": len(Xtr), "test_rows": len(Xte),
        "accuracy": round(accuracy_score(yte, pred), 4),
        "precision": round(precision_score(yte, pred), 4),
        "recall": round(recall_score(yte, pred), 4),
        "f1": round(f1_score(yte, pred), 4),
        "roc_auc": round(roc_auc_score(yte, proba), 4),
        "feature_importance": dict(sorted(zip(FAILURE_FEATURES, clf.feature_importances_.round(4).tolist()),
                                          key=lambda kv: -kv[1])),
    }
    fail_rows = fd[(fd.failure == 1) & (fd["mode"] != "random")]   # learn real modes only
    mode_clf = RandomForestClassifier(n_estimators=150, max_depth=12, random_state=7, n_jobs=-1)
    mode_clf.fit(fail_rows[FAILURE_FEATURES], fail_rows["mode"])
    healthy = fd[fd.failure == 0][FAILURE_FEATURES]
    joblib.dump({"clf": clf, "mode_clf": mode_clf, "features": FAILURE_FEATURES,
                 "baseline_mean": healthy.mean().to_dict(), "baseline_std": healthy.std().to_dict()},
                ML_DIR / "failure_model.joblib")

    # 2. anomaly detection ---------------------------------------------------------------
    h = healthy_sensors(8000)[SENSOR_FEATURES]
    iso = IsolationForest(n_estimators=200, contamination=0.02, random_state=7)
    iso.fit(h)
    test_norm = healthy_sensors(2000, np.random.default_rng(1))[SENSOR_FEATURES]
    test_anom = generate_failure_data(4000, np.random.default_rng(2))
    test_anom = test_anom[test_anom.failure == 1][SENSOR_FEATURES]
    fp_rate = float((iso.predict(test_norm) == -1).mean())
    det_rate = float((iso.predict(test_anom) == -1).mean())
    # hybrid = IsolationForest OR z-score>3.5 on any sensor (what the app actually uses)
    mu, sd = h.mean(), h.std()
    z_flag = lambda X: (((X - mu) / sd).abs() > 3.5).any(axis=1).values
    hyb_fp = float(((iso.predict(test_norm) == -1) | z_flag(test_norm)).mean())
    hyb_det = float(((iso.predict(test_anom) == -1) | z_flag(test_anom)).mean())
    metrics["anomaly_model"] = {"algorithm": "IsolationForest + z-score + machine limit rules",
                                "isolation_forest_false_positive_rate": round(fp_rate, 4),
                                "isolation_forest_detection_rate": round(det_rate, 4),
                                "hybrid_false_positive_rate": round(hyb_fp, 4),
                                "hybrid_detection_rate_on_failure_states": round(hyb_det, 4)}
    joblib.dump({"iso": iso, "features": SENSOR_FEATURES,
                 "mean": h.mean().to_dict(), "std": h.std().to_dict()},
                ML_DIR / "anomaly_model.joblib")

    # 3. batch defect risk ---------------------------------------------------------------
    dd = generate_defect_data()
    X, y = dd[DEFECT_FEATURES], dd["defect"]
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=7, stratify=y)
    gb = GradientBoostingClassifier(n_estimators=200, max_depth=3, learning_rate=0.08, random_state=7)
    gb.fit(Xtr, ytr)
    proba = gb.predict_proba(Xte)[:, 1]
    pred = (proba >= 0.5).astype(int)
    metrics["defect_model"] = {
        "algorithm": "GradientBoostingClassifier",
        "train_rows": len(Xtr), "test_rows": len(Xte),
        "accuracy": round(accuracy_score(yte, pred), 4),
        "f1": round(f1_score(yte, pred), 4),
        "roc_auc": round(roc_auc_score(yte, proba), 4),
        "feature_importance": dict(sorted(zip(DEFECT_FEATURES, gb.feature_importances_.round(4).tolist()),
                                          key=lambda kv: -kv[1])),
    }
    drows = dd[dd.defect == 1]
    type_clf = RandomForestClassifier(n_estimators=120, max_depth=10, random_state=7, n_jobs=-1)
    type_clf.fit(drows[DEFECT_FEATURES], drows["defect_type"])
    joblib.dump({"clf": gb, "type_clf": type_clf, "features": DEFECT_FEATURES},
                ML_DIR / "defect_model.joblib")

    (ML_DIR / "metrics.json").write_text(json.dumps(metrics, indent=2))
    fd.sample(2000, random_state=1).to_csv(ML_DIR / "sample_failure_training_data.csv", index=False)
    dd.sample(2000, random_state=1).to_csv(ML_DIR / "sample_defect_training_data.csv", index=False)
    if verbose:
        print(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    train_all()
