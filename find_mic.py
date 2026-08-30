with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re
patterns = ['input-wrap', 'class="send"', 'aria-label="Send message"', 'sendClick']
for p in patterns:
    positions = [m.start() for m in re.finditer(re.escape(p), content, re.IGNORECASE)]
    print(f'Pattern "{p}" found at positions: {positions[:5]}')