"""
backend/app/catalogue/parser/

Format readers + metadata detection + structured extraction for uploaded
curriculum documents (PDF, DOCX, DOC, CSV, XLSX, XLS).

Pipeline: read_curriculum_document(path) -> DocumentText
          extract_curriculum(pages, tables, hints) -> payload dict
          detect_scheme / detect_programme / detect_level -> metadata hints
"""

from app.catalogue.parser.detect import detect_level, detect_programme, detect_scheme
from app.catalogue.parser.extract import extract_curriculum
from app.catalogue.parser.readers import (
    CUR_EXTENSIONS,
    CUR_EXTENSION_LABEL,
    DocumentText,
    FormatNotSupportedError,
    read_curriculum_document,
    supported_extensions,
)

__all__ = [
    "CUR_EXTENSIONS",
    "CUR_EXTENSION_LABEL",
    "DocumentText",
    "FormatNotSupportedError",
    "detect_level",
    "detect_programme",
    "detect_scheme",
    "extract_curriculum",
    "read_curriculum_document",
    "supported_extensions",
]
