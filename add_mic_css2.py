with open('frontend/css/chatbot.css', 'r', errors='replace') as f:
    content = f.read()

# Add mic button styles
mic_styles = '''
#cusw .input-wrap .mic {
  width: 42px; height: 42px; border-radius: 50%; flex-shrink: 0; border: none;
  background: var(--cus-surface); color: var(--cus-green);
  cursor: pointer; display: grid; place-items: center;
  transition: all .2s ease; position: relative;
  border: 1.5px solid #cfd9df;
}
#cusw .input-wrap .mic:hover:not(:disabled) {
  background: #f0f7f3; border-color: #0f5132; transform: scale(1.05);
  box-shadow: 0 4px 16px rgba(15,81,50,.15);
}
#cusw .input-wrap .mic:active:not(:disabled) { transform: scale(.93); }
#cusw .input-wrap .mic:disabled { opacity: .35; cursor: default; transform: none; box-shadow: none; }
#cusw .input-wrap .mic .mic-icon { width: 20px; height: 20px; transition: opacity .2s ease; }
#cusw .input-wrap .mic .mic-label {
  position: absolute; bottom: 100%; left: 50%; transform: translateX(-50%);
  white-space: nowrap; font-size: 11px; color: #6b7280;
  background: #f3f4f6; padding: 2px 8px; border-radius: 6px;
  opacity: 0; visibility: hidden; pointer-events: none;
  transition: opacity .15s ease, visibility .15s ease;
  margin-bottom: 4px;
}
#cusw .input-wrap .mic.listening .mic-label {
  opacity: 1; visibility: visible;
}
#cusw .input-wrap .mic.listening .mic-icon {
  animation: micPulse 1s ease-in-out infinite;
  stroke: #0f5132;
}
#cusw .input-wrap .mic.listening {
  background: #e8f0ec; border-color: #0f5132;
  box-shadow: 0 0 0 3px rgba(15,81,50,.15);
}
@keyframes micPulse {
  0%, 100% { transform: scale(1); opacity: 1; }
  50% { transform: scale(1.1); opacity: .7; }
}
'''

# Insert before the send button styles
pos = content.find('#cusw .input-wrap .send {')
if pos >= 0:
    new_content = content[:pos] + mic_styles + content[pos:]
    with open('frontend/css/chatbot.css', 'w', errors='replace') as f:
        f.write(new_content)
    print('Mic CSS styles added')
else:
    print('Could not find send button styles position')