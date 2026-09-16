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
"""
import functools
import importlib
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
from src.logging_setup import configure_logging

# Features that describe what the MENTOR did rather than what the student did.
CONTACT_FEATURES = ["mentor_contact_freq_per_month", "days_since_last_contact"]

BASELINE = list(config.FEATURES)


def variants() -> dict[str, list[str]]:
    student_side = [f for f in BASELINE if f not in CONTACT_FEATURES]
    # Keep the categoricals: dropped to bare contact columns CatBoost has almost
    # nothing to split on and the comparison stops being about the two features.
    contact_only = [f for f in BASELINE if f in CONTACT_FEATURES or f in config.CAT_COLS]
    return {
        "full (bugünkü set)": BASELINE,
        "iletişim feature'ları çıkarıldı": student_side,
        "sadece iletişim + kategorikler": contact_only,
    }


def train_with(features: list[str], workdir: Path) -> dict:
    """Run the real training pipeline with `features`, writing nothing permanent.

    config is patched and the modules that copied the names at import time are
    reloaded, in dependency order, so the whole pipeline sees the variant.
    """
    config.FEATURES = features
    config.MODEL_PATH = str(workdir / "model.cbm")
    config.MODEL_META_PATH = str(workdir / "model_meta.json")
    config.CALIBRATOR_PATH = str(workdir / "calibrator.joblib")
    config.METRICS_DIR = str(workdir / "metrics")

    import src.data.features
    import src.data.preprocess
    import src.model.baseline
    import pipeline.training_pipeline

    for module in (
        src.data.features,
        src.data.preprocess,
        src.model.baseline,
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
    return result["meta"]


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
    }


def main() -> int:
    configure_logging()
    rows = []
    with tempfile.TemporaryDirectory(prefix="eo-churn-featureset-") as tmp:
        for name, features in variants().items():
            print(f"\n=== {name} ({len(features)} feature) ===", flush=True)
            workdir = Path(tmp) / name.replace(" ", "_").replace("'", "")
            workdir.mkdir(parents=True, exist_ok=True)
            rows.append(row(name, train_with(features, workdir)))

    print("\n" + "=" * 100)
    header = (
        f"{'variant':<34}{'n':>4}{'ROC':>8}{'PR':>8}{'PR ham':>9}"
        f"{'logreg':>9}{'CV':>8}{'eşik':>7}{'skor':>7}"
    )
    print(header)
    print("-" * 100)
    for r in rows:
        pr_raw = f"{r['pr_auc_raw']:>9.3f}" if r["pr_auc_raw"] is not None else f"{'-':>9}"
        scores = f"{r['scores']}/{r['scores_raw']}" if r["scores"] else "-"
        print(
            f"{r['variant']:<34}{r['n_feat']:>4}{r['roc_auc']:>8.3f}{r['pr_auc']:>8.3f}{pr_raw}"
            f"{r['logreg_pr_auc']:>9.3f}{r['cv_auc']:>8.3f}{r['threshold']:>7.2f}{scores:>7}"
        )
    print("=" * 100)

    full, without, contact_only = rows
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
