FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages

# Install Chromium for Playwright
RUN playwright install chromium

COPY app.py .

EXPOSE 8080

# --threads: a slow /convert render no longer blocks /health and other
#   requests on the single worker (that's why even GET / was returning 502).
# --max-requests: recycle the worker periodically so leaked Chromium/node
#   subprocesses and their memory can't accumulate until the box OOMs.
# --graceful-timeout: give an in-flight render a moment to unwind on recycle.
# WEB_CONCURRENCY stays 1 by default to bound peak Chromium memory; bump it
#   only on an instance with headroom (each worker can launch its own browser).
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-8080} --workers ${WEB_CONCURRENCY:-1} --threads ${WEB_THREADS:-4} --timeout 120 --graceful-timeout 30 --max-requests 40 --max-requests-jitter 10 app:app"]
