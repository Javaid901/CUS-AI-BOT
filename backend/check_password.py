from app.database import SessionLocal, create_all
from app.models import Student
from app.auth.security import verify_password

create_all()
db = SessionLocal()
s = db.query(Student).filter(Student.reg_no == 'CUS-2023-0001').first()
if s:
    print(f'Hash: {s.hashed_password}')
    print(f'Verify student123: {verify_password("student123", s.hashed_password)}')
    print(f'Verify student: {verify_password("student", s.hashed_password)}')
    print(f'Verify 123456: {verify_password("123456", s.hashed_password)}')
    print(f'Verify password: {verify_password("password", s.hashed_password)}')
db.close()