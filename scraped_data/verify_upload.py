"""Verify the upload and test chat retrieval."""
import urllib.request, json, urllib.parse

# Login as admin
login_data = urllib.parse.urlencode({'username': 'admin', 'password': 'admin123'}).encode()
req = urllib.request.Request('http://localhost:8001/api/auth/login', data=login_data, method='POST')
req.add_header('Content-Type', 'application/x-www-form-urlencoded')
token = json.loads(urllib.request.urlopen(req).read())['access_token']

# List documents
req2 = urllib.request.Request('http://localhost:8001/api/documents', method='GET')
req2.add_header('Authorization', f'Bearer {token}')
docs = json.loads(urllib.request.urlopen(req2).read())
print(f'Total documents: {len(docs)}')
for d in docs:
    nm = d.get('filename', '?')
    st = d.get('status', '?')
    ch = d.get('chunks', 0)
    print(f'  - {nm}: {st} ({ch} chunks)')

# KB Health
req3 = urllib.request.Request('http://localhost:8001/api/admin/kb-health', method='GET')
req3.add_header('Authorization', f'Bearer {token}')
health = json.loads(urllib.request.urlopen(req3).read())
print()
print(f'KB Health: {health["status"]}')
print(f'Documents: {health["documents"]["total"]}')
print(f'Chunks: {health["chunks"]}')
print(f'Conversations: {health["conversations"]}')

# Test chat - ask about something that should be in the PDF
import time
import urllib.request

# Login as a student
slogin = urllib.parse.urlencode({'username': 'verify_test', 'password': 'test123'}).encode()
sreq = urllib.request.Request('http://localhost:8001/api/auth/register', 
    data=json.dumps({'username': 'verify_test', 'email': 'verify@test.com', 'password': 'test123', 'role': 'student'}).encode(),
    method='POST')
sreq.add_header('Content-Type', 'application/json')
try:
    stoken = json.loads(urllib.request.urlopen(sreq).read())['access_token']
except:
    sreq2 = urllib.request.Request('http://localhost:8001/api/auth/login', data=slogin, method='POST')
    sreq2.add_header('Content-Type', 'application/x-www-form-urlencoded')
    stoken = json.loads(urllib.request.urlopen(sreq2).read())['access_token']

print()

# Test questions
questions = [
    "What programs does Cluster University offer?",
    "How can I apply for UG admissions?",
    "What is the contact address of Cluster University Srinagar?",
    "Who is the Vice Chancellor of Cluster University?",
]

for q in questions:
    chat_body = json.dumps({'message': q, 'stream': False}).encode()
    creq = urllib.request.Request('http://localhost:8001/api/chat/ask', data=chat_body, method='POST')
    creq.add_header('Authorization', f'Bearer {stoken}')
    creq.add_header('Content-Type', 'application/json')
    t0 = time.time()
    try:
        resp = urllib.request.urlopen(creq, timeout=60)
        data = resp.read().decode()
        elapsed = time.time() - t0
        # Extract answer from SSE
        tokens = []
        for line in data.split('\n'):
            if line.startswith('data:') and not line.startswith('data: *') and not line.startswith('data: {'):
                tokens.append(line[5:].strip())
        answer = ''.join(tokens)
        has_done = 'event: done' in data
        print(f'Q: {q}')
        print(f'  Time: {elapsed:.1f}s | done: {has_done}')
        print(f'  Answer: {answer[:150]}...')
        print()
    except Exception as e:
        print(f'Q: {q} -> FAIL: {e}')
        print()

print('=== Verification Complete ===')
