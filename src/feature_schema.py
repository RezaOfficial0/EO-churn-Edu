"""The feature contract this API serves, as data a consumer can check itself.

Two things outside this repo read our scored output and have to know what the
feature set IS: the dashboard (labels) and the retention app (one rule per
feature). Until now neither could ask - both kept a hand-written copy of
`config.FEATURES`, and a copy drifts silently.

It already happened. B-21 took three columns out of FEATURES; nothing failed,
no endpoint changed shape, and the retention app's `feature_rules.json` simply
stopped covering the features the model now reports - so half the students fell
back to a generic action. A silent downgrade, not an error.

`feature_set_hash` is the cheap fix for that whole class of bug: a consumer
pins the hash it was written against and compares one string. When FEATURES
changes the comparison fails loudly, in that consumer's own test suite, before
anyone ships a half-generic campaign.

Nothing here is secret: it is the shape of the request body a client already has
to construct. Training statistics (imputation medians), file paths, data hashes
and the error breakdown stay in /metrics' allow-list and out of this module.
"""
import hashlib

from config import (
    CAT_COLS,
    CATEGORICAL_LEVELS,
    FEATURE_BOUNDS,
    FEATURE_LABELS,
    FEATURES,
    FLAG_FEATURES,
    INTEGER_FEATURES,
    SHAP_TOP_N_FEATURES,
    STUDENT_INFO,
)

SCHEMA_VERSION = 1


def feature_set_hash() -> str:
    """Stable fingerprint of the feature set, order-independent.

    Sorted, so reordering FEATURES (which changes nothing for a consumer) does
    not look like a contract change, while adding or removing a column does.
    Newline-joined rather than concatenated so that ["ab", "c"] and ["a", "bc"]
    cannot collide.
    """
    joined = "\n".join(sorted(FEATURES))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def _feature_entry(name: str) -> dict:
    if name in CAT_COLS:
        kind = "categorical"
    elif name in FLAG_FEATURES:
        kind = "flag"
    else:
        kind = "number"

    entry = {
        "name": name,
        # The label a human reads. Per client, like FEATURES itself - which is
        # exactly why a consumer must not keep its own copy.
        "label": FEATURE_LABELS.get(name, name),
        "type": kind,
        # A flag says "this value was imputed, not measured". A consumer that
        # shows the number without the flag is claiming a measurement we never
        # made, so the flag has to be discoverable from here.
        "is_flag": kind == "flag",
    }

    if kind == "categorical":
        entry["levels"] = list(CATEGORICAL_LEVELS.get(name, []))
    else:
        bounds = FEATURE_BOUNDS.get(name)
        if bounds is not None:
            entry["min"], entry["max"] = bounds
        # A flag is 0 or 1 and nothing else. Reporting `integer: false` for one
        # (which is what INTEGER_FEATURES alone would say, since flags are listed
        # separately there) invites a client to send 0.5 and be surprised.
        entry["integer"] = kind == "flag" or name in INTEGER_FEATURES

    return entry


def feature_schema() -> dict:
    """The full contract: identity columns, every feature, and the fingerprint."""
    return {
        "schema_version": SCHEMA_VERSION,
        "feature_set_hash": feature_set_hash(),
        "id_field": STUDENT_INFO[0],
        # Returned next to the features on /students and /predict, never model
        # input. A consumer that allow-lists feature names needs to know these
        # exist so it does not reject the record for carrying them.
        "info_fields": list(STUDENT_INFO),
        "features": [_feature_entry(name) for name in FEATURES],
        # How many SHAP reasons a scored record carries, so a consumer can size
        # its own validation instead of guessing from one sample.
        "top_reasons_count": SHAP_TOP_N_FEATURES,
    }
