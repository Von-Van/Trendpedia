#!/usr/bin/env python3
"""Create a double-clickable shortcut for the dashboard.

macOS    builds Trendpedia.app and puts it in ~/Applications
Windows  creates shortcuts on the Desktop and in the Start menu
Linux    writes a .desktop entry into the applications menu

    python scripts/install_desktop.py
"""
from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "scripts" / "launcher.py"
ASSETS = ROOT / "assets"

# Shown under the icon. Short names survive the desktop's truncation better.
APP_NAME = "Trendpedia"
DESCRIPTION = "Explore how public attention moves across Wikipedia"
BUNDLE_ID = "com.trendpedia.attentionatlas"


def say(message: str) -> None:
    print(f"  {message}", flush=True)


def ensure_icons() -> None:
    """Generate the icon files if they are not already committed."""
    needed = {"darwin": "icon.icns", "win32": "icon.ico"}.get(sys.platform, "icon.png")
    if (ASSETS / needed).exists():
        return
    say("Generating the application icon…")
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts" / "make_icon.py")],
                       check=True, capture_output=True)
    except Exception as exc:
        say(f"Could not generate icons ({exc}). The shortcut will use a default one.")


def ensure_dependencies() -> str:
    """Make sure something on this machine can run the app. Returns that Python."""
    check = [sys.executable, "-c", "import streamlit, pandas, plotly, networkx"]
    if subprocess.run(check, capture_output=True).returncode == 0:
        say("Dependencies are already installed.")
        return sys.executable

    venv = ROOT / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.exists():
        say("Creating a private Python environment (.venv)…")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)

    if subprocess.run([str(python), "-c", "import streamlit"],
                      capture_output=True).returncode != 0:
        say("Installing dependencies — this takes a minute…")
        subprocess.run([str(python), "-m", "pip", "install", "--upgrade", "pip"],
                       check=False, capture_output=True)
        subprocess.run([str(python), "-m", "pip", "install", "-r",
                        str(ROOT / "requirements.txt")], check=True)
    say("Dependencies ready.")
    return str(python)


# --- macOS -------------------------------------------------------------------

def install_macos(python: str, applications: Path | None = None) -> Path:
    # Built in the project folder, then copied where the user will find it.
    bundle = ROOT / f"{APP_NAME}.app"
    if bundle.exists():
        shutil.rmtree(bundle)

    macos_dir = bundle / "Contents" / "MacOS"
    resources = bundle / "Contents" / "Resources"
    macos_dir.mkdir(parents=True)
    resources.mkdir(parents=True)

    # The absolute project path is baked in, so the bundle keeps working after
    # it is copied to ~/Applications or dragged to the Dock.
    script = macos_dir / APP_NAME
    script.write_text(
        "#!/bin/bash\n"
        f'PROJECT="{ROOT}"\n'
        f'PYTHON="{python}"\n'
        '# Prefer the project environment if one appears later.\n'
        'if [ -x "$PROJECT/.venv/bin/python" ]; then PYTHON="$PROJECT/.venv/bin/python"; fi\n'
        'if [ ! -x "$PYTHON" ]; then PYTHON="$(command -v python3)"; fi\n'
        '\n'
        '# On Apple Silicon, Launch Services may start this bundle under Rosetta.\n'
        '# Python would then run as x86_64 and fail to load native arm64 wheels\n'
        "# (numpy reports it as 'incompatible architecture'). Force native\n"
        '# execution, but only after checking this interpreter really has an\n'
        '# arm64 slice — some people deliberately run an Intel Python.\n'
        'ARCH_PREFIX=""\n'
        'if [ -x /usr/bin/arch ] && '
        '[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null)" = "1" ]; then\n'
        '  if /usr/bin/arch -arm64 "$PYTHON" -c "" >/dev/null 2>&1; then\n'
        '    ARCH_PREFIX="/usr/bin/arch -arm64"\n'
        '  fi\n'
        'fi\n'
        '\n'
        'exec $ARCH_PREFIX "$PYTHON" "$PROJECT/scripts/launcher.py"\n')
    script.chmod(0o755)

    icon = ASSETS / "icon.icns"
    if icon.exists():
        shutil.copy(icon, resources / "icon.icns")

    plist = {
        "CFBundleName": APP_NAME,
        "CFBundleDisplayName": APP_NAME,
        "CFBundleIdentifier": BUNDLE_ID,
        "CFBundleExecutable": APP_NAME,
        "CFBundleIconFile": "icon",
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1.0",
        "CFBundleInfoDictionaryVersion": "6.0",
        "NSHighResolutionCapable": True,
        "LSMinimumSystemVersion": "10.13",
        # Prefer the machine's own architecture. Without this, Launch Services
        # can start the bundle under Rosetta on Apple Silicon, and native wheels
        # then refuse to load.
        "LSArchitecturePriority": ["arm64", "x86_64"],
        "LSRequiresNativeExecution": True,
        # It opens a browser window; it has no windows of its own.
        "LSBackgroundOnly": False,
    }
    with (bundle / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(plist, handle)

    destination = applications or (Path.home() / "Applications")
    destination.mkdir(parents=True, exist_ok=True)
    installed = destination / bundle.name
    if installed.resolve() != bundle.resolve():
        if installed.exists():
            shutil.rmtree(installed)
        shutil.copytree(bundle, installed, symlinks=True)

    # Nudge Launch Services so the icon appears without a logout.
    subprocess.run(["touch", str(installed)], capture_output=True)
    return installed


# --- Windows -----------------------------------------------------------------

def install_windows(python: str) -> list[Path]:
    """Create .lnk shortcuts via PowerShell's WScript.Shell."""
    pythonw = Path(python).with_name("pythonw.exe")
    runner = str(pythonw if pythonw.exists() else python)

    icon = ASSETS / "icon.ico"
    desktop = Path(os.path.expandvars(r"%USERPROFILE%\Desktop"))
    start_menu = Path(os.path.expandvars(
        r"%APPDATA%\Microsoft\Windows\Start Menu\Programs"))

    created = []
    for folder in (desktop, start_menu):
        if not folder.exists():
            continue
        link = folder / f"{APP_NAME}.lnk"
        script = f"""
$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{link}')
$s.TargetPath = '{runner}'
$s.Arguments = '"{LAUNCHER}"'
$s.WorkingDirectory = '{ROOT}'
$s.Description = '{DESCRIPTION}'
{f"$s.IconLocation = '{icon}'" if icon.exists() else ""}
$s.Save()
"""
        result = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True)
        if result.returncode == 0 and link.exists():
            created.append(link)
        else:
            say(f"Could not create {link}: {result.stderr.strip()[:200]}")
    return created


# --- Linux -------------------------------------------------------------------

def install_linux(python: str) -> Path:
    icon = ASSETS / "icon.png"
    folder = Path.home() / ".local" / "share" / "applications"
    folder.mkdir(parents=True, exist_ok=True)
    entry = folder / f"{BUNDLE_ID}.desktop"
    entry.write_text(
        "[Desktop Entry]\nType=Application\n"
        f"Name={APP_NAME}\nComment={DESCRIPTION}\n"
        f"Exec={python} {LAUNCHER}\nPath={ROOT}\n"
        f"Icon={icon}\nTerminal=false\nCategories=Education;Science;\n")
    entry.chmod(0o755)
    subprocess.run(["update-desktop-database", str(folder)], capture_output=True)
    return entry


def main() -> int:
    print(f"\nSetting up {APP_NAME}\n" + "-" * 40)
    if not LAUNCHER.exists():
        print(f"Could not find {LAUNCHER}. Run this from inside the project folder.")
        return 1

    ensure_icons()
    try:
        python = ensure_dependencies()
    except subprocess.CalledProcessError as exc:
        print(f"\nSetup failed while installing dependencies:\n{exc}")
        print("Try running:  python -m pip install -r requirements.txt")
        return 1

    print()
    if sys.platform == "darwin":
        installed = install_macos(python)
        print(f"Installed: {installed}")
        print(f"\nOpen it from Launchpad or Spotlight by typing “{APP_NAME}”.")
        print("To keep it in the Dock: open it once, then right-click its Dock")
        print('icon and choose Options > Keep in Dock.')
    elif os.name == "nt":
        links = install_windows(python)
        if not links:
            print("No shortcuts could be created.")
            return 1
        for link in links:
            print(f"Installed: {link}")
        print(f"\nOpen it from the Desktop or the Start menu by typing “{APP_NAME}”.")
    else:
        print(f"Installed: {install_linux(python)}")
        print(f"\nLook for “{APP_NAME}” in your applications menu.")

    print("\nThe first launch takes a few seconds while the dashboard starts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
