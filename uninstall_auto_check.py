#!/usr/bin/env python3
import subprocess
from pathlib import Path


PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / "com.admissions.monitor.plist"


def main():
    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    if PLIST_PATH.exists():
        PLIST_PATH.unlink()
    print("自动检查已关闭。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
