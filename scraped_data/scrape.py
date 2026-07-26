import urllib.request, os, json, html, re

def fetch_text(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read().decode('utf-8', errors='replace')
        data = re.sub(r'<[^>]+>', '\n', data)
        data = html.unescape(data)
        data = re.sub(r'\n\s*\n\s*\n+', '\n\n', data)
        data = re.sub(r' +\n', '\n', data)
        data = '\n'.join(line.strip() for line in data.split('\n'))
        data = re.sub(r'\n{3,}', '\n\n', data)
        return data.strip()
    except Exception as e:
        return f'[FETCH ERROR: {e}]'

outdir = os.path.dirname(os.path.abspath(__file__))

pages = {
    'home': 'https://www.cusrinagar.edu.in/',
    'admissions_registration': 'https://www.cusrinagar.edu.in/registration/instructions',
    'nep_admissions': 'https://www.cusrinagar.edu.in/NEP/Index',
}

texts = {}
for name, url in pages.items():
    t = fetch_text(url)
    texts[name] = t
    print(f'Fetched {name}: {len(t)} chars')

# collegedunia for course/fee data
cu_url = 'https://collegedunia.com/university/60446-cluster-university-of-srinagar-cus-srinagar'
try:
    req = urllib.request.Request(cu_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    data = re.sub(r'<[^>]+>', '\n', data)
    data = html.unescape(data)
    texts['courses'] = re.sub(r'\n{3,}', '\n\n', '\n'.join(l.strip() for l in data.split('\n'))).strip()
    print(f'Fetched courses: {len(texts["courses"])} chars')
except Exception as e:
    print(f'courses fetch error: {e}')

# zollege for fee data
zl_url = 'https://zollege.in/university/219767-cluster-university-of-srinagar-cus-srinagar'
try:
    req = urllib.request.Request(zl_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    data = re.sub(r'<[^>]+>', '\n', data)
    data = html.unescape(data)
    texts['fees'] = re.sub(r'\n{3,}', '\n\n', '\n'.join(l.strip() for l in data.split('\n'))).strip()
    print(f'Fetched fees: {len(texts["fees"])} chars')
except Exception as e:
    print(f'fees fetch error: {e}')

# collegeadmission for notices
ca_url = 'https://www.collegeadmission.in/university/cluster-university-of-srinagar-cus-srinagar-410/notice'
try:
    req = urllib.request.Request(ca_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    data = re.sub(r'<[^>]+>', '\n', data)
    data = html.unescape(data)
    texts['notices'] = re.sub(r'\n{3,}', '\n\n', '\n'.join(l.strip() for l in data.split('\n'))).strip()
    print(f'Fetched notices: {len(texts["notices"])} chars')
except Exception as e:
    print(f'notices fetch error: {e}')

# admission notification page
ad_url = 'https://www.collegeadmission.in/notice/university/admission-to-undergraduate-ug-programme-2026-27-15252'
try:
    req = urllib.request.Request(ad_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    data = re.sub(r'<[^>]+>', '\n', data)
    data = html.unescape(data)
    texts['ug_admission_notice'] = re.sub(r'\n{3,}', '\n\n', '\n'.join(l.strip() for l in data.split('\n'))).strip()
    print(f'Fetched UG admission notice: {len(texts["ug_admission_notice"])} chars')
except Exception as e:
    print(f'UG notice fetch error: {e}')

# educationdunia page
ed_url = 'https://educationdunia.com/university/cluster-university-of-srinagar/admission'
try:
    req = urllib.request.Request(ed_url, headers={'User-Agent': 'Mozilla/5.0'})
    data = urllib.request.urlopen(req, timeout=30).read().decode('utf-8', errors='replace')
    data = re.sub(r'<[^>]+>', '\n', data)
    data = html.unescape(data)
    texts['educationdunia'] = re.sub(r'\n{3,}', '\n\n', '\n'.join(l.strip() for l in data.split('\n'))).strip()
    print(f'Fetched educationdunia: {len(texts["educationdunia"])} chars')
except Exception as e:
    print(f'educationdunia fetch error: {e}')

with open(os.path.join(outdir, 'all_scraped_text.json'), 'w', encoding='utf-8') as f:
    json.dump(texts, f, indent=2, ensure_ascii=False)
print(f'Saved all text to {os.path.join(outdir, "all_scraped_text.json")}')
