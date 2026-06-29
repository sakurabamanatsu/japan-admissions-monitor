#!/usr/bin/env python3
import json
import os
import smtplib
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path

import monitor_admissions as monitor


ROOT = Path(__file__).resolve().parent
STATE_FILE = ROOT / "cloud_state.json"
DATA_FILE = ROOT / "web" / "data.json"

REGIONS = {
    "东京大学": "关东",
    "早稻田大学": "关东",
    "庆应义塾大学": "关东",
    "東洋大学": "关东",
    "中央大学": "关东",
    "立教大学": "关东",
    "日本大学": "关东",
    "法政大学": "关东",
    "駒沢大学": "关东",
    "東海大学": "关东",
    "一橋大学": "关东",
    "青山学院大学": "关东",
    "専修大学": "关东",
    "京都大学": "关西",
    "大阪大学": "关西",
    "立命馆大学": "关西",
    "关西大学": "关西",
    "同志社大学": "关西",
    "龍谷大学": "关西",
    "名古屋大学": "中部",
    "东北大学": "东北",
    "北海道大学": "北海道",
    "九州大学": "九州",
    "APU立命馆亚洲太平洋大学": "九州",
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0)


def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "items": {}, "last_run": None}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_json(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def infer_category(title, matched):
    text = f"{title} {matched}".lower()
    if any(word in text for word in ("学部", "本科", "undergraduate")):
        return "学部"
    if any(word in text for word in ("大学院", "修士", "博士", "graduate")):
        return "大学院"
    if any(
        word in text
        for word in ("募集要項", "入試要項", "出願要項", "guidelines")
    ):
        return "募集要项"
    return "其他"


def is_relevant(title, matched):
    title_text = title.lower()
    combined = f"{title} {matched}".lower()
    foreign_terms = (
        "外国人",
        "外国学生",
        "外国学校",
        "留学生",
        "international student",
        "international applicant",
        "/international/",
        "ryugakusei",
    )
    admission_terms = (
        "募集要項",
        "入試要項",
        "入学試験要項",
        "出願要項",
        "選考",
        "入試",
        "入学案内",
        "application guideline",
        "admission guideline",
        "admission",
        "apply",
    )
    guideline_terms = (
        "募集要項",
        "入試要項",
        "入学試験要項",
        "出願要項",
        "application guideline",
        "admission guideline",
    )
    foreign_in_title = any(term in title_text for term in foreign_terms)
    admission_in_title = any(term in title_text for term in admission_terms)
    guideline_in_title = any(term in title_text for term in guideline_terms)
    foreign_anywhere = any(term in combined for term in foreign_terms)
    excluded = any(
        term in title_text
        for term in ("入試結果", "過去問題", "/result", "result.pdf", "archive")
    )
    if excluded and not guideline_in_title:
        return False
    return (foreign_in_title and admission_in_title) or (
        guideline_in_title and foreign_anywhere
    )


def send_email(new_items):
    host = os.environ.get("EMAIL_SMTP_HOST", "").strip() or "smtp.gmail.com"
    port_text = os.environ.get("EMAIL_SMTP_PORT", "").strip() or "465"
    user = os.environ.get("EMAIL_USER", "").strip()
    password = os.environ.get("EMAIL_PASSWORD", "").strip()
    recipient = os.environ.get("EMAIL_TO", "").strip()
    if not (host and user and password and recipient and new_items):
        print("邮件未配置或没有新内容，跳过发送。")
        return
    port = int(port_text)

    message = EmailMessage()
    message["Subject"] = f"发现 {len(new_items)} 条新的日本大学留学生入试信息"
    message["From"] = user
    message["To"] = recipient
    lines = ["发现新的外国人留学生募集要项相关内容：", ""]
    for item in new_items[:30]:
        lines.extend(
            [
                f"学校：{item['school']}",
                f"标题：{item['title']}",
                f"链接：{item['url']}",
                "",
            ]
        )
    message.set_content("\n".join(lines))
    with smtplib.SMTP_SSL(host, port, timeout=30) as smtp:
        smtp.login(user, password)
        smtp.send_message(message)
    print("新内容通知邮件已发送。")


def main():
    state = load_state()
    initialized = bool(state.get("initialized"))
    known_items = state.setdefault("items", {})
    schools = monitor.load_schools()
    keywords = monitor.load_keywords()
    checked_at = utc_now()
    errors = []
    new_items = []

    print(f"开始云端检查 {len(schools)} 所学校。")
    for school in schools:
        print(f"- {school['name']}", flush=True)
        items, school_errors = monitor.collect_school(school, keywords)
        errors.extend(f"{school['name']}: {error}" for error in school_errors)
        school_was_known = any(
            row.get("school") == school["name"] for row in known_items.values()
        )
        for item in items:
            if not is_relevant(item["title"], item["matched"]):
                continue
            key = monitor.item_key(item["school"], item["url"])
            old = known_items.get(key)
            first_seen = old.get("first_seen_at") if old else checked_at.isoformat()
            changed = old is not None and old.get("title") != item["title"]
            newly_detected = (
                initialized and school_was_known and (old is None or changed)
            )
            record = {
                "school": item["school"],
                "region": REGIONS.get(item["school"], "其他"),
                "category": infer_category(item["title"], item["matched"]),
                "title": item["title"],
                "url": item["url"],
                "matched": item["matched"],
                "source": item["source"],
                "is_pdf": item["url"].lower().split("?", 1)[0].endswith(".pdf"),
                "first_seen_at": first_seen,
                "last_seen_at": checked_at.isoformat(),
                "is_new_until": (
                    (checked_at + timedelta(days=7)).isoformat()
                    if newly_detected
                    else old.get("is_new_until", "") if old else ""
                ),
            }
            if newly_detected:
                new_items.append(record)
            known_items[key] = record

    state["initialized"] = True
    state["last_run"] = {
        "checked_at": checked_at.isoformat(),
        "schools": len(schools),
        "results": len(known_items),
        "errors": len(errors),
    }
    save_json(STATE_FILE, state)

    rows = []
    for key, item in known_items.items():
        row = {"id": key, **item}
        try:
            new_until = datetime.fromisoformat(item.get("is_new_until", ""))
        except (TypeError, ValueError):
            new_until = checked_at - timedelta(days=1)
        row["is_new"] = new_until >= checked_at
        rows.append(row)
    rows.sort(key=lambda row: row["first_seen_at"], reverse=True)

    unique_schools = {school["name"] for school in schools}
    payload = {
        "generated_at": checked_at.isoformat(),
        "interval_minutes": 15,
        "last_error": f"{len(errors)} 个网页访问失败" if errors else "",
        "counts": {
            "total": len(rows),
            "new_count": sum(bool(row["is_new"]) for row in rows),
            "pdf_count": sum(bool(row["is_pdf"]) for row in rows),
            "school_count": len(unique_schools),
        },
        "schools": [
            {
                "name": school["name"],
                "url": school["url"],
                "region": REGIONS.get(school["name"], "其他"),
                "active": True,
            }
            for school in schools
        ],
        "items": rows,
    }
    save_json(DATA_FILE, payload)
    send_email(new_items)
    print(
        f"完成：展示 {len(rows)} 条，新增 {len(new_items)} 条，"
        f"错误 {len(errors)} 个。"
    )


if __name__ == "__main__":
    main()
