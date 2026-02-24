FROM python:3.11-slim-bookworm

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

# Create a non-root user for safer runtime
RUN addgroup --system botgroup && adduser --system --ingroup botgroup botuser
RUN chown -R botuser:botgroup /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

USER botuser

CMD ["python", "-u", "src/bot.py"]
