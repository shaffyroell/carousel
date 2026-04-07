FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt --break-system-packages

# Install Chromium for Playwright
RUN playwright install chromium

COPY app.py .

EXPOSE 8080

CMD ["python", "app.py"]
