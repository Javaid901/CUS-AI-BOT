from app.models import Student
for attr in dir(Student):
    if not attr.startswith('_'):
        print(attr)