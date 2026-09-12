"""
DIYA scripts/run_demo.py — one-command startup for both servers.

Cross-platform (Windows/POSIX) orchestration: checks dependencies, starts
the FastAPI backend and the Vite dev server as background subprocesses,
waits for both to be ready, prints their URLs, and cleanly tears down BOTH
-- including any child processes (e.g. npm's spawned vite node process,
uvicorn's own children) -- on Ctrl+C. No `make` dependency; this machine
has none installed, and Python is already the one dependency every part
of this project shares.
"""

from __future__ import annotations

import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import requests

# Python fully buffers stdout by default whenever it isn't a live TTY (a
# background launcher, a log-file redirect, a process supervisor) -- which
# is exactly how a "run this and watch its progress" script tends to get
# invoked. Without this, nothing below prints until the buffer fills or the
# process exits, which for a script that runs indefinitely until Ctrl+C
# means "printed nothing, ever." Force line buffering unconditionally.
sys.stdout.reconfigure(line_buffering=True)

REPO_ROOT = Path(__file__).resolve().parents[1]
WEB_DIR = REPO_ROOT / "web"

IS_WINDOWS = platform.system() == "Windows"
NPM_CMD = "npm.cmd" if IS_WINDOWS else "npm"

BACKEND_HEALTH_URL = "http://localhost:8000/api/health"
BACKEND_TIMEOUT_S = 15.0
FRONTEND_TIMEOUT_S = 15.0
POLL_INTERVAL_S = 0.3

LOCAL_URL_RE = re.compile(r"Local:\s+(http://\S+)")
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    """Vite/npm emit ANSI color codes even when stdout is piped (not a
    TTY); an escape sequence can land INSIDE a word we need to match
    literally (e.g. "Local\\x1b[22m:"), so strip them before storing."""
    return ANSI_ESCAPE_RE.sub("", text)


REQUIRED_MODULES = {
    "fastapi": "fastapi",
    "uvicorn": "uvicorn[standard]",
    "pydantic": "pydantic",
    "yaml": "pyyaml",
    "pandas": "pandas",
    "pyarrow": "pyarrow",
    "numpy": "numpy",
    "pvlib": "pvlib",
    "pulp": "PuLP",
    "requests": "requests",
}


class OutputCollector:
    """Reads a subprocess's combined stdout/stderr in a background thread,
    keeping the last `max_lines` for post-mortem printing and letting
    callers search it (e.g. for Vite's printed "Local: <url>" line)."""

    def __init__(self, proc: subprocess.Popen, max_lines: int = 300):
        self.proc = proc
        self.lines: list[str] = []
        self._lock = threading.Lock()
        self._max_lines = max_lines
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    def _read(self) -> None:
        try:
            for line in self.proc.stdout:
                with self._lock:
                    self.lines.append(_strip_ansi(line.rstrip("\n")))
                    if len(self.lines) > self._max_lines:
                        self.lines.pop(0)
        except (ValueError, OSError):
            pass  # pipe closed on process exit

    def text(self) -> str:
        with self._lock:
            return "\n".join(self.lines)

    def find(self, pattern: re.Pattern) -> re.Match | None:
        with self._lock:
            for line in self.lines:
                m = pattern.search(line)
                if m:
                    return m
        return None


def start_process(cmd: list[str], cwd: Path) -> subprocess.Popen:
    """Starts `cmd` as its own process group/job so kill_process_tree can
    take down the whole tree later (npm spawns a separate node process for
    the actual Vite server; killing only npm's own PID orphans it)."""
    env = {**os.environ, "NO_COLOR": "1", "FORCE_COLOR": "0"}
    kwargs: dict = dict(
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        env=env,
    )
    if IS_WINDOWS:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def kill_process_tree(proc: subprocess.Popen, label: str) -> None:
    if proc.poll() is not None:
        return
    print(f"  stopping {label} (pid {proc.pid})...")
    if IS_WINDOWS:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            text=True,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def check_dependencies() -> None:
    print("Checking dependencies...")
    missing = []
    for module_name, package_name in REQUIRED_MODULES.items():
        try:
            __import__(module_name)
        except ImportError:
            missing.append(package_name)
    if missing:
        print(f"  ERROR: missing Python dependencies: {', '.join(missing)}")
        print("  Run: pip install -r requirements.txt")
        sys.exit(1)
    print("  core/api Python dependencies: OK")

    if not (WEB_DIR / "node_modules").exists():
        print("  web/node_modules missing -- running npm install (this may take a minute)...")
        result = subprocess.run([NPM_CMD, "install"], cwd=WEB_DIR)
        if result.returncode != 0:
            print("  ERROR: npm install failed.")
            sys.exit(1)
    print("  web/ dependencies: OK")


def start_backend() -> tuple[subprocess.Popen, OutputCollector]:
    print("Starting backend (uvicorn api.main:app --port 8000)...")
    proc = start_process([sys.executable, "-m", "uvicorn", "api.main:app", "--port", "8000"], cwd=REPO_ROOT)
    collector = OutputCollector(proc)

    start = time.time()
    while time.time() - start < BACKEND_TIMEOUT_S:
        if proc.poll() is not None:
            print(f"  ERROR: backend process exited early (code {proc.returncode}). Captured output:")
            print(collector.text())
            sys.exit(1)
        try:
            resp = requests.get(BACKEND_HEALTH_URL, timeout=1)
            if resp.status_code == 200:
                print(f"  backend ready: {BACKEND_HEALTH_URL} -> 200")
                return proc, collector
        except requests.RequestException:
            pass
        time.sleep(POLL_INTERVAL_S)

    print(f"  ERROR: backend did not become healthy within {BACKEND_TIMEOUT_S:.0f}s. Captured output:")
    print(collector.text())
    kill_process_tree(proc, "backend")
    sys.exit(1)


def start_frontend() -> tuple[subprocess.Popen, OutputCollector, str]:
    print("Starting frontend (npm run dev)...")
    proc = start_process([NPM_CMD, "run", "dev"], cwd=WEB_DIR)
    collector = OutputCollector(proc)

    start = time.time()
    while time.time() - start < FRONTEND_TIMEOUT_S:
        if proc.poll() is not None:
            print(f"  ERROR: frontend process exited early (code {proc.returncode}). Captured output:")
            print(collector.text())
            sys.exit(1)
        match = collector.find(LOCAL_URL_RE)
        if match:
            url = match.group(1)
            try:
                resp = requests.get(url, timeout=1)
                if resp.status_code == 200:
                    print(f"  frontend ready: {url}")
                    return proc, collector, url
            except requests.RequestException:
                pass
        time.sleep(POLL_INTERVAL_S)

    print(f"  ERROR: frontend did not start serving within {FRONTEND_TIMEOUT_S:.0f}s. Captured output:")
    print(collector.text())
    kill_process_tree(proc, "frontend")
    sys.exit(1)


def _install_signal_handlers() -> None:
    """Ctrl+C in an interactive terminal delivers SIGINT (-> KeyboardInterrupt)
    directly, since this script shares its invoker's console. On Windows,
    also treat SIGBREAK the same way: it's what a controlling process sends
    when this script is launched as a subprocess with its own process group
    (e.g. an automated test, or a process supervisor) rather than typed
    interactively -- same graceful shutdown path either way."""
    if IS_WINDOWS:

        def _on_break(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGBREAK, _on_break)


def main() -> None:
    _install_signal_handlers()
    check_dependencies()
    backend_proc, backend_out = start_backend()
    frontend_proc, frontend_out, frontend_url = start_frontend()

    print()
    print("  API:      http://localhost:8000")
    print(f"  Frontend: {frontend_url}")
    print()
    print("DIYA is running. Press Ctrl+C to stop both.")

    try:
        while True:
            time.sleep(0.5)
            if backend_proc.poll() is not None:
                print("\nBackend process exited unexpectedly. Captured output:")
                print(backend_out.text())
                break
            if frontend_proc.poll() is not None:
                print("\nFrontend process exited unexpectedly. Captured output:")
                print(frontend_out.text())
                break
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        kill_process_tree(backend_proc, "backend")
        kill_process_tree(frontend_proc, "frontend")
        print("Both servers stopped.")


if __name__ == "__main__":
    main()
