"""
Email alert delivery via Resend.

Matches triggered alerts to subscribers by country + event_type preferences,
formats briefs as HTML emails, and sends via the Resend API.

Requires RESEND_API_KEY environment variable.
"""

import json
import re
from datetime import datetime

import requests

from config.settings import RESEND_API_KEY, ALERT_FROM_EMAIL, EVENT_TYPE_RESOLUTION
from utils.db import get_matched_subscribers, get_latest_brief
from utils.logger import logger


def _markdown_to_html(md: str) -> str:
    """Minimal markdown to HTML conversion for email bodies."""
    html = md

    # Headers
    html = re.sub(r'^# (.+)$', r'<h1 style="color:#e2e8f0;font-size:1.3em;margin:0.8em 0 0.4em">\1</h1>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2 style="color:#94a3b8;font-size:1.1em;margin:0.7em 0 0.3em">\1</h2>', html, flags=re.MULTILINE)

    # Bold
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)

    # List items
    html = re.sub(r'^- (.+)$', r'<li style="margin:0.2em 0">\1</li>', html, flags=re.MULTILINE)

    # Tables (basic)
    lines = html.split('\n')
    in_table = False
    result = []
    for line in lines:
        if '|' in line and line.strip().startswith('|'):
            if not in_table:
                result.append('<table style="width:100%;border-collapse:collapse;margin:0.5em 0;font-size:0.85em">')
                in_table = True
            if re.match(r'^\|[-|: ]+\|$', line.strip()):
                continue  # skip separator row
            cells = [c.strip() for c in line.strip().strip('|').split('|')]
            tag = 'th' if not any('<td' in r for r in result[-3:] if '<t' in r) else 'td'
            style = 'padding:0.3em 0.5em;border-bottom:1px solid #334155;text-align:left'
            row = ''.join(f'<{tag} style="{style}">{c}</{tag}>' for c in cells)
            result.append(f'<tr>{row}</tr>')
        else:
            if in_table:
                result.append('</table>')
                in_table = False
            result.append(line)
    if in_table:
        result.append('</table>')
    html = '\n'.join(result)

    # Paragraphs (double newline)
    html = re.sub(r'\n\n', '</p><p style="margin:0.5em 0;line-height:1.6">', html)
    html = f'<p style="margin:0.5em 0;line-height:1.6">{html}</p>'

    return html


def _build_alert_email_html(alert, brief_body, country_name):
    """Build a formatted HTML email for an alert."""
    et_name = EVENT_TYPE_RESOLUTION.get(alert["event_type"], {}).get("name", alert["event_type"])
    delta_pct = alert["delta"] * 100
    direction_arrow = "&#x2191;" if alert["delta"] > 0 else "&#x2193;"
    direction_color = "#ef4444" if alert["delta"] > 0 else "#22c55e"

    brief_html = _markdown_to_html(brief_body) if brief_body else "<p>No brief available.</p>"

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family:-apple-system,system-ui,sans-serif;background:#0f172a;color:#cbd5e1;padding:2em;max-width:640px;margin:0 auto">
  <div style="border-bottom:1px solid #1e293b;padding-bottom:1em;margin-bottom:1.5em">
    <div style="font-size:0.7em;color:#64748b;text-transform:uppercase;letter-spacing:0.1em">Precursion Alert</div>
    <h1 style="color:#e2e8f0;font-size:1.3em;margin:0.3em 0">{country_name}: {et_name}</h1>
    <div style="font-size:1.5em;font-weight:800;color:{direction_color}">
      {direction_arrow} {delta_pct:+.1f}pp
      <span style="font-size:0.6em;color:#94a3b8;font-weight:400">
        {alert['previous_probability']:.0%} &rarr; {alert['current_probability']:.0%}
      </span>
    </div>
  </div>

  <div style="background:#1e293b;border-radius:8px;padding:1.2em;margin-bottom:1.5em">
    {brief_html}
  </div>

  <div style="font-size:0.75em;color:#475569;border-top:1px solid #1e293b;padding-top:1em">
    <a href="https://precursion.ai/country/{alert['country_iso3']}" style="color:#38bdf8">
      View full assessment on Precursion
    </a>
    &nbsp;&middot;&nbsp;
    {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M UTC')}
  </div>
</body>
</html>"""


def send_alert_email(to_email: str, subject: str, html_body: str) -> bool:
    """Send an email via Resend API. Returns True on success."""
    if not RESEND_API_KEY:
        logger.debug("RESEND_API_KEY not set, skipping email to %s", to_email)
        return False

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "from": ALERT_FROM_EMAIL,
                "to": [to_email],
                "subject": subject,
                "html": html_body,
            },
            timeout=15,
        )
        if resp.status_code in (200, 201):
            logger.info("Email sent to %s: %s", to_email, subject)
            return True
        else:
            logger.warning("Resend API error %d: %s", resp.status_code, resp.text[:200])
            return False
    except requests.exceptions.RequestException as e:
        logger.warning("Email delivery failed for %s: %s", to_email, e)
        return False


def deliver_alerts(conn, alerts):
    """
    For each triggered alert, find matching subscribers and send emails.

    Args:
        conn: Database connection
        alerts: List of alert trigger dicts (from detect_changes_for_country)

    Returns:
        Number of emails sent
    """
    if not RESEND_API_KEY:
        logger.info("Email delivery skipped (RESEND_API_KEY not set).")
        return 0

    if not alerts:
        return 0

    total_sent = 0
    configs = {}

    for alert in alerts:
        iso3 = alert["country_iso3"]
        et = alert["event_type"]

        # Get matching subscribers
        subscribers = get_matched_subscribers(conn, iso3, et)
        if not subscribers:
            continue

        # Get country name
        if iso3 not in configs:
            try:
                from config.settings import load_all_available_country_configs
                all_configs = load_all_available_country_configs()
                configs.update(all_configs)
            except Exception:
                pass
        country_name = configs.get(iso3, {}).get("name", iso3)

        # Get today's brief for context
        brief = get_latest_brief(conn, iso3)
        brief_body = brief["body_markdown"] if brief else ""

        # Build email
        et_name = EVENT_TYPE_RESOLUTION.get(et, {}).get("name", et)
        delta_pct = alert["delta"] * 100
        direction = "rose" if alert["delta"] > 0 else "fell"
        subject = (f"[Precursion] {country_name} {et_name} {direction} "
                   f"{delta_pct:+.0f}pp to {alert['current_probability']:.0%}")

        html = _build_alert_email_html(alert, brief_body, country_name)

        # Send to each matched subscriber
        for sub in subscribers:
            if send_alert_email(sub["email"], subject, html):
                total_sent += 1

    if total_sent:
        logger.info("Delivered %d alert emails.", total_sent)

    return total_sent
