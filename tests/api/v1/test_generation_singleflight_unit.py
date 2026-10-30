"""Unit tests for concurrent API generation without a threaded HTTP client."""

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.api.v1.moyuren import get_moyuren
from app.api.v1.ops import ops_generate
from app.core.config import TemplateRenderConfig
from app.core.services import AppServices


@pytest.fixture
def generation_request(tmp_path: Path, mock_services: AppServices) -> Request:
    """Create a request with configured services and an empty image cache."""
    app = FastAPI()
    config = MagicMock()
    config.paths.cache_dir = str(tmp_path)
    config.ops.api_key = "test-secret-key"
    config.server.base_domain = "http://localhost:8000"
    config.get_templates_config.return_value.items = [SimpleNamespace(name="moyuren")]
    config.get_templates_config.return_value.config = TemplateRenderConfig()
    app.state.config = config
    app.state.logger = logging.getLogger("test.generation")
    app.state.services = mock_services
    (tmp_path / "images").mkdir()
    (tmp_path / "data").mkdir()
    return Request({
        "type": "http",
        "app": app,
        "headers": [(b"authorization", b"Bearer test-secret-key")],
    })


async def _generate_response(request: Request, endpoint: str) -> Response:
    """Call the selected generation endpoint with concrete query defaults."""
    if endpoint == "ops":
        return await ops_generate(request)
    with patch("app.api.v1.moyuren.image_width", return_value=1000):
        return await get_moyuren(
            request, date=None, encode="json", template="moyuren", detail=False,
            format=None, width=None,
        )


@pytest.mark.parametrize("endpoint", ["moyuren", "ops"])
async def test_concurrent_generation_returns_success(
    generation_request: Request, endpoint: str
) -> None:
    """Concurrent API requests wait for one generation and both return 200."""
    app = generation_request.app
    services: AppServices = app.state.services
    cache_dir = Path(app.state.config.paths.cache_dir)
    started = asyncio.Event()
    release = asyncio.Event()

    async def generate(
        _app: FastAPI, template_name: str | None = None, trigger: str = "manual"
    ) -> dict[str, str]:
        started.set()
        await release.wait()
        filename = "moyuren_20260210.jpg"
        (cache_dir / "images" / filename).write_bytes(b"image")
        (cache_dir / "data" / "2026-02-10.json").write_text(json.dumps({
            "date": "2026-02-10",
            "updated": "2026/02/10 07:22:32",
            "updated_at": 1739145752000,
            "images": {"moyuren": filename},
        }))
        return {"moyuren": filename}

    with (
        patch.object(services.generator, "_generate_and_save_image", side_effect=generate) as pipeline,
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.ops.today_business", return_value=date(2026, 2, 10)),
    ):
        async with asyncio.timeout(1):
            first = asyncio.create_task(_generate_response(generation_request, endpoint))
            await started.wait()
            second = asyncio.create_task(_generate_response(generation_request, endpoint))
            await asyncio.sleep(0)
            assert not second.done()
            release.set()
            responses = await asyncio.gather(first, second)

    assert [response.status_code for response in responses] == [200, 200]
    assert responses[0].body == responses[1].body
    pipeline.assert_awaited_once()


@pytest.mark.parametrize(("endpoint", "retry_after"), [("moyuren", "5"), ("ops", "10")])
async def test_generation_wait_timeout_keeps_busy_response(
    generation_request: Request, endpoint: str, retry_after: str
) -> None:
    """In-process wait timeouts keep each endpoint's 503 and Retry-After."""
    services: AppServices = generation_request.app.state.services
    started = asyncio.Event()
    release = asyncio.Event()

    async def generate(
        _app: FastAPI, template_name: str | None = None, trigger: str = "manual"
    ) -> dict[str, str]:
        started.set()
        await release.wait()
        return {"moyuren": "moyuren.jpg"}

    with (
        patch.object(services.generator, "_generate_and_save_image", side_effect=generate),
        patch("app.services.generator.INFLIGHT_WAIT_TIMEOUT_SEC", 0.01),
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
    ):
        async with asyncio.timeout(1):
            first = asyncio.create_task(services.generator.generate(generation_request.app))
            await started.wait()
            try:
                response = await _generate_response(generation_request, endpoint)
            finally:
                release.set()
                await first

    assert response.status_code == 503
    assert response.headers["Retry-After"] == retry_after
    assert json.loads(bytes(response.body))["error"]["code"] == "GENERATION_5002"
