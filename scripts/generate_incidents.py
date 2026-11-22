"""Generate a repeatable synthetic Incident dataset as NDJSON.

    uv run python scripts/generate_incidents.py            # writes data/incidents.jsonl

The same seed with the same pinned `faker` version gives byte-identical output.
Free text (title, description, resolution_summary) is customer-visible by business
rule (ADR 0003), so templates contain no Internal Identifiers: no hostnames, request
IDs, emails or person names.
"""

import argparse
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from faker import Faker

from incident_triage.models import Environment, ImpactCategory, Incident, Service, Severity

# Each template is one failure mode of a Service, tagged with the Impact Category a
# customer would see. Placeholders are filled per Incident.
TEMPLATES: dict[Service, list[dict]] = {
    Service.PAYROLL: [
        {
            "title": "Payroll run delayed for {weekday} pay cycle",
            "impact": ImpactCategory.PAY_CALCULATION,
            "description": "The scheduled payroll run did not complete within its window. "
            "About {count} employees saw pay statements stuck in 'processing' for "
            "{minutes} minutes.",
            "resolution": "A stalled batch job was restarted and the run completed. Added "
            "an alert when a payroll run exceeds its expected duration.",
            "tags": ["batch", "delay", "pay-run"],
            "severity": [Severity.SEV1, Severity.SEV2],
        },
        {
            "title": "Overtime calculated at the wrong rate",
            "impact": ImpactCategory.PAY_CALCULATION,
            "description": "Overtime hours were paid at the standard rate instead of 1.5x "
            "for roughly {count} hourly employees after a pay rule change.",
            "resolution": "Corrected the overtime multiplier in the pay rule and ran an "
            "off-cycle adjustment for affected employees.",
            "tags": ["calculation", "overtime", "pay-rules"],
            "severity": [Severity.SEV1, Severity.SEV2],
        },
        {
            "title": "Tax withholding missing for new state",
            "impact": ImpactCategory.PAY_CALCULATION,
            "description": "Employees who moved to a new work state had no state tax "
            "withheld. {count} pay statements were affected.",
            "resolution": "Added the missing state tax jurisdiction mapping and corrected "
            "withholding on the next pay cycle.",
            "tags": ["calculation", "tax"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
    ],
    Service.TIMEKEEPING: [
        {
            "title": "Clock-in punches not syncing from terminals",
            "impact": ImpactCategory.TIME_TRACKING,
            "description": "Time clock punches were not appearing in timesheets for "
            "{minutes} minutes. Around {count} employees saw missing punches.",
            "resolution": "The punch sync queue was backed up; it was drained and its "
            "consumer capacity increased. Missing punches were backfilled.",
            "tags": ["punches", "sync", "delay"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Timesheet approval button unresponsive",
            "impact": ImpactCategory.TIME_TRACKING,
            "description": "Managers could not approve timesheets; the approve action "
            "spun indefinitely. Reported by {count} managers before the pay cutoff.",
            "resolution": "Rolled back a front-end release that broke the approval form "
            "and added an end-to-end test for the approval flow.",
            "tags": ["approvals", "ui", "regression"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Daylight saving shift double-counted hours",
            "impact": ImpactCategory.TIME_TRACKING,
            "description": "Overnight shifts spanning the daylight saving change recorded "
            "an extra hour for {count} employees.",
            "resolution": "Fixed shift duration calculation to use time-zone-aware "
            "timestamps and corrected affected timesheets.",
            "tags": ["calculation", "timezone", "dst"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
    ],
    Service.AUTH: [
        {
            "title": "Users unable to log in via single sign-on",
            "impact": ImpactCategory.LOGIN_ACCESS,
            "description": "SSO logins failed with an 'invalid assertion' error for "
            "{minutes} minutes. About {pct}% of login attempts were affected.",
            "resolution": "An expired signing certificate was rotated. Added certificate "
            "expiry monitoring with a 30-day warning.",
            "tags": ["sso", "login", "certificate"],
            "severity": [Severity.SEV1, Severity.SEV2],
        },
        {
            "title": "Password reset emails not arriving",
            "impact": ImpactCategory.LOGIN_ACCESS,
            "description": "Users requesting a password reset did not receive the email. "
            "{count} reset requests went undelivered over {minutes} minutes.",
            "resolution": "The email provider's rate limit was raised and the queued reset "
            "emails were re-sent.",
            "tags": ["password-reset", "email", "login"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Sessions expiring after a few minutes",
            "impact": ImpactCategory.LOGIN_ACCESS,
            "description": "Users were logged out unexpectedly after about {short} "
            "minutes instead of the configured session length.",
            "resolution": "A configuration change shortened the session timeout; it was "
            "reverted and config changes now require review.",
            "tags": ["session", "login", "config"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
    ],
    Service.BENEFITS: [
        {
            "title": "Open enrollment page timing out",
            "impact": ImpactCategory.PERFORMANCE,
            "description": "The benefits enrollment page timed out for many employees "
            "during open enrollment. Error rate peaked at {pct}% for {minutes} minutes.",
            "resolution": "Added an index for the plan eligibility query and scaled out "
            "the enrollment service for the enrollment window.",
            "tags": ["enrollment", "timeout", "performance"],
            "severity": [Severity.SEV1, Severity.SEV2],
        },
        {
            "title": "Dependent coverage missing after enrollment",
            "impact": ImpactCategory.OTHER,
            "description": "Employees who added dependents saw only employee-only coverage "
            "on their confirmation. {count} enrollments were affected.",
            "resolution": "Fixed the dependent mapping in the enrollment save step and "
            "reprocessed affected enrollments.",
            "tags": ["enrollment", "dependents", "data"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Benefit deduction amounts out of date",
            "impact": ImpactCategory.PAY_CALCULATION,
            "description": "Payroll deductions used last year's premium amounts for "
            "{count} employees.",
            "resolution": "Refreshed premium rates from the carrier feed and scheduled "
            "deduction corrections on the next pay cycle.",
            "tags": ["deductions", "data", "stale"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
    ],
    Service.SCHEDULING: [
        {
            "title": "Shift swap requests stuck pending",
            "impact": ImpactCategory.DATA_DELAY,
            "description": "Shift swap requests were not reaching managers for approval. "
            "{count} requests were stuck pending for {minutes} minutes.",
            "resolution": "A failed background worker was restarted and pending swap "
            "requests were re-queued. Added worker health checks.",
            "tags": ["shift-swap", "queue", "delay"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
        {
            "title": "Published schedules showing wrong week",
            "impact": ImpactCategory.OTHER,
            "description": "Employees viewing their schedule saw the previous week's "
            "shifts after publishing on {weekday}.",
            "resolution": "Fixed a cache key that ignored the week start date and cleared "
            "the schedule cache.",
            "tags": ["cache", "ui", "schedule"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Auto-scheduler ignoring availability",
            "impact": ImpactCategory.OTHER,
            "description": "The auto-scheduler assigned shifts to employees outside their "
            "stated availability for {count} shifts.",
            "resolution": "Corrected the availability filter in the scheduling rules and "
            "regenerated affected schedules.",
            "tags": ["auto-schedule", "rules", "regression"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
    ],
    Service.REPORTING: [
        {
            "title": "Labor cost report failing to generate",
            "impact": ImpactCategory.REPORT_GENERATION,
            "description": "The labor cost report failed with a timeout for large "
            "organizations. {count} report requests failed.",
            "resolution": "Paginated the report query and moved large reports to "
            "asynchronous generation with email delivery.",
            "tags": ["report", "timeout", "performance"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Dashboard figures delayed by several hours",
            "impact": ImpactCategory.DATA_DELAY,
            "description": "Workforce dashboards showed data {short} hours old instead of "
            "near real time.",
            "resolution": "The data refresh pipeline was stuck on a malformed record; the "
            "record was quarantined and the pipeline resumed.",
            "tags": ["dashboard", "delay", "pipeline"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
        {
            "title": "Exported CSV missing columns",
            "impact": ImpactCategory.REPORT_GENERATION,
            "description": "Report exports omitted custom columns added by customers. "
            "{count} exports were affected.",
            "resolution": "Fixed the export column mapping for custom fields and added a "
            "regression test.",
            "tags": ["export", "csv", "regression"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
    ],
    Service.NOTIFICATIONS: [
        {
            "title": "Push notifications delayed",
            "impact": ImpactCategory.NOTIFICATION_DELIVERY,
            "description": "Mobile push notifications arrived up to {minutes} minutes late "
            "for shift reminders.",
            "resolution": "Increased throughput on the notification dispatcher and added "
            "queue-depth alerting.",
            "tags": ["push", "delay", "mobile"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
        {
            "title": "Duplicate email notifications sent",
            "impact": ImpactCategory.NOTIFICATION_DELIVERY,
            "description": "Employees received the same approval notification email {short} times.",
            "resolution": "Made the notification sender idempotent with a per-message "
            "deduplication key.",
            "tags": ["email", "duplicate"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
        {
            "title": "SMS alerts not delivered",
            "impact": ImpactCategory.NOTIFICATION_DELIVERY,
            "description": "SMS shift alerts failed for {pct}% of recipients for "
            "{minutes} minutes.",
            "resolution": "Failed over to the secondary SMS provider and re-sent failed alerts.",
            "tags": ["sms", "delivery", "provider"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
    ],
    Service.INTEGRATIONS: [
        {
            "title": "HRIS employee sync failing",
            "impact": ImpactCategory.INTEGRATION_SYNC,
            "description": "The nightly employee sync from the customer's HR system "
            "failed. {count} employee changes were not applied.",
            "resolution": "The partner API changed a field format; the mapping was "
            "updated and the sync re-run.",
            "tags": ["sync", "hris", "api-change"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "General ledger export rejected",
            "impact": ImpactCategory.INTEGRATION_SYNC,
            "description": "The payroll general ledger export was rejected by the finance "
            "system due to an invalid date format.",
            "resolution": "Corrected the export date format and re-sent the ledger file.",
            "tags": ["export", "finance", "format"],
            "severity": [Severity.SEV2, Severity.SEV3],
        },
        {
            "title": "Webhook deliveries timing out",
            "impact": ImpactCategory.INTEGRATION_SYNC,
            "description": "Outbound webhooks to customer endpoints timed out for "
            "{minutes} minutes; {count} events were retried.",
            "resolution": "Reduced webhook payload size and added exponential backoff "
            "with a dead-letter queue.",
            "tags": ["webhook", "timeout", "retry"],
            "severity": [Severity.SEV3, Severity.SEV4],
        },
    ],
}

ENV_WEIGHTS = {Environment.PROD: 4, Environment.STAGE: 2, Environment.QA: 2, Environment.DEV: 1}
START, END = datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 10, 31, tzinfo=UTC)


def generate(count: int, seed: int) -> list[Incident]:
    rng = random.Random(seed)
    fake = Faker()
    fake.seed_instance(seed)

    drafts = []
    for _ in range(count):
        service = rng.choice(list(Service))
        template = rng.choice(TEMPLATES[service])
        environment = rng.choices(list(ENV_WEIGHTS), weights=list(ENV_WEIGHTS.values()))[0]
        values = {
            "count": fake.random_int(5, 5000),
            "minutes": fake.random_int(10, 240),
            "short": fake.random_int(2, 6),
            "pct": fake.random_int(5, 90),
            "weekday": fake.day_of_week(),
        }
        drafts.append(
            {
                "created_at": fake.date_time_between(START, END, tzinfo=UTC).replace(microsecond=0),
                "environment": environment,
                "service": service,
                "severity": rng.choice(template["severity"]),
                "title": template["title"].format(**values),
                "description": template["description"].format(**values),
                "resolution_summary": template["resolution"],
                "tags": template["tags"],
            }
        )

    drafts.sort(key=lambda d: d["created_at"])
    return [Incident(id=n, **d) for n, d in enumerate(drafts, start=1)]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--count", type=int, default=300)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--out", type=Path, default=Path("data/incidents.jsonl"))
    args = parser.parse_args()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", encoding="utf-8") as f:
        for incident in generate(args.count, args.seed):
            f.write(json.dumps(incident.model_dump(mode="json")) + "\n")
    print(f"Wrote {args.count} incidents to {args.out}")


if __name__ == "__main__":
    main()
