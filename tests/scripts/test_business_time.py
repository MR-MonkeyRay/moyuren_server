"""独立脚本业务时间处理的回归测试。"""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from scripts import publish_static, render_once


@pytest.mark.parametrize("module", [render_once, publish_static])
def test_timestamp_fields_use_business_date_and_display_time(
    monkeypatch: pytest.MonkeyPatch, module: object
) -> None:
    """显示时区为 UTC 时，业务星期/日期不应改变。"""
    now = datetime(2026, 10, 29, 2, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(module, "get_display_timezone", lambda: timezone.utc)

    fields = module._build_timestamp_fields(now)

    assert fields == {
        "date": "2026-10-29",
        "updated": "2026/10/28 18:00:00",
        "updated_at": 1793210400000,
        "is_crazy_thursday": True,
    }


def test_publish_artifacts_choose_dated_or_latest_image() -> None:
    """历史文本产物应保留其对应日期的图片 URL。"""
    data = {
        "date": "2026-10-29",
        "updated": "2026/10/28 18:00:00",
    }
    base_url = "https://example.com"
    dated_path = "2026-10-29/moyuren.jpg"

    dated_txt = publish_static.generate_txt(data, base_url, dated_path)
    latest_txt = publish_static.generate_txt(data, base_url)
    dated_md = publish_static.generate_md(data, base_url, dated_path)
    latest_md = publish_static.generate_md(data, base_url)

    assert "https://example.com/2026-10-29/moyuren.jpg" in dated_txt
    assert "https://example.com/latest.jpg" not in dated_txt
    assert "https://example.com/latest.jpg" in latest_txt
    assert "https://example.com/2026-10-29/moyuren.jpg" in dated_md
    assert "https://example.com/latest.jpg" not in dated_md
    assert "https://example.com/latest.jpg" in latest_md
