"""The desktop shortcut path: launcher helpers and bundle generation.

These do not start a server — they cover the parts that decide *how* one gets
started, which is where the platform-specific traps live.
"""
from __future__ import annotations

import importlib.util
import plistlib
import socket
import sys
from pathlib import Path

import pytest

from tests.conftest import ROOT


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


launcher = _load("launcher")
installer = _load("install_desktop")


# --- launcher ----------------------------------------------------------------

def test_paths_resolve_from_the_script_not_the_working_directory():
    """A shortcut launches from '/', so nothing may depend on the caller's cwd."""
    assert launcher.ROOT == ROOT
    assert launcher.APP.exists()
    assert launcher.APP.name == "app.py"


def test_free_port_returns_something_bindable():
    port = launcher.free_port()
    assert 1024 < port < 65536
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", port))


def test_free_port_skips_a_port_already_in_use():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen(1)
        busy = taken.getsockname()[1]
        assert launcher.free_port(busy) != busy


def test_is_serving_is_false_for_a_dead_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert launcher.is_serving(port) is False


def test_the_running_interpreter_passes_the_dependency_check():
    assert launcher.can_run(sys.executable) is True


def test_a_nonexistent_interpreter_fails_the_check():
    assert launcher.can_run("/nonexistent/python") is False


# --- macOS bundle ------------------------------------------------------------

@pytest.mark.skipif(sys.platform != "darwin", reason="macOS bundle")
class TestMacBundle:
    @pytest.fixture(scope="class")
    def bundle(self, tmp_path_factory) -> Path:
        target = tmp_path_factory.mktemp("apps")
        return installer.install_macos(sys.executable, applications=target)

    def test_bundle_has_the_expected_shape(self, bundle):
        assert (bundle / "Contents" / "Info.plist").exists()
        assert (bundle / "Contents" / "MacOS" / installer.APP_NAME).exists()

    def test_executable_is_executable(self, bundle):
        script = bundle / "Contents" / "MacOS" / installer.APP_NAME
        assert script.stat().st_mode & 0o111

    def test_plist_is_valid_and_declares_native_execution(self, bundle):
        """Without these, Launch Services can start the app under Rosetta and
        native wheels fail to load."""
        with (bundle / "Contents" / "Info.plist").open("rb") as handle:
            plist = plistlib.load(handle)
        assert plist["CFBundlePackageType"] == "APPL"
        assert plist["CFBundleExecutable"] == installer.APP_NAME
        assert plist["LSRequiresNativeExecution"] is True
        assert plist["LSArchitecturePriority"][0] == "arm64"

    def test_launch_script_guards_against_rosetta(self, bundle):
        script = (bundle / "Contents" / "MacOS" / installer.APP_NAME).read_text()
        assert "hw.optional.arm64" in script
        assert "/usr/bin/arch -arm64" in script

    def test_launch_script_uses_absolute_paths(self, bundle):
        """The bundle keeps working after being moved to ~/Applications."""
        script = (bundle / "Contents" / "MacOS" / installer.APP_NAME).read_text()
        assert str(ROOT) in script
        assert "scripts/launcher.py" in script

    def test_launch_script_prefers_a_project_venv_when_one_appears(self, bundle):
        script = (bundle / "Contents" / "MacOS" / installer.APP_NAME).read_text()
        assert ".venv/bin/python" in script
