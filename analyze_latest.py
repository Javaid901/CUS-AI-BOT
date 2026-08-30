with open('latest_chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re
terms = ['SpeechRecognition', 'webkitSpeechRecognition', '.mic', 'micStart', 'micFinalize', 
         'pointerdown', 'pointerup', 'interimResults', 'continuous', 'recognition',
         'sendClick', 'micSubmitting', 'mic-label', 'micPulse', 'listening']
for term in terms:
    count = content.lower().count(term.lower())
    print(f'{term}: {count} occurrences')
PYEOF