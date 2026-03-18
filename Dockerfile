FROM python:3.13-slim

LABEL maintainer="icerui"
LABEL description="gitinstall - 让你轻松安装 GitHub 项目"

# 安装常用构建工具（支持安装各类项目）
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl wget build-essential && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -e .

# 数据持久化
VOLUME /root/.gitinstall

# 默认端口
EXPOSE 8080

# 生产环境：监听所有接口
ENV GITINSTALL_HOST=0.0.0.0

CMD ["gitinstall", "web", "--host", "0.0.0.0", "--no-open"]
