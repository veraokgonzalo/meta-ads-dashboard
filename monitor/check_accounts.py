#!/usr/bin/env python3
"""
Monitorea el estado de las cuentas publicitarias de Meta.
Envia alerta por Gmail cuando una cuenta cambia a un estado problemático
o recupera el estado ACTIVE, y cuando una cuenta activa gastó ayer pero hoy
no lleva gasto (chequeado desde NO_SPEND_HOUR, hora local de la cuenta).
"""
import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib import error, parse, request
from zoneinfo import ZoneInfo

API_BASE = "https://graph.facebook.com/v21.0"
STATES_FILE = Path(__file__).parent / "states.json"

STATUS_NAMES = {
    1:   "ACTIVE",
    2:   "DISABLED",
    3:   "UNSETTLED",
    7:   "PENDING_RISK_REVIEW",
    9:   "IN_GRACE_PERIOD",
    100: "PENDING_CLOSURE",
    101: "CLOSED",
}

STATUS_LABELS_ES = {
    1:   "Activa",
    2:   "Deshabilitada",
    3:   "Saldo pendiente",
    7:   "En revisión de riesgo",
    9:   "Período de gracia",
    100: "Cierre pendiente",
    101: "Cerrada",
}

PROBLEM_STATUSES = {2, 3, 7, 9, 100, 101}

# Hora local de la cuenta desde la que se chequea "ayer gastó, hoy no"
NO_SPEND_HOUR = 10


# ── API ───────────────────────────────────────────────────────────────────────

def api_get(path, params):
    url = f"{API_BASE}/{path}?" + parse.urlencode(params)
    with request.urlopen(url) as resp:
        return json.loads(resp.read())


def fetch_accounts(token):
    accounts = []
    params = {
        "access_token": token,
        "fields": "id,name,account_status,currency,timezone_name",
        "limit": 100,
    }
    data = api_get("me/adaccounts", params)
    accounts.extend(data.get("data", []))
    while "paging" in data and "next" in data.get("paging", {}):
        with request.urlopen(data["paging"]["next"]) as resp:
            data = json.loads(resp.read())
        accounts.extend(data.get("data", []))
    return accounts


def fetch_daily_spend(token, acct_id, since, until):
    """Gasto por día entre since y until (fechas en la zona horaria de la cuenta)."""
    data = api_get(f"{acct_id}/insights", {
        "access_token":   token,
        "fields":         "spend",
        "time_range":     json.dumps({"since": since, "until": until}),
        "time_increment": 1,
    })
    return {d["date_start"]: float(d.get("spend") or 0) for d in data.get("data", [])}


# ── Estado persistido ─────────────────────────────────────────────────────────

def load_states():
    if not STATES_FILE.exists():
        return {}
    return json.loads(STATES_FILE.read_text(encoding="utf-8"))


def save_states(states):
    STATES_FILE.write_text(
        json.dumps(states, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Email ─────────────────────────────────────────────────────────────────────

def status_color(status):
    if status == 1:
        return "#16a34a"
    if status in {3, 7, 9, 100}:
        return "#d97706"
    return "#dc2626"


TD = "padding:12px 16px;border-bottom:1px solid #e5e5e5"


def build_email(changes):
    subject = (
        f"Meta Ads — {len(changes)} cuenta(s) cambiaron de estado"
        if len(changes) > 1
        else f"Meta Ads — {changes[0]['name']} pasó a {STATUS_LABELS_ES.get(changes[0]['curr_status'], '?')}"
    )

    rows = ""
    for c in changes:
        prev_label = STATUS_LABELS_ES.get(c["prev_status"], str(c["prev_status"]))
        curr_label = STATUS_LABELS_ES.get(c["curr_status"], str(c["curr_status"]))
        arrow = "→"
        rows += f"""
        <tr>
          <td style="{TD};font-size:13px;font-weight:600;color:#171717">{c['name']}</td>
          <td style="{TD};font-family:monospace;font-size:11px;color:#a3a3a3">{c['id']}</td>
          <td style="{TD};font-size:12px;color:#737373">{prev_label} {arrow} <strong style="color:{status_color(c['curr_status'])}">{curr_label}</strong></td>
        </tr>"""

    return subject, render_email("Cambio de estado detectado", "Estado", rows)


def build_no_spend_email(no_spend):
    subject = (
        f"Meta Ads — {len(no_spend)} cuentas sin gasto hoy"
        if len(no_spend) > 1
        else f"Meta Ads — {no_spend[0]['name']} sin gasto hoy"
    )

    rows = ""
    for c in no_spend:
        spent = f"{c['yesterday']:,.0f}".replace(",", ".")
        rows += f"""
        <tr>
          <td style="{TD};font-size:13px;font-weight:600;color:#171717">{c['name']}</td>
          <td style="{TD};font-family:monospace;font-size:11px;color:#a3a3a3">{c['id']}</td>
          <td style="{TD};font-size:12px;color:#737373">Ayer {c['currency']} {spent} → <strong style="color:#dc2626">Hoy {c['currency']} 0</strong></td>
        </tr>"""

    return subject, render_email("Cuentas que dejaron de gastar", "Gasto", rows)


def render_email(title, last_header, rows):
    now = datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:system-ui,-apple-system,sans-serif">
  <div style="max-width:560px;margin:40px auto;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.08)">

    <div style="background:#171717;padding:18px 24px;display:flex;align-items:center;gap:10px">
      <div style="width:22px;height:22px;background:#fff;border-radius:5px;display:inline-flex;align-items:center;justify-content:center">
        <svg width="12" height="12" viewBox="0 0 14 14" fill="none"><path d="M7 1.5L12.5 11H1.5L7 1.5Z" fill="#171717"/></svg>
      </div>
      <span style="color:#fff;font-size:14px;font-weight:600;vertical-align:middle">Meta Ads Monitor</span>
    </div>

    <div style="padding:28px 24px 20px">
      <p style="margin:0 0 4px;font-size:18px;font-weight:700;color:#171717;letter-spacing:-0.5px">{title}</p>
      <p style="margin:0 0 24px;color:#a3a3a3;font-size:12px">{now}</p>

      <table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;border:1px solid #e5e5e5;border-radius:8px;overflow:hidden">
        <thead>
          <tr style="background:#fafafa">
            <th style="padding:9px 16px;text-align:left;font-size:10px;font-weight:600;color:#a3a3a3;text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid #e5e5e5">Cuenta</th>
            <th style="padding:9px 16px;text-align:left;font-size:10px;font-weight:600;color:#a3a3a3;text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid #e5e5e5">ID</th>
            <th style="padding:9px 16px;text-align:left;font-size:10px;font-weight:600;color:#a3a3a3;text-transform:uppercase;letter-spacing:0.06em;border-bottom:1px solid #e5e5e5">{last_header}</th>
          </tr>
        </thead>
        <tbody>{rows}</tbody>
      </table>
    </div>

    <div style="padding:14px 24px;background:#fafafa;border-top:1px solid #e5e5e5">
      <a href="https://business.facebook.com/latest/home" style="font-size:12px;color:#0a72ef;text-decoration:none">Abrir Meta Business Manager →</a>
    </div>

  </div>
</body>
</html>"""


def send_alert(gmail_user, gmail_password, alert_to, subject, html):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Meta Ads Monitor <{gmail_user}>"
    msg["To"] = alert_to
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(gmail_user, gmail_password)
        smtp.sendmail(gmail_user, alert_to, msg.as_string())


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    token         = os.environ["META_TOKEN"]
    gmail_user    = os.environ["GMAIL_USER"]
    gmail_pass    = os.environ["GMAIL_APP_PASSWORD"]
    alert_to      = os.environ["ALERT_TO"]

    print("Fetching accounts from Meta Graph API...")
    accounts = fetch_accounts(token)
    print(f"  {len(accounts)} accounts found")

    prev_states = load_states()
    is_first_run = not prev_states
    curr_states = {}
    changes = []

    for acct in accounts:
        acct_id    = acct["id"]
        curr_status = int(acct["account_status"])
        name       = acct.get("name") or acct_id
        curr_states[acct_id] = {"status": curr_status, "name": name}
        # Conservar el último día en que se avisó "sin gasto" para no repetir la alerta
        if (prev_states.get(acct_id) or {}).get("no_spend_alerted"):
            curr_states[acct_id]["no_spend_alerted"] = prev_states[acct_id]["no_spend_alerted"]

        if is_first_run:
            continue

        prev = prev_states.get(acct_id)
        if prev is None:
            print(f"  New account found: {name} ({acct_id}) — status {STATUS_NAMES.get(curr_status)}")
            continue

        prev_status = prev["status"]
        if prev_status == curr_status:
            continue

        # Alerta si pasa a estado problemático o si se recupera a ACTIVE
        if curr_status in PROBLEM_STATUSES or (prev_status in PROBLEM_STATUSES and curr_status == 1):
            changes.append({
                "id":          acct_id,
                "name":        name,
                "prev_status": prev_status,
                "curr_status": curr_status,
            })
            print(
                f"  CHANGE: {name} ({acct_id}) "
                f"{STATUS_NAMES.get(prev_status)} -> {STATUS_NAMES.get(curr_status)}"
            )

    # Cuentas activas que gastaron ayer y hoy no (una alerta por cuenta y día)
    no_spend = []
    if not is_first_run:
        for acct in accounts:
            acct_id = acct["id"]
            if int(acct["account_status"]) != 1:
                continue
            local_now = datetime.now(ZoneInfo(acct.get("timezone_name") or "UTC"))
            today = local_now.date().isoformat()
            if local_now.hour < NO_SPEND_HOUR or curr_states[acct_id].get("no_spend_alerted") == today:
                continue
            yesterday = (local_now.date() - timedelta(days=1)).isoformat()
            try:
                spend = fetch_daily_spend(token, acct_id, yesterday, today)
            except error.URLError as e:
                print(f"  WARN: no se pudo leer el gasto de {acct_id}: {e}")
                continue
            if spend.get(yesterday, 0) > 0 and spend.get(today, 0) == 0:
                curr_states[acct_id]["no_spend_alerted"] = today
                no_spend.append({
                    "id":        acct_id,
                    "name":      curr_states[acct_id]["name"],
                    "currency":  acct.get("currency", ""),
                    "yesterday": spend[yesterday],
                })
                print(f"  NO SPEND: {curr_states[acct_id]['name']} ({acct_id}) ayer {spend[yesterday]}, hoy 0")

    save_states(curr_states)
    print(f"  States saved ({len(curr_states)} accounts)")

    if is_first_run:
        print("First run — baseline saved. No alerts sent.")
        return

    if changes:
        print(f"Sending alert for {len(changes)} change(s)...")
        send_alert(gmail_user, gmail_pass, alert_to, *build_email(changes))
        print("Alert sent.")
    else:
        print("No status changes detected.")

    if no_spend:
        print(f"Sending no-spend alert for {len(no_spend)} account(s)...")
        send_alert(gmail_user, gmail_pass, alert_to, *build_no_spend_email(no_spend))
        print("Alert sent.")
    else:
        print("No accounts stopped spending.")


if __name__ == "__main__":
    main()
