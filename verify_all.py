with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()

checks = [
    ('class="mic"', 'mic button HTML'),
    ('SpeechRecognition', 'SpeechRecognition'),
    ('webkitSpeechRecognition', 'webkitSpeechRecognition'),
    ('micStart', 'micStart function'),
    ('micFinalize', 'micFinalize function'),
    ('pointerdown', 'pointerdown handler'),
    ('pointerup', 'pointerup handler'),
    ('micSubmitting', 'micSubmitting flag'),
    ('sendClick', 'sendClick call'),
]
for check, desc in checks:
    found = check in content
    print(f'  {desc}: {"FOUND" if found else "MISSING"}')

# Also check CSS
with open('frontend/css/chatbot.css', 'r', errors='replace') as f:
    css = f.read()
css_checks = [
    ('input-wrap .mic', 'mic button CSS'),
    ('micPulse', 'micPulse animation'),
    ('listening .mic-label', 'listening label'),
]
for check, desc in css_checks:
    found = check in css
    print(f'  CSS {desc}: {"FOUND" if found else "MISSING"}')