"""The optional daily Mac job runs independently of the dashboard."""

import fcntl
import os
import plistlib
import subprocess
import sys
from pathlib import Path

from storage import ROOT, runtime_dir

LABEL = "local.sitescout.capstone.daily"


def _location():
    return Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"


def autopilot_status():
    with (runtime_dir() / "batch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            running = False
        except BlockingIOError:
            running = True
    return {"enabled": _location().is_file(), "limit": 10, "running": running}


def set_autopilot(enabled):
    path = _location()
    domain = f"gui/{os.getuid()}"
    if not enabled:
        if path.exists():
            job = f"{domain}/{LABEL}"
            loaded = subprocess.run(["launchctl", "print", job], capture_output=True)
            if loaded.returncode == 0:
                result = subprocess.run(["launchctl", "bootout", job], capture_output=True, text=True)
                if result.returncode:
                    raise RuntimeError("macOS could not stop the daily job: " + result.stderr.strip())
            path.unlink(missing_ok=True)
        return
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    log = str(runtime_dir() / "autopilot.log")
    settings = {
        "Label": LABEL,
        "ProgramArguments": [sys.executable, str(ROOT / "pipeline.py"), "--daily"],
        "WorkingDirectory": str(ROOT),
        "StartCalendarInterval": {"Hour": 9, "Minute": 0},
        "EnvironmentVariables": {"PATH": os.environ["PATH"]},
        "StandardOutPath": log,
        "StandardErrorPath": log,
    }
    path.write_bytes(plistlib.dumps(settings))
    result = subprocess.run(
        ["launchctl", "bootstrap", domain, str(path)], capture_output=True, text=True
    )
    if result.returncode:
        path.unlink(missing_ok=True)
        raise RuntimeError("macOS could not register the daily job: " + result.stderr.strip())
