"""
Test isolation: run the suite against a throwaway SQLite database so that
test-created records (authorities, grievances, users, ...) never leak into
the live development database (cus_ai.db).
"""
import os
import tempfile

_TEST_DB_DIR = tempfile.mkdtemp(prefix="cus_test_db_")
os.environ["DATABASE_URL"] = f"sqlite:///{os.path.join(_TEST_DB_DIR, 'test.db')}"