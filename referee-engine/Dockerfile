FROM python:3.12-slim AS builder

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim

WORKDIR /app

# 安装必要的系统依赖（含 docker CLI — referee 用 `docker exec/cp` 跟 agent 容器对话）
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    curl \
    openssh-client \
    tzdata \
    docker-cli \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

ENV TZ=Asia/Shanghai

# Cache-bust marker (bump when you need to force COPY rebuild):
ARG SOURCE_REV=2026-06-11-r1

COPY . .

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
