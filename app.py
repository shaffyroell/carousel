#!/usr/bin/env python3
"""
carousel-pdf / app.py
----------------------
POST /convert
  Body: { "html": "<full html string>" }
  Returns: PDF binary (application/pdf)
"""

import asyncio
import base64
import logging
import os
import tempfile
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VERSION = "v19"
SLIDE_WIDTH  = 1080
SLIDE_HEIGHT = 1350

# Screenshot pixel density. At 2x each 1080x1350 slide is a 2160x2700 bitmap
# (~23 MB decoded in-browser); on a small (e.g. 512 MB) instance a multi-slide
# render plus Chromium itself can exceed the memory limit, the worker is
# OOM-killed mid-request, and the proxy returns 502. Native for a 1080px
# Instagram target is 1x, so 2 is oversampling — drop SLIDE_SCALE to 1 if the
# service is memory-constrained. Env-configurable so it needs no code change.
DEVICE_SCALE = float(os.environ.get("SLIDE_SCALE", "2"))


CHROMIUM_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-web-security",
    "--allow-file-access-from-files",
]


async def render_page(html: str):
    """Launch browser, load HTML, return (page, browser, playwright) — caller must close."""
    p = await async_playwright().start()
    browser = await p.chromium.launch(args=CHROMIUM_ARGS)
    page = await browser.new_page(
        viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
        device_scale_factor=DEVICE_SCALE,
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                     mode="w", encoding="utf-8") as f:
        f.write(html)
        tmp_path = f.name
    try:
        # "load" (not "networkidle") so a single dangling external request
        # — a webfont/image that never settles — can't hang the worker for
        # the full timeout and take the (single-worker) service down with it.
        await page.goto(f"file://{tmp_path}", wait_until="load", timeout=30000)
    finally:
        os.unlink(tmp_path)

    # Wait for webfonts so text isn't captured in a fallback face.
    try:
        await page.evaluate(
            "() => (document.fonts && document.fonts.ready) "
            "? document.fonts.ready.then(() => true) : true"
        )
    except Exception:
        pass

    # Log page dimensions for debugging
    dims = await page.evaluate(
        "() => ({scrollH: document.documentElement.scrollHeight, "
        "scrollW: document.documentElement.scrollWidth, "
        "bgColor: document.body ? getComputedStyle(document.body).backgroundColor : 'n/a', "
        "slides: document.querySelectorAll('.slide').length})"
    )
    logger.info(f"Page dims: {dims}")
    return page, browser, p


async def html_to_pdf_bytes(html: str) -> bytes:
    page, browser, pw = await render_page(html)
    png_buffers = []
    try:
        slides = await page.query_selector_all(".slide")
        slide_count = len(slides)
        logger.info(f"Slide count: {slide_count}")

        if not slide_count:
            body = await page.evaluate(
                "document.body.innerHTML.substring(0, 500)"
            )
            raise ValueError(f"No .slide elements found. Body: {body}")

        # Screenshot each slide by its real bounding box. Scrolling to a
        # fixed i*SLIDE_HEIGHT and clipping a viewport rectangle only lines
        # up when slides are perfectly stacked with no body margin, no gaps
        # between slides, and every slide is exactly SLIDE_HEIGHT tall — any
        # real-world spacing makes later slides drift and bleed into each
        # other. An element screenshot is immune to all of that.
        for i, slide in enumerate(slides):
            await slide.scroll_into_view_if_needed()
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
            png = await slide.screenshot()
            png_buffers.append(png)
            logger.info(f"Slide {i + 1}/{slide_count} captured ({len(png)} bytes)")
    finally:
        await browser.close()
        await pw.stop()

    # Build PDF with reportlab — one image per page, exact dimensions
    out = io.BytesIO()
    c = canvas.Canvas(out, pagesize=(SLIDE_WIDTH, SLIDE_HEIGHT))
    for i, b in enumerate(png_buffers):
        c.drawImage(
            ImageReader(io.BytesIO(b)),
            0, 0,
            width=SLIDE_WIDTH,
            height=SLIDE_HEIGHT,
        )
        c.showPage()
    c.save()
    pdf_bytes = out.getvalue()
    logger.info(f"PDF built: {len(pdf_bytes)} bytes, {len(png_buffers)} pages")
    return pdf_bytes, len(png_buffers)


def extract_html(request):
    content_type = request.content_type or ""
    if "application/json" in content_type:
        data = request.get_json(force=True, silent=True)
        if data and "html" in data:
            return data["html"]
    return request.data.decode("utf-8")


@app.route("/convert", methods=["POST"])
def convert():
    content_type = request.content_type or ""
    logger.info(f"[{VERSION}] Content-Type: {content_type}, Body: {len(request.data)} bytes")

    html = extract_html(request)
    has_slide = 'class="slide"' in html or 'slide slide-' in html
    logger.info(f"HTML length: {len(html)}, has slide: {has_slide}")

    if not html or len(html) < 50:
        return jsonify({"error": "Empty or missing HTML"}), 400

    try:
        pdf_bytes, slide_count = asyncio.run(html_to_pdf_bytes(html))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        logger.exception("Conversion failed")
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500

    # ?b64=1 returns JSON with base64-encoded PDF — safe for n8n binary handling
    if request.args.get("b64") == "1":
        return jsonify({
            "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            "pages": slide_count,
        })

    return send_file(
        io.BytesIO(pdf_bytes),
        mimetype="application/pdf",
        as_attachment=True,
        download_name="carousel.pdf"
    )


@app.route("/screenshot", methods=["POST"])
def screenshot_endpoint():
    """Diagnostic: returns a PNG of the first slide so you can see what Playwright renders."""
    html = extract_html(request)
    if not html or len(html) < 50:
        return jsonify({"error": "Empty or missing HTML"}), 400

    slide_index = int(request.args.get("slide", 0))
    full_page = request.args.get("full", "0") == "1"

    async def _shot():
        page, browser, pw = await render_page(html)
        try:
            if full_page:
                return await page.screenshot(full_page=True)
            slides = await page.query_selector_all(".slide")
            if not slides:
                raise ValueError("No .slide elements found")
            slide = slides[min(slide_index, len(slides) - 1)]
            await slide.scroll_into_view_if_needed()
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
            return await slide.screenshot()
        finally:
            await browser.close()
            await pw.stop()

    try:
        png = asyncio.run(_shot())
        return send_file(io.BytesIO(png), mimetype="image/png")
    except Exception as e:
        logger.exception("Screenshot failed")
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": VERSION}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
