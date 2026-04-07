#!/usr/bin/env python3
"""
carousel-pdf / app.py
----------------------
POST /convert
  Body: { "html": "<full html string>" }
  Returns: PDF binary (application/pdf)
"""

import asyncio
import img2pdf
import logging
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VERSION = "v9"
SLIDE_WIDTH  = 1080
SLIDE_HEIGHT = 1350


async def html_to_pdf_bytes(html: str) -> bytes:
    png_buffers = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
        ])
        try:
            page = await browser.new_page(
                viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
                device_scale_factor=2
            )

            await page.set_content(html, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(500)  # allow layout/paint to finish

            slide_count = await page.evaluate("document.querySelectorAll('.slide').length")
            logger.info(f"Slide count via JS: {slide_count}")

            if not slide_count:
                body_html = await page.evaluate("document.body.innerHTML.substring(0, 500)")
                logger.error(f"No slides found. Body preview: {body_html}")
                raise ValueError(f"No .slide elements found. Body preview: {body_html}")

            slides = await page.query_selector_all(".slide")
            logger.info(f"Slides found via Playwright: {len(slides)}")

            for i, slide in enumerate(slides):
                # bounding_box() returns viewport-relative coords — unusable after
                # scrolling. Compute absolute Y directly from slide index instead.
                y = i * SLIDE_HEIGHT
                await page.evaluate(f"window.scrollTo(0, {y})")
                # Wait for two rAF cycles to guarantee Chromium has repainted
                await page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
                png_bytes = await slide.screenshot()
                png_buffers.append(png_bytes)
                logger.info(f"Slide {i+1} screenshotted at y={y}")
        finally:
            await browser.close()

    pdf_bytes = img2pdf.convert(
        [io.BytesIO(b) for b in png_buffers],
        layout_fun=img2pdf.get_fixed_dpi_layout_fun((144, 144))
    )

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
    logger.info(f"[{VERSION}] Received request. Content-Type: {content_type}, Body size: {len(request.data)} bytes")

    html = extract_html(request)
    has_slide = 'class="slide"' in html or "class='slide'" in html
    logger.info(f"HTML length: {len(html)}, Has .slide: {has_slide}")

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


@app.route("/debug", methods=["POST"])
def debug():
    html = extract_html(request)

    async def _debug(html):
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = await browser.new_page(
                    viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
                    device_scale_factor=2
                )
                await page.set_content(html, wait_until="domcontentloaded", timeout=30000)
                slide_count = await page.evaluate("document.querySelectorAll('.slide').length")
                body_preview = await page.evaluate("document.body.innerHTML.substring(0, 1000)")
                title = await page.title()
                return {"slide_count": slide_count, "body_preview": body_preview, "title": title}
            finally:
                await browser.close()

    try:
        result = asyncio.run(_debug(html))
        return jsonify({
            "version": VERSION,
            "html_length": len(html),
            "has_slide_class": 'class="slide"' in html or "class='slide'" in html,
            **result
        }), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": VERSION}), 200


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
