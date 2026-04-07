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
import tempfile
import logging
from pathlib import Path
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
import io

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

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

            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8") as f:
                    f.write(html)
                    tmp_path = Path(f.name)

                logger.info(f"Wrote HTML to {tmp_path} ({len(html)} chars)")

                await page.goto(f"file://{tmp_path.resolve()}")
                await page.wait_for_load_state("networkidle", timeout=20000)

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
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
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
    logger.info(f"Received request. Content-Type: {content_type}, Body size: {len(request.data)} bytes")

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
    logger.info(f"HTML preview (first 300 chars): {html[:300]}")

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
    return jsonify({"status": "ok"}), 200


if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
