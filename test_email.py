#!/usr/bin/env python3
from datetime import datetime
from pathlib import Path

from monitor_admissions import load_email_to, send_email_notification


def main():
    to_address = load_email_to()
    if not to_address or to_address == "you@example.com":
        print("还没有设置收件邮箱。")
        print("请先打开 email_config.txt，把 to=you@example.com 改成你的邮箱。")
        return 1

    test_item = {
        "school": "测试学校",
        "title": "这是一封测试邮件",
        "url": "https://example.com/",
        "matched": "测试",
    }
    report_path = Path(__file__).resolve().parent / "reports"
    sent, message = send_email_notification(to_address, [test_item], report_path)
    print(message)
    if sent:
        print(f"收件邮箱：{to_address}")
        print(f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("如果收件箱没有看到，请检查垃圾邮件箱，或确认 Mac 自带 Mail app 能正常发送邮件。")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
