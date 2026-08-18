"""
backend/app/catalogue/models.py

SQLAlchemy ORM models for the NEP Academic Catalogue.

Tables (all brand-new — no existing schema is modified):
  academic_schemes      — academic schemes (Traditional, NEP 2020, future)
  programme_categories  — degree categories (Undergraduate, Postgraduate, ...)
  programmes            — programme master data (duration, credits, scheme, ...)
  minor_disciplines     — available minor disciplines per programme
  programme_subjects    — semester-wise subjects (major/minor/VAC/SEC/AEC/generic)
  learning_outcomes     — programme learning outcomes
  curriculum_documents  — uploaded curriculum PDFs linked to a Programme + documents row
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base, _UUID, utcnow


class AcademicScheme(Base):
    """An academic scheme a programme can follow (Traditional / NEP 2020 / ...).

    Everything shown in the chatbot's scheme picker comes from this table —
    adding a scheme in the Admin Panel makes it available immediately.
    """

    __tablename__ = "academic_schemes"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(100), unique=True, nullable=False)          # "NEP 2020 Curriculum"
    code = Column(String(40), unique=True, nullable=False)           # "nep2020" | "traditional"
    description = Column(Text, nullable=True)
    sort_order = Column(Integer, default=0)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programmes = relationship("Programme", back_populates="scheme")


class ProgrammeCategory(Base):
    """A degree category used to group programmes (UG / PG / PhD)."""

    __tablename__ = "programme_categories"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(60), unique=True, nullable=False)          # e.g. "Undergraduate"
    level_label = Column(String(20), nullable=False, default="ug")  # ug | pg | phd | integrated
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programmes = relationship(
        "Programme", back_populates="category", cascade="all, delete-orphan"
    )


class Programme(Base):
    """A curated academic programme under the NEP catalogue."""

    __tablename__ = "programmes"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String(150), unique=True, nullable=False)          # "Bachelor of Computer Applications"
    code = Column(String(40), unique=True, nullable=False)           # "BCA"
    category_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programme_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    degree_level = Column(String(40), nullable=True)                 # "Bachelor of ..."
    scheme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("academic_schemes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    academic_scheme = Column(String(20), nullable=True)              # cbcs | nep2020 (legacy denormalised code)
    eligibility = Column(Text, nullable=True)                        # eligibility criteria
    fee_structure = Column(JSON, nullable=True)                      # [{"label": "...", "value": "..."}]
    duration_years = Column(Integer, nullable=True)
    total_credits = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    major_disciplines = Column(JSON, default=list)                   # list[str]
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    category = relationship("ProgrammeCategory", back_populates="programmes")
    scheme = relationship("AcademicScheme", back_populates="programmes")
    subjects = relationship(
        "ProgrammeSubject", back_populates="programme", cascade="all, delete-orphan"
    )
    minor_disciplines = relationship(
        "MinorDiscipline", back_populates="programme", cascade="all, delete-orphan"
    )
    learning_outcomes = relationship(
        "LearningOutcome",
        back_populates="programme",
        cascade="all, delete-orphan",
        order_by="LearningOutcome.position",
    )
    curriculum_documents = relationship(
        "CurriculumDocument", back_populates="programme", cascade="all, delete-orphan"
    )
    curriculum_uploads = relationship(
        "CurriculumUpload", back_populates="programme", cascade="all, delete-orphan"
    )


class MinorDiscipline(Base):
    """A named minor discipline a student can pick within a programme."""

    __tablename__ = "minor_disciplines"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programmes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programme = relationship("Programme", back_populates="minor_disciplines")


class ProgrammeSubject(Base):
    """A semester-wise subject. category in: major | minor | vac | sec | aec | generic.

    programme_id may be NULL for university-wide courses (shared VAC/SEC/AEC
    pools). For minor subjects, minor_discipline_id links to the discipline.
    """

    __tablename__ = "programme_subjects"
    __table_args__ = (
        UniqueConstraint(
            "programme_id",
            "minor_discipline_id",
            "category",
            "semester",
            "subject_name",
            name="uq_programme_subject",
        ),
    )

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programmes.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    minor_discipline_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("minor_disciplines.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category = Column(String(20), nullable=False, default="major")
    semester = Column(Integer, nullable=True)  # NULL for university-wide courses
    subject_code = Column(String(30), nullable=True)
    subject_name = Column(String(200), nullable=False)
    credits = Column(Integer, nullable=True)
    hours = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programme = relationship("Programme", back_populates="subjects")
    minor_discipline = relationship("MinorDiscipline")


class LearningOutcome(Base):
    """A single learning outcome for a programme (ordered)."""

    __tablename__ = "learning_outcomes"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programmes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    outcome_text = Column(Text, nullable=False)
    position = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programme = relationship("Programme", back_populates="learning_outcomes")


class CurriculumDocument(Base):
    """A curriculum PDF uploaded for a programme (also flowed into RAG).

    document_id references the existing documents table so the file can be
    searched through the regular retrieval pipeline.
    """

    __tablename__ = "curriculum_documents"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programmes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    document_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)
    semester = Column(Integer, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    programme = relationship("Programme", back_populates="curriculum_documents")


class CurriculumUpload(Base):
    """A curriculum document upload with version history (extension of the
    academic catalogue — NOT part of Knowledge Sync).

    Lifecycle:
      draft   — parsed, pending admin review (nothing live)
      active  — published; one active upload per programme
      archived— superseded by a newer version

    `payload` holds the structured extraction produced by the document parser
    (programme fields, semester-wise subjects, minors, outcomes, eligibility,
    fees) and is editable in the admin review screen before publishing.
    `document_id` references the RAG document created from the same file so
    the uploaded curriculum becomes the primary academic retrieval source.
    """

    __tablename__ = "curriculum_uploads"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    programme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("programmes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    scheme_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("academic_schemes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    document_id = Column(
        _UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    filename = Column(String(255), nullable=False)           # original filename
    stored_filename = Column(String(255), nullable=True)     # sanitized stored name
    file_type = Column(String(10), nullable=True)            # pdf | docx | doc | xlsx | xls | csv
    file_size = Column(Integer, nullable=True)
    sha256 = Column(String(64), nullable=True, index=True)   # duplicate detection
    version = Column(Integer, default=1, nullable=False)     # auto version history
    revision = Column(String(40), nullable=True)             # e.g. "2025" / "Rev 4"
    academic_session = Column(String(40), nullable=True)     # e.g. "2024-2026"
    # ---- detected programme / scheme (denormalised, editable in review) ----
    programme_name = Column(String(150), nullable=True)
    programme_code = Column(String(40), nullable=True, index=True)
    scheme_name = Column(String(100), nullable=True)
    scheme_code = Column(String(40), nullable=True)
    level = Column(String(20), nullable=True)                # ug | pg | phd | integrated
    # ---- lifecycle ----
    status = Column(String(20), default="draft", nullable=False, index=True)
    parse_status = Column(String(20), default="ok", nullable=False)  # ok | partial | failed
    warnings = Column(JSON, default=list)
    payload = Column(JSON, default=dict)                     # structured extraction
    uploaded_by = Column(
        _UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    programme = relationship("Programme", back_populates="curriculum_uploads")
    scheme = relationship("AcademicScheme")
    document = relationship("Document")