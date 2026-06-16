# ============================================================
# Stage 1: Build nginx with RTMP module
# Uses plain alpine (no Python needed for compilation)
# ============================================================
FROM alpine:3.21 AS nginx-builder

ARG NGINX_VER=1.27.4
ARG RTMP_VER=1.2.2

RUN apk update && apk add --no-cache \
  wget make pcre-dev openssl-dev zlib-dev gcc musl-dev

# Cache mount persists nginx source archives across rebuilds
RUN --mount=type=cache,target=/var/cache/nginx \
  wget -nc https://nginx.org/download/nginx-${NGINX_VER}.tar.gz -P /var/cache/nginx && \
  wget -nc https://github.com/arut/nginx-rtmp-module/archive/v${RTMP_VER}.tar.gz -P /var/cache/nginx && \
  tar xzf /var/cache/nginx/nginx-${NGINX_VER}.tar.gz -C /tmp && \
  tar xzf /var/cache/nginx/v${RTMP_VER}.tar.gz -C /tmp && \
  cd /tmp/nginx-${NGINX_VER} && \
  ./configure \
    --add-module=/tmp/nginx-rtmp-module-${RTMP_VER} \
    --conf-path=/etc/nginx/nginx.conf \
    --error-log-path=/var/log/nginx/error.log \
    --http-log-path=/var/log/nginx/access.log \
    --with-http_ssl_module && \
  make -j$(nproc) && \
  make install

# ============================================================
# Stage 2: Install Python dependencies into .venv
# ============================================================
FROM python:3.13-alpine AS python-builder

ARG APP_WORKDIR=/iptv-api

WORKDIR $APP_WORKDIR

COPY Pipfile Pipfile.lock ./

RUN apk add --no-cache gcc musl-dev python3-dev libffi-dev zlib-dev jpeg-dev

# Cache mount saves pip download cache between builds
RUN --mount=type=cache,target=/root/.cache/pip \
  pip install pipenv && \
  PIPENV_VENV_IN_PROJECT=1 pipenv install --deploy

# ============================================================
# Stage 3: Final runtime image
# ============================================================
FROM python:3.13-alpine

ARG APP_WORKDIR=/iptv-api

ENV APP_WORKDIR=$APP_WORKDIR \
    APP_PORT=5180 \
    NGINX_HTTP_PORT=8080 \
    NGINX_RTMP_PORT=1935 \
    PUBLIC_PORT=80 \
    PATH="$APP_WORKDIR/.venv/bin:/usr/local/nginx/sbin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8

WORKDIR $APP_WORKDIR

# Runtime system dependencies (changes rarely → early for cache)
RUN apk add --no-cache ffmpeg pcre

# Nginx log setup
RUN mkdir -p /var/log/nginx /usr/local/nginx/html && \
    ln -sf /dev/stdout /var/log/nginx/access.log && \
    ln -sf /dev/stderr /var/log/nginx/error.log

# Build artifacts from previous stages
COPY --from=nginx-builder /usr/local/nginx /usr/local/nginx
COPY --from=python-builder $APP_WORKDIR/.venv $APP_WORKDIR/.venv

# Application source code (explicit paths for cache efficiency)
COPY main.py favicon.ico version.json ./
COPY utils/ utils/
COPY service/ service/
COPY updates/ updates/
COPY locales/ locales/
COPY config/ config/

# Default config templates (copied to separate dir, used on first run)
COPY config /iptv-api-config
COPY entrypoint.sh /iptv-api-entrypoint.sh
COPY nginx.conf.template /etc/nginx/nginx.conf.template
COPY stat.xsl /usr/local/nginx/html/stat.xsl

RUN chmod +x /iptv-api-entrypoint.sh

EXPOSE $NGINX_HTTP_PORT

ENTRYPOINT ["/iptv-api-entrypoint.sh"]
