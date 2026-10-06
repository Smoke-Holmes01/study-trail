"""Deterministic evidence for deadlines and explicit total time."""

import re
from datetime import date, timedelta
from zoneinfo import ZoneInfo


def constraints(text, created_at):
    today = created_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    deadline = None
    match = re.search(r"(\d{4})[-年/](\d{1,2})[-月/](\d{1,2})日?", text)
    try:
        if match:
            deadline = date(*map(int, match.groups()))
        else:
            match = re.search(r"(\d{1,2})月(\d{1,2})日", text)
            if match:
                deadline = date(today.year, *map(int, match.groups()))
            else:
                match = re.search(r"([一二两三四五六七八九十\d]+)(天|周)(?:内|后)", text)
                if match:
                    numbers = {
                        "一": 1,
                        "二": 2,
                        "两": 2,
                        "三": 3,
                        "四": 4,
                        "五": 5,
                        "六": 6,
                        "七": 7,
                        "八": 8,
                        "九": 9,
                        "十": 10,
                    }
                    number = int(match[1]) if match[1].isdigit() else numbers.get(match[1])
                    if number:
                        deadline = today + timedelta(days=number * (7 if match[2] == "周" else 1))
    except ValueError:
        deadline = None
    total = None
    match = re.search(
        r"(?:总共|总计|总时间|合计|总耗时|一共)\s*(?:不超过|最多|只有|为|有)?\s*(\d+)\s*(分钟|小时)", text
    )
    if match:
        total = int(match[1]) * (60 if match[2] == "小时" else 1)
    return {"deadline": deadline.isoformat() if deadline else None, "time_limit_minutes": total}
