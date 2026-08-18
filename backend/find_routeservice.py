with open('C:\\Users\\LENOVO\\OneDrive\\Desktop\\CUS-AI-bot\\backend\\app\\orchestrator\\engine.py', 'r') as f:
    lines = f.readlines()
    for i, line in enumerate(lines, 1):
        if '_route_service' in line:
            print(f"{i}: {line}", end='')