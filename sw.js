const CACHE = 'fdmu-zhytomyr-v4_0-cache';
const STATIC_FILES = ['./manifest.json','./logo.jpg','./icon-192.png','./icon-512.png'];
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(STATIC_FILES)));
  self.skipWaiting();
});
self.addEventListener('activate', event => {
  event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))));
  self.clients.claim();
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  const isData = url.pathname.endsWith('/calc_data.json') || url.pathname.endsWith('/index.html') || url.pathname.endsWith('/README.txt') || url.pathname.endsWith('/sw.js') || url.pathname.endsWith('/');
  if (isData) {
    event.respondWith(fetch(event.request, {cache:'reload'}).catch(() => caches.match(event.request)));
    return;
  }
  event.respondWith(caches.match(event.request).then(cached => cached || fetch(event.request).then(resp => {
    if(resp && resp.status === 200) { const clone = resp.clone(); caches.open(CACHE).then(cache => cache.put(event.request, clone)); }
    return resp;
  })));
});
