#!/usr/bin/env python3
"""ops/monitoring/check_resources.py -- Step 7.3 prep (ops track).

Cron disk/memory check (docs/SPEC.md §14: "disk and memory alerts"),
alerting by SMTP -- the Postmark relay settings from kickoff decision #4
(D-113-email-postmark), read from the SAME root .env every other service
reads (SMTP_HOST/SMTP_PORT/SMTP_USERNAME/SMTP_PASSWORD/SMTP_FROM_ADDRESS,
identical names to backend/app/config.py's own Settings fields).

Stdlib only, no backend import, by direct instruction: this script must
run standalone on the bare host (crontab, no virtualenv, no `app/`
package on PATH) and must never be coupled to backend/'s own dependency
graph or deploy cadence for a check this unrelated to it. The SMTP send
below is therefore a SEPARATE, independent implementation of the same
shape backend/app/notifications/service.py already uses (SMTP(host,
port, timeout=10) -> starttls() unconditionally -> optional login ->
send), not a refactor shared with it -- the two have no common caller and
living in two different, never-co-deployed processes, so a shared helper
would need its own package/import boundary between them for no real
benefit (rule 9: duplication only matters where a shared abstraction
would actually pay for itself).

Exit code 0 on a clean run (including "alert sent" -- cron should not
treat an over-threshold condition as a script FAILURE, only as the thing
the email itself reports); non-zero only if the check could not run at
all (e.g. SMTP unreachable while over threshold) or, same as
app/notifications/service.py's own send_email(), SMTP not configured at
all.

Usage (docs/deploy/RUNBOOK.md's own crontab line):
  */5 * * * * /usr/bin/python3 /opt/samoflow/ops/monitoring/check_resources.py
"""
from __future__ import annotations

import os
import shutil
import smtplib
import socket
import sys
from email.message import EmailMessage

DISK_PATH = os.environ.get("MONITORING_DISK_PATH", "/")
DISK_THRESHOLD_PERCENT = float(os.environ.get("MONITORING_DISK_THRESHOLD_PERCENT", "85"))
MEMORY_THRESHOLD_PERCENT = float(os.environ.get("MONITORING_MEMORY_THRESHOLD_PERCENT", "85"))
ALERT_EMAIL = os.environ.get("MONITORING_ALERT_EMAIL")

SMTP_HOST = os.environ.get("SMTP_HOST")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD")
SMTP_FROM_ADDRESS = os.environ.get("SMTP_FROM_ADDRESS")


def disk_usage_percent(path: str) -> float:
    usage = shutil.disk_usage(path)
    return (usage.used / usage.total) * 100.0


def memory_usage_percent() -> float:
    """Linux only (the deploy target, docs/SPEC.md §14: "One Ubuntu
    server") -- reads /proc/meminfo directly rather than adding a
    dependency (e.g. psutil) for one ratio stdlib can already compute.
    MemAvailable (not MemFree): the kernel's own "usable without
    swapping" estimate, which already accounts for reclaimable cache/
    buffers -- MemFree alone would overstate real memory pressure on a
    Linux box with a large, healthy page cache.
    """
    values: dict[str, int] = {}
    with open("/proc/meminfo", encoding="ascii") as f:
        for line in f:
            key, _, rest = line.partition(":")
            if key in ("MemTotal", "MemAvailable"):
                values[key] = int(rest.strip().split()[0])
    total = values["MemTotal"]
    available = values["MemAvailable"]
    return ((total - available) / total) * 100.0


def send_alert_email(subject: str, body: str) -> bool:
    if not SMTP_HOST or not SMTP_FROM_ADDRESS or not ALERT_EMAIL:
        print(
            "SMTP or MONITORING_ALERT_EMAIL not configured -- alert not sent "
            f"(subject={subject!r})",
            file=sys.stderr,
        )
        return False
    message = EmailMessage()
    message["From"] = SMTP_FROM_ADDRESS
    message["To"] = ALERT_EMAIL
    message["Subject"] = subject
    message.set_content(body)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=10) as server:
            server.starttls()
            if SMTP_USERNAME and SMTP_PASSWORD:
                server.login(SMTP_USERNAME, SMTP_PASSWORD)
            server.send_message(message)
    except Exception as exc:  # noqa: BLE001 -- reported, never re-raised past main()
        print(f"SMTP send failed (subject={subject!r}): {exc!r}", file=sys.stderr)
        return False
    return True


def main() -> int:
    hostname = socket.gethostname()
    alerts: list[str] = []

    disk_pct = disk_usage_percent(DISK_PATH)
    if disk_pct >= DISK_THRESHOLD_PERCENT:
        alerts.append(
            f"Disk usage on {DISK_PATH!r} is {disk_pct:.1f}% "
            f"(threshold {DISK_THRESHOLD_PERCENT:.1f}%)."
        )

    mem_pct = memory_usage_percent()
    if mem_pct >= MEMORY_THRESHOLD_PERCENT:
        alerts.append(
            f"Memory usage is {mem_pct:.1f}% (threshold {MEMORY_THRESHOLD_PERCENT:.1f}%)."
        )

    print(f"disk={disk_pct:.1f}% memory={mem_pct:.1f}% host={hostname}")

    if not alerts:
        return 0

    body = f"Resource alert on {hostname}:\n\n" + "\n".join(alerts)
    sent = send_alert_email(f"[SamoFlow] Resource alert on {hostname}", body)
    return 0 if sent else 1


if __name__ == "__main__":
    raise SystemExit(main())
