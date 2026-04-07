FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages

# Install Chromium for Playwright
RUN playwright install chromium

COPY app.py .

EXPOSE 8080

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 1 --timeout 120 app:app"]
