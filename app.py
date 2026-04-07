#!/usr/bin/env python3
"""
carousel-pdf / app.py
----------------------
POST /convert
  Body: { "html": "<full html string>" }
  Returns: PDF binary (application/pdf)
"""

import asyncio
import logging
import os
import tempfile
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
from PIL import Image
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VERSION = "v14"
SLIDE_WIDTH  = 1080
SLIDE_HEIGHT = 1350


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
    )
    with tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                     mode="w", encoding="utf-8") as f:
        f.write(html)
        tmp_path = f.name
    try:
        await page.goto(f"file://{tmp_path}", wait_until="networkidle", timeout=30000)
    finally:
        os.unlink(tmp_path)

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
        slide_count = await page.evaluate(
            "document.querySelectorAll('.slide').length"
        )
        logger.info(f"Slide count: {slide_count}")

        if not slide_count:
            body = await page.evaluate(
                "document.body.innerHTML.substring(0, 500)"
            )
            raise ValueError(f"No .slide elements found. Body: {body}")

        for i in range(slide_count):
            await page.evaluate(f"window.scrollTo(0, {i * SLIDE_HEIGHT})")
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
            png = await page.screenshot(
                clip={"x": 0, "y": 0, "width": SLIDE_WIDTH, "height": SLIDE_HEIGHT}
            )
            png_buffers.append(png)
            logger.info(f"Slide {i + 1}/{slide_count} captured ({len(png)} bytes)")
    finally:
        await browser.close()
        await pw.stop()

    # Convert PNGs to PDF using Pillow
    images = []
    for b in png_buffers:
        img = Image.open(io.BytesIO(b))
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        images.append(img)

    out = io.BytesIO()
    images[0].save(
        out,
        format="PDF",
        save_all=True,
        append_images=images[1:],
        resolution=144,
    )
    pdf_bytes = out.getvalue()
    logger.info(f"PDF built: {len(pdf_bytes)} bytes, {len(images)} pages")
    return pdf_bytes


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
        pdf_bytes = asyncio.run(html_to_pdf_bytes(html))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
        logger.exception("Conversion failed")
        return jsonify({"error": f"Conversion failed: {str(e)}"}), 500

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
            scroll_y = slide_index * SLIDE_HEIGHT
            await page.evaluate(f"window.scrollTo(0, {scroll_y})")
            await page.evaluate(
                "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
            )
            return await page.screenshot(
                clip={"x": 0, "y": 0, "width": SLIDE_WIDTH, "height": SLIDE_HEIGHT}
            )
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
