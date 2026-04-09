FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

# 构建上下文为仓库根目录（见 docker-compose.yml）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY aso_core/ ./aso_core/
COPY aso-service/app/ ./app/

ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
