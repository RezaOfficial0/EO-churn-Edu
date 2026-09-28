import numpy as np
import pandas as pd
import pytest

from src.model.threshold import select_threshold


def test_picks_the_cost_minimising_threshold():
    # 100 rows: probs equal the label plus a little noise, so a threshold near 0.5
    # separates them cleanly.
    y = np.array([0, 1] * 50)
    proba = np.where(y == 1, 0.8, 0.2)

    chosen = select_threshold(y, proba, cost_false_alarm=1, cost_missed_churn=1)
    assert 0.2 < chosen["threshold"] <= 0.8
    assert chosen["false_alarms"] == 0
    assert chosen["missed_churns"] == 0


def test_high_cost_of_missing_pushes_the_threshold_down():
    y = np.array([0, 1] * 50)
    proba = np.where(y == 1, 0.55, 0.45)  # weak separation

    cheap_misses = select_threshold(y, proba, cost_false_alarm=1, cost_missed_churn=1)
    expensive_misses = select_threshold(y, proba, cost_false_alarm=1, cost_missed_churn=50)

    assert expensive_misses["threshold"] <= cheap_misses["threshold"]
    assert expensive_misses["missed_churns"] <= cheap_misses["missed_churns"]


# --- the contracts a refactor must not change quietly (B-26) ------------------


def _counts_at(y, proba, threshold):
    """What `select_threshold` should have counted, recomputed with `>=`."""
    predicted = np.asarray(proba) >= threshold
    y = np.asarray(y).astype(int)
    return {
        "false_alarms": int((predicted & (y == 0)).sum()),
        "missed_churns": int((~predicted & (y == 1)).sum()),
    }


def test_a_student_exactly_at_the_threshold_is_flagged():
    """The `>=` boundary, pinned where it is the only thing that matters.

    y=[1, 0] with probabilities [0.30, 0.29]: t=0.30 is the ONLY threshold on the
    0.01..0.99 grid that costs nothing, and only because 0.30 >= 0.30 flags the
    churner. Under `>` no threshold would be free and the sweep would settle on 0.01,
    so this test tells the two rules apart rather than merely exercising one.
    """
    chosen = select_threshold([1, 0], [0.30, 0.29], cost_false_alarm=5, cost_missed_churn=5)

    assert chosen["threshold"] == 0.30
    assert chosen["expected_cost"] == 0
    assert (chosen["false_alarms"], chosen["missed_churns"]) == (0, 0)


def test_a_cost_plateau_resolves_to_its_lowest_threshold():
    """Today's tie-break, stated exactly: the FIRST minimum on the ascending sweep.

    Cleanly separated scores at 0.2 / 0.8 make every threshold in 0.21..0.80 cost
    zero, and `total_cost < best` (strict) keeps the earliest of them - the most
    aggressive cut of the plateau, not its middle. B-26's acceptance criteria
    describe picking the plateau's MIDDLE, which this code does not do; pinned here
    so that policy change shows up as a failing test instead of a silent shift in
    every customer's alert volume.
    """
    y = np.array([0, 1] * 50)
    proba = np.where(y == 1, 0.8, 0.2)

    assert select_threshold(y, proba, 1, 1)["threshold"] == 0.21


def test_the_reported_counts_match_the_chosen_threshold():
    """The mistakes in the dict are the mistakes at that threshold - the training
    summary and model_meta.json quote them without recomputing anything."""
    rng = np.random.default_rng(0)
    proba = rng.random(300)
    y = (rng.random(300) < proba).astype(int)

    chosen = select_threshold(y, proba, cost_false_alarm=1, cost_missed_churn=3)
    expected = _counts_at(y, proba, chosen["threshold"])

    assert chosen["false_alarms"] == expected["false_alarms"]
    assert chosen["missed_churns"] == expected["missed_churns"]
    assert chosen["expected_cost"] == expected["false_alarms"] + 3 * expected["missed_churns"]


def test_the_threshold_always_comes_off_the_one_percent_grid():
    """Anything outside 0.01..0.99 in steps of 0.01 means the sweep changed: the
    threshold is written into model_meta.json and compared against in the API."""
    rng = np.random.default_rng(1)
    for cost_missed in (1, 3, 20):
        chosen = select_threshold(
            (rng.random(200) < 0.3).astype(int), rng.random(200), 1, cost_missed
        )
        assert 0.01 <= chosen["threshold"] <= 0.99
        assert round(chosen["threshold"] * 100) == pytest.approx(chosen["threshold"] * 100)
        assert isinstance(chosen["threshold"], float)
        assert isinstance(chosen["expected_cost"], int)


def test_how_many_students_get_flagged_only_ever_grows_with_the_cost_of_a_miss():
    """There is no capacity argument (see the note in the report): the cost ratio is
    the only lever on alert volume, so it has to be a monotone one. Weak separation
    on purpose - with a clean split every ratio picks the same free threshold."""
    rng = np.random.default_rng(2)
    y = (rng.random(400) < 0.3).astype(int)
    proba = np.clip(0.45 + 0.1 * y + rng.normal(0, 0.05, 400), 0.01, 0.99)

    flagged = [
        int((proba >= select_threshold(y, proba, 1, cost_missed)["threshold"]).sum())
        for cost_missed in (1, 2, 5, 10, 50)
    ]
    assert flagged == sorted(flagged)


def test_a_validation_set_with_no_churners_stops_flagging_instead_of_flagging_all():
    """A pilot month where nobody left. The cheapest rule is to alert on nobody, and
    a threshold above every score is how this function says that."""
    proba = np.full(10, 0.9)
    chosen = select_threshold(np.zeros(10, dtype=int), proba, 1, 3)

    assert chosen["threshold"] > proba.max()
    assert (chosen["expected_cost"], chosen["false_alarms"]) == (0, 0)


def test_a_validation_set_where_everyone_churned_flags_everyone():
    chosen = select_threshold(np.ones(10, dtype=int), np.full(10, 0.1), 1, 3)

    assert chosen["threshold"] <= 0.1
    assert (chosen["expected_cost"], chosen["missed_churns"]) == (0, 0)


def test_lists_and_a_reindexed_series_work_the_same_as_arrays():
    """`select_threshold` is called with `y_val` straight out of a stratified split,
    which is a Series with a shuffled, non-contiguous index."""
    y = [0, 1, 0, 1]
    proba = [0.1, 0.9, 0.2, 0.8]
    series = pd.Series(y, index=[17, 4, 99, 3])

    assert select_threshold(y, proba, 1, 3) == select_threshold(
        series, pd.Series(proba, index=series.index), 1, 3
    )
