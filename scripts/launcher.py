#!/usr/bin/env python3
"""Start the Attention Atlas and open it in a browser.

This is what the desktop and Start-menu shortcuts run. It is deliberately
tolerant: it finds a usable Python, installs anything missing, reuses an
instance that is already running, and reports failures in a dialog box rather
than a console the user will never see.

    python scripts/launcher.py
"""
from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "app.py"
VENV = ROOT / ".venv"
LOG = ROOT / "data" / "launcher.log"
PREFERRED_PORT = 8501
STARTUP_TIMEOUT = 180  # seconds; first run compiles a lot of wheels


# --- talking to the user -----------------------------------------------------

def log(message: str) -> None:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except OSError:
        pass


def notify(message: str) -> None:
    """Non-blocking heads-up. Silent if the platform has no easy way to show one."""
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display notification {message!r} with title "Wikipedia Attention Atlas"'],
                capture_output=True, timeout=10)
    except Exception:
        pass


def alert(title: str, message: str) -> None:
    """Blocking error dialog, because a shortcut has nowhere to print."""
    log(f"ERROR: {title} — {message}")
    detail = f"{message}\n\nDetails were written to:\n{LOG}"
    try:
        if sys.platform == "darwin":
            subprocess.run(
                ["osascript", "-e",
                 f'display dialog {detail!r} with title {title!r} '
                 f'buttons {{"OK"}} default button "OK" with icon caution'],
                capture_output=True, timeout=120)
        elif os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, detail, title, 0x10)
        else:
            print(f"{title}: {detail}", file=sys.stderr)
    except Exception:
        print(f"{title}: {detail}", file=sys.stderr)


# --- finding a Python that can run the app -----------------------------------

def venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def can_run(python: Path | str) -> bool:
    """True if this interpreter already has the dependencies."""
    try:
        result = subprocess.run(
            [str(python), "-c", "import streamlit, pandas, plotly, networkx"],
            capture_output=True, timeout=120, text=True, cwd=str(ROOT))
        if result.returncode != 0:
            # Worth logging: the alternative is silently building a second copy
            # of every dependency in a venv the user did not ask for.
            # Keep the head, not the tail: with chained exceptions the root
            # cause is printed first and the outer wrapper last, and the
            # wrapper is usually the less informative of the two.
            detail = (result.stderr or result.stdout or "").strip()
            log(f"{python} cannot run the app:\n{detail[:1200]}")
        return result.returncode == 0
    except Exception as exc:
        log(f"Could not test {python}: {exc}")
        return False


def create_environment() -> Path:
    """Build .venv and install requirements. Returns the interpreter path."""
    notify("Setting up for first use — this takes a minute.")
    log("Creating virtual environment…")
    subprocess.run([sys.executable, "-m", "venv", str(VENV)], check=True,
                   capture_output=True)
    python = venv_python()
    log("Installing dependencies…")
    subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"],
                   capture_output=True)
    result = subprocess.run(
        [str(python), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")],
        capture_output=True, text=True)
    if result.returncode != 0:
        log(result.stdout[-3000:])
        log(result.stderr[-3000:])
        raise RuntimeError("Installing dependencies failed. See the log for pip's output.")
    log("Dependencies installed.")
    return python


def resolve_python() -> Path | str:
    """Prefer an interpreter that already works; otherwise build one."""
    if can_run(sys.executable):
        log(f"Using the current interpreter: {sys.executable}")
        return sys.executable
    if venv_python().exists() and can_run(venv_python()):
        log(f"Using the project environment: {venv_python()}")
        return venv_python()
    return create_environment()


# --- the server --------------------------------------------------------------

def is_serving(port: int) -> bool:
    """True if a Streamlit app is already answering on this port."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=1.5) as response:
            return response.read().strip() == b"ok"
    except (URLError, OSError, ValueError):
        return False


def free_port(preferred: int = PREFERRED_PORT) -> int:
    for port in range(preferred, preferred + 20):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket() as probe:          # let the OS choose
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def start_server(python: Path | str, port: int) -> subprocess.Popen:
    command = [
        str(python), "-m", "streamlit", "run", str(APP),
        "--server.port", str(port),
        "--server.headless", "true",       # we open the browser ourselves
        "--browser.gatherUsageStats", "false",
    ]
    log(f"Starting: {' '.join(command)}")
    kwargs: dict = {"cwd": str(ROOT), "stdout": subprocess.PIPE,
                    "stderr": subprocess.STDOUT, "text": True}
    if os.name == "nt":                     # no console window flash
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    else:
        # Own process group, so shutting down takes the whole server tree with
        # it rather than just the direct child.
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def stop_server(process: subprocess.Popen) -> None:
    """Shut the server down, children included."""
    if process.poll() is not None:
        return
    try:
        if os.name != "nt":
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:
            process.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        try:
            if os.name != "nt":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.kill()
        except (ProcessLookupError, PermissionError, OSError):
            process.kill()


def install_signal_handlers() -> None:
    """Turn a termination signal into an exception the cleanup path can catch.

    Quitting from the Dock or Task Manager sends SIGTERM, whose default
    behaviour ends the process without running `finally` — which would strand
    the Streamlit server as an orphan the user cannot stop.
    """
    def handle(signum, frame):
        raise KeyboardInterrupt

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handle)
        except (ValueError, OSError, AttributeError):
            pass


def wait_until_ready(process: subprocess.Popen, port: int) -> bool:
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout else ""
            log(f"Server exited early:\n{output[-3000:]}")
            return False
        if is_serving(port):
            return True
        time.sleep(0.4)
    return False


def main() -> int:
    # A shortcut launches us from wherever the desktop happens to be — "/" on
    # macOS — so anchor to the project before doing anything else. This also
    # keeps the dependency check and the server start in the same directory.
    os.chdir(ROOT)

    log("=" * 60)
    install_signal_handlers()
    if not APP.exists():
        alert("Wikipedia Attention Atlas",
              f"Could not find app.py.\n\nExpected it at:\n{APP}\n\n"
              "The shortcut may be pointing at a folder that has moved.")
        return 1

    if is_serving(PREFERRED_PORT):
        log("Already running — opening the existing window.")
        webbrowser.open(f"http://localhost:{PREFERRED_PORT}")
        return 0

    try:
        python = resolve_python()
    except Exception as exc:
        alert("Wikipedia Attention Atlas",
              f"Could not prepare the Python environment.\n\n{exc}")
        return 1

    port = free_port()
    process = start_server(python, port)
    if not wait_until_ready(process, port):
        stop_server(process)
        alert("Wikipedia Attention Atlas",
              "The dashboard did not start within the expected time.")
        return 1

    url = f"http://localhost:{port}"
    log(f"Ready at {url}")
    webbrowser.open(url)

    try:
        process.wait()                      # keep the shortcut's process alive
    except KeyboardInterrupt:
        log("Shutting down…")
    finally:
        stop_server(process)
        log("Stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
