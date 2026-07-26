"""Create a PDF from the combined knowledge base text using fpdf2."""
import os
from fpdf import FPDF

class CUSPDF(FPDF):
    def header(self):
        self.set_font('Helvetica', 'B', 10)
        self.set_text_color(15, 122, 77)
        self.cell(0, 8, 'Cluster University of Srinagar - Knowledge Base', align='C', new_x='LMARGIN', new_y='NEXT')
        self.line(10, self.get_y(), 200, self.get_y())
        self.ln(4)

    def footer(self):
        self.set_y(-15)
        self.set_font('Helvetica', 'I', 8)
        self.set_text_color(128, 128, 128)
        self.cell(0, 10, f'Page {self.page_no()}/{{nb}}', align='C')

scrape_dir = os.path.dirname(os.path.abspath(__file__))
txt_path = os.path.join(scrape_dir, 'CUS_Complete_Knowledge_Base.txt')

with open(txt_path, 'r', encoding='utf-8') as f:
    text = f.read()

pdf = CUSPDF()
pdf.alias_nb_pages()
pdf.set_auto_page_break(auto=True, margin=20)
pdf.add_page()

# Title page
pdf.set_font('Helvetica', 'B', 24)
pdf.set_text_color(15, 122, 77)
pdf.ln(40)
pdf.cell(0, 15, 'Cluster University of Srinagar', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('Helvetica', '', 16)
pdf.set_text_color(60, 60, 60)
pdf.cell(0, 12, 'Complete Knowledge Base', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.set_font('Helvetica', 'I', 11)
pdf.set_text_color(100, 100, 100)
pdf.cell(0, 10, 'Extracted from official website (cusrinagar.edu.in) and affiliated sources', align='C', new_x='LMARGIN', new_y='NEXT')
pdf.cell(0, 10, 'July 2026', align='C', new_x='LMARGIN', new_y='NEXT')

pdf.ln(20)
pdf.set_font('Helvetica', '', 10)
pdf.set_text_color(80, 80, 80)
pdf.multi_cell(0, 6, 'Sources:\n- Official Website: www.cusrinagar.edu.in\n- 7 Official PDF Documents\n- College Admission Portal\n- Education Duniya\n- University Notifications')

pdf.add_page()

# Content
pdf.set_font('Helvetica', '', 8)
pdf.set_text_color(30, 30, 30)

# Split into lines and write
lines = text.split('\n')
for line in lines:
    # Handle special chars
    safe = line.encode('latin-1', errors='replace').decode('latin-1')
    stripped = safe.strip()
    if not stripped:
        pdf.ln(2)
        continue
    # Truncate to avoid overflow
    display = stripped[:200]
    # Check for section headers
    if line.startswith('=' * 10):
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(15, 122, 77)
        try:
            pdf.cell(0, 4, display, new_x='LMARGIN', new_y='NEXT')
        except:
            pass
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(30, 30, 30)
    elif line.startswith('---'):
        pdf.set_font('Helvetica', 'B', 8)
        pdf.set_text_color(0, 80, 150)
        try:
            pdf.cell(0, 4, display, new_x='LMARGIN', new_y='NEXT')
        except:
            pass
        pdf.set_font('Helvetica', '', 8)
        pdf.set_text_color(30, 30, 30)
    else:
        try:
            pdf.multi_cell(0, 4, display)
        except:
            try:
                pdf.cell(0, 4, display, new_x='LMARGIN', new_y='NEXT')
            except:
                pass

pdf_path = os.path.join(scrape_dir, 'CUS_Complete_Knowledge_Base.pdf')
pdf.output(pdf_path)
print(f"PDF created: {pdf_path}")
print(f"Pages: {pdf.page_no()}")
print(f"Size: {os.path.getsize(pdf_path)} bytes")
