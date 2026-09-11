"""Turn a run's alert rows into the message a mentor actually reads.

Pure functions: no network, no database, no config side effects - which is what
makes the wording testable and the channels dumb.

The alert log stores `top_reasons_detail` as [{feature, impact}, ...] - the SHAP
contribution, not the value behind it. A mentor needs the value ("41 gün"), so
`build_message` takes today's student frame and joins the two.
"""
import re
from datetime import datetime
from html import escape

import pandas as pd

from config import FEATURE_LABELS, NOTIFY_TITLE, STUDENT_INFO

_ID_COLUMN = STUDENT_INFO[0]

# How many at-risk students get the full treatment before the message just counts
# the rest. Telegram caps a message at 4096 characters.
MAX_DETAILED_STUDENTS = 10


def label_for(feature: str) -> str:
    """Turkish label for a feature, falling back to the raw column name."""
    return FEATURE_LABELS.get(feature, feature)


def format_value(feature: str, value) -> str:
    """Render one feature value the way a person would write it."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or pd.isna(value):
        return "veri yok"
    if isinstance(value, str):
        return value
    number = float(value)
    # The 0/1 missing-flags read as a yes/no, not as a number.
    if feature.endswith("_missing"):
        return "evet" if number else "hayır"
    if number == int(number):
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


# "days_since_last_contact (+0.91), satisfaction_survey_score (-0.17)"
_REASON_TEXT = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\s*\(([+-]?\d*\.?\d+)\)")


def parse_reasons(row) -> list[dict]:
    """The row's reasons as [{feature, impact}], from whichever column carries them.

    The database keeps `top_reasons_detail` as JSONB, but the CSV alert log only
    has the display string `top_reasons` - so in CSV mode it is parsed back. The
    string is produced by this codebase, so its shape is known, not guessed.
    """
    detail = row.get("top_reasons_detail")
    if isinstance(detail, list) and detail:
        return detail
    text = row.get("top_reasons")
    if not isinstance(text, str):
        return []
    return [
        {"feature": feature, "impact": float(impact)}
        for feature, impact in _REASON_TEXT.findall(text)
    ]


def _reason_lines(reasons, student_values: dict) -> list[str]:
    """One line per SHAP reason: label, value, and which way it pushes risk."""
    lines = []
    for reason in reasons or []:
        feature = reason.get("feature")
        if feature is None:
            continue
        direction = "riski artırıyor" if reason.get("impact", 0) > 0 else "riski azaltıyor"
        value = format_value(feature, student_values.get(feature))
        lines.append(f"{label_for(feature)}: {value} — {direction}")
    return lines


def _student_values(students: pd.DataFrame | None) -> dict[str, dict]:
    """{student_id: {feature: value}} for today's students, or {} if unavailable.

    Used only to put a number next to each reason; the message degrades to
    "veri yok" rather than failing when the frame is missing a student.
    """
    if students is None or students.empty or _ID_COLUMN not in students.columns:
        return {}
    indexed = students.set_index(students[_ID_COLUMN].astype(str))
    return {student_id: row.to_dict() for student_id, row in indexed.iterrows()}


def build_message(
    new_alerts: pd.DataFrame,
    *,
    still_at_risk: pd.DataFrame | None = None,
    students: pd.DataFrame | None = None,
    run_at: datetime | None = None,
) -> tuple[str, str]:
    """Return (subject, body) as plain text.

    `new_alerts` are the students flagged for the first time this run - the ones
    a mentor should act on today. `still_at_risk` were already flagged in the
    previous run; they are summarised in one line rather than repeated in full,
    so a daily message does not become noise a week in.
    """
    run_at = run_at or datetime.now()
    values = _student_values(students)
    new_count = 0 if new_alerts is None else len(new_alerts)
    repeat_count = 0 if still_at_risk is None else len(still_at_risk)
    total = new_count + repeat_count

    date = run_at.strftime("%d.%m.%Y")
    if total == 0:
        subject = f"{NOTIFY_TITLE} — {date} — risk altında öğrenci yok"
        return subject, f"{NOTIFY_TITLE}\n{date}\n\nBugün risk eşiğinin üzerinde öğrenci yok."

    subject = f"{NOTIFY_TITLE} — {date} — {new_count} yeni, {total} toplam"
    lines = [
        NOTIFY_TITLE,
        f"{date} · {total} öğrenci risk altında, {new_count} tanesi yeni.",
    ]

    if new_count:
        lines += ["", "YENİ RİSKLİ ÖĞRENCİLER"]
        shown = new_alerts.head(MAX_DETAILED_STUDENTS)
        for position, (_, row) in enumerate(shown.iterrows(), start=1):
            student_id = str(row[_ID_COLUMN])
            probability = float(row["churn_probability"])
            lines.append(f"\n{position}. {student_id} — ayrılma olasılığı %{probability * 100:.0f}")
            for line in _reason_lines(parse_reasons(row), values.get(student_id, {})):
                lines.append(f"   - {line}")
        if new_count > len(shown):
            lines.append(f"\n... ve {new_count - len(shown)} öğrenci daha.")

    if repeat_count:
        ids = ", ".join(
            f"{row[_ID_COLUMN]} (%{float(row['churn_probability']) * 100:.0f})"
            for _, row in still_at_risk.iterrows()
        )
        lines += ["", f"ÖNCEKİ KOŞUDA DA UYARI VERİLMİŞTİ ({repeat_count})", ids]

    return subject, "\n".join(lines)


def build_html(
    new_alerts: pd.DataFrame,
    *,
    still_at_risk: pd.DataFrame | None = None,
    students: pd.DataFrame | None = None,
    run_at: datetime | None = None,
) -> str:
    """The same content as HTML, for the email channel.

    Deliberately plain inline-styled HTML: email clients ignore <style> blocks and
    external CSS, and half of them strip anything clever.
    """
    run_at = run_at or datetime.now()
    values = _student_values(students)
    new_count = 0 if new_alerts is None else len(new_alerts)
    repeat_count = 0 if still_at_risk is None else len(still_at_risk)
    total = new_count + repeat_count
    date = run_at.strftime("%d.%m.%Y")

    font = "font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif"
    parts = [
        f'<div style="{font};max-width:640px;color:#1a1a1a">',
        f'<h2 style="margin:0 0 4px">{escape(NOTIFY_TITLE)}</h2>',
    ]

    if total == 0:
        parts += [
            f'<p style="color:#666;margin:0 0 16px">{date}</p>',
            "<p>Bugün risk eşiğinin üzerinde öğrenci yok.</p>",
            "</div>",
        ]
        return "".join(parts)

    parts.append(
        f'<p style="color:#666;margin:0 0 20px">{date} · <strong>{total}</strong> öğrenci '
        f"risk altında, <strong>{new_count}</strong> tanesi yeni.</p>"
    )

    if new_count:
        parts.append('<h3 style="margin:0 0 12px">Yeni riskli öğrenciler</h3>')
        for _, row in new_alerts.head(MAX_DETAILED_STUDENTS).iterrows():
            student_id = str(row[_ID_COLUMN])
            probability = float(row["churn_probability"]) * 100
            parts.append(
                '<div style="border-left:3px solid #c0392b;padding:2px 0 2px 12px;margin:0 0 16px">'
                f'<div style="font-weight:600">{escape(student_id)} '
                f'<span style="color:#c0392b">— ayrılma olasılığı %{probability:.0f}</span></div>'
                '<ul style="margin:6px 0 0;padding-left:18px;color:#444">'
            )
            for line in _reason_lines(parse_reasons(row), values.get(student_id, {})):
                parts.append(f"<li>{escape(line)}</li>")
            parts.append("</ul></div>")

    if repeat_count:
        ids = ", ".join(
            f"{escape(str(row[_ID_COLUMN]))} (%{float(row['churn_probability']) * 100:.0f})"
            for _, row in still_at_risk.iterrows()
        )
        parts.append(
            f'<h3 style="margin:24px 0 8px">Önceki koşuda da uyarı verilmişti ({repeat_count})</h3>'
            f'<p style="color:#666;margin:0">{ids}</p>'
        )

    parts.append("</div>")
    return "".join(parts)
