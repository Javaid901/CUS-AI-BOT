with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()
import re
# Find 'gold' class references
gold_pos = [m.start() for m in re.finditer('class="gold"', content)]
print('gold class positions:', gold_pos)
# Also search for 'Ask CUS AI' 
ask_pos = content.find('Ask CUS AI')
print('Ask CUS AI at:', ask_pos)
if ask_pos >= 0:
    print('Context:', content[ask_pos-100:ask_pos+100])
# Also check the index.html for the launcher
with open('frontend/pages/index.html', 'r', errors='replace') as f:
    ica = f.read()
ask_in_html = ica.find('Ask CUS AI')
print('Ask CUS AI in HTML at:', ask_in_html)
if ask_in_html >= 0:
    print('HTML context:', ica[ask_in_html-50:ask_in_html+150])