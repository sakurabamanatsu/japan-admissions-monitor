#!/usr/bin/env python3
import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parent
SCHOOLS_CSV = ROOT / "schools.csv"
FIELDS = [
    "enabled",
    "name",
    "ownership",
    "region",
    "batch",
    "url",
    "notes",
]


def clean(value):
    return (value or "").strip()


def load_rows():
    if not SCHOOLS_CSV.exists():
        return []
    with SCHOOLS_CSV.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            rows.append({field: clean(row.get(field, "")) for field in FIELDS})
        return rows


def save_rows(rows):
    with SCHOOLS_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def is_enabled(row):
    return row.get("enabled", "").lower() in ("yes", "y", "true", "1", "是")


def print_rows(rows):
    print("")
    print("当前学校列表：")
    if not rows:
        print("  还没有学校。")
        return
    for index, row in enumerate(rows, start=1):
        status = "启用" if is_enabled(row) else "停用"
        print(
            f"  {index}. [{status}] [第{row['batch'] or '1'}批] "
            f"[{row['ownership'] or '未分类'}] "
            f"{row['name']} - {row['url']}"
        )


def ask_number(prompt, max_value):
    value = input(prompt).strip()
    if not value.isdigit():
        print("请输入数字。")
        return None
    number = int(value)
    if number < 1 or number > max_value:
        print("数字不在列表范围内。")
        return None
    return number


def add_school(rows):
    print("")
    name = input("学校名：").strip()
    ownership = input("大学类型（国立/公立/私立）：").strip()
    if ownership not in ("国立", "公立", "私立"):
        print("大学类型必须填写：国立、公立或私立。")
        return
    region = input("地区（北海道/东北/关东/中部/关西/九州/其他）：").strip()
    if region not in ("北海道", "东北", "关东", "中部", "关西", "九州", "其他"):
        print("地区填写不正确。")
        return
    url = input("招生页面网址：https://").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not name or not url:
        print("学校名和网址都必须填写。")
        return
    rows.append(
        {
            "enabled": "yes",
            "name": name,
            "ownership": ownership,
            "region": region,
            "batch": str(len(rows) % 4 + 1),
            "url": url,
            "notes": "手动添加",
        }
    )
    save_rows(rows)
    print("已添加并启用。")


def toggle_school(rows):
    print_rows(rows)
    if not rows:
        return
    number = ask_number("输入要启用/停用的学校编号：", len(rows))
    if number is None:
        return
    row = rows[number - 1]
    row["enabled"] = "no" if is_enabled(row) else "yes"
    save_rows(rows)
    print(f"已切换：{row['name']} -> {'启用' if is_enabled(row) else '停用'}")


def edit_url(rows):
    print_rows(rows)
    if not rows:
        return
    number = ask_number("输入要修改网址的学校编号：", len(rows))
    if number is None:
        return
    row = rows[number - 1]
    print(f"当前网址：{row['url']}")
    url = input("新网址：https://").strip()
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    if not url:
        print("没有修改。")
        return
    row["url"] = url
    save_rows(rows)
    print("网址已修改。")


def delete_school(rows):
    print_rows(rows)
    if not rows:
        return
    number = ask_number("输入要删除的学校编号：", len(rows))
    if number is None:
        return
    row = rows[number - 1]
    confirm = input(f"确定删除 {row['name']} 吗？输入 yes 确认：").strip().lower()
    if confirm != "yes":
        print("已取消。")
        return
    rows.pop(number - 1)
    save_rows(rows)
    print("已删除。")


def main():
    rows = load_rows()
    while True:
        print_rows(rows)
        print("")
        print("请选择操作：")
        print("  1. 启用/停用学校")
        print("  2. 新增学校")
        print("  3. 修改学校网址")
        print("  4. 删除学校")
        print("  5. 保存并退出")
        choice = input("输入数字：").strip()
        if choice == "1":
            toggle_school(rows)
        elif choice == "2":
            add_school(rows)
            rows = load_rows()
        elif choice == "3":
            edit_url(rows)
            rows = load_rows()
        elif choice == "4":
            delete_school(rows)
            rows = load_rows()
        elif choice == "5":
            print("已保存。之后双击“双击运行.command”即可按当前学校列表检查。")
            return 0
        else:
            print("请输入 1 到 5。")


if __name__ == "__main__":
    raise SystemExit(main())
