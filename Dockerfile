FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends ffmpeg nodejs \
    && rm -rf /var/lib/apt/lists/*

# Cloudflare WARP (free) via wireproxy + wgcf: routes yt-dlp through a
# Cloudflare IP so YouTube stops blocking Render's datacenter IP.
# wireproxy is a userspace WireGuard client (no NET_ADMIN / privileged).
# Binaries are fetched at build time; WARP device registration happens
# at container start (see start.sh) and is optional/fail-safe.
RUN curl -fsSL -o /tmp/wireproxy.tar.gz https://github.com/windtf/wireproxy/releases/download/v1.1.3/wireproxy_linux_amd64.tar.gz \
    && tar -xzf /tmp/wireproxy.tar.gz -C /usr/local/bin wireproxy \
    && chmod +x /usr/local/bin/wireproxy \
    && rm /tmp/wireproxy.tar.gz \
    && curl -fsSL -o /usr/local/bin/wgcf https://github.com/ViRb3/wgcf/releases/download/v2.2.29/wgcf_2.2.29_linux_amd64 \
    && chmod +x /usr/local/bin/wgcf

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY server.py pipeline.py transcribe.py avatar.png start.sh ./
RUN chmod +x start.sh

# Keep the transcription libraries single-threaded: the container sees
# the host's many cores and would otherwise spawn large thread pools
# (part of the 2026-11-07 OOM on Render Free's 512 MB).
ENV HOST=0.0.0.0 PORT=7860 AVATAR_PATH=/app/avatar.png \
    OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 TOKENIZERS_PARALLELISM=false
EXPOSE 7860
CMD ["./start.sh"]
