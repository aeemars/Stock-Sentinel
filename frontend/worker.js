/**
 * Stock Sentinel — Cloudflare Worker Edge Gateway & Cron Trigger Scheduler
 *
 * Responsibilities:
 * 1. Edge Static Asset Serving & Caching (PWA Manifest, Icons, Service Worker)
 * 2. Automated Scheduled Cron Triggers (Triggers /api/run-workflow on market close)
 * 3. Intelligent Reverse Proxy & Edge TLS / Security Headers
 * 4. Microsecond Edge Health & Telemetry Endpoint (/cdn-cgi/health)
 */

export default {
  /**
   * Handle incoming HTTP requests
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // 1. Edge Health & Diagnostics Endpoint
    if (url.pathname === '/cdn-cgi/health' || url.pathname === '/health') {
      return new Response(JSON.stringify({
        status: 'ok',
        service: 'Stock Sentinel Cloudflare Worker Edge',
        colo: request.cf?.colo || 'UNKNOWN',
        country: request.cf?.country || 'UNKNOWN',
        timestamp: new Date().toISOString()
      }, null, 2), {
        status: 200,
        headers: {
          'Content-Type': 'application/json; charset=utf-8',
          'Cache-Control': 'no-cache, no-store, must-revalidate',
          'Access-Control-Allow-Origin': '*'
        }
      });
    }

    // 2. Static Asset Handling via Cloudflare Workers Assets binding
    const isStaticAsset = (
      url.pathname.startsWith('/static/') ||
      url.pathname === '/manifest.webmanifest' ||
      url.pathname === '/sw.js' ||
      url.pathname === '/favicon.svg' ||
      url.pathname === '/favicon.ico'
    );

    if (isStaticAsset && env.ASSETS) {
      try {
        const assetResponse = await env.ASSETS.fetch(request);
        if (assetResponse.status < 400) {
          const responseHeaders = new Headers(assetResponse.headers);

          // Apply specific PWA headers
          if (url.pathname === '/sw.js') {
            responseHeaders.set('Service-Worker-Allowed', '/');
            responseHeaders.set('Cache-Control', 'no-cache, no-store, must-revalidate');
            responseHeaders.set('Content-Type', 'application/javascript; charset=utf-8');
          } else if (url.pathname === '/manifest.webmanifest') {
            responseHeaders.set('Content-Type', 'application/manifest+json; charset=utf-8');
            responseHeaders.set('Cache-Control', 'public, max-age=86400');
          } else if (url.pathname.startsWith('/static/')) {
            responseHeaders.set('Cache-Control', 'public, max-age=31536000, immutable');
          }

          return new Response(assetResponse.body, {
            status: assetResponse.status,
            statusText: assetResponse.statusText,
            headers: responseHeaders
          });
        }
      } catch (err) {
        console.warn('[Cloudflare Worker] Asset fetch error, falling back to origin:', err);
      }
    }

    // 3. Reverse Proxy to FastAPI Backend Origin
    const origin = env.BACKEND_ORIGIN || 'http://127.0.0.1:5000';
    const targetUrl = new URL(url.pathname + url.search, origin);

    // Forward existing request headers with edge client IP and TLS info
    const forwardHeaders = new Headers(request.headers);
    forwardHeaders.set('X-Forwarded-Host', url.host);
    forwardHeaders.set('X-Forwarded-Proto', 'https');
    if (request.headers.get('cf-connecting-ip')) {
      forwardHeaders.set('X-Real-IP', request.headers.get('cf-connecting-ip'));
      forwardHeaders.set('X-Forwarded-For', request.headers.get('cf-connecting-ip'));
    }

    const modifiedRequest = new Request(targetUrl.toString(), {
      method: request.method,
      headers: forwardHeaders,
      body: ['GET', 'HEAD'].includes(request.method) ? undefined : request.body,
      redirect: 'manual'
    });

    try {
      const response = await fetch(modifiedRequest);

      // Clone and append Edge security headers
      const edgeHeaders = new Headers(response.headers);
      edgeHeaders.set('X-Edge-Served-By', 'Cloudflare-Worker');
      edgeHeaders.set('X-Edge-Colo', request.cf?.colo || 'UNKNOWN');

      return new Response(response.body, {
        status: response.status,
        statusText: response.statusText,
        headers: edgeHeaders
      });
    } catch (error) {
      console.error('[Cloudflare Worker] Origin proxy error:', error);
      return new Response(
        `<!DOCTYPE html>
        <html class="dark" lang="en">
        <head>
          <meta charset="utf-8"/>
          <title>Origin Gateway Error — Stock Sentinel</title>
          <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
          <style>
            body { background: #10131a; color: #e1e2ec; font-family: system-ui, -apple-system, sans-serif; display: flex; align-items: center; justify-content: center; min-height: 100vh; margin: 0; padding: 20px; }
            .card { max-width: 480px; width: 100%; background: #1d2027; border: 1px solid #424754; border-radius: 16px; padding: 32px; text-align: center; box-shadow: 0 20px 40px rgba(0,0,0,0.4); }
            h1 { font-size: 24px; margin-bottom: 8px; color: #ffb4ab; }
            p { font-size: 14px; color: #c2c6d6; line-height: 1.6; }
            .badge { display: inline-block; padding: 4px 12px; background: rgba(255, 180, 171, 0.1); border: 1px solid rgba(255, 180, 171, 0.3); border-radius: 999px; font-size: 12px; font-family: monospace; color: #ffb4ab; margin-bottom: 16px; }
            button { margin-top: 20px; padding: 10px 24px; background: #adc6ff; color: #002e6a; border: none; border-radius: 8px; font-weight: 600; cursor: pointer; transition: opacity 0.2s; }
            button:hover { opacity: 0.9; }
          </style>
        </head>
        <body>
          <div class="card">
            <span class="badge">CLOUDFLARE EDGE ERROR 502</span>
            <h1>Backend Origin Unreachable</h1>
            <p>Stock Sentinel's Cloudflare Worker was unable to reach the upstream FastAPI origin. Please verify that your backend service is running and configured in <code>wrangler.toml</code>.</p>
            <button onclick="window.location.reload()">Retry Connection</button>
          </div>
        </body>
        </html>`,
        {
          status: 502,
          headers: {
            'Content-Type': 'text/html; charset=utf-8',
            'Cache-Control': 'no-cache, no-store, must-revalidate'
          }
        }
      );
    }
  },

  /**
   * Handle scheduled Cron Triggers:
   * 1. Market Close Scan: "30 20 * * 1-5" (Mon-Fri at 20:30 UTC) -> POST /api/run-workflow
   * 2. Render Keep-Alive: "*/14 * * * *" (Every 14m, 24/7) -> GET /health
   */
  async scheduled(event, env, ctx) {
    const origin = env.BACKEND_ORIGIN;
    if (!origin) {
      console.error('[Cloudflare Cron] BACKEND_ORIGIN environment variable is not defined.');
      return;
    }

    const cleanOrigin = origin.replace(/\/+$/, '');

    // Branch based on the firing cron trigger pattern
    if (event.cron === "30 20 * * 1-5") {
      console.log(`[Cloudflare Cron] Market close workflow trigger fired at ${new Date(event.scheduledTime).toISOString()}`);
      const workflowUrl = `${cleanOrigin}/api/run-workflow`;
      const cronSecret = env.CRON_SECRET;

      const headers = {
        'Content-Type': 'application/json',
        'X-Triggered-By': 'Cloudflare-Worker-Cron',
        'User-Agent': 'Cloudflare-Worker-Cron/1.0'
      };

      if (cronSecret) {
        headers['Authorization'] = `Bearer ${cronSecret}`;
      }

      ctx.waitUntil(
        fetch(workflowUrl, {
          method: 'POST',
          headers
        })
        .then(async (res) => {
          const text = await res.text();
          console.log(`[Cloudflare Cron] Run-workflow HTTP ${res.status}: ${text.slice(0, 200)}`);
        })
        .catch((err) => {
          console.error('[Cloudflare Cron] Error invoking /api/run-workflow:', err);
        })
      );
    } else {
      // Keep-Alive Ping (fires every 14 minutes to prevent Render free instance spin down)
      const healthUrl = `${cleanOrigin}/health`;
      ctx.waitUntil(
        fetch(healthUrl, {
          method: 'GET',
          headers: {
            'User-Agent': 'StockSentinel-KeepAlive/1.0',
            'X-Purpose': 'Render-KeepAlive'
          }
        })
        .then(async (res) => {
          console.log(`[KeepAlive] Pinged Render /health at ${new Date().toISOString()} - HTTP ${res.status}`);
        })
        .catch((err) => {
          console.warn('[KeepAlive] Warning: Failed to ping Render /health:', err.message || err);
        })
      );
    }
  }
};
