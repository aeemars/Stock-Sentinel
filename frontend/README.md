# Stock Sentinel — Frontend (Cloudflare Workers Edge)

This directory contains the Cloudflare Workers Edge Gateway, PWA static assets, and scheduled cron triggers for Stock Sentinel.

## Architecture

- **`worker.js`**: Edge reverse-proxy with caching, security headers, health endpoints (`/cdn-cgi/health`), and cron trigger automation (`/api/run-workflow`).
- **`wrangler.toml`**: Cloudflare Workers configuration with `[assets]` binding pointing to `./static`.
- **`static/`**: PWA manifest, service worker (`/sw.js`), icons, favicon, and `_headers`.

---

## Deployment Instructions

### 1. Configure Backend Origin
In `wrangler.toml`, ensure `BACKEND_ORIGIN` is pointed to your live Render backend URL:

```toml
[vars]
BACKEND_ORIGIN = "https://your-service-name.onrender.com"
```

### 2. Deploy to Cloudflare Workers
From this directory:

```bash
# Using npx
npx wrangler deploy

# Or using npm
npm run deploy
```

### 3. Set Edge Secrets (One-time)
If Cloudflare Workers triggers the automated intelligence workflow directly:

```bash
npx wrangler secret put CRON_SECRET
```
