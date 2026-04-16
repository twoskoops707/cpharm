// CPharm Service Worker — enables PWA install on Android
const CACHE = 'cpharm-v1';

self.addEventListener('install', e => {
  self.skipWaiting();
});

self.addEventListener('activate', e => {
  e.waitUntil(clients.claim());
});

// Network-first: always try live data, fall back to cache
self.addEventListener('fetch', e => {
  if (e.request.url.includes('/api/')) {
    // API calls: network only, never cache
    return;
  }
  e.respondWith(
    fetch(e.request)
      .then(res => {
        const clone = res.clone();
        caches.open(CACHE).then(c => c.put(e.request, clone));
        return res;
      })
      .catch(() => caches.match(e.request))
  );
});
