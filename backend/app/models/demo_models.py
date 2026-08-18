"""
backend/app/models/demo_models.py

Demo/synthetic data ORM models for all student services.
These tables store fictional demo data used for presentation/testing.

Every table has a ForeignKey to students.id so the demo data
is linked to actual student accounts.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base, utcnow
from app.database import _UUID


def _fk_col(**kwargs):
    """UUID foreign key column with sensible defaults."""
    kwargs.setdefault("nullable", False)
    kwargs.setdefault("index", True)
    return Column(_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), **kwargs)


class StudentResult(Base):
    __tablename__ = "student_results"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    exam_type = Column(String(50), default="Regular")
    subject_name = Column(String(200), nullable=False)
    subject_code = Column(String(20), nullable=True)
    internal_marks = Column(Integer, nullable=True)
    external_marks = Column(Integer, nullable=True)
    total_marks = Column(Integer, nullable=True)
    max_marks = Column(Integer, default=100)
    grade = Column(String(5), nullable=True)
    sgpa = Column(String(5), nullable=True)
    cgpa = Column(String(5), nullable=True)
    status = Column(String(20), default="pass")
    academic_year = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class StudentAdmitCard(Base):
    __tablename__ = "student_admit_cards"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    exam_type = Column(String(50), default="Regular")
    exam_session = Column(String(100), nullable=True)
    centre_name = Column(String(200), nullable=True)
    centre_address = Column(Text, nullable=True)
    centre_code = Column(String(20), nullable=True)
    reporting_time = Column(String(50), nullable=True)
    subjects = Column(Text, nullable=True)
    instructions = Column(Text, nullable=True)
    issued_date = Column(String(20), nullable=True)
    academic_year = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class StudentExamForm(Base):
    __tablename__ = "student_exam_forms"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    exam_type = Column(String(50), default="Regular")
    form_status = Column(String(50), default="Pending")
    subjects = Column(Text, nullable=True)
    fee_status = Column(String(50), default="Unpaid")
    fee_amount = Column(Integer, nullable=True)
    transaction_id = Column(String(100), nullable=True)
    submission_date = Column(String(20), nullable=True)
    academic_year = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class FeeReceipt(Base):
    __tablename__ = "fee_receipts"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    receipt_no = Column(String(50), nullable=True)
    transaction_id = Column(String(100), nullable=True)
    fee_heads = Column(Text, nullable=True)
    paid_amount = Column(Integer, nullable=True)
    total_amount = Column(Integer, nullable=True)
    pending_amount = Column(Integer, default=0)
    payment_date = Column(String(20), nullable=True)
    payment_mode = Column(String(50), nullable=True)
    semester = Column(Integer, nullable=True)
    academic_year = Column(String(20), nullable=True)
    status = Column(String(20), default="Paid")
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class StudentAttendance(Base):
    __tablename__ = "student_attendance"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    subject_name = Column(String(200), nullable=False)
    subject_code = Column(String(20), nullable=True)
    total_classes = Column(Integer, nullable=True)
    attended_classes = Column(Integer, nullable=True)
    percentage = Column(String(10), nullable=True)
    academic_year = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class StudentTranscript(Base):
    __tablename__ = "student_transcripts"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    academic_year = Column(String(20), nullable=True)
    credits_earned = Column(Integer, nullable=True)
    total_credits = Column(Integer, nullable=True)
    sgpa = Column(String(5), nullable=True)
    cgpa = Column(String(5), nullable=True)
    status = Column(String(20), default="Completed")
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class MigrationCertificate(Base):
    __tablename__ = "migration_certificates"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    certificate_no = Column(String(50), nullable=True)
    issue_status = Column(String(50), default="Not Applied")
    issue_date = Column(String(20), nullable=True)
    application_date = Column(String(20), nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class Revaluation(Base):
    __tablename__ = "student_revaluations"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    subject_name = Column(String(200), nullable=False)
    subject_code = Column(String(20), nullable=True)
    application_date = Column(String(20), nullable=True)
    status = Column(String(50), default="Pending")
    result = Column(String(100), nullable=True)
    fee_status = Column(String(20), default="Paid")
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class XeroxRequest(Base):
    __tablename__ = "xerox_requests"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    paper_name = Column(String(200), nullable=True)
    application_date = Column(String(20), nullable=True)
    fee_status = Column(String(20), default="Paid")
    request_status = Column(String(50), default="Processing")
    estimated_date = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class BacklogStatus(Base):
    __tablename__ = "student_backlogs"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    subject_name = Column(String(200), nullable=False)
    subject_code = Column(String(20), nullable=True)
    exam_type = Column(String(50), default="Backlog")
    status = Column(String(20), default="Pending")
    improvement_applied = Column(Boolean, default=False)
    cleared_date = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class CourseRegistration(Base):
    __tablename__ = "course_registrations"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    semester = Column(Integer, nullable=False)
    academic_year = Column(String(20), nullable=True)
    elective_subjects = Column(Text, nullable=True)
    registered_subjects = Column(Text, nullable=True)
    registration_date = Column(String(20), nullable=True)
    status = Column(String(20), default="Registered")
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


class HelpdeskTicket(Base):
    __tablename__ = "helpdesk_tickets"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = _fk_col()
    ticket_id = Column(String(50), nullable=True)
    category = Column(String(100), nullable=True)
    subject = Column(String(200), nullable=True)
    description = Column(Text, nullable=True)
    status = Column(String(50), default="Open")
    assigned_officer = Column(String(200), nullable=True)
    assigned_department = Column(String(200), nullable=True)
    resolution = Column(Text, nullable=True)
    created_date = Column(String(20), nullable=True)
    resolved_date = Column(String(20), nullable=True)
    created_at = Column(DateTime, default=utcnow)

    student = relationship("Student")


__all__ = [
    "BacklogStatus",
    "CourseRegistration",
    "FeeReceipt",
    "HelpdeskTicket",
    "MigrationCertificate",
    "Revaluation",
    "StudentAdmitCard",
    "StudentAttendance",
    "StudentExamForm",
    "StudentResult",
    "StudentTranscript",
    "XeroxRequest",
]

# Register the string-referenced relationship target (Student) so this module
# is also importable standalone (e.g. by one-off maintenance scripts).
from app.models.db_models import Student as _Student  # noqa: E402, F401
