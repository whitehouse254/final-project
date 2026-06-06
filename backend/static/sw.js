const CACHE_NAME = 'victors-pos-v1';
const OFFLINE_DB = 'victors-offline';

// Files to cache for offline app shell
const APP_SHELL = [
    '/',
    '/static/offline-db.js',
    '/static/sync.js',
    'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js',
    'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.0/css/all.min.css',
    'https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap'
];

// API endpoints to cache responses for (read-only data)
const CACHEABLE_APIS = [
    '/api/products',
    '/api/products/categories',
    '/api/loyalty',
    '/api/dashboard',
    '/api/config',
];

// ── INSTALL: cache app shell ──────────────────────────────────────────────────
self.addEventListener('install', event => {
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then(cache => cache.addAll(APP_SHELL))
            .then(() => self.skipWaiting())
    );
});

// ── ACTIVATE: clean old caches ────────────────────────────────────────────────
self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys().then(keys =>
            Promise.all(keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k)))
        ).then(() => self.clients.claim())
    );
});

// ── FETCH: network-first for APIs, cache-first for shell ──────────────────────
self.addEventListener('fetch', event => {
    const url = new URL(event.request.url);
    const isSameOrigin = url.origin === self.location.origin;
    const isAPI = url.pathname.startsWith('/api/');
    const isGET = event.request.method === 'GET';
    const isWriteAPI = ['POST', 'PUT', 'DELETE', 'PATCH'].includes(event.request.method);

    // POST/PUT/DELETE: try network, queue if offline
    if (isSameOrigin && isAPI && isWriteAPI) {
        event.respondWith(handleWriteRequest(event.request));
        return;
    }

    // GET API calls: network first, fall back to cache
    if (isSameOrigin && isAPI && isGET) {
        event.respondWith(networkFirstAPI(event.request));
        return;
    }

    // App shell: cache first, fall back to network
    if (isGET) {
        event.respondWith(cacheFirstShell(event.request));
        return;
    }
});

// Network-first for GET APIs: try live, cache on success, serve cache if offline
async function networkFirstAPI(request) {
    const url = new URL(request.url);
    const shouldCache = CACHEABLE_APIS.some(path => url.pathname.startsWith(path));

    try {
        const response = await fetch(request.clone());
        if (response.ok && shouldCache) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        const cached = await caches.match(request);
        if (cached) return cached;
        return new Response(
            JSON.stringify({ error: 'offline', cached: false }),
            { status: 503, headers: { 'Content-Type': 'application/json' } }
        );
    }
}

// Cache-first for app shell
async function cacheFirstShell(request) {
    const cached = await caches.match(request);
    if (cached) return cached;
    try {
        const response = await fetch(request);
        if (response.ok) {
            const cache = await caches.open(CACHE_NAME);
            cache.put(request, response.clone());
        }
        return response;
    } catch {
        return caches.match('/') || new Response('Offline', { status: 503 });
    }
}

// Write requests: try network, queue to IndexedDB if offline
async function handleWriteRequest(request) {
    try {
        const response = await fetch(request.clone());
        return response;
    } catch {
        // Save to IndexedDB pending queue
        const body = await request.text().catch(() => '{}');
        await queueOfflineAction({
            url: request.url,
            method: request.method,
            body: body,
            headers: Object.fromEntries(request.headers.entries()),
            timestamp: Date.now()
        });
        // Return optimistic success so UI doesn't break
        return new Response(
            JSON.stringify({ status: 'queued', offline: true, message: 'Saved offline, will sync when connected' }),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
        );
    }
}

// Queue a failed write to IndexedDB
function queueOfflineAction(action) {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(OFFLINE_DB, 1);
        req.onupgradeneeded = e => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('pending_actions')) {
                db.createObjectStore('pending_actions', { keyPath: 'id', autoIncrement: true });
            }
            if (!db.objectStoreNames.contains('offline_sales')) {
                db.createObjectStore('offline_sales', { keyPath: 'id', autoIncrement: true });
            }
            if (!db.objectStoreNames.contains('sync_log')) {
                db.createObjectStore('sync_log', { keyPath: 'id', autoIncrement: true });
            }
        };
        req.onsuccess = e => {
            const db = e.target.result;
            const tx = db.transaction('pending_actions', 'readwrite');
            tx.objectStore('pending_actions').add(action);
            tx.oncomplete = () => { db.close(); resolve(); };
            tx.onerror = () => { db.close(); reject(tx.error); };
        };
        req.onerror = () => reject(req.error);
    });
}

// Listen for sync trigger from main thread
self.addEventListener('message', event => {
    if (event.data === 'SKIP_WAITING') {
        self.skipWaiting();
    }
    if (event.data === 'TRIGGER_SYNC') {
        event.waitUntil(syncPendingActions());
    }
});

// Background sync: flush pending actions when back online
self.addEventListener('sync', event => {
    if (event.tag === 'sync-pending') {
        event.waitUntil(syncPendingActions());
    }
});

async function syncPendingActions() {
    const db = await openDB();
    const actions = await getAllPending(db);

    let synced = 0;
    let failed = 0;

    for (const action of actions) {
        try {
            const response = await fetch(action.url, {
                method: action.method,
                headers: { 'Content-Type': 'application/json', ...action.headers },
                body: action.method !== 'GET' ? action.body : undefined
            });
            if (response.ok) {
                await deletePending(db, action.id);
                synced++;
            } else {
                failed++;
            }
        } catch {
            failed++;
        }
    }

    db.close();

    // Notify all open tabs of sync result
    const clients = await self.clients.matchAll();
    clients.forEach(client => client.postMessage({
        type: 'SYNC_COMPLETE',
        synced,
        failed,
        total: actions.length
    }));
}

function openDB() {
    return new Promise((resolve, reject) => {
        const req = indexedDB.open(OFFLINE_DB, 1);
        req.onupgradeneeded = e => {
            const db = e.target.result;
            if (!db.objectStoreNames.contains('pending_actions')) {
                db.createObjectStore('pending_actions', { keyPath: 'id', autoIncrement: true });
            }
        };
        req.onsuccess = e => resolve(e.target.result);
        req.onerror = () => reject(req.error);
    });
}

function getAllPending(db) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction('pending_actions', 'readonly');
        const req = tx.objectStore('pending_actions').getAll();
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
}

function deletePending(db, id) {
    return new Promise((resolve, reject) => {
        const tx = db.transaction('pending_actions', 'readwrite');
        const req = tx.objectStore('pending_actions').delete(id);
        req.onsuccess = () => resolve();
        req.onerror = () => reject(req.error);
    });
}