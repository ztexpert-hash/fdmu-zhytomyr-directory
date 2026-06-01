const CACHE = 'fdmu-zhytomyr-directory-v15';
const FILES = ['./index.html', './manifest.json', './calc_data.json'];
self.addEventListener('install', e => { e.waitUntil(caches.open(CACHE).then(c => c.addAll(FILES))); self.skipWaiting(); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))); self.clients.claim(); });
self.addEventListener('fetch', e => { e.respondWith(fetch(e.request).then(r => { if(r && r.status === 200){ const clone=r.clone(); caches.open(CACHE).then(c=>c.put(e.request,clone)); } return r; }).catch(()=>caches.match(e.request))); });
