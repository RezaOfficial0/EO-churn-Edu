# Logs: what is in them, and how long they are kept

The data subjects here are mostly under-age students, so a log is personal data
processing like any other. This page says what the system writes, what it
deliberately does not, and how long a deployment may keep it.

## Format

```
2026-09-23 09:14:02 INFO     api.main [3f9c1ab77d02] | GET /predict/{student_id} -> 200 (61 ms)
```

| Part | Note |
|---|---|
| `[3f9c1ab77d02]` | request id — random per request, not derived from the data. It is what ties an access line to a traceback logged while serving the same request (`src/logging_setup.py`) |
| the path | the **route template**, never the values in it. `GET /predict/STU300001` used to be written verbatim; that is a plain-text student id in an unrotated file |
| an unmatched path | truncated to its first segment (`/no-such-route/...`), because a 404 path is whatever the caller typed and may still carry an id |

## What is deliberately absent

- **Student ids in access lines.** See above. The request id replaces them for
  correlation.
- **Student ids in validation failures.** `validate()` logs the *count* of duplicate
  ids, not the ids (`src/data/validation.py`). Duplicates are findable in the source
  data the operator already has.
- **Student ids and internal column names in 4xx bodies.** The caller is told what
  kind of thing was wrong; the detail goes to the log.
- **File paths in `GET /health`.** It is the one unauthenticated endpoint.

## What is still in them

- **Tracebacks of unhandled exceptions**, logged by `api.main`'s handler. A pandas
  or CatBoost exception message can contain data values, so a traceback is not safe
  to forward to a third-party log service without review.
- **Row and column counts**, thresholds, timings, and the number of at-risk students
  per run. None of these identify anyone.

## Retention

| Log | Where | Keep for |
|---|---|---|
| API access + application log | container stdout, collected by whatever runs the container | **30 days**, then delete |
| daily pipeline / notification runs | same | **30 days**, then delete |
| the alert log (`alerts` table / `daily_alerts.csv`) | the database or `data/` | **not a log** — it is the record of what the system told mentors, and it is covered by the data-retention decision for student data, not by this page |

Thirty days is long enough to investigate an incident reported a few weeks late and
short enough that a leaked archive is of limited use. Nothing in this repo enforces
it yet: with Docker it is a `logging` driver option (for example
`max-size` / `max-file` on `json-file`), and with systemd a journald retention
setting. A deployment that forwards logs off the machine has to apply the same limit
there, and has to take the traceback caveat above into account before doing so.
