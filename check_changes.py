with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re
checks = [
    ('function sendClick', 'sendClick'),
    ('<textarea rows', 'textarea'),
    ('<button class="send"', 'send button'),
    ('class="input-wrap"', 'input-wrap'),
    ('.mic', 'mic button'),
]
for check, desc in checks:
    found = check in content.lower()
    print(f'  {desc}: {"FOUND" if found else "MISSING"}')

with open('frontend/css/chatbot.css', 'r', errors='replace') as f:
    css = f.read()
css_checks = ['.mic', 'micPulse', 'listening']
for c in css_checks:
    found = c in css
    print(f'  CSS {c}: {"FOUND" if found else "MISSING"}')