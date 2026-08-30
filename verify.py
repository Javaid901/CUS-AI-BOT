#!/usr/bin/env python3
with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
print('Length:', len(content))
idx = content.find('sendClick')
print('sendClick at:', idx if idx >= 0 else 'NOT FOUND')
idx2 = content.lower().find('.mic')
print('.mic at:', idx2 if idx2 >= 0 else 'NOT FOUND')
idx3 = content.find('button class="send"')
print('send button at:', idx3 if idx3 >= 0 else 'NOT FOUND')