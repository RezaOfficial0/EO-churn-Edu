"""DEMO ONLY: record one synthetic "yesterday" run, so a fresh demo shows trends.

    python scripts/seed_demo_history.py            # refuses if the alert log has any run
    python scripts/seed_demo_history.py --force    # seed anyway

Why this exists. The daily message has two parts that only appear when there is
an *earlier* run to compare against: the summary of students who were already at
risk yesterday, and the arrows next to them ("%56 ↑ (önceki %42)"). A freshly
reset demo has exactly one run, so every student reads as new and neither part
ever renders - the most carefully built feature is invisible in front of a
customer.

This writes one run timestamped 24 hours ago, made from today's own scores:

  - a fixed subset (about 60%) of today's at-risk students is carried into
    "yesterday", so tomorrow's real run marks them `still_at_risk` and the rest
    `new` - both sections of the message appear;
  - their "yesterday" probability is today's scaled down for most of them (risk
    rising, the story a mentor needs to act on) and up for a few (improving), and
    never below the threshold, since a student under it would not have been
    recorded at all.

The choices are seeded, so every reset produces the same demo.

**This fabricates history.** It exists for demo_reset.sh on a machine holding
synthetic data, and refuses to run against an alert log that already has runs
unless told to. Never run it against a customer's data.
"""
import argparse
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (  # noqa: E402
    CALIBRATOR_PATH,
    DAILY_ALERTS_PATH,
    MODEL_META_PATH,
    MODEL_PATH,
    STUDENT_INFO,
)
from pipeline.daily_pipeline import score_students  # noqa: E402
from src.data.loader import append_to_alert_log, latest_run_alerts  # noqa: E402
from src.explainer.shap_explainer import create_explainer  # noqa: E402
from src.logging_setup import configure_logging  # noqa: E402
from src.model.calibrate import load_calibrator  # noqa: E402
from src.model.load import load_meta, load_model  # noqa: E402

logger = logging.getLogger(__name__)

_ID_COLUMN = STUDENT_INFO[0]

SEED = 7
CARRY_OVER = 0.6
# Most carried-over students were lower yesterday (risk rising, arrow up); about a
# quarter were higher (improving, arrow down). Ranges are wide enough that the
# change survives rounding to whole percentages in the message.
RISING = (0.72, 0.92)
FALLING = (1.06, 1.18)
SHARE_FALLING = 0.25
CEILING = 0.99


def make_history(
    at_risk: pd.DataFrame,
    threshold: float,
    *,
    carry_over: float = CARRY_OVER,
    seed: int = SEED,
) -> pd.DataFrame:
    """Build yesterday's synthetic run from today's at-risk students. Pure.

    Returns the rows to record, with `status` = "new" (it is the first run) and a
    rewritten `churn_probability`. Leaves at least one of today's students out
    whenever there are two or more, so today's run has a `new` section too.
    """
    if at_risk.empty or len(at_risk) < 2:
        empty = at_risk.iloc[0:0].copy()
        empty["status"] = pd.Series(dtype=str)
        return empty

    rng = np.random.default_rng(seed)
    # Sort first: the choice must depend on the students, not on row order.
    ordered = at_risk.sort_values(_ID_COLUMN).reset_index(drop=True)
    n = len(ordered)
    k = min(max(1, round(n * carry_over)), n - 1)
    carried = ordered.iloc[np.sort(rng.choice(n, size=k, replace=False))].copy()

    falling = rng.random(k) < SHARE_FALLING
    factors = np.where(
        falling,
        rng.uniform(*FALLING, size=k),
        rng.uniform(*RISING, size=k),
    )
    carried["churn_probability"] = np.clip(
        carried["churn_probability"].to_numpy() * factors, threshold, CEILING
    )
    carried["status"] = "new"
    return carried.reset_index(drop=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--force", action="store_true", help="seed even if the alert log already has runs"
    )
    args = parser.parse_args(argv)
    configure_logging()

    existing = latest_run_alerts(DAILY_ALERTS_PATH)
    if not existing.empty and not args.force:
        print(
            "error: the alert log already has runs. This script fabricates history and\n"
            "is meant for a freshly reset demo only (scripts/demo_reset.sh does that).\n"
            "Use --force if you are sure this is disposable demo data.",
            file=sys.stderr,
        )
        return 1

    model = load_model(MODEL_PATH)
    meta = load_meta(MODEL_META_PATH) or {}
    threshold = meta.get("chosen_threshold")
    if threshold is None:
        print("error: model_meta.json has no chosen_threshold - train the model first.", file=sys.stderr)
        return 1

    at_risk = score_students(
        model=model,
        explainer=create_explainer(model),
        calibrator=load_calibrator(CALIBRATOR_PATH),
        imputation_values=meta.get("imputation_values", {}),
        threshold=threshold,
    )
    history = make_history(at_risk, threshold)
    if history.empty:
        print(f"nothing to seed: {len(at_risk)} student(s) at risk today, need at least 2.")
        return 0

    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    append_to_alert_log(history, DAILY_ALERTS_PATH, run_at=yesterday)
    logger.warning("DEMO: synthetic run recorded for %s", yesterday.isoformat())
    print(
        f"DEMO: synthetic 'yesterday' run recorded - {len(history)} of today's "
        f"{len(at_risk)} at-risk students carried over.\n"
        f"The next real run will mark those {len(history)} still_at_risk and "
        f"the other {len(at_risk) - len(history)} new."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
