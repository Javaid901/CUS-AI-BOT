"""
backend/app/models/db_models.py

SQLAlchemy ORM models for the CUS AI Assistant.

Tables:
  users            - admin / superadmin accounts (and the chat widget auto-registers
                     lightweight "student" users to obtain a JWT for chat).
  documents        - an uploaded source file and its processing status.
  document_chunks  - individual text chunks with page numbers (stored in Chroma too).
  conversations    - a chat session.
  messages         - individual user/assistant messages within a conversation.
  audit_logs       - admin actions (login, upload, delete, reindex, chat requests, errors).
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base, _UUID, utcnow


class User(Base):
    __tablename__ = "users"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String(64), unique=True, index=True, nullable=False)
    email = Column(String(255), unique=True, index=True, nullable=True)
    hashed_password = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="student")  # student | admin | authority_admin | superadmin
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_login = Column(DateTime(timezone=True), nullable=True)
    # ----- Authority scope (Authority Admin accounts) -----
    # An Authority Admin is bound to exactly one authority. Super Admin derives
    # the effective scope from this column in the DB — never from the request.
    authority_id = Column(
        String(36),
        ForeignKey("authorities.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # ----- Profile -----
    full_name = Column(String(120), nullable=True)
    designation = Column(String(120), nullable=True)
    phone = Column(String(30), nullable=True)
    avatar_path = Column(String(255), nullable=True)  # relative path under /api/uploads
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    documents = relationship("Document", back_populates="owner", cascade="all, delete-orphan")
    refresh_tokens = relationship("RefreshToken", back_populates="user", cascade="all, delete-orphan")
    authority = relationship("Authority", foreign_keys=[authority_id])


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)

    user = relationship("User", back_populates="refresh_tokens")


class Document(Base):
    __tablename__ = "documents"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id = Column(_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(400), nullable=False)            # human-friendly title
    filename = Column(String(400), nullable=False)          # sanitized stored filename
    original_filename = Column(String(400), nullable=True)  # as uploaded
    file_type = Column(String(20), nullable=True)           # pdf | docx | txt | md
    file_size = Column(Integer, nullable=True)              # bytes
    sha256 = Column(String(64), nullable=True, index=True)  # SHA256 hash for dedup
    status = Column(String(20), default="processing", nullable=False, index=True)
    # status: queued | processing | indexing | ready | failed
    chunk_count = Column(Integer, default=0, nullable=False)
    error = Column(Text, nullable=True)
    language = Column(String(10), nullable=True)
    # ----- Content metadata (enables scheme/semester-aware RAG filtering) -----
    academic_scheme = Column(String(20), nullable=True)   # cbcs | nep | nep2020
    programme = Column(String(50), nullable=True)         # e.g. "bca"
    department = Column(String(200), nullable=True)
    batch = Column(String(20), nullable=True)             # e.g. "2023-2026"
    semester = Column(String(10), nullable=True)          # e.g. "4"
    document_type = Column(String(50), nullable=True)     # syllabus | prospectus | fee_sheet | regulation | notice | exam_scheme
    category = Column(String(50), nullable=True)          # e.g. "nep2020"
    # ----- College-scoped knowledge source columns -----
    college_id = Column(String(64), nullable=True, index=True)    # college slug, e.g. "amar-singh-college"
    college_name = Column(String(255), nullable=True)            # display name of the owning college
    scope = Column(String(20), nullable=False, default="university", index=True)  # university | college
    source_kind = Column(String(20), nullable=True)             # upload | manual | url | backfill
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    owner = relationship("User", back_populates="documents")
    chunks = relationship("DocumentChunk", back_populates="document", cascade="all, delete-orphan")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (UniqueConstraint("document_id", "chunk_index", name="uq_doc_chunk"),)

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(_UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    page_number = Column(Integer, nullable=True)
    content = Column(Text, nullable=False)
    char_start = Column(Integer, nullable=True)
    char_end = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)

    document = relationship("Document", back_populates="chunks")


class Conversation(Base):
    __tablename__ = "conversations"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    title = Column(String(300), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    conversation_id = Column(_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(20), nullable=False)  # user | assistant | system
    content = Column(Text, nullable=False)
    citations = Column(Text, nullable=True)    # JSON-encoded list of citation dicts
    model = Column(String(60), nullable=True)
    latency_ms = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    conversation = relationship("Conversation", back_populates="messages")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    actor_id = Column(_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_role = Column(String(20), nullable=True)
    action = Column(String(40), nullable=False, index=True)  # login, upload, delete, reindex, chat, error
    target = Column(String(400), nullable=True)
    detail = Column(Text, nullable=True)
    ip = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)


class Student(Base):
    """A real university student with credentials for student services."""

    __tablename__ = "students"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reg_no = Column(String(50), unique=True, index=True, nullable=False)
    roll_no = Column(String(50), nullable=True)
    name = Column(String(200), nullable=False)
    father_name = Column(String(200), nullable=True)
    mother_name = Column(String(200), nullable=True)
    dob = Column(String(20), nullable=True)
    gender = Column(String(10), nullable=True)
    category = Column(String(20), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(20), nullable=True)
    college = Column(String(200), nullable=True)
    programme = Column(String(50), nullable=False)
    academic_scheme = Column(String(20), nullable=True)  # cbcs | nep | nep2020
    current_semester = Column(Integer, nullable=False, default=1)
    admission_year = Column(Integer, nullable=False)
    batch = Column(String(20), nullable=True)
    address = Column(Text, nullable=True)
    status = Column(String(20), default="active", nullable=False)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class StudentSession(Base):
    """An active student login session."""

    __tablename__ = "student_sessions"

    id = Column(_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    student_id = Column(_UUID(as_uuid=True), ForeignKey("students.id", ondelete="CASCADE"), nullable=False, index=True)
    token = Column(String(255), unique=True, index=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    revoked = Column(Boolean, default=False, nullable=False)

    student = relationship("Student")


__all__ = [
    "_UUID",
    "AuditLog",
    "Base",
    "Conversation",
    "Document",
    "DocumentChunk",
    "Message",
    "RefreshToken",
    "Student",
    "StudentSession",
    "User",
]
