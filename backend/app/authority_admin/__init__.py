"""
backend/app/authority_admin/__init__.py

Authority Admin account management (SUPER ADMIN only) + self-service scope
endpoints (AUTHORITY ADMIN only).

Reuses the existing `users` table, bcrypt hashing and JWT infrastructure — no
second authentication framework is introduced.
"""

from app.authority_admin.routes import router  # noqa: F401
from app.authority_admin.routes import self_router  # noqa: F401