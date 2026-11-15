#!/usr/bin/env python3
"""Run demo services plus an idle, opt-in real observation worker."""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from srs.config import Settings

ROOT = Path(__file__).resolve().parents[1]


def main():
    settings = Settings(mode="demo")
    if not settings.configured:
        raise SystemExit("Copy .env.web.example to .env.web and set a unique WEB_ADMIN_PASSWORD of at least 12 characters.")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the frontend first: npm --prefix frontend ci && npm --prefix frontend run build")
    environment = {**os.environ, "SRS_MODE": "demo"}
    processes = []
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for command in (["api", "--host", "127.0.0.1", "--port", "8000"], ["worker"], ["scheduler"],
                        ["monitor", "--credentials-file", str(ROOT / ".env.hosts")]):
            processes.append(subprocess.Popen([sys.executable, "-m", "srs.cli", *command], cwd=ROOT, env=environment))
            # Let the API create the demo schema before the other processes open it.
            if command[0] == "api":
                time.sleep(1)
        print("http://127.0.0.1:8000 — jobs: demo; monitoring: real SSH only after Start. Ctrl+C stops all services.", flush=True)
        while not stopping:
            if any(process.poll() is not None for process in processes):
                raise SystemExit("A demo process stopped; shutting down the remaining services.")
            time.sleep(0.25)
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    main()
