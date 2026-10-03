# The retrospective backtest: what we offer a prospect, and what it may claim

An investor refused to watch the product demo. The verdict was not about the
product: *no real-world use case, no real customer data, so nothing else matters.*
There is no answer to that from inside the repo, and there is no answer that starts
with "integrate with us first".

So the offer is the other way round:

> Send us an anonymised export of your last 12 months. In two weeks we will show
> you which students you lost that we would have flagged, how many days earlier,
> and which ones we would have missed.

The prospect commits to nothing — no integration, no deployment, no access to their
systems. Two CSVs. `scripts/backtest.py` turns them into the evidence.

This document is the design. The authoritative column list is
`python scripts/backtest.py --contract`, which prints it from the same constants
the script validates against, so it cannot drift from the code the way a copy in
prose would.

---

## Why the existing pipeline cannot do this

`pipeline/training_pipeline.py` trains and evaluates on one snapshot with a random
stratified split (`src/data/preprocess.split_train_val_test`). Every row in that
split is contemporaneous, so a row from "later" can perfectly well train the model
that scores a row from "earlier". For the ordinary question — *does this model
separate churners from non-churners?* — that is fine.

For a backtest it is fatal, because the claim being measured is **we would have
known this in advance**. A split that ignores time answers a different question and
answers it flatteringly.

`docs/LEAKAGE_AUDIT.md` is the house record of how easily a churn metric flatters
itself: three columns had to be dropped, and PR-AUC fell 0.520 → 0.369 as a result.
That audit is about the *feature* axis — was this number knowable before the window
opened. This script is the same discipline on the *time* axis.

---

## The walk-forward scheme

Parameters: churn window **W** (`--churn-window-days`, default
`config.CHURN_WINDOW_DAYS` = 30), step **S** (`--step-days`, default 30), active
window **A** (`--active-window-days`, default: twice the export's own median
observation gap).

Evaluation points ("origins") are laid out **backwards** from the last usable date,
`max(as_of_date) − W`, which is the last date whose look-ahead window is fully
observed inside the export. Backwards rather than forwards so that the most
data-rich folds exist at every setting of `--step-days`, and changing the step does
not shuffle which months get measured. The first origin needs
`W + S` days of history behind it (`--warmup-days`).

At each origin **T**:

| step | rule | enforced in |
|---|---|---|
| training rows | `as_of_date <= T − W` | `select_training_rows()` |
| training labels | churned in `(as_of_date, as_of_date + W]` | `label_rows()` |
| calibration / threshold | the latest dates *inside* that training slice | `_time_ordered_validation_split()` |
| imputation medians | fitted on that training slice only | `fit_preprocessor()` |
| scored at T | each active student's most recent row with `as_of_date <= T` | `select_scoring_rows()` |
| outcome | churned in `(T, T + W]` | `label_rows()` |

### The embargo, and why `as_of_date <= T` is not enough

A row dated `d` carries the label "did this student churn in `(d, d + W]`". That
label is **not known** until `d + W`. A row dated `T − 5` therefore has a window
that is still open at `T`: training on it hands the model an outcome that had not
happened yet.

So the cut is `d + W <= T`, i.e. `as_of_date <= T − W`. The gap between `T − W` and
`T` is dead space at every origin — rows that exist, are in the past, and still may
not be used. That is the whole scheme in one line, and it is the one thing most
likely to be "simplified" by a later refactor.

### Where the guarantee lives in code

- `select_training_rows()` applies the embargo and calls `_assert_only_past()`.
- `run_fold()` calls `_assert_only_past()` **again** on the exact frames handed to
  `model.fit` and to the calibrator, and once more on the scoring frame against its
  own origin.
- A violation raises `TemporalLeakageError` and kills the run. It is never a
  warning: a backtest that sees the future reports a *better* number than a correct
  one, so nothing downstream would ever complain.

The assertion is on the data rather than trusted from the call site, which is what
makes it survive a refactor of the selection.

`tests/test_backtest.py::test_run_fold_does_not_let_a_future_row_influence_a_past_decision`
builds a history in which one feature column is uniform noise on every date the
embargo allows and "this student leaves within 60 days" on every date it does not.
A correct run scores precision@20 = 0.00 against a 10% base rate;
`test_removing_the_embargo_makes_the_fold_score_perfectly` removes both the embargo
and the guard on the same data and scores **1.00**. Without that second test the
first could be passing because the fixture has no learnable leak in it.

---

## Required versus optional, and why the required list is two columns long

Every required column is a reason for a prospect to give up, and a prospect who
gives up is worth less than a backtest run on nine features instead of twenty. So:

- **history**: `student_id` and `as_of_date`. Nothing else.
- **outcomes**: `student_id` and `churn_date`.

Every model feature is optional. A feature that did not arrive is listed in the
report and simply not used; the run only refuses when fewer than
`config.BACKTEST_MIN_FEATURES` (3) arrive, because below that there is nothing to
train and a two-feature model that produces a chart is a worse outcome for a sales
conversation than an email asking for three more columns.

The one thing we cannot check and the one thing that makes the result mean anything:
**each row's feature values must be the values as they stood on that row's
`as_of_date`.** That is the prospect's assertion. It is stated as a caveat inside
every report, in the client's own language, next to the numbers it affects.

Columns in `config.AUDITED_OUT_FEATURES` are recognised, reported, and **not used** —
the reverse-causality argument in `docs/LEAKAGE_AUDIT.md` applies to a client's
export exactly as it applied to ours. The synthetic demo files deliberately contain
them so that this can be seen happening rather than asserted.

---

## The two operating points

What counts as "a flag" is a choice, and it changes every headline, so the report
publishes both and names which one it used.

- **capacity** (`--flag-rule capacity`, the default) — the top `--k` students at
  each origin. This is the operating point a client recognises: *our mentors work
  the top 20 names each month.*
- **threshold** (`--flag-rule threshold`) — the cost-optimised probability cut-off
  the production pipeline uses (`src/model/threshold.py`, `config.DECISION_COST`).

On a 30-day window the base rate is 3–9%, and at a 1:3 false-alarm / missed-churn
cost an outreach only pays above 25% precision — which no honest model reaches at
that base rate. The threshold rule therefore flags almost nobody, and a coverage
figure computed from it would describe the cost setting rather than the model. Both
counts appear per fold either way.

### Coverage has a ceiling, and the ceiling is arithmetic

A client with 900 departures a year and mentors who can work 20 names a month
cannot be told "you only caught 12%" as though the other 88% were a model failure.
`oracle_coverage()` therefore computes what a **perfect ranking** would have covered
at the same capacity, by spending each origin's slots greedily on churners not yet
covered. The report prints the achieved coverage, that ceiling, and the achieved
share of it. Coverage read against 100% instead of against the ceiling is the single
easiest way to misrepresent this product in either direction.

---

## What the report will not claim

- **Lead time** is reported as a distribution (median, quartiles, range), not a
  mean, and its resolution is `--step-days`: with a 30-day step, a flag that came
  10 days early and one that came 30 days early are the same measurement.
- **Money** is "value at risk and visible" — flagged students × their monthly
  value. Nobody intervened in a backtest, nobody was called, so there is no saved
  revenue to report and no key in the JSON that could be read as one. The
  disclaimer is a field in the JSON, not only a line on the terminal, because the
  customer-facing report is generated from that file later.
- **Every headline carries a 95% bootstrap interval**, resampled at student level so
  one student cannot become two observations — the same approach as
  `precision_at_k_stability()` in `scripts/compare_feature_sets.py`, for the same
  reason set out in the "precision@20 is twenty coin flips" section of
  `docs/LEAKAGE_AUDIT.md`. A figure measured on fewer than
  `config.BACKTEST_MIN_REPORTABLE_N` (30) students is published with
  `reportable: false` and described in words as noise.
- **Churners we never scored** (their last observation fell outside the active
  window before they left) are counted and published separately, and are *not* held
  against coverage. Counting them would blame the model for a gap in the client's
  own export.
- **A skipped fold is reported with its reason.** "We measured nine months" and "we
  measured nine months and three of them were unusable" are different claims.
- **The baseline can win.** A one-column rule is run on the same folds, with its
  direction re-derived on each fold's own training slice and flagging exactly as
  many students as the model did, so the comparison is at equal mentor capacity
  rather than at an arbitrary cut-off. When the intervals overlap the verdict says
  the difference *cannot be measured on this data*; when the rule wins, the verdict
  says the model lost, in plain Turkish, at the top of section 6. A backtest that
  cannot embarrass us is worthless as evidence.

---

## Demonstrating it before any client data exists

There is no historical data in this repo — `data/mentorluk_churn_veriseti.csv` is a
single snapshot, itself synthetic. `scripts/make_synthetic_history.py` fabricates
the missing time axis from that snapshot's own statistics so the backtest can be
run, tested and shown today.

The output is built to be unmistakable: `SENTETIK_` filenames, a
`config.SYNTHETIC_MARKER_COLUMN` on **every row of both CSVs**, the same notice in
the mapping JSON, a plain-text OKUBENI beside them, and a `SENTETIK` banner above
and below every figure the backtest prints from them. The marker is a column rather
than a filename convention because a filename survives exactly one "let me just
rename this before I forward it".

What those files can demonstrate: that the walk-forward machinery runs, over real
folds, from two CSVs to a JSON report. What they cannot demonstrate: anything about
accuracy. The churners' features are drifted towards their snapshot values over the
`--drift-days` before they leave **by the generator**. A model finding that drift
again is arithmetic, not evidence — and `--drift-days` is also a hard ceiling on any
lead time those files can show, which is why it is printed in the OKUBENI and in the
generator's own summary.

---

## Running it

```bash
python scripts/backtest.py --contract          # the input contract, in full

python scripts/make_synthetic_history.py       # writes data/SENTETIK_backtest_*

python scripts/backtest.py \
    --history  data/SENTETIK_backtest_history.csv \
    --outcomes data/SENTETIK_backtest_outcomes.csv \
    --mapping  data/SENTETIK_backtest_mapping.json
```

The JSON lands in `metrics/` (prefixed `SENTETIK_` when the inputs are synthetic)
and the readable summary prints in Turkish. The JSON is what a customer report gets
generated from; this script does not write the report.
