FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && useradd --uid 10001 --create-home app
COPY app ./app
RUN mkdir -p /app/data && chown -R app:app /app
USER app
EXPOSE 43921
CMD ["uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "43921", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "127.0.0.1"]
