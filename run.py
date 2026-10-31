#!/usr/bin/env python3
"""
FactoryGuard AI — one-file launcher (Windows / macOS / Linux).

    python run.py              # set up (first time) and start on http://localhost:8000
    python run.py --port 9000  # another port
    python run.py --reset      # wipe the demo database and re-seed
    python run.py --no-browser # don't open the browser
    python run.py --test       # start, run the 82 API checks, then stop

First run: creates backend/.venv, installs requirements, copies .env.example -> .env,
trains the ML models and seeds 30 days of synthetic plant data (about 1-3 minutes).
"""
import argparse
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import venv
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
VENV = BACKEND / ".venv"
PY = VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
REQ = BACKEND / "requirements.txt"
STAMP = VENV / ".requirements-installed"


def say(msg):
    print(f"[FactoryGuard] {msg}", flush=True)


def fail(msg):
    print(f"\n[FactoryGuard] ERROR: {msg}\n", file=sys.stderr, flush=True)
    sys.exit(1)


def check_python():
    if sys.version_info < (3, 10):
        fail(f"Python 3.10 or newer is required (you have {sys.version.split()[0]}). "
             "Install it from https://www.python.org/downloads/ and run: python run.py")


def ensure_venv():
    if not PY.exists():
        say("Creating virtual environment in backend/.venv …")
        venv.EnvBuilder(with_pip=True, clear=True).create(VENV)
    if not STAMP.exists() or STAMP.read_text() != REQ.read_text():
        say("Installing Python packages (first run only, ~1-2 min) …")
        r = subprocess.run([str(PY), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", str(REQ)])
        if r.returncode:
            fail("pip install failed. Check your internet connection, then run python run.py again.")
        STAMP.write_text(REQ.read_text())


def ensure_env():
    env, example = ROOT / ".env", ROOT / ".env.example"
    if not env.exists() and example.exists():
        shutil.copy(example, env)
        say("Created .env from .env.example (add an LLM API key there for live GenAI; optional).")


def port_free(port):
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


def wait_healthy(port, proc, timeout=240):
    url = f"http://127.0.0.1:{port}/api/health"
    t0 = time.time()
    while time.time() - t0 < timeout:
        if proc.poll() is not None:
            fail("The server stopped during startup — see the messages above.")
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                if r.status == 200:
                    return True
        except Exception:
            time.sleep(1)
    return False


def main():
    ap = argparse.ArgumentParser(description="Start FactoryGuard AI")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow other devices on your network")
    ap.add_argument("--reset", action="store_true", help="delete the demo database and re-seed")
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument("--test", action="store_true", help="start, run API tests, stop")
    a = ap.parse_args()

    check_python()
    ensure_venv()
    ensure_env()
    if a.reset:
        for f in BACKEND.glob("factoryguard.db*"):
            f.unlink()
        say("Database removed; it will be re-seeded on start.")
    if not port_free(a.port):
        fail(f"Port {a.port} is already in use. Close the other program or run: python run.py --port {a.port + 1}")

    say("Starting server (first start trains models and seeds data — please wait) …")
    proc = subprocess.Popen([str(PY), "-m", "uvicorn", "app.main:app", "--host", a.host, "--port", str(a.port)],
                            cwd=str(BACKEND))
    try:
        if not wait_healthy(a.port, proc):
            fail("Server did not become ready in time.")
        url = f"http://localhost:{a.port}"
        print("\n" + "=" * 62)
        print(f"  FactoryGuard AI is running:  {url}")
        print(f"  API docs (Swagger):          {url}/docs")
        print("  Logins: admin/Admin@123  planthead/Plant@123")
        print("          tech1/Tech@123   op01/Oper@123")
        print("  Press Ctrl+C to stop.")
        print("=" * 62 + "\n", flush=True)
        if a.test:
            r = subprocess.run([str(PY), str(BACKEND / "tests" / "test_api.py"), url])
            sys.exit(r.returncode)
        if not a.no_browser:
            webbrowser.open(url)
        proc.wait()
    except KeyboardInterrupt:
        say("Stopping …")
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
