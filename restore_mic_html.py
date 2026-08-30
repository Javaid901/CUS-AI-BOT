with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()

# Find the input-area div and insert mic button after textarea and before send button
# The textarea and send button are in the input-wrap div

# Find where '<textarea rows="1"' appears
textarea_pos = content.find('<textarea rows="1"')
if textarea_pos < 0:
    print('ERROR: textarea not found')
    exit(1)

# Find the end of textarea
textarea_end = content.find('</textarea>', textarea_pos)
if textarea_end < 0:
    print('ERROR: textarea end not found')
    exit(1)

# Find the send button after the textarea
send_pos = content.find('<button class="send"', textarea_end)
if send_pos < 0:
    print('ERROR: send button not found after textarea')
    exit(1)

# The mic button to insert
mic_button = '''          <button class="mic" aria-label="Hold to speak" title="Hold to speak" type="button" aria-pressed="false">
            <svg class="mic-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" width="20" height="20"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-6 0z"/><path d="M19 10v2a7 7 0 0 1-14 0v-2"/><line x1="12" y1="19" x2="12" y2="22"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
            <span class="mic-label">Listening...</span>
          </button>'''

# Insert the mic button right after </textarea>
insert_pos = textarea_end + len('</textarea>')
new_content = content[:insert_pos] + mic_button + content[insert_pos:]

# Write the new content
with open('frontend/js/chatbot.js', 'w', errors='replace') as f:
    f.write(new_content)

print('Mic button HTML added to chatbot.js')