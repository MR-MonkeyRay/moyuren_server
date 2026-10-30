"""Unit coverage for image variant API selection and cache identity."""

import json
import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Literal

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, Response
from PIL import Image

import app.api.v1.moyuren as moyuren_module
from app.core.config import TemplateRenderConfig
from app.models.schemas import ImageVariant


@pytest.fixture(autouse=True)
def inline_to_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """Execute small Pillow helpers inline in this sandbox."""
    async def inline(function: Callable[..., Any], *args: object) -> Any:
        return function(*args)

    monkeypatch.setattr(moyuren_module.asyncio, "to_thread", inline)


def _request(tmp_path: Path, *, variants: list[dict[str, object]] | None = None) -> Request:
    app = FastAPI()
    cache_dir = tmp_path / "cache"
    images_dir = cache_dir / "images"
    data_dir = cache_dir / "data"
    images_dir.mkdir(parents=True)
    data_dir.mkdir()
    filename = "moyuren_20260210_072232.jpg"
    Image.new("RGB", (1000, 500), "white").save(images_dir / filename, "JPEG")
    payload: dict[str, object] = {
        "date": "2026-02-10",
        "updated": "2026/02/10 07:22:32",
        "updated_at": 1739145752000,
        "images": {"moyuren": filename},
    }
    if variants is not None:
        payload["image_variants"] = {"moyuren": variants}
    (data_dir / "2026-02-10.json").write_text(json.dumps(payload))
    render_config = TemplateRenderConfig(
        output_formats=["jpeg", "webp"], output_widths=[320, 640]
    )
    templates_config = SimpleNamespace(
        config=render_config, items=[SimpleNamespace(name="moyuren")]
    )
    config = SimpleNamespace(
        paths=SimpleNamespace(cache_dir=str(cache_dir)),
        server=SimpleNamespace(base_domain="https://example.com"),
        get_templates_config=lambda: templates_config,
    )
    app.state.config = config
    app.state.logger = logging.getLogger("test.api.variants")
    return Request({"type": "http", "app": app, "headers": []})


async def _call(
    request: Request,
    *,
    encode: str = "image",
    format: Literal["jpeg", "webp"] | None = None,
    width: int | None = None,
    detail: bool = False,
) -> Response:
    return await moyuren_module.get_moyuren(
        request,
        date=None,
        encode=encode,
        template="moyuren",
        detail=detail,
        format=format,
        width=width,
    )


@pytest.mark.parametrize(
    ("requested", "expected"), [(300, 320), (321, 640), (900, 1000), (1200, 1000)]
)
async def test_width_selection_uses_nearest_at_least_then_largest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, requested: int, expected: int
) -> None:
    request = _request(tmp_path)
    generated: list[int | None] = []

    def fake_ensure(
        source: Path,
        config: object,
        format: Literal["jpeg", "webp"],
        width: int | None,
    ) -> ImageVariant:
        generated.append(width)
        filename = source.name if width is None else f"{source.stem}_w{width}.jpg"
        source.with_name(filename).write_bytes(b"variant")
        return ImageVariant(filename=filename, format=format, width=width or 1000)

    monkeypatch.setattr(moyuren_module, "ensure_variant", fake_ensure)
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))
    response = await _call(request, width=requested)

    assert isinstance(response, FileResponse)
    assert generated == ([] if expected == 1000 else [expected])
    assert response.headers["content-type"] == "image/jpeg"


async def test_exact_recorded_webp_and_url_response(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    variants = [
        {"filename": "moyuren_20260210_072232_w640.webp", "format": "webp", "width": 640}
    ]
    request = _request(tmp_path, variants=variants)
    image = Path(request.app.state.config.paths.cache_dir) / "images" / variants[0]["filename"]
    image.write_bytes(b"webp")
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))

    response = await _call(request, encode="url", format="webp", width=600)

    assert response.status_code == 200
    assert bytes(response.body).decode() == f"https://example.com/static/{image.name}"


async def test_recorded_widths_participate_in_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    variants = [
        {"filename": "moyuren_20260210_072232_w500.webp", "format": "webp", "width": 500}
    ]
    request = _request(tmp_path, variants=variants)
    request.app.state.config.get_templates_config().config.output_widths = [200]
    image = Path(request.app.state.config.paths.cache_dir) / "images" / variants[0]["filename"]
    image.write_bytes(b"webp")
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))

    response = await _call(request, format="webp", width=300)

    assert isinstance(response, FileResponse)
    assert Path(response.path).name == image.name


async def test_disabled_format_returns_400(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(tmp_path)
    request.app.state.config.get_templates_config().config.output_formats = ["jpeg"]
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))

    response = await _call(request, format="webp")

    assert response.status_code == 400
    assert json.loads(bytes(response.body))["error"]["code"] == "API_7003"


@pytest.mark.parametrize("recorded", [False, True])
async def test_missing_configured_variant_is_generated_lazily(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recorded: bool,
) -> None:
    request = _request(tmp_path, variants=[
        {"filename": "moyuren_20260210_072232_w640.webp", "format": "webp", "width": 640},
    ] if recorded else None)
    data_file = Path(request.app.state.config.paths.cache_dir) / "data" / "2026-02-10.json"
    original_data = data_file.read_bytes()
    calls: list[tuple[str, int | None]] = []

    def fake_ensure(
        source: Path,
        config: object,
        format: Literal["jpeg", "webp"],
        width: int | None,
    ) -> ImageVariant:
        calls.append((format, width))
        target = source.with_name(f"{source.stem}_w640.webp")
        target.write_bytes(b"webp")
        return ImageVariant(filename=target.name, format="webp", width=640)

    monkeypatch.setattr(moyuren_module, "ensure_variant", fake_ensure)
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))

    response = await _call(request, format="webp", width=500)

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/webp"
    assert calls == [("webp", 640)]
    assert data_file.read_bytes() == original_data


def test_openapi_exposes_variant_parameters(tmp_path: Path) -> None:
    """Publish the exact format choices and positive width constraint without HTTP clients."""
    request = _request(tmp_path)
    request.app.include_router(moyuren_module.router)
    parameters = request.app.openapi()["paths"]["/api/v1/moyuren"]["get"]["parameters"]
    schemas = {parameter["name"]: parameter["schema"] for parameter in parameters}
    assert "url" in schemas["encode"]["enum"]
    assert schemas["format"]["anyOf"][0]["enum"] == ["jpeg", "webp"]
    assert schemas["width"]["anyOf"][0]["exclusiveMinimum"] == 0


async def test_variant_etags_are_distinct_and_validate_independently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = _request(tmp_path)
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))
    jpeg = await _call(request)
    resized_jpeg = await _call(request, width=320)
    webp = await _call(request, format="webp")
    assert jpeg.headers["etag"] != webp.headers["etag"]
    assert jpeg.headers["etag"] != resized_jpeg.headers["etag"]

    cached_request = Request({
        "type": "http",
        "app": request.app,
        "headers": [(b"if-none-match", webp.headers["etag"].encode())],
    })
    cached_webp = await _call(cached_request, format="webp")
    uncached_jpeg = await _call(cached_request)
    assert cached_webp.status_code == 304
    assert uncached_jpeg.status_code == 200

    modified_request = Request({
        "type": "http",
        "app": request.app,
        "headers": [(b"if-modified-since", webp.headers["last-modified"].encode())],
    })
    assert (await _call(modified_request, format="webp")).status_code == 200


@pytest.mark.parametrize("detail", [False, True])
async def test_json_variants_use_absolute_image_urls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, detail: bool
) -> None:
    request = _request(tmp_path, variants=[
        {"filename": "moyuren_20260210_072232.jpg", "format": "jpeg", "width": 1000},
        {"filename": "moyuren_20260210_072232.webp", "format": "webp", "width": 1000},
    ])
    monkeypatch.setattr(moyuren_module, "today_business", lambda: date(2026, 2, 10))

    response = await _call(request, encode="json", detail=detail)
    payload = json.loads(bytes(response.body))

    assert payload["variants"] == [
        {"image": "https://example.com/static/moyuren_20260210_072232.jpg", "format": "jpeg", "width": 1000},
        {"image": "https://example.com/static/moyuren_20260210_072232.webp", "format": "webp", "width": 1000},
    ]
