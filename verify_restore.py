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

print('=== CHATBOT.JS CHECKS ===')
for check, desc in checks:
    found = check in content
    print('  ' + desc + ': ' + ('FOUND' if found else 'MISSING'))

# Check CSS
with open('frontend/css/chatbot.css', 'r', errors='replace') as f:
    css = f.read()

css_checks = ['input-wrap .mic', 'micPulse', 'listening .mic-label']
print()
print('=== CSS CHECKS ===')
for c in css_checks:
    found = c in css
    print('  CSS ' + c + ': ' + ('FOUND' if found else 'MISSING'))