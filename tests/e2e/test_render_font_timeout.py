"""Real-browser regression tests for stalled web font requests."""

import asyncio
import logging
from pathlib import Path
from typing import cast

import pytest
from playwright.async_api import async_playwright

from app.core.config import (
    TemplateItemConfig,
    TemplateRenderConfig,
    TemplatesConfig,
    ViewportConfig,
)
from app.services.browser import browser_manager
from app.services.renderer import ImageRenderer

pytestmark = pytest.mark.e2e


async def _chromium_is_installed() -> bool:
    """Return whether Playwright's configured Chromium executable exists."""
    playwright = await async_playwright().start()
    try:
        return Path(playwright.chromium.executable_path).is_file()
    finally:
        await playwright.stop()


@pytest.mark.asyncio
async def test_render_aborts_stalled_font_after_readiness_timeout(
    tmp_path: Path,
) -> None:
    """Render a JPEG after a requested font stalls indefinitely."""
    if not await _chromium_is_installed():
        pytest.skip("Playwright Chromium is not installed")

    font_requested = asyncio.Event()
    release_font = asyncio.Event()
    handler_tasks: set[asyncio.Task[None]] = set()
    font_handler_tasks: set[asyncio.Task[None]] = set()
    connections: set[asyncio.StreamWriter] = set()

    async def handle_request(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Serve CSS immediately while holding the font response open."""
        task = cast(asyncio.Task[None], asyncio.current_task())
        handler_tasks.add(task)
        connections.add(writer)
        try:
            try:
                request = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.IncompleteReadError, ConnectionError):
                return
            path = request.split(b" ", 2)[1]
            if path == b"/font.css":
                body = (
                    b'@font-face { font-family: "StalledFont"; '
                    b'src: url("/font.woff2") format("woff2"); } '
                    b'body { font-family: "StalledFont", sans-serif; }'
                )
                writer.write(
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/css\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Connection: close\r\n\r\n"
                    + body
                )
                await writer.drain()
                return
            if path == b"/font.woff2":
                font_handler_tasks.add(task)
                font_requested.set()
                await release_font.wait()
                return

            writer.write(
                b"HTTP/1.1 404 Not Found\r\n"
                b"Content-Length: 0\r\n"
                b"Connection: close\r\n\r\n"
            )
            await writer.drain()
        finally:
            connections.discard(writer)
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass

    server = await asyncio.start_server(handle_request, "127.0.0.1", 0)
    try:
        socket = server.sockets[0]
        port = int(socket.getsockname()[1])
        template_path = tmp_path / "font-timeout.html"
        template_path.write_text(
            "<!doctype html>"
            "<html><head>"
            f'<link rel="stylesheet" href="http://127.0.0.1:{port}/font.css">'
            '<script>void document.documentElement.offsetWidth; '
            'void document.fonts.load("16px StalledFont");</script>'
            "</head><body>Fallback text remains renderable</body></html>",
            encoding="utf-8",
        )
        templates_config = TemplatesConfig(
            default="font-timeout",
            items=[
                TemplateItemConfig(
                    name="font-timeout",
                    path=str(template_path),
                    viewport=ViewportConfig(width=480, height=240),
                )
            ],
        )
        render_config = TemplateRenderConfig(
            device_scale_factor=1,
            jpeg_quality=80,
            use_china_cdn=False,
            remote_resource_cache_enabled=False,
            # Only the font-readiness timeout is under test; allow slow page loads.
            page_load_timeout_sec=30.0,
            font_ready_timeout_sec=1.0,
            allowed_resource_hosts=["127.0.0.1"],
        )
        renderer = ImageRenderer(
            templates_config=templates_config,
            images_dir=str(tmp_path / "images"),
            render_config=render_config,
            logger=logging.getLogger(__name__),
        )
        await asyncio.wait_for(browser_manager.warmup(), timeout=60.0)
        # This timeout only bounds a regression hang; slow browser operations
        # must not determine whether stalled-font handling works.
        filename = await asyncio.wait_for(
            renderer.render({}, "font-timeout"), timeout=60.0
        )
        image = (renderer.images_dir / filename).read_bytes()

        assert font_requested.is_set()
        # Rendering completed while every requested font response was still
        # withheld. Only cleanup below can release these server handlers.
        assert not release_font.is_set()
        assert font_handler_tasks
        assert all(not task.done() for task in font_handler_tasks)
        assert image.startswith(b"\xff\xd8\xff")
        assert image.endswith(b"\xff\xd9")
    finally:
        release_font.set()
        server.close()
        await server.wait_closed()
        for connection in tuple(connections):
            connection.close()
        if handler_tasks:
            await asyncio.gather(*tuple(handler_tasks), return_exceptions=True)
        await browser_manager.shutdown()
