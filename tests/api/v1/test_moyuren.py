"""app/api/v1/moyuren.py 统一 API 端点测试。"""

import json
import logging
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from starlette.requests import Request

from app.api.v1.moyuren import _build_cache_headers, _check_not_modified, router
from app.core.config import TemplateRenderConfig
from app.core.services import AppServices
from app.services.generator import GenerationBusyError


class TestMoyurenAPI:
    """统一摸鱼日历 API 端点测试。"""

    @pytest.fixture
    def sample_data(self) -> dict[str, Any]:
        """样例数据文件内容。"""
        return {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "moyuren_20260210_072232.jpg"},
            "weekday": "星期一",
            "lunar_date": "正月十三",
            "fun_content": {"title": "🐟 摸鱼小贴士", "content": "测试内容"},
            "is_crazy_thursday": False,
            "kfc_content": None,
            "date_info": {"year": 2026, "month": 2, "day": 10},
            "weekend": False,
            "solar_term": None,
            "guide": {"宜": ["摸鱼"], "忌": ["加班"]},
            "news_list": [],
            "news_meta": {},
            "holidays": [],
            "kfc_content_full": None,
            "stock_indices": [],
        }

    @pytest.fixture
    def app(self, tmp_path: Path, sample_data: dict, mock_services: AppServices) -> FastAPI:
        """创建测试用 FastAPI 应用。"""
        app = FastAPI()
        app.include_router(router)

        # 创建缓存目录结构
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # 创建当日数据文件
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(sample_data))

        # 创建模拟图片文件
        image_path = images_dir / "moyuren_20260210_072232.jpg"
        Image.new("RGB", (1000, 500), "white").save(image_path, "JPEG")

        # 模拟配置
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"
        mock_config.get_templates_config.return_value.config = TemplateRenderConfig()

        app.state.config = mock_config
        app.state.services = mock_services
        app.state.logger = logging.getLogger("test")

        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """创建测试客户端。"""
        return TestClient(app)

    @pytest.fixture
    def mock_today(self) -> Iterator[MagicMock]:
        """模拟 today_business，使其返回固定日期。"""
        with patch("app.api.v1.moyuren.today_business") as mock:
            mock.return_value = date(2026, 2, 10)
            yield mock

    def test_get_moyuren_json_simple(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试使用默认参数（encode=json、detail=false）的 GET /api/v1/moyuren。"""
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-10"
        assert data["updated"] == "2026/02/10 07:22:32"
        assert data["updated_at"] == 1739145752000
        assert data["image"] == "http://localhost:8000/static/moyuren_20260210_072232.jpg"
        # 精简响应不应包含详情字段
        assert "weekday" not in data
        assert "fun_content" not in data
        assert "schema_version" not in data

    def test_get_moyuren_json_detail(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?detail=true。"""
        response = client.get("/api/v1/moyuren?detail=true")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-10"
        assert data["image"] == "http://localhost:8000/static/moyuren_20260210_072232.jpg"
        # 详情响应应包含所有字段
        assert data["weekday"] == "星期一"
        assert data["lunar_date"] == "正月十三"
        assert data["fun_content"] == {"title": "🐟 摸鱼小贴士", "content": "测试内容"}
        assert data["is_crazy_thursday"] is False
        assert "guide" in data
        assert "news_list" in data
        assert "schema_version" not in data

    def test_get_moyuren_text(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?encode=text。"""
        response = client.get("/api/v1/moyuren?encode=text")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        text = response.text
        assert "日期: 2026-02-10" in text
        assert "更新时间: 2026/02/10 07:22:32" in text
        assert "图片: http://localhost:8000/static/moyuren_20260210_072232.jpg" in text

    def test_get_moyuren_markdown(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?encode=markdown。"""
        response = client.get("/api/v1/moyuren?encode=markdown")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        markdown = response.text
        assert "# 摸鱼日历 - 2026-02-10" in markdown
        assert "**更新时间**: 2026/02/10 07:22:32" in markdown
        assert "![摸鱼日历](http://localhost:8000/static/moyuren_20260210_072232.jpg)" in markdown

    def test_get_moyuren_image(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?encode=image。"""
        response = client.get("/api/v1/moyuren?encode=image")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content.startswith(b"\xff\xd8")

    def test_get_moyuren_with_date_parameter(
        self, tmp_path: Path, mock_today: MagicMock, mock_services: AppServices
    ) -> None:
        """测试 GET /api/v1/moyuren?date=2026-02-09。"""
        app = FastAPI()
        app.include_router(router)

        # 创建缓存目录
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # 创建 2026-02-09 的数据文件
        history_data = {
            "date": "2026-02-09",
            "updated": "2026/02/09 07:00:00",
            "updated_at": 1739059200000,
            "images": {"moyuren": "moyuren_20260209_070000.jpg"},
        }
        data_file = data_dir / "2026-02-09.json"
        data_file.write_text(json.dumps(history_data))

        # 创建图片文件
        image_path = images_dir / "moyuren_20260209_070000.jpg"
        image_path.write_bytes(b"history image")

        # 模拟配置
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"
        mock_config.get_templates_config.return_value.config = TemplateRenderConfig()

        app.state.config = mock_config
        app.state.services = mock_services

        client = TestClient(app)
        response = client.get("/api/v1/moyuren?date=2026-02-09")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-09"
        assert data["image"] == "http://localhost:8000/static/moyuren_20260209_070000.jpg"
        # 历史数据的缓存头应包含 immutable
        assert "Cache-Control" in response.headers
        assert "immutable" in response.headers["Cache-Control"]

    def test_get_moyuren_invalid_date_format(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?date=invalid 返回 400。"""
        response = client.get("/api/v1/moyuren?date=invalid")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "API_7001"
        assert "Invalid date format" in data["error"]["message"]

    def test_get_moyuren_invalid_encode(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?encode=invalid 返回 400。"""
        response = client.get("/api/v1/moyuren?encode=invalid")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "API_7002"
        assert "Invalid encode parameter" in data["error"]["message"]

    def test_encode_openapi_enum(self, app: FastAPI) -> None:
        """测试 OpenAPI 列出输出格式，但不改变运行时校验。"""
        parameters = app.openapi()["paths"]["/api/v1/moyuren"]["get"]["parameters"]
        encode = next(parameter for parameter in parameters if parameter["name"] == "encode")
        assert encode["schema"]["enum"] == ["json", "text", "markdown", "image", "url"]

    def test_get_moyuren_date_not_found(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试数据不存在时 GET /api/v1/moyuren?date=2025-01-01 返回 404。"""
        response = client.get("/api/v1/moyuren?date=2025-01-01")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "API_7005"
        assert "No data available for date" in data["error"]["message"]

    def test_get_moyuren_cache_headers_today(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试当日数据的缓存头（ETag 与 Last-Modified）。"""
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 200
        assert "ETag" in response.headers
        assert "Last-Modified" in response.headers
        assert "Cache-Control" in response.headers
        assert "must-revalidate" in response.headers["Cache-Control"]

    def test_get_moyuren_304_not_modified(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试携带 If-None-Match 请求头时返回 304 未修改响应。"""
        # 首次请求获取 ETag
        response1 = client.get("/api/v1/moyuren")
        assert response1.status_code == 200
        etag = response1.headers["ETag"]

        # 第二次请求携带 If-None-Match
        response2 = client.get("/api/v1/moyuren", headers={"If-None-Match": etag})
        assert response2.status_code == 304
        assert response2.content == b""

    @pytest.mark.parametrize("validator", ['W/{etag}', '"other", W/{etag}, "another"', '*'])
    def test_get_moyuren_etag_variants(
        self, client: TestClient, mock_today: MagicMock, validator: str,
    ) -> None:
        """测试弱校验器、校验器列表和通配符均返回 304。"""
        response = client.get("/api/v1/moyuren")
        etag = response.headers["ETag"]
        cached = client.get(
            "/api/v1/moyuren", headers={"If-None-Match": validator.format(etag=etag)},
        )
        assert cached.status_code == 304
        assert cached.content == b""

    def test_get_moyuren_etag_takes_precedence(
        self, client: TestClient, mock_today: MagicMock,
    ) -> None:
        """测试 ETag 不匹配时优先于匹配的修改日期。"""
        response = client.get("/api/v1/moyuren")
        cached = client.get("/api/v1/moyuren", headers={
            "If-None-Match": '"other"',
            "If-Modified-Since": response.headers["Last-Modified"],
        })
        assert cached.status_code == 200

    def test_get_moyuren_last_modified_milliseconds(
        self, app: FastAPI, client: TestClient, mock_today: MagicMock,
    ) -> None:
        """测试回传 Last-Modified 可匹配毫秒级更新时间戳。"""
        data_file = Path(app.state.config.paths.cache_dir) / "data" / "2026-02-10.json"
        data = json.loads(data_file.read_text())
        data["updated_at"] += 321
        data_file.write_text(json.dumps(data))
        response = client.get("/api/v1/moyuren")
        cached = client.get(
            "/api/v1/moyuren", headers={"If-Modified-Since": response.headers["Last-Modified"]},
        )
        assert cached.status_code == 304
        assert cached.content == b""

    def test_get_moyuren_template_parameter(
        self, tmp_path: Path, mock_today: MagicMock, mock_services: AppServices
    ) -> None:
        """测试 GET /api/v1/moyuren?template=custom。"""
        app = FastAPI()
        app.include_router(router)

        # 创建缓存目录
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # 创建包含多个模板的数据文件
        multi_template_data = {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {
                "moyuren": "moyuren_20260210_072232.jpg",
                "custom": "custom_20260210_072232.jpg",
            },
        }
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(multi_template_data))

        # 创建图片文件
        (images_dir / "moyuren_20260210_072232.jpg").write_bytes(b"moyuren image")
        (images_dir / "custom_20260210_072232.jpg").write_bytes(b"custom image")

        # 模拟配置
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"
        mock_config.get_templates_config.return_value.config = TemplateRenderConfig()

        app.state.config = mock_config
        app.state.services = mock_services

        client = TestClient(app)

        # 测试默认模板（首个可用模板）
        response1 = client.get("/api/v1/moyuren")
        assert response1.status_code == 200
        data1 = response1.json()
        assert "moyuren_20260210_072232.jpg" in data1["image"]

        # 测试指定模板
        response2 = client.get("/api/v1/moyuren?template=custom")
        assert response2.status_code == 200
        data2 = response2.json()
        assert "custom_20260210_072232.jpg" in data2["image"]

    def test_get_moyuren_template_not_found(self, client: TestClient, mock_today: MagicMock) -> None:
        """测试 GET /api/v1/moyuren?template=nonexistent 返回 500。"""
        response = client.get("/api/v1/moyuren?template=nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "API_7004"

    def test_get_moyuren_image_path_traversal_protection(
        self, tmp_path: Path, mock_today: MagicMock, mock_services: AppServices
    ) -> None:
        """测试 encode=image 的路径穿越防护。"""
        app = FastAPI()
        app.include_router(router)

        # 创建缓存目录
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # 创建含恶意文件名的数据文件
        malicious_data = {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "../../../etc/passwd"},
        }
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(malicious_data))

        # 模拟配置
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"
        mock_config.get_templates_config.return_value.config = TemplateRenderConfig()

        app.state.config = mock_config
        app.state.services = mock_services

        client = TestClient(app)
        response = client.get("/api/v1/moyuren?encode=image")

        # 应拒绝路径穿越尝试
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "STORAGE_4002"
        assert "Invalid filename" in data["error"]["message"]

    def test_get_moyuren_image_not_found(
        self, tmp_path: Path, mock_today: MagicMock, mock_services: AppServices
    ) -> None:
        """测试图片文件缺失时 GET /api/v1/moyuren?encode=image 返回 404。"""
        app = FastAPI()
        app.include_router(router)

        # 创建缓存目录
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # 仅创建数据文件，不创建图片文件
        data = {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "moyuren_20260210_072232.jpg"},
        }
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(data))
        # 不创建图片文件

        # 模拟配置
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"
        mock_config.get_templates_config.return_value.config = TemplateRenderConfig()

        app.state.config = mock_config
        app.state.services = mock_services

        client = TestClient(app)
        with patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate:
            response = client.get("/api/v1/moyuren?encode=image")

        generate.assert_awaited_once_with(app, template_name="moyuren")
        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "STORAGE_4003"
        assert "Image file not found" in data["error"]["message"]

    @pytest.mark.parametrize("encode", ["json", "text", "markdown", "image"])
    def test_missing_today_image_is_regenerated(
        self, app: FastAPI, client: TestClient, mock_today: MagicMock, encode: str,
    ) -> None:
        """测试所有输出格式在当日图片缺失时均触发重新生成。"""
        cache_dir = Path(app.state.config.paths.cache_dir)
        image_path = cache_dir / "images" / "moyuren_20260210_072232.jpg"
        image_path.unlink()

        async def regenerate(
            application: FastAPI, template_name: str | None = None,
        ) -> dict[str, str]:
            filename = "moyuren_regenerated.jpg"
            Image.new("RGB", (1000, 500), "white").save(
                cache_dir / "images" / filename, "JPEG"
            )
            data_file = cache_dir / "data" / "2026-02-10.json"
            data = json.loads(data_file.read_text())
            data["images"] = {"moyuren": filename}
            data_file.write_text(json.dumps(data))
            return {"moyuren": filename}

        with patch("app.api.v1.moyuren.generate_and_save_image", side_effect=regenerate) as generate:
            response = client.get(f"/api/v1/moyuren?encode={encode}")

        generate.assert_awaited_once_with(app, template_name="moyuren")
        assert response.status_code == 200
        if encode == "image":
            assert response.content.startswith(b"\xff\xd8")
        else:
            assert "moyuren_regenerated.jpg" in response.text

    def test_missing_today_image_generation_busy(
        self, app: FastAPI, client: TestClient, mock_today: MagicMock,
    ) -> None:
        """测试当日图片缺失且生成繁忙时返回 503。"""
        image_path = Path(app.state.config.paths.cache_dir) / "images" / "moyuren_20260210_072232.jpg"
        image_path.unlink()
        with patch(
            "app.api.v1.moyuren.generate_and_save_image",
            side_effect=GenerationBusyError("busy"),
        ) as generate:
            response = client.get("/api/v1/moyuren?encode=image")

        generate.assert_awaited_once_with(app, template_name="moyuren")
        assert response.status_code == 503
        assert response.headers["Retry-After"] == "5"
        assert response.json()["error"]["code"] == "GENERATION_5002"

    def test_missing_history_image_does_not_generate(
        self, app: FastAPI, client: TestClient, mock_today: MagicMock,
    ) -> None:
        """测试历史图片缺失时返回 404，不重新生成。"""
        mock_today.return_value = date(2026, 2, 11)
        image_path = Path(app.state.config.paths.cache_dir) / "images" / "moyuren_20260210_072232.jpg"
        image_path.unlink()
        with patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate:
            response = client.get("/api/v1/moyuren?date=2026-02-10&encode=image")

        generate.assert_not_awaited()
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "STORAGE_4003"


class TestConditionalRequests:
    """仅测试请求头，不使用客户端或应用生命周期。"""

    @pytest.mark.parametrize(("headers", "expected"), [
        ({"If-None-Match": '"1739145752321"'}, True),
        ({"If-None-Match": 'W/"1739145752321"'}, True),
        ({"If-None-Match": '"other", "1739145752321", "another"'}, True),
        ({"If-None-Match": '*'}, True),
        ({"If-None-Match": '"other"', "If-Modified-Since": "Mon, 10 Feb 2025 00:02:32 GMT"}, False),
        ({"If-None-Match": '', "If-Modified-Since": "Mon, 10 Feb 2025 00:02:32 GMT"}, False),
        ({"If-Modified-Since": "Mon, 10 Feb 2025 00:02:32 GMT"}, True),
        ({"If-Modified-Since": "Mon, 10 Feb 2025 00:02:31 GMT"}, False),
        ({"If-Modified-Since": "Mon, 10 Feb 2025 08:02:32 +0800"}, True),
        ({"If-Modified-Since": "Monday, 10-Feb-25 00:02:32 GMT"}, True),
        ({"If-Modified-Since": "invalid"}, False),
        ({"If-Modified-Since": "Sun, 99 Feb 2025 23:42:32 GMT"}, False),
        ({}, False),
    ])
    def test_cache_validator(self, headers: dict[str, str], expected: bool) -> None:
        """测试校验器匹配、优先级、日期解析和秒级精度。"""
        request = Request({
            "type": "http",
            "headers": [(name.lower().encode("ascii"), value.encode("ascii")) for name, value in headers.items()],
        })
        assert _check_not_modified(request, 1739145752321) is expected

    def test_echo_last_modified(self) -> None:
        """测试带小数秒的时间戳所生成的 Last-Modified 可回传并匹配。"""
        today = date(2026, 2, 10)
        with patch("app.api.v1.moyuren.today_business", return_value=today):
            headers = _build_cache_headers(today, 1739145752321)
        request = Request({
            "type": "http",
            "headers": [(b"if-modified-since", headers["Last-Modified"].encode("ascii"))],
        })
        assert _check_not_modified(request, 1739145752321)
