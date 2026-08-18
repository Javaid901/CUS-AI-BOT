#!/usr/bin/env python
# Remove helpdesk from SERVICE_KEYWORDS in extractor.py
with open('app/orchestrator/extractor.py', 'r') as f:
    lines = f.readlines()

new_lines = []
for line in lines:
    if '"helpdesk": "helpdesk"' in line:
        continue
    new_lines.append(line)

with open('app/orchestrator/extractor.py', 'w') as f:
    f.writelines(new_lines)

print('Removed helpdesk from SERVICE_KEYWORDS')