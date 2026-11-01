self.addEventListener("install",e=>self.skipWaiting());
self.addEventListener("fetch",e=>{if(e.request.url.includes("tenders.json"))e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));});
