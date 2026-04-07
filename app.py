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
from pathlib import Path
from flask import Flask, request, send_file, jsonify
from playwright.async_api import async_playwright
import io

app = Flask(__name__)

SLIDE_WIDTH  = 1080
SLIDE_HEIGHT = 1350


async def html_to_pdf_bytes(html: str) -> bytes:
    png_buffers = []

    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(
            viewport={"width": SLIDE_WIDTH, "height": SLIDE_HEIGHT},
            device_scale_factor=2
        )

        # Write HTML to a temp file so local assets and fonts resolve correctly
        with tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w") as f:
            f.write(html)
            tmp_path = Path(f.name)

        await page.goto(f"file://{tmp_path.resolve()}")
        await page.wait_for_load_state("networkidle", timeout=20000)

        slides = await page.query_selector_all(".slide")

        if not slides:
            await browser.close()
            raise ValueError("No .slide elements found in HTML.")

        for i, slide in enumerate(slides):
            png_bytes = await slide.screenshot(
                clip={
                    "x": 0,
                    "y": i * SLIDE_HEIGHT,
                    "width": SLIDE_WIDTH,
                    "height": SLIDE_HEIGHT
                }
            )
            png_buffers.append(png_bytes)

        await browser.close()
        tmp_path.unlink(missing_ok=True)

    pdf_bytes = img2pdf.convert(
        png_buffers,
        layout_fun=img2pdf.get_fixed_dpi_layout_fun((144, 144))
    )

    return pdf_bytes


@app.route("/convert", methods=["POST"])
def convert():
    data = request.get_json(force=True)

    if not data or "html" not in data:
        return jsonify({"error": "Missing 'html' field in request body"}), 400

    html = data["html"]

    try:
        pdf_bytes = asyncio.run(html_to_pdf_bytes(html))
    except ValueError as e:
        return jsonify({"error": str(e)}), 422
    except Exception as e:
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
    app.run(host="0.0.0.0", port=8080)
