"""Upload the PDF to the admin panel."""
import urllib.request, urllib.parse, json, os, sys

# Login
login_data = urllib.parse.urlencode({
    'username': 'admin',
    'password': 'admin123'
}).encode()
login_req = urllib.request.Request(
    'http://localhost:8001/api/auth/login',
    data=login_data,
    method='POST'
)
login_req.add_header('Content-Type', 'application/x-www-form-urlencoded')

resp = urllib.request.urlopen(login_req)
token = json.loads(resp.read())['access_token']
print(f'Token: {token[:20]}...')

# Upload PDF
script_dir = os.path.dirname(os.path.abspath(__file__))
pdf_path = os.path.join(script_dir, 'CUS_Complete_Knowledge_Base.pdf')

with open(pdf_path, 'rb') as f:
    pdf_data = f.read()

print(f'PDF size: {len(pdf_data)} bytes')

boundary = '----WebKitFormBoundary' + os.urandom(16).hex()

part_boundary = b'--' + boundary.encode()
close_boundary = b'--' + boundary.encode() + b'--'

parts = []
parts.append(part_boundary)
parts.append(b'Content-Disposition: form-data; name="file"; filename="CUS_Complete_Knowledge_Base.pdf"')
parts.append(b'Content-Type: application/pdf')
parts.append(b'')
parts.append(pdf_data)
parts.append(close_boundary)

body = b'\r\n'.join(parts)

upload_url = 'http://localhost:8001/api/documents/upload'
req = urllib.request.Request(upload_url, data=body, method='POST')
req.add_header('Authorization', f'Bearer {token}')
req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')

try:
    resp = urllib.request.urlopen(req, timeout=120)
    result = json.loads(resp.read())
    print(f'Upload OK: id={result.get("id")} status={result.get("status")} chunks={result.get("chunks")}')
except urllib.error.HTTPError as e:
    print(f'Upload FAIL: code={e.code}')
    print(f'Response: {e.read().decode()}')
    sys.exit(1)

print('Done!')
