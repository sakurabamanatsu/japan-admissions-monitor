#!/usr/bin/env python3
import os
import plistlib
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PLIST_NAME = "com.admissions.monitor.plist"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / PLIST_NAME
LOG_DIR = ROOT / "logs"


def ask_interval():
    print("自动检查间隔：")
    print("  1. 每 30 分钟检查一次（推荐）")
    print("  2. 每 60 分钟检查一次")
    print("  3. 每 15 分钟检查一次")
    value = input("输入数字：").strip()
    if value == "2":
        return 3600
    if value == "3":
        return 900
    return 1800


def install(interval_seconds):
    LOG_DIR.mkdir(exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": "com.admissions.monitor",
        "ProgramArguments": [
            "/usr/bin/python3",
            str(ROOT / "monitor_admissions.py"),
        ],
        "WorkingDirectory": str(ROOT),
        "StartInterval": interval_seconds,
        "RunAtLoad": True,
        "StandardOutPath": str(LOG_DIR / "auto_check.log"),
        "StandardErrorPath": str(LOG_DIR / "auto_check_error.log"),
    }
    with PLIST_PATH.open("wb") as f:
        plistlib.dump(plist, f)

    subprocess.run(["launchctl", "unload", str(PLIST_PATH)], capture_output=True)
    result = subprocess.run(["launchctl", "load", str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        print("自动检查设置文件已写入，但启动失败：")
        print(result.stderr.strip() or result.stdout.strip())
        return 1
    print("")
    print("自动检查已开启。")
    print(f"间隔：每 {interval_seconds // 60} 分钟")
    print(f"日志：{LOG_DIR / 'auto_check.log'}")
    return 0


def main():
    interval = ask_interval()
    return install(interval)


if __name__ == "__main__":
    raise SystemExit(main())
