"""Tests for bundled font routing and template fallback links."""

import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.config import (
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    ViewportConfig,
)
from app.services.compute import DataComputer
from app.services.renderer import ImageRenderer


def _renderer(tmp_path: Path, fonts_dir: Path, **render_options: object) -> ImageRenderer:
    """Build a renderer whose local font bundle is isolated to a test directory."""
    return ImageRenderer(
        templates_config=TemplatesConfig(),
        images_dir=str(tmp_path / "images"),
        render_config=TemplateRenderConfig(
            local_fonts_dir=str(fonts_dir),
            allowed_resource_hosts=[],
            **render_options,
        ),
        logger=logging.getLogger(__name__),
    )


def _route(url: str, *, resource_type: str = "font", method: str = "GET") -> AsyncMock:
    """Build the subset of a Playwright route used by the font handler."""
    route = AsyncMock()
    route.request = MagicMock(url=url, resource_type=resource_type, method=method)
    return route


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "body", "resource_type", "content_type"),
    [
        ("fonts.css", b"@font-face {}", "stylesheet", "text/css; charset=utf-8"),
        ("Moyuren-Test.woff2", b"woff2-data", "font", "font/woff2"),
    ],
)
async def test_local_font_route_serves_bundle_before_allowlist_and_tracking(
    tmp_path: Path,
    name: str,
    body: bytes,
    resource_type: str,
    content_type: str,
) -> None:
    """Serve the virtual origin with CORS without fetching or tracking it remotely."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"@font-face {}")
    (fonts_dir / name).write_bytes(body)
    renderer = _renderer(tmp_path, fonts_dir)
    page = AsyncMock()
    font_requests = await renderer._install_resource_cache_routes(page)
    route = _route(
        f"https://fonts.moyuren.invalid/{name}", resource_type=resource_type
    )

    async def assert_not_pending(**_: object) -> None:
        assert not font_requests.pending

    route.fulfill.side_effect = assert_not_pending
    await page.route.call_args.args[1](route)

    route.fulfill.assert_awaited_once_with(
        status=200,
        content_type=content_type,
        headers={"Access-Control-Allow-Origin": "*"},
        body=body,
    )
    route.fetch.assert_not_awaited()
    route.abort.assert_not_awaited()
    route.continue_.assert_not_awaited()
    assert not font_requests.pending


@pytest.mark.asyncio
async def test_local_font_route_uses_per_renderer_memory_cache(tmp_path: Path) -> None:
    """Reuse the first local read for later requests in the renderer instance."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"initial")
    renderer = _renderer(tmp_path, fonts_dir)
    page = AsyncMock()
    await renderer._install_resource_cache_routes(page)
    handle_route = page.route.call_args.args[1]

    first = _route(
        "https://fonts.moyuren.invalid/fonts.css", resource_type="stylesheet"
    )
    await handle_route(first)
    (fonts_dir / "fonts.css").write_bytes(b"changed")
    second = _route(
        "https://fonts.moyuren.invalid/fonts.css", resource_type="stylesheet"
    )
    await handle_route(second)

    other_renderer = _renderer(tmp_path / "other", fonts_dir)
    other_page = AsyncMock()
    await other_renderer._install_resource_cache_routes(other_page)
    other = _route(
        "https://fonts.moyuren.invalid/fonts.css", resource_type="stylesheet"
    )
    await other_page.route.call_args.args[1](other)

    assert first.fulfill.await_args.kwargs["body"] == b"initial"
    assert second.fulfill.await_args.kwargs["body"] == b"initial"
    assert other.fulfill.await_args.kwargs["body"] == b"changed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "method"),
    [
        ("https://fonts.moyuren.invalid/..%2Fsecret.woff2", "GET"),
        ("https://fonts.moyuren.invalid/nested/font.woff2", "GET"),
        ("https://fonts.moyuren.invalid/font.ttf", "GET"),
        ("https://fonts.moyuren.invalid/missing.woff2", "GET"),
        ("https://fonts.moyuren.invalid/fonts.css", "POST"),
    ],
)
async def test_local_font_route_returns_404_for_invalid_requests(
    tmp_path: Path, url: str, method: str
) -> None:
    """Reject traversal, nested paths, unsupported files, misses, and non-GET requests."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"css")
    renderer = _renderer(tmp_path, fonts_dir)
    page = AsyncMock()
    await renderer._install_resource_cache_routes(page)
    route = _route(url, method=method)

    await page.route.call_args.args[1](route)

    route.fulfill.assert_awaited_once_with(
        status=404,
        headers={"Access-Control-Allow-Origin": "*"},
        body=b"",
    )
    route.fetch.assert_not_awaited()
    route.abort.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_font_route_rejects_symlink_escape(tmp_path: Path) -> None:
    """Do not serve a matching filename whose resolved target leaves the bundle."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"css")
    outside = tmp_path / "outside.woff2"
    outside.write_bytes(b"secret")
    (fonts_dir / "escape.woff2").symlink_to(outside)
    renderer = _renderer(tmp_path, fonts_dir)
    page = AsyncMock()
    await renderer._install_resource_cache_routes(page)
    route = _route("https://fonts.moyuren.invalid/escape.woff2")

    await page.route.call_args.args[1](route)

    assert route.fulfill.await_args.kwargs["status"] == 404
    route.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_font_route_remains_available_after_remote_fonts_stop(
    tmp_path: Path,
) -> None:
    """Keep serving bundled files after the remote font timeout aborts its queue."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"css")
    (fonts_dir / "Moyuren-Test.woff2").write_bytes(b"woff2-data")
    renderer = _renderer(tmp_path, fonts_dir)
    page = AsyncMock()
    font_requests = await renderer._install_resource_cache_routes(page)
    await font_requests.abort_pending()
    route = _route("https://fonts.moyuren.invalid/Moyuren-Test.woff2")

    await page.route.call_args.args[1](route)

    assert font_requests.stopped is True
    assert route.fulfill.await_args.kwargs["status"] == 200
    assert route.fulfill.await_args.kwargs["content_type"] == "font/woff2"
    assert route.fulfill.await_args.kwargs["body"] == b"woff2-data"
    route.abort.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("create_directory", [False, True])
@pytest.mark.parametrize("remote_font_fallback", [False, True])
async def test_incomplete_local_bundle_warns_once_and_uses_remote_fallback(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    create_directory: bool,
    remote_font_fallback: bool,
) -> None:
    """Report a missing bundle once per renderer and keep the virtual host offline."""
    fonts_dir = tmp_path / "missing-fonts"
    if create_directory:
        fonts_dir.mkdir()
    with caplog.at_level(logging.WARNING):
        renderer = _renderer(
            tmp_path, fonts_dir, remote_font_fallback=remote_font_fallback
        )
        page = AsyncMock()
        await renderer._install_resource_cache_routes(page)
        handle_route = page.route.call_args.args[1]
        first = _route(
            "https://fonts.moyuren.invalid/fonts.css", resource_type="stylesheet"
        )
        second = _route("https://fonts.moyuren.invalid/missing.woff2")
        await handle_route(first)
        await handle_route(second)

        templates_dir = Path(__file__).resolve().parents[2] / "templates"
        template_item = TemplateItemConfig(
            name="moyuren",
            path=str(templates_dir / "moyuren.html"),
            viewport=ViewportConfig(width=794, height=1123),
        )
        data = DataComputer().compute(
            {"news": {"data": {"date": "2026-03-12", "news": []}}}
        )
        html = renderer._render_template(data, template_item)

    assert caplog.text.count("Local font bundle missing at") == 1
    assert first.fulfill.await_args.kwargs["status"] == 404
    assert second.fulfill.await_args.kwargs["status"] == 404
    first.fetch.assert_not_awaited()
    second.fetch.assert_not_awaited()
    assert ("fonts.googleapis.cn" in html) is remote_font_fallback


@pytest.mark.parametrize("template_name", ["moyuren.html", "moyuren_cute.html"])
@pytest.mark.parametrize("remote_font_fallback", [False, True])
@pytest.mark.parametrize("use_china_cdn", [False, True])
def test_templates_use_local_fonts_with_optional_google_fallback(
    tmp_path: Path,
    template_name: str,
    remote_font_fallback: bool,
    use_china_cdn: bool,
) -> None:
    """Always load the bundle and emit the selected Google fallback only when enabled."""
    fonts_dir = tmp_path / "fonts"
    fonts_dir.mkdir()
    (fonts_dir / "fonts.css").write_bytes(b"css")
    templates_dir = Path(__file__).resolve().parents[2] / "templates"
    template_item = TemplateItemConfig(
        name=Path(template_name).stem,
        path=str(templates_dir / template_name),
        viewport=ViewportConfig(width=794, height=1123),
    )
    renderer = _renderer(
        tmp_path,
        fonts_dir,
        remote_font_fallback=remote_font_fallback,
        use_china_cdn=use_china_cdn,
    )
    data = DataComputer().compute(
        {"news": {"data": {"date": "2026-03-12", "news": []}}}
    )

    html = renderer._render_template(data, template_item)

    assert 'href="https://fonts.moyuren.invalid/fonts.css"' in html
    assert ("fonts.googleapis." in html) is remote_font_fallback
    if remote_font_fallback:
        expected_host = "fonts.googleapis.cn" if use_china_cdn else "fonts.googleapis.com"
        unexpected_host = "fonts.googleapis.com" if use_china_cdn else "fonts.googleapis.cn"
        assert expected_host in html
        assert unexpected_host not in html
    assert "LXGW+WenKai" not in html
