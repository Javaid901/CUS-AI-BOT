with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re
btn_pos = [m.start() for m in re.finditer('class="btn"', content)]
print('btn class positions:', btn_pos[:10])
gold_pos = [m.start() for m in re.finditer('"gold"', content)]
print('"gold" positions:', gold_pos[:10])
a_gold = [m.start() for m in re.finditer('class="btn gold"', content)]
print('class="btn gold" positions:', a_gold)