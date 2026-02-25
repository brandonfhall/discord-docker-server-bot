FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
# Fix for "Not supported URL scheme http+docker" error with requests 2.32.0+
RUN pip install "requests<2.32.0"

COPY src/ ./src/

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "src.bot"]
