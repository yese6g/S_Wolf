# SilverWolf 容器镜像（可选，构建时需要联网拉取 Python 依赖）
# 日常本地使用推荐直接运行 .venv 环境，无需 Docker
FROM python:3.10-slim

WORKDIR /app

# 安装依赖（注意：sentence-transformers 体积较大，可注释掉以减小镜像）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝整个项目（修复 v0.4.0 之前只拷贝 main.py 导致模块缺失的问题）
COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
