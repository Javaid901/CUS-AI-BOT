with open('C:\\Users\\LENOVO\\OneDrive\\Desktop\\CUS-AI-bot\\backend\\app\\orchestrator\\engine.py', 'r') as f:
    lines = f.readlines()
    for i in range(1590, min(1650, len(lines))):
        print(f"{i}: {lines[i-1]}", end='')