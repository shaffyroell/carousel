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

VERSION = "v4"
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

            # Use set_content instead of file:// to avoid temp file issues
            await page.set_content(html, wait_until="networkidle", timeout=30000)

            slide_count = await page.evaluate("document.querySelectorAll('.slide').length")
            logger.info(f"Slide count via JS: {slide_count}")

            slides = await page.query_selector_all(".slide")
            logger.info(f"Slides found via Playwright: {len(slides)}")

            if not slides:
                body_html = await page.evaluate("document.body.innerHTML.substring(0, 500)")
                logger.error(f"No slides found. Body preview: {body_html}")
                raise ValueError(f"No .slide elements found. Body preview: {body_html}")

            for i, slide in enumerate(slides):
                png_bytes = await slide.screenshot()
                png_buffers.append(png_bytes)
                logger.info(f"Slide {i+1} screenshotted")
        finally:
            await browser.close()

    pdf_bytes = img2pdf.convert(
        [io.BytesIO(b) for b in png_buffers],
        layout_fun=img2pdf.get_fixed_dpi_layout_fun((144, 144))
    )

    return pdf_bytes


@app.route("/convert", methods=["POST"])
def convert():
    content_type = request.content_type or ""
    logger.info(f"[{VERSION}] Received request. Content-Type: {content_type}, Body size: {len(request.data)} bytes")

    if "application/json" in content_type:
        data = request.get_json(force=True, silent=True)
        if data and "html" in data:
            html = data["html"]
        else:
            html = request.data.decode("utf-8")
    else:
        html = request.data.decode("utf-8")

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
    """Returns what Playwright actually sees — use to diagnose 422s."""
    content_type = request.content_type or ""
    if "application/json" in content_type:
        data = request.get_json(force=True, silent=True)
        html = data.get("html", "") if data else request.data.decode("utf-8")
    else:
        html = request.data.decode("utf-8")

    async def _debug(html):
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = await browser.new_page(
                    viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
                    device_scale_factor=2
                )
                await page.set_content(html, wait_until="networkidle", timeout=30000)
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
