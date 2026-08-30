with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re

terms = ['SpeechRecognition', 'webkitSpeechRecognition', '.mic', 'micStart', 'micFinalize', 
         'pointerdown', 'pointerup', 'interimResults', 'continuous', 'recognition',
         'sendClick', 'micSubmitting', 'mic-label', 'micPulse', 'listening']

for term in terms:
    count = content.lower().count(term.lower())
    print(f'{term}: {count} occurrences')

# Also check CSS
with open('frontend/css/chatbot.css', 'r', errors='replace') as f:
    css = f.read()

css_terms = ['mic', '.mic', 'micPulse', 'listening', 'mic-label']
for term in css_terms:
    count = css.lower().count(term.lower())
    print(f'CSS {term}: {count} occurrences')