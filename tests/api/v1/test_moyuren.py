"""Tests for app/api/v1/moyuren.py - unified API endpoint."""

import json
import logging
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.moyuren import router


class TestMoyurenAPI:
    """Tests for unified Moyuren API endpoint."""

    @pytest.fixture
    def sample_data(self) -> dict[str, Any]:
        """Sample data file content."""
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
    def app(self, tmp_path: Path, sample_data: dict) -> FastAPI:
        """Create a test FastAPI app."""
        app = FastAPI()
        app.include_router(router)

        # Set up cache directory structure
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # Create data file for today
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(sample_data))

        # Create mock image file
        image_path = images_dir / "moyuren_20260210_072232.jpg"
        image_path.write_bytes(b"fake image content")

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config
        app.state.logger = logging.getLogger("test")

        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client."""
        return TestClient(app)

    @pytest.fixture
    def mock_today(self):
        """Mock today_business to return fixed date."""
        with patch("app.api.v1.moyuren.today_business") as mock:
            mock.return_value = date(2026, 2, 10)
            yield mock

    def test_get_moyuren_json_simple(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren with default parameters (encode=json, detail=false)."""
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-10"
        assert data["updated"] == "2026/02/10 07:22:32"
        assert data["updated_at"] == 1739145752000
        assert data["image"] == "http://localhost:8000/static/moyuren_20260210_072232.jpg"
        # Simple response should not include detail fields
        assert "weekday" not in data
        assert "fun_content" not in data

    def test_get_moyuren_json_detail(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?detail=true."""
        response = client.get("/api/v1/moyuren?detail=true")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-10"
        assert data["image"] == "http://localhost:8000/static/moyuren_20260210_072232.jpg"
        # Detail response should include all fields
        assert data["weekday"] == "星期一"
        assert data["lunar_date"] == "正月十三"
        assert data["fun_content"] == {"title": "🐟 摸鱼小贴士", "content": "测试内容"}
        assert data["is_crazy_thursday"] is False
        assert "guide" in data
        assert "news_list" in data

    def test_get_moyuren_text(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?encode=text."""
        response = client.get("/api/v1/moyuren?encode=text")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/plain; charset=utf-8"
        text = response.text
        assert "日期: 2026-02-10" in text
        assert "更新时间: 2026/02/10 07:22:32" in text
        assert "图片: http://localhost:8000/static/moyuren_20260210_072232.jpg" in text

    def test_get_moyuren_markdown(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?encode=markdown."""
        response = client.get("/api/v1/moyuren?encode=markdown")

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/markdown; charset=utf-8"
        markdown = response.text
        assert "# 摸鱼日历 - 2026-02-10" in markdown
        assert "**更新时间**: 2026/02/10 07:22:32" in markdown
        assert "![摸鱼日历](http://localhost:8000/static/moyuren_20260210_072232.jpg)" in markdown

    def test_get_moyuren_image(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?encode=image."""
        response = client.get("/api/v1/moyuren?encode=image")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == b"fake image content"

    def test_get_moyuren_with_date_parameter(self, tmp_path: Path, mock_today) -> None:
        """Test GET /api/v1/moyuren?date=2026-02-09."""
        app = FastAPI()
        app.include_router(router)

        # Set up cache directory
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # Create data file for 2026-02-09
        history_data = {
            "date": "2026-02-09",
            "updated": "2026/02/09 07:00:00",
            "updated_at": 1739059200000,
            "images": {"moyuren": "moyuren_20260209_070000.jpg"},
        }
        data_file = data_dir / "2026-02-09.json"
        data_file.write_text(json.dumps(history_data))

        # Create image file
        image_path = images_dir / "moyuren_20260209_070000.jpg"
        image_path.write_bytes(b"history image")

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config

        client = TestClient(app)
        response = client.get("/api/v1/moyuren?date=2026-02-09")

        assert response.status_code == 200
        data = response.json()
        assert data["date"] == "2026-02-09"
        assert data["image"] == "http://localhost:8000/static/moyuren_20260209_070000.jpg"
        # History data should have immutable cache header
        assert "Cache-Control" in response.headers
        assert "immutable" in response.headers["Cache-Control"]

    def test_get_moyuren_invalid_date_format(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?date=invalid returns 400."""
        response = client.get("/api/v1/moyuren?date=invalid")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "API_7001"
        assert "Invalid date format" in data["error"]["message"]

    def test_get_moyuren_invalid_encode(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?encode=invalid returns 400."""
        response = client.get("/api/v1/moyuren?encode=invalid")

        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "API_7002"
        assert "Invalid encode parameter" in data["error"]["message"]

    def test_get_moyuren_date_not_found(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?date=2025-01-01 returns 404 when data doesn't exist."""
        response = client.get("/api/v1/moyuren?date=2025-01-01")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "API_7005"
        assert "No data available for date" in data["error"]["message"]

    def test_get_moyuren_cache_headers_today(self, client: TestClient, mock_today) -> None:
        """Test cache headers for today's data (ETag + Last-Modified)."""
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 200
        assert "ETag" in response.headers
        assert "Last-Modified" in response.headers
        assert "Cache-Control" in response.headers
        assert "must-revalidate" in response.headers["Cache-Control"]

    def test_get_moyuren_304_not_modified(self, client: TestClient, mock_today) -> None:
        """Test 304 Not Modified response with If-None-Match header."""
        # First request to get ETag
        response1 = client.get("/api/v1/moyuren")
        assert response1.status_code == 200
        etag = response1.headers["ETag"]

        # Second request with If-None-Match
        response2 = client.get("/api/v1/moyuren", headers={"If-None-Match": etag})
        assert response2.status_code == 304
        assert response2.content == b""

    def test_get_moyuren_template_parameter(self, tmp_path: Path, mock_today) -> None:
        """Test GET /api/v1/moyuren?template=custom."""
        app = FastAPI()
        app.include_router(router)

        # Set up cache directory
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # Create data file with multiple templates
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

        # Create image files
        (images_dir / "moyuren_20260210_072232.jpg").write_bytes(b"moyuren image")
        (images_dir / "custom_20260210_072232.jpg").write_bytes(b"custom image")

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config

        client = TestClient(app)

        # Test default template (first available)
        response1 = client.get("/api/v1/moyuren")
        assert response1.status_code == 200
        data1 = response1.json()
        assert "moyuren_20260210_072232.jpg" in data1["image"]

        # Test specific template
        response2 = client.get("/api/v1/moyuren?template=custom")
        assert response2.status_code == 200
        data2 = response2.json()
        assert "custom_20260210_072232.jpg" in data2["image"]

    def test_get_moyuren_template_not_found(self, client: TestClient, mock_today) -> None:
        """Test GET /api/v1/moyuren?template=nonexistent returns 500."""
        response = client.get("/api/v1/moyuren?template=nonexistent")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "API_7004"

    def test_get_moyuren_image_path_traversal_protection(self, tmp_path: Path, mock_today) -> None:
        """Test path traversal protection for encode=image."""
        app = FastAPI()
        app.include_router(router)

        # Set up cache directory
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # Create data file with malicious filename
        malicious_data = {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "../../../etc/passwd"},
        }
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(malicious_data))

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config

        client = TestClient(app)
        response = client.get("/api/v1/moyuren?encode=image")

        # Should reject path traversal attempt
        assert response.status_code == 400
        data = response.json()
        assert data["error"]["code"] == "STORAGE_4002"
        assert "Invalid filename" in data["error"]["message"]

    def test_get_moyuren_image_not_found(self, tmp_path: Path, mock_today) -> None:
        """Test GET /api/v1/moyuren?encode=image returns 404 when image file missing."""
        app = FastAPI()
        app.include_router(router)

        # Set up cache directory
        cache_dir = tmp_path / "cache"
        data_dir = cache_dir / "data"
        images_dir = cache_dir / "images"
        data_dir.mkdir(parents=True)
        images_dir.mkdir(parents=True)

        # Create data file but no image file
        data = {
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": "moyuren_20260210_072232.jpg"},
        }
        data_file = data_dir / "2026-02-10.json"
        data_file.write_text(json.dumps(data))
        # Don't create image file

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.cache_dir = str(cache_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config

        client = TestClient(app)
        response = client.get("/api/v1/moyuren?encode=image")

        assert response.status_code == 404
        data = response.json()
        assert data["error"]["code"] == "STORAGE_4003"
        assert "Image file not found" in data["error"]["message"]
