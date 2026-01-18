#!/usr/bin/env python3
"""
Initialize Morning Shift Checklist Templates from official DOCX.
Run once during deployment.
"""
import sys
from pathlib import Path

# Ensure project root is on sys.path so `from app...` imports work
# when this script is executed directly (e.g. `python scripts/init_checklists.py`).
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
from datetime import time
from uuid import uuid4

from app.db.database import init_db, get_async_connection
from app.core.logging import get_logger

log = get_logger("init-checklists")

# ---------------------------------------------------------
# MORNING SHIFT CHECKLIST (EXACT FROM DOCX)
# ---------------------------------------------------------

MORNING_CHECKLIST_ITEMS = [

    # ---- Uptime communications ----
    {
        "title": "Share Systems Uptime Status via email & WhatsApp @ 07:00",
        "description": "ICT & Digital. Refer to night shift handover notes if pending.",
        "item_type": "TIMED",
        "is_required": True,
        "scheduled_time": time(7, 0),
        "notify_before_minutes": 5,
        "severity": 5,
        "sort_order": 100,
    },

    {
        "title": "Check all IDC services are functioning (OF Services)",
        "description": "Confirm IDC operational status @ 07:00",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 200,
    },

    {
        "title": "Attend to handover notes from previous shift",
        "description": "Escalate unresolved issues and document at end of checklist",
        "item_type": "CONDITIONAL",
        "is_required": True,
        "severity": 4,
        "sort_order": 300,
    },

    {
        "title": "Check and action system exceptions, locks, and logs",
        "description": "Escalate where applicable",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 400,
    },

    {
        "title": "Check services and server disk space (Apps & Databases)",
        "description": "IDC, Mobile Banking ATE, Billers, Postilion, Internet Banking, RTGS, DigiPay, SMS Alerts, ZEEPAY",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 500,
    },

    {
        "title": "Extract All-Accounts List (IDC & DigiPay) and upload to Trustlink STP",
        "description": r"Upload to C:\TRUSTLINK\PROD\RTGS\AccListToSTPLink on server 0.45 by 07:40",
        "item_type": "TIMED",
        "is_required": True,
        "scheduled_time": time(7, 40),
        "severity": 5,
        "sort_order": 600,
    },

    {
        "title": "Verify RTGS folder monitoring is enabled",
        "description": "Log in to the CFT site on 0.45, navigate to Flow Definitions -> Folder Monitoring section",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 4,
        "sort_order": 700,
    },

    {
        "title": "Verify RTGS acceleration is disabled",
        "description": "on 0.45",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 4,
        "sort_order": 800,
    },

    {
        "title": "Ensure RTGS folders mounted on IDC App Servers",
        "description": "Validate using df -h on servers 1.108 & 1.109",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 900,
    },

    {
        "title": "Ensure RTGS folders mounted on Mobile Banking Server",
        "description": "Validate using df -h onServer 0.249",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1000,
    },

    {
        "title": "Ensure SWIFT folders mounted on IDC App Servers",
        "description": "Servers 1.108 & 1.109",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1100,
    },

    {
        "title": "Confirm Incoming RTGS payments are settling",
        "description": "Check on server 0.45",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1200,
    },

    {
        "title": "Confirm Outgoing RTGS payments are settling",
        "description": "",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1300,
    },

    {
        "title": "Escalate database issues to Database Administrators",
        "description": "",
        "item_type": "CONDITIONAL",
        "is_required": True,
        "severity": 5,
        "sort_order": 1400,
    },

    {
        "title": "Escalate infrastructure issues to Infra Team",
        "description": "",
        "item_type": "CONDITIONAL",
        "is_required": True,
        "severity": 5,
        "sort_order": 1500,
    },

    {
        "title": "Check and attend to logged issues on iSupport",
        "description": "",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 4,
        "sort_order": 1600,
    },

    {
        "title": "Verify USSD & Mobile Banking functionality",
        "description": "Test balance enquiry *276# and AFCLink",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1700,
    },

    {
        "title": "Verify Wallet Services & Digi-Loans functionality",
        "description": "",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 5,
        "sort_order": 1800,
    },

    {
        "title": "Check D365 server space and service health",
        "description": "",
        "item_type": "ROUTINE",
        "is_required": True,
        "severity": 4,
        "sort_order": 1900,
    },

    {
        "title": "Implement approved change requests",
        "description": "From FlexiDoc Portal, Email, or Handover Notes",
        "item_type": "CONDITIONAL",
        "is_required": True,
        "severity": 4,
        "sort_order": 2000,
    },

]

# ---- Hourly uptime notifications (08:00–14:55) ----
_UPTIME_HOURS = [
    (8, 0), (9, 0), (10, 0), (11, 0),
    (12, 0), (13, 0), (14, 0), (14, 55)
]

base_order = 2100
for h, m in _UPTIME_HOURS:
    MORNING_CHECKLIST_ITEMS.append({
        "title": f"Share Systems Uptime Status via email & WhatsApp @ {h:02d}:{m:02d}",
        "description": "ICT & Digital",
        "item_type": "TIMED",
        "is_required": True,
        "scheduled_time": time(h, m),
        "notify_before_minutes": 5,
        "severity": 5,
        "sort_order": base_order,
    })
    base_order += 100


# ---------------------------------------------------------
# TEMPLATE CREATION
# ---------------------------------------------------------

async def create_template():
    async with get_async_connection() as conn:
        template_id = uuid4()

        await conn.execute("""
            INSERT INTO checklist_templates (id, name, description, shift, version)
            VALUES ($1, $2, $3, 'MORNING', 1)
            ON CONFLICT (name, shift, version) DO NOTHING
        """, template_id, "ICT Operations Day Shift (Morning)",
             "Official morning ICT operations checklist",)

        for item in MORNING_CHECKLIST_ITEMS:
            await conn.execute("""
                INSERT INTO checklist_template_items
                (id, template_id, title, description, item_type,
                 is_required, scheduled_time, notify_before_minutes,
                 severity, sort_order)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            """,
            uuid4(), template_id,
            item["title"], item.get("description"),
            item["item_type"], item["is_required"],
            item.get("scheduled_time"),
            item.get("notify_before_minutes", 0),
            item["severity"], item["sort_order"]
            )

        log.info(f"✅ Morning checklist initialized with {len(MORNING_CHECKLIST_ITEMS)} items")


async def main():
    await init_db()
    await create_template()


if __name__ == "__main__":
    asyncio.run(main())
