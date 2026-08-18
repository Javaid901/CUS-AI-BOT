with open('C:\\Users\\LENOVO\\OneDrive\\Desktop\\CUS-AI-bot\\backend\\app\\orchestrator\\engine.py', 'r') as f:
    lines = f.readlines()
    # Print from line 1468 onwards
    for i in range(1468, min(1600, len(lines))):
        print(f"{i}: {lines[i-1]}", end='')