"""Extract PDF text + combine all scraped data into one document."""
import os, json, re

try:
    import fitz  # PyMuPDF
    print("PyMuPDF loaded OK")
except ImportError:
    print("PyMuPDF not available")
    fitz = None

scrape_dir = os.path.dirname(os.path.abspath(__file__))

# ---- 1. Load scraped web text ----
with open(os.path.join(scrape_dir, 'all_scraped_text.json'), 'r', encoding='utf-8') as f:
    web_texts = json.load(f)

# ---- 2. Extract PDF text ----
pdf_dir = scrape_dir
pdf_texts = {}
pdf_files = {
    'ug_admission_2026': 'ug_admission_2026.pdf',
    'pg_extension': 'pg_extension.pdf',
    'pg_syllabus': 'pg_syllabus.pdf',
    'dyd_programmes': 'dyd_programmes.pdf',
    'pg_programmes': 'pg_programmes.pdf',
    'commencement': 'commencement.pdf',
    'lateral_syllabus': 'lateral_syllabus.pdf',
}

if fitz:
    for name, fname in pdf_files.items():
        fpath = os.path.join(pdf_dir, fname)
        if not os.path.exists(fpath):
            pdf_texts[name] = f"[PDF not found: {fname}]"
            continue
        try:
            doc = fitz.open(fpath)
            text_parts = []
            for page_num in range(len(doc)):
                page = doc.load_page(page_num)
                text = page.get_text()
                if text.strip():
                    text_parts.append(f"--- Page {page_num+1} ---\n{text}")
            doc.close()
            full = '\n\n'.join(text_parts)
            # Clean up
            full = re.sub(r'\n{3,}', '\n\n', full)
            pdf_texts[name] = full.strip()
            print(f"Extracted {name}: {len(full)} chars")
        except Exception as e:
            pdf_texts[name] = f"[PDF EXTRACTION ERROR: {e}]"
            print(f"FAIL {name}: {e}")
else:
    print("Skipping PDF extraction (no PyMuPDF)")
    pdf_texts = {name: "[PDF text extraction unavailable]" for name in pdf_files}

# ---- 3. Combine everything ----
sections = []

sections.append(("=" * 70))
sections.append("CLUSTER UNIVERSITY OF SRINAGAR - COMPLETE KNOWLEDGE BASE")
sections.append("Source: Official website (cusrinagar.edu.in) and affiliated sources")
sections.append(f"Scraped: July 2026")
sections.append(("=" * 70))

sections.append("")
sections.append("")

# -- Section: University Overview --
sections.append(("=" * 70))
sections.append("SECTION 1: UNIVERSITY OVERVIEW")
sections.append(("=" * 70))

home_text = web_texts.get('home', '')
# Extract the about section
about_match = re.search(r'About Us\s*(.+?)(?=Upcoming Events|Downloads|$)', home_text, re.DOTALL)
if about_match:
    sections.append(about_match.group(1).strip())

# Navigation structure
sections.append("")
sections.append("--- Website Navigation Structure ---")
nav_items = [
    "HOME",
    "ABOUT US: Vision & Mission, Chancellor, Pro Chancellor, Vice Chancellor,",
    "  Controller of Examination Office, Registrar Office, Administration,",
    "  Finance Officer, University Statutes & Act, Right To Information Act,",
    "  Internal Complaints Committee, UGC e-Samadhan Grievance Cell,",
    "  National Service Scheme (NSS), Equity Cell, Placement,",
    "  Statuary Bodies (University Council, University Syndicate, Academic Council)",
    "ADMISSIONS: NEP, Integrated/Professional, P.G. Admissions-2026",
    "ACADEMICS: Dean of Academic Affairs, Deans of Schools, PG Departments,",
    "  Constituent Colleges, Programs Offered, Syllabus, BOS,",
    "  List of Skill Courses, E-Resources",
    "EXAMINATIONS: Controller of Examination, Examination Schedule,",
    "  Paper Pattern (NEP-2020, UG Paper, PG Paper, Engineering),",
    "  Division Improvement, Model Papers, Fee Structure",
    "RESULTS: NEP, U.G, Integrated/Professional, P.G., B.Ed.",
    "RESEARCH: Ph.D Programs, List of Scholars, Initiatives, Fee Structure,",
    "  Programs offered",
    "GALLERY",
    "DIRECTORATES",
    "DOWNLOADS",
    "HELPDESK",
]
sections.extend(nav_items)

sections.append("")
sections.append("--- Quick Links on Homepage ---")
quick_links = [
    "Jobs Portal",
    "NEP-2020 Draft Regulations",
    "Bulletin of Information",
    "Approved Books and Courses",
    "Submit Anti-Ragging Affidavit",
    "Alumni Registration",
    "ABC Registration Guidelines",
    "Academic Bank of Credits Portal",
    "Statutes and Act",
    "Student Academic History",
]
sections.extend(quick_links)

sections.append("")
sections.append("--- Notice Board Categories ---")
sections.append("General | Examination | Admission | Jobs")

sections.append("")
sections.append("--- Vice Chancellor ---")
vc_match = re.search(r'Vice Chancellor\s*(.+?)(?=Read more|$)', home_text, re.DOTALL)
if vc_match:
    sections.append(vc_match.group(1).strip())
sections.append("Prof. (Dr.) Mohammad Mobin is the Vice Chancellor.")

sections.append("")
sections.append("--- Contact Information ---")
contact_match = re.search(r'Contact us\s*(.+?)(?=Copyright|$)', home_text, re.DOTALL)
if contact_match:
    sections.append(contact_match.group(1).strip())
sections.append("Cluster University Of Srinagar")
sections.append("Gogji-Bagh, Srinagar")
sections.append("Jammu & Kashmir, India - 190008")
sections.append("National Anti-Ragging Helpline: 1800-180-5522")

sections.append("")
sections.append("--- Important Links ---")
sections.append("1. Ministry of Education: https://www.education.gov.in/")
sections.append("2. UGC Portal: https://www.ugc.gov.in/")
sections.append("3. National Scholarship Portal: https://scholarships.gov.in/")
sections.append("4. National Digital Library: https://ndl.iitkgp.ac.in/")
sections.append("5. NAD DigiLocker: https://nad.digilocker.gov.in/")

# -- Section: Admissions --
sections.append("")
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 2: ADMISSIONS")
sections.append(("=" * 70))

sections.append("")
sections.append("--- 2.1 ADMISSION PORTAL & REGISTRATION ---")
adm_text = web_texts.get('admissions_registration', '')
sections.append(adm_text)

sections.append("")
sections.append("--- 2.2 NEP ADMISSIONS ---")
nep_text = web_texts.get('nep_admissions', '')
sections.append(nep_text)

sections.append("")
sections.append("--- 2.3 UG ADMISSION 2026-27 NOTICE ---")
ug_text = web_texts.get('ug_admission_notice', '')
sections.append(ug_text[:15000])  # limit size

sections.append("")
sections.append("--- 2.4 ADMISSION SUMMARY FROM EDUCATIONDUNIA ---")
edu_text = web_texts.get('educationdunia', '')
sections.append(edu_text[:15000])

# Add educationdunia rest
if len(edu_text) > 15000:
    sections.append("")
    sections.append("--- 2.4 (continued) ---")
    sections.append(edu_text[15000:30000])

# -- Section: Notices --
sections.append("")
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 3: NOTICES AND NOTIFICATIONS")
sections.append(("=" * 70))

notices_text = web_texts.get('notices', '')
sections.append(notices_text[:20000])

# -- Section: PDF Content --
sections.append("")
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 4: OFFICIAL PDF DOCUMENTS")
sections.append(("=" * 70))

section_map = {
    'ug_admission_2026': '4.1 UG Admission 2026-27 Official Notice (PDF)',
    'pg_programmes': '4.2 PG Programmes Admission Notification (PDF)',
    'pg_extension': '4.3 PG Date Extension Notice (PDF)',
    'pg_syllabus': '4.4 PG Entrance Syllabus 2026 (PDF)',
    'dyd_programmes': '4.5 Design Your Degree (DYD) Programmes (PDF)',
    'commencement': '4.6 Commencement of Classwork - PG 4th Semester (PDF)',
    'lateral_syllabus': '4.7 B.Tech Lateral Entry Syllabus (PDF)',
}

for key, title in section_map.items():
    text = pdf_texts.get(key, '[No data]')
    if text and not text.startswith('[PDF'):
        text = text[:10000]  # limit per PDF to keep combined doc manageable
    sections.append("")
    sections.append(f"--- {title} ---")
    sections.append("")
    sections.append(text)

# -- Section: Programs/Courses --
sections.append("")
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 5: PROGRAMS AND COURSES")
sections.append(("=" * 70))

sections.append("""
CLUSTER UNIVERSITY OF SRINAGAR - PROGRAMS OFFERED

UNDERGRADUATE PROGRAMS (UG):
- BA (Bachelor of Arts) - 3 Years
- B.Sc (Bachelor of Science) - 3 Years
  - B.Sc Medical
  - B.Sc Non-Medical
- B.Com (Bachelor of Commerce) - 3 Years
- BBA (Bachelor of Business Administration) - 3 Years
- BCA (Bachelor of Computer Applications) - 3 Years
- B.Tech (Bachelor of Technology) - 4 Years
  - Computer Science and Engineering
  - Civil Engineering
  - Mechanical Engineering
  - Biomedical Engineering
  - Information Technology
- B.Ed (Bachelor of Education) - 2 Years
- B.Voc (Bachelor of Vocation)
- BHM (Bachelor of Hotel Management)
- BA + MA (Integrated) - 5 Years
- B.Sc + M.Sc (Integrated) - 5 Years
- BBA + MBA (Integrated) - 4 Years
- BCA + MCA (Integrated) - 5 Years
- B.Ed-M.Ed (Integrated) - 3 Years
- B.Tech (Lateral Entry) - 3 Years

POSTGRADUATE PROGRAMS (PG):
- MA (Master of Arts) - 2 Years
- M.Sc (Master of Science) - 2 Years
- M.Com (Master of Commerce) - 2 Years
- MBA (Master of Business Administration) - 2 Years
- MCA (Master of Computer Applications) - 2 Years
- M.Ed (Master of Education) - 2 Years
- PG Diploma in ECCE - 1 Year
- PG Diploma in various subjects - 1 Year

RESEARCH PROGRAMS:
- Ph.D in various disciplines
- M.Phil (where applicable)

PROFESSIONAL / INTEGRATED PROGRAMS:
- Integrated/Honours/Professional Programmes (5-Year)
- Design Your Degree (DYD) Programmes

ADMISSION MODE:
- UG: CUET UG (Common University Entrance Test)
- PG: CUET PG
- B.Tech: JEE Mains
- Ph.D: University-Based Exam or UGC/CSIR NET/JRF
- Integrated: University Entrance Test (CUSET)

FEES (per year approx):
- BA: ~Rs 3,500
- B.Com: ~Rs 3,500
- B.Sc: ~Rs 4,500
- BBA: ~Rs 10,500
- BCA: ~Rs 10,500
- B.Tech CSE: ~Rs 19,900 (total)
- B.Ed: ~Rs 10,000
- MA: ~Rs 5,500
- M.Sc: ~Rs 6,500
- MBA: ~Rs 15,000
- MCA: ~Rs 15,000
- M.Ed: ~Rs 10,000
- Integrated Programmes: ~Rs 29,375 (total)
- B.Tech Lateral Entry: ~Rs 15,100
- Ph.D: varies by program

CONSTITUENT COLLEGES:
1. S.P. College - Arts, Science, Commerce
2. GDC Bemina - Science, Humanities
3. GDC Anantnag - Science, Commerce, Education
4. GDC Pulwama - Commerce, Computer Applications
5. GDC Kulgam - Education, Science, Commerce

SCHOLARSHIPS:
Students can apply through:
- National Scholarship Portal (NSP)
- Post Matric Scholarship for J&K Students
- Merit-cum-Means Scholarships
- UGC Fellowships for Research Scholars
""")

# -- Section: Examination Info --
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 6: EXAMINATIONS")
sections.append(("=" * 70))

sections.append("""
EXAMINATION PATTERN:

UG (NEP-2020):
- Multiple Entry and Exit Options
- Semester System
- 2nd & 4th Semester exams
- 6th Semester FYUP exams
- 8th Semester (NEP) exams

PG:
- Semester System
- 2-Year Programme (4 semesters)
- 1-Year Programme (2 semesters)

Paper Pattern by Category:
- NEP-2020 Pattern
- UG Paper Pattern
- PG Paper Pattern
- Engineering Paper Pattern

KEY EXAM DATES (2026):
- CUET UG Application: Jan 3, 2025 - Jan 31, 2026
- CUET UG Exam: May 11-31, 2026
- CUET UG Result: June 2026
- CUET PG Application: Dec 14, 2025 - Jan 14, 2026
- CUET PG Exam: March 2026
- CUET PG Result: April-May 2026
- CUSET (University Entrance): As per notification

Recent Examination Notifications (2026):
- UG 2nd & 4th Semester NEP Exam Form Last Date Extended (Jul 20)
- UG 6th Semester FYUP Result Declared (Jul 17)
- FYUGP 7th and 8th Semester Admission Notification Released (Jul 17)
- B.Tech 2nd Semester Date Sheet Released (Jul 15)
- B.Ed 1st Semester Date Sheet Released (Jul 6)
- UG 8th Semester (NEP) Exam Form Notice (Jul 6)
- Various backlog exam schedules

RESULTS:
- NEP Results
- UG Results
- Integrated/Professional Results
- PG Results
- B.Ed Results
- B.Tech Results
""")

# -- Section: Downloads --
sections.append("")
sections.append(("=" * 70))
sections.append("SECTION 7: DOWNLOADS AND RESOURCES")
sections.append(("=" * 70))

sections.append("""
Available Downloads:
1. About Us (PDF)
2. Migration Certificate (PDF)
3. University Directory
4. Hostel Information
5. Bulletin of Information
6. Statutes and Act
7. NEP-2020 Draft Regulations
8. Approved Books and Courses
9. Anti-Ragging Affidavit Form
10. ABC Registration Guidelines
11. Model Papers (various subjects)
12. Previous Year Entrance Papers (2018, 2019)
13. PG Entrance Syllabus 2026
14. B.Tech Lateral Entry Syllabus
""")

# ---- Save combined text ----
combined = '\n'.join(sections)

# clean up
combined = re.sub(r'\n{4,}', '\n\n\n', combined)

outpath = os.path.join(scrape_dir, 'CUS_Complete_Knowledge_Base.txt')
with open(outpath, 'w', encoding='utf-8') as f:
    f.write(combined)
print(f"\nSaved combined text to {outpath}")
print(f"Total size: {len(combined)} chars ({len(combined.split())} words)")

# Also create a JSON summary
summary = {
    'total_chars': len(combined),
    'total_words': len(combined.split()),
    'sections': [
        '1. University Overview',
        '2. Admissions (UG, PG, NEP, DYD)',
        '3. Notices and Notifications',
        '4. Official PDF Documents (7 PDFs)',
        '5. Programs and Courses (with fees)',
        '6. Examinations and Results',
        '7. Downloads and Resources',
    ],
    'pdfs_downloaded': list(pdf_files.keys()),
    'web_pages_scraped': list(web_texts.keys()),
}
with open(os.path.join(scrape_dir, 'summary.json'), 'w', encoding='utf-8') as f:
    json.dump(summary, f, indent=2)
print("Summary saved to summary.json")
