"""Tests for app/api/v1/moyuren.py - API endpoints."""

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.moyuren import router


class TestMoyurenAPI:
    """Tests for Moyuren API endpoints."""

    @pytest.fixture
    def sample_state_data(self) -> dict[str, Any]:
        """Sample state data."""
        return {
            "version": 2,
            "public": {
                "date": "2026-02-04",
                "timestamp": "2026-02-04T10:00:00+08:00",
                "updated": "2026/02/04 10:00:00",
                "updated_at": 1738634400000,
                "weekday": "星期三",
                "lunar_date": "正月初七",
                "fun_content": {"title": "🐟 摸鱼小贴士", "content": "测试内容"},
                "is_crazy_thursday": False,
                "kfc_content": None,
            },
            "templates": {
                "moyuren": {
                    "filename": "moyuren_20260204.jpg",
                    "updated": "2026/02/04 10:00:00",
                    "updated_at": 1738634400000,
                }
            },
            "template_data": {"moyuren": {}},
            # Backward compatible fields
            "date": "2026-02-04",
            "timestamp": "2026-02-04T10:00:00+08:00",
            "updated": "2026/02/04 10:00:00",
            "updated_at": 1738634400000,
            "filename": "moyuren_20260204.jpg",
        }

    @pytest.fixture
    def app(self, tmp_path: Path, sample_state_data: dict) -> FastAPI:
        """Create a test FastAPI app."""
        app = FastAPI()
        app.include_router(router)

        # Set up app state
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        state_path = state_dir / "latest.json"
        state_path.write_text(json.dumps(sample_state_data))

        # Create mock image file
        image_path = static_dir / "moyuren_20260204.jpg"
        image_path.write_bytes(b"fake image content")

        # Mock config
        mock_config = MagicMock()
        mock_config.paths.state_path = str(state_path)
        mock_config.paths.static_dir = str(static_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config
        app.state.logger = logging.getLogger("test")

        return app

    @pytest.fixture
    def client(self, app: FastAPI) -> TestClient:
        """Create a test client."""
        return TestClient(app)

    def test_get_moyuren_success(self, client: TestClient) -> None:
        """Test successful GET /api/v1/moyuren."""
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert "updated" in data
        assert "image" in data

    def test_get_moyuren_detail_success(self, client: TestClient) -> None:
        """Test successful GET /api/v1/moyuren/detail."""
        response = client.get("/api/v1/moyuren/detail")

        assert response.status_code == 200
        data = response.json()
        assert "date" in data
        assert "fun_content" in data

    def test_get_moyuren_latest_success(
        self, client: TestClient, app: FastAPI
    ) -> None:
        """Test successful GET /api/v1/moyuren/latest."""
        response = client.get("/api/v1/moyuren/latest")

        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"

    def test_get_moyuren_no_state_triggers_generation(
        self, tmp_path: Path
    ) -> None:
        """Test GET /api/v1/moyuren triggers generation when no state."""
        app = FastAPI()
        app.include_router(router)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        state_path = state_dir / "latest.json"
        # Don't create state file

        mock_config = MagicMock()
        mock_config.paths.state_path = str(state_path)
        mock_config.paths.static_dir = str(static_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config
        app.state.logger = logging.getLogger("test")

        # Mock generate_and_save_image to create state file
        async def mock_generate(app_instance: FastAPI) -> None:
            state_data = {
                "version": 2,
                "date": "2026-02-04",
                "timestamp": "2026-02-04T10:00:00+08:00",
                "updated": "2026/02/04 10:00:00",
                "updated_at": 1738634400000,
                "filename": "moyuren_20260204.jpg",
                "public": {
                    "date": "2026-02-04",
                    "timestamp": "2026-02-04T10:00:00+08:00",
                    "updated": "2026/02/04 10:00:00",
                    "updated_at": 1738634400000,
                },
                "templates": {
                    "moyuren": {
                        "filename": "moyuren_20260204.jpg",
                        "updated": "2026/02/04 10:00:00",
                        "updated_at": 1738634400000,
                    }
                },
            }
            state_path.write_text(json.dumps(state_data))
            # Create image file
            (static_dir / "moyuren_20260204.jpg").write_bytes(b"fake")

        with patch(
            "app.api.v1.moyuren.generate_and_save_image",
            side_effect=mock_generate
        ):
            client = TestClient(app)
            response = client.get("/api/v1/moyuren")

        assert response.status_code == 200

    def test_get_moyuren_invalid_state_returns_error(
        self, tmp_path: Path
    ) -> None:
        """Test GET /api/v1/moyuren with invalid state returns error."""
        app = FastAPI()
        app.include_router(router)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        state_path = state_dir / "latest.json"
        state_path.write_text("invalid json")

        mock_config = MagicMock()
        mock_config.paths.state_path = str(state_path)
        mock_config.paths.static_dir = str(static_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config
        app.state.logger = logging.getLogger("test")

        client = TestClient(app)
        response = client.get("/api/v1/moyuren")

        assert response.status_code == 500

    def test_get_moyuren_latest_missing_image_returns_error(
        self, tmp_path: Path
    ) -> None:
        """Test GET /api/v1/moyuren/latest with missing image returns error."""
        app = FastAPI()
        app.include_router(router)

        state_dir = tmp_path / "state"
        state_dir.mkdir()
        static_dir = tmp_path / "static"
        static_dir.mkdir()

        state_data = {
            "version": 2,
            "filename": "nonexistent.jpg",
            "date": "2026-02-04",
            "timestamp": "2026-02-04T10:00:00+08:00",
            "updated": "2026/02/04 10:00:00",
            "updated_at": 1738634400000,
            "public": {"date": "2026-02-04"},
            "templates": {
                "moyuren": {
                    "filename": "nonexistent.jpg",
                    "updated": "2026/02/04 10:00:00",
                    "updated_at": 1738634400000,
                }
            },
        }
        state_path = state_dir / "latest.json"
        state_path.write_text(json.dumps(state_data))

        mock_config = MagicMock()
        mock_config.paths.state_path = str(state_path)
        mock_config.paths.static_dir = str(static_dir)
        mock_config.server.base_domain = "http://localhost:8000"

        app.state.config = mock_config
        app.state.logger = logging.getLogger("test")

        client = TestClient(app)
        response = client.get("/api/v1/moyuren/latest")

        assert response.status_code == 404
