"""
backend/app/seeders/demo_data.py

Seeds comprehensive synthetic demo data for all student services.
Runs once on startup when DEMO_MODE=True and demo tables are empty.
"""

from __future__ import annotations

import json
import random
import uuid

from sqlalchemy.orm import Session

from app.auth.security import hash_password
from app.models import Student, StudentSession
from app.models.demo_models import (
    BacklogStatus,
    CourseRegistration,
    FeeReceipt,
    HelpdeskTicket,
    MigrationCertificate,
    Revaluation,
    StudentAdmitCard,
    StudentAttendance,
    StudentExamForm,
    StudentResult,
    StudentTranscript,
    XeroxRequest,
)

# ---------------------------------------------------------------------------
# Master student list -- 25 demo students across 9 programmes
# ---------------------------------------------------------------------------

_STUDENTS = [
    {"reg_no": "CUS-2023-0001", "roll_no": "23001", "name": "Aarav Sharma", "father_name": "Rajesh Sharma", "mother_name": "Sunita Sharma", "dob": "15-Apr-2005", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "bca", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "aarav.sharma@cus.ac.in", "phone": "+91-9419000001", "address": "Lal Chowk, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0002", "roll_no": "23002", "name": "Priya Singh", "father_name": "Vikram Singh", "mother_name": "Anita Singh", "dob": "22-Aug-2004", "gender": "Female", "category": "OBC", "college": "Amar Singh College, Srinagar", "programme": "bba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "priya.singh@cus.ac.in", "phone": "+91-9419000002", "address": "Soura, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2022-0003", "roll_no": "22003", "name": "Rohit Kumar", "father_name": "Suresh Kumar", "mother_name": "Geeta Devi", "dob": "10-Jan-2003", "gender": "Male", "category": "SC", "college": "Government Degree College, Bemina", "programme": "bsc", "semester": 6, "admission_year": 2022, "batch": "2022-2025", "email": "rohit.kumar@cus.ac.in", "phone": "+91-9419000003", "address": "Bemina, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0004", "roll_no": "24004", "name": "Anjali Verma", "father_name": "Ravi Verma", "mother_name": "Sita Verma", "dob": "05-Jun-2006", "gender": "Female", "category": "General", "college": "Women's College, Sopore", "programme": "ba", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "anjali.verma@cus.ac.in", "phone": "+91-9419000004", "address": "Sopore, Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0005", "roll_no": "23005", "name": "Vikram Patel", "father_name": "Mohan Patel", "mother_name": "Kavita Patel", "dob": "18-Nov-2004", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "bcom", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "vikram.patel@cus.ac.in", "phone": "+91-9419000005", "address": "Rajbagh, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0006", "roll_no": "24006", "name": "Sneha Gupta", "father_name": "Amit Gupta", "mother_name": "Pooja Gupta", "dob": "12-Mar-2006", "gender": "Female", "category": "OBC", "college": "Government Degree College, Anantnag", "programme": "bca", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "sneha.gupta@cus.ac.in", "phone": "+91-9419000006", "address": "Anantnag, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0007", "roll_no": "23007", "name": "Arjun Nair", "father_name": "Gopal Nair", "mother_name": "Lakshmi Nair", "dob": "28-Sep-2004", "gender": "Male", "category": "General", "college": "Government Degree College, Baramulla", "programme": "bba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "arjun.nair@cus.ac.in", "phone": "+91-9419000007", "address": "Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2022-0008", "roll_no": "22008", "name": "Neha Sharma", "father_name": "Sanjay Sharma", "mother_name": "Meena Sharma", "dob": "15-Jul-2003", "gender": "Female", "category": "SC", "college": "Sri Pratap College of Physical Education", "programme": "bsc", "semester": 6, "admission_year": 2022, "batch": "2022-2025", "email": "neha.sharma@cus.ac.in", "phone": "+91-9419000008", "address": "Kashmir University Campus, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0009", "roll_no": "24009", "name": "Rahul Desai", "father_name": "Deepak Desai", "mother_name": "Asha Desai", "dob": "02-Feb-2006", "gender": "Male", "category": "General", "college": "Government Degree College, Kulgam", "programme": "bcom", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "rahul.desai@cus.ac.in", "phone": "+91-9419000009", "address": "Kulgam, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0010", "roll_no": "23010", "name": "Isha Malik", "father_name": "Faisal Malik", "mother_name": "Zahoora Malik", "dob": "20-Oct-2004", "gender": "Female", "category": "General", "college": "Women's College, Sopore", "programme": "ba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "isha.malik@cus.ac.in", "phone": "+91-9419000010", "address": "Sopore, Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0011", "roll_no": "23011", "name": "Karan Joshi", "father_name": "Hemant Joshi", "mother_name": "Shobha Joshi", "dob": "14-May-2004", "gender": "Male", "category": "OBC", "college": "Amar Singh College, Srinagar", "programme": "bca", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "karan.joshi@cus.ac.in", "phone": "+91-9419000011", "address": "Jawahar Nagar, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0012", "roll_no": "23012", "name": "Divya Chauhan", "father_name": "Rajendra Chauhan", "mother_name": "Kanti Chauhan", "dob": "30-Aug-2004", "gender": "Female", "category": "General", "college": "Government Degree College, Pulwama", "programme": "bba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "divya.chauhan@cus.ac.in", "phone": "+91-9419000012", "address": "Pulwama, J&K", "status": "active"},
    {"reg_no": "CUS-2022-0013", "roll_no": "22013", "name": "Abhishek Yadav", "father_name": "Ram Yadav", "mother_name": "Savitri Yadav", "dob": "25-Dec-2002", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "bsc", "semester": 6, "admission_year": 2022, "batch": "2022-2025", "email": "abhishek.yadav@cus.ac.in", "phone": "+91-9419000013", "address": "Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0014", "roll_no": "24014", "name": "Fatima Mir", "father_name": "Abdul Mir", "mother_name": "Naseema Mir", "dob": "08-Mar-2006", "gender": "Female", "category": "General", "college": "Government Degree College, Anantnag", "programme": "ba", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "fatima.mir@cus.ac.in", "phone": "+91-9419000014", "address": "Anantnag, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0015", "roll_no": "23015", "name": "Rajesh Tiwari", "father_name": "Om Tiwari", "mother_name": "Radhika Tiwari", "dob": "17-Jun-2004", "gender": "Male", "category": "SC", "college": "Government Degree College, Baramulla", "programme": "bcom", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "rajesh.tiwari@cus.ac.in", "phone": "+91-9419000015", "address": "Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0016", "roll_no": "24016", "name": "Pooja Reddy", "father_name": "Srinivas Reddy", "mother_name": "Lalitha Reddy", "dob": "11-Nov-2005", "gender": "Female", "category": "OBC", "college": "Sri Pratap College, Srinagar", "programme": "bca", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "pooja.reddy@cus.ac.in", "phone": "+91-9419000016", "address": "Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0017", "roll_no": "23017", "name": "Manish Thakur", "father_name": "Dinesh Thakur", "mother_name": "Sarita Thakur", "dob": "03-Apr-2004", "gender": "Male", "category": "General", "college": "Amar Singh College, Srinagar", "programme": "bba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "manish.thakur@cus.ac.in", "phone": "+91-9419000017", "address": "Soura, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2022-0018", "roll_no": "22018", "name": "Riya Kapoor", "father_name": "Vijay Kapoor", "mother_name": "Neelam Kapoor", "dob": "19-Sep-2003", "gender": "Female", "category": "General", "college": "Government Degree College, Kulgam", "programme": "bsc", "semester": 6, "admission_year": 2022, "batch": "2022-2025", "email": "riya.kapoor@cus.ac.in", "phone": "+91-9419000018", "address": "Kulgam, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0019", "roll_no": "24019", "name": "Amit Saxena", "father_name": "Rakesh Saxena", "mother_name": "Shweta Saxena", "dob": "07-Jul-2005", "gender": "Male", "category": "General", "college": "Government Degree College, Bemina", "programme": "bcom", "semester": 2, "admission_year": 2024, "batch": "2024-2027", "email": "amit.saxena@cus.ac.in", "phone": "+91-9419000019", "address": "Bemina, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0020", "roll_no": "23020", "name": "Nisha Agarwal", "father_name": "Rohit Agarwal", "mother_name": "Komal Agarwal", "dob": "24-Jan-2005", "gender": "Female", "category": "OBC", "college": "Women's College, Sopore", "programme": "ba", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "nisha.agarwal@cus.ac.in", "phone": "+91-9419000020", "address": "Sopore, Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0021", "roll_no": "24021", "name": "Sahil Bhat", "father_name": "Javaid Bhat", "mother_name": "Rubeena Bhat", "dob": "15-May-2006", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "mca", "semester": 2, "admission_year": 2024, "batch": "2024-2026", "email": "sahil.bhat@cus.ac.in", "phone": "+91-9419000021", "address": "Lal Chowk, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0022", "roll_no": "24022", "name": "Kriti Sinha", "father_name": "Anil Sinha", "mother_name": "Rekha Sinha", "dob": "29-Oct-2005", "gender": "Female", "category": "General", "college": "Amar Singh College, Srinagar", "programme": "mba", "semester": 2, "admission_year": 2024, "batch": "2024-2026", "email": "kriti.sinha@cus.ac.in", "phone": "+91-9419000022", "address": "Jawahar Nagar, Srinagar, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0023", "roll_no": "23023", "name": "Mohit Chauhan", "father_name": "Virender Chauhan", "mother_name": "Suman Chauhan", "dob": "21-Feb-2004", "gender": "Male", "category": "SC", "college": "Government Degree College, Pulwama", "programme": "bsc", "semester": 4, "admission_year": 2023, "batch": "2023-2026", "email": "mohit.chauhan@cus.ac.in", "phone": "+91-9419000023", "address": "Pulwama, J&K", "status": "active"},
    {"reg_no": "CUS-2024-0024", "roll_no": "24024", "name": "Zara Khan", "father_name": "Imran Khan", "mother_name": "Shabnam Khan", "dob": "16-Dec-2005", "gender": "Female", "category": "General", "college": "Women's College, Sopore", "programme": "ma", "semester": 2, "admission_year": 2024, "batch": "2024-2026", "email": "zara.khan@cus.ac.in", "phone": "+91-9419000024", "address": "Sopore, Baramulla, J&K", "status": "active"},
    {"reg_no": "CUS-2023-0025", "roll_no": "23025", "name": "Harsh Vardhan", "father_name": "Pradeep Vardhan", "mother_name": "Anupama Vardhan", "dob": "09-Mar-2004", "gender": "Male", "category": "General", "college": "Sri Pratap College, Srinagar", "programme": "msc", "semester": 4, "admission_year": 2023, "batch": "2023-2025", "email": "harsh.vardhan@cus.ac.in", "phone": "+91-9419000025", "address": "Rajbagh, Srinagar, J&K", "status": "active"},
]

# ---------------------------------------------------------------------------
# Subject data per programme
# ---------------------------------------------------------------------------

_PROGRAMME_SUBJECTS = {
    "bca": {
        1: [("BCA-101", "Mathematics-I"), ("BCA-102", "Programming in C"), ("BCA-103", "Digital Electronics"), ("BCA-104", "English Communication"), ("BCA-105", "Computer Fundamentals")],
        2: [("BCA-201", "Data Structures"), ("BCA-202", "Object Oriented Programming"), ("BCA-203", "Database Management Systems"), ("BCA-204", "Financial Accounting"), ("BCA-205", "Environmental Studies")],
        3: [("BCA-301", "Operating Systems"), ("BCA-302", "Computer Networks"), ("BCA-303", "Web Technologies"), ("BCA-304", "Software Engineering"), ("BCA-305", "Python Programming")],
        4: [("BCA-401", "Java Programming"), ("BCA-402", "Data Mining"), ("BCA-403", "Computer Graphics"), ("BCA-404", "Artificial Intelligence"), ("BCA-405", "Project Work")],
        5: [("BCA-501", "Machine Learning"), ("BCA-502", "Cloud Computing"), ("BCA-503", "Cyber Security"), ("BCA-504", "Mobile App Development"), ("BCA-505", "Major Project")],
        6: [("BCA-601", "Big Data Analytics"), ("BCA-602", "Blockchain Technology"), ("BCA-603", "IoT"), ("BCA-604", "Soft Computing"), ("BCA-605", "Internship")],
    },
    "bba": {
        1: [("BBA-101", "Principles of Management"), ("BBA-102", "Business Mathematics"), ("BBA-103", "Financial Accounting"), ("BBA-104", "Microeconomics"), ("BBA-105", "Business Communication")],
        2: [("BBA-201", "Marketing Management"), ("BBA-202", "Human Resource Management"), ("BBA-203", "Macroeconomics"), ("BBA-204", "Cost Accounting"), ("BBA-205", "Organizational Behavior")],
        3: [("BBA-301", "Financial Management"), ("BBA-302", "Business Statistics"), ("BBA-303", "Business Law"), ("BBA-304", "Production Management"), ("BBA-305", "Entrepreneurship")],
        4: [("BBA-401", "Investment Management"), ("BBA-402", "International Business"), ("BBA-403", "Taxation"), ("BBA-404", "E-Commerce"), ("BBA-405", "Strategic Management")],
        5: [("BBA-501", "Supply Chain Management"), ("BBA-502", "Business Analytics"), ("BBA-503", "Corporate Governance"), ("BBA-504", "Project Management"), ("BBA-505", "Summer Training Report")],
        6: [("BBA-601", "Retail Management"), ("BBA-602", "Advertising Management"), ("BBA-603", "Consumer Behavior"), ("BBA-604", "Sales Management"), ("BBA-605", "Comprehensive Viva")],
    },
    "bsc": {
        1: [("BSC-101", "Physics-I"), ("BSC-102", "Chemistry-I"), ("BSC-103", "Mathematics-I"), ("BSC-104", "English"), ("BSC-105", "Environmental Science")],
        2: [("BSC-201", "Physics-II"), ("BSC-202", "Chemistry-II"), ("BSC-203", "Mathematics-II"), ("BSC-204", "Scientific Computing"), ("BSC-205", "Communicative English")],
        3: [("BSC-301", "Physics-III"), ("BSC-302", "Chemistry-III"), ("BSC-303", "Mathematics-III"), ("BSC-304", "Statistics"), ("BSC-305", "Digital Electronics")],
        4: [("BSC-401", "Quantum Mechanics"), ("BSC-402", "Organic Chemistry"), ("BSC-403", "Numerical Methods"), ("BSC-404", "Biochemistry"), ("BSC-405", "Practicals")],
        5: [("BSC-501", "Nuclear Physics"), ("BSC-502", "Inorganic Chemistry"), ("BSC-503", "Real Analysis"), ("BSC-504", "Bioinformatics"), ("BSC-505", "Lab Work")],
        6: [("BSC-601", "Astrophysics"), ("BSC-602", "Polymer Chemistry"), ("BSC-603", "Complex Analysis"), ("BSC-604", "Genetics"), ("BSC-605", "Research Project")],
    },
    "ba": {
        1: [("BA-101", "English Literature"), ("BA-102", "Political Science"), ("BA-103", "History"), ("BA-104", "Economics"), ("BA-105", "Sociology")],
        2: [("BA-201", "Indian English Literature"), ("BA-202", "Public Administration"), ("BA-203", "Medieval History"), ("BA-204", "Microeconomics"), ("BA-205", "Social Psychology")],
        3: [("BA-301", "Poetry & Drama"), ("BA-302", "International Relations"), ("BA-303", "Modern History"), ("BA-304", "Macroeconomics"), ("BA-305", "Anthropology")],
        4: [("BA-401", "Linguistics"), ("BA-402", "Comparative Politics"), ("BA-403", "World History"), ("BA-404", "Development Economics"), ("BA-405", "Research Methods")],
        5: [("BA-501", "Postcolonial Literature"), ("BA-502", "Political Theory"), ("BA-503", "Indian History"), ("BA-504", "International Trade"), ("BA-505", "Dissertation")],
        6: [("BA-601", "Literary Criticism"), ("BA-602", "Indian Constitution"), ("BA-603", "Contemporary History"), ("BA-604", "Indian Economy"), ("BA-605", "Viva Voce")],
    },
    "bcom": {
        1: [("BCOM-101", "Financial Accounting"), ("BCOM-102", "Business Organization"), ("BCOM-103", "Business Mathematics"), ("BCOM-104", "English"), ("BCOM-105", "Environmental Studies")],
        2: [("BCOM-201", "Corporate Accounting"), ("BCOM-202", "Business Law"), ("BCOM-203", "Company Law"), ("BCOM-204", "Income Tax Law"), ("BCOM-205", "Business Statistics")],
        3: [("BCOM-301", "Cost Accounting"), ("BCOM-302", "Advanced Accounting"), ("BCOM-303", "Auditing"), ("BCOM-304", "GST Law"), ("BCOM-305", "E-Commerce")],
        4: [("BCOM-401", "Management Accounting"), ("BCOM-402", "Financial Management"), ("BCOM-403", "International Business"), ("BCOM-404", "Corporate Tax"), ("BCOM-405", "Computerized Accounting")],
        5: [("BCOM-501", "Investment Management"), ("BCOM-502", "Economic Planning"), ("BCOM-503", "Entrepreneurship"), ("BCOM-504", "Business Ethics"), ("BCOM-505", "Project")],
        6: [("BCOM-601", "Financial Markets"), ("BCOM-602", "Banking Law"), ("BCOM-603", "Advertising"), ("BCOM-604", "Export Management"), ("BCOM-605", "Viva")],
    },
    "mca": {
        1: [("MCA-101", "Advanced Data Structures"), ("MCA-102", "Database Technologies"), ("MCA-103", "Software Engineering"), ("MCA-104", "Computer Networks"), ("MCA-105", "Programming Lab")],
        2: [("MCA-201", "Machine Learning"), ("MCA-202", "Web Technologies"), ("MCA-203", "Cloud Computing"), ("MCA-204", "Cyber Security"), ("MCA-205", "Project Lab")],
        3: [("MCA-301", "Big Data Analytics"), ("MCA-302", "IoT"), ("MCA-303", "Blockchain"), ("MCA-304", "Data Science"), ("MCA-305", "Industry Internship")],
        4: [("MCA-401", "Major Project"), ("MCA-402", "Seminar"), ("MCA-403", "Viva Voce"), ("", ""), ("", "")],
    },
    "mba": {
        1: [("MBA-101", "Management Concepts"), ("MBA-102", "Business Economics"), ("MBA-103", "Financial Accounting"), ("MBA-104", "Marketing Management"), ("MBA-105", "Organizational Behavior")],
        2: [("MBA-201", "Human Resource Management"), ("MBA-202", "Financial Management"), ("MBA-203", "Operations Research"), ("MBA-204", "Business Analytics"), ("MBA-205", "Legal Aspects of Business")],
        3: [("MBA-301", "Strategic Management"), ("MBA-302", "International Business"), ("MBA-303", "Entrepreneurship"), ("MBA-304", "Corporate Finance"), ("MBA-305", "Summer Internship")],
        4: [("MBA-401", "Business Policy"), ("MBA-402", "Digital Marketing"), ("MBA-403", "Supply Chain Management"), ("MBA-404", "Major Project"), ("", "")],
    },
    "ma": {
        1: [("MA-101", "Core Literary Theory"), ("MA-102", "British Poetry"), ("MA-103", "Drama"), ("MA-104", "Linguistics"), ("MA-105", "Creative Writing")],
        2: [("MA-201", "Literary Criticism"), ("MA-202", "American Literature"), ("MA-203", "Indian Writing in English"), ("MA-204", "World Literature"), ("MA-205", "Dissertation")],
        3: [("MA-301", "Postcolonial Studies"), ("MA-302", "Women's Writing"), ("MA-303", "Comparative Literature"), ("MA-304", "Cultural Studies"), ("MA-305", "Research Methodology")],
        4: [("MA-401", "Contemporary Theory"), ("MA-402", "Translation Studies"), ("MA-403", "Media Studies"), ("MA-404", "Major Dissertation"), ("", "")],
    },
    "msc": {
        1: [("MSC-101", "Advanced Mathematics"), ("MSC-102", "Classical Mechanics"), ("MSC-103", "Quantum Mechanics"), ("MSC-104", "Lab-I"), ("MSC-105", "Computational Physics")],
        2: [("MSC-201", "Electrodynamics"), ("MSC-202", "Statistical Mechanics"), ("MSC-203", "Nuclear Physics"), ("MSC-204", "Lab-II"), ("MSC-205", "Mathematical Methods")],
        3: [("MSC-301", "Solid State Physics"), ("MSC-302", "Atomic & Molecular Physics"), ("MSC-303", "Astrophysics"), ("MSC-304", "Lab-III"), ("MSC-305", "Project")],
        4: [("MSC-401", "Particle Physics"), ("MSC-402", "Condensed Matter"), ("MSC-403", "Advanced Lab"), ("MSC-404", "Dissertation"), ("", "")],
    },
}

_GRADES = ["A+", "A", "B+", "B", "C+", "C", "D", "F"]
_GRADE_MARKS = [(90, "A+"), (80, "A"), (72, "B+"), (64, "B"), (56, "C+"), (48, "C"), (40, "D"), (0, "F")]


def _random_marks() -> tuple[int, int, int, str]:
    internal = random.randint(15, 25)
    external = random.randint(30, 60)
    total = internal + external
    for min_mark, grade in _GRADE_MARKS:
        if total >= min_mark:
            return internal, external, total, grade
    return internal, external, total, "F"


def _sgpa(results: list) -> str:
    grade_points = {"A+": 10, "A": 9, "B+": 8, "B": 7, "C+": 6, "C": 5, "D": 4, "F": 0}
    total_points = sum(grade_points.get(g, 0) for _, g in results)
    n = len(results) or 1
    return f"{(total_points / n):.2f}"


def _seed_demo_students(db: Session, count: int = 25) -> None:
    """Seed demo students into the DB (skipped if students already exist)."""
    existing = db.query(Student).count()
    if existing > 0:
        import logging
        logging.getLogger("cus").info(
            "Students table already has %d records -- skipping demo seed", existing
        )
        return

    students_to_seed = _STUDENTS[:count]
    student_objects = []
    for s in students_to_seed:
        # NEP 2020 cohorts start from 2023 admissions; earlier cohorts follow CBCS
        scheme = "nep2020" if s["admission_year"] >= 2023 else "cbcs"
        student = Student(
            id=uuid.uuid4(),
            reg_no=s["reg_no"],
            roll_no=s["roll_no"],
            name=s["name"],
            father_name=s["father_name"],
            mother_name=s["mother_name"],
            dob=s["dob"],
            gender=s["gender"],
            category=s["category"],
            email=s["email"],
            phone=s["phone"],
            college=s["college"],
            programme=s["programme"],
            academic_scheme=scheme,
            current_semester=s["semester"],
            admission_year=s["admission_year"],
            batch=s["batch"],
            address=s["address"],
            status=s["status"],
            hashed_password=hash_password("student123"),
            is_active=True,
        )
        db.add(student)
        db.flush()
        student_objects.append((student, s))

    db.commit()

    # Seed service data for each student
    for student, s in student_objects:
        _seed_results(db, student, s)
        _seed_admit_card(db, student, s)
        _seed_exam_form(db, student, s)
        _seed_fee_receipts(db, student, s)
        _seed_attendance(db, student, s)
        _seed_transcripts(db, student, s)
        _seed_migration(db, student, s)
        _seed_revaluation(db, student, s)
        _seed_xerox(db, student, s)
        _seed_backlog(db, student, s)
        _seed_course_registration(db, student, s)
        _seed_helpdesk(db, student, s)

    db.commit()

    import logging
    logging.getLogger("cus").info(
        "Seeded %d demo students with full service data", len(student_objects)
    )


def _seed_results(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    subjects = _PROGRAMME_SUBJECTS.get(programme, {})
    semester = s["semester"]
    base_year = s["admission_year"]

    for sem in range(1, semester + 1):
        subjs = subjects.get(sem, [])
        sem_results = []
        added = []
        for code, name in subjs:
            if not code:
                continue
            internal, external, total, grade = _random_marks()
            r = StudentResult(
                id=uuid.uuid4(),
                student_id=student.id,
                semester=sem,
                exam_type="Regular",
                subject_name=name,
                subject_code=code,
                internal_marks=internal,
                external_marks=external,
                total_marks=total,
                max_marks=100,
                grade=grade,
                sgpa="0.00",
                cgpa="0.00",
                status="pass" if grade != "F" else "fail",
                academic_year=f"{base_year + (sem-1)//2}-{base_year + (sem-1)//2 + 1}",
            )
            sem_results.append((total, grade))
            added.append(r)
            db.add(r)
        if sem_results:
            sgpa = _sgpa(sem_results)
            for r in added:
                r.sgpa = sgpa


def _seed_admit_card(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    semester = s["semester"]
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(semester, [])
    subject_names = [name for _, name in subjects if name]
    centres = [
        ("SPC01", "Sri Pratap College, Srinagar - Main Campus", "Lal Chowk, Srinagar, J&K"),
        ("ASC02", "Amar Singh College, Srinagar", "Soura, Srinagar, J&K"),
        ("GDC03", "Government Degree College, Bemina", "Bemina, Srinagar, J&K"),
        ("WSC04", "Women's College, Sopore", "Sopore, Baramulla, J&K"),
    ]
    centre = random.choice(centres)
    ac = StudentAdmitCard(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=semester,
        exam_type="Regular",
        exam_session=f"{'Nov/Dec' if semester % 2 == 0 else 'May/Jun'} {s['admission_year'] + (semester // 2)}",
        centre_name=centre[1],
        centre_code=centre[0],
        centre_address=centre[2],
        reporting_time="09:00 AM",
        subjects=json.dumps(subject_names),
        instructions=json.dumps([
            "Bring this admit card to the examination hall",
            "Carry a valid photo ID (Aadhaar/College ID)",
            "Report 30 minutes before the scheduled time",
            "Mobile phones and electronic gadgets are strictly prohibited",
            "Use only blue/black ballpoint pen",
        ]),
        issued_date=f"01-{'Dec' if semester % 2 == 0 else 'May'}-{s['admission_year'] + (semester // 2)}",
        academic_year=f"{s['admission_year']}-{s['admission_year'] + 1}",
    )
    db.add(ac)


def _seed_exam_form(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    semester = s["semester"]
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(semester, [])
    subject_names = [name for _, name in subjects if name]
    ef = StudentExamForm(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=semester,
        exam_type="Regular",
        form_status="Submitted",
        subjects=json.dumps(subject_names),
        fee_status="Paid",
        fee_amount=1500 + semester * 500,
        transaction_id=f"TXN{random.randint(10000000, 99999999)}",
        submission_date=f"15-{'Nov' if semester % 2 == 0 else 'May'}-{s['admission_year'] + (semester // 2)}",
        academic_year=f"{s['admission_year']}-{s['admission_year'] + 1}",
    )
    db.add(ef)


def _seed_fee_receipts(db: Session, student: Student, s: dict) -> None:
    for sem in range(1, s["semester"] + 1):
        total = random.randint(35000, 55000)
        paid = total - (500 if sem == s["semester"] and random.random() < 0.2 else 0)
        heads = {
            "Tuition Fee": total // 2,
            "Examination Fee": 3000,
            "Library Fee": 2000,
            "Laboratory Fee": 2500 if "sc" in s["programme"] or "ca" in s["programme"] else 0,
            "Sports Fee": 1000,
            "Student Welfare": 1500,
            "Development Fee": 5000,
            "Miscellaneous": max(0, total - (total // 2 + 3000 + 2000 + 2500 + 1000 + 1500 + 5000)),
        }
        fr = FeeReceipt(
            id=uuid.uuid4(),
            student_id=student.id,
            receipt_no=f"RCP/{s['admission_year']}/{sem}/{random.randint(1000, 9999)}",
            transaction_id=f"TXN{random.randint(10000000, 99999999)}",
            fee_heads=json.dumps(heads),
            paid_amount=paid,
            total_amount=total,
            pending_amount=total - paid,
            payment_date=f"10-{'Jul' if sem % 2 else 'Jan'}-{s['admission_year'] + (sem // 2)}",
            payment_mode=random.choice(["Online", "DD", "Cash"]),
            semester=sem,
            academic_year=f"{s['admission_year']}-{s['admission_year'] + 1}",
            status="Paid" if paid >= total else "Partial",
        )
        db.add(fr)


def _seed_attendance(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    for sem in range(1, s["semester"] + 1):
        subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(sem, [])
        for code, name in subjects:
            if not code:
                continue
            total = random.randint(25, 40)
            attended = random.randint(int(total * 0.6), total)
            pct = round((attended / total) * 100, 1)
            att = StudentAttendance(
                id=uuid.uuid4(),
                student_id=student.id,
                semester=sem,
                subject_name=name,
                subject_code=code,
                total_classes=total,
                attended_classes=attended,
                percentage=f"{pct:.1f}%",
                academic_year=f"{s['admission_year']}-{s['admission_year'] + 1}",
            )
            db.add(att)


def _seed_transcripts(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    cgpa_sum = 0.0
    for sem in range(1, s["semester"] + 1):
        subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(sem, [])
        valid_subjs = [subj for subj in subjects if subj[0]]
        credits = len(valid_subjs) * 4
        earned = credits - (4 if random.random() < 0.05 else 0)
        sgpa_val = round(random.uniform(6.0, 9.5), 2)
        cgpa_sum += sgpa_val
        cgpa = round(cgpa_sum / sem, 2)
        tr = StudentTranscript(
            id=uuid.uuid4(),
            student_id=student.id,
            semester=sem,
            academic_year=f"{s['admission_year'] + (sem-1)//2}-{s['admission_year'] + (sem-1)//2 + 1}",
            credits_earned=earned,
            total_credits=credits,
            sgpa=str(sgpa_val),
            cgpa=str(cgpa),
            status="Completed",
        )
        db.add(tr)


def _seed_migration(db: Session, student: Student, s: dict) -> None:
    max_sem = {"bca": 6, "bba": 6, "bsc": 6, "ba": 6, "bcom": 6, "mca": 4, "mba": 4, "ma": 4, "msc": 4}
    is_final = s["semester"] >= max_sem.get(s["programme"], 6)
    mc = MigrationCertificate(
        id=uuid.uuid4(),
        student_id=student.id,
        certificate_no=f"MIG/{s['admission_year']}/{s['roll_no']}" if is_final else None,
        issue_status="Issued" if is_final else "Not Applied",
        issue_date=f"01-Jul-{s['admission_year'] + (max_sem.get(s['programme'], 6) // 2)}" if is_final else None,
        application_date=f"15-May-{s['admission_year'] + (max_sem.get(s['programme'], 6) // 2)}" if is_final else None,
        reason="Course Completion" if is_final else None,
    )
    db.add(mc)


def _seed_revaluation(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    # About 40% of students have revaluation applications
    if random.random() > 0.4:
        return
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(s["semester"], [])
    valid = [(c, n) for c, n in subjects if c]
    if not valid:
        return
    subj = random.choice(valid)
    rv = Revaluation(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=s["semester"],
        subject_name=subj[1],
        subject_code=subj[0],
        application_date=f"20-{'Dec' if s['semester'] % 2 == 0 else 'Jun'}-{s['admission_year'] + (s['semester'] // 2)}",
        status=random.choice(["Pending", "Under Review", "Completed"]),
        result=random.choice(["Marks Increased by 5", "No Change", "Under Review", None]),
        fee_status="Paid",
    )
    db.add(rv)


def _seed_xerox(db: Session, student: Student, s: dict) -> None:
    # About 30% of students have xerox requests
    if random.random() > 0.3:
        return
    programme = s["programme"]
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(max(1, s["semester"] - 1), [])
    valid = [n for c, n in subjects if c]
    if not valid:
        return
    subj = random.choice(valid)
    xr = XeroxRequest(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=s["semester"] - 1 if s["semester"] > 1 else 1,
        paper_name=subj,
        application_date=f"10-{'Jan' if s['semester'] % 2 == 0 else 'Jul'}-{s['admission_year'] + (s['semester'] // 2)}",
        fee_status="Paid",
        request_status=random.choice(["Processing", "Ready for Collection", "Dispatched"]),
        estimated_date=f"25-{'Jan' if s['semester'] % 2 == 0 else 'Jul'}-{s['admission_year'] + (s['semester'] // 2)}",
    )
    db.add(xr)


def _seed_backlog(db: Session, student: Student, s: dict) -> None:
    # About 25% of students have backlogs
    if random.random() > 0.25:
        return
    programme = s["programme"]
    backlog_sem = max(1, s["semester"] - random.randint(1, 2))
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(backlog_sem, [])
    valid = [(c, n) for c, n in subjects if c]
    if not valid:
        return
    subj = random.choice(valid)
    bl = BacklogStatus(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=backlog_sem,
        subject_name=subj[1],
        subject_code=subj[0],
        exam_type="Backlog",
        status=random.choice(["Pending", "Cleared"]),
        improvement_applied=random.choice([True, False]),
        cleared_date=f"15-{'Jun' if random.random() > 0.5 else 'Dec'}-{s['admission_year'] + 1}" if random.random() > 0.5 else None,
    )
    db.add(bl)


def _seed_course_registration(db: Session, student: Student, s: dict) -> None:
    programme = s["programme"]
    subjects = _PROGRAMME_SUBJECTS.get(programme, {}).get(s["semester"], [])
    if not subjects:
        return
    all_subjs = [n for c, n in subjects if c]
    mandatory = len(all_subjs) - 1
    electives = all_subjs[mandatory:] if mandatory < len(all_subjs) else []
    cr = CourseRegistration(
        id=uuid.uuid4(),
        student_id=student.id,
        semester=s["semester"],
        academic_year=f"{s['admission_year']}-{s['admission_year'] + 1}",
        elective_subjects=json.dumps(electives),
        registered_subjects=json.dumps(all_subjs),
        registration_date=f"01-{'Jul' if s['semester'] % 2 == 1 else 'Jan'}-{s['admission_year'] + (s['semester'] // 2)}",
        status="Registered",
    )
    db.add(cr)


def _seed_helpdesk(db: Session, student: Student, s: dict) -> None:
    # About 35% of students have helpdesk tickets
    if random.random() > 0.35:
        return
    categories = [
        ("IT Support", "Unable to access student portal", "I am facing issues logging into the student portal. The page shows a 502 error."),
        ("Academic Query", "Grade discrepancy", "My internal marks for one subject seem incorrect. Please verify."),
        ("Examination", "Admit card issue", "My admit card shows incorrect subject details."),
        ("Fee Related", "Payment not reflected", "I paid my semester fee but it is still showing as pending."),
        ("Document Issue", "Migration certificate", "I need my migration certificate urgently for further studies."),
    ]
    cat = random.choice(categories)
    statuses = ["Open", "In Progress", "Resolved", "Closed"]
    assigned = [
        ("Mr. Faisal Ahmad", "IT Department"),
        ("Ms. Sana Mir", "Academic Affairs"),
        ("Mr. Irfan Shah", "Examination Wing"),
        ("Dr. Naseer Lone", "Finance Section"),
        ("Mr. Tariq Bhat", "Administration"),
    ]
    officer = random.choice(assigned)
    ht = HelpdeskTicket(
        id=uuid.uuid4(),
        student_id=student.id,
        ticket_id=f"HTK-{s['reg_no']}-{random.randint(100, 999)}",
        category=cat[0],
        subject=cat[1],
        description=cat[2],
        status=random.choice(statuses),
        assigned_officer=officer[0],
        assigned_department=officer[1],
        resolution=random.choice(["Issue resolved. Portal access restored.", "Under review. Will be updated shortly.", "Fee has been reconciled. Thank you.", None]),
        created_date=f"05-{'Mar' if random.random() > 0.5 else 'Sep'}-{s['admission_year'] + 1}",
        resolved_date=f"12-{'Mar' if random.random() > 0.5 else 'Sep'}-{s['admission_year'] + 1}" if random.random() > 0.5 else None,
    )
    db.add(ht)


def seed_demo_data(db: Session, count: int = 25) -> int:
    """Main entry point — seeds demo data if service tables are empty.
    
    Checks StudentResult table instead of Student table to allow
    pre-existing student accounts to also get demo service data.
    
    Returns the number of students seeded (0 if service data already exists).
    """
    from app.models.demo_models import StudentResult
    existing_service = db.query(StudentResult).count()
    if existing_service > 0:
        return 0
    # Check if students exist first; if not, seed them too
    existing_students = db.query(Student).count()
    if existing_students == 0:
        _seed_demo_students(db, count=count)
        seeded = min(count, len(_STUDENTS))
    else:
        # Students exist but no service data — seed service data for existing students
        _seed_service_data_for_existing(db)
        seeded = existing_students
    return seeded


def _seed_service_data_for_existing(db: Session) -> None:
    """Seed demo service data for existing students (no new student creation)."""
    students = db.query(Student).all()
    for student in students:
        s = _make_student_dict(student)
        _seed_results(db, student, s)
        _seed_admit_card(db, student, s)
        _seed_exam_form(db, student, s)
        _seed_fee_receipts(db, student, s)
        _seed_attendance(db, student, s)
        _seed_transcripts(db, student, s)
        _seed_migration(db, student, s)
        _seed_revaluation(db, student, s)
        _seed_xerox(db, student, s)
        _seed_backlog(db, student, s)
        _seed_course_registration(db, student, s)
        _seed_helpdesk(db, student, s)
    db.commit()
    import logging
    logging.getLogger("cus").info(
        "Seeded demo service data for %d existing students", len(students)
    )


def _make_student_dict(student: Student) -> dict:
    """Convert a Student ORM object to a dict matching _STUDENTS structure."""
    return {
        "reg_no": student.reg_no,
        "roll_no": student.roll_no or "",
        "name": student.name,
        "father_name": student.father_name or "",
        "mother_name": student.mother_name or "",
        "dob": student.dob or "",
        "gender": student.gender or "",
        "category": student.category or "",
        "college": student.college or "",
        "programme": student.programme,
        "semester": student.current_semester,
        "admission_year": student.admission_year,
        "batch": student.batch or "",
        "status": student.status or "active",
    }


def reset_demo_data(db: Session) -> None:
    """Delete all demo data from all service tables + student sessions + students."""
    tables = [
        StudentResult, StudentAdmitCard, StudentExamForm, FeeReceipt,
        StudentAttendance, StudentTranscript, MigrationCertificate,
        Revaluation, XeroxRequest, BacklogStatus, CourseRegistration,
        HelpdeskTicket, StudentSession, Student,
    ]
    for table in tables:
        db.query(table).delete()
    db.commit()
