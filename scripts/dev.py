#!/usr/bin/env python3
"""Run demo services plus an idle, opt-in real observation worker."""
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import ProxyHandler, build_opener

from srs.config import Settings

ROOT = Path(__file__).resolve().parents[1]
ADDRESS = ("127.0.0.1", 8000)
BASE_URL = f"http://{ADDRESS[0]}:{ADDRESS[1]}"


def wait_for_api(process, *, timeout=90, stopping=lambda: False):
    """Wait for the API's health route, which only exists after database setup."""
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + timeout
    while not stopping() and time.monotonic() < deadline:
        if process.poll() is not None:
            raise SystemExit("API exited before the database was ready. See the startup error above.")
        try:
            with opener.open(f"{BASE_URL}/api/v1/health", timeout=0.5) as response:
                if response.status == 200 and process.poll() is None:
                    return
        except OSError:
            pass
        time.sleep(0.15)
    if not stopping():
        raise SystemExit("Timed out waiting for the API and database. No background services were started.")



def main():
    settings = Settings(mode="demo")
    if not settings.configured:
        raise SystemExit("Copy .env.web.example to .env.web and set a unique WEB_ADMIN_PASSWORD of at least 12 characters.")
    if not (ROOT / "frontend/dist/index.html").is_file():
        raise SystemExit("Build the frontend first: pnpm --dir frontend install --frozen-lockfile && pnpm --dir frontend build")
    environment = {**os.environ, "SRS_MODE": "demo"}
    processes = []
    stopping = False
    def stop(signum, frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(ADDRESS)
            except OSError:
                raise SystemExit(f"{BASE_URL} is unavailable. Stop the existing service before starting SRS.") from None
        api = subprocess.Popen(
            [sys.executable, "-m", "srs.cli", "api", "--host", ADDRESS[0], "--port", str(ADDRESS[1])],
            cwd=ROOT, env=environment,
        )
        processes.append(api)
        wait_for_api(api, stopping=lambda: stopping)
        if stopping:
            return
        for command in (["worker"], ["scheduler"], ["monitor", "--credentials-file", str(ROOT / ".env.hosts")]):
            processes.append(subprocess.Popen([sys.executable, "-m", "srs.cli", *command], cwd=ROOT, env=environment))
        print(f"{BASE_URL} — database ready. Jobs: demo; monitoring: real SSH only after Start. Ctrl+C stops all services.", flush=True)
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
