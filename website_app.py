#!/usr/bin/env python3
import csv
import json
import sqlite3
import threading
import time
from datetime import datetime
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import monitor_admissions as monitor


ROOT = Path(__file__).resolve().parent
WEB_DIR = ROOT / "web"
DB_FILE = ROOT / "state" / "website.db"
HOST = "127.0.0.1"
PORT = 8765
CHECK_INTERVAL_SECONDS = 30 * 60

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

scan_lock = threading.Lock()
runtime = {
    "scanning": False,
    "current_school": "",
    "last_started_at": "",
    "last_finished_at": "",
    "last_error": "",
    "next_check_at": "",
}


def db_connection():
    DB_FILE.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_database():
    with db_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                school TEXT NOT NULL,
                region TEXT NOT NULL,
                ownership TEXT NOT NULL DEFAULT '未分类',
                category TEXT NOT NULL,
                title TEXT NOT NULL,
                url TEXT NOT NULL UNIQUE,
                matched TEXT NOT NULL,
                source TEXT NOT NULL,
                is_pdf INTEGER NOT NULL DEFAULT 0,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_new INTEGER NOT NULL DEFAULT 0,
                pdf_year_status TEXT NOT NULL DEFAULT '',
                relevance INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_items_school ON items(school);
            CREATE INDEX IF NOT EXISTS idx_items_seen ON items(first_seen_at DESC);
            CREATE TABLE IF NOT EXISTS runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                schools INTEGER NOT NULL DEFAULT 0,
                results INTEGER NOT NULL DEFAULT 0,
                errors INTEGER NOT NULL DEFAULT 0
            );
            """
        )
        columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(items)").fetchall()
        }
        if "relevance" not in columns:
            conn.execute(
                "ALTER TABLE items ADD COLUMN relevance INTEGER NOT NULL DEFAULT 0"
            )
        if "ownership" not in columns:
            conn.execute(
                "ALTER TABLE items ADD COLUMN ownership TEXT NOT NULL DEFAULT '未分类'"
            )
        if "pdf_year_status" not in columns:
            conn.execute(
                "ALTER TABLE items ADD COLUMN pdf_year_status TEXT NOT NULL DEFAULT ''"
            )
    import_existing_state()
    import_latest_report()
    refresh_item_classification()


def infer_category(title, matched):
    title_text = title.lower()
    text = f"{title} {matched}".lower()
    if any(
        word in title_text for word in ("学部", "本科", "undergraduate")
    ):
        return "学部"
    if any(
        word in title_text
        for word in ("大学院", "研究科", "修士", "博士", "graduate")
    ):
        return "大学院"
    if any(
        word in text
        for word in (
            "募集要項",
            "入試要項",
            "入学試験要項",
            "出願要項",
            "guidelines",
        )
    ):
        return "募集要项"
    return "其他"


def infer_relevance(title, matched, pdf_year_status=""):
    title_text = title.lower()
    text = f"{title} {matched}".lower()
    if monitor.is_scholarship_related(title):
        return 0
    if monitor.is_non_admission_recruitment(title):
        return 0
    if pdf_year_status in ("not_target", "excluded"):
        return 0
    if (
        pdf_year_status != "target"
        and not monitor.is_target_admission_year(title)
    ):
        return 0
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
    foreign_anywhere = any(term in text for term in foreign_terms)
    graduate_in_title = any(
        term in title_text
        for term in (
            "大学院",
            "研究科",
            "修士",
            "博士",
            "graduate school",
            "master",
            "doctoral",
        )
    )
    excluded = any(
        term in title_text
        for term in (
            "入試結果",
            "入試問題",
            "過去問題",
            "合格発表",
            "合格者",
            "受験番号",
            "受入れの方針",
            "アドミッション・ポリシー",
            "admission policy",
            "/result",
            "result.pdf",
            "archive",
        )
    )
    if excluded and not guideline_in_title:
        return 0
    if (foreign_in_title and admission_in_title) or (
        guideline_in_title and foreign_anywhere
    ) or (
        graduate_in_title
        and foreign_anywhere
        and (admission_in_title or guideline_in_title)
    ):
        return 3
    if foreign_anywhere or any(term in text for term in admission_terms):
        return 1
    return 0


def ownership_for_school(name):
    with monitor.SCHOOLS_CSV.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        for row in csv.DictReader(file):
            if row.get("name") == name:
                ownership = row.get("ownership", "")
                if ownership in ("国立", "公立", "私立"):
                    return ownership
    return "未分类"


def region_for_school(name):
    with monitor.SCHOOLS_CSV.open(
        "r", encoding="utf-8-sig", newline=""
    ) as file:
        for row in csv.DictReader(file):
            if row.get("name") == name:
                return row.get("region", "") or "其他"
    return REGIONS.get(name, "其他")


def import_existing_state():
    state = monitor.load_state()
    rows = state.get("items", {}).values()
    with db_connection() as conn:
        for row in rows:
            title = row.get("title", "")
            url = row.get("url", "")
            school = row.get("school", "")
            if not title or not url or not school:
                continue
            is_pdf = int(urlparse(url).path.lower().endswith(".pdf"))
            conn.execute(
                """
                INSERT OR IGNORE INTO items
                (school, region, ownership, category, title, url, matched,
                 source, is_pdf, first_seen_at, last_seen_at, is_new,
                 pdf_year_status, relevance)
                VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    school,
                    region_for_school(school),
                    ownership_for_school(school),
                    infer_category(title, ""),
                    title,
                    url,
                    url,
                    is_pdf,
                    row.get("first_seen_at", ""),
                    row.get("last_seen_at", ""),
                    row.get("pdf_year_status", ""),
                    infer_relevance(
                        title,
                        "",
                        row.get("pdf_year_status", ""),
                    ),
                ),
            )


def import_latest_report():
    reports = sorted(monitor.REPORT_DIR.glob("report_*.csv"), reverse=True)
    if not reports:
        return
    with reports[0].open("r", encoding="utf-8-sig", newline="") as file:
        rows = list(csv.DictReader(file))
    with db_connection() as conn:
        for row in rows:
            conn.execute(
                """
                UPDATE items SET matched=?, source=?, category=?, relevance=?
                WHERE url=?
                """,
                (
                    row.get("matched", ""),
                    row.get("source", ""),
                    infer_category(row.get("title", ""), row.get("matched", "")),
                    infer_relevance(row.get("title", ""), row.get("matched", "")),
                    row.get("url", ""),
                ),
            )


def refresh_item_classification():
    with db_connection() as conn:
        rows = conn.execute(
            "SELECT id, school, title, matched, pdf_year_status FROM items"
        ).fetchall()
        for row in rows:
            conn.execute(
                """
                UPDATE items SET region=?, ownership=?, category=?, relevance=?
                WHERE id=?
                """,
                (
                    region_for_school(row["school"]),
                    ownership_for_school(row["school"]),
                    infer_category(row["title"], row["matched"]),
                    infer_relevance(
                        row["title"],
                        row["matched"],
                        row["pdf_year_status"],
                    ),
                    row["id"],
                ),
            )


def save_items(items, checked_at, baseline):
    new_items = []
    with db_connection() as conn:
        known_schools = {
            row["school"]
            for row in conn.execute("SELECT DISTINCT school FROM items").fetchall()
        }
        for item in items:
            existing = conn.execute(
                "SELECT id, title FROM items WHERE url = ?", (item["url"],)
            ).fetchone()
            changed = existing is not None and existing["title"] != item["title"]
            is_new = int(
                not baseline
                and item["school"] in known_schools
                and (existing is None or changed)
            )
            values = (
                item["school"],
                region_for_school(item["school"]),
                ownership_for_school(item["school"]),
                infer_category(item["title"], item["matched"]),
                item["title"],
                item["url"],
                item["matched"],
                item["source"],
                int(urlparse(item["url"]).path.lower().endswith(".pdf")),
                item.get("pdf_year_status", ""),
                checked_at,
                is_new,
                infer_relevance(
                    item["title"],
                    item["matched"],
                    item.get("pdf_year_status", ""),
                ),
            )
            if existing is None:
                conn.execute(
                    """
                    INSERT INTO items
                    (school, region, ownership, category, title, url, matched,
                     source, is_pdf, pdf_year_status, first_seen_at,
                     last_seen_at, is_new, relevance)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        values[0],
                        values[1],
                        values[2],
                        values[3],
                        values[4],
                        values[5],
                        values[6],
                        values[7],
                        values[8],
                        values[9],
                        checked_at,
                        checked_at,
                        values[11],
                        values[12],
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE items SET school=?, region=?, ownership=?, category=?,
                    title=?, matched=?, source=?, is_pdf=?,
                    pdf_year_status=?, last_seen_at=?, is_new=?, relevance=?
                    WHERE url=?
                    """,
                    (
                        values[0],
                        values[1],
                        values[2],
                        values[3],
                        values[4],
                        values[6],
                        values[7],
                        values[8],
                        values[9],
                        values[10],
                        values[11],
                        values[12],
                        values[5],
                    ),
                )
            if is_new:
                new_items.append(item)
    return new_items


def scan_all():
    if not scan_lock.acquire(blocking=False):
        return False
    runtime.update(
        {
            "scanning": True,
            "current_school": "",
            "last_started_at": datetime.now().isoformat(timespec="seconds"),
            "last_error": "",
        }
    )
    started_at = runtime["last_started_at"]
    schools = []
    all_items = []
    all_errors = []
    try:
        schools = monitor.load_schools()
        keywords = monitor.load_keywords()
        with db_connection() as conn:
            baseline = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0] == 0
            run_id = conn.execute(
                "INSERT INTO runs(started_at, schools) VALUES (?, ?)",
                (started_at, len(schools)),
            ).lastrowid

        for school in schools:
            runtime["current_school"] = school["name"]
            items, errors = monitor.collect_school(school, keywords)
            all_items.extend(items)
            all_errors.extend(f"{school['name']}: {error}" for error in errors)

        checked_at = datetime.now().isoformat(timespec="seconds")
        new_items = save_items(all_items, checked_at, baseline)
        if new_items:
            report_path = ROOT / "reports"
            monitor.send_email_notification(
                monitor.load_email_to(), new_items, report_path
            )
        with db_connection() as conn:
            conn.execute(
                """
                UPDATE runs SET finished_at=?, results=?, errors=? WHERE id=?
                """,
                (checked_at, len(all_items), len(all_errors), run_id),
            )
        runtime["last_finished_at"] = checked_at
        if all_errors:
            runtime["last_error"] = f"{len(all_errors)} 个网页访问失败"
    except Exception as exc:
        runtime["last_error"] = str(exc)
    finally:
        runtime["scanning"] = False
        runtime["current_school"] = ""
        runtime["next_check_at"] = datetime.fromtimestamp(
            time.time() + CHECK_INTERVAL_SECONDS
        ).isoformat(timespec="seconds")
        scan_lock.release()
    return True


def scheduler():
    while True:
        scan_all()
        time.sleep(CHECK_INTERVAL_SECONDS)


def read_school_rows():
    with monitor.SCHOOLS_CSV.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def write_school_rows(rows):
    fields = [
        "enabled",
        "name",
        "ownership",
        "region",
        "batch",
        "url",
        "notes",
    ]
    with monitor.SCHOOLS_CSV.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=fields,
            extrasaction="ignore",
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


class WebsiteHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_DIR), **kwargs)

    def log_message(self, format, *args):
        return

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        return json.loads(self.rfile.read(length) or b"{}")

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/items":
            self.get_items(parse_qs(parsed.query))
            return
        if parsed.path == "/api/status":
            self.get_status()
            return
        if parsed.path == "/api/schools":
            self.get_schools()
            return
        if parsed.path.startswith("/api/"):
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        if parsed.path == "/":
            self.path = "/index.html"
        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/scan":
                started = scan_all_async()
                self.send_json(
                    {"started": started, "message": "检查已开始" if started else "正在检查中"}
                )
                return
            if parsed.path == "/api/schools":
                self.save_school(self.read_json())
                return
            if parsed.path == "/api/items/read":
                payload = self.read_json()
                with db_connection() as conn:
                    conn.execute(
                        "UPDATE items SET is_new=0 WHERE id=?", (int(payload["id"]),)
                    )
                self.send_json({"ok": True})
                return
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            self.send_json({"error": f"提交内容有误：{exc}"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/schools":
            self.send_json({"error": "接口不存在"}, HTTPStatus.NOT_FOUND)
            return
        query = parse_qs(parsed.query)
        name = query.get("name", [""])[0]
        rows = [row for row in read_school_rows() if row.get("name") != name]
        write_school_rows(rows)
        self.send_json({"ok": True})

    def get_items(self, query):
        keyword = query.get("q", [""])[0].strip()
        ownership = query.get("ownership", [""])[0].strip()
        region = query.get("region", [""])[0].strip()
        category = query.get("category", [""])[0].strip()
        pdf_only = query.get("pdf", [""])[0] == "1"
        new_only = query.get("new", [""])[0] == "1"
        clauses = ["relevance>=2"]
        params = []
        if keyword:
            clauses.append("(title LIKE ? OR school LIKE ? OR matched LIKE ?)")
            params.extend([f"%{keyword}%"] * 3)
        for column, value in (
            ("ownership", ownership),
            ("region", region),
            ("category", category),
        ):
            if value:
                clauses.append(f"{column}=?")
                params.append(value)
        if pdf_only:
            clauses.append("is_pdf=1")
        if new_only:
            clauses.append("is_new=1")
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with db_connection() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM items {where}
                ORDER BY is_new DESC, first_seen_at DESC, id DESC LIMIT 500
                """,
                params,
            ).fetchall()
        self.send_json({"items": [dict(row) for row in rows], "total": len(rows)})

    def get_status(self):
        with db_connection() as conn:
            counts = conn.execute(
                """
                SELECT COUNT(*) total, SUM(is_new) new_count,
                SUM(is_pdf) pdf_count, COUNT(DISTINCT school) school_count
                FROM items WHERE relevance>=2
                """
            ).fetchone()
            last_run = conn.execute(
                "SELECT * FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        payload = dict(runtime)
        payload["counts"] = {
            key: counts[key] or 0 for key in counts.keys()
        }
        active_schools = {
            row.get("name")
            for row in read_school_rows()
            if row.get("enabled", "").lower()
            not in {"no", "n", "false", "0", "不要", "否"}
        }
        payload["counts"]["school_count"] = len(active_schools)
        payload["last_run"] = dict(last_run) if last_run else None
        payload["interval_minutes"] = CHECK_INTERVAL_SECONDS // 60
        self.send_json(payload)

    def get_schools(self):
        rows = read_school_rows()
        for row in rows:
            row["region"] = row.get("region", "") or REGIONS.get(
                row.get("name", ""), "其他"
            )
            row["active"] = row.get("enabled", "").lower() not in {
                "no",
                "n",
                "false",
                "0",
                "不要",
                "否",
            }
        self.send_json({"schools": rows})

    def save_school(self, payload):
        name = monitor.clean_text(payload["name"])
        url = monitor.clean_text(payload["url"])
        if not name or not monitor.usable_url(url):
            raise ValueError("请填写学校名称和完整官网网址")
        rows = read_school_rows()
        old_name = monitor.clean_text(payload.get("old_name", ""))
        existing = next(
            (
                row
                for row in rows
                if row.get("name") == (old_name or name)
            ),
            {},
        )
        replacement = {
            "enabled": "yes" if payload.get("active", True) else "no",
            "name": name,
            "ownership": monitor.clean_text(
                payload.get("ownership", "未分类")
            ),
            "region": monitor.clean_text(
                payload.get("region", existing.get("region", "其他"))
            ),
            "batch": monitor.clean_text(
                payload.get("batch", existing.get("batch", "1"))
            ),
            "url": url,
            "notes": monitor.clean_text(payload.get("notes", "网站添加")),
        }
        if replacement["ownership"] not in ("国立", "公立", "私立"):
            raise ValueError("大学类型必须是国立、公立或私立")
        replaced = False
        for index, row in enumerate(rows):
            if row.get("name") == (old_name or name):
                rows[index] = replacement
                replaced = True
                break
        if not replaced:
            rows.append(replacement)
        write_school_rows(rows)
        self.send_json({"ok": True})


def scan_all_async():
    if runtime["scanning"]:
        return False
    threading.Thread(target=scan_all, daemon=True, name="manual-scan").start()
    return True


def main():
    init_database()
    runtime["next_check_at"] = datetime.now().isoformat(timespec="seconds")
    threading.Thread(target=scheduler, daemon=True, name="scan-scheduler").start()
    server = ThreadingHTTPServer((HOST, PORT), WebsiteHandler)
    print(f"监控网站已启动：http://{HOST}:{PORT}", flush=True)
    print("关闭这个窗口会停止网站和自动监控。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
