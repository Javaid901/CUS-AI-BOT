from app.database import SessionLocal
from sqlalchemy import text, func

db = SessionLocal()

print("=== TABLES ===")
# List all tables
tables = ["students", "student_results", "student_admit_cards", "student_exam_forms", 
          "student_revaluations", "xerox_requests", "student_transcripts", "fee_receipts"]
for t in tables:
    try:
        rows = db.execute(text(f"SELECT * FROM {t} LIMIT 3")).fetchall()
        print(f"\n--- {t} (sample 3) ---")
        for r in rows:
            print(f"  {r}")
    except Exception as e:
        print(f"\n--- {t} --- ERROR: {e}")

print("\n\n=== DISTINCT SEMESTERS ===")
try:
    sems = db.execute(text("SELECT DISTINCT semester FROM student_results ORDER BY semester")).scalars().all()
    print(f"student_results semesters: {list(sems)}")
except Exception as e:
    print(f"Error: {e}")

try:
    sems = db.execute(text("SELECT DISTINCT semester FROM student_exam_forms")).scalars().all()
    print(f"student_exam_forms semesters: {list(sems)}")
except Exception as e:
    print(f"Error: {e}")

try:
    sems = db.execute(text("SELECT DISTINCT semester FROM xerox_requests")).scalars().all()
    print(f"xerox_requests semesters: {list(sems)}")
except Exception as e:
    print(f"Error: {e}")

try:
    sems = db.execute(text("SELECT DISTINCT semester FROM student_revaluations")).scalars().all()
    print(f"student_revaluations semesters: {list(sems)}")
except Exception as e:
    print(f"Error: {e}")

print("\n\n=== STUDENTS SAMPLE ===")
rows = db.execute(text("SELECT reg_no, name, programme, current_semester, academic_scheme, batch, college FROM students LIMIT 5")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== fee_receipts sample ===")
rows = db.execute(text("SELECT * FROM fee_receipts LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== student_transcripts sample ===")
rows = db.execute(text("SELECT * FROM student_transcripts LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== student_admit_cards sample ===")
rows = db.execute(text("SELECT * FROM student_admit_cards LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== student_exam_forms sample ===")
rows = db.execute(text("SELECT * FROM student_exam_forms LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== student_revaluations sample ===")
rows = db.execute(text("SELECT * FROM student_revaluations LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

print("\n\n=== xerox_requests sample ===")
rows = db.execute(text("SELECT * FROM xerox_requests LIMIT 3")).fetchall()
for r in rows:
    print(f"  {r}")

db.close()