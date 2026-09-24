"""B-09: model_meta.json must not carry absolute paths.

The sidecar travels with the model into a Docker image and onto a customer machine,
where the trainer's home directory is both meaningless and a disclosure.
"""
import json

from config import BASE_DIR, MODEL_META_PATH
from pipeline.training_pipeline import _relative_to_base


def test_paths_inside_the_repo_become_relative():
    assert _relative_to_base(BASE_DIR / "data" / "x.csv") == "data/x.csv"
    assert _relative_to_base(str(BASE_DIR / "saved_models" / "calibrator.joblib")) == (
        "saved_models/calibrator.joblib"
    )


def test_a_path_outside_the_repo_stays_absolute():
    """A file genuinely elsewhere is reported as elsewhere, not as a ../../.. chain."""
    outside = _relative_to_base("/tmp/some-export.csv")
    assert outside.startswith("/")
    assert ".." not in outside


def test_the_committed_meta_has_no_absolute_paths():
    meta = json.loads(open(MODEL_META_PATH, encoding="utf-8").read())
    for field in ("data_file", "calibrator_path"):
        assert not meta[field].startswith("/"), field
