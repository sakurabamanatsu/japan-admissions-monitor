#!/usr/bin/env python3
import csv
import hashlib
import heapq
import html
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlparse
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent
SCHOOLS_CSV = ROOT / "schools.csv"
KEYWORDS_FILE = ROOT / "keywords.txt"
STATE_DIR = ROOT / "state"
REPORT_DIR = ROOT / "reports"
STATE_FILE = STATE_DIR / "seen_items.json"
EMAIL_CONFIG_FILE = ROOT / "email_config.txt"
TARGET_ADMISSION_YEAR = int(os.environ.get("TARGET_ADMISSION_YEAR", "2027"))

DEFAULT_KEYWORDS = [
    "外国人留学生",
    "外国学生",
    "私費外国人留学生",
    "留学生入試",
    "外国人留学生入試",
    "外国学校卒業",
    "外国人留学生大学院入試",
    "大学院入試",
    "研究科",
    "修士",
    "博士",
    "募集要項",
    "入試要項",
    "入学試験要項",
    "出願要項",
    "入学者選抜要項",
    "application guidelines",
    "admission guidelines",
    "international students",
    "international student",
    "international admission",
    "international graduate",
    "graduate admission",
    "graduate school",
    "master's",
    "doctoral",
    "ryugakusei",
]

CRAWL_HINTS = [
    "入試",
    "入学",
    "受験",
    "募集",
    "要項",
    "留学生",
    "外国人",
    "大学院",
    "研究科",
    "修士",
    "博士",
    "admission",
    "applicant",
    "international",
    "graduate",
    "master",
    "doctoral",
]

SCHOLARSHIP_EXCLUSION_TERMS = (
    "奨学金",
    "奨学生",
    "国費",
    "給付金",
    "授業料免除",
    "授業料減免",
    "入学料免除",
    "入学料減免",
    "学費支援",
    "経済支援",
    "scholarship",
    "tuition waiver",
    "tuition exemption",
    "jasso",
    "mext",
)


def is_scholarship_related(text):
    lower = text.lower()
    return any(term in lower for term in SCHOLARSHIP_EXCLUSION_TERMS)


def is_target_admission_year(text, target_year=TARGET_ADMISSION_YEAR):
    normalized = unicodedata.normalize("NFKC", text).lower()
    reiwa_year = target_year - 2018
    patterns = (
        rf"(?<!\d){target_year}(?!\d)",
        rf"令和\s*0?{reiwa_year}(?!\d)",
        rf"(?<![a-z0-9])r0?{reiwa_year}(?!\d)",
    )
    return any(re.search(pattern, normalized) for pattern in patterns)


class LinkParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.text_parts = []
        self.links = []
        self._in_title = False
        self._current_link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag.lower() == "title":
            self._in_title = True
        if tag.lower() == "a" and attrs.get("href"):
            self._current_link = {"href": attrs["href"], "text": ""}

    def handle_endtag(self, tag):
        if tag.lower() == "title":
            self._in_title = False
        if tag.lower() == "a" and self._current_link:
            self.links.append(self._current_link)
            self._current_link = None

    def handle_data(self, data):
        data = data.strip()
        if not data:
            return
        if self._in_title:
            self.title += data + " "
        if self._current_link is not None:
            self._current_link["text"] += data + " "
        self.text_parts.append(data)


def load_keywords():
    if not KEYWORDS_FILE.exists():
        return DEFAULT_KEYWORDS
    keywords = []
    for line in KEYWORDS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keywords.append(line)
    return keywords or DEFAULT_KEYWORDS


def clean_text(value):
    value = html.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def fetch_with_curl(url, timeout=15):
    marker_url = b"\n__ADMISSIONS_MONITOR_FINAL_URL__"
    marker_type = b"\n__ADMISSIONS_MONITOR_CONTENT_TYPE__"
    result = subprocess.run(
        [
            "curl",
            "-L",
            "--max-time",
            str(timeout),
            "-A",
            "Mozilla/5.0 admissions-monitor/1.0",
            "-sS",
            "-w",
            "\n__ADMISSIONS_MONITOR_FINAL_URL__%{url_effective}"
            "\n__ADMISSIONS_MONITOR_CONTENT_TYPE__%{content_type}",
            url,
        ],
        capture_output=True,
    )
    if result.returncode != 0:
        raise URLError(result.stderr.decode("utf-8", errors="replace").strip())
    body, _, tail = result.stdout.partition(marker_url)
    final_url, _, content_tail = tail.partition(marker_type)
    content_type = content_tail.decode("utf-8", errors="replace").strip()
    return final_url.decode("utf-8", errors="replace").strip() or url, content_type, body


def fetch(url, timeout=12):
    req = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 admissions-monitor/1.0 "
                "(checks public university admission pages)"
            )
        },
    )
    try:
        with urlopen(req, timeout=timeout) as response:
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
            raw = response.read(2_000_000)
        return final_url, content_type, raw
    except Exception:
        return fetch_with_curl(url)


def decode_html(raw):
    for encoding in ("utf-8", "cp932", "shift_jis", "euc_jp"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def parse_html(raw):
    parser = LinkParser()
    parser.feed(decode_html(raw))
    return {
        "title": clean_text(parser.title),
        "text": clean_text(" ".join(parser.text_parts)),
        "links": parser.links,
    }


def same_site(url_a, url_b):
    host_a = urlparse(url_a).netloc.lower().removeprefix("www.")
    host_b = urlparse(url_b).netloc.lower().removeprefix("www.")
    return host_a == host_b


def usable_url(url):
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def normalize_url(url):
    return urldefrag(url)[0]


def keyword_hits(text, keywords):
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def should_follow(text):
    lower = text.lower()
    return any(hint.lower() in lower for hint in CRAWL_HINTS)


def link_priority(text, keywords):
    lower = text.lower()
    score = 0
    strong_hints = (
        "外国人",
        "外国学生",
        "留学生",
        "international student",
        "international applicant",
        "international/",
        "大学院",
        "研究科",
        "graduate school",
        "graduate admission",
    )
    guideline_hints = (
        "募集要項",
        "入試要項",
        "入学試験要項",
        "出願要項",
        "application guideline",
        "admission guideline",
        "guidelines",
    )
    score += sum(100 for hint in strong_hints if hint in lower)
    score += sum(40 for hint in guideline_hints if hint in lower)
    score += sum(15 for keyword in keywords if keyword.lower() in lower)
    score += sum(5 for hint in CRAWL_HINTS if hint.lower() in lower)
    if is_target_admission_year(text):
        score += 200
    if re.search(r"20\d{2}", lower):
        score += 10
    if any(
        hint in lower
        for hint in (
            "イベント",
            "open.?campus",
            "オープンキャンパス",
            "contact",
            "お問い合わせ",
            "archive",
            "過去問題",
            "result",
            "入試結果",
        )
    ):
        score -= 20
    if is_scholarship_related(text):
        score -= 200
    return score


def pdf_item(school, url, source_text, keywords):
    combined = f"{source_text} {url}"
    hits = keyword_hits(combined, keywords)
    if not hits:
        return None
    title = clean_text(source_text) or "PDF"
    if title.startswith("学校设置的网址"):
        title = f"{school['name']} 外国人留学生入試募集要項（PDF）"
    if "ryugakusei-youkou" in url.lower():
        year_match = re.search(r"/(20\d{2})/", url)
        year = f"{year_match.group(1)}年度 " if year_match else ""
        title = f"{year}{school['name']} 外国人留学生入学試験要項（PDF）"
    return {
        "school": school["name"],
        "title": title,
        "url": url,
        "matched": " / ".join(hits),
        "source": url,
    }


def item_key(school, url):
    return hashlib.sha256(f"{school}\n{url}".encode("utf-8")).hexdigest()


def item_fingerprint(title, url, content_hash=""):
    return hashlib.sha256(
        f"{title}\n{url}\n{content_hash}".encode("utf-8")
    ).hexdigest()


def load_schools():
    if not SCHOOLS_CSV.exists():
        print(f"找不到 {SCHOOLS_CSV}")
        sys.exit(1)
    schools = []
    with SCHOOLS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            enabled = clean_text(row.get("enabled", "yes")).lower()
            url = clean_text(row.get("url", ""))
            name = clean_text(row.get("name", ""))
            if enabled in ("no", "n", "false", "0", "不要", "否"):
                continue
            if not name or not usable_url(url):
                continue
            ownership = clean_text(row.get("ownership", ""))
            if ownership not in ("国立", "公立", "私立"):
                ownership = "未分类"
            region = clean_text(row.get("region", "")) or "其他"
            batch = clean_text(row.get("batch", ""))
            notes = clean_text(row.get("notes", ""))
            schools.append(
                {
                    "name": name,
                    "ownership": ownership,
                    "region": region,
                    "batch": batch,
                    "notes": notes,
                    "url": url,
                }
            )
    return schools


def load_state():
    if not STATE_FILE.exists():
        return {"items": {}, "runs": []}
    return json.loads(STATE_FILE.read_text(encoding="utf-8"))


def save_state(state):
    STATE_DIR.mkdir(exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def load_email_to():
    if not EMAIL_CONFIG_FILE.exists():
        return ""
    for line in EMAIL_CONFIG_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() == "to":
                return value.strip()
        elif "@" in line:
            return line
    return ""


def send_email_notification(to_address, new_items, report_path):
    if not to_address or not new_items:
        return False, "没有需要发送的邮件。"

    lines = [
        "发现新的日本大学留学生入试/募集要项相关内容：",
        "",
    ]
    for item in new_items[:30]:
        lines.extend(
            [
                f"学校：{item['school']}",
                f"标题：{item['title']}",
                f"链接：{item['url']}",
                f"关键词：{item['matched']}",
                "",
            ]
        )
    if len(new_items) > 30:
        lines.append(f"还有 {len(new_items) - 30} 条，请打开报告查看。")
        lines.append("")
    lines.extend(["完整报告：", str(report_path)])
    body = "\n".join(lines)
    subject = f"发现 {len(new_items)} 条新的日本大学留学生入试信息"

    script_lines = [
        "on run argv",
        "set recipientAddress to item 1 of argv",
        "set messageSubject to item 2 of argv",
        "set messageBody to item 3 of argv",
        'tell application "Mail"',
        "set newMessage to make new outgoing message with properties {subject:messageSubject, content:messageBody, visible:false}",
        "tell newMessage",
        "make new to recipient at end of to recipients with properties {address:recipientAddress}",
        "send",
        "end tell",
        "end tell",
        "end run",
    ]
    command = ["osascript"]
    for line in script_lines:
        command.extend(["-e", line])
    try:
        result = subprocess.run(
            [*command, to_address, subject, body],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception as exc:
        return False, f"邮件发送失败：{exc}"
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        return False, f"邮件发送失败：{detail}"
    return True, "邮件已发送。"


def collect_school(
    school, keywords, max_follow=40, max_pages=15, max_depth=3
):
    found = []
    errors = []
    visited = set()
    queued = {school["url"]}
    source_note = clean_text(
        f"学校设置的网址 {school.get('notes', '')}"
    )
    queue = [(0, 0, school["url"], 0, source_note)]
    sequence = 0

    while queue and len(visited) < max_pages:
        _, _, url, depth, source_text = heapq.heappop(queue)
        if url in visited:
            continue
        visited.add(url)
        try:
            final_url, content_type, raw = fetch(url)
            final_url = normalize_url(final_url)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            errors.append(f"{url} -> {exc}")
            continue

        lower_url = final_url.lower()
        if "pdf" in content_type.lower() or lower_url.endswith(".pdf"):
            item = pdf_item(school, final_url, source_text, keywords)
            if item:
                item["source"] = url
                item["content_hash"] = hashlib.sha256(raw).hexdigest()
                found.append(item)
            continue

        if "html" not in content_type.lower() and raw[:100].lower().find(b"<html") == -1:
            continue

        page = parse_html(raw)
        page_text = f'{page["title"]} {final_url} {page["text"][:20000]}'
        hits = keyword_hits(page_text, keywords)
        if hits:
            found.append(
                {
                    "school": school["name"],
                    "title": page["title"] or final_url,
                    "url": final_url,
                    "matched": " / ".join(hits[:6]),
                    "source": url,
                }
            )

        if depth >= max_depth:
            continue

        candidates = []
        for link in page["links"]:
            href = (link.get("href") or "").strip()
            if href.startswith(("#", "mailto:", "tel:", "javascript:")):
                continue
            next_url = normalize_url(urljoin(final_url, href))
            if not usable_url(next_url) or not same_site(final_url, next_url):
                continue
            link_text = clean_text(f'{link.get("text", "")} {next_url}')
            if is_scholarship_related(link_text):
                continue
            if urlparse(next_url).path.lower().endswith(".pdf"):
                item = pdf_item(school, next_url, link_text, keywords)
                if item:
                    item["source"] = final_url
                    found.append(item)
                continue
            if keyword_hits(link_text, keywords) or should_follow(link_text):
                candidates.append(
                    (link_priority(link_text, keywords), next_url, link_text)
                )

        candidates.sort(key=lambda candidate: candidate[0], reverse=True)
        followed = 0
        for priority, next_url, link_text in candidates:
            if next_url in visited or next_url in queued:
                continue
            sequence += 1
            heapq.heappush(
                queue,
                (-priority, sequence, next_url, depth + 1, link_text),
            )
            queued.add(next_url)
            followed += 1
            if followed >= max_follow:
                break

        time.sleep(0.2)

    unique = {}
    for item in found:
        unique[item["url"]] = item
    return list(unique.values()), errors


def write_reports(results, errors, first_run):
    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = REPORT_DIR / f"report_{stamp}.csv"
    html_path = REPORT_DIR / f"report_{stamp}.html"

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "status",
                "school",
                "title",
                "url",
                "matched",
                "source",
                "checked_at",
            ],
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(results)

    rows = []
    for item in results:
        status = "新发现" if item["status"] == "NEW" else "已记录"
        if first_run:
            status = "基准记录"
        rows.append(
            "<tr>"
            f"<td>{html.escape(status)}</td>"
            f"<td>{html.escape(item['school'])}</td>"
            f"<td>{html.escape(item['title'])}</td>"
            f"<td><a href=\"{html.escape(item['url'])}\">{html.escape(item['url'])}</a></td>"
            f"<td>{html.escape(item['matched'])}</td>"
            "</tr>"
        )
    error_html = "".join(f"<li>{html.escape(e)}</li>" for e in errors)
    html_doc = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <title>日本大学留学生募集要项监视报告</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #222; }}
    table {{ border-collapse: collapse; width: 100%; margin-top: 16px; }}
    th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; }}
    th {{ background: #f4f6f8; text-align: left; }}
    a {{ color: #0756a5; }}
    .note {{ color: #555; }}
  </style>
</head>
<body>
  <h1>日本大学留学生募集要项监视报告</h1>
  <p class="note">生成时间：{datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
  <table>
    <thead><tr><th>状态</th><th>学校</th><th>标题</th><th>链接</th><th>命中的关键词</th></tr></thead>
    <tbody>{''.join(rows) if rows else '<tr><td colspan="5">没有找到匹配内容。</td></tr>'}</tbody>
  </table>
  <h2>访问失败的网址</h2>
  <ul>{error_html if error_html else '<li>无</li>'}</ul>
</body>
</html>
"""
    html_path.write_text(html_doc, encoding="utf-8")
    return csv_path, html_path


def main():
    schools = load_schools()
    if not schools:
        print("schools.csv 里没有可检查的学校。请填写 name 和 url，并把 enabled 写成 yes。")
        return 2

    keywords = load_keywords()
    state = load_state()
    first_run = not state.get("items")
    all_results = []
    all_errors = []
    now = datetime.now().isoformat(timespec="seconds")

    print(f"开始检查 {len(schools)} 所学校。", flush=True)
    for school in schools:
        print(f"- {school['name']}", flush=True)
        items, errors = collect_school(school, keywords)
        all_errors.extend([f"{school['name']}: {e}" for e in errors])
        for item in items:
            key = item_key(item["school"], item["url"])
            fingerprint = item_fingerprint(
                item["title"],
                item["url"],
                item.get("content_hash", ""),
            )
            old = state["items"].get(key)
            is_new = (not first_run) and (
                old is None or old.get("fingerprint") != fingerprint
            )
            item["status"] = "NEW" if is_new else "SEEN"
            item["checked_at"] = now
            state["items"][key] = {
                "school": item["school"],
                "title": item["title"],
                "url": item["url"],
                "fingerprint": fingerprint,
                "first_seen_at": old.get("first_seen_at") if old else now,
                "last_seen_at": now,
            }
            all_results.append(item)

    state.setdefault("runs", []).append(
        {"checked_at": now, "schools": len(schools), "results": len(all_results)}
    )
    state["runs"] = state["runs"][-50:]
    save_state(state)

    csv_path, html_path = write_reports(all_results, all_errors, first_run)
    new_count = sum(1 for item in all_results if item["status"] == "NEW")
    new_items = [item for item in all_results if item["status"] == "NEW"]

    print("")
    if first_run:
        print("第一次运行完成：已经建立基准。之后再运行时，新出现的内容会显示为【新发现】。")
    else:
        print(f"本次新发现：{new_count} 条。")
        email_to = load_email_to()
        if email_to and new_items:
            sent, message = send_email_notification(email_to, new_items, html_path)
            print(message)
        elif new_items:
            print("还没有设置收件邮箱，所以没有发送邮件。请编辑 email_config.txt。")
    print(f"报告 HTML：{html_path}")
    print(f"报告 CSV：{csv_path}")
    if all_errors:
        print(f"有 {len(all_errors)} 个网址访问失败，详情在报告底部。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
