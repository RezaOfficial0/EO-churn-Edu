"""Retrospective backtest: what would EO-Churn have caught on YOUR last 12 months?

    python scripts/backtest.py --history h.csv --outcomes o.csv --mapping m.json
    python scripts/backtest.py --contract          # print the input contract and exit

Why this exists
---------------
A prospect commits to nothing: no integration, no deployment, no access to their
systems. They send two anonymised CSVs. This script turns those two files into the
only evidence that answers the question an investor asked and the demo could not:
**which students did you actually lose that we would have flagged, and how many
days earlier?**

So this is the sales motion, not a dev tool, and the thing it must be is *honest*.
A flattering number that collapses in production costs more than no number: the
prospect's own analyst will re-run the comparison, and `docs/LEAKAGE_AUDIT.md` is
the house record of how easily a churn metric flatters itself.

How it differs from pipeline/training_pipeline.py, and why it has to
--------------------------------------------------------------------
The training pipeline trains and evaluates on ONE snapshot with a random stratified
split. Every row in that split is contemporaneous, so a row from "later" can train
a model that scores a row from "earlier". For a backtest that is fatal: the whole
claim being measured is *we would have known this in advance*.

This script therefore walks forward through time, and at each evaluation point it
may only use information that already existed:

    origin T, churn window W

      training rows   as_of_date <= T - W       (their outcome had ALREADY been
                                                 observed by T; this is an embargo,
                                                 not just a cut-off)
      calibration     the latest dates inside that training slice
      threshold       chosen on that same calibration slice
      imputation      medians fitted on that training slice only
      scored at T     each active student's most recent row with as_of_date <= T
      outcome         did they churn in (T, T + W]?

The embargo is the part that is easy to get wrong. Filtering `as_of_date <= T` is
not enough: a row dated T - 5 has a churn window that is still open at T, so its
label is not knowable at T, and training on it leaks the future backwards. Hence
`T - W`.

Where the guarantee is enforced
-------------------------------
`select_training_rows()` applies the embargo and `_assert_only_past()` re-checks
it. `run_fold()` calls `_assert_only_past()` again on the exact frame it is about
to hand to `model.fit`, and on the scoring frame against its origin. A violation
raises `TemporalLeakageError` and kills the run - it is never a warning. The check
is cheap and it is the single assumption the entire report rests on, so it is
asserted on the data rather than trusted from the call site.
tests/test_backtest.py builds a history in which leakage would show up as an
impossibly good score, and fails if that score appears.

What it will not claim
----------------------
Nobody intervened in a backtest. The money section reports value **at risk and
visible**, never value saved, and the Turkish summary says so in the same breath
as the figure. Every headline carries a bootstrap interval, and a figure measured
on fewer than `config.BACKTEST_MIN_REPORTABLE_N` students is published with
`reportable: false` and described in words as noise. The baseline comparison can
embarrass the model, and when it does the summary says the model lost - a backtest
that cannot produce a bad result is not evidence of anything.

Output
------
A JSON file with everything (the material a customer report is generated from
later - this script does not write the report) and a readable summary on the
terminal in Turkish. Code, identifiers, log lines and error messages stay English,
in line with the rest of the repo.
"""
import argparse
import hashlib
import json
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import (  # noqa: E402
    AUDITED_OUT_FEATURES,
    BACKTEST_ACTIVE_WINDOW_DAYS,
    BACKTEST_BOOTSTRAP_RESAMPLES,
    BACKTEST_MIN_FEATURES,
    BACKTEST_MIN_OUTCOME_COVERAGE,
    BACKTEST_MIN_REPORTABLE_N,
    BACKTEST_MIN_TRAIN_CHURNERS,
    BACKTEST_MIN_TRAIN_ROWS,
    BACKTEST_OUTPUT_DIR,
    BACKTEST_STEP_DAYS,
    BACKTEST_VAL_DATE_FRACTION,
    CAT_COLS,
    CHURN_WINDOW_DAYS,
    DECISION_COST,
    FEATURE_LABELS,
    FEATURES,
    FLAG_FEATURES,
    PLAN_MONTHS,
    PRECISION_AT_K,
    SYNTHETIC_MARKER_COLUMN,
    SYNTHETIC_MARKER_VALUE,
)
from src.logging_setup import configure_logging  # noqa: E402
from src.model.calibrate import (  # noqa: E402
    DegenerateCalibrationError,
    churn_proba,
    fit_calibrator,
)
from src.model.evaluate import lift_at_k, precision_at_k  # noqa: E402
from src.model.model import build_model  # noqa: E402
from src.model.threshold import select_threshold  # noqa: E402
from src.model.train import train as fit_model  # noqa: E402
from src.serialization import to_external  # noqa: E402

logger = logging.getLogger(__name__)


class BacktestInputError(Exception):
    """The two CSVs or the mapping cannot be used, and the message says exactly why.

    Every message names the file, the column, the problem and the fix. Whoever runs
    this is reading it at 23:00 with a prospect waiting for an answer, and "KeyError:
    'as_of_date'" costs that person an hour they do not have.
    """


class TemporalLeakageError(RuntimeError):
    """A row that could not have been known at the evaluation point reached the model.

    Never downgraded to a warning. A backtest whose time ordering is broken produces
    a BETTER number than a correct one, so the failure mode is a great result shown
    to a customer and then contradicted by their own analyst.
    """


# --- The input contract ------------------------------------------------------
# Two CSVs and a mapping. Required columns are kept to the absolute minimum: every
# required column is a reason for a prospect to give up, and a prospect who gives
# up is worth less than a backtest run on nine features instead of twenty.
HISTORY_REQUIRED = ["student_id", "as_of_date"]
OUTCOMES_REQUIRED = ["student_id", "churn_date"]
OUTCOMES_OPTIONAL = ["churned"]

# Either of these makes the money section possible; neither is required.
# `monthly_value_try` is used directly, `monthly_fee_try` (+ `plan_type`) is turned
# into it with config.PLAN_MONTHS, exactly as src/data/features.py does.
MONEY_COLUMNS = ["monthly_value_try", "monthly_fee_try"]

# Columns this script COMPUTES. Mapping one of them is refused rather than
# silently overwritten: a client column called "satisfaction_missing" means
# something to the client and nothing to us.
DERIVED_NEVER_MAPPED = list(FLAG_FEATURES)

# Everything a history file may usefully carry. A column outside this list is
# ignored (and listed in the report), because a prospect's export has thirty
# columns we have no use for and refusing it would be absurd.
HISTORY_KNOWN_TARGETS = list(
    dict.fromkeys(
        HISTORY_REQUIRED
        + [f for f in FEATURES if f not in DERIVED_NEVER_MAPPED]
        + MONEY_COLUMNS
        + ["plan_type"]
        + list(AUDITED_OUT_FEATURES)
    )
)
OUTCOMES_KNOWN_TARGETS = OUTCOMES_REQUIRED + OUTCOMES_OPTIONAL

# The feature columns a model could actually be trained on, if supplied.
OPTIONAL_FEATURE_COLUMNS = [
    f
    for f in FEATURES
    if f not in DERIVED_NEVER_MAPPED and f not in AUDITED_OUT_FEATURES
]

DEFAULT_CHURN_TRUE_VALUES = ["1", "true", "yes", "y", "evet", "e", "churned", "ayrildi"]
DEFAULT_CHURN_FALSE_VALUES = ["0", "false", "no", "n", "hayir", "hayır", "h", "active", "aktif"]

MAPPING_KEYS = {
    "history",
    "outcomes",
    "date_format",
    "churn_true_values",
    "churn_false_values",
}

# Stand-in for a missing categorical value. CatBoost will not take NaN in a
# categorical column, and "unknown" is itself information worth keeping as a level
# rather than imputing away.
CATEGORICAL_MISSING = "__missing__"

# How many offending values an error message lists. Enough to see the pattern
# ("every date is dd.mm.yyyy"), few enough to read in a terminal.
_MAX_OFFENDERS_SHOWN = 5


CONTRACT = f"""
EO-Churn retrospective backtest - input contract
================================================

Three files. Nothing else, no integration, no access to your systems.

1) HISTORY CSV   --history
   One row per (student, observation date). The feature values in a row must be the
   values as they stood ON THAT DATE - that is the one thing we cannot check for you
   and the one thing that makes the result mean anything.

   REQUIRED (2):
     student_id    a stable, anonymous id. Any string. Must match the outcomes file.
     as_of_date    the date that row describes.

   OPTIONAL - every one of these improves the model, none of them blocks the run:
     {", ".join(OPTIONAL_FEATURE_COLUMNS)}
   OPTIONAL - needed only for the money section:
     monthly_value_try, or monthly_fee_try together with plan_type
   At least {BACKTEST_MIN_FEATURES} of the optional feature columns must be present,
   or there is nothing to train on.

   Columns we recognise but do NOT use (dropped - see docs/LEAKAGE_AUDIT.md):
     {", ".join(AUDITED_OUT_FEATURES)}
   Any other column is ignored and listed in the report.

2) OUTCOMES CSV   --outcomes
   One row per student. Students missing from this file are treated as never churned.

   REQUIRED (2):
     student_id    same ids as the history file.
     churn_date    the date they left. Empty for a student who did not leave.
   OPTIONAL:
     churned       a flag. When present and true, churn_date must be filled in.

3) MAPPING JSON   --mapping
   Your column names on the LEFT, ours on the RIGHT.

     {{
       "history":  {{ "ogrenci_no": "student_id", "gozlem_tarihi": "as_of_date" }},
       "outcomes": {{ "ogrenci_no": "student_id", "ayrilma_tarihi": "churn_date" }},
       "date_format": "%d.%m.%Y",
       "churn_true_values": ["Evet"],
       "churn_false_values": ["Hayir"]
     }}

   "history" and "outcomes" are both optional - leave a section out if your column
   is already called what we call it. "date_format" is optional; without it dates
   are read as ISO (YYYY-MM-DD) with a fallback to pandas' own parsing. Keys
   starting with "_" are treated as comments.
"""


# --- Mapping and loading -----------------------------------------------------
def load_mapping(path: str | Path) -> dict:
    """Read and check the mapping JSON. Every failure names the fix."""
    path = Path(path)
    if not path.exists():
        raise BacktestInputError(
            f"mapping file not found: {path}. Create it as "
            '{"history": {"<their column>": "<our column>"}, "outcomes": {...}} '
            "- run `python scripts/backtest.py --contract` for the full contract."
        )
    try:
        with open(path, encoding="utf-8") as f:
            mapping = json.load(f)
    except json.JSONDecodeError as e:
        raise BacktestInputError(
            f"mapping file {path} is not valid JSON: {e.msg} at line {e.lineno} "
            f"column {e.colno}. A trailing comma and a missing quote are the two "
            "usual causes."
        ) from e

    if not isinstance(mapping, dict):
        raise BacktestInputError(
            f"mapping file {path} must contain a JSON object, got "
            f"{type(mapping).__name__}. Run `python scripts/backtest.py --contract`."
        )

    unknown = sorted(
        key for key in mapping if not key.startswith("_") and key not in MAPPING_KEYS
    )
    if unknown:
        raise BacktestInputError(
            f"mapping file {path} has unknown key(s): {unknown}. Known keys: "
            f"{sorted(MAPPING_KEYS)}. A key starting with '_' is treated as a "
            "comment; anything else is a typo and would be silently ignored."
        )

    for section in ("history", "outcomes"):
        value = mapping.get(section, {})
        if not isinstance(value, dict):
            raise BacktestInputError(
                f'mapping file {path}: "{section}" must be an object mapping THEIR '
                f"column name to OUR column name, got {type(value).__name__}."
            )
        for their, ours in value.items():
            if not isinstance(ours, str):
                raise BacktestInputError(
                    f'mapping file {path}, "{section}": "{their}" is mapped to '
                    f"{ours!r}, which is not a column name."
                )
    return mapping


def _check_targets(
    renames: dict, *, section: str, mapping_path: Path, known: list[str]
) -> None:
    """Refuse a mapping that points at a name this backtest does not know."""
    for their, ours in renames.items():
        if ours in DERIVED_NEVER_MAPPED:
            raise BacktestInputError(
                f'mapping file {mapping_path}, "{section}": "{their}" is mapped to '
                f'"{ours}", which this backtest COMPUTES from your data. Remove the '
                "entry - mapping it would overwrite the computed column."
            )
        if ours not in known:
            raise BacktestInputError(
                f'mapping file {mapping_path}, "{section}": "{their}" is mapped to '
                f'"{ours}", which is not a column this backtest knows. Known '
                f"{section} targets: {known}. Remove the entry or correct the "
                "spelling on the RIGHT of the mapping."
            )

    collisions = {}
    for their, ours in renames.items():
        collisions.setdefault(ours, []).append(their)
    for ours, theirs in collisions.items():
        if len(theirs) > 1:
            raise BacktestInputError(
                f'mapping file {mapping_path}, "{section}": "{ours}" is the target of '
                f"{len(theirs)} columns: {sorted(theirs)}. Each of our columns may be "
                "mapped from at most one of yours."
            )


def _read_csv(path: str | Path, *, file_label: str) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise BacktestInputError(f"{file_label} file not found: {path}")
    try:
        frame = pd.read_csv(path, dtype={}, keep_default_na=True)
    except pd.errors.EmptyDataError as e:
        raise BacktestInputError(
            f"{file_label} file {path} is empty - it does not even have a header row."
        ) from e
    except pd.errors.ParserError as e:
        raise BacktestInputError(
            f"{file_label} file {path} could not be parsed as CSV: {e}. Check the "
            "delimiter and that no row has more fields than the header."
        ) from e
    if frame.empty:
        raise BacktestInputError(
            f"{file_label} file {path} has a header but no data rows."
        )
    return frame


def apply_mapping(
    frame: pd.DataFrame,
    renames: dict,
    *,
    file_label: str,
    file_path: Path,
    mapping_path: Path,
    section: str,
    known: list[str],
    required: list[str],
) -> pd.DataFrame:
    """Rename their columns to ours and check that the required ones are there."""
    _check_targets(renames, section=section, mapping_path=mapping_path, known=known)

    absent = [their for their in renames if their not in frame.columns]
    if absent:
        raise BacktestInputError(
            f'mapping file {mapping_path}, "{section}": '
            f"{sorted(absent)} is mapped but is not a column in {file_path}. That "
            f"file's columns are: {list(frame.columns)}. Correct the name on the "
            "LEFT of the mapping - the left side is YOUR column name, not ours."
        )

    # A rename onto a name the file already uses for a different column would make
    # two columns with one name, and pandas keeps both.
    clashes = [
        ours
        for their, ours in renames.items()
        if ours in frame.columns and ours != their
    ]
    if clashes:
        raise BacktestInputError(
            f'mapping file {mapping_path}, "{section}": the mapping renames a column '
            f"to {sorted(clashes)}, but {file_path} already has a column with that "
            "name. Rename or drop one of them in the CSV - two columns with the same "
            "name cannot be told apart."
        )

    frame = frame.rename(columns=renames)

    missing = [column for column in required if column not in frame.columns]
    if missing:
        hints = ", ".join(f'"<your column>": "{column}"' for column in missing)
        raise BacktestInputError(
            f"{file_label} file {file_path} is missing required column(s): {missing}. "
            f"Either rename the column in the CSV, or add {hints} to \"{section}\" in "
            f"{mapping_path}. Required: {required}. The file's columns are: "
            f"{list(frame.columns)}."
        )
    return frame


def parse_date_column(
    frame: pd.DataFrame,
    column: str,
    *,
    file_label: str,
    file_path: Path,
    mapping_path: Path,
    date_format: str | None,
    allow_blank: bool,
) -> pd.Series:
    """Parse one date column, or raise naming the rows that could not be read.

    Blank is legitimate in `outcomes.churn_date` (a student who did not leave) and
    never in `history.as_of_date` (a row with no date cannot be placed in time), so
    the caller says which it is instead of this function guessing.
    """
    raw = frame[column]
    blank = raw.isna() | (raw.astype(str).str.strip() == "")
    if date_format:
        parsed = pd.to_datetime(raw, format=date_format, errors="coerce")
    else:
        parsed = pd.to_datetime(raw, errors="coerce", format="ISO8601")
        if parsed.isna().any():
            # Second pass for the rows ISO8601 refused - a mixed file (some ISO,
            # some dd/mm/yyyy) is common and worth reading rather than rejecting.
            fallback = pd.to_datetime(raw, errors="coerce")
            parsed = parsed.fillna(fallback)

    bad = parsed.isna() & ~blank
    if bad.any():
        offenders = [
            f"row {int(position) + 1} {raw.iloc[position]!r}"
            for position in np.flatnonzero(bad.to_numpy())[:_MAX_OFFENDERS_SHOWN]
        ]
        stated = (
            f'the mapping states "date_format": "{date_format}"'
            if date_format
            else "no \"date_format\" was given, so ISO (YYYY-MM-DD) was assumed"
        )
        raise BacktestInputError(
            f'{file_label} file {file_path}, column "{column}": {int(bad.sum())} of '
            f"{len(raw)} value(s) could not be read as a date ({stated}). First "
            f"offenders, row numbers counting data rows from 1: {', '.join(offenders)}. "
            f'Use ISO YYYY-MM-DD, or state the format with "date_format": "%d.%m.%Y" '
            f"in {mapping_path}."
        )

    if blank.any() and not allow_blank:
        rows = [
            f"row {int(position) + 1}"
            for position in np.flatnonzero(blank.to_numpy())[:_MAX_OFFENDERS_SHOWN]
        ]
        raise BacktestInputError(
            f'{file_label} file {file_path}, column "{column}": {int(blank.sum())} '
            f"row(s) have no date at all (first: {', '.join(rows)}). A history row "
            "with no as-of date cannot be placed in time, so it cannot be used. Fill "
            "the date in or drop those rows."
        )
    return parsed


def _truthiness(
    values: pd.Series,
    *,
    true_values: list[str],
    false_values: list[str],
    file_label: str,
    file_path: Path,
    mapping_path: Path,
    column: str,
) -> pd.Series:
    """Map a client's churn flag onto a boolean, refusing anything ambiguous."""
    text = values.astype(str).str.strip().str.lower()
    blank = values.isna() | (text == "") | (text == "nan")
    true_set = {str(v).strip().lower() for v in true_values}
    false_set = {str(v).strip().lower() for v in false_values}

    unknown = sorted(set(text[~blank]) - true_set - false_set)
    if unknown:
        raise BacktestInputError(
            f'{file_label} file {file_path}, column "{column}": value(s) '
            f"{unknown[:_MAX_OFFENDERS_SHOWN]} are neither true nor false. Accepted "
            f"as true: {sorted(true_set)}; as false: {sorted(false_set)}. Add the "
            f'value to "churn_true_values" or "churn_false_values" in {mapping_path}, '
            "or correct the data."
        )
    return text.isin(true_set) & ~blank


def read_history(
    path: str | Path, mapping: dict, mapping_path: Path
) -> tuple[pd.DataFrame, dict]:
    """Load the history CSV into our column names, with `as_of_date` as a Timestamp."""
    path = Path(path)
    raw = _read_csv(path, file_label="history")
    supplied_columns = list(raw.columns)

    frame = apply_mapping(
        raw,
        mapping.get("history", {}),
        file_label="history",
        file_path=path,
        mapping_path=mapping_path,
        section="history",
        known=HISTORY_KNOWN_TARGETS,
        required=HISTORY_REQUIRED,
    )
    frame["as_of_date"] = parse_date_column(
        frame,
        "as_of_date",
        file_label="history",
        file_path=path,
        mapping_path=mapping_path,
        date_format=mapping.get("date_format"),
        allow_blank=False,
    )
    frame["student_id"] = frame["student_id"].astype(str).str.strip()
    if (frame["student_id"] == "").any():
        raise BacktestInputError(
            f'history file {path}, column "student_id": '
            f"{int((frame['student_id'] == '').sum())} row(s) have an empty id. An "
            "unidentified row cannot be joined to an outcome, so it cannot be used."
        )

    # A student with two rows on one date is ambiguous: which one was true that day?
    # Keeping the last is a guess, so it is reported rather than made silently.
    duplicated = int(frame.duplicated(subset=["student_id", "as_of_date"]).sum())
    if duplicated:
        logger.warning(
            "history has %d duplicate (student_id, as_of_date) row(s) - keeping the "
            "last of each; the report records the count",
            duplicated,
        )
        frame = frame.drop_duplicates(subset=["student_id", "as_of_date"], keep="last")

    frame = _add_monthly_value(frame)
    recognised = [c for c in frame.columns if c in HISTORY_KNOWN_TARGETS]
    ignored = [c for c in frame.columns if c not in HISTORY_KNOWN_TARGETS]
    frame = frame[recognised].reset_index(drop=True)

    features_used = [c for c in OPTIONAL_FEATURE_COLUMNS if c in frame.columns]
    if len(features_used) < BACKTEST_MIN_FEATURES:
        raise BacktestInputError(
            f"history file {path} carries {len(features_used)} of the model's feature "
            f"columns and the backtest needs at least {BACKTEST_MIN_FEATURES} to train "
            f"anything (found: {features_used}). Columns the model can use: "
            f"{OPTIONAL_FEATURE_COLUMNS}. Map at least {BACKTEST_MIN_FEATURES} of them "
            f'in "history" in {mapping_path}.'
        )

    info = {
        "path": str(path),
        "sha256": _sha256(path),
        "rows": int(len(frame)),
        "students": int(frame["student_id"].nunique()),
        "first_as_of_date": frame["as_of_date"].min().date().isoformat(),
        "last_as_of_date": frame["as_of_date"].max().date().isoformat(),
        "duplicate_student_date_rows_dropped": duplicated,
        "supplied_columns": supplied_columns,
        "features_used": features_used,
        "features_not_supplied": [
            c for c in OPTIONAL_FEATURE_COLUMNS if c not in frame.columns
        ],
        "audited_out_columns_ignored": [
            c for c in AUDITED_OUT_FEATURES if c in recognised
        ],
        "unrecognised_columns_ignored": ignored,
        "median_observation_gap_days": _median_observation_gap(frame),
    }
    return frame, info


def _add_monthly_value(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive `monthly_value_try` from the plan price, as src/data/features.py does.

    Not imported from there: that function REPLACES monthly_fee_try and assumes the
    column exists, and here both columns are optional. The arithmetic - and the
    config table it reads - is the same, so a client's figures line up with the
    product's.
    """
    if "monthly_value_try" in frame.columns or "monthly_fee_try" not in frame.columns:
        return frame
    frame = frame.copy()
    if "plan_type" in frame.columns:
        months = frame["plan_type"].map(PLAN_MONTHS)
        unknown = sorted(set(frame.loc[months.isna(), "plan_type"].dropna().astype(str)))
        if unknown:
            logger.warning(
                "plan_type not in config.PLAN_MONTHS: %s - treating the price as "
                "monthly for those rows",
                unknown,
            )
        months = months.fillna(1)
    else:
        logger.warning(
            "monthly_fee_try supplied without plan_type - the price is treated as a "
            "monthly amount, which overstates the value of any multi-month plan"
        )
        months = 1
    frame["monthly_value_try"] = (
        pd.to_numeric(frame["monthly_fee_try"], errors="coerce") / months
    ).round(2)
    return frame


def _median_observation_gap(frame: pd.DataFrame) -> float | None:
    """Median gap in days between a student's consecutive observations.

    This is what the default active window is derived from, so the same defaults
    work on a weekly export and on a monthly one.
    """
    gaps = (
        frame.sort_values(["student_id", "as_of_date"])
        .groupby("student_id")["as_of_date"]
        .diff()
        .dropna()
        .dt.days
    )
    return float(gaps.median()) if len(gaps) else None


def read_outcomes(
    path: str | Path, mapping: dict, mapping_path: Path
) -> tuple[pd.Series, dict]:
    """Load the outcomes CSV. Returns (churn_date by student_id, info)."""
    path = Path(path)
    raw = _read_csv(path, file_label="outcomes")
    frame = apply_mapping(
        raw,
        mapping.get("outcomes", {}),
        file_label="outcomes",
        file_path=path,
        mapping_path=mapping_path,
        section="outcomes",
        known=OUTCOMES_KNOWN_TARGETS,
        required=OUTCOMES_REQUIRED,
    )
    frame["student_id"] = frame["student_id"].astype(str).str.strip()
    frame["churn_date"] = parse_date_column(
        frame,
        "churn_date",
        file_label="outcomes",
        file_path=path,
        mapping_path=mapping_path,
        date_format=mapping.get("date_format"),
        allow_blank=True,
    )

    flag_used = False
    if "churned" in frame.columns:
        flag_used = True
        flagged = _truthiness(
            frame["churned"],
            true_values=mapping.get("churn_true_values", DEFAULT_CHURN_TRUE_VALUES),
            false_values=mapping.get("churn_false_values", DEFAULT_CHURN_FALSE_VALUES),
            file_label="outcomes",
            file_path=path,
            mapping_path=mapping_path,
            column="churned",
        )
        undated = flagged & frame["churn_date"].isna()
        if undated.any():
            offenders = [
                f"row {int(position) + 1} student {frame['student_id'].iloc[position]!r}"
                for position in np.flatnonzero(undated.to_numpy())[:_MAX_OFFENDERS_SHOWN]
            ]
            raise BacktestInputError(
                f"outcomes file {path}: {int(undated.sum())} row(s) are marked churned "
                f"but carry no churn_date (first: {', '.join(offenders)}). A backtest "
                "measures how many days EARLY a flag was, so a churn with no date "
                "cannot be used at all. Supply the date, or clear the flag for those "
                "rows if they did not actually leave."
            )
        # A date with the flag set to false: the flag is the client's own statement
        # about the student, so it wins, and the disagreement is reported.
        contradicted = int((~flagged & frame["churn_date"].notna()).sum())
        if contradicted:
            logger.warning(
                "outcomes: %d row(s) carry a churn_date while 'churned' says they did "
                "not leave - the flag wins and those dates are ignored",
                contradicted,
            )
        frame.loc[~flagged, "churn_date"] = pd.NaT
    else:
        contradicted = 0

    duplicated = frame["student_id"].duplicated()
    if duplicated.any():
        # Earliest wins: a student can only leave once, and a second row is either a
        # re-enrolment (a different story) or a data error. Either way the first
        # departure is the event this backtest is about.
        logger.warning(
            "outcomes has %d duplicate student_id row(s) - keeping the EARLIEST "
            "churn_date for each",
            int(duplicated.sum()),
        )
        frame = (
            frame.sort_values("churn_date", na_position="last")
            .drop_duplicates(subset=["student_id"], keep="first")
        )

    churn_dates = frame.set_index("student_id")["churn_date"]
    info = {
        "path": str(path),
        "sha256": _sha256(path),
        "rows": int(len(raw)),
        "students": int(len(churn_dates)),
        "churners": int(churn_dates.notna().sum()),
        "churn_flag_column_used": flag_used,
        "duplicate_student_rows_collapsed": int(duplicated.sum()),
        "churn_dates_ignored_because_flag_said_active": contradicted,
        "first_churn_date": (
            churn_dates.min().date().isoformat() if churn_dates.notna().any() else None
        ),
        "last_churn_date": (
            churn_dates.max().date().isoformat() if churn_dates.notna().any() else None
        ),
    }
    return churn_dates, info


def check_outcome_coverage(
    history: pd.DataFrame,
    churn_dates: pd.Series,
    *,
    history_path: Path,
    outcomes_path: Path,
    allow_partial: bool,
) -> dict:
    """Refuse two files that do not look like they describe the same students.

    An id-format mismatch ("STU100751" against "100751") produces a backtest with no
    churners and a coverage of 0%, which reads as "your model found nothing" rather
    than "you sent the wrong column". That mistake must not be survivable.

    The check has to work on both shapes a prospect sends, and they need opposite
    denominators:

      - a FULL ROSTER (one row per student, churn_date blank for most) - the share of
        HISTORY students present in the outcomes file is the signal;
      - a CHURNERS-ONLY list, which is what docs/VERI_TALEBI.md asks for - here a low
        share in that direction is correct and expected (most students did not
        leave), so the signal is the share of OUTCOME ids found in the history
        instead.

    Getting this wrong in either direction costs the same thing - the run stops on a
    file that was exactly right, at 23:00, with a prospect waiting - so both numbers
    are computed, the shape decides which one gates, and both end up in the report.
    """
    history_students = set(history["student_id"].unique())
    outcome_students = set(churn_dates.index)
    matched = history_students & outcome_students

    share_of_history = len(matched) / len(history_students) if history_students else 0.0
    share_of_outcomes = len(matched) / len(outcome_students) if outcome_students else 0.0
    churners_only = bool(len(churn_dates)) and bool(churn_dates.notna().all())

    if churners_only:
        share, basis = share_of_outcomes, (
            "share of the outcomes file's ids found in the history (the outcomes "
            "file lists only churners)"
        )
    else:
        share, basis = share_of_history, (
            "share of the history's students found in the outcomes file"
        )

    if share < BACKTEST_MIN_OUTCOME_COVERAGE and not allow_partial:
        history_sample = sorted(history_students)[:3]
        outcome_sample = sorted(map(str, outcome_students))[:3]
        raise BacktestInputError(
            f"the two files do not look like they describe the same students: "
            f"{basis} is {share:.1%}, below the "
            f"{BACKTEST_MIN_OUTCOME_COVERAGE:.0%} floor ({len(matched)} id(s) in "
            f"common). This is almost always the wrong file or a student-id "
            f"mismatch: history ids look like {history_sample}, outcomes ids like "
            f"{outcome_sample} ({history_path.name} vs {outcomes_path.name}). Fix "
            "the id column, or pass --allow-partial-outcomes to run anyway and treat "
            "every unmatched student as never churned."
        )
    return {
        "history_students": len(history_students),
        "outcome_students": len(outcome_students),
        "matched_students": len(matched),
        "outcomes_file_shape": "churners_only" if churners_only else "full_roster",
        "gating_basis": basis,
        "match_share": round(share, 4),
        "share_of_history_students": round(share_of_history, 4),
        "share_of_outcome_ids": round(share_of_outcomes, 4),
        "unmatched_treated_as_never_churned": len(history_students - matched),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_synthetic(*frames: pd.DataFrame) -> bool:
    """True when any input carries the synthetic marker column.

    Checked on the RAW columns before our renames, so a marker survives a mapping
    that does not mention it - which is every mapping, since the marker is not a
    column anyone would map.
    """
    return any(
        SYNTHETIC_MARKER_COLUMN in frame.columns
        and frame[SYNTHETIC_MARKER_COLUMN].astype(str).eq(SYNTHETIC_MARKER_VALUE).any()
        for frame in frames
    )


# --- Walk-forward scheme -----------------------------------------------------
def evaluation_origins(
    first_as_of: pd.Timestamp,
    last_as_of: pd.Timestamp,
    *,
    churn_window_days: int,
    step_days: int,
    warmup_days: int,
) -> list[pd.Timestamp]:
    """The dates the model is evaluated at, oldest first.

    The grid is laid out BACKWARDS from the last usable origin
    (`last_as_of - churn_window_days`, the last date whose look-ahead window is
    fully observed in the export) rather than forwards from the first. Anchoring to
    the recent end means the most data-rich folds exist at every setting of
    `--step-days`, and changing the step does not shuffle which months are measured.

    `warmup_days` is how much history must sit behind the first origin: at least one
    churn window (so a training row's outcome can have been observed) plus one step.
    """
    last_origin = last_as_of - timedelta(days=churn_window_days)
    first_origin = first_as_of + timedelta(days=warmup_days)
    if last_origin < first_origin:
        span = (last_as_of - first_as_of).days
        raise BacktestInputError(
            f"the history spans {span} day(s) "
            f"({first_as_of.date()} to {last_as_of.date()}), which is not enough for "
            f"even one evaluation point: a point needs {warmup_days} day(s) of "
            f"history behind it and {churn_window_days} day(s) of outcome ahead of "
            f"it, i.e. at least {warmup_days + churn_window_days} day(s) in total. "
            "Send a longer export, or lower --churn-window-days / --step-days."
        )

    origins = []
    cursor = last_origin
    while cursor >= first_origin:
        origins.append(cursor)
        cursor = cursor - timedelta(days=step_days)
    return sorted(origins)


def _assert_only_past(
    as_of_dates: pd.Series,
    origin: pd.Timestamp,
    *,
    churn_window_days: int,
    what: str,
) -> None:
    """The time-ordering guarantee, asserted on the data itself.

    `churn_window_days > 0` is the TRAINING check: a row may only be used once its
    own churn window has closed, so `as_of_date + W <= origin`. `0` is the SCORING
    check: a row may describe the origin itself but never anything after it.

    Asserted on the frame rather than trusted from the call site because a future
    row makes the report BETTER, so nothing downstream would ever complain.
    """
    if as_of_dates.empty:
        return
    latest = pd.Timestamp(as_of_dates.max())
    limit = origin - timedelta(days=churn_window_days)
    if latest > limit:
        raise TemporalLeakageError(
            f"{what}: a row dated {latest.date()} reached the model at evaluation "
            f"point {origin.date()}, but nothing after {limit.date()} could have been "
            f"known there (churn window {churn_window_days} day(s)). A backtest that "
            "sees the future reports a better number than a correct one, so this "
            "stops the run. See the module docstring of scripts/backtest.py."
        )


def select_training_rows(
    history: pd.DataFrame, origin: pd.Timestamp, *, churn_window_days: int
) -> pd.DataFrame:
    """The rows that may train the model evaluated at `origin`.

    The embargo, which is the whole scheme in one line: a row dated `d` carries the
    label "did this student churn in (d, d + W]", and that label is not KNOWN until
    `d + W`. So `d + W <= origin`, not `d <= origin`. Filtering on `d <= origin`
    would hand the model rows whose outcome was still in the future at the moment
    the model is pretending to make a decision - which is exactly the leak
    docs/LEAKAGE_AUDIT.md is about, moved from the feature axis to the time axis.

    Rows dated on or after a student's own churn date are dropped too: a row about a
    student who has already gone is not an early-warning opportunity.
    """
    cutoff = origin - timedelta(days=churn_window_days)
    rows = history[history["as_of_date"] <= cutoff]
    rows = rows[~_already_gone(rows)]
    _assert_only_past(
        rows["as_of_date"],
        origin,
        churn_window_days=churn_window_days,
        what="training rows",
    )
    return rows.reset_index(drop=True)


def _already_gone(rows: pd.DataFrame) -> pd.Series:
    if "churn_date" not in rows.columns:
        return pd.Series(False, index=rows.index)
    return rows["churn_date"].notna() & (rows["churn_date"] <= rows["as_of_date"])


def label_rows(
    as_of: pd.Series | pd.Timestamp,
    churn_date: pd.Series,
    *,
    churn_window_days: int,
) -> pd.Series:
    """1 when the student churned in `(as_of, as_of + W]`, else 0.

    Half-open at the left on purpose: a churn ON the as-of date is not something that
    row could have warned about.
    """
    horizon = (
        as_of + pd.to_timedelta(churn_window_days, unit="D")
        if isinstance(as_of, pd.Series)
        else pd.Timestamp(as_of) + timedelta(days=churn_window_days)
    )
    return (
        churn_date.notna() & (churn_date > as_of) & (churn_date <= horizon)
    ).astype(int)


def select_scoring_rows(
    history: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    active_window_days: int,
) -> pd.DataFrame:
    """Each student active at `origin`, as their most recent row on or before it.

    "Active" is the most recent row being no more than `active_window_days` old: a
    student whose last observation is six months back is not someone a mentor would
    have had on a list that week, and counting them as a miss would understate the
    model on the client's own terms. Students who had already churned by `origin`
    are excluded - they cannot be warned about any more.
    """
    rows = history[history["as_of_date"] <= origin]
    rows = rows[rows["as_of_date"] > origin - timedelta(days=active_window_days)]
    if "churn_date" in rows.columns:
        rows = rows[~(rows["churn_date"].notna() & (rows["churn_date"] <= origin))]
    # The history index is deliberately NOT reset: `missed_breakdown` reads each
    # missed churner's features back out of the history by the row that was scored,
    # and a per-fold 0..n index would silently point at the wrong students.
    rows = rows.sort_values(["student_id", "as_of_date"]).drop_duplicates(
        subset=["student_id"], keep="last"
    )
    _assert_only_past(
        rows["as_of_date"], origin, churn_window_days=0, what="scoring rows"
    )
    return rows


# --- Fold-local preprocessing ------------------------------------------------
def fit_preprocessor(
    train: pd.DataFrame, feature_columns: list[str]
) -> dict:
    """Learn imputation from the TRAINING SLICE ONLY. Part of the guarantee.

    Not `src/data/features.fit_imputation`: that one groups by `plan_type` over two
    named columns, both of which are properties of the one dataset this repo was
    built on. A prospect's export may have neither, and may have nulls in columns
    ours never did, so the fold's recipe is derived from the fold's own data -
    which is also the only way the "fitted on the past only" claim stays true.
    """
    categorical = [c for c in feature_columns if c in CAT_COLS]
    numeric = [c for c in feature_columns if c not in categorical]

    medians: dict[str, float] = {}
    empty: list[str] = []
    for column in numeric:
        values = pd.to_numeric(train[column], errors="coerce")
        if values.notna().any():
            medians[column] = float(values.median())
        else:
            # No value at all in this slice: 0 and a flag, so the column carries
            # "we never saw this" rather than an invented central value.
            empty.append(column)
            medians[column] = 0.0

    flagged = [
        column
        for column in numeric
        if pd.to_numeric(train[column], errors="coerce").isna().any()
    ]
    columns = categorical + numeric + [f"{c}_missing" for c in flagged]
    return {
        "categorical": categorical,
        "numeric": numeric,
        "medians": medians,
        "all_null_in_train": empty,
        "flagged": flagged,
        "columns": columns,
    }


def apply_preprocessor(frame: pd.DataFrame, prep: dict) -> pd.DataFrame:
    """Build the model matrix with the medians learned on the training slice."""
    out = {}
    for column in prep["categorical"]:
        values = frame[column]
        out[column] = (
            values.where(values.notna(), CATEGORICAL_MISSING).astype(str).str.strip()
        )
    for column in prep["numeric"]:
        numeric = pd.to_numeric(frame[column], errors="coerce")
        out[column] = numeric.fillna(prep["medians"][column])
    for column in prep["flagged"]:
        out[f"{column}_missing"] = (
            pd.to_numeric(frame[column], errors="coerce").isna().astype(int)
        )
    return pd.DataFrame(out, index=frame.index)[prep["columns"]]


def _time_ordered_validation_split(
    train: pd.DataFrame, labels: pd.Series, *, date_fraction: float
) -> tuple[np.ndarray, np.ndarray, str]:
    """Hold back the training slice's LATEST dates for calibration and threshold.

    By date rather than at random, because the calibrator and the threshold are
    applied to data that comes after the training slice, and a random split fits
    them on an average of the whole slice instead of its recent end.

    Falls back to a stratified random split of the SAME slice (no future row is
    involved either way) when the slice has too few distinct dates, or when the
    date split leaves one class in the validation half - `fit_calibrator` refuses a
    single-class calibration set, correctly, and losing the whole fold to that is a
    worse outcome than a split that is less faithful to the time order.
    """
    dates = np.sort(train["as_of_date"].unique())
    if len(dates) >= 2:
        cut = dates[max(1, int(len(dates) * (1.0 - date_fraction)))]
        is_val = (train["as_of_date"] >= cut).to_numpy()
        if is_val.any() and (~is_val).any() and len(np.unique(labels[is_val])) == 2:
            return np.flatnonzero(~is_val), np.flatnonzero(is_val), "by_date"

    from sklearn.model_selection import train_test_split

    if len(np.unique(labels)) < 2:
        return np.array([], dtype=int), np.array([], dtype=int), "impossible"
    train_index, val_index = train_test_split(
        np.arange(len(train)),
        test_size=date_fraction,
        random_state=42,
        stratify=labels,
    )
    return train_index, val_index, "stratified_fallback"


# --- One fold ----------------------------------------------------------------
def run_fold(
    history: pd.DataFrame,
    origin: pd.Timestamp,
    *,
    feature_columns: list[str],
    churn_window_days: int,
    active_window_days: int,
    k: int,
    flag_rule: str,
    baseline_column: str | None,
    min_train_rows: int,
    min_train_churners: int,
    val_date_fraction: float,
) -> dict:
    """Train on the past, score the present, look one window ahead. One evaluation point.

    Returns a dict with `"status"` either "ok" or "skipped". A fold is never
    silently dropped: a skipped fold carries the reason into the report, because
    "we measured nine months" and "we measured nine months and three were
    unusable" are different claims.
    """
    train_rows = select_training_rows(
        history, origin, churn_window_days=churn_window_days
    )
    train_labels = label_rows(
        train_rows["as_of_date"],
        train_rows["churn_date"],
        churn_window_days=churn_window_days,
    )

    if len(train_rows) < min_train_rows:
        return _skipped(
            origin,
            f"only {len(train_rows)} labelled training row(s), minimum {min_train_rows}",
        )
    if int(train_labels.sum()) < min_train_churners:
        return _skipped(
            origin,
            f"only {int(train_labels.sum())} churner(s) in the training slice, "
            f"minimum {min_train_churners}",
        )

    scoring = select_scoring_rows(
        history, origin, active_window_days=active_window_days
    )
    if scoring.empty:
        return _skipped(
            origin,
            f"no student has an observation in the {active_window_days} day(s) before "
            "this point, so there was nobody to score",
        )
    scoring_labels = label_rows(
        origin, scoring["churn_date"], churn_window_days=churn_window_days
    )

    train_index, val_index, split_kind = _time_ordered_validation_split(
        train_rows, train_labels, date_fraction=val_date_fraction
    )
    if split_kind == "impossible":
        return _skipped(origin, "the training slice holds a single class")

    prep = fit_preprocessor(train_rows.iloc[train_index], feature_columns)
    X_train = apply_preprocessor(train_rows.iloc[train_index], prep)
    X_val = apply_preprocessor(train_rows.iloc[val_index], prep)
    y_train = train_labels.iloc[train_index]
    y_val = train_labels.iloc[val_index]

    # The guarantee, re-asserted on the exact frames the model is about to see -
    # not on the selection that produced them.
    _assert_only_past(
        train_rows["as_of_date"].iloc[train_index],
        origin,
        churn_window_days=churn_window_days,
        what="model fit frame",
    )
    _assert_only_past(
        train_rows["as_of_date"].iloc[val_index],
        origin,
        churn_window_days=churn_window_days,
        what="calibration frame",
    )

    model = build_model(cat_features=prep["categorical"])
    fit_model(model, X_train, y_train, X_val, y_val)
    try:
        calibrator = fit_calibrator(model, X_val, y_val)
    except DegenerateCalibrationError as e:
        return _skipped(origin, f"calibration impossible on this fold: {e}")

    selection = select_threshold(
        y_val,
        churn_proba(model, X_val, calibrator),
        cost_false_alarm=DECISION_COST["false_alarm"],
        cost_missed_churn=DECISION_COST["missed_churn"],
    )
    threshold = selection["threshold"]

    X_score = apply_preprocessor(scoring, prep)
    scores = np.asarray(churn_proba(model, X_score, calibrator), dtype=float)
    truth = scoring_labels.to_numpy()

    # Two operating points, and the difference matters enough that both are in the
    # report. CAPACITY is the default and the one a client recognises: "our mentors
    # work the top 20 of the list each month". The cost-optimised THRESHOLD is what
    # the production pipeline uses, and on a 30-day window with a 3-4% base rate it
    # flags almost nobody - because at DECISION_COST 1:3 an outreach only pays above
    # 25% precision, which no honest model reaches at that base rate. Reporting
    # coverage off the threshold would therefore describe the cost setting rather
    # than the model, so the default is capacity and the threshold's own counts are
    # published beside it.
    at_capacity = np.zeros(len(scores), dtype=bool)
    at_capacity[np.argsort(scores)[::-1][: min(k, len(scores))]] = True
    at_threshold = scores >= threshold
    flagged = at_capacity if flag_rule == "capacity" else at_threshold

    baseline = _baseline_scores(
        train_rows.iloc[train_index],
        y_train,
        scoring,
        baseline_column,
        prep,
        n_to_flag=int(flagged.sum()),
    )

    scored = pd.DataFrame(
        {
            "origin": origin,
            "student_id": scoring["student_id"].to_numpy(),
            "as_of_date": scoring["as_of_date"].to_numpy(),
            "churn_date": scoring["churn_date"].to_numpy(),
            "score": scores,
            "label": truth,
            "flagged": flagged.astype(int),
            "baseline_flagged": baseline["flagged"].astype(int),
            "monthly_value_try": _monthly_value(scoring),
            # The row in the HISTORY frame this score came from, so the missed
            # breakdown can read the student's state back out of it.
            "history_row": scoring.index.to_numpy(),
        }
    )

    return {
        "status": "ok",
        "origin": origin.date().isoformat(),
        "train": {
            "rows": int(len(train_rows)),
            "fit_rows": int(len(train_index)),
            "calibration_rows": int(len(val_index)),
            "churners": int(train_labels.sum()),
            "base_rate": float(train_labels.mean()),
            "first_as_of_date": train_rows["as_of_date"].min().date().isoformat(),
            "last_as_of_date": train_rows["as_of_date"].max().date().isoformat(),
            "embargo_cutoff": (
                origin - timedelta(days=churn_window_days)
            ).date().isoformat(),
            "validation_split": split_kind,
            "imputation_medians": prep["medians"],
            "columns_all_null_in_train": prep["all_null_in_train"],
        },
        "threshold": threshold,
        "threshold_selection": selection,
        "scored": _fold_metrics(truth, scores, flagged, k=k)
        | {
            "flag_rule": flag_rule,
            "flagged_at_capacity": int(at_capacity.sum()),
            "caught_at_capacity": int((at_capacity & (truth == 1)).sum()),
            "flagged_at_cost_threshold": int(at_threshold.sum()),
            "caught_at_cost_threshold": int((at_threshold & (truth == 1)).sum()),
        },
        "baseline": _fold_metrics(
            truth,
            baseline["score"],
            baseline["flagged"],
            k=k,
            probability_scale=False,
        )
        | {"column": baseline["column"], "direction": baseline["direction"]},
        "money": _fold_money(scored),
        "_scored_rows": scored,
    }


def _skipped(origin: pd.Timestamp, reason: str) -> dict:
    logger.warning("fold %s skipped: %s", origin.date(), reason)
    return {"status": "skipped", "origin": origin.date().isoformat(), "reason": reason}


def _monthly_value(frame: pd.DataFrame) -> np.ndarray:
    if "monthly_value_try" not in frame.columns:
        return np.full(len(frame), np.nan)
    return pd.to_numeric(frame["monthly_value_try"], errors="coerce").to_numpy()


def _fold_metrics(
    truth: np.ndarray,
    scores: np.ndarray,
    flagged: np.ndarray,
    *,
    k: int,
    probability_scale: bool = True,
) -> dict:
    """Metrics for one fold's scored students.

    ROC-AUC and PR-AUC need both classes present; a window with no churners is a
    legitimate outcome rather than an error, so they come back `None` and the
    counts still tell the reader what happened.
    """
    from sklearn.metrics import average_precision_score, roc_auc_score

    truth = np.asarray(truth).astype(int)
    flagged = np.asarray(flagged).astype(bool)
    both_classes = len(np.unique(truth)) == 2
    n_flagged = int(flagged.sum())
    caught = int((flagged & (truth == 1)).sum())
    base_rate = float(truth.mean()) if truth.size else 0.0

    metrics = {
        "students_scored": int(truth.size),
        "churners_in_window": int(truth.sum()),
        "base_rate": base_rate,
        "flagged": n_flagged,
        "caught": caught,
        "precision": (caught / n_flagged) if n_flagged else None,
        "recall": (caught / int(truth.sum())) if truth.sum() else None,
        "k": int(min(k, truth.size)),
        "precision_at_k": float(precision_at_k(truth, scores, k)),
        "lift_at_k": float(lift_at_k(truth, scores, k)) if base_rate else None,
        "roc_auc": float(roc_auc_score(truth, scores)) if both_classes else None,
        "pr_auc": float(average_precision_score(truth, scores)) if both_classes else None,
    }
    if probability_scale:
        metrics["mean_score"] = float(np.mean(scores)) if scores.size else None
    return metrics


def _baseline_scores(
    train: pd.DataFrame,
    train_labels: pd.Series,
    scoring: pd.DataFrame,
    column: str | None,
    prep: dict,
    *,
    n_to_flag: int,
) -> dict:
    """Rank students by ONE column, no model at all - the rule the model must beat.

    Two things make this a fair fight rather than a straw man:

      - the column's DIRECTION is re-derived on each fold's own training slice, so
        the rule is never accidentally inverted and handed a 0.2 precision it did
        not deserve;
      - it flags exactly as many students as the model did, so coverage and lead
        time are compared at equal mentor capacity. Comparing a rule at an
        arbitrary cut-off against a cost-optimised threshold measures the cut-off.

    Both are computed from the training slice only, so the baseline obeys the same
    embargo as the model.
    """
    if column is None or column not in scoring.columns:
        return {
            "score": np.zeros(len(scoring)),
            "flagged": np.zeros(len(scoring), dtype=bool),
            "column": None,
            "direction": None,
        }

    train_values = pd.to_numeric(train[column], errors="coerce")
    correlation = train_values.corr(train_labels.astype(float))
    direction = -1.0 if (correlation is not None and correlation < 0) else 1.0

    values = pd.to_numeric(scoring[column], errors="coerce").fillna(
        prep["medians"].get(column, float(train_values.median()))
    )
    score = (direction * values).to_numpy(dtype=float)

    flagged = np.zeros(len(score), dtype=bool)
    if n_to_flag > 0:
        flagged[np.argsort(score)[::-1][:n_to_flag]] = True
    return {
        "score": score,
        "flagged": flagged,
        "column": column,
        "direction": "higher is riskier" if direction > 0 else "lower is riskier",
    }


def choose_baseline_column(
    history: pd.DataFrame, feature_columns: list[str], *, requested: str | None
) -> tuple[str | None, str]:
    """Pick the single column the simple rule ranks by.

    `src/model/baseline.py` defaults to `message_response_time_hours` - the
    strongest student-side column that survived the leakage audit - so that is the
    first choice whenever the client supplied it, and the comparison then matches
    the one in model_meta.json. Otherwise the column with the most non-null values
    is used: a rule evaluated on a column that is 80% missing measures the
    imputation, not the rule.
    """
    numeric = [c for c in feature_columns if c not in CAT_COLS]
    if requested:
        if requested not in history.columns:
            raise BacktestInputError(
                f"--baseline-column {requested!r} is not a usable column in the "
                f"history. Available numeric feature columns: {numeric}."
            )
        return requested, "requested on the command line"
    if "message_response_time_hours" in numeric:
        return (
            "message_response_time_hours",
            "src/model/baseline.py default (strongest student-side column in the audit)",
        )
    if not numeric:
        return None, "no numeric feature column was supplied"
    densest = max(numeric, key=lambda c: int(history[c].notna().sum()))
    return densest, "most densely populated numeric column supplied"


def _fold_money(scored: pd.DataFrame) -> dict:
    values = scored["monthly_value_try"]
    if values.isna().all():
        return {"available": False, "reason": "no monthly_value_try / monthly_fee_try supplied"}
    flagged = scored["flagged"] == 1
    caught = flagged & (scored["label"] == 1)
    return {
        "available": True,
        "flagged_monthly_value_try": float(values[flagged].sum(skipna=True)),
        "caught_churner_monthly_value_try": float(values[caught].sum(skipna=True)),
        "students_without_value": int(values.isna().sum()),
    }


# --- Aggregation across folds ------------------------------------------------
NOT_VALUE_SAVED = (
    "Bu tutar RISK ALTINDA VE GORUNUR hale gelen aylik degerdir. Kurtarilan gelir "
    "DEGILDIR: backtest'te kimse mudahale etmedi, hicbir ogrenci aranmadi. "
    "Kurtarilan gelir ancak mudahale edilen bir pilotta olculebilir."
)


def lead_time_records(
    scored: pd.DataFrame,
    *,
    first_origin: pd.Timestamp,
    last_origin: pd.Timestamp,
    churn_window_days: int,
    churn_dates: pd.Series,
) -> tuple[pd.DataFrame, dict]:
    """One row per churner we had a chance at, with the lead time we achieved.

    "A chance at" is deliberately narrow and stated in the report: the student left
    inside the measured span AND was scored at least once at a point before they
    left. A churner who was never scored (their history went stale before they
    left, so no evaluation point saw them) is NOT counted as a miss - that would
    blame the model for a gap in the export - but the count is published, because
    on a real export it is often large and it is the client's own data quality.
    """
    span_end = last_origin + timedelta(days=churn_window_days)
    in_span = churn_dates.notna() & (churn_dates > first_origin) & (churn_dates <= span_end)
    churners_in_span = set(churn_dates.index[in_span])

    before = scored[scored["churn_date"].notna() & (scored["origin"] < scored["churn_date"])]
    records = []
    for student_id, rows in before.groupby("student_id", sort=False):
        if student_id not in churners_in_span:
            continue
        churn_date = pd.Timestamp(rows["churn_date"].iloc[0])
        hits = rows[rows["flagged"] == 1]
        baseline_hits = rows[rows["baseline_flagged"] == 1]
        records.append(
            {
                "student_id": student_id,
                "churn_date": churn_date,
                "opportunities": int(len(rows)),
                "flagged": int(len(hits) > 0),
                "lead_days": (
                    int((churn_date - pd.Timestamp(hits["origin"].min())).days)
                    if len(hits)
                    else None
                ),
                "baseline_flagged": int(len(baseline_hits) > 0),
                "baseline_lead_days": (
                    int((churn_date - pd.Timestamp(baseline_hits["origin"].min())).days)
                    if len(baseline_hits)
                    else None
                ),
                "last_scored_row": int(rows.sort_values("origin")["history_row"].iloc[-1]),
                "monthly_value_try": float(
                    rows.sort_values("origin")["monthly_value_try"].iloc[-1]
                )
                if pd.notna(rows.sort_values("origin")["monthly_value_try"].iloc[-1])
                else None,
            }
        )

    table = pd.DataFrame(records)
    info = {
        "churners_in_measured_span": len(churners_in_span),
        "churners_with_a_scoring_opportunity": int(len(table)),
        "churners_never_scored_before_leaving": len(churners_in_span) - int(len(table)),
        "measured_span": {
            "from": first_origin.date().isoformat(),
            "to": span_end.date().isoformat(),
        },
    }
    return table, info


def oracle_coverage(
    scored: pd.DataFrame, table: pd.DataFrame, *, k: int, flag_rule: str
) -> dict:
    """What a PERFECT ranking would have covered at the same capacity.

    This is the denominator the headline coverage has to be read against, and
    without it a coverage figure is unreadable: a client with 900 departures a year
    and mentors who can work 20 names a month cannot be told "you only caught 12%"
    as though the other 88% were a model failure. Most of it is arithmetic - there
    were never enough slots.

    Computed greedily forward through the evaluation points: at each point, spend
    the capacity on churners who are not covered yet. Greedy is optimal here
    because a slot at an earlier point can only ever cover a churner who is still
    coverable later, so taking uncovered churners first never costs a later cover.

    Under the threshold rule there is no capacity to spend, so the ceiling is 100%
    and the field says so rather than reporting a meaningless number.
    """
    if table.empty:
        return {"available": False, "reason": "no evaluable churner"}
    if flag_rule != "capacity":
        return {
            "available": False,
            "reason": "the threshold rule has no capacity limit, so the ceiling is 1.0",
        }

    coverable = set(table["student_id"])
    covered: set[str] = set()
    slots = 0
    before = scored[
        scored["churn_date"].notna() & (scored["origin"] < scored["churn_date"])
    ]
    for _origin, rows in before.groupby("origin", sort=True):
        churners = [
            student_id
            for student_id in rows.loc[rows["label"] == 1, "student_id"]
            if student_id in coverable and student_id not in covered
        ]
        budget = min(k, len(rows))
        slots += budget
        covered.update(churners[:budget])

    return {
        "available": True,
        "oracle_coverage": float(len(covered) / len(coverable)),
        "flag_slots_total": slots,
        "evaluable_churners": int(len(coverable)),
        "note": (
            "Kusursuz bir siralama ayni kapasiteyle bu kadarini kapsayabilirdi. "
            "Bunun uzeri model hatasi degil, kapasite sinirdir."
        ),
    }


def _distribution(values) -> dict:
    """Quartiles, not just a mean: a lead time is a distribution and the tails sell.

    Nulls are dropped rather than treated as zero - a churner we never flagged has
    no lead time, and counting them as 0 days would quietly turn the coverage miss
    into a lead-time penalty and report the same failure twice.
    """
    values = pd.to_numeric(pd.Series(list(values), dtype="object"), errors="coerce")
    values = values.dropna().to_numpy(dtype=float)
    if not values.size:
        return {"n": 0}
    return {
        "n": int(values.size),
        "min": int(values.min()),
        "q1": float(np.percentile(values, 25)),
        "median": float(np.median(values)),
        "q3": float(np.percentile(values, 75)),
        "max": int(values.max()),
        "mean": float(values.mean()),
    }


def bootstrap_over_students(
    table: pd.DataFrame,
    statistics: dict,
    *,
    resamples: int,
    seed: int,
) -> dict:
    """Percentile CIs by resampling STUDENTS, not rows.

    Same approach as `precision_at_k_stability` in scripts/compare_feature_sets.py,
    and for the same reason: a coverage of 62% measured on 34 churners is not a
    result, it is a range, and a founder quoting it to a customer needs the width.
    Resampling is at student level so one student cannot become two independent
    observations.
    """
    out = {}
    if table.empty:
        return {name: {"n": 0, "reportable": False} for name in statistics}

    rng = np.random.default_rng(seed)
    draws = [rng.integers(0, len(table), len(table)) for _ in range(resamples)]
    for name, statistic in statistics.items():
        point = statistic(table)
        resampled = [statistic(table.iloc[index]) for index in draws]
        resampled = [v for v in resampled if v is not None]
        if point is None or not resampled:
            out[name] = {"n": int(len(table)), "reportable": False}
            continue
        low, high = np.percentile(resampled, [2.5, 97.5])
        out[name] = {
            "value": float(point),
            "ci95_low": round(float(low), 4),
            "ci95_high": round(float(high), 4),
            "n": int(len(table)),
            "resamples": resamples,
            "reportable": bool(len(table) >= BACKTEST_MIN_REPORTABLE_N),
        }
    return out


def bootstrap_precision_at_k(
    folds: list[dict], *, k: int, column: str, resamples: int, seed: int
) -> dict:
    """CI for precision@k averaged over folds, resampling within each fold.

    Averaged over folds rather than pooled: each fold is one week's worth of the
    client's actual mentor capacity, and pooling turns ten lists of twenty into one
    list of two hundred, which is a capacity nobody has.
    """
    usable = [f["_scored_rows"] for f in folds if f["status"] == "ok"]
    if not usable:
        return {"n": 0, "reportable": False}

    rng = np.random.default_rng(seed)
    point = float(
        np.mean(
            [
                precision_at_k(f["label"].to_numpy(), f[column].to_numpy(), k)
                for f in usable
            ]
        )
    )
    resampled = []
    for _ in range(resamples):
        values = []
        for fold in usable:
            index = rng.integers(0, len(fold), len(fold))
            sample = fold.iloc[index]
            values.append(
                precision_at_k(sample["label"].to_numpy(), sample[column].to_numpy(), k)
            )
        resampled.append(float(np.mean(values)))
    low, high = np.percentile(resampled, [2.5, 97.5])
    total_rows = int(sum(len(f) for f in usable))
    return {
        "value": point,
        "ci95_low": round(float(low), 4),
        "ci95_high": round(float(high), 4),
        "folds": len(usable),
        "k": k,
        "rows": total_rows,
        "resamples": resamples,
        # k students per fold is what the figure is actually measured on.
        "reportable": bool(len(usable) * k >= BACKTEST_MIN_REPORTABLE_N),
    }


def missed_breakdown(
    table: pd.DataFrame, history: pd.DataFrame, feature_columns: list[str]
) -> dict:
    """Which churners we never flagged, and what they looked like.

    The most honest section of the report and the one a sceptical buyer will push
    on, so it is a breakdown rather than a number: by every categorical column they
    supplied, and by the standardised gap on every numeric one. Features are read
    from the LAST row we scored the student on before they left - the state the
    model saw when it had its final chance.
    """
    if table.empty:
        return {"missed": 0, "caught": 0, "note": "no evaluable churner in this backtest"}

    missed = table[table["flagged"] == 0]
    caught = table[table["flagged"] == 1]
    rows = history.loc[table["last_scored_row"].to_numpy()]
    rows = rows.assign(_missed=(table["flagged"] == 0).to_numpy())

    categorical = {}
    for column in [c for c in feature_columns if c in CAT_COLS]:
        grouped = rows.groupby(rows[column].astype(str))["_missed"]
        categorical[column] = {
            str(level): {
                "churners": int(size),
                "missed": int(total),
                "missed_rate": float(total / size) if size else None,
            }
            for level, size, total in zip(
                grouped.size().index, grouped.size(), grouped.sum()
            )
        }

    numeric_gaps = []
    for column in [c for c in feature_columns if c not in CAT_COLS]:
        values = pd.to_numeric(rows[column], errors="coerce")
        missed_values = values[rows["_missed"].to_numpy()]
        caught_values = values[~rows["_missed"].to_numpy()]
        spread = float(values.std(ddof=0))
        if not spread or missed_values.notna().sum() < 2 or caught_values.notna().sum() < 2:
            continue
        numeric_gaps.append(
            {
                "feature": column,
                "label": FEATURE_LABELS.get(column, column),
                "missed_mean": float(missed_values.mean()),
                "caught_mean": float(caught_values.mean()),
                "standardised_gap": float(
                    (missed_values.mean() - caught_values.mean()) / spread
                ),
            }
        )
    numeric_gaps.sort(key=lambda row: abs(row["standardised_gap"]), reverse=True)

    return {
        "missed": int(len(missed)),
        "caught": int(len(caught)),
        "missed_rate": float(len(missed) / len(table)),
        "missed_monthly_value_try": float(
            pd.to_numeric(missed["monthly_value_try"], errors="coerce").sum()
        ),
        "by_segment": categorical,
        "numeric_gaps": numeric_gaps,
        "note": (
            "Segment ve feature kirilimi, ogrencinin AYRILMADAN ONCE skorlandigi son "
            "satirdan okunur."
        ),
    }


def _verdict(model: dict, baseline: dict, coverage: dict, baseline_coverage: dict) -> dict:
    """Did the model beat the one-column rule? Including "we cannot tell"."""
    if baseline.get("value") is None:
        return {
            "outcome": "no_baseline",
            "tr": "Basit kural hesaplanamadi: karsilastirma icin uygun tek kolon yok.",
        }

    model_value, baseline_value = model["value"], baseline["value"]
    overlap = (
        model["ci95_low"] <= baseline["ci95_high"]
        and baseline["ci95_low"] <= model["ci95_high"]
    )
    coverage_delta = (coverage.get("value") or 0.0) - (baseline_coverage.get("value") or 0.0)

    if overlap:
        outcome = "indistinguishable"
        text = (
            f"Model (precision@K {model_value:.3f}) ile tek kolonluk basit kural "
            f"({baseline_value:.3f}) arasindaki fark BU VERIDE OLCULEMIYOR: guven "
            "araliklari ust uste biniyor. Bu sonuc, modelin kurali gectigi seklinde "
            "sunulamaz."
        )
    elif model_value > baseline_value:
        outcome = "model_better"
        text = (
            f"Model basit kurali geciyor: precision@K {model_value:.3f} vs "
            f"{baseline_value:.3f}, guven araliklari ayrisiyor. Kapsama farki "
            f"{coverage_delta:+.1%}."
        )
    else:
        outcome = "baseline_better"
        text = (
            f"MODEL BASIT KURALI GECEMIYOR: precision@K {model_value:.3f} vs "
            f"{baseline_value:.3f}. Bu veride modelin karmasikligi kendini "
            "cikarmiyor; musteriye once bunun soylenmesi gerekir."
        )
    return {
        "outcome": outcome,
        "model_precision_at_k": model_value,
        "baseline_precision_at_k": baseline_value,
        "confidence_intervals_overlap": bool(overlap),
        "coverage_delta": coverage_delta,
        "tr": text,
    }


# --- The run -----------------------------------------------------------------
def run_backtest(
    history: pd.DataFrame,
    churn_dates: pd.Series,
    *,
    feature_columns: list[str],
    churn_window_days: int,
    step_days: int,
    active_window_days: int,
    warmup_days: int,
    k: int,
    flag_rule: str,
    baseline_column: str | None,
    min_train_rows: int,
    min_train_churners: int,
    val_date_fraction: float,
    bootstrap_resamples: int,
    seed: int,
) -> dict:
    """Walk forward through the history and assemble the whole report body."""
    history = history.copy()
    history["churn_date"] = history["student_id"].map(churn_dates)

    origins = evaluation_origins(
        history["as_of_date"].min(),
        history["as_of_date"].max(),
        churn_window_days=churn_window_days,
        step_days=step_days,
        warmup_days=warmup_days,
    )
    logger.info(
        "%d evaluation point(s): %s .. %s",
        len(origins),
        origins[0].date(),
        origins[-1].date(),
    )

    folds = []
    for origin in origins:
        logger.info("evaluation point %s", origin.date())
        folds.append(
            run_fold(
                history,
                origin,
                feature_columns=feature_columns,
                churn_window_days=churn_window_days,
                active_window_days=active_window_days,
                k=k,
                flag_rule=flag_rule,
                baseline_column=baseline_column,
                min_train_rows=min_train_rows,
                min_train_churners=min_train_churners,
                val_date_fraction=val_date_fraction,
            )
        )

    usable = [f for f in folds if f["status"] == "ok"]
    if not usable:
        raise BacktestInputError(
            "every evaluation point was skipped, so there is nothing to report. The "
            "reasons are: "
            + "; ".join(f"{f['origin']}: {f['reason']}" for f in folds)
            + ". Usually this means the export is too short, or churn is too rare in "
            "it to train on - lower --min-train-rows / --min-train-churners only if "
            "you are willing to publish a number measured on that little data."
        )

    scored = pd.concat([f["_scored_rows"] for f in usable], ignore_index=True)
    first_origin = pd.Timestamp(min(f["_scored_rows"]["origin"].iloc[0] for f in usable))
    last_origin = pd.Timestamp(max(f["_scored_rows"]["origin"].iloc[0] for f in usable))

    table, span_info = lead_time_records(
        scored,
        first_origin=first_origin,
        last_origin=last_origin,
        churn_window_days=churn_window_days,
        churn_dates=churn_dates,
    )

    lead = _distribution(table["lead_days"].tolist() if not table.empty else [])
    baseline_lead = _distribution(
        table["baseline_lead_days"].tolist() if not table.empty else []
    )
    intervals = bootstrap_over_students(
        table,
        {
            "coverage": lambda t: float(t["flagged"].mean()) if len(t) else None,
            "lead_time_median_days": lambda t: (
                float(np.median(t.loc[t["flagged"] == 1, "lead_days"].dropna()))
                if (t["flagged"] == 1).any()
                else None
            ),
            "baseline_coverage": lambda t: (
                float(t["baseline_flagged"].mean()) if len(t) else None
            ),
        },
        resamples=bootstrap_resamples,
        seed=seed,
    )
    # The coverage interval's universe is every evaluable churner; the lead-time
    # median's is only the churners we actually flagged, since an unflagged one has
    # no lead time. `reportable` has to be judged against the second, or a median of
    # four students inherits the credibility of nine hundred.
    flagged_churners = int(table["flagged"].sum()) if not table.empty else 0
    if "lead_time_median_days" in intervals:
        intervals["lead_time_median_days"].update(
            {
                "n": flagged_churners,
                "resampled_over_churners": int(len(table)),
                "reportable": bool(flagged_churners >= BACKTEST_MIN_REPORTABLE_N),
            }
        )

    model_p_at_k = bootstrap_precision_at_k(
        folds, k=k, column="score", resamples=bootstrap_resamples, seed=seed
    )
    baseline_p_at_k = _baseline_precision_at_k(
        usable, k=k, resamples=bootstrap_resamples, seed=seed + 1
    )

    money = _aggregate_money(scored, table)
    verdict = _verdict(
        model_p_at_k,
        baseline_p_at_k,
        intervals.get("coverage", {}),
        intervals.get("baseline_coverage", {}),
    )

    return {
        "parameters": {
            "churn_window_days": churn_window_days,
            "step_days": step_days,
            "active_window_days": active_window_days,
            "warmup_days": warmup_days,
            "precision_at_k": k,
            "flag_rule": flag_rule,
            "min_train_rows": min_train_rows,
            "min_train_churners": min_train_churners,
            "validation_date_fraction": val_date_fraction,
            "bootstrap_resamples": bootstrap_resamples,
            "seed": seed,
            "decision_cost": DECISION_COST,
            "features_used": feature_columns,
            "baseline_column": baseline_column,
        },
        "walk_forward": {
            "evaluation_points": [o.date().isoformat() for o in origins],
            "usable": len(usable),
            "skipped": [
                {"origin": f["origin"], "reason": f["reason"]}
                for f in folds
                if f["status"] == "skipped"
            ],
            "embargo": (
                "At evaluation point T only rows with as_of_date <= T - "
                f"{churn_window_days} may train the model: a row's label is not "
                "knowable until its own churn window has closed. Enforced in "
                "select_training_rows() and re-asserted on the fit frame by "
                "_assert_only_past()."
            ),
        },
        "folds": [
            {key: value for key, value in f.items() if not key.startswith("_")}
            for f in folds
        ],
        "headline": {
            "flag_rule": flag_rule,
            "lead_time_days": lead | {"ci95": intervals.get("lead_time_median_days")},
            "coverage": intervals.get("coverage", {}),
            "coverage_ceiling": oracle_coverage(
                scored, table, k=k, flag_rule=flag_rule
            ),
            "precision_at_k": model_p_at_k,
            "lift_at_k": _mean_lift(usable),
            **span_info,
        },
        "money": money,
        "missed": missed_breakdown(table, history, feature_columns),
        "baseline": {
            "column": baseline_column,
            "how_it_is_run": (
                "Ranks students by one column, direction re-derived on each fold's "
                "own training slice, flagging exactly as many students as the model "
                "flagged in that fold (equal mentor capacity)."
            ),
            "precision_at_k": baseline_p_at_k,
            "coverage": intervals.get("baseline_coverage", {}),
            "lead_time_days": baseline_lead,
        },
        "verdict": verdict,
    }


def _baseline_precision_at_k(
    folds: list[dict], *, k: int, resamples: int, seed: int
) -> dict:
    """precision@k for the rule, bootstrapped the same way as the model's.

    Kept separate from `bootstrap_precision_at_k` because the rule's ranking score
    is not in the scored-rows frame (only its flags are): the frame is the record of
    what each fold decided, and a continuous baseline score would be a fourth column
    nothing else reads.
    """
    rng = np.random.default_rng(seed)
    per_fold = []
    for fold in folds:
        rows = fold["_scored_rows"]
        per_fold.append((rows["label"].to_numpy(), rows["baseline_flagged"].to_numpy()))
    if not per_fold:
        return {"n": 0, "reportable": False}

    def mean_precision(samples) -> float:
        # The rule's flags are its decision, so precision over the flagged set IS
        # the rule's precision at the model's capacity. When the model flagged more
        # than k, this is the stricter of the two numbers.
        values = []
        for truth, flagged in samples:
            flagged = flagged.astype(bool)
            values.append(
                float(truth[flagged].mean()) if flagged.any() else 0.0
            )
        return float(np.mean(values))

    point = mean_precision(per_fold)
    resampled = []
    for _ in range(resamples):
        drawn = []
        for truth, flagged in per_fold:
            index = rng.integers(0, truth.size, truth.size)
            drawn.append((truth[index], flagged[index]))
        resampled.append(mean_precision(drawn))
    low, high = np.percentile(resampled, [2.5, 97.5])
    return {
        "value": point,
        "ci95_low": round(float(low), 4),
        "ci95_high": round(float(high), 4),
        "folds": len(per_fold),
        "resamples": resamples,
        "measured_as": "precision over the students the rule flagged, per fold, averaged",
        "reportable": bool(
            sum(int(f["_scored_rows"]["baseline_flagged"].sum()) for f in folds)
            >= BACKTEST_MIN_REPORTABLE_N
        ),
    }


def _mean_lift(folds: list[dict]) -> dict:
    values = [
        f["scored"]["lift_at_k"] for f in folds if f["scored"]["lift_at_k"] is not None
    ]
    if not values:
        return {"n": 0}
    return {
        "value": float(np.mean(values)),
        "min": float(np.min(values)),
        "max": float(np.max(values)),
        "folds": len(values),
        "meaning": "1.0 = tesadufi secimden daha iyi degil",
    }


def _aggregate_money(scored: pd.DataFrame, table: pd.DataFrame) -> dict:
    values = scored["monthly_value_try"]
    if values.isna().all():
        return {
            "available": False,
            "reason": (
                "monthly_value_try / monthly_fee_try kolonlari gelmedi, bu yuzden "
                "parasal buyukluk hesaplanamadi."
            ),
        }
    # Distinct students: a student flagged at four evaluation points is one student
    # at risk, not four, and summing per fold would quadruple the headline.
    per_student = (
        scored[scored["flagged"] == 1]
        .sort_values("origin")
        .drop_duplicates(subset=["student_id"], keep="last")
    )
    caught = table[table["flagged"] == 1] if not table.empty else table
    return {
        "available": True,
        "currency": "TRY",
        "basis": "monthly value per student",
        "flagged_students": int(len(per_student)),
        "flagged_monthly_value_try": float(
            pd.to_numeric(per_student["monthly_value_try"], errors="coerce").sum()
        ),
        "caught_churners": int(len(caught)),
        "caught_churner_monthly_value_try": (
            float(pd.to_numeric(caught["monthly_value_try"], errors="coerce").sum())
            if len(caught)
            else 0.0
        ),
        "value_at_risk_not_value_saved": NOT_VALUE_SAVED,
    }


def caveats(report: dict, *, is_synthetic: bool, history_info: dict) -> list[str]:
    """The things a reader must be told before quoting any figure above.

    In the JSON as well as on the terminal: the JSON is what a customer-facing
    report gets generated from later, and these have to travel with the numbers
    rather than be remembered by whoever writes that report.
    """
    parameters = report["parameters"]
    items = [
        f"Lead time cozunurlugu {parameters['step_days']} gundur: degerlendirme "
        f"noktalari {parameters['step_days']} gun arayla. Bundan daha kisa bir erken "
        "uyari bu olcumde gorunmez.",
        "Backtest'te kimse mudahale etmedi. Butun sayilar 'ne gorurduk' sorusunun "
        "cevabidir, 'ne kurtarirdik' sorusunun degil.",
        "Feature'larin her satirda O TARIHTE gecerli olan degerler oldugu "
        "musterinin beyanidir; bunu dosyadan dogrulayamayiz. Yanlissa butun "
        "sayilar yukari sapar (bkz. docs/LEAKAGE_AUDIT.md).",
        f"Modelin kullandigi {len(parameters['features_used'])} feature'in "
        f"temporal gecerliligi musterinin export'una baglidir; "
        f"{len(history_info['features_not_supplied'])} feature hic gelmedi.",
    ]
    if history_info["audited_out_columns_ignored"]:
        items.append(
            "Export'ta gelen ama KULLANILMAYAN kolonlar: "
            f"{history_info['audited_out_columns_ignored']} - ters nedensellik "
            "nedeniyle denetimde cikarildilar (docs/LEAKAGE_AUDIT.md)."
        )
    span = report["headline"]
    if not span["coverage"].get("reportable", False):
        items.append(
            f"Kapsama ve lead time {span['coverage'].get('n', 0)} ogrenciye dayaniyor; "
            f"{BACKTEST_MIN_REPORTABLE_N} altindaki bir olcum gurultudur ve rapora "
            "rakam olarak girmemelidir."
        )
    ceiling = span.get("coverage_ceiling", {})
    if ceiling.get("available"):
        items.append(
            f"Kapsama kapasiteyle sinirli: {parameters['precision_at_k']} kisilik "
            f"liste x {report['walk_forward']['usable']} donem = "
            f"{ceiling['flag_slots_total']} isaretleme hakki, "
            f"{ceiling['evaluable_churners']} ayrilmaya karsi. Kusursuz bir siralama "
            f"bile en fazla {ceiling['oracle_coverage']:.1%} kapsayabilirdi - kapsama "
            "rakami bu tavana gore okunmali, 100'e gore degil."
        )
    if span["churners_never_scored_before_leaving"]:
        items.append(
            f"{span['churners_never_scored_before_leaving']} ogrenci ayrilmadan once "
            "hic skorlanamadi (son gozlemleri aktiflik penceresinin disinda kaldi). "
            "Bunlar kapsama hesabina girmiyor - girerse modeli musterinin veri "
            "bosluguyla sucluyor olurduk."
        )
    if report["walk_forward"]["skipped"]:
        items.append(
            f"{len(report['walk_forward']['skipped'])} degerlendirme noktasi atlandi; "
            "nedenleri JSON'da walk_forward.skipped altinda."
        )
    if is_synthetic:
        items.insert(
            0,
            "VERI SENTETIK. Bu rapordaki hicbir sayi gercek musteri sonucu degildir "
            "ve hicbir yere gosterilemez.",
        )
    return items


# --- Terminal summary (Turkish) ----------------------------------------------
def _interval(stat: dict, *, percent: bool = False, digits: int = 3) -> str:
    if not stat or stat.get("value") is None:
        return "olculemedi"
    if percent:
        body = (
            f"{stat['value']:.1%} [{stat['ci95_low']:.1%} - {stat['ci95_high']:.1%}]"
        )
    else:
        body = (
            f"{stat['value']:.{digits}f} "
            f"[{stat['ci95_low']:.{digits}f} - {stat['ci95_high']:.{digits}f}]"
        )
    if not stat.get("reportable", True):
        body += f"  <-- n={stat.get('n', stat.get('rows', '?'))}, GURULTU"
    return body


def print_summary(report: dict, *, out_path: Path) -> None:
    """The Turkish summary a founder reads before writing the customer report."""
    width = 78
    synthetic = report["is_synthetic_data"]

    print()
    if synthetic:
        print("!" * width)
        print("SENTETIK VERI - BU RAPORDAKI HICBIR SAYI GERCEK DEGILDIR")
        print(report["synthetic_notice"])
        print("!" * width)

    print("=" * width)
    print("EO-CHURN GERIYE DONUK BACKTEST - OZET")
    print("=" * width)

    inputs = report["inputs"]
    contract = report["contract"]
    print(
        f"\nGIRDI\n"
        f"  gecmis dosyasi   : {Path(inputs['history']['path']).name}\n"
        f"  satir / ogrenci  : {inputs['history']['rows']} / "
        f"{inputs['history']['students']}\n"
        f"  donem            : {inputs['history']['first_as_of_date']} - "
        f"{inputs['history']['last_as_of_date']}\n"
        f"  sonuc dosyasi    : {Path(inputs['outcomes']['path']).name} "
        f"({inputs['outcomes']['churners']} ayrilma)\n"
        f"  sonuc dosyasi sekli: {inputs['coverage']['outcomes_file_shape']}\n"
        f"  eslesen ogrenci  : {inputs['coverage']['matched_students']} "
        f"(gecmisin %{inputs['coverage']['share_of_history_students'] * 100:.0f}'i, "
        f"sonuc kimliklerinin "
        f"%{inputs['coverage']['share_of_outcome_ids'] * 100:.0f}'i)\n"
        f"  kullanilan feature: {len(contract['features_used'])} "
        f"(gelmeyen: {len(contract['features_not_supplied'])})"
    )
    if contract["audited_out_columns_ignored"]:
        print(
            f"  kullanilmayan     : {', '.join(contract['audited_out_columns_ignored'])}"
            " (denetimde cikarildi)"
        )

    parameters = report["parameters"]
    walk = report["walk_forward"]
    rule = (
        f"her noktada en riskli {parameters['precision_at_k']} ogrenci (kapasite)"
        if parameters["flag_rule"] == "capacity"
        else "maliyet-optimal olasilik esigi"
    )
    print(
        f"\nILERI YURUYUS\n"
        f"  churn penceresi  : {parameters['churn_window_days']} gun\n"
        f"  nokta araligi    : {parameters['step_days']} gun\n"
        f"  aktiflik pencere : {parameters['active_window_days']} gun\n"
        f"  isaretleme kurali: {rule}\n"
        f"  degerlendirme    : {walk['usable']} / "
        f"{len(walk['evaluation_points'])} nokta kullanildi"
    )
    for skipped in walk["skipped"]:
        print(f"    atlandi {skipped['origin']}: {skipped['reason']}")

    headline = report["headline"]
    lead = headline["lead_time_days"]
    print("\n" + "-" * width)
    print("1) ERKEN UYARI SURESI (lead time) - SATISI YAPAN SAYI")
    print("-" * width)
    if lead.get("n"):
        print(
            f"  Isaretledigimiz {lead['n']} ayrilan ogrenci icin, ilk isaretten "
            f"ayrilmaya kadar:\n"
            f"    medyan   : {lead['median']:.0f} gun\n"
            f"    ceyrekler: Q1 {lead['q1']:.0f} gun / Q3 {lead['q3']:.0f} gun\n"
            f"    aralik   : {lead['min']} - {lead['max']} gun\n"
            f"    medyan %95 guven araligi: {_interval(lead.get('ci95'), digits=1)}"
        )
    else:
        print("  Ayrilmadan once isaretlenen ogrenci yok - lead time hesaplanamiyor.")

    print("\n" + "-" * width)
    print("2) KAPSAMA (ayrilanlarin ne kadarini en az bir kez isaretledik)")
    print("-" * width)
    print(
        f"  olculebilir ayrilma     : {headline['churners_with_a_scoring_opportunity']}"
        f" / {headline['churners_in_measured_span']}\n"
        f"  hic skorlanamayan       : "
        f"{headline['churners_never_scored_before_leaving']}\n"
        f"  kapsama                 : "
        f"{_interval(headline['coverage'], percent=True)}\n"
        f"  olculen donem           : {headline['measured_span']['from']} - "
        f"{headline['measured_span']['to']}"
    )
    ceiling = headline["coverage_ceiling"]
    if ceiling.get("available"):
        achieved = headline["coverage"].get("value") or 0.0
        share = achieved / ceiling["oracle_coverage"] if ceiling["oracle_coverage"] else 0.0
        print(
            f"  KAPASITE TAVANI         : {ceiling['oracle_coverage']:.1%} "
            f"({ceiling['flag_slots_total']} isaretleme hakki, "
            f"{ceiling['evaluable_churners']} ayrilma)\n"
            f"  tavanin ne kadari       : {share:.1%}\n"
            f"  {ceiling['note']}"
        )
    else:
        print(f"  kapasite tavani         : {ceiling.get('reason')}")

    print("\n" + "-" * width)
    print(f"3) KAPASITEYE GORE ISABET (precision@{parameters['precision_at_k']})")
    print("-" * width)
    lift = headline["lift_at_k"]
    print(
        f"  Mentorlar her donemde en riskli {parameters['precision_at_k']} ogrenciyi "
        f"arayabiliyorsa:\n"
        f"    precision@{parameters['precision_at_k']} : "
        f"{_interval(headline['precision_at_k'])}\n"
        f"    lift@{parameters['precision_at_k']}      : "
        + (f"{lift['value']:.2f}x ({lift['min']:.2f} - {lift['max']:.2f})" if lift.get("value") else "olculemedi")
        + "\n    (lift 1.0 = tesadufi secimden daha iyi degil)"
    )

    money = report["money"]
    print("\n" + "-" * width)
    print("4) PARASAL BUYUKLUK")
    print("-" * width)
    if money["available"]:
        print(
            f"  isaretlenen ogrenci       : {money['flagged_students']}\n"
            f"  aylik degeri (toplam)     : "
            f"{money['flagged_monthly_value_try']:,.0f} TL\n"
            f"  yakalanan ayrilanlar      : {money['caught_churners']}\n"
            f"  onlarin aylik degeri      : "
            f"{money['caught_churner_monthly_value_try']:,.0f} TL\n"
        )
        print("  " + NOT_VALUE_SAVED)
    else:
        print(f"  {money['reason']}")

    missed = report["missed"]
    print("\n" + "-" * width)
    print("5) KACIRDIKLARIMIZ - raporun en durust bolumu")
    print("-" * width)
    if missed.get("missed") is None or not missed.get("caught", 0) + missed.get("missed", 0):
        print(f"  {missed.get('note')}")
    else:
        print(
            f"  hic isaretlenmeyen ayrilan: {missed['missed']} / "
            f"{missed['missed'] + missed['caught']} ({missed['missed_rate']:.1%})"
        )
        if missed.get("missed_monthly_value_try"):
            print(
                f"  kacirilan aylik deger     : "
                f"{missed['missed_monthly_value_try']:,.0f} TL"
            )
        for column, levels in list(missed["by_segment"].items())[:3]:
            worst = sorted(
                (lv for lv in levels.items() if lv[1]["missed_rate"] is not None),
                key=lambda item: item[1]["missed_rate"],
                reverse=True,
            )[:3]
            label = FEATURE_LABELS.get(column, column)
            rendered = ", ".join(
                f"{level} {stat['missed_rate']:.0%} ({stat['missed']}/{stat['churners']})"
                for level, stat in worst
            )
            print(f"    en cok kacirilan {label}: {rendered}")
        for gap in missed["numeric_gaps"][:3]:
            print(
                f"    {gap['label']}: kacirilan ort. {gap['missed_mean']:.2f} vs "
                f"yakalanan {gap['caught_mean']:.2f} "
                f"(standart fark {gap['standardised_gap']:+.2f})"
            )

    baseline = report["baseline"]
    print("\n" + "-" * width)
    print("6) BASIT KURAL KARSILASTIRMASI")
    print("-" * width)
    print(
        f"  kural                 : tek kolon ile sirala -> "
        f"{baseline['column'] or 'yok'}\n"
        f"  esit kapasite         : modelin isaretledigi kadar ogrenci isaretlenir\n"
        f"  kural precision@K     : {_interval(baseline['precision_at_k'])}\n"
        f"  kural kapsama         : {_interval(baseline['coverage'], percent=True)}\n"
        f"  kural lead time medyan: "
        + (
            f"{baseline['lead_time_days']['median']:.0f} gun "
            f"(n={baseline['lead_time_days']['n']})"
            if baseline["lead_time_days"].get("n")
            else "olculemedi"
        )
    )
    print(f"\n  >>> {report['verdict']['tr']}")

    print("\n" + "-" * width)
    print("7) UYARILAR VE SINIRLAR")
    print("-" * width)
    for item in report["caveats"]:
        print(f"  - {item}")

    print("\n" + "=" * width)
    print(f"JSON: {out_path}")
    if synthetic:
        print("SENTETIK VERI - bu rapor sadece makinenin calistigini gosterir.")
    print("=" * width + "\n")


# --- CLI ---------------------------------------------------------------------
def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Retrospective backtest on a prospect's own 12-month export: which "
            "students they lost that we would have flagged, and how many days "
            "earlier."
        ),
        epilog="Run with --contract to print the full input contract.",
    )
    parser.add_argument("--history", help="history CSV: one row per (student, date)")
    parser.add_argument("--outcomes", help="outcomes CSV: who churned and when")
    parser.add_argument("--mapping", help="mapping JSON: their column name -> ours")
    parser.add_argument(
        "--contract", action="store_true", help="print the input contract and exit"
    )
    parser.add_argument("--churn-window-days", type=int, default=CHURN_WINDOW_DAYS)
    parser.add_argument("--step-days", type=int, default=BACKTEST_STEP_DAYS)
    parser.add_argument(
        "--active-window-days",
        type=int,
        default=BACKTEST_ACTIVE_WINDOW_DAYS,
        help="how stale a student's last row may be and still be scored "
        "(0 = twice the export's median observation gap)",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=0,
        help="history required behind the first evaluation point "
        "(0 = one churn window plus one step)",
    )
    parser.add_argument(
        "--k",
        type=int,
        default=PRECISION_AT_K,
        help="how many students the client can actually contact per period",
    )
    parser.add_argument(
        "--flag-rule",
        choices=("capacity", "threshold"),
        default="capacity",
        help="what counts as a flag: the top --k students at each evaluation point "
        "(capacity, the default - it is the operating point a client recognises), "
        "or the cost-optimised probability threshold the production pipeline uses",
    )
    parser.add_argument("--baseline-column", default=None)
    parser.add_argument("--min-train-rows", type=int, default=BACKTEST_MIN_TRAIN_ROWS)
    parser.add_argument(
        "--min-train-churners", type=int, default=BACKTEST_MIN_TRAIN_CHURNERS
    )
    parser.add_argument(
        "--bootstrap-resamples", type=int, default=BACKTEST_BOOTSTRAP_RESAMPLES
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--allow-partial-outcomes",
        action="store_true",
        help="accept an outcomes file that covers few of the history's students",
    )
    parser.add_argument("--out", default=None, help="where to write the JSON report")
    return parser.parse_args(argv)


def build_report(args: argparse.Namespace) -> tuple[dict, Path]:
    """Validate the inputs, run the walk-forward, return (report, output path)."""
    missing = [
        name
        for name in ("history", "outcomes", "mapping")
        if not getattr(args, name)
    ]
    if missing:
        raise BacktestInputError(
            f"--{', --'.join(missing)} is required. Run "
            "`python scripts/backtest.py --contract` for what the files must contain."
        )

    # A non-positive step would make `evaluation_origins` loop forever and a k of 0
    # would flag nobody at every point - both produce a report rather than an error,
    # which is the failure mode this whole script exists to avoid.
    positive = {
        "--churn-window-days": args.churn_window_days,
        "--step-days": args.step_days,
        "--k": args.k,
        "--bootstrap-resamples": args.bootstrap_resamples,
    }
    not_positive = {name: value for name, value in positive.items() if value < 1}
    if not_positive:
        raise BacktestInputError(
            "these must all be at least 1: "
            + ", ".join(f"{name}={value}" for name, value in not_positive.items())
            + "."
        )

    mapping_path = Path(args.mapping)
    mapping = load_mapping(mapping_path)
    history, history_info = read_history(args.history, mapping, mapping_path)
    churn_dates, outcomes_info = read_outcomes(args.outcomes, mapping, mapping_path)
    coverage = check_outcome_coverage(
        history,
        churn_dates,
        history_path=Path(args.history),
        outcomes_path=Path(args.outcomes),
        allow_partial=args.allow_partial_outcomes,
    )

    is_synthetic = detect_synthetic(
        pd.read_csv(args.history, nrows=5), pd.read_csv(args.outcomes, nrows=5)
    )
    if is_synthetic:
        logger.warning(
            "the input files carry %s=%s - this run is SYNTHETIC and none of its "
            "numbers may be shown to anyone",
            SYNTHETIC_MARKER_COLUMN,
            SYNTHETIC_MARKER_VALUE,
        )

    feature_columns = history_info["features_used"]
    baseline_column, baseline_reason = choose_baseline_column(
        history, feature_columns, requested=args.baseline_column
    )

    active_window = args.active_window_days
    if active_window <= 0:
        gap = history_info["median_observation_gap_days"]
        # Twice the cadence: one gap would make a student inactive the moment their
        # own grid is a day off the evaluation grid.
        active_window = int(max(2 * (gap or 14), 14))
        logger.info(
            "active window derived from the export: %d day(s) (median observation "
            "gap %s)",
            active_window,
            gap,
        )
    warmup = args.warmup_days or (args.churn_window_days + args.step_days)

    body = run_backtest(
        history,
        churn_dates,
        feature_columns=feature_columns,
        churn_window_days=args.churn_window_days,
        step_days=args.step_days,
        active_window_days=active_window,
        warmup_days=warmup,
        k=args.k,
        baseline_column=baseline_column,
        flag_rule=args.flag_rule,
        min_train_rows=args.min_train_rows,
        min_train_churners=args.min_train_churners,
        val_date_fraction=BACKTEST_VAL_DATE_FRACTION,
        bootstrap_resamples=args.bootstrap_resamples,
        seed=args.seed,
    )

    report = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "generated_by": "scripts/backtest.py",
        "is_synthetic_data": is_synthetic,
        "inputs": {
            "history": history_info,
            "outcomes": outcomes_info,
            "mapping": {"path": str(mapping_path), "sha256": _sha256(mapping_path)},
            "coverage": coverage,
        },
        "contract": {
            "mapped_history_columns": mapping.get("history", {}),
            "mapped_outcome_columns": mapping.get("outcomes", {}),
            "features_used": feature_columns,
            "features_not_supplied": history_info["features_not_supplied"],
            "audited_out_columns_ignored": history_info["audited_out_columns_ignored"],
            "unrecognised_columns_ignored": history_info["unrecognised_columns_ignored"],
            "baseline_column_chosen_because": baseline_reason,
        },
        **body,
    }
    if is_synthetic:
        report["synthetic_notice"] = (
            "Girdi dosyalari scripts/make_synthetic_history.py tarafindan uretildi "
            f"({SYNTHETIC_MARKER_COLUMN} kolonu). Bu rapor sadece backtest "
            "mekanizmasinin calistigini gosterir; dogrulukla ilgili hicbir sey "
            "kanitlamaz."
        )
    report["caveats"] = caveats(
        report, is_synthetic=is_synthetic, history_info=history_info
    )

    if args.out:
        out_path = Path(args.out)
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        prefix = "SENTETIK_" if is_synthetic else ""
        out_path = Path(BACKTEST_OUTPUT_DIR) / f"{prefix}backtest_{stamp}.json"
    return report, out_path


def write_report(report: dict, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(to_external(report), f, indent=2, ensure_ascii=False)
        f.write("\n")
    return out_path


def main(argv=None) -> int:
    configure_logging()
    args = parse_args(argv)
    if args.contract:
        print(CONTRACT)
        return 0
    try:
        report, out_path = build_report(args)
    except BacktestInputError as e:
        # Printed rather than raised: a traceback above this message buries the one
        # line that says what to fix.
        print(f"\nGIRDI HATASI / INPUT ERROR\n\n{e}\n", file=sys.stderr)
        return 2
    write_report(report, out_path)
    print_summary(report, out_path=out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
