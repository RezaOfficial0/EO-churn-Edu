"""SYNTHETIC DATA. Turn the single snapshot in data/ into a fake 12-month history.

    python scripts/make_synthetic_history.py
    python scripts/make_synthetic_history.py --end-date 2026-09-30 --months 12 --seed 42

Why this exists. `scripts/backtest.py` is the retrospective-backtest offer we make
to a prospect: "send an anonymised export of your last 12 months and we will show
you which students you lost that we would have flagged, and how many days earlier".
No such export exists yet, and this repo holds exactly one snapshot
(`data/mentorluk_churn_veriseti.csv`): one row per student, no as-of date, no
history, and a `churn` flag over an undocumented window. That is not something a
walk-forward backtest can run on at all.

So this script fabricates the missing time axis, from that snapshot's own
statistics, in order that `scripts/backtest.py` can be run, tested and demonstrated
before any customer data arrives.

**Every number that comes out of a backtest of these files is fiction.** The files
are built to make that impossible to forget:

  - the filenames start with `SENTETIK_` ("synthetic" in Turkish),
  - every row of every CSV carries `config.SYNTHETIC_MARKER_COLUMN` set to
    `config.SYNTHETIC_MARKER_VALUE`,
  - the mapping JSON carries the same notice,
  - a plain-text OKUBENI (README) file is written next to them,
  - and `scripts/backtest.py` reads the marker column and refuses to print a
    single figure without a SENTETIK banner over it.

How the history is built, and what it does and does not prove
-------------------------------------------------------------
The snapshot describes each student at ONE moment. For a churner that moment is
taken to be the moment they left; for everyone else, the end of the period. The
history is then interpolated backwards:

  - **churners** start the period looking like an average non-churner and drift
    towards their own snapshot values over the `--drift-days` before they leave.
    That drift is the signal the model is supposed to catch, and `--drift-days`
    is therefore an upper bound on the lead time this data can ever show.
  - **non-churners** sit at their snapshot values with gaussian noise, no trend.
  - `tenure_months` is a clock (it counts up), `days_to_next_exam` a countdown.
    Interpolating either like a behavioural feature would produce a student whose
    tenure goes down.
  - the categoricals and the plan price are static.
  - a null in the snapshot stays null in every row for that student, so the
    backtest's imputation path is actually exercised.

What this therefore demonstrates: that the walk-forward machinery runs, that the
metrics are computed over real folds, that the plumbing from two CSVs to a JSON
report works. What it cannot demonstrate: anything about accuracy. The drift is
something this script put there on purpose, and a model finding it again is
arithmetic, not evidence.

The export deliberately uses the PROSPECT's column names, not ours
------------------------------------------------------------------
A real export will not use our column names, so neither does this one: the id is
`ogrenci_no`, the as-of date is `gozlem_tarihi`, the dates are `%d.%m.%Y`, and the
churn flag reads `Evet` / `Hayir`. The mapping JSON written alongside is what
translates them. If this script wrote our own names, the single part of the
backtest most likely to break on a real customer (the mapping) would never be
exercised before a customer exercised it.
"""
import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (  # noqa: E402
    AUDITED_OUT_FEATURES,
    BASE_DIR,
    CAT_COLS,
    FEATURE_BOUNDS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
    RAW_DATA_PATH,
    SYNTHETIC_MARKER_COLUMN,
    SYNTHETIC_MARKER_VALUE,
    TARGET_FEATURE,
)
from src.data.loader import data_loader  # noqa: E402
from src.logging_setup import configure_logging  # noqa: E402

logger = logging.getLogger(__name__)

# --- Output ------------------------------------------------------------------
HISTORY_NAME = "SENTETIK_backtest_history.csv"
OUTCOMES_NAME = "SENTETIK_backtest_outcomes.csv"
MAPPING_NAME = "SENTETIK_backtest_mapping.json"
NOTICE_NAME = "SENTETIK_backtest_OKUBENI.txt"

# --- The prospect's column names ---------------------------------------------
# Not all of them: renaming twenty feature columns would make the file unreadable
# while proving nothing the five below do not. These five cover each shape the
# mapping has to handle - the id, the as-of date, the churn flag, the churn date,
# and plain feature columns.
HISTORY_COLUMN_NAMES = {
    "student_id": "ogrenci_no",
    "as_of_date": "gozlem_tarihi",
    "program_adherence_rate": "program_uyum_orani",
    "satisfaction_survey_score": "memnuniyet_puani",
    "monthly_fee_try": "plan_ucreti_try",
}
OUTCOME_COLUMN_NAMES = {
    "student_id": "ogrenci_no",
    "churned": "ayrildi_mi",
    "churn_date": "ayrilma_tarihi",
}
CLIENT_DATE_FORMAT = "%d.%m.%Y"
CHURN_TRUE_VALUE = "Evet"
CHURN_FALSE_VALUE = "Hayir"

# --- Generation defaults ------------------------------------------------------
# One observation per student per fortnight. Real exports are usually weekly or
# monthly; a fortnight sits between the two and keeps the file to ~60k rows.
DEFAULT_OBSERVATION_STEP_DAYS = 14
DEFAULT_MONTHS = 12
# How long before a churner leaves their features start drifting. Also the ceiling
# on any lead time this data can show, so it is stated in the output files.
DEFAULT_DRIFT_DAYS = 90
# Per-observation noise, as a fraction of the column's own standard deviation.
DEFAULT_NOISE_FRACTION = 0.10
DEFAULT_SEED = 42

# Columns that are a clock rather than a behaviour: {column: change per day}.
# Interpolating these like a behavioural feature produces a student whose time in
# the programme decreases, which no reader of the file would trust again.
CLOCK_COLUMNS = {
    "days_to_next_exam": -1.0,
}

# `tenure_months` is a clock too, but it is not computed by running the snapshot
# value backwards: the snapshot's own `tenure_months` and `enrollment_date` imply a
# different as-of date for every student (enrolled 2026-05-22 with tenure 3.1 means
# late August, not the end of the period), so running it back from one shared
# reference date drives it negative for recently-enrolled students. Computed from
# the enrolment date instead, which is internally consistent by construction.
TENURE_COLUMN = "tenure_months"
DAYS_PER_MONTH = 30.44

# Snapshot columns that never move inside the period.
STATIC_COLUMNS = list(CAT_COLS) + ["monthly_fee_try"]

# Not carried into the history: a cohort identifier the audit deliberately keeps
# out of the model (docs/LEAKAGE_AUDIT.md), and it is what decides when a student
# first appears, which is a property of the history rather than a column in it.
ENROLMENT_COLUMN = "enrollment_date"


class SnapshotError(Exception):
    """The snapshot cannot be turned into a history (missing column, no churners)."""


def drifting_columns(snapshot: pd.DataFrame) -> list[str]:
    """Numeric snapshot columns that are interpolated towards the churn moment.

    Everything numeric that is neither a clock, nor static, nor produced by feature
    engineering. `AUDITED_OUT_FEATURES` are INCLUDED on purpose: a real client's
    export carries them, so this one does too, and the backtest ignoring them is
    then a thing that can be seen happening rather than asserted.
    """
    candidates = [f for f in FEATURES if f not in FLAG_FEATURES] + list(
        AUDITED_OUT_FEATURES
    )
    return [
        column
        for column in dict.fromkeys(candidates)
        if column in snapshot.columns
        and column not in CLOCK_COLUMNS
        and column not in STATIC_COLUMNS
        and column != TENURE_COLUMN
    ]


def _clip_and_round(values: np.ndarray, column: str) -> np.ndarray:
    """Keep a generated column inside config.FEATURE_BOUNDS and in its own dtype.

    Without this the noise walks `program_adherence_rate` past 1.0 and
    `satisfaction_survey_score` past 5, and the file would then be rejected by our
    own API bounds - i.e. the demo data would not be data the product accepts.
    """
    bounds = FEATURE_BOUNDS.get(column)
    if bounds is not None:
        values = np.clip(values, bounds[0], bounds[1])
    if column in INTEGER_FEATURES:
        return np.round(values)
    return np.round(values, 2)


def assign_churn_dates(
    snapshot: pd.DataFrame,
    *,
    observed_from: pd.Series,
    period_end: date,
    observation_step_days: int,
    rng: np.random.Generator,
) -> tuple[pd.Series, int]:
    """Give every snapshot churner a churn date inside the period.

    A churner needs at least two observations before they leave, otherwise there is
    nothing for a walk-forward backtest to have scored. A student who enrolled too
    late for that is DEMOTED to a non-churner and counted - the count is returned
    and printed, because silently dropping part of the positive class would change
    the base rate of the demo data without saying so.
    """
    earliest = observed_from + pd.to_timedelta(2 * observation_step_days, unit="D")
    latest = pd.Timestamp(period_end)
    room_days = (latest - earliest).dt.days

    is_churner = snapshot[TARGET_FEATURE].astype(int) == 1
    has_room = room_days >= 0
    demoted = int((is_churner & ~has_room).sum())

    churn_date = pd.Series(pd.NaT, index=snapshot.index, dtype="datetime64[ns]")
    eligible = is_churner & has_room
    offsets = rng.integers(0, room_days.where(eligible, 0).astype(int) + 1)
    churn_date[eligible] = earliest[eligible] + pd.to_timedelta(
        offsets[eligible.to_numpy()], unit="D"
    )
    return churn_date, demoted


def observation_dates(
    first: pd.Timestamp,
    last: pd.Timestamp,
    *,
    step_days: int,
    jitter: int,
) -> list[pd.Timestamp]:
    """A student's observation dates, built BACKWARDS from their last one.

    Backwards because the interesting end is the recent end: a churner must have an
    observation shortly before they leave, or the backtest has nothing to score
    them on. `jitter` moves each student's grid off everyone else's, so the file is
    not one global grid of dates - which is both more realistic and the only way
    the backtest's "most recent row within the active window" logic gets exercised.
    """
    cursor = last - pd.Timedelta(days=int(jitter))
    dates = []
    while cursor >= first:
        dates.append(cursor)
        cursor = cursor - pd.Timedelta(days=step_days)
    return list(reversed(dates))


def build_history(
    snapshot: pd.DataFrame,
    *,
    period_start: date,
    period_end: date,
    observation_step_days: int,
    drift_days: int,
    noise_fraction: float,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (history, outcomes, stats). Both frames use OUR column names.

    The rename to the prospect's names happens in `write_files`, so everything
    above this line stays readable against config.FEATURES.
    """
    if TARGET_FEATURE not in snapshot.columns:
        raise SnapshotError(
            f"the snapshot has no {TARGET_FEATURE!r} column, so there is nothing to "
            f"turn into churn dates. Columns present: {list(snapshot.columns)}"
        )
    if ENROLMENT_COLUMN not in snapshot.columns:
        raise SnapshotError(
            f"the snapshot has no {ENROLMENT_COLUMN!r} column, so there is no way to "
            "decide when each student first appears in the history."
        )
    if int(snapshot[TARGET_FEATURE].sum()) == 0:
        raise SnapshotError(
            "the snapshot has no churners at all, so the generated history would "
            "have no outcomes to measure a backtest against."
        )

    rng = np.random.default_rng(seed)
    snapshot = snapshot.reset_index(drop=True)

    enrolled = pd.to_datetime(snapshot[ENROLMENT_COLUMN], errors="coerce")
    observed_from = enrolled.clip(lower=pd.Timestamp(period_start)).fillna(
        pd.Timestamp(period_start)
    )

    churn_date, demoted = assign_churn_dates(
        snapshot,
        observed_from=observed_from,
        period_end=period_end,
        observation_step_days=observation_step_days,
        rng=rng,
    )

    drift = drifting_columns(snapshot)
    # The value a churner is interpolated BACK towards: the non-churner mean of the
    # same column. Using the non-churner cohort rather than the overall mean is what
    # makes the drift point the right way for every column at once, without this
    # script needing an opinion per feature about which direction is "risky".
    healthy = snapshot[snapshot[TARGET_FEATURE].astype(int) == 0]
    baseline = {column: float(healthy[column].mean()) for column in drift}
    spread = {column: float(snapshot[column].std(ddof=0)) for column in drift}

    jitters = rng.integers(1, observation_step_days + 1, size=len(snapshot))
    rows: list[dict] = []
    for position, student in snapshot.iterrows():
        reference = (
            churn_date.iloc[position]
            if pd.notna(churn_date.iloc[position])
            else pd.Timestamp(period_end)
        )
        # Strictly before the churn date for a churner: a row dated on or after the
        # day a student left is a row about someone who is no longer there.
        last_observation = min(reference, pd.Timestamp(period_end))
        dates = observation_dates(
            observed_from.iloc[position],
            last_observation,
            step_days=observation_step_days,
            jitter=int(jitters[position]),
        )
        if not dates:
            continue

        churned = pd.notna(churn_date.iloc[position])
        offsets = np.array([(reference - d).days for d in dates], dtype=float)
        if churned:
            # 1.0 on the churn date, 0.0 `drift_days` or more before it.
            progress = np.clip(1.0 - offsets / float(drift_days), 0.0, 1.0)
        else:
            progress = np.ones(len(dates))

        generated: dict[str, np.ndarray] = {}
        for column in drift:
            target = student[column]
            if pd.isna(target):
                # A null in the snapshot stays null in every row, so the backtest's
                # missing-flag and imputation path is actually taken.
                generated[column] = np.full(len(dates), np.nan)
                continue
            start = baseline[column] if churned else float(target)
            series = start + (float(target) - start) * progress
            noise = rng.normal(0.0, noise_fraction * spread[column], size=len(dates))
            generated[column] = _clip_and_round(series + noise, column)

        for column, per_day in CLOCK_COLUMNS.items():
            if column not in snapshot.columns or pd.isna(student[column]):
                continue
            generated[column] = _clip_and_round(
                float(student[column]) - per_day * offsets, column
            )

        if TENURE_COLUMN in snapshot.columns:
            enrolment = enrolled.iloc[position]
            if pd.notna(enrolment):
                elapsed = np.array([(d - enrolment).days for d in dates], dtype=float)
            else:
                elapsed = (
                    float(student[TENURE_COLUMN]) * DAYS_PER_MONTH - offsets
                    if pd.notna(student[TENURE_COLUMN])
                    else np.full(len(dates), np.nan)
                )
            generated[TENURE_COLUMN] = _clip_and_round(
                elapsed / DAYS_PER_MONTH, TENURE_COLUMN
            )

        for index, as_of in enumerate(dates):
            row = {
                "student_id": student["student_id"],
                "as_of_date": as_of.date(),
            }
            for column in STATIC_COLUMNS:
                if column in snapshot.columns:
                    row[column] = student[column]
            for column, values in generated.items():
                row[column] = values[index]
            rows.append(row)

    history = pd.DataFrame(rows)
    outcomes = pd.DataFrame(
        {
            "student_id": snapshot["student_id"],
            "churned": np.where(churn_date.notna(), CHURN_TRUE_VALUE, CHURN_FALSE_VALUE),
            "churn_date": [
                d.date() if pd.notna(d) else None for d in churn_date
            ],
        }
    )

    stats = {
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "observation_step_days": observation_step_days,
        "drift_days": drift_days,
        "noise_fraction": noise_fraction,
        "seed": seed,
        "students": int(len(snapshot)),
        "students_in_history": int(history["student_id"].nunique()),
        "history_rows": int(len(history)),
        "churners": int(churn_date.notna().sum()),
        "churners_demoted_no_room": demoted,
        "drifting_columns": drift,
        "static_columns": [c for c in STATIC_COLUMNS if c in snapshot.columns],
        "clock_columns": [
            c
            for c in list(CLOCK_COLUMNS) + [TENURE_COLUMN]
            if c in snapshot.columns
        ],
    }
    return history, outcomes, stats


# --- Writing -----------------------------------------------------------------
_MARKER_LINE = (
    "SENTETIK VERI - GERCEK MUSTERI VERISI DEGIL. "
    "scripts/make_synthetic_history.py tarafindan uretildi."
)


def _stamp_marker(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame[SYNTHETIC_MARKER_COLUMN] = SYNTHETIC_MARKER_VALUE
    return frame


def _restore_integer_dtypes(frame: pd.DataFrame) -> pd.DataFrame:
    """Write `config.INTEGER_FEATURES` as whole numbers, not as 3.0.

    Nullable Int64 rather than int: these columns may carry a null the generator
    preserved from the snapshot, and a counter written as "3.0" is the kind of
    detail that makes a prospect's engineer wonder what else we rounded.
    """
    frame = frame.copy()
    for column in INTEGER_FEATURES:
        if column in frame.columns:
            frame[column] = frame[column].round().astype("Int64")
    return frame


def _format_dates(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    """Write dates in the prospect's format, so the mapping's `date_format` matters."""
    frame = frame.copy()
    for column in columns:
        frame[column] = pd.to_datetime(frame[column]).dt.strftime(CLIENT_DATE_FORMAT)
        frame[column] = frame[column].where(frame[column].notna(), "")
    return frame


def write_files(
    history: pd.DataFrame,
    outcomes: pd.DataFrame,
    stats: dict,
    out_dir: Path,
) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)

    history_out = _stamp_marker(
        _restore_integer_dtypes(_format_dates(history, ["as_of_date"]))
    ).rename(columns=HISTORY_COLUMN_NAMES)
    outcomes_out = _stamp_marker(_format_dates(outcomes, ["churn_date"])).rename(
        columns=OUTCOME_COLUMN_NAMES
    )

    paths = {
        "history": out_dir / HISTORY_NAME,
        "outcomes": out_dir / OUTCOMES_NAME,
        "mapping": out_dir / MAPPING_NAME,
        "notice": out_dir / NOTICE_NAME,
    }
    history_out.to_csv(paths["history"], index=False)
    outcomes_out.to_csv(paths["outcomes"], index=False)

    mapping = {
        "_notice": _MARKER_LINE,
        "_direction": "left = the prospect's column name, right = ours",
        "date_format": CLIENT_DATE_FORMAT,
        "churn_true_values": [CHURN_TRUE_VALUE],
        "churn_false_values": [CHURN_FALSE_VALUE],
        "history": {
            their: ours for ours, their in HISTORY_COLUMN_NAMES.items()
        },
        "outcomes": {
            their: ours for ours, their in OUTCOME_COLUMN_NAMES.items()
        },
    }
    with open(paths["mapping"], "w", encoding="utf-8") as f:
        json.dump(mapping, f, indent=2, ensure_ascii=False)
        f.write("\n")

    with open(paths["notice"], "w", encoding="utf-8") as f:
        f.write(_notice_text(stats, paths))

    return paths


def _notice_text(stats: dict, paths: dict[str, Path]) -> str:
    return f"""{'=' * 78}
{_MARKER_LINE}
{'=' * 78}

Bu klasordeki SENTETIK_backtest_* dosyalari GERCEK MUSTERI VERISI DEGILDIR.
data/mentorluk_churn_veriseti.csv (tek anlik goruntu, kendisi de sentetik)
uzerinden uretilmis yapay bir 12 aylik gecmistir.

Ne ise yarar:
  scripts/backtest.py'nin gercek musteri verisi gelmeden once calistirilabilmesi,
  test edilebilmesi ve gosterilebilmesi.

Ne ise YARAMAZ:
  Dogrulukla ilgili HICBIR sey. Churn edecek ogrencilerin feature'lari,
  ayrilmalarindan {stats['drift_days']} gun once bu script tarafindan bilerek
  kaydirildi. Modelin bu kaymayi bulmasi bir kanit degil, aritmetiktir.
  Bu dosyalardan cikan hicbir sayi musteriye/yatirimciya gosterilemez.

Uretim parametreleri:
  donem               : {stats['period_start']} - {stats['period_end']}
  gozlem sikligi      : {stats['observation_step_days']} gun
  kayma suresi        : {stats['drift_days']} gun (lead time icin ust sinir)
  gurultu             : kolon std'sinin {stats['noise_fraction']:.0%}'i
  seed                : {stats['seed']}
  ogrenci             : {stats['students']} ({stats['students_in_history']} tanesi gecmiste)
  satir               : {stats['history_rows']}
  churn eden          : {stats['churners']}
  yer olmadigi icin churn'den dusurulen: {stats['churners_demoted_no_room']}

Dosyalar:
  {paths['history'].name}   gecmis  (sutun adlari MUSTERI adlari, tarih {CLIENT_DATE_FORMAT})
  {paths['outcomes'].name}  sonuclar
  {paths['mapping'].name}   eslestirme (sol: musterinin adi, sag: bizim adimiz)

Calistirmak icin:
  python scripts/backtest.py \\
      --history {paths['history']} \\
      --outcomes {paths['outcomes']} \\
      --mapping {paths['mapping']}

Her iki CSV'nin her satirinda `{SYNTHETIC_MARKER_COLUMN}` kolonu
`{SYNTHETIC_MARKER_VALUE}` degerini tasir. scripts/backtest.py bu kolonu okur ve
hicbir sayiyi SENTETIK uyarisi olmadan yazdirmaz.
"""


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "SYNTHETIC: build a fake multi-month history + outcomes from the single "
            "snapshot in data/, so scripts/backtest.py can be run before any real "
            "customer export exists."
        ),
    )
    parser.add_argument("--snapshot", default=RAW_DATA_PATH, help="input snapshot CSV")
    parser.add_argument(
        "--out-dir", default=str(Path(BASE_DIR) / "data"), help="where to write"
    )
    parser.add_argument(
        "--end-date",
        default=date.today().isoformat(),
        help="last day of the generated period (YYYY-MM-DD, default: today)",
    )
    parser.add_argument("--months", type=int, default=DEFAULT_MONTHS)
    parser.add_argument(
        "--observation-step-days", type=int, default=DEFAULT_OBSERVATION_STEP_DAYS
    )
    parser.add_argument(
        "--drift-days",
        type=int,
        default=DEFAULT_DRIFT_DAYS,
        help="how long before leaving a churner's features start drifting",
    )
    parser.add_argument("--noise-fraction", type=float, default=DEFAULT_NOISE_FRACTION)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    configure_logging()
    args = parse_args(argv)

    if args.months <= 0 or args.observation_step_days <= 0 or args.drift_days <= 0:
        raise SystemExit(
            "--months, --observation-step-days and --drift-days must all be positive"
        )

    period_end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    period_start = period_end - timedelta(days=round(args.months * 30.44))

    snapshot = data_loader(args.snapshot)
    history, outcomes, stats = build_history(
        snapshot,
        period_start=period_start,
        period_end=period_end,
        observation_step_days=args.observation_step_days,
        drift_days=args.drift_days,
        noise_fraction=args.noise_fraction,
        seed=args.seed,
    )
    stats["snapshot"] = str(args.snapshot)
    paths = write_files(history, outcomes, stats, Path(args.out_dir))

    print("\n" + "!" * 78)
    print(_MARKER_LINE)
    print("!" * 78)
    print(
        f"\ndonem         : {stats['period_start']} - {stats['period_end']}\n"
        f"ogrenci       : {stats['students_in_history']} / {stats['students']}\n"
        f"satir         : {stats['history_rows']}\n"
        f"churn eden    : {stats['churners']}"
        f" (yer olmadigi icin dusurulen: {stats['churners_demoted_no_room']})\n"
        f"kayma suresi  : {stats['drift_days']} gun"
        " <- bu veride lead time'in ust siniri\n"
    )
    for name, path in paths.items():
        print(f"{name:<9}: {path}")
    print(
        "\nBu dosyalardan cikan hicbir sayi gercek degildir. Calistirmak icin:\n"
        f"  python scripts/backtest.py --history {paths['history']} "
        f"--outcomes {paths['outcomes']} --mapping {paths['mapping']}\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
