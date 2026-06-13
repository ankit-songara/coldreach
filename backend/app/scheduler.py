"""
Background scheduler — delivers queued follow-ups when they come due.

A single asyncio task started on app startup. Every POLL_SECONDS it:
  1. checks automation is enabled and Gmail creds are stored
  2. pulls scheduled_emails with status='pending' and send_at <= now
  3. respects the daily send cap (shared with the rest of sending)
  4. sends each via SMTP, marks sent/failed, and advances the contact
     (status='followed_up', followups_sent++, last_emailed_at)

Replies cancel pending follow-ups elsewhere (inbox sync), so anything still
'pending' here is genuinely awaiting a nudge.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from collections import defaultdict

from app.db.database import SessionLocal
from app.db.crud import (
    ConfigRepository, ScheduledEmailRepository, ContactRepository,
    all_due_scheduled, mark_scheduled_sent, mark_scheduled_failed,
)
from app.schemas.contact import ContactUpdate
from app import mailer

log = logging.getLogger(__name__)

POLL_SECONDS = 60
DEFAULT_DAILY_CAP = 50
SEND_GAP_SECONDS = 4   # small jitterable gap between automated sends


class FollowUpScheduler:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run())
        log.info("Follow-up scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        log.info("Follow-up scheduler stopped")

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.to_thread(self._tick)
            except Exception as e:
                log.error(f"Scheduler tick failed: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=POLL_SECONDS)
            except asyncio.TimeoutError:
                pass

    def _tick(self) -> None:
        db = SessionLocal()
        try:
            due_all = all_due_scheduled(db, datetime.utcnow())
            if not due_all:
                return

            # Process per user — each has their own creds, cap and automation flag
            by_user: dict[int, list] = defaultdict(list)
            for item in due_all:
                by_user[item.user_id].append(item)

            for user_id, items in by_user.items():
                self._process_user(db, user_id, items)
        finally:
            db.close()

    def _process_user(self, db, user_id: int, items: list) -> None:
        cfg = ConfigRepository(db, user_id)
        address, password = cfg.get_gmail_creds()
        if not address or not password:
            return  # this user has no sending creds

        automation_on = cfg.automation_enabled()
        contacts = ContactRepository(db, user_id)
        sched = ScheduledEmailRepository(db, user_id)

        # First-touch scheduled sends always fire; follow-ups need automation on
        due = [i for i in items if (not i.is_followup) or automation_on]
        if not due:
            return

        cap = int(cfg.get("daily_send_cap", str(DEFAULT_DAILY_CAP)) or DEFAULT_DAILY_CAP)
        budget = max(0, cap - self._sent_today(contacts))
        if budget <= 0:
            log.info(f"[user {user_id}] daily cap {cap} reached; deferring {len(due)}")
            return

        for item in due[:budget]:
            contact = contacts.get_by_id(item.contact_id)
            if not contact:
                mark_scheduled_failed(db, item.id, "contact deleted")
                continue
            if contact.replied_at or contact.status in ("replied", "interview", "rejected", "bounced"):
                sched.cancel(item.id)
                continue
            # Never send to addresses we already know are bad — protects the
            # sending account's reputation (mirrors the bulk-send guard).
            if contact.bounced or contact.email_status == "invalid":
                sched.cancel(item.id)
                log.info(f"[user {user_id}] skipped {contact.email}: known invalid/bounced")
                continue
            try:
                mailer.send_email(address, password, contact.email, item.subject, item.body)
                mark_scheduled_sent(db, item.id)
                if item.is_followup:
                    contacts.update(contact.id, ContactUpdate(
                        status="followed_up",
                        last_emailed_at=datetime.utcnow(),
                        followups_sent=(contact.followups_sent or 0) + 1,
                    ))
                else:
                    contacts.update(contact.id, ContactUpdate(
                        status="emailed",
                        last_emailed_at=datetime.utcnow(),
                    ))
                log.info(f"[user {user_id}] scheduled email sent to {contact.email}")
            except Exception as e:
                mark_scheduled_failed(db, item.id, str(e))
                log.error(f"[user {user_id}] send to {contact.email} failed: {e}")

    @staticmethod
    def _sent_today(contacts: ContactRepository) -> int:
        since = datetime.utcnow() - timedelta(hours=24)
        return sum(
            1 for c in contacts.get_all()
            if c.last_emailed_at and c.last_emailed_at >= since
        )


scheduler = FollowUpScheduler()
