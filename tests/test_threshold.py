import numpy as np

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
