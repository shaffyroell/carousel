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
import os
import tempfile
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

VERSION = "v11"
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

            # Write to temp file and load via file:// — this does a full browser
            # page load, guaranteeing inline CSS is applied before we screenshot.
            with tempfile.NamedTemporaryFile(suffix=".html", delete=False,
                                             mode="w", encoding="utf-8") as f:
                f.write(html)
                tmp_path = f.name

            try:
                await page.goto(f"file://{tmp_path}")
                await page.wait_for_load_state("load", timeout=30000)

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
                    # Scroll to exact slide position, wait for repaint, screenshot viewport
                    await page.evaluate(f"window.scrollTo(0, {i * SLIDE_HEIGHT})")
                    await page.evaluate(
                        "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                    )
                    png = await page.screenshot(clip={
                        "x": 0, "y": 0,
                        "width": SLIDE_WIDTH, "height": SLIDE_HEIGHT,
                    })
                    png_buffers.append(png)
                    logger.info(f"Slide {i + 1}/{slide_count} screenshotted")
            finally:
                os.unlink(tmp_path)
        finally:
            await browser.close()

    return img2pdf.convert(
        [io.BytesIO(b) for b in png_buffers],
        layout_fun=img2pdf.get_fixed_dpi_layout_fun((144, 144))
    )


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
    logger.info(f"HTML length: {len(html)}, has slide: {'class=\"slide\"' in html or 'slide slide-' in html}")

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


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "version": VERSION}), 200


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
