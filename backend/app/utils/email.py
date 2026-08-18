"""
backend/app/utils/email.py

Best-effort outbound email sender (grievance acknowledgements).

Contract:
  * Everything is best-effort: a failure to send NEVER raises and NEVER
    blocks the grievance submission. EMAIL_ENABLED defaults to OFF, so the
    service runs without any SMTP configuration.
  * No secrets are logged; credentials come from settings only.
"""

from __future__ import annotations

import logging
import smtplib
from datetime import datetime
from email.message import EmailMessage

from app.config import settings

_log = logging.getLogger("cus_ai")

_RETRYABLE_ERRORS = (
    OSError,  # connection refused / reset / DNS / timeout
    TimeoutError,
    smtplib.SMTPServerDisconnected,
)


def enabled() -> bool:
    return bool(settings.EMAIL_ENABLED and settings.SMTP_HOST)


def _mask_email(email: str) -> str:
    """Mask a recipient address for safe logs (never logs full addresses)."""
    email = (email or "").strip()
    local, sep, domain = email.partition("@")
    if not sep or not local or not domain:
        return "invalid-address"
    return local[0] + "***@" + domain


def _fmt_dt(value: datetime | None, fallback: str = "—") -> str:
    """Safe timestamp formatting (never raises, never leaks tz internals)."""
    if value is None:
        return fallback
    try:
        return value.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return fallback


def _deliver(
    to_email: str, subject: str, body: str, event: str | None = None
) -> bool:
    """Deliver one message via SMTP. Never raises; True only on acceptance.

    Adds safe per-attempt diagnostics ([EMAIL]/[EMAIL FAILURE] log lines with
    masked recipient) and a bounded retry (exactly one immediate re-attempt)
    applied ONLY to transient transport errors; misconfiguration and provider
    rejections are reported immediately and never retried.
    """
    if not enabled():
        _log.warning(
            "[EMAIL FAILURE] event=%s recipient=%s provider=smtp "
            "error_type=NOT_CONFIGURED retryable=false reason=%s",
            event or "?",
            _mask_email(to_email),
            "EMAIL_ENABLED/SMTP_HOST not configured",
        )
        return False
    if not to_email:
        _log.warning(
            "[EMAIL FAILURE] event=%s recipient=%s provider=smtp "
            "error_type=NO_RECIPIENT retryable=false",
            event or "?",
            _mask_email(to_email),
        )
        return False
    if not settings.MAIL_FROM:
        _log.warning(
            "[EMAIL FAILURE] event=%s recipient=%s provider=smtp "
            "error_type=CONFIG retryable=false reason=MAIL_FROM not set",
            event or "?",
            _mask_email(to_email),
        )
        return False

    msg = EmailMessage()
    from_name = settings.MAIL_FROM_NAME or "CUS Grievance Cell"
    msg["From"] = f"{from_name} <{settings.MAIL_FROM}>"
    msg["To"] = to_email
    msg["Subject"] = subject[:200]
    msg.set_content(body)

    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            with smtplib.SMTP(
                settings.SMTP_HOST, settings.SMTP_PORT, timeout=15
            ) as smtp:
                smtp.ehlo()
                if settings.SMTP_STARTTLS:
                    smtp.starttls()
                if settings.SMTP_USER and settings.SMTP_PASSWORD:
                    smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
                smtp.send_message(msg)
            _log.info(
                "[EMAIL] event=%s recipient=%s provider=smtp status=ACCEPTED",
                event or "?",
                _mask_email(to_email),
            )
            return True
        except Exception as exc:  # noqa: BLE001 — best-effort by design
            last_error = exc
            if attempt == 1 and isinstance(exc, _RETRYABLE_ERRORS):
                continue
            break
    _log.warning(
        "[EMAIL FAILURE] event=%s recipient=%s provider=smtp "
        "error_type=%s retryable=%s attempts=%s",
        event or "?",
        _mask_email(to_email),
        type(last_error).__name__ if last_error is not None else "UNKNOWN",
        "true" if isinstance(last_error, _RETRYABLE_ERRORS) else "false",
        2 if isinstance(last_error, _RETRYABLE_ERRORS) else 1,
    )
    return False


def send_grievance_acknowledgement(
    to_email: str,
    reference: str,
    subject: str,
    authority_name: str | None,
    student_first_name: str | None = None,
    submitted_on: datetime | None = None,
) -> bool:
    """Send the grievance-submission confirmation.

    Returns True only when the message was accepted by the SMTP server.
    Never raises. On failure the submission flow records email_status="failed".
    """
    if not to_email or not enabled():
        return False

    addressee = (student_first_name or "").strip() or "Student"
    body = (
        f"Dear {addressee},\n\n"
        f"Your grievance has been successfully submitted through the "
        f"CUS AI Grievance System.\n\n"
        f"Reference Number: {reference}\n"
        f"Authority: {authority_name or 'Grievance Cell'}\n"
        f"Category: {subject or 'Grievance'}\n"
        f"Submitted: {_fmt_dt(submitted_on)}\n"
        f"Status: Submitted\n\n"
        f"Your grievance has been forwarded to the concerned authority and "
        f"will be reviewed in order of receipt. Keep your reference number "
        f"and tracking token safe — they are the only way to check the "
        f"status of this complaint.\n\n"
        f"Regards,\nCluster University of Srinagar\nCUS AI Grievance System"
    )
    return _deliver(
        to_email,
        f"Grievance Submitted Successfully — {reference}",
        body,
        event="GRIEVANCE_SUBMITTED_STUDENT",
    )


def send_grievance_to_authority(
    to_email: str,
    reference: str,
    category: str,
    grievance_text: str,
    student: dict[str, str | None],
    authority_name: str,
    submitted_on: datetime | None = None,
) -> bool:
    """Notify the selected authority of a new grievance.

    `student` carries only the self-reported details the authority needs to
    act (name, roll number, college, semester, email). Returns True only when
    the SMTP server accepted the message; never raises.
    """
    if not to_email or not enabled():
        return False

    details = []
    for label, key in (
        ("Name", "name"),
        ("Roll number", "roll_number"),
        ("College", "college"),
        ("Semester", "semester"),
        ("Student email", "email"),
    ):
        val = (student.get(key) or "").strip()
        if val:
            details.append(f"{label}: {val}")
    details_block = "\n".join(details) if details else "Student details: not provided"

    body = (
        f"Respected Sir/Madam,\n\n"
        f"A student has submitted the following grievance through the "
        f"Cluster University of Srinagar Grievance Cell.\n\n"
        f"Reference Number: {reference}\n"
        f"Category/Subject: {category or 'Grievance'}\n"
        f"Submitted: {_fmt_dt(submitted_on)}\n\n"
        f"Grievance text:\n"
        f"----------\n"
        f"{grievance_text}\n"
        f"----------\n\n"
        f"Student information:\n"
        f"{details_block}\n\n"
    )
    if settings.PUBLIC_BASE_URL:
        body += (
            f"Please log in to the Authority Administration dashboard to "
            f"review and respond to this grievance:\n"
            f"{settings.PUBLIC_BASE_URL.rstrip('/')}/authority-admin/dashboard\n\n"
        )
    body += "Regards,\nCUS Grievance Cell — Cluster University of Srinagar"
    return _deliver(
        to_email,
        f"New Grievance — {reference}: {category or 'Grievance submission'}",
        body,
        event="GRIEVANCE_SUBMITTED_AUTHORITY",
    )


def send_grievance_response(
    to_email: str,
    reference: str,
    status: str,
    authority_name: str | None,
    response: str,
    responded_at: datetime | None = None,
) -> bool:
    """Send the authority's official response to the student.

    Returns True only when the SMTP server accepted the message; never raises.
    """
    if not to_email or not enabled():
        return False

    normalize = {
        "submitted": "Submitted",
        "acknowledged": "Acknowledged",
        "in_progress": "In Progress",
        "resolved": "Resolved",
        "closed": "Closed",
        "rejected": "Rejected",
    }
    status_label = normalize.get((status or "").lower(), (status or "Updated").title())

    body = (
        f"Dear Student,\n\n"
        f"Thank you for contacting Cluster University of Srinagar.\n"
        f"The concerned authority has recorded an official response to your "
        f"grievance.\n\n"
        f"Reference: {reference}\n"
        f"Authority: {authority_name or 'Grievance Cell'}\n"
        f"Status: {status_label}\n"
        f"Response Date/Time: {_fmt_dt(responded_at)}\n\n"
        f"Official response:\n"
        f"----------\n"
        f"{response}\n"
        f"----------\n\n"
        f"You can track the status of your grievance at any time using your "
        f"reference number and tracking token.\n\n"
        f"Regards,\nCluster University Srinagar — Grievance Cell"
    )
    return _deliver(
        to_email,
        f"Response to Your Grievance — {reference}",
        body,
        event="GRIEVANCE_RESPONSE",
    )


def send_grievance_acknowledged(
    to_email: str,
    reference: str,
    authority_name: str | None,
    student_first_name: str | None = None,
    acknowledged_at: datetime | None = None,
    final_response: str | None = None,
) -> bool:
    """Notify the student that their grievance has been ACKNOWLEDGED.

    Triggered automatically when an authority marks a grievance as
    acknowledged. Returns True only when the SMTP server accepted the message;
    never raises (delivery state is recorded by the notification service).
    """
    if not to_email or not enabled():
        return False

    addressee = (student_first_name or "").strip() or "Student"
    body = (
        f"Dear {addressee},\n\n"
        f"Good news — your grievance has been acknowledged and is now being "
        f"taken up by the concerned authority.\n\n"
        f"Reference: {reference}\n"
        f"Authority: {authority_name or 'Grievance Cell'}\n"
        f"Acknowledgement Date/Time: {_fmt_dt(acknowledged_at)}\n"
        f"Status: Acknowledged\n\n"
        f"The authority will review your complaint and respond in due course. "
        f"You can track progress at any time using your reference number and "
        f"tracking token.\n\n"
        f"Regards,\nCluster University Srinagar — Grievance Cell"
    )
    if final_response:
        body += (
            f"\nOfficial response:\n"
            f"----------\n"
            f"{final_response}\n"
            f"----------\n"
        )
    return _deliver(
        to_email,
        f"Grievance Acknowledged — {reference}",
        body,
        event="GRIEVANCE_ACKNOWLEDGED",
    )


def send_grievance_resolved(
    to_email: str,
    reference: str,
    authority_name: str | None,
    student_first_name: str | None = None,
    resolved_at: datetime | None = None,
    final_response: str | None = None,
) -> bool:
    """Notify the student that their grievance has been RESOLVED.

    Triggered automatically when an authority marks a grievance as resolved.
    Returns True only when the SMTP server accepted the message; never raises
    (delivery state is recorded by the notification service).
    """
    if not to_email or not enabled():
        return False

    addressee = (student_first_name or "").strip() or "Student"
    body = (
        f"Dear {addressee},\n\n"
        f"We are happy to inform you that your grievance has been resolved.\n\n"
        f"Reference: {reference}\n"
        f"Authority: {authority_name or 'Grievance Cell'}\n"
        f"Resolution Date/Time: {_fmt_dt(resolved_at)}\n"
        f"Status: Resolved\n\n"
    )
    if final_response:
        body += (
            f"The authority's response to your grievance:\n"
            f"----------\n"
            f"{final_response}\n"
            f"----------\n\n"
        )
    body += (
        f"If you still face any difficulty, you may submit a fresh grievance "
        f"through the Cluster University of Srinagar Grievance Cell.\n\n"
        f"Thank you for bringing the matter to our notice.\n\n"
        f"Regards,\nCluster University Srinagar — Grievance Cell"
    )
    return _deliver(
        to_email,
        f"Grievance Resolved — {reference}",
        body,
        event="GRIEVANCE_RESOLVED",
    )


def send_test_email(to_email: str) -> bool:
    """Super-Admin health-check message. Reuses the same best-effort sender.

    Returns True only when the SMTP server accepted the message; never raises.
    """
    if not to_email or not enabled():
        return False
    body = (
        f"Hello,\n\n"
        f"This is a test message from the Cluster University of Srinagar "
        f"CUS AI Grievance System. If you received this, outbound email is "
        f"working correctly.\n\n"
        f"Regards,\nCUS Grievance Cell — Cluster University of Srinagar"
    )
    return _deliver(
        to_email,
        "Test email — CUS AI Grievance System",
        body,
        event="EMAIL_TEST",
    )


__all__ = [
    "enabled",
    "send_grievance_acknowledged",
    "send_grievance_acknowledgement",
    "send_grievance_resolved",
    "send_grievance_response",
    "send_grievance_to_authority",
    "send_test_email",
]