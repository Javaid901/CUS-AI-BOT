"""
backend/run.py

Application entrypoint with port cleanup.

Kills any stale process on the configured port before binding,
preventing the EADDRINUSE (10048) error from orphaned uvicorn children.

Usage:
    python run.py                # serves on configured host/port
    uvicorn app.main:app --reload  # dev with reload (use this instead of DEBUG=True)
"""

from __future__ import annotations

import os
import socket

import uvicorn

from app.config import settings


def _free_port(host: str, port: int) -> None:
    """Kill any process holding the given port."""
    import subprocess
    import sys

    try:
        # Check if port is in use
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        result = s.connect_ex((host, port))
        s.close()
        if result != 0:
            return  # Port is free

        # Find and kill the owning process
        if sys.platform == "win32":
            cmd = f'netstat -ano | findstr ":{port} "'
            output = subprocess.check_output(cmd, shell=True).decode()
            for line in output.strip().splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and "LISTENING" in line:
                    pid = parts[-1]
                    try:
                        os.kill(int(pid), 9)
                        print(f"Killed stale process PID {pid} on port {port}")
                    except (OSError, ValueError):
                        pass
        else:
            cmd = ["fuser", "-k", f"{port}/tcp"]
            subprocess.run(cmd, capture_output=True)
    except Exception:
        pass  # Best-effort cleanup


def main() -> None:
    _free_port(settings.HOST, settings.PORT)
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
