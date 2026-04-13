"""
Weekly PDF report generator.
Converts the weekly digest into a branded PDF document.

Usage:
    python -m pipeline.pdf_report
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable,
)

from config.settings import COUNTRIES, load_country_config
from utils import risk_level as _risk_level
from utils.db import get_connection, initialize_db, get_prediction_history
from pipeline.weekly_digest import generate_weekly_digest

import json


# Brand colors
NAVY = HexColor("#070d18")
CYAN = HexColor("#38bdf8")
WHITE = HexColor("#f0f4f8")
SLATE = HexColor("#94a3b8")
RED = HexColor("#ef4444")
AMBER = HexColor("#f59e0b")
YELLOW = HexColor("#eab308")
GREEN = HexColor("#22c55e")

RISK_COLORS = {"CRITICAL": RED, "HIGH": AMBER, "ELEVATED": YELLOW, "LOW": GREEN}


def generate_pdf_report(conn):
    """Generate a branded PDF report from current predictions."""
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%Y-%m-%d")

    report_dir = Path("data/reports")
    report_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = report_dir / f"weekly_report_{date_str}.pdf"

    doc = SimpleDocTemplate(
        str(pdf_path), pagesize=letter,
        topMargin=0.6 * inch, bottomMargin=0.6 * inch,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        "Brand", fontName="Helvetica-Bold", fontSize=22,
        textColor=HexColor("#38bdf8"), spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        "SubBrand", fontName="Helvetica", fontSize=10,
        textColor=HexColor("#94a3b8"), spaceAfter=16,
    ))
    styles.add(ParagraphStyle(
        "SectionHead", fontName="Helvetica-Bold", fontSize=13,
        textColor=HexColor("#f0f4f8"), spaceBefore=16, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        "BodyText2", fontName="Helvetica", fontSize=9,
        textColor=HexColor("#94a3b8"), leading=13, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        "Footer", fontName="Helvetica", fontSize=7,
        textColor=HexColor("#475569"), leading=9, spaceBefore=20,
    ))

    elements = []

    # Header
    elements.append(Paragraph("PRECURSION", styles["Brand"]))
    elements.append(Paragraph(f"Weekly Geopolitical Risk Report  |  {date_str}", styles["SubBrand"]))
    elements.append(HRFlowable(width="100%", thickness=1, color=HexColor("#253550")))
    elements.append(Spacer(1, 12))

    # Risk ranking table
    elements.append(Paragraph("Risk Rankings", styles["SectionHead"]))

    table_data = [["Rank", "Country", "Probability", "Level", "Track A", "Track B"]]
    country_data = []

    for country_name in COUNTRIES:
        config = load_country_config(country_name)
        iso3 = config["iso3"]
        history = get_prediction_history(conn, iso3, limit=1)
        if not history:
            continue
        p = history[0]["calibrated_probability"]
        a = history[0]["track_a_probability"]
        b = history[0]["track_b_probability"]
        country_data.append((config["name"], p, a, b))

    country_data.sort(key=lambda x: -x[1])

    for i, (name, p, a, b) in enumerate(country_data, 1):
        level = _risk_level(p)
        table_data.append([
            str(i), name, f"{p:.0%}", level,
            f"{a:.0%}" if a else "--",
            f"{b:.0%}" if b else "--",
        ])

    t = Table(table_data, colWidths=[35, 150, 70, 70, 60, 60])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("TEXTCOLOR", (0, 0), (-1, 0), HexColor("#94a3b8")),
        ("TEXTCOLOR", (0, 1), (-1, -1), HexColor("#c8d6e5")),
        ("BACKGROUND", (0, 0), (-1, 0), HexColor("#0c1629")),
        ("BACKGROUND", (0, 1), (-1, -1), HexColor("#070d18")),
        ("GRID", (0, 0), (-1, -1), 0.5, HexColor("#1e2d42")),
        ("ALIGN", (2, 0), (5, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 16))

    # Spotlight on top 3
    elements.append(Paragraph("Spotlight: Highest Risk Countries", styles["SectionHead"]))
    for name, p, a, b in country_data[:3]:
        config = load_country_config(
            next(c for c in COUNTRIES if load_country_config(c)["name"] == name)
        )
        iso3 = config["iso3"]
        history = get_prediction_history(conn, iso3, limit=1)
        reasoning = {}
        if history and history[0].get("reasoning_summary"):
            reasoning = json.loads(history[0]["reasoning_summary"])

        summary = reasoning.get("executive_summary") or reasoning.get("supervisor", "")
        if not summary:
            summary = config["risk_context"]

        level = _risk_level(p)
        elements.append(Paragraph(
            f"<b>{name}</b> ({p:.0%} {level})", styles["BodyText2"]
        ))
        elements.append(Paragraph(summary[:300], styles["BodyText2"]))
        elements.append(Spacer(1, 6))

    # Methodology footer
    elements.append(HRFlowable(width="100%", thickness=0.5, color=HexColor("#1e2d42")))
    elements.append(Paragraph(
        "Predictions generated by the Precursion dual-track forecasting system. "
        "Track A: PITF structural model calibrated on published political science research "
        "(Goldstone et al. 2010). Track B: LLM causal reasoning ensemble with 4 independent "
        "forecasting agents. Probabilities represent 30-day instability risk.",
        styles["Footer"],
    ))

    doc.build(elements)
    return str(pdf_path)


def main():
    initialize_db()
    conn = get_connection()
    path = generate_pdf_report(conn)
    print(f"PDF report saved to: {path}")
    conn.close()


if __name__ == "__main__":
    main()
