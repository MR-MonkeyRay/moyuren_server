"""Unit tests for missing-image recovery without a threaded HTTP client."""

import json
import logging
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response

from app.api.v1.moyuren import get_moyuren
from app.core.errors import StorageError
from app.core.services import AppServices
from app.services.generator import GenerationBusyError


@pytest.fixture
def api_request(tmp_path: Path, mock_services: AppServices) -> Request:
    """Create a request with cached metadata and a missing image."""
    app = FastAPI()
    config = MagicMock()
    config.paths.cache_dir = str(tmp_path)
    config.server.base_domain = "http://localhost:8000"
    config.get_templates_config.return_value.items = [MagicMock(name="moyuren")]
    config.get_templates_config.return_value.items[0].name = "moyuren"
    app.state.config = config
    app.state.services = mock_services
    app.state.logger = logging.getLogger("test.moyuren.recovery")
    (tmp_path / "images").mkdir()
    (tmp_path / "data").mkdir()
    _save_data(tmp_path, {"moyuren": "missing.jpg"})
    return Request({"type": "http", "app": app, "headers": []})


def _save_data(cache_dir: Path, images: dict[str, str]) -> None:
    """Write valid metadata for the fixed business date."""
    (cache_dir / "data" / "2026-02-10.json").write_text(json.dumps({
        "date": "2026-02-10",
        "updated": "2026/02/10 07:22:32",
        "updated_at": 1739145752000,
        "images": images,
    }))


async def _get_response(
    api_request: Request, encode: str = "json", template: str | None = None,
    target_date: str | None = None,
) -> Response:
    """Call the route directly with concrete query defaults."""
    async def inline(function: Callable[..., Any], *args: object) -> Any:
        return function(*args)

    with (
        patch("app.api.v1.moyuren.image_width", return_value=1000),
        patch("app.api.v1.moyuren.asyncio.to_thread", side_effect=inline),
    ):
        return await get_moyuren(
            api_request, date=target_date, encode=encode, template=template, detail=False,
            format=None, width=None,
        )


@pytest.mark.parametrize("encode", ["json", "text", "markdown", "image"])
@pytest.mark.parametrize("template", [None, "moyuren"])
async def test_missing_image_regenerates_and_reloads(
    api_request: Request, encode: str, template: str | None,
) -> None:
    """Every output format recovers the selected image and reloads metadata."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)

    async def regenerate(
        app: FastAPI, template_name: str | None = None,
    ) -> dict[str, str]:
        (cache_dir / "images" / "regenerated.jpg").write_bytes(b"image")
        _save_data(cache_dir, {"moyuren": "regenerated.jpg"})
        return {"moyuren": "regenerated.jpg"}

    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", side_effect=regenerate) as generate,
    ):
        response = await _get_response(api_request, encode, template)

    generate.assert_awaited_once_with(api_request.app, template_name="moyuren")
    assert response.status_code == 200
    if isinstance(response, FileResponse):
        assert Path(response.path).name == "regenerated.jpg"
    else:
        assert b"regenerated.jpg" in response.body


@pytest.mark.parametrize("encode", ["json", "text", "markdown", "image"])
@pytest.mark.parametrize("missing_data", [False, True])
async def test_generation_busy_response(
    api_request: Request, encode: str, missing_data: bool,
) -> None:
    """Missing JSON and missing images share the same busy response."""
    if missing_data:
        (Path(api_request.app.state.config.paths.cache_dir) / "data" / "2026-02-10.json").unlink()
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", side_effect=GenerationBusyError("busy")) as generate,
    ):
        response = await _get_response(api_request, encode)

    generate.assert_awaited_once_with(api_request.app, template_name=None if missing_data else "moyuren")
    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert json.loads(bytes(response.body))["error"] == {
        "code": "GENERATION_5002",
        "message": "Image generation in progress, another process is generating the image",
    }


@pytest.mark.parametrize("missing_data", [False, True])
async def test_generation_failure_response(api_request: Request, missing_data: bool) -> None:
    """Generation failures retain the existing structured error response."""
    if missing_data:
        (Path(api_request.app.state.config.paths.cache_dir) / "data" / "2026-02-10.json").unlink()
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", side_effect=StorageError("failed")) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_awaited_once_with(api_request.app, template_name=None if missing_data else "moyuren")
    assert response.status_code == 500
    assert json.loads(bytes(response.body))["error"]["code"] == "GENERATION_5001"


@pytest.mark.parametrize("encode", ["json", "text", "markdown", "image"])
async def test_image_still_missing_after_regeneration(api_request: Request, encode: str) -> None:
    """Recovery attempts generation once and returns 404 if the image stays missing."""
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request, encode)

    generate.assert_awaited_once_with(api_request.app, template_name="moyuren")
    assert response.status_code == 404
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4003"


async def test_historical_missing_image_does_not_generate(api_request: Request) -> None:
    """Historical images keep their existing 404 behavior without generation."""
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 11)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request, "image", target_date="2026-02-10")

    generate.assert_not_awaited()
    assert response.status_code == 404
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4003"


async def test_unknown_template_does_not_generate(api_request: Request) -> None:
    """Unknown templates keep their template-not-found response."""
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request, template="unknown")

    generate.assert_not_awaited()
    assert response.status_code == 404
    assert json.loads(bytes(response.body))["error"]["code"] == "API_7004"


@pytest.mark.parametrize("template", [None, "moyuren"])
async def test_configured_template_without_filename_recovers(
    api_request: Request, template: str | None,
) -> None:
    """A configured template absent from metadata also triggers recovery."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    _save_data(cache_dir, {})

    async def regenerate(
        app: FastAPI, template_name: str | None = None,
    ) -> dict[str, str]:
        (cache_dir / "images" / "regenerated.jpg").write_bytes(b"image")
        _save_data(cache_dir, {"moyuren": "regenerated.jpg"})
        return {"moyuren": "regenerated.jpg"}

    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", side_effect=regenerate) as generate,
    ):
        response = await _get_response(api_request, template=template)

    generate.assert_awaited_once_with(api_request.app, template_name=template)
    assert response.status_code == 200


@pytest.mark.parametrize("filename", ["../outside.jpg", "nested/image.jpg", "nested\\image.jpg"])
async def test_invalid_image_path_does_not_generate(api_request: Request, filename: str) -> None:
    """Unsafe image filenames are rejected before any generation attempt."""
    _save_data(Path(api_request.app.state.config.paths.cache_dir), {"moyuren": filename})
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_not_awaited()
    assert response.status_code == 400
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4002"


async def test_missing_default_image_regenerates_first_template(api_request: Request) -> None:
    """Default recovery follows metadata order even for an unconfigured template."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    _save_data(cache_dir, {"custom": "missing.jpg", "moyuren": "available.jpg"})
    (cache_dir / "images" / "available.jpg").write_bytes(b"image")
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_awaited_once_with(api_request.app, template_name="custom")
    assert response.status_code == 404


@pytest.mark.parametrize("template", [None, "moyuren", "custom"])
async def test_only_selected_image_triggers_recovery(
    api_request: Request, template: str | None,
) -> None:
    """Check the first available image or the explicit template, independently."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    _save_data(cache_dir, {"moyuren": "available.jpg", "custom": "missing.jpg"})
    (cache_dir / "images" / "available.jpg").write_bytes(b"image")
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request, template=template)

    if template == "custom":
        generate.assert_awaited_once_with(api_request.app, template_name="custom")
        assert response.status_code == 404
    else:
        generate.assert_not_awaited()
        assert response.status_code == 200


@pytest.mark.parametrize("template", [None, "moyuren"])
async def test_missing_json_does_not_generate_twice(
    api_request: Request, template: str | None,
) -> None:
    """A generation that restores only metadata does not trigger a second attempt."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    (cache_dir / "data" / "2026-02-10.json").unlink()

    async def regenerate(
        app: FastAPI, template_name: str | None = None,
    ) -> dict[str, str]:
        _save_data(cache_dir, {"moyuren": "missing.jpg"})
        return {"moyuren": "missing.jpg"}

    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", side_effect=regenerate) as generate,
    ):
        response = await _get_response(api_request, template=template)

    generate.assert_awaited_once_with(api_request.app, template_name=None)
    assert response.status_code == 404
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4003"


async def test_matching_etag_does_not_hide_missing_image(api_request: Request) -> None:
    """Image recovery and missing-file errors take precedence over a cached ETag."""
    api_request.scope["headers"] = [(b"if-none-match", b'"1739145752000"')]
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_awaited_once_with(api_request.app, template_name="moyuren")
    assert response.status_code == 404
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4003"


async def test_image_symlink_outside_directory_is_rejected(api_request: Request) -> None:
    """Reject symlinks into a sibling directory even when it shares a path prefix."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    outside_dir = cache_dir / "images_outside"
    outside_dir.mkdir()
    outside_image = outside_dir / "outside.jpg"
    outside_image.write_bytes(b"outside")
    (cache_dir / "images" / "missing.jpg").symlink_to(outside_image)
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_not_awaited()
    assert response.status_code == 400
    assert json.loads(bytes(response.body))["error"]["code"] == "STORAGE_4002"


@pytest.mark.parametrize("template", [None, "custom"])
async def test_missing_image_generates_only_selected_template(
    api_request: Request, template: str | None,
) -> None:
    """Generate the first mapped template or the explicitly requested template."""
    cache_dir = Path(api_request.app.state.config.paths.cache_dir)
    _save_data(cache_dir, {"first": "first_missing.jpg", "custom": "custom_missing.jpg"})
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request, template=template)

    generate.assert_awaited_once_with(
        api_request.app, template_name="first" if template is None else "custom",
    )
    assert response.status_code == 404


async def test_missing_image_without_known_template_generates_all(api_request: Request) -> None:
    """Generate all templates when no template or image mapping identifies one."""
    _save_data(Path(api_request.app.state.config.paths.cache_dir), {})
    with (
        patch("app.api.v1.moyuren.today_business", return_value=date(2026, 2, 10)),
        patch("app.api.v1.moyuren.generate_and_save_image", new_callable=AsyncMock) as generate,
    ):
        response = await _get_response(api_request)

    generate.assert_awaited_once_with(api_request.app, template_name=None)
    assert response.status_code == 404
