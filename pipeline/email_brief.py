"""
Daily email brief delivery via SMTP.

Generates a morning summary email with top movers, full country rankings,
and a short AI-generated regional summary. Sent after the daily pipeline
completes.

Reads recipients from data/email_recipients.txt (one email per line).
Requires SMTP_* environment variables for delivery.

Standalone usage:
    python -m pipeline.email_brief --dry-run
"""

import argparse
import json
import os
import smtplib
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from config.settings import PROJECT_ROOT, load_all_country_configs
from utils.db import (
    get_connection, initialize_db,
    get_latest_predictions_all, get_previous_predictions_all,
    get_latest_briefs_all,
)
from utils.logger import logger

# --- SMTP config from environment ---
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "brief@precursion.ai")

RECIPIENTS_FILE = PROJECT_ROOT / "data" / "email_recipients.txt"

RISK_COLORS = {
    "CRITICAL": "#ef4444",
    "HIGH": "#f59e0b",
    "ELEVATED": "#eab308",
    "LOW": "#22c55e",
}


def _risk_level(p):
    if p is None:
        return "LOW"
    if p >= 0.60:
        return "CRITICAL"
    if p >= 0.40:
        return "HIGH"
    if p >= 0.20:
        return "ELEVATED"
    return "LOW"


def _load_recipients():
    """Load email recipients from data/email_recipients.txt."""
    if not RECIPIENTS_FILE.exists():
        return []
    lines = RECIPIENTS_FILE.read_text().strip().splitlines()
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _gather_brief_data(conn):
    """
    Query predictions, compute deltas, gather briefs.
    Returns a dict with everything needed to build the email.
    """
    configs = load_all_country_configs()

    # Current and previous composite predictions
    current = get_latest_predictions_all(conn, event_type="composite")
    previous = get_previous_predictions_all(conn, event_type="composite")
    prev_by_iso3 = {r["country_iso3"]: r for r in previous}

    # Latest briefs for reasoning context
    briefs = get_latest_briefs_all(conn)
    brief_by_iso3 = {b["country_iso3"]: b for b in briefs}

    countries = []
    for row in current:
        iso3 = row["country_iso3"]
        prob = row["calibrated_probability"]
        prev = prev_by_iso3.get(iso3)
        delta = (prob - prev["calibrated_probability"]) if prev else None
        risk = _risk_level(prob)

        name = configs.get(iso3, {}).get("name", iso3)
        brief = brief_by_iso3.get(iso3)
        headline = brief["headline"] if brief else ""

        countries.append({
            "iso3": iso3,
            "name": name,
            "probability": prob,
            "delta": delta,
            "risk_level": risk,
            "headline": headline,
        })

    # Sort by probability descending
    countries.sort(key=lambda c: c["probability"], reverse=True)

    # Top movers: |delta| >= 1pp, sorted by |delta| descending
    movers = [c for c in countries if c["delta"] is not None
              and abs(round(c["delta"] * 100)) >= 1]
    movers.sort(key=lambda c: abs(c["delta"]), reverse=True)

    return {
        "countries": countries,
        "movers": movers,
        "date": datetime.now().astimezone().strftime("%Y-%m-%d"),
    }


def _generate_summary(movers, countries):
    """
    Generate a 2-3 sentence regional summary via Haiku.
    Falls back to a static summary if the API call fails.
    """
    if not movers:
        high = [c for c in countries if c["risk_level"] in ("HIGH", "CRITICAL")]
        if high:
            names = ", ".join(c["name"] for c in high[:3])
            return f"No significant probability shifts today. {names} remain at elevated risk levels."
        return "No significant probability shifts today. All monitored countries remain within established baselines."

    # Build a terse prompt for Haiku
    mover_lines = []
    for m in movers[:5]:
        pp = round(m["delta"] * 100)
        arrow = "up" if pp > 0 else "down"
        mover_lines.append(
            f"- {m['name']}: {round(m['probability'] * 100)}% ({arrow} {abs(pp)}pp). {m['headline']}"
        )

    prompt = f"""Write a 2-3 sentence summary of today's geopolitical risk changes for an analyst audience. Be specific and factual. No filler.

Today's significant movers:
{chr(10).join(mover_lines)}

Total countries monitored: {len(countries)}
High/Critical risk count: {sum(1 for c in countries if c['risk_level'] in ('HIGH', 'CRITICAL'))}"""

    try:
        from utils.api_client import generate
        result = generate(
            prompt=prompt,
            model="haiku",
            system="You are a geopolitical risk analyst. Write concise daily briefing summaries. 2-3 sentences max.",
            temperature=0.3,
            max_tokens=256,
            response_format="text",
        )
        return result.get("text", "").strip()
    except Exception as e:
        logger.warning("LLM summary generation failed: %s", e)
        # Static fallback
        top = movers[0]
        pp = round(top["delta"] * 100)
        direction = "rose" if pp > 0 else "fell"
        return (
            f"{top['name']} {direction} {abs(pp)}pp to "
            f"{round(top['probability'] * 100)}%, the largest move today."
        )


def _build_subject(data):
    """Build the email subject line."""
    date_str = data["date"]
    movers = data["movers"]
    if movers:
        top = movers[0]
        pp = abs(round(top["delta"] * 100))
        arrow = "\u2191" if top["delta"] > 0 else "\u2193"
        return f"Precursion Daily Brief \u2014 {date_str} \u2014 {top['name']} {arrow}{pp}pp"
    return f"Precursion Daily Brief \u2014 {date_str}"


def _build_html(data, summary_text):
    """Build the full HTML email body with inline CSS."""
    date_str = data["date"]
    countries = data["countries"]
    movers = data["movers"]

    # --- Top movers rows ---
    mover_rows = ""
    for m in movers[:7]:
        pp = round(m["delta"] * 100)
        arrow = "&#x2191;" if pp > 0 else "&#x2193;"
        delta_color = "#ef4444" if pp > 0 else "#22c55e"
        if abs(pp) <= 2:
            delta_color = "#64748b"
        risk_color = RISK_COLORS.get(m["risk_level"], "#64748b")
        reason = m["headline"][:90] if m["headline"] else ""

        mover_rows += f"""<tr>
  <td style="padding:8px 12px;border-bottom:1px solid #1e293b;font-weight:600;font-size:14px;color:#e2e8f0">{m['name']}</td>
  <td style="padding:8px 12px;border-bottom:1px solid #1e293b;font-family:monospace;font-weight:700;font-size:15px;color:{risk_color};text-align:center">{round(m['probability'] * 100)}%</td>
  <td style="padding:8px 12px;border-bottom:1px solid #1e293b;font-family:monospace;font-weight:700;font-size:14px;color:{delta_color};text-align:center">{arrow}{abs(pp)}pp</td>
  <td style="padding:8px 10px;border-bottom:1px solid #1e293b;font-size:12px;color:#94a3b8;max-width:220px">{reason}</td>
</tr>"""

    # --- Full rankings rows ---
    ranking_rows = ""
    for i, c in enumerate(countries):
        risk_color = RISK_COLORS.get(c["risk_level"], "#64748b")
        bg = "rgba(255,255,255,0.02)" if i % 2 == 0 else "transparent"
        prob_pct = round(c["probability"] * 100)

        # Inline bar
        bar_width = max(prob_pct, 2)

        ranking_rows += f"""<tr style="background:{bg}">
  <td style="padding:6px 12px;border-bottom:1px solid #1e293b;font-size:13px;color:#e2e8f0;font-weight:500">{c['name']}</td>
  <td style="padding:6px 12px;border-bottom:1px solid #1e293b;text-align:center">
    <span style="font-family:monospace;font-size:10px;font-weight:700;letter-spacing:0.05em;padding:2px 6px;border-radius:3px;background:{risk_color};color:#0f172a">{c['risk_level']}</span>
  </td>
  <td style="padding:6px 12px;border-bottom:1px solid #1e293b">
    <div style="display:flex;align-items:center;gap:8px">
      <div style="flex:1;height:4px;background:rgba(255,255,255,0.06);border-radius:2px;overflow:hidden">
        <div style="width:{bar_width}%;height:100%;background:{risk_color};border-radius:2px"></div>
      </div>
      <span style="font-family:monospace;font-weight:700;font-size:13px;color:{risk_color};min-width:32px;text-align:right">{prob_pct}%</span>
    </div>
  </td>
</tr>"""

    # --- Assemble ---
    movers_section = ""
    if movers:
        movers_section = f"""
<div style="margin-bottom:28px">
  <h2 style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 12px">Top Movers</h2>
  <table style="width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden">
    <tr>
      <th style="padding:8px 12px;text-align:left;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Country</th>
      <th style="padding:8px 12px;text-align:center;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Prob</th>
      <th style="padding:8px 12px;text-align:center;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Delta</th>
      <th style="padding:8px 10px;text-align:left;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Driver</th>
    </tr>
    {mover_rows}
  </table>
</div>"""

    html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#0f172a;color:#cbd5e1;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-text-size-adjust:100%">
<div style="max-width:640px;margin:0 auto;padding:24px 20px">

  <!-- Header -->
  <div style="border-bottom:1px solid #1e293b;padding-bottom:16px;margin-bottom:24px">
    <div style="font-family:monospace;font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.12em;margin-bottom:4px">Precursion Daily Brief</div>
    <h1 style="font-size:20px;font-weight:800;color:#e2e8f0;margin:0 0 4px;letter-spacing:-0.01em">Risk Overview &mdash; {date_str}</h1>
    <div style="font-size:13px;color:#94a3b8">{len(countries)} countries monitored</div>
  </div>

  <!-- Summary -->
  <div style="padding:14px 18px;margin-bottom:24px;background:rgba(56,189,248,0.06);border:1px solid rgba(56,189,248,0.12);border-radius:8px;font-size:14px;line-height:1.6;color:#94a3b8">
    {summary_text}
  </div>

  {movers_section}

  <!-- Full Rankings -->
  <div style="margin-bottom:28px">
    <h2 style="font-size:11px;font-weight:700;color:#64748b;text-transform:uppercase;letter-spacing:0.1em;margin:0 0 12px">All Countries</h2>
    <table style="width:100%;border-collapse:collapse;background:#1e293b;border-radius:8px;overflow:hidden">
      <tr>
        <th style="padding:6px 12px;text-align:left;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Country</th>
        <th style="padding:6px 12px;text-align:center;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Risk</th>
        <th style="padding:6px 12px;text-align:left;font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.08em;border-bottom:1px solid #334155">Violence Probability (30d)</th>
      </tr>
      {ranking_rows}
    </table>
  </div>

  <!-- Footer -->
  <div style="border-top:1px solid #1e293b;padding-top:16px;font-size:12px;color:#475569;text-align:center">
    <a href="https://precursion.ai" style="color:#38bdf8;text-decoration:none;font-weight:600">View full dashboard &rarr;</a>
    &nbsp;&nbsp;&middot;&nbsp;&nbsp;
    <a href="https://precursion.ai/public" style="color:#38bdf8;text-decoration:none">Public predictions</a>
    &nbsp;&nbsp;&middot;&nbsp;&nbsp;
    <a href="https://precursion.substack.com" style="color:#38bdf8;text-decoration:none">Substack</a>
    <div style="margin-top:10px;font-size:11px;color:#334155">
      Daily geopolitical risk forecasts, measured against reality.
    </div>
  </div>

</div>
</body>
</html>"""

    return html


def send_brief(recipients, subject, html_body, dry_run=False):
    """
    Send the daily brief email via SMTP.

    Returns number of recipients successfully sent to.
    """
    if dry_run:
        print(f"Subject: {subject}")
        print(f"To: {', '.join(recipients)}")
        print(f"---")
        print(html_body)
        return len(recipients)

    if not SMTP_HOST or not SMTP_USER or not SMTP_PASSWORD:
        logger.info("Daily brief email skipped (SMTP not configured).")
        return 0

    sent = 0
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)

            for recipient in recipients:
                try:
                    msg = MIMEMultipart("alternative")
                    msg["From"] = SMTP_FROM
                    msg["To"] = recipient
                    msg["Subject"] = subject
                    msg.attach(MIMEText(html_body, "html"))

                    server.sendmail(SMTP_FROM, recipient, msg.as_string())
                    sent += 1
                    logger.info("Daily brief sent to %s", recipient)
                except Exception as e:
                    logger.warning("Failed to send brief to %s: %s", recipient, e)
    except Exception as e:
        logger.warning("SMTP connection failed: %s", e)

    return sent


def send_daily_brief(dry_run=False):
    """
    Main entry point: gather data, generate summary, build email, send.

    Returns number of emails sent (or recipient count if dry_run).
    """
    conn = get_connection()
    try:
        data = _gather_brief_data(conn)
    finally:
        conn.close()

    if not data["countries"]:
        logger.info("No predictions found. Skipping daily brief.")
        return 0

    recipients = _load_recipients()
    if not recipients and not dry_run:
        logger.info("No email recipients configured. Skipping daily brief.")
        return 0

    summary_text = _generate_summary(data["movers"], data["countries"])
    subject = _build_subject(data)
    html = _build_html(data, summary_text)

    if dry_run:
        recipients = recipients or ["(dry-run)"]
    return send_brief(recipients, subject, html, dry_run=dry_run)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Precursion daily email brief")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print email HTML to stdout instead of sending")
    args = parser.parse_args()

    initialize_db()
    sent = send_daily_brief(dry_run=args.dry_run)
    if not args.dry_run:
        logger.info("Daily brief: %d emails sent.", sent)
