# Logic-Coloc —— 云托管（容器）部署镜像
# 构建上下文 = 本仓库根目录本身，直接 `docker build -t logic-coloc .` 即可。
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# 依赖单独一层：以后只改业务代码时不会重装依赖，构建快很多。
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# ⚠️ 关键的一步，不要改：
# 本仓库目录本身就是一个 Python 包（模块间用相对导入 `from . import …`），
# 它不是项目根。所以必须把整个仓库放到 /app/logic_coloc 这一层，
# 让 /app 成为包的父目录，`uvicorn logic_coloc.api:app` 才解析得到。
WORKDIR /app
COPY . /app/logic_coloc

# 让包可被 import（也对应 config.py 里 _load_dotenv 找「上一级目录 .env」的行为，
# 线上不需要 .env，环境变量由平台注入）。
ENV PYTHONPATH=/app

EXPOSE 8000

# 平台会注入 $PORT，所以端口取 ${PORT:-8000}。
# --proxy-headers + --forwarded-allow-ips：让 request.base_url 拿到公网域名和 https，
# 否则 /api/upload 返回的文件 URL 会拼成容器内网地址。
CMD ["sh", "-c", "uvicorn logic_coloc.api:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
