from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import Settings


@dataclass(frozen=True)
class DailyEntry:
    daily_path: Path
    relative_path: Path
    is_new_file: bool


class DailyNoteWriter:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._tz = ZoneInfo(settings.timezone)
        self._settings.daily_path.mkdir(parents=True, exist_ok=True)

    def append_entry(
        self,
        *,
        text: str,
        is_task: bool = False,
        target_date: datetime | None = None,
    ) -> DailyEntry:
        now = datetime.now(self._tz)
        target = target_date or now

        filename = f"{target.strftime('%Y-%m-%d')}.md"
        daily_file = self._settings.daily_path / filename
        relative_path = Path(self._settings.daily_dir) / filename

        is_new = not daily_file.exists()

        if is_new:
            content = self._create_daily_template(target)
        else:
            content = daily_file.read_text(encoding="utf-8")

        entry = self._format_entry(text=text, time=now, is_task=is_task)
        content = content.rstrip() + "\n\n" + entry + "\n"

        daily_file.write_text(content, encoding="utf-8")

        return DailyEntry(
            daily_path=daily_file,
            relative_path=relative_path,
            is_new_file=is_new,
        )

    def _create_daily_template(self, date: datetime) -> str:
        weekday_names = ["一", "二", "三", "四", "五", "六", "日"]
        weekday = weekday_names[date.weekday()]
        return f"""---
date: {date.strftime('%Y-%m-%d')}
tags:
  - daily
---

# {date.strftime('%Y-%m-%d')} 星期{weekday}
"""

    def _format_entry(self, *, text: str, time: datetime, is_task: bool) -> str:
        time_str = time.strftime("%H:%M")
        if is_task:
            return f"## {time_str}\n- [ ] {text} #task"
        return f"## {time_str}\n- {text}"

    def parse_date_modifier(self, modifier: str) -> datetime | None:
        now = datetime.now(self._tz)
        modifier = modifier.lower().strip()

        if modifier in ("明天", "tomorrow"):
            return now + timedelta(days=1)
        if modifier in ("後天", "后天"):
            return now + timedelta(days=2)
        if modifier in ("今天", "today"):
            return now

        weekday_map = {
            "週一": 0, "周一": 0, "monday": 0,
            "週二": 1, "周二": 1, "tuesday": 1,
            "週三": 2, "周三": 2, "wednesday": 2,
            "週四": 3, "周四": 3, "thursday": 3,
            "週五": 4, "周五": 4, "friday": 4,
            "週六": 5, "周六": 5, "saturday": 5,
            "週日": 6, "周日": 6, "sunday": 6,
        }

        if modifier in weekday_map:
            target_weekday = weekday_map[modifier]
            current_weekday = now.weekday()
            days_ahead = target_weekday - current_weekday
            if days_ahead <= 0:
                days_ahead += 7
            return now + timedelta(days=days_ahead)

        return None
