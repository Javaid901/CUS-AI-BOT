#!/usr/bin/env python3
with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()

textarea_pos = content.find('<textarea rows="1"')
if textarea_pos < 0:
    print('ERROR: textarea not found')
    import sys
    sys.exit(1)

textarea_end = content.find('</textarea>', textarea_pos)
if textarea_end < 0:
    print('ERROR: textarea end not found')
    import sys
    sys.exit(1)

mic_button = '''          <button class="mic" aria-label="Hold to speak" title="Hold to speak" type="button" aria-pressed="false">
            <svg class="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-6 0z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            <span class="mic-label">Listening...</span>
          </button>'''

new_content = content[:textarea_end + len('</textarea>')] + mic_button + content[textarea_end + len('</textarea>'):]

with open('frontend/js/chatbot.js', 'w', errors='replace') as f:
    f.write(new_content)

print('Mic button HTML added successfully')