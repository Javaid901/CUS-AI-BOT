with open('frontend/js/chatbot.js', 'r', errors='replace') as f:
    content = f.read()

# Show the area around the mic button
idx = content.lower().find('class="mic"')
print('=== MIC BUTTON STRUCTURE ===')
print(content[max(0,idx-50):idx+200])