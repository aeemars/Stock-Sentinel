/* Stock Sentinel - Progressive Web App Service Worker */
const CACHE_NAME = 'stock-sentinel-v1.0.0';
const OFFLINE_URL = '/offline';

const PRECACHE_ASSETS = [
  '/offline',
  '/manifest.webmanifest',
  '/static/favicon.svg',
  '/static/icons/icon-192.png',
  '/static/icons/icon-512.png',
  '/static/icons/icon-maskable-192.png',
  '/static/icons/icon-maskable-512.png',
  '/static/icons/apple-touch-icon.png'
];

// Install Event: precache core offline shell
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => {
      return cache.addAll(PRECACHE_ASSETS);
    }).then(() => self.skipWaiting())
  );
});

// Activate Event: purge stale caches and claim clients immediately
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('[PWA SW] Removing outdated cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch Event
self.addEventListener('fetch', (event) => {
  const { request } = event;

  // Only handle HTTP GET requests
  if (request.method !== 'GET') {
    return;
  }

  const url = new URL(request.url);

  // Skip unsupported schemes (chrome-extension, etc.)
  if (!url.protocol.startsWith('http')) {
    return;
  }

  // 1. Navigation Requests (HTML documents)
  // Strategy: Network-First with Cache fallback, falling back to Offline Page
  if (request.mode === 'navigate') {
    event.respondWith(
      fetch(request)
        .then((networkResponse) => {
          // If valid 200 response, update cache in background
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => {
              cache.put(request, responseClone);
            });
          }
          return networkResponse;
        })
        .catch(async () => {
          // Fallback to cache for this specific URL if previously visited
          const cachedResponse = await caches.match(request);
          if (cachedResponse) {
            return cachedResponse;
          }
          // Otherwise serve dedicated offline fallback
          const offlinePage = await caches.match(OFFLINE_URL);
          return offlinePage || new Response('Offline — Stock Sentinel', {
            status: 503,
            headers: { 'Content-Type': 'text/plain' }
          });
        })
    );
    return;
  }

  // 2. Static Assets (Icons, images, external fonts & styles)
  // Strategy: Stale-While-Revalidate
  if (
    url.pathname.startsWith('/static/') ||
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com') ||
    url.hostname.includes('cdn.tailwindcss.com')
  ) {
    event.respondWith(
      caches.match(request).then((cachedResponse) => {
        const fetchPromise = fetch(request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const responseClone = networkResponse.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(request, responseClone));
          }
          return networkResponse;
        }).catch(() => {/* Ignore background fetch failures when offline */});

        return cachedResponse || fetchPromise;
      })
    );
    return;
  }

  // 3. API & Webhook routes (/api/*): Network only (live quotes / telemetry)
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(request));
    return;
  }
});
