// offline-db.js — IndexedDB wrapper for offline POS storage
// Place in: backend/static/offline-db.js

const DB_NAME = 'victors-offline';
const DB_VERSION = 1;

const STORES = {
    PENDING_ACTIONS: 'pending_actions',   // API calls queued while offline
    OFFLINE_SALES:   'offline_sales',     // Sales made while offline
    PRODUCTS_CACHE:  'products_cache',    // Local product catalog
    SYNC_LOG:        'sync_log',          // History of sync operations
};

class OfflineDB {
    constructor() {
        this.db = null;
        this.ready = this._init();
    }

    _init() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);

            req.onupgradeneeded = e => {
                const db = e.target.result;

                // Pending API actions queue
                if (!db.objectStoreNames.contains(STORES.PENDING_ACTIONS)) {
                    const store = db.createObjectStore(STORES.PENDING_ACTIONS, { keyPath: 'id', autoIncrement: true });
                    store.createIndex('timestamp', 'timestamp');
                    store.createIndex('url', 'url');
                }

                // Offline sales (full sale objects saved locally)
                if (!db.objectStoreNames.contains(STORES.OFFLINE_SALES)) {
                    const store = db.createObjectStore(STORES.OFFLINE_SALES, { keyPath: 'local_id', autoIncrement: true });
                    store.createIndex('timestamp', 'timestamp');
                    store.createIndex('synced', 'synced');
                }

                // Products cached for offline browsing/selling
                if (!db.objectStoreNames.contains(STORES.PRODUCTS_CACHE)) {
                    const store = db.createObjectStore(STORES.PRODUCTS_CACHE, { keyPath: 'id' });
                    store.createIndex('category', 'category');
                    store.createIndex('barcode', 'barcode');
                }

                // Sync operation log
                if (!db.objectStoreNames.contains(STORES.SYNC_LOG)) {
                    db.createObjectStore(STORES.SYNC_LOG, { keyPath: 'id', autoIncrement: true });
                }
            };

            req.onsuccess = e => { this.db = e.target.result; resolve(this); };
            req.onerror  = () => reject(req.error);
        });
    }

    // ── PENDING ACTIONS ───────────────────────────────────────────────────────

    async queueAction(action) {
        await this.ready;
        return this._put(STORES.PENDING_ACTIONS, { ...action, timestamp: Date.now(), synced: false });
    }

    async getPendingActions() {
        await this.ready;
        return this._getAll(STORES.PENDING_ACTIONS);
    }

    async deletePendingAction(id) {
        await this.ready;
        return this._delete(STORES.PENDING_ACTIONS, id);
    }

    async getPendingCount() {
        await this.ready;
        return this._count(STORES.PENDING_ACTIONS);
    }

    // ── OFFLINE SALES ─────────────────────────────────────────────────────────

    async saveOfflineSale(sale) {
        await this.ready;
        const record = {
            ...sale,
            timestamp: Date.now(),
            synced: false,
            local_invoice: `OFFLINE-${Date.now()}-${Math.random().toString(36).slice(2,6).toUpperCase()}`
        };
        const id = await this._put(STORES.OFFLINE_SALES, record);
        return { ...record, local_id: id };
    }

    async getUnsynedSales() {
        await this.ready;
        const all = await this._getAll(STORES.OFFLINE_SALES);
        return all.filter(s => !s.synced);
    }

    async markSaleSynced(localId, serverInvoice) {
        await this.ready;
        const sale = await this._get(STORES.OFFLINE_SALES, localId);
        if (sale) {
            sale.synced = true;
            sale.server_invoice = serverInvoice;
            sale.synced_at = Date.now();
            await this._put(STORES.OFFLINE_SALES, sale);
        }
    }

    async getOfflineSalesCount() {
        await this.ready;
        const unsync = await this.getUnsynedSales();
        return unsync.length;
    }

    // ── PRODUCTS CACHE ────────────────────────────────────────────────────────

    async cacheProducts(products) {
        await this.ready;
        const tx = this.db.transaction(STORES.PRODUCTS_CACHE, 'readwrite');
        const store = tx.objectStore(STORES.PRODUCTS_CACHE);
        // Clear old cache first
        await new Promise((res, rej) => { const r = store.clear(); r.onsuccess = res; r.onerror = rej; });
        // Insert all
        for (const p of products) {
            store.put(p);
        }
        return new Promise((res, rej) => { tx.oncomplete = res; tx.onerror = rej; });
    }

    async getCachedProducts(search = '', category = '') {
        await this.ready;
        let all = await this._getAll(STORES.PRODUCTS_CACHE);
        if (search) all = all.filter(p => p.name.toLowerCase().includes(search.toLowerCase()));
        if (category) all = all.filter(p => p.category === category);
        return all;
    }

    async getCachedProductByBarcode(barcode) {
        await this.ready;
        const all = await this._getAll(STORES.PRODUCTS_CACHE);
        return all.find(p => p.barcode === barcode) || null;
    }

    async getCachedProductCount() {
        await this.ready;
        return this._count(STORES.PRODUCTS_CACHE);
    }

    // ── SYNC LOG ──────────────────────────────────────────────────────────────

    async logSync(result) {
        await this.ready;
        return this._put(STORES.SYNC_LOG, { ...result, timestamp: Date.now() });
    }

    async getSyncHistory(limit = 10) {
        await this.ready;
        const all = await this._getAll(STORES.SYNC_LOG);
        return all.sort((a, b) => b.timestamp - a.timestamp).slice(0, limit);
    }

    // ── INTERNAL HELPERS ──────────────────────────────────────────────────────

    _put(store, data) {
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(store, 'readwrite');
            const req = tx.objectStore(store).put(data);
            req.onsuccess = () => resolve(req.result);
            req.onerror  = () => reject(req.error);
        });
    }

    _get(store, key) {
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(store, 'readonly');
            const req = tx.objectStore(store).get(key);
            req.onsuccess = () => resolve(req.result);
            req.onerror  = () => reject(req.error);
        });
    }

    _getAll(store) {
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(store, 'readonly');
            const req = tx.objectStore(store).getAll();
            req.onsuccess = () => resolve(req.result);
            req.onerror  = () => reject(req.error);
        });
    }

    _delete(store, key) {
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(store, 'readwrite');
            const req = tx.objectStore(store).delete(key);
            req.onsuccess = () => resolve();
            req.onerror  = () => reject(req.error);
        });
    }

    _count(store) {
        return new Promise((resolve, reject) => {
            const tx = this.db.transaction(store, 'readonly');
            const req = tx.objectStore(store).count();
            req.onsuccess = () => resolve(req.result);
            req.onerror  = () => reject(req.error);
        });
    }

    // ── FULL CLEAR (for testing) ──────────────────────────────────────────────
    async clearAll() {
        await this.ready;
        for (const store of Object.values(STORES)) {
            const tx = this.db.transaction(store, 'readwrite');
            tx.objectStore(store).clear();
        }
    }
}

// Export singleton
window.offlineDB = new OfflineDB();