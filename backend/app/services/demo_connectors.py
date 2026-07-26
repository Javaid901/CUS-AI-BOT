"""
backend/app/services/demo_connectors.py

All student service connectors backed by synthetic demo data in PostgreSQL.
Replaces the placeholder connectors when DEMO_MODE is enabled.

Each connector queries the corresponding demo table and returns
realistic ServiceResult data for the frontend detail card.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy.orm import Session

_log = logging.getLogger("cus")

from app.services.base import ServiceConnector, ServiceResult

# ---------------------------------------------------------------------------
# Helper: get DB session
# ---------------------------------------------------------------------------

def _db():
    from app.database import SessionLocal
    return SessionLocal()


def _safe_json_load(value: Any, default: Any = None) -> Any:
    """Safely parse a JSON value that may be a string, already-deserialized, or None."""
    if value is None:
        return default
    if isinstance(value, (dict, list, int, float)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError, TypeError):
            return default
    return default


def _get_student(params: dict, db: Session | None = None) -> dict | None:
    """Look up a student by reg_no from params. Returns student info dict or None."""
    reg_no = params.get("reg_no")
    if not reg_no:
        _log.warning("_get_student: no reg_no in params (keys=%s)", list(params.keys()))
        return None
    own_session = False
    if db is None:
        db = _db()
        own_session = True
    try:
        from app.models import Student
        s = db.query(Student).filter(Student.reg_no == reg_no).first()
        if not s:
            _log.warning("_get_student: student not found for reg_no=%s", reg_no)
            return None
        _log.info("_get_student: found student id=%s reg_no=%s programme=%s sem=%s", s.id, s.reg_no, s.programme, s.current_semester)
        return {
            "id": s.id,
            "reg_no": s.reg_no,
            "roll_no": s.roll_no,
            "name": s.name,
            "father_name": s.father_name,
            "mother_name": s.mother_name,
            "dob": s.dob,
            "gender": s.gender,
            "category": s.category,
            "email": s.email,
            "phone": s.phone,
            "college": s.college,
            "programme": s.programme,
            "semester": s.current_semester,
            "admission_year": s.admission_year,
            "batch": s.batch,
            "address": s.address,
            "status": s.status,
        }
    finally:
        if own_session:
            db.close()


# ===================================================================
# 1. Results
# ===================================================================

class ResultsConnector(ServiceConnector):
    name = "results"
    display_name = "Results"
    description = "Semester exam results, grade cards, SGPA/CGPA"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import StudentResult
            sem = params.get("semester")
            _log.info("ResultsConnector: student_id=%s reg_no=%s sem=%s", student["id"], student["reg_no"], sem)
            query = db.query(StudentResult).filter(StudentResult.student_id == student["id"])
            if sem:
                try:
                    query = query.filter(StudentResult.semester == int(sem))
                except (ValueError, TypeError):
                    pass
            results = query.order_by(StudentResult.semester, StudentResult.subject_name).all()
            _log.info("ResultsConnector: rows found=%d", len(results))

            if not results:
                return ServiceResult(success=True, data={
                    "title": "Examination Results",
                    "message": f"No results found for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Programme", "value": student["programme"].upper()},
                        {"label": "Semester", "value": str(student["semester"])},
                        {"label": "Status", "value": "No results available"},
                    ],
                    "actions": [
                        {"id": "sem1", "label": "Semester 1"},
                        {"id": "sem2", "label": "Semester 2"},
                        {"id": "sem3", "label": "Semester 3"},
                        {"id": "sem4", "label": "Semester 4"},
                    ],
                })

            semesters_seen = set()
            for r in results:
                semesters_seen.add(r.semester)
            sem_actions = [
                {"id": f"sem{s}", "label": f"Semester {s}"}
                for s in sorted(semesters_seen)
            ]

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Semester", "value": str(results[0].semester)},
            ]

            for r in results:
                fields.append({"label": r.subject_name, "value": f"Int: {r.internal_marks or '-'} / Ext: {r.external_marks or '-'} / Total: {r.total_marks or '-'} / Grade: {r.grade or '-'}"})

            sgpa = results[0].sgpa if results[0].sgpa != "0.00" else None
            if sgpa:
                fields.append({"label": "SGPA", "value": sgpa})

            return ServiceResult(success=True, data={
                "title": "Examination Results",
                "message": f"Results for {student['name']} ({student['reg_no']}) -- {student['programme'].upper()}.",
                "fields": fields,
                "actions": sem_actions,
            })
        finally:
            db.close()


# ===================================================================
# 2. Admit Card
# ===================================================================

class AdmitCardConnector(ServiceConnector):
    name = "admit_card"
    display_name = "Admit Card"
    description = "Download exam admit cards / hall tickets"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import StudentAdmitCard
            _log.info("AdmitCardConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            ac = db.query(StudentAdmitCard).filter(
                StudentAdmitCard.student_id == student["id"],
                StudentAdmitCard.semester == student["semester"],
            ).first()
            _log.info("AdmitCardConnector: row found=%s", ac is not None)
            if not ac:
                return ServiceResult(success=True, data={
                    "title": "Admit Card / Hall Ticket",
                    "message": f"No admit card issued yet for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Programme", "value": student["programme"].upper()},
                        {"label": "Semester", "value": str(student["semester"])},
                        {"label": "Status", "value": "Not Available"},
                    ],
                    "actions": [],
                })

            subjects_list = _safe_json_load(ac.subjects, [])
            instructions_list = _safe_json_load(ac.instructions, [])

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Semester", "value": str(ac.semester)},
                {"label": "Examination", "value": f"{ac.semester} Semester {ac.exam_type}"},
                {"label": "Exam Session", "value": ac.exam_session or "-"},
                {"label": "Exam Centre", "value": ac.centre_name or "-"},
                {"label": "Centre Code", "value": ac.centre_code or "-"},
                {"label": "Reporting Time", "value": ac.reporting_time or "-"},
                {"label": "Subjects", "value": ", ".join(subjects_list) if subjects_list else "-"},
                {"label": "Instructions", "value": " | ".join(instructions_list) if instructions_list else "-"},
                {"label": "Issued Date", "value": ac.issued_date or "-"},
            ]
            return ServiceResult(success=True, data={
                "title": "Admit Card / Hall Ticket",
                "message": "Your admit card is available for the following examinations.",
                "fields": fields,
                "actions": [
                    {"id": "download_admit", "label": "Download Admit Card"},
                    {"id": "exam_schedule", "label": "View Exam Schedule"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 3. Exam Form
# ===================================================================

class ExamFormConnector(ServiceConnector):
    name = "exam_form"
    display_name = "Exam Form"
    description = "Fill and submit examination forms"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import StudentExamForm
            _log.info("ExamFormConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            ef = db.query(StudentExamForm).filter(
                StudentExamForm.student_id == student["id"],
                StudentExamForm.semester == student["semester"],
            ).first()
            _log.info("ExamFormConnector: row found=%s", ef is not None)
            if not ef:
                return ServiceResult(success=True, data={
                    "title": "Examination Form",
                    "message": f"No exam form found for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Semester", "value": str(student["semester"])},
                        {"label": "Status", "value": "No form found"},
                    ],
                    "actions": [],
                })

            subjects_list = _safe_json_load(ef.subjects, [])
            return ServiceResult(success=True, data={
                "title": "Examination Form",
                "message": f"Examination form status for {student['name']} ({student['reg_no']}).",
                "fields": [
                    {"label": "Student Name", "value": student["name"]},
                    {"label": "Registration No.", "value": student["reg_no"]},
                    {"label": "Programme", "value": student["programme"].upper()},
                    {"label": "Semester", "value": str(ef.semester)},
                    {"label": "Exam Type", "value": ef.exam_type or "Regular"},
                    {"label": "Form Status", "value": ef.form_status or "Pending"},
                    {"label": "Fee Status", "value": ef.fee_status or "Unpaid"},
                    {"label": "Fee Amount", "value": f"₹ {ef.fee_amount:,}" if ef.fee_amount else "-"},
                    {"label": "Transaction ID", "value": ef.transaction_id or "-"},
                    {"label": "Submission Date", "value": ef.submission_date or "-"},
                    {"label": "Registered Subjects", "value": ", ".join(subjects_list) if subjects_list else "-"},
                ],
                "actions": [
                    {"id": "fill_form", "label": "Fill Examination Form"},
                    {"id": "view_fee", "label": "View Exam Fee"},
                    {"id": "previous_forms", "label": "Previous Forms"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 4. Fee Receipt
# ===================================================================

class FeeConnector(ServiceConnector):
    name = "fee"
    display_name = "Fee Receipt"
    description = "View fee receipts, payment history, and dues"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import FeeReceipt
            _log.info("FeeConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            receipts = db.query(FeeReceipt).filter(
                FeeReceipt.student_id == student["id"],
            ).order_by(FeeReceipt.semester).all()
            _log.info("FeeConnector: receipts found=%d", len(receipts))

            if not receipts:
                return ServiceResult(success=True, data={
                    "title": "Fee Details",
                    "message": f"No fee records found for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Status", "value": "No records"},
                    ],
                    "actions": [],
                })

            receipts[-1]
            total_paid = sum(r.paid_amount or 0 for r in receipts)
            total_amount = sum(r.total_amount or 0 for r in receipts)
            total_pending = total_amount - total_paid

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
            ]

            for r in receipts:
                heads = _safe_json_load(r.fee_heads, {})
                if isinstance(heads, dict):
                    heads_str = ", ".join([f"{k}: ₹{v:,}" for k, v in heads.items() if v])
                else:
                    heads_str = "-"
                fields += [
                    {"label": f"Semester {r.semester} -- Receipt", "value": r.receipt_no or "-"},
                    {"label": "  Fee Heads", "value": heads_str or "-"},
                    {"label": "  Paid", "value": f"₹ {r.paid_amount:,}" if r.paid_amount else "-"},
                    {"label": "  Date", "value": r.payment_date or "-"},
                ]

            fields += [
                {"label": "Total Fee", "value": f"₹ {total_amount:,}"},
                {"label": "Total Paid", "value": f"₹ {total_paid:,}"},
                {"label": "Total Due", "value": f"₹ {total_pending:,}" if total_pending else "No dues"},
            ]
            return ServiceResult(success=True, data={
                "title": "Fee Details",
                "message": f"Fee account summary for {student['name']} ({student['reg_no']}).",
                "fields": fields,
                "actions": [
                    {"id": "download_receipt", "label": "Download Fee Receipt"},
                    {"id": "payment_history", "label": "Payment History"},
                    {"id": "pay_online", "label": "Pay Online"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 5. Attendance
# ===================================================================

class AttendanceConnector(ServiceConnector):
    name = "attendance"
    display_name = "Attendance / Internal Marks"
    description = "View attendance records and internal assessment marks"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import StudentAttendance
            _log.info("AttendanceConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            records = db.query(StudentAttendance).filter(
                StudentAttendance.student_id == student["id"],
                StudentAttendance.semester == student["semester"],
            ).all()
            _log.info("AttendanceConnector: records found=%d", len(records))

            if not records:
                return ServiceResult(success=True, data={
                    "title": "Attendance & Internal Marks",
                    "message": f"No attendance records found for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Status", "value": "No records"},
                    ],
                    "actions": [],
                })

            percentages = []
            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Semester", "value": f"Current: {student['semester']}"},
            ]
            for r in records:
                pct_str = r.percentage or "0%"
                try:
                    percentages.append(float(pct_str.replace("%", "")))
                except (ValueError, TypeError):
                    percentages.append(0.0)
                fields.append({
                    "label": r.subject_name,
                    "value": f"{r.attended_classes}/{r.total_classes} ({pct_str})",
                })

            overall = sum(percentages) / len(percentages) if percentages else 0
            fields.append({"label": "Overall Attendance", "value": f"{overall:.1f}%"})
            warning = ""
            if overall < 75:
                warning = "⚠️ Your attendance is below 75%. Please attend classes regularly to avoid debarment."
            elif overall < 85:
                warning = "ℹ️ Maintain your attendance above 85% for better internal marks."

            return ServiceResult(success=True, data={
                "title": "Attendance & Internal Marks",
                "message": f"Attendance records for {student['name']} ({student['reg_no']}). {warning}",
                "fields": fields,
                "actions": [
                    {"id": "subject_wise", "label": "Subject-wise Attendance"},
                    {"id": "internal_marks", "label": "Internal Marks Details"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 6. Course Registration
# ===================================================================

class RegistrationConnector(ServiceConnector):
    name = "registration"
    display_name = "Course Registration"
    description = "Register for courses each semester"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import CourseRegistration
            _log.info("RegistrationConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            cr = db.query(CourseRegistration).filter(
                CourseRegistration.student_id == student["id"],
                CourseRegistration.semester == student["semester"],
            ).first()
            _log.info("RegistrationConnector: row found=%s", cr is not None)
            if not cr:
                return ServiceResult(success=True, data={
                    "title": "Course Registration",
                    "message": f"No course registration found for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Status", "value": "Not Registered"},
                    ],
                    "actions": [],
                })

            registered = _safe_json_load(cr.registered_subjects, [])
            electives = _safe_json_load(cr.elective_subjects, [])
            return ServiceResult(success=True, data={
                "title": "Course Registration",
                "message": f"Course registration for {student['name']} ({student['reg_no']}).",
                "fields": [
                    {"label": "Student Name", "value": student["name"]},
                    {"label": "Registration No.", "value": student["reg_no"]},
                    {"label": "Semester", "value": str(cr.semester)},
                    {"label": "Academic Year", "value": cr.academic_year or "-"},
                    {"label": "Registration Date", "value": cr.registration_date or "-"},
                    {"label": "Status", "value": cr.status or "-"},
                    {"label": "Registered Subjects", "value": ", ".join(registered) if registered else "-"},
                    {"label": "Elective Subjects", "value": ", ".join(electives) if electives else "None"},
                ],
                "actions": [
                    {"id": "register_courses", "label": "Register for Courses"},
                    {"id": "view_registered", "label": "View Registered Courses"},
                    {"id": "drop_course", "label": "Drop Course"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 7. Migration Certificate
# ===================================================================

class MigrationConnector(ServiceConnector):
    name = "migration"
    display_name = "Migration Certificate"
    description = "Apply for and download migration certificates"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import MigrationCertificate
            _log.info("MigrationConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            mc = db.query(MigrationCertificate).filter(
                MigrationCertificate.student_id == student["id"],
            ).first()
            _log.info("MigrationConnector: row found=%s", mc is not None)
            if not mc:
                return ServiceResult(success=True, data={
                    "title": "Migration Certificate",
                    "message": f"No migration certificate record for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Status", "value": "No record"},
                    ],
                    "actions": [],
                })
            return ServiceResult(success=True, data={
                "title": "Migration Certificate",
                "message": f"Migration certificate status for {student['name']} ({student['reg_no']}).",
                "fields": [
                    {"label": "Student Name", "value": student["name"]},
                    {"label": "Registration No.", "value": student["reg_no"]},
                    {"label": "Programme", "value": student["programme"].upper()},
                    {"label": "Certificate No.", "value": mc.certificate_no or "-"},
                    {"label": "Issue Status", "value": mc.issue_status or "Not Available"},
                    {"label": "Issue Date", "value": mc.issue_date or "-"},
                    {"label": "Application Date", "value": mc.application_date or "-"},
                    {"label": "Reason", "value": mc.reason or "-"},
                ],
                "actions": [
                    {"id": "apply_migration", "label": "Apply for Migration"},
                    {"id": "download_migration", "label": "Download Certificate"},
                    {"id": "check_status", "label": "Check Application Status"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 8. Transcript
# ===================================================================

class TranscriptConnector(ServiceConnector):
    name = "transcript"
    display_name = "Transcript"
    description = "Request official transcripts and academic records"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import StudentTranscript
            _log.info("TranscriptConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            transcripts = db.query(StudentTranscript).filter(
                StudentTranscript.student_id == student["id"],
            ).order_by(StudentTranscript.semester).all()
            _log.info("TranscriptConnector: transcripts found=%d", len(transcripts))

            if not transcripts:
                return ServiceResult(success=True, data={
                    "title": "Transcript",
                    "message": f"No transcript records for {student['name']}.",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Status", "value": "No records"},
                    ],
                    "actions": [],
                })

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
            ]
            for t in transcripts:
                fields += [
                    {"label": f"Semester {t.semester} ({t.academic_year})", "value": f"Credits: {t.credits_earned}/{t.total_credits} | SGPA: {t.sgpa} | CGPA: {t.cgpa}"},
                ]

            latest = transcripts[-1]
            fields.append({"label": "Overall CGPA", "value": latest.cgpa or "Not computed"})

            return ServiceResult(success=True, data={
                "title": "Transcript",
                "message": f"Academic transcript for {student['name']} ({student['reg_no']}).",
                "fields": fields,
                "actions": [
                    {"id": "request_transcript", "label": "Request Transcript"},
                    {"id": "download_transcript", "label": "Download Available Transcript"},
                    {"id": "track_request", "label": "Track Request Status"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 9. Degree Status
# ===================================================================

class DegreeConnector(ServiceConnector):
    name = "degree"
    display_name = "Degree Status"
    description = "Check degree issuance status and download degree"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        student = _get_student(params)
        if not student:
            return ServiceResult(success=False, error="Student not found.")
        _log.info("DegreeConnector: reg_no=%s programme=%s sem=%s", student["reg_no"], student["programme"], student["semester"])
        max_sem = {"bca": 6, "bba": 6, "bsc": 6, "ba": 6, "bcom": 6, "mca": 4, "mba": 4, "ma": 4, "msc": 4}
        prog_max = max_sem.get(student["programme"], 6)
        is_final = student["semester"] >= prog_max

        degree_status = "Issued -- Awaiting Collection" if is_final else "In Progress"
        prog_name = student["programme"].upper()
        if is_final:
            degree_message = f"Your {prog_name} degree has been issued. You can collect it from the college office."
        else:
            degree_message = f"Your {prog_name} programme is in progress. Degree will be processed after semester {prog_max}."

        return ServiceResult(success=True, data={
            "title": "Degree Status",
            "message": degree_message,
            "fields": [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": prog_name},
                {"label": "Year of Admission", "value": str(student["admission_year"])},
                {"label": "Degree Status", "value": degree_status},
            ],
            "actions": [
                {"id": "track_degree", "label": "Track Degree Status"},
                {"id": "download_degree", "label": "Download Degree"},
                {"id": "verify_degree", "label": "Verify Degree"},
            ],
        })


# ===================================================================
# 10. Backlog Status
# ===================================================================

class BacklogConnector(ServiceConnector):
    name = "backlog"
    display_name = "Backlog Status"
    description = "View backlog subjects, apply for improvement exams"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import BacklogStatus
            _log.info("BacklogConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            backlogs = db.query(BacklogStatus).filter(
                BacklogStatus.student_id == student["id"],
            ).all()
            _log.info("BacklogConnector: backlogs found=%d", len(backlogs))

            if not backlogs:
                return ServiceResult(success=True, data={
                    "title": "Backlog / Improvement Status",
                    "message": f"No backlogs found for {student['name']}. Keep up the good work!",
                    "fields": [
                        {"label": "Student Name", "value": student["name"]},
                        {"label": "Registration No.", "value": student["reg_no"]},
                        {"label": "Programme", "value": student["programme"].upper()},
                        {"label": "Total Backlogs", "value": "No backlogs"},
                        {"label": "Pending Subjects", "value": "None"},
                    ],
                    "actions": [
                        {"id": "backlog_details", "label": "View Backlog Details"},
                        {"id": "improvement_form", "label": "Improvement Exam Form"},
                    ],
                })

            pending = [b for b in backlogs if b.status == "Pending"]
            cleared = [b for b in backlogs if b.status == "Cleared"]
            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Total Backlogs", "value": str(len(backlogs))},
                {"label": "Pending Subjects", "value": str(len(pending))},
                {"label": "Cleared Subjects", "value": str(len(cleared))},
            ]
            for b in backlogs:
                fields.append({
                    "label": f"Sem {b.semester} -- {b.subject_name}",
                    "value": f"Status: {b.status} | Improvement: {'Applied' if b.improvement_applied else 'Not Applied'} | Cleared: {b.cleared_date or '-'}",
                })
            return ServiceResult(success=True, data={
                "title": "Backlog / Improvement Status",
                "message": f"Backlog status for {student['name']} ({student['reg_no']}).",
                "fields": fields,
                "actions": [
                    {"id": "backlog_details", "label": "View Backlog Details"},
                    {"id": "improvement_form", "label": "Improvement Exam Form"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 11. Student Profile
# ===================================================================

class ProfileConnector(ServiceConnector):
    name = "profile"
    display_name = "Student Profile"
    description = "View personal and academic profile information"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        student = _get_student(params)
        if not student:
            return ServiceResult(success=False, error="Student not found.")
        _log.info("ProfileConnector: reg_no=%s name=%s", student["reg_no"], student["name"])
        return ServiceResult(success=True, data={
            "title": "Student Profile",
            "message": f"Profile information for {student['name']}.",
            "fields": [
                {"label": "Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Roll No.", "value": student["roll_no"] or "-"},
                {"label": "Father's Name", "value": student["father_name"] or "-"},
                {"label": "Mother's Name", "value": student["mother_name"] or "-"},
                {"label": "Date of Birth", "value": student["dob"] or "-"},
                {"label": "Gender", "value": student["gender"] or "-"},
                {"label": "Category", "value": student["category"] or "-"},
                {"label": "Email", "value": student["email"] or "-"},
                {"label": "Phone", "value": student["phone"] or "-"},
                {"label": "College", "value": student["college"] or "-"},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Current Semester", "value": str(student["semester"])},
                {"label": "Admission Year", "value": str(student["admission_year"])},
                {"label": "Batch", "value": student["batch"] or "-"},
                {"label": "Address", "value": student["address"] or "-"},
                {"label": "Status", "value": student["status"] or "Active"},
            ],
            "actions": [
                {"id": "edit_profile", "label": "Edit Profile"},
                {"id": "change_password", "label": "Change Password"},
            ],
        })


# ===================================================================
# 12. Re-evaluation
# ===================================================================

class ReEvaluationConnector(ServiceConnector):
    name = "re_evaluation"
    display_name = "Re-evaluation / Rechecking"
    description = "Apply for answer script re-evaluation and rechecking"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import Revaluation
            _log.info("ReEvaluationConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            revals = db.query(Revaluation).filter(
                Revaluation.student_id == student["id"],
            ).all()
            _log.info("ReEvaluationConnector: revals found=%d", len(revals))

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Fee per Subject", "value": "₹ 500"},
                {"label": "Application Period", "value": "Open"},
            ]
            if revals:
                for r in revals:
                    fields.append({
                        "label": f"Sem {r.semester} -- {r.subject_name}",
                        "value": f"Status: {r.status} | Result: {r.result or '-'} | Fee: {r.fee_status}",
                    })
            else:
                fields.append({"label": "No Applications", "value": "You have not applied for any re-evaluation yet."})

            return ServiceResult(success=True, data={
                "title": "Re-evaluation / Rechecking",
                "message": f"Re-evaluation status for {student['name']} ({student['reg_no']}).",
                "fields": fields,
                "actions": [
                    {"id": "apply_reval", "label": "Apply for Re-evaluation"},
                    {"id": "check_reval_status", "label": "Check Application Status"},
                    {"id": "reval_faq", "label": "Re-evaluation FAQ"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 13. Xerox Copy
# ===================================================================

class XeroxCopyConnector(ServiceConnector):
    name = "xerox_copy"
    display_name = "Xerox / Photocopy"
    description = "Request xerox copies of answer scripts"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                return ServiceResult(success=False, error="Student not found.")
            from app.models.demo_models import XeroxRequest
            _log.info("XeroxCopyConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            xerox = db.query(XeroxRequest).filter(
                XeroxRequest.student_id == student["id"],
            ).all()
            _log.info("XeroxCopyConnector: xerox found=%d", len(xerox))

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Fee per Subject", "value": "₹ 200"},
                {"label": "Processing Time", "value": "7-10 working days"},
            ]
            if xerox:
                for x in xerox:
                    fields.append({
                        "label": f"Sem {x.semester} -- {x.paper_name or '-'}",
                        "value": f"Status: {x.request_status} | Fee: {x.fee_status} | Est: {x.estimated_date or '-'}",
                    })
            else:
                fields.append({"label": "No Requests", "value": "You have not requested any xerox copies yet."})

            return ServiceResult(success=True, data={
                "title": "Xerox / Photocopy of Answer Scripts",
                "message": f"Xerox copy requests for {student['name']} ({student['reg_no']}).",
                "fields": fields,
                "actions": [
                    {"id": "request_xerox", "label": "Request Xerox Copy"},
                    {"id": "check_xerox_status", "label": "Check Request Status"},
                ],
            })
        finally:
            db.close()


# ===================================================================
# 14. Semester Admission Form
# ===================================================================

class SemesterAdmissionConnector(ServiceConnector):
    name = "semester_admission"
    display_name = "Semester Admission Form"
    description = "Fill semester admission / re-admission forms"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        if not reg_no or not password:
            return ServiceResult(success=False, error="Registration number and password are required.")
        return ServiceResult(success=True, data={"session_token": f"demo_{reg_no}", "expiry": None})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        student = _get_student(params)
        if not student:
            return ServiceResult(success=False, error="Student not found.")
        _log.info("SemesterAdmissionConnector: reg_no=%s programme=%s sem=%s", student["reg_no"], student["programme"], student["semester"])
        next_sem = student["semester"] + 1
        max_sem = {"bca": 6, "bba": 6, "bsc": 6, "ba": 6, "bcom": 6, "mca": 4, "mba": 4, "ma": 4, "msc": 4}
        prog_max = max_sem.get(student["programme"], 6)
        is_last = student["semester"] >= prog_max

        if is_last:
            return ServiceResult(success=True, data={
                "title": "Semester Admission Form",
                "message": f"You have completed all semesters of {student['programme'].upper()}. No further admission required.",
                "fields": [
                    {"label": "Student Name", "value": student["name"]},
                    {"label": "Registration No.", "value": student["reg_no"]},
                    {"label": "Programme", "value": student["programme"].upper()},
                    {"label": "Status", "value": "Completed"},
                ],
                "actions": [],
            })

        return ServiceResult(success=True, data={
            "title": "Semester Admission Form",
            "message": f"Semester admission for {student['name']} ({student['reg_no']}).",
            "fields": [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
                {"label": "Programme", "value": student["programme"].upper()},
                {"label": "Current Semester", "value": str(student["semester"])},
                {"label": "Next Semester", "value": str(next_sem)},
                {"label": "Academic Year", "value": f"{student['admission_year']}-{student['admission_year'] + 1}"},
                {"label": "Admission Fee", "value": "₹ 8,000"},
                {"label": "Last Date", "value": "15-Jul-2025"},
                {"label": "Late Fee", "value": "₹ 500 after due date"},
            ],
            "actions": [
                {"id": "fill_admission", "label": "Fill Admission Form"},
                {"id": "view_admission_fee", "label": "View Fee Details"},
                {"id": "download_receipt", "label": "Download Receipt"},
            ],
        })


# ===================================================================
# 15. Helpdesk (no auth required)
# ===================================================================

class HelpdeskConnector(ServiceConnector):
    name = "helpdesk"
    display_name = "Helpdesk / Support"
    description = "Contact university helpdesk for support queries"

    async def authenticate(self, reg_no: str, password: str) -> ServiceResult:
        return ServiceResult(success=True, data={"session_token": "helpdesk_public"})

    async def fetch(self, session_token: str | None, params: dict[str, Any]) -> ServiceResult:
        db = _db()
        try:
            student = _get_student(params, db)
            if not student:
                _log.info("HelpdeskConnector: no student found, returning generic help")
                return ServiceResult(success=True, data={
                    "title": "Helpdesk & Support",
                    "message": "Contact the university helpdesk for assistance.",
                    "fields": [
                        {"label": "IT Support", "value": "Phone: 0194-2452710\nEmail: support@cus.ac.in"},
                        {"label": "Academic Queries", "value": "Email: academics@cus.ac.in"},
                        {"label": "Examination Help", "value": "Email: exams@cus.ac.in"},
                        {"label": "Admission Queries", "value": "Email: admissions@cus.ac.in"},
                        {"label": "Student Grievance", "value": "Portal: https://cus.ac.in/grievance"},
                        {"label": "Working Hours", "value": "Monday-Friday, 10:00 AM - 4:00 PM"},
                    ],
                    "actions": [
                        {"id": "call_helpdesk", "label": "Call Helpdesk"},
                        {"id": "email_helpdesk", "label": "Send Email"},
                        {"id": "faq", "label": "View FAQ"},
                    ],
                })
            _log.info("HelpdeskConnector: student_id=%s reg_no=%s", student["id"], student["reg_no"])
            from app.models.demo_models import HelpdeskTicket
            tickets = db.query(HelpdeskTicket).filter(
                HelpdeskTicket.student_id == student["id"],
            ).order_by(HelpdeskTicket.created_date.desc()).all()
            _log.info("HelpdeskConnector: tickets found=%d", len(tickets))

            fields = [
                {"label": "Student Name", "value": student["name"]},
                {"label": "Registration No.", "value": student["reg_no"]},
            ]
            if tickets:
                for t in tickets:
                    status_icon = "✅" if t.status in ("Resolved", "Closed") else "🔄" if t.status == "In Progress" else "🆕"
                    fields.append({
                        "label": f"Ticket: {t.ticket_id} ({status_icon} {t.status})",
                        "value": f"Category: {t.category} | Subject: {t.subject} | Assigned: {t.assigned_officer} ({t.assigned_department}) | Resolution: {t.resolution or 'Pending'}",
                    })
            else:
                fields.append({"label": "No Tickets", "value": "You have not raised any helpdesk tickets yet."})

            fields += [
                {"label": "IT Support", "value": "Phone: 0194-2452710\nEmail: support@cus.ac.in"},
                {"label": "Academic Queries", "value": "Email: academics@cus.ac.in"},
                {"label": "Examination Help", "value": "Email: exams@cus.ac.in"},
                {"label": "Admission Queries", "value": "Email: admissions@cus.ac.in"},
                {"label": "Student Grievance", "value": "Portal: https://cus.ac.in/grievance"},
                {"label": "Working Hours", "value": "Monday-Friday, 10:00 AM - 4:00 PM"},
            ]
            return ServiceResult(success=True, data={
                "title": "Helpdesk & Support",
                "message": f"Your tickets and contact information for {student['name']}.",
                "fields": fields,
                "actions": [
                    {"id": "call_helpdesk", "label": "Call Helpdesk"},
                    {"id": "email_helpdesk", "label": "Send Email"},
                    {"id": "faq", "label": "View FAQ"},
                ],
            })
        finally:
            db.close()

    @property
    def requires_auth(self) -> bool:
        return False
