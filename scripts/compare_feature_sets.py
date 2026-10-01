"""Train the same pipeline on different feature sets and compare what they buy.

    python scripts/compare_feature_sets.py

Why this exists: in the current model every single at-risk student's SHAP
explanation is led by `mentor_contact_freq_per_month`, and most also carry
`days_since_last_contact`. Those two describe the MENTOR's behaviour, not the
student's. Two things follow, and only measurement tells them apart:

  - if dropping them costs little, the student-side signals were there all along
    and the model was simply taking the easier route; the explanations get more
    varied and more useful to a mentor at almost no cost in accuracy;
  - if dropping them collapses the model - and especially if they alone score as
    well as everything together - then the dataset's churn was generated from
    them, and no amount of feature work fixes that. That is a data problem, and
    it needs to be known before anyone shows a metric to a customer.

Nothing here touches saved_models/ or metrics/: every variant trains into a
temporary directory and is thrown away. Use it to decide, then edit
config.FEATURES and run `python running_train_pipeline.py` for real.

The results ARE written down, though: `docs/feature_set_comparison.json`, which
`docs/LEAKAGE_AUDIT.md` quotes. metrics/train_metrics_*.json is gitignored and
every variant here is thrown away, so without that file the audit's numbers would
be unreproducible claims in prose (B-21).
"""
import functools
import importlib
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.data.loader import data_loader
from src.logging_setup import configure_logging

# Features that describe what the MENTOR did rather than what the student did.
CONTACT_FEATURES = ["mentor_contact_freq_per_month", "days_since_last_contact"]

# Verified redundant with `grade` (B-21, docs/LEAKAGE_AUDIT.md): 7 distinct values
# per grade band, univariate ROC-AUC 0.500.
REDUNDANT_FEATURES = ["days_to_next_exam"]

BASELINE = list(config.FEATURES)
ORIGINAL_CAT_COLS = list(config.CAT_COLS)

# The set the audit landed on, and what config.FEATURES is now. Kept here as a
# variant so the comparison table always contains the shipped model's row.
POST_AUDIT = [
    f for f in BASELINE if f not in CONTACT_FEATURES and f not in REDUNDANT_FEATURES
]

# config.FEATURES exactly as it stood before B-21, so this script keeps measuring
# the "before" column after the audit narrowed the real one. Written out in full
# rather than rebuilt by appending the three dropped names: CatBoost breaks ties
# between equally good splits by feature order, so appending them instead of putting
# them back where they were moves the "before" PR-AUC by ~0.01 and the comparison
# stops being like-for-like.
PRE_AUDIT = [
    "grade",
    "track",
    "city_tier",
    "parent_involvement",
    "plan_type",
    "monthly_value_try",
    "tenure_months",
    "program_adherence_rate",
    "weekly_study_hours_planned",
    "weekly_study_hours_actual",
    "mentor_contact_freq_per_month",
    "days_since_last_contact",
    "message_response_time_hours",
    "late_response_count_30d",
    "trial_exam_count_total",
    "trial_exam_avg_net",
    "trial_exam_score_trend",
    "missed_trial_exam_count",
    "payment_delay_days_avg",
    "support_ticket_count_90d",
    "satisfaction_survey_score",
    "days_to_next_exam",
    "weekly_study_hours_actual_missing",
    "satisfaction_missing",
]
_unknown_pre_audit = sorted(set(config.FEATURES) - set(PRE_AUDIT))
assert not _unknown_pre_audit, (
    f"config.FEATURES has names PRE_AUDIT does not: {_unknown_pre_audit}. A NEW "
    "feature has to be added to PRE_AUDIT too, or the before/after table compares "
    "two different things."
)


def variants() -> dict[str, list[str]]:
    student_side = [f for f in PRE_AUDIT if f not in CONTACT_FEATURES]
    # Keep the categoricals: dropped to bare contact columns CatBoost has almost
    # nothing to split on and the comparison stops being about the two features.
    contact_only = [f for f in PRE_AUDIT if f in CONTACT_FEATURES or f in config.CAT_COLS]
    return {
        "full (denetim öncesi)": PRE_AUDIT,
        "iletişim feature'ları çıkarıldı": student_side,
        "sadece iletişim + kategorikler": contact_only,
        # The issue's headline comparison: one mentor-behaviour column, nothing else.
        "tek kolon: days_since_last_contact": ["days_since_last_contact"],
        "sadece days_to_next_exam çıkarıldı": [
            f for f in PRE_AUDIT if f not in REDUNDANT_FEATURES
        ],
        "post-fix (B-21, sevk edilen)": POST_AUDIT,
    }


def train_with(features: list[str], workdir: Path) -> dict:
    """Run the real training pipeline with `features`, writing nothing permanent.

    config is patched and the modules that copied the names at import time are
    reloaded, in dependency order, so the whole pipeline sees the variant.
    """
    config.FEATURES = features
    # A variant may drop a categorical (the single-column variant drops all five).
    # cast_categoricals and build_model iterate CAT_COLS over a frame that is
    # exactly FEATURES, so a name left in CAT_COLS that is not in the variant is a
    # KeyError, not a measurement.
    config.CAT_COLS = [c for c in ORIGINAL_CAT_COLS if c in features]
    config.MODEL_PATH = str(workdir / "model.cbm")
    config.MODEL_META_PATH = str(workdir / "model_meta.json")
    config.CALIBRATOR_PATH = str(workdir / "calibrator.joblib")
    config.METRICS_DIR = str(workdir / "metrics")

    import src.data.features
    import src.data.preprocess
    import src.model.baseline
    import src.model.cross_validate
    import src.model.model
    import pipeline.training_pipeline

    for module in (
        src.data.features,
        src.data.preprocess,
        src.model.baseline,
        # build_model binds cat_features=CAT_COLS as a DEFAULT ARGUMENT, so without
        # this reload a variant that drops a categorical hands CatBoost a column
        # name that is not in the frame.
        src.model.model,
        # binds build_model itself at import, so it must be reloaded AFTER
        # src.model.model or it keeps calling the previous variant's factory.
        src.model.cross_validate,
        pipeline.training_pipeline,
    ):
        importlib.reload(module)

    # A variant that drops a feature leaves that column sitting in the raw file,
    # and validate() rightly refuses an unexpected column - in production an extra
    # column would silently become model feature #25. Here the extras are exactly
    # the features being measured, so the check is relaxed for this script only.
    # Nothing downstream picks them up: preprocess selects config.FEATURES.
    pipeline.training_pipeline.validate = functools.partial(
        pipeline.training_pipeline.validate, allow_extra_columns=True
    )

    result = pipeline.training_pipeline.run_training_pipeline(
        config.RAW_DATA_PATH, config.MODEL_PATH
    )
    meta = result["meta"]
    meta["_stability"] = precision_at_k_stability(
        result["model"], result["calibrator"]
    )
    return meta


# How many bootstrap resamples the precision@k interval is built from. 2000 is
# plenty for a two-decimal percentile interval and costs well under a second.
BOOTSTRAP_RESAMPLES = 2000
# The k values the interval is reported at. PRECISION_AT_K (20) is the one the
# product cares about; the larger ones are here because 20 rows is 20 coin flips -
# without them nobody can tell a real difference from sampling noise.
STABILITY_K = (20, 50, 100)


def precision_at_k_stability(model, calibrator) -> dict:
    """Bootstrap a 95% interval around precision@k on the test split.

    Why this is in the comparison script and not in `evaluate_model`: precision@20
    is measured on twenty rows, and a quoted 0.75-against-0.35 can be eight
    students of luck. A founder quoting the number to a customer needs the width of
    it, and the width is the one thing the metrics dict does not carry. It stays out
    of `metrics` on purpose - GET /metrics and the dashboard read
    `precision_at_<k>` by pattern and must not be handed more keys that match it.

    The split is rebuilt rather than passed through: it is deterministic
    (`random_state=42`), so this is the same test set the pipeline evaluated on.
    """
    import src.data.features
    import src.data.preprocess
    from src.model.calibrate import churn_proba
    from src.model.evaluate import precision_at_k

    raw = data_loader(config.RAW_DATA_PATH)
    engineered, _ = src.data.features.build_training_frame(raw)
    X, y = src.data.preprocess.split_features_target(engineered)
    *_, X_test, _, _, y_test = src.data.preprocess.split_train_val_test(X, y)

    proba = np.asarray(churn_proba(model, X_test, calibrator), dtype=float)
    truth = np.asarray(y_test).astype(int)
    rng = np.random.default_rng(42)
    draws = [
        rng.integers(0, truth.size, truth.size) for _ in range(BOOTSTRAP_RESAMPLES)
    ]

    out = {"n_test": int(truth.size), "base_rate": float(truth.mean()), "at_k": {}}
    for k in STABILITY_K:
        if k > truth.size:
            continue
        resampled = [precision_at_k(truth[i], proba[i], k) for i in draws]
        low, high = np.percentile(resampled, [2.5, 97.5])
        out["at_k"][str(k)] = {
            "precision": float(precision_at_k(truth, proba, k)),
            "ci95_low": round(float(low), 4),
            "ci95_high": round(float(high), 4),
        }
    return out


def _at_k(metrics: dict, prefix: str) -> float | None:
    """Read `precision_at_<k>` / `lift_at_<k>` without knowing k.

    `evaluate_model` names these for the k it could actually measure
    (`effective_k`, B-26), so the key is `precision_at_20` here but
    `precision_at_12` on a smaller test set. Matching by prefix is the same
    pattern the dashboard uses - see README, GET /metrics.
    """
    for key, value in metrics.items():
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            return value
    return None


def row(name: str, meta: dict) -> dict:
    m = meta["metrics"]
    return {
        "variant": name,
        "n_feat": len(meta["features"]),
        "roc_auc": m["roc_auc"],
        "pr_auc": m["average_precision"],
        # Calibration costs ranking resolution, so the raw pair is the fair
        # comparison against the (uncalibrated) baselines.
        "pr_auc_raw": m.get("average_precision_uncalibrated"),
        "scores": m.get("distinct_scores"),
        "scores_raw": m.get("distinct_scores_uncalibrated"),
        "logreg_pr_auc": meta["baseline_metrics"]["logistic_regression"]["average_precision"],
        "cv_auc": meta["cv_auc_mean"],
        "threshold": meta["chosen_threshold"],
        "precision": m["precision"],
        "recall": m["recall"],
        # The number that describes the real mentor workflow (README, B-23).
        "k": m.get("precision_at_k_effective", config.PRECISION_AT_K),
        "precision_at_k": _at_k(m, "precision_at_"),
        "lift_at_k": _at_k(m, "lift_at_"),
        "flagged": int(sum(m["confusion_matrix"][r][1] for r in (0, 1))),
        "precision_at_k_stability": meta.get("_stability"),
        "features": list(meta["features"]),
    }


# Written down rather than only printed: docs/LEAKAGE_AUDIT.md quotes these numbers
# and metrics/ is gitignored, so this is the only committed record of the
# measurement behind the audit's verdicts (B-21).
RESULTS_PATH = Path(__file__).resolve().parents[1] / "docs" / "feature_set_comparison.json"


def write_results(rows: list[dict], path: Path = RESULTS_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "scripts/compare_feature_sets.py",
        "issue": "B-21",
        "data_file": Path(config.RAW_DATA_PATH).name,
        "is_synthetic_data": True,
        "precision_at_k_requested": config.PRECISION_AT_K,
        "contact_features": CONTACT_FEATURES,
        "redundant_features": REDUNDANT_FEATURES,
        "variants": rows,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return path


def main() -> int:
    configure_logging()
    rows = []
    with tempfile.TemporaryDirectory(prefix="eo-churn-featureset-") as tmp:
        for name, features in variants().items():
            print(f"\n=== {name} ({len(features)} feature) ===", flush=True)
            workdir = Path(tmp) / name.replace(" ", "_").replace("'", "")
            workdir.mkdir(parents=True, exist_ok=True)
            rows.append(row(name, train_with(features, workdir)))

    print("\n" + "=" * 118)
    header = (
        f"{'variant':<36}{'n':>4}{'ROC':>8}{'PR':>8}{'PR ham':>9}"
        f"{'logreg':>9}{'CV':>8}{'eşik':>7}{'P@K':>7}{'lift@K':>8}{'isaret':>8}"
    )
    print(header)
    print("-" * 118)
    for r in rows:
        pr_raw = f"{r['pr_auc_raw']:>9.3f}" if r["pr_auc_raw"] is not None else f"{'-':>9}"
        p_at_k = f"{r['precision_at_k']:>7.3f}" if r["precision_at_k"] is not None else f"{'-':>7}"
        lift = f"{r['lift_at_k']:>8.2f}" if r["lift_at_k"] is not None else f"{'-':>8}"
        print(
            f"{r['variant']:<36}{r['n_feat']:>4}{r['roc_auc']:>8.3f}{r['pr_auc']:>8.3f}{pr_raw}"
            f"{r['logreg_pr_auc']:>9.3f}{r['cv_auc']:>8.3f}{r['threshold']:>7.2f}"
            f"{p_at_k}{lift}{r['flagged']:>8}"
        )
    print("=" * 118)
    written = write_results(rows)
    print(f"\nsonuclar yazildi: {written}")

    by_name = {r["variant"]: r for r in rows}
    full = by_name["full (denetim öncesi)"]
    without = by_name["iletişim feature'ları çıkarıldı"]
    contact_only = by_name["sadece iletişim + kategorikler"]
    print(
        "\nNasıl okunur:\n"
        f"  - PR-AUC dengesiz sınıfta asıl gösterge. Tam set {full['pr_auc']:.3f}, "
        f"iletişim feature'ları olmadan {without['pr_auc']:.3f} "
        f"({without['pr_auc'] - full['pr_auc']:+.3f}).\n"
        f"  - Sadece iletişim + kategorikler {contact_only['pr_auc']:.3f}. Bu değer tam sete "
        "yakınsa churn pratikte o iki kolondan türetilmiş demektir;\n"
        "    model diğer 20 feature'dan kayda değer bir şey öğrenmiyor.\n"
        f"  - Her variantta CatBoost'un logistic regression'ı geçmesi gerekir "
        "(yoksa modelin bir değeri yok).\n"
        "\nKarar verdikten sonra config.FEATURES'ı düzenle ve "
        "`python running_train_pipeline.py` ile gerçek modeli eğit.\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
