# Cloudflare Deployment Guide — Stock Sentinel (Workers & Pages)

This guide walks you through deploying Stock Sentinel with **Cloudflare Workers** and **Cloudflare Pages**, delivering ultra-low-latency global CDN caching, automated edge cron triggers, and zero-trust edge protection.

---

## 1. Architecture Overview

Stock Sentinel is architected as an ASGI FastAPI Python application with PostgreSQL persistence and an installable PWA frontend. When deployed on Cloudflare:

```
                                      ┌─────────────────────────────────────────────────────────┐
                                      │              Cloudflare Global Edge (Anycast)           │
                                      │                                                         │
[Browser / Mobile PWA] ───────────────┼──► [Cloudflare Pages / Edge CDN]                       │
                                      │     - Static Assets (/static/*) 1y immutable cache      │
                                      │     - PWA Shell (/manifest.webmanifest, /sw.js)         │
                                      │     - Edge Security Headers (_headers)                  │
                                      │                                                         │
                                      │──► [Cloudflare Worker (worker.js)]                      │
                                      │     - Reverse Proxy & SSL Termination                   │
                                      │     - Microsecond Health Check (/cdn-cgi/health)        │
                                      │     - Scheduled Cron Trigger (20:30 UTC Mon-Fri)        │
                                      └───────────────────────────┬─────────────────────────────┘
                                                                  │
                                                HTTPS Proxy / Cloudflare Tunnel
                                                                  │
                                                                  ▼
                                      ┌─────────────────────────────────────────────────────────┐
                                      │              Stock Sentinel Application Origin          │
                                      │                                                         │
                                      │   FastAPI + Uvicorn (Container / VPS / Cloud Host)      │
                                      │   Neon / Supabase PostgreSQL Database                   │
                                      └─────────────────────────────────────────────────────────┘
```

---

## 2. Prerequisites

1. **Cloudflare Account** (Free tier is fully supported).
2. **Wrangler CLI** (Cloudflare's official developer tool):
   ```bash
   npm install -g wrangler
   # Or run via npx without global install:
   npx wrangler --version
   ```
3. Authenticate Wrangler with your Cloudflare account:
   ```bash
   npx wrangler login
   ```

---

## 3. Deployment Methods

### Method 1: Cloudflare Worker Gateway + Cloud Backend (Recommended Production Setup)

This method deploys the FastAPI backend on your chosen container host (Render, Fly.io, Railway, Hetzner, AWS) or VPS, while Cloudflare Workers serves as the **global edge proxy, static CDN, and automated Cron Trigger scheduler**.

#### Step 1: Deploy Backend Container
Use the included production [Dockerfile](file:///home/aeemars/Downloads/Stock-Sentinel/Dockerfile):
```bash
# Build and run locally or on your server
docker build -t stock-sentinel .
docker run -d -p 5000:5000 \
  -e NEON_DATABASE_URL="postgresql://user:pass@ep-xyz.neon.tech/stocks_db?sslmode=require" \
  -e SECRET_KEY="your-32-byte-secret-key" \
  -e CRON_SECRET="your-generated-cron-secret" \
  -e ENVIRONMENT="cloudflare" \
  stock-sentinel
```

#### Step 2: Configure `wrangler.toml`
Edit [`wrangler.toml`](file:///home/aeemars/Downloads/Stock-Sentinel/wrangler.toml):
```toml
name = "stock-sentinel"
main = "worker.js"
compatibility_date = "2024-09-23"
compatibility_flags = ["nodejs_compat"]

[assets]
directory = "./static"
binding = "ASSETS"

[triggers]
crons = ["30 20 * * 1-5"]

[vars]
ENVIRONMENT = "cloudflare"
BACKEND_ORIGIN = "https://your-backend-app.example.com"
```

#### Step 3: Register Cloudflare Secrets
Store sensitive credentials in Cloudflare's encrypted key-value store:
```bash
# Secret token to authenticate automated market close scans
npx wrangler secret put CRON_SECRET

# Session signing key
npx wrangler secret put SECRET_KEY

# OpenRouter AI key (optional)
npx wrangler secret put OPENROUTER_API_KEY
```

#### Step 4: Deploy Worker & Static Assets
```bash
npx wrangler deploy
```

Once complete, your worker will be live at `https://stock-sentinel.<your-subdomain>.workers.dev` (or on your custom domain).

---

### Method 2: Cloudflare Tunnel (`cloudflared`) Direct Zero-Trust Setup

Cloudflare Tunnel lets you route traffic from Cloudflare's edge directly into a local server, home lab, or private VPS **without opening inbound firewall ports or configuring public IP addresses**.

#### Step 1: Install `cloudflared`
```bash
# Ubuntu / Debian
curl -L --output cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
sudo dpkg -i cloudflared.deb
```

#### Step 2: Authenticate and Create Tunnel
```bash
# Login to Cloudflare
cloudflared tunnel login

# Create a named tunnel
cloudflared tunnel create stock-sentinel

# Route your DNS hostname to the tunnel
cloudflared tunnel route dns stock-sentinel stocks.yourdomain.com
```

#### Step 3: Configure and Start Tunnel
Copy [cloudflare-tunnel.example.yml](file:///home/aeemars/Downloads/Stock-Sentinel/cloudflare-tunnel.example.yml) to `~/.cloudflared/config.yml` with your Tunnel UUID:
```yaml
tunnel: <YOUR-TUNNEL-UUID>
credentials-file: /root/.cloudflared/<YOUR-TUNNEL-UUID>.json

ingress:
  - hostname: stocks.yourdomain.com
    service: http://127.0.0.1:5000
  - service: http_status:404
```

Start the tunnel service:
```bash
cloudflared tunnel run stock-sentinel
```

---

### Method 3: Cloudflare Pages Direct Git Integration

If you prefer using the Cloudflare Dashboard to deploy the static shell:

1. Go to **Cloudflare Dashboard** → **Workers & Pages** → **Create application** → **Pages** → **Connect to Git**.
2. Select the `Stock-Sentinel` repository.
3. Configure build settings:
   - **Framework preset**: None
   - **Build command**: *(leave blank or `echo "Static assets ready"`)*
   - **Build output directory**: `static`
   - **Root directory**: `/`
4. Cloudflare Pages automatically detects and enforces [`static/_headers`](file:///home/aeemars/Downloads/Stock-Sentinel/static/_headers) for 1-year immutable caching on icons, and correct MIME types for [`static/manifest.webmanifest`](file:///home/aeemars/Downloads/Stock-Sentinel/static/manifest.webmanifest) and [`static/sw.js`](file:///home/aeemars/Downloads/Stock-Sentinel/static/sw.js).

---

## 4. Testing & Edge Verification

### 1. Test Edge Health Check
Verify the worker is active without hitting the database:
```bash
curl -i https://<your-worker-or-domain>/cdn-cgi/health
```
Expected output:
```json
{
  "status": "ok",
  "service": "Stock Sentinel Cloudflare Worker Edge",
  "colo": "LHR",
  "country": "GB",
  "timestamp": "2026-10-21T14:20:00.000Z"
}
```

### 2. Test Automated Cron Trigger Manually
Simulate the scheduled market close intelligence trigger locally or on Cloudflare:
```bash
npx wrangler trigger cron
```
Cloudflare Workers will execute `worker.js:scheduled()`, sending an authenticated `POST /api/run-workflow` request with bearer token `CRON_SECRET`.

### 3. Verify Edge Headers & PWA Caching
```bash
# Check manifest MIME type
curl -I https://<your-domain>/manifest.webmanifest

# Check service worker scope & no-cache headers
curl -I https://<your-domain>/sw.js
```
The response should include:
- `Content-Type: application/manifest+json; charset=utf-8` (for manifest)
- `Service-Worker-Allowed: /` (for service worker)
- `Cache-Control: no-cache, no-store, must-revalidate` (for service worker)
- `X-Edge-Served-By: Cloudflare-Worker`

---

## 5. Summary of Cloudflare Configuration Files

| File | Purpose |
|------|---------|
| [`wrangler.toml`](file:///home/aeemars/Downloads/Stock-Sentinel/wrangler.toml) | Primary Cloudflare Worker configuration, asset bindings, and cron triggers |
| [`worker.js`](file:///home/aeemars/Downloads/Stock-Sentinel/worker.js) | Cloudflare Worker edge gateway script, reverse proxy, and scheduled trigger handler |
| [`_headers`](file:///home/aeemars/Downloads/Stock-Sentinel/_headers) | Cloudflare Pages & CDN edge security and caching headers |
| [`_routes.json`](file:///home/aeemars/Downloads/Stock-Sentinel/_routes.json) | Cloudflare Pages routing rule matrix |
| [`_redirects`](file:///home/aeemars/Downloads/Stock-Sentinel/_redirects) | Cloudflare Pages edge redirects |
| [`Dockerfile`](file:///home/aeemars/Downloads/Stock-Sentinel/Dockerfile) | Lean, multi-stage production container image for Uvicorn |
| [`.dockerignore`](file:///home/aeemars/Downloads/Stock-Sentinel/.dockerignore) | Docker build exclusions |
| [`cloudflare-tunnel.example.yml`](file:///home/aeemars/Downloads/Stock-Sentinel/cloudflare-tunnel.example.yml) | Reference configuration for Cloudflare Tunnel (`cloudflared`) |
