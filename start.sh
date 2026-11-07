#!/bin/bash
# Start script for cristian-clips-api on Render.
# Sets up Cloudflare WARP (free) as a SOCKS5 proxy so yt-dlp downloads
# exit through a Cloudflare IP instead of Render's datacenter IP, which
# YouTube blocks with "Sign in to confirm you're not a bot".
# WARP is OPTIONAL and fail-safe: if any step fails, the server still
# starts normally and the pipeline falls back to cookies.
set -u
cd /app

WARP_DIR="/tmp/warp"
mkdir -p "$WARP_DIR"

setup_warp() {
    # Already have a working proxy? (restart within same container)
    if [ -f "$WARP_DIR/wireproxy.conf" ] && [ -f "$WARP_DIR/wgcf-account.toml" ]; then
        echo "[warp] reusing existing WARP config"
    else
        echo "[warp] registering a new WARP device..."
        # --accept-tos: non-interactive (no prompt). Timeout so a hung
        # registration cannot block the server from starting.
        timeout 60 /usr/local/bin/wgcf register --accept-tos \
            --config "$WARP_DIR/wgcf-account.toml" >/tmp/warp-register.log 2>&1 \
            || { echo "[warp] register failed, continuing without WARP"; return 1; }
        echo "[warp] generating WireGuard profile..."
        timeout 30 /usr/local/bin/wgcf generate \
            --config "$WARP_DIR/wgcf-account.toml" >/tmp/warp-generate.log 2>&1 \
            || { echo "[warp] generate failed, continuing without WARP"; return 1; }
        # wgcf writes wgcf-profile.conf next to the account file.
        if [ ! -f "$WARP_DIR/wgcf-profile.conf" ]; then
            echo "[warp] profile missing, continuing without WARP"; return 1
        fi
        cp "$WARP_DIR/wgcf-profile.conf" "$WARP_DIR/wireproxy.conf"
        printf '\n[Socks5]\nBindAddress = 127.0.0.1:1080\n' >> "$WARP_DIR/wireproxy.conf"
        echo "[warp] config ready"
    fi
    # Validate the config before starting the proxy.
    /usr/local/bin/wireproxy -c "$WARP_DIR/wireproxy.conf" -n >/dev/null 2>&1 \
        || { echo "[warp] bad config, continuing without WARP"; return 1; }
    echo "[warp] starting wireproxy on 127.0.0.1:1080..."
    /usr/local/bin/wireproxy -c "$WARP_DIR/wireproxy.conf" -d -s >/tmp/warp-wireproxy.log 2>&1 \
        || { echo "[warp] wireproxy failed to start, continuing without WARP"; return 1; }
    # Wait for the SOCKS5 port to listen (max ~10 s).
    for i in $(seq 1 10); do
        if (exec 3<>/dev/tcp/127.0.0.1/1080) 2>/dev/null; then
            exec 3<&-; exec 3>&-
            echo "[warp] proxy is up on 127.0.0.1:1080"
            return 0
        fi
        sleep 1
    done
    echo "[warp] proxy did not come up, continuing without WARP"
    return 1
}

# Only attempt WARP when not explicitly disabled.
if [ "${WARP_ENABLED:-1}" = "1" ]; then
    setup_warp || true
else
    echo "[warp] disabled by WARP_ENABLED=0"
fi

echo "[server] starting cristian-clips-api..."
exec python3 server.py
