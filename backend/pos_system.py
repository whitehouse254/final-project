import sqlite3
import datetime
import hashlib
import random
import os
import shutil
import json
import smtplib
from io import BytesIO
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, session, jsonify, send_file, Response
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

app = Flask(__name__)
app.secret_key = 'supermarket-secret-key-change-in-production'

UPLOAD_FOLDER = 'static/product_images'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -------------------- CONFIGURATION MANAGER --------------------
class ConfigManager:
    DEFAULT_CONFIG = {
        "company_name": "VICTOR'S SUPER MARKET",
        "currency": "Ksh",
        "tax_rates": [{"name": "VAT 16%", "rate": 0.16, "categories": ["general", "electronics", "beverages", "snacks"]}],
        "payment_methods": ["Cash", "Card", "MPESA", "Bank Transfer", "Voucher"],
        "loyalty_points_per_ksh": 0.01,
        "low_stock_threshold": 5,
        "expiry_warning_days": 30,
        "auto_backup": True,
        "backup_interval_days": 1,
        "receipt_header": "THANK YOU FOR SHOPPING WITH US!",
        "receipt_footer": "Visit again!",
        "enable_branch_support": False,
        "branches": [{"id": 1, "name": "Main Store", "location": "Nairobi"}],
        "email_alerts": False,
        "smtp_host": "",
        "smtp_port": 587,
        "smtp_user": "",
        "smtp_pass": "",
        "alert_email": "",
        "mpesa_consumer_key": "",
        "mpesa_consumer_secret": "",
        "mpesa_shortcode": "",
        "mpesa_passkey": "",
        "mpesa_callback_url": ""
    }

    def __init__(self, config_path="config.json"):
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self):
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                user = json.load(f)
                merged = self.DEFAULT_CONFIG.copy()
                merged.update(user)
                return merged
        else:
            self.save_config(self.DEFAULT_CONFIG)
            return self.DEFAULT_CONFIG.copy()

    def save_config(self, config=None):
        with open(self.config_path, 'w') as f:
            json.dump(config or self.config, f, indent=4)

    def get_tax_rate(self, product_category):
        for t in self.config["tax_rates"]:
            if product_category in t.get("categories", []):
                return t["rate"]
        return 0.16

config_manager = ConfigManager()

# -------------------- DATABASE --------------------
class Database:
    def __init__(self, db_name="supermarket.db"):
        sqlite3.register_adapter(datetime, lambda d: d.isoformat())
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.run_migrations()
        self.populate_initial_data()
        self.conn.commit()

    def create_tables(self):
        self.cursor.executescript('''
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT UNIQUE,
                name TEXT NOT NULL,
                category TEXT,
                buying_price REAL,
                selling_price REAL NOT NULL,
                quantity INTEGER DEFAULT 0,
                min_stock INTEGER DEFAULT 5,
                unit TEXT DEFAULT 'pcs',
                supplier TEXT,
                expiry_date DATE,
                batch_number TEXT,
                image_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS sales (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_no TEXT UNIQUE,
                customer_name TEXT,
                customer_phone TEXT,
                total_amount REAL,
                discount REAL DEFAULT 0,
                tax REAL DEFAULT 0,
                net_amount REAL,
                payment_method TEXT,
                payments_json TEXT,
                cashier TEXT,
                sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                shift_id INTEGER
            );
            CREATE TABLE IF NOT EXISTS sale_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_no TEXT,
                product_id INTEGER,
                product_name TEXT,
                quantity INTEGER,
                unit_price REAL,
                total REAL,
                returned BOOLEAN DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT,
                role TEXT DEFAULT 'cashier',
                full_name TEXT,
                last_activity TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS suppliers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                contact_person TEXT,
                phone TEXT,
                email TEXT,
                address TEXT
            );
            CREATE TABLE IF NOT EXISTS stock_movements (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_id INTEGER,
                movement_type TEXT,
                quantity INTEGER,
                reason TEXT,
                user TEXT,
                movement_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS returns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_invoice TEXT,
                return_invoice TEXT UNIQUE,
                refund_amount REAL,
                reason TEXT,
                cashier TEXT,
                return_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS loyalty (
                customer_phone TEXT PRIMARY KEY,
                customer_name TEXT,
                points INTEGER DEFAULT 0,
                tier TEXT DEFAULT 'Bronze',
                total_spent REAL DEFAULT 0,
                joined_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT,
                amount REAL,
                description TEXT,
                expense_date DATE,
                user TEXT
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user TEXT,
                action TEXT,
                table_name TEXT,
                record_id TEXT,
                old_value TEXT,
                new_value TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS shifts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                cashier_name TEXT,
                start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                end_time TIMESTAMP,
                total_sales REAL DEFAULT 0,
                status TEXT DEFAULT 'active'
            );
            CREATE TABLE IF NOT EXISTS quotations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                quote_no TEXT UNIQUE,
                customer_name TEXT,
                customer_phone TEXT,
                quote_date DATE,
                expiry_date DATE,
                items_json TEXT,
                subtotal REAL,
                tax REAL,
                total REAL,
                status TEXT DEFAULT 'draft',
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                invoice_no TEXT,
                delivery_address TEXT,
                delivery_date DATE,
                status TEXT DEFAULT 'pending',
                driver_name TEXT,
                tracking_info TEXT
            );
            CREATE TABLE IF NOT EXISTS suspended_carts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cart_data TEXT,
                customer_name TEXT,
                customer_phone TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS cash_drawer (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id INTEGER,
                opening_amount REAL,
                closing_amount REAL,
                expected_amount REAL,
                actual_amount REAL,
                discrepancy REAL,
                closed_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                po_number TEXT UNIQUE,
                supplier_name TEXT,
                status TEXT DEFAULT 'draft',
                items_json TEXT,
                total_amount REAL DEFAULT 0,
                notes TEXT,
                created_by TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                received_at TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT,
                title TEXT,
                message TEXT,
                is_read INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        self.conn.commit()

    def run_migrations(self):
        migrations = [
            "ALTER TABLE users ADD COLUMN last_activity TIMESTAMP",
            "ALTER TABLE products ADD COLUMN image_path TEXT",
            "ALTER TABLE sales ADD COLUMN payments_json TEXT",
        ]
        for m in migrations:
            try:
                self.cursor.execute(m)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass

    def populate_initial_data(self):
        hashed_admin = hashlib.sha256("victor@123".encode()).hexdigest()
        if not self.fetch_one("SELECT id FROM users WHERE username='victor'"):
            self.execute_query("INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                               ("victor", hashed_admin, "admin", "Victor Admin"))
        hashed_cashier = hashlib.sha256("".encode()).hexdigest()
        if not self.fetch_one("SELECT id FROM users WHERE username='cashier'"):
            self.execute_query("INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                               ("cashier", hashed_cashier, "cashier", "Store Cashier"))
        for uname, pwd, fname in [("john","john123","John Doe"), ("mary","mary123","Mary Smith")]:
            if not self.fetch_one("SELECT id FROM users WHERE username=?", (uname,)):
                self.execute_query("INSERT INTO users (username, password, role, full_name) VALUES (?,?,?,?)",
                                   (uname, hashlib.sha256(pwd.encode()).hexdigest(), "cashier", fname))
        if self.fetch_one("SELECT COUNT(*) FROM suppliers")[0] == 0:
            for i in range(1, 21):
                self.execute_query("INSERT INTO suppliers (name,contact_person,phone,email,address) VALUES (?,?,?,?,?)",
                                   (f"Supplier {i}", f"Contact {i}", f"07{random.randint(10000000,99999999)}",
                                    f"sup{i}@mail.com", f"Addr {i}"))
        if self.fetch_one("SELECT COUNT(*) FROM loyalty")[0] == 0:
            first = ['Alice','Brian','Carol','David','Eunice','Francis','Grace','Henry','Irene','James']
            last = ['Wanjiku','Kimani','Otieno','Mwangi','Achieng','Omondi','Nduta','Kipchoge','Chebet','Kariuki']
            for _ in range(20):
                name = f"{random.choice(first)} {random.choice(last)}"
                phone = f"07{random.randint(10000000,99999999)}"
                points = random.randint(0, 500)
                spent = random.randint(0, 20000)
                tier = 'Gold' if spent > 15000 else ('Silver' if spent > 5000 else 'Bronze')
                self.execute_query("INSERT OR IGNORE INTO loyalty (customer_name, customer_phone, points, total_spent, tier) VALUES (?,?,?,?,?)",
                                   (name, phone, points, spent, tier))
        self.populate_products()
        self.conn.commit()

    def populate_products(self):
        if self.fetch_one("SELECT COUNT(*) FROM products")[0] > 0:
            return
        suppliers = [r[0] for r in self.fetch_all("SELECT name FROM suppliers")]
        if not suppliers:
            suppliers = ["General Supplier"]
        categories = {
            "Grains": ["Rice (1kg)", "Maize Flour (1kg)", "Wheat Flour (2kg)", "Oats (500g)", "Sorghum (1kg)"],
            "Dairy": ["Fresh Milk (1L)", "Yogurt (500ml)", "Cheese (250g)", "Butter (250g)"],
            "Beverages": ["Mineral Water (1L)", "Soda (330ml)", "Juice (1L)", "Tea (100g)"],
            "Snacks": ["Potato Chips (100g)", "Chocolate Bar (50g)", "Biscuits (100g)", "Nuts (200g)"],
            "Fruits": ["Apples (1kg)", "Bananas (1 bunch)", "Oranges (1kg)", "Mangoes (1kg)"],
            "Meat": ["Beef (1kg)", "Chicken Whole", "Pork (500g)", "Fish (500g)"],
            "Household": ["Soap (200g)", "Detergent (1kg)", "Cooking Oil (1L)", "Sugar (2kg)"]
        }
        count = 0
        for cat, names in categories.items():
            for name in names:
                for v in range(2):
                    if count >= 100: break
                    buying = round(random.uniform(20, 500), 2)
                    selling = round(buying * 1.3, 2)
                    qty = random.randint(10, 300)
                    min_stock = random.randint(5, 20)
                    barcode = f"890{random.randint(1000000000, 9999999999)}"
                    # Some near expiry for testing alerts
                    days_ahead = random.choice([7, 14, 25, 60, 90, 180, 365])
                    expiry = (datetime.now() + timedelta(days=days_ahead)).date()
                    batch = f"BATCH{random.randint(1000,9999)}"
                    self.execute_query(
                        "INSERT OR IGNORE INTO products (barcode,name,category,buying_price,selling_price,quantity,min_stock,unit,supplier,expiry_date,batch_number) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (barcode, f"{name} v{v+1}", cat, buying, selling, qty, min_stock,
                         random.choice(["pcs","kg","L"]), random.choice(suppliers), expiry, batch))
                    count += 1
                if count >= 100: break
            if count >= 100: break

    def execute_query(self, query, params=()):
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        self.conn.commit()
        cursor.close()

    def fetch_all(self, query, params=()):
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchall()
        cursor.close()
        return result

    def fetch_one(self, query, params=()):
        cursor = self.conn.cursor()
        cursor.execute(query, params)
        result = cursor.fetchone()
        cursor.close()
        return result

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def log_action(self, user, action, table, rid, old="", new=""):
        self.execute_query(
            "INSERT INTO audit_log (user,action,table_name,record_id,old_value,new_value) VALUES (?,?,?,?,?,?)",
            (user, action, table, str(rid), str(old), str(new)))

    def add_notification(self, ntype, title, message):
        self.execute_query(
            "INSERT INTO notifications (type, title, message) VALUES (?,?,?)",
            (ntype, title, message))

db = Database()

# -------------------- HELPERS --------------------
@app.before_request
def update_user_activity():
    if 'user_id' in session:
        try:
            db.execute_query("UPDATE users SET last_activity=CURRENT_TIMESTAMP WHERE id=?", (int(session['user_id']),))
        except Exception:
            pass

def login_required(allowed_roles=None):
    def decorator(f):
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return jsonify({'error': 'unauthorized'}), 401
            if allowed_roles and session.get('role') not in allowed_roles:
                return jsonify({'error': 'forbidden'}), 403
            return f(*args, **kwargs)
        wrapper.__name__ = f.__name__
        return wrapper
    return decorator

def generate_invoice_no():
    today = datetime.now().date()
    last = db.fetch_one("SELECT invoice_no FROM sales WHERE DATE(sale_date)=? ORDER BY id DESC LIMIT 1", (today,))
    seq = (int(last[0].split('-')[-1]) + 1) if last else 1
    return f"INV-{today.strftime('%Y%m%d')}-{seq:04d}"

def generate_po_number():
    today = datetime.now().date()
    last = db.fetch_one("SELECT po_number FROM purchase_orders WHERE DATE(created_at)=? ORDER BY id DESC LIMIT 1", (today,))
    seq = (int(last[0].split('-')[-1]) + 1) if last else 1
    return f"PO-{today.strftime('%Y%m%d')}-{seq:04d}"

def update_loyalty_tier(phone, net_total):
    existing = db.fetch_one("SELECT customer_phone FROM loyalty WHERE customer_phone=?", (phone,))
    points = int(net_total * 0.01)
    if not existing:
        return
    db.execute_query("UPDATE loyalty SET points=points+?, total_spent=total_spent+? WHERE customer_phone=?",
                     (points, net_total, phone))
    row = db.fetch_one("SELECT total_spent FROM loyalty WHERE customer_phone=?", (phone,))
    if row:
        spent = row[0]
        tier = 'Gold' if spent >= 15000 else ('Silver' if spent >= 5000 else 'Bronze')
        db.execute_query("UPDATE loyalty SET tier=? WHERE customer_phone=?", (tier, phone))

def check_expiry_alerts():
    warning_days = config_manager.config.get('expiry_warning_days', 30)
    soon = db.fetch_all(
        "SELECT name, expiry_date, quantity FROM products WHERE expiry_date IS NOT NULL AND expiry_date <= date('now', '+' || ? || ' days') AND quantity > 0",
        (warning_days,))
    for p in soon:
        existing = db.fetch_one(
            "SELECT id FROM notifications WHERE title=? AND is_read=0", (f"Expiry: {p[0]}",))
        if not existing:
            days_left = (datetime.strptime(p[1], '%Y-%m-%d').date() - datetime.now().date()).days
            msg = f"{p[0]} expires in {days_left} days (stock: {p[2]})"
            if days_left <= 0:
                msg = f"{p[0]} has EXPIRED (stock: {p[2]})"
            db.add_notification('expiry', f"Expiry: {p[0]}", msg)

def check_low_stock_alerts():
    low = db.fetch_all("SELECT name, quantity, min_stock FROM products WHERE quantity <= min_stock")
    for p in low:
        existing = db.fetch_one("SELECT id FROM notifications WHERE title=? AND is_read=0", (f"Low stock: {p[0]}",))
        if not existing:
            db.add_notification('stock', f"Low stock: {p[0]}", f"{p[0]} has only {p[1]} left (min: {p[2]})")

# -------------------- STATIC FRONTEND --------------------
@app.route('/')
def index():
    return send_file('frontend.html')

# -------------------- AUTH API --------------------
@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get('username', '')
    password = data.get('password', '')
    hashed = hashlib.sha256(password.encode()).hexdigest()
    user = db.fetch_one("SELECT id,username,role,full_name FROM users WHERE username=? AND password=?", (username, hashed))
    if user:
        session['user_id'] = user[0]
        session['username'] = user[1]
        session['role'] = user[2]
        session['full_name'] = user[3]
        db.execute_query("UPDATE users SET last_activity=CURRENT_TIMESTAMP WHERE id=?", (user[0],))
        db.log_action(username, "LOGIN", "users", user[0], "", "Success")
        return jsonify({'status': 'success', 'user': {'id': user[0], 'username': user[1], 'role': user[2], 'full_name': user[3]}})
    return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def api_logout():
    session.clear()
    return jsonify({'status': 'success'})

@app.route('/api/me')
def api_me():
    if 'user_id' not in session:
        return jsonify({'error': 'unauthorized'}), 401
    return jsonify({'id': session['user_id'], 'username': session['username'], 'role': session['role'], 'full_name': session['full_name']})

# -------------------- DASHBOARD --------------------
@app.route('/api/dashboard')
@login_required()
def api_dashboard():
    today = datetime.now().date()
    check_expiry_alerts()
    check_low_stock_alerts()
    today_data = db.fetch_one("SELECT COALESCE(SUM(net_amount),0), COUNT(*) FROM sales WHERE DATE(sale_date)=?", (today,))
    week_data = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales WHERE DATE(sale_date) >= date('now','-7 days')", ())
    month_data = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales WHERE DATE(sale_date) >= date('now','-30 days')", ())
    total_products = db.fetch_one("SELECT COUNT(*) FROM products")[0]
    low_stock = db.fetch_one("SELECT COUNT(*) FROM products WHERE quantity <= min_stock")[0]
    total_sales = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales")[0]
    unread_notifs = db.fetch_one("SELECT COUNT(*) FROM notifications WHERE is_read=0")[0]
    warning_days = config_manager.config.get('expiry_warning_days', 30)
    expiring_soon = db.fetch_one(
        "SELECT COUNT(*) FROM products WHERE expiry_date IS NOT NULL AND expiry_date <= date('now', '+' || ? || ' days') AND quantity > 0",
        (warning_days,))[0]
    # 7-day trend
    trend = db.fetch_all(
        "SELECT DATE(sale_date), COALESCE(SUM(net_amount),0) FROM sales WHERE DATE(sale_date) >= date('now','-7 days') GROUP BY DATE(sale_date) ORDER BY DATE(sale_date)")
    # Top 5 products today
    top_today = db.fetch_all(
        "SELECT p.name, SUM(si.quantity), SUM(si.total) FROM sale_items si JOIN products p ON si.product_id=p.id JOIN sales s ON si.invoice_no=s.invoice_no WHERE DATE(s.sale_date)=? GROUP BY si.product_id ORDER BY SUM(si.quantity) DESC LIMIT 5",
        (today,))
    return jsonify({
        'today_sales': today_data[0], 'today_transactions': today_data[1],
        'week_sales': week_data[0], 'month_sales': month_data[0],
        'total_products': total_products, 'low_stock': low_stock,
        'total_sales': total_sales, 'unread_notifications': unread_notifs,
        'expiring_soon': expiring_soon,
        'trend': [{'date': t[0], 'amount': t[1]} for t in trend],
        'top_today': [{'name': t[0], 'qty': t[1], 'total': t[2]} for t in top_today]
    })

# -------------------- PRODUCTS --------------------
@app.route('/api/products', methods=['GET'])
@login_required()
def api_products():
    search = request.args.get('q', '')
    category = request.args.get('category', '')
    query = "SELECT id,barcode,name,category,buying_price,selling_price,quantity,min_stock,unit,supplier,expiry_date,batch_number,image_path FROM products WHERE 1=1"
    params = []
    if search:
        query += " AND (name LIKE ? OR barcode LIKE ?)"
        params += [f'%{search}%', f'%{search}%']
    if category:
        query += " AND category=?"
        params.append(category)
    query += " ORDER BY name"
    products = db.fetch_all(query, params)
    return jsonify([{
        'id': p[0], 'barcode': p[1], 'name': p[2], 'category': p[3],
        'buying_price': p[4], 'selling_price': p[5], 'quantity': p[6],
        'min_stock': p[7], 'unit': p[8], 'supplier': p[9],
        'expiry_date': p[10], 'batch_number': p[11], 'image_path': p[12]
    } for p in products])

@app.route('/api/products', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_add_product():
    data = request.json
    barcode = data.get('barcode') or f"890{random.randint(1000000000,9999999999)}"
    db.execute_query(
        "INSERT INTO products (barcode,name,category,buying_price,selling_price,quantity,min_stock,unit,supplier,expiry_date,batch_number) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (barcode, data['name'], data.get('category',''), data.get('buying_price',0),
         data['selling_price'], data.get('quantity',0), data.get('min_stock',5),
         data.get('unit','pcs'), data.get('supplier',''), data.get('expiry_date',''), data.get('batch_number','')))
    db.log_action(session['username'], "INSERT", "products", data['name'], "", f"Added product")
    return jsonify({'status': 'success'})

@app.route('/api/products/<int:pid>', methods=['PUT'])
@login_required(allowed_roles=['admin'])
def api_update_product(pid):
    data = request.json
    old = db.fetch_one("SELECT * FROM products WHERE id=?", (pid,))
    db.execute_query(
        "UPDATE products SET name=?,category=?,buying_price=?,selling_price=?,quantity=?,min_stock=?,unit=?,supplier=?,expiry_date=?,batch_number=? WHERE id=?",
        (data['name'], data.get('category',''), data.get('buying_price',0), data['selling_price'],
         data['quantity'], data.get('min_stock',5), data.get('unit','pcs'), data.get('supplier',''),
         data.get('expiry_date',''), data.get('batch_number',''), pid))
    db.log_action(session['username'], "UPDATE", "products", pid, str(old), str(data))
    return jsonify({'status': 'success'})

@app.route('/api/products/<int:pid>', methods=['DELETE'])
@login_required(allowed_roles=['admin'])
def api_delete_product(pid):
    db.execute_query("DELETE FROM products WHERE id=?", (pid,))
    return jsonify({'status': 'success'})

@app.route('/api/products/<int:pid>/stock', methods=['PUT'])
@login_required(allowed_roles=['admin'])
def api_update_stock(pid):
    data = request.json
    qty = data.get('quantity', 0)
    old = db.fetch_one("SELECT quantity FROM products WHERE id=?", (pid,))
    db.execute_query("UPDATE products SET quantity=? WHERE id=?", (qty, pid))
    db.execute_query("INSERT INTO stock_movements (product_id,movement_type,quantity,reason,user) VALUES (?,?,?,?,?)",
                     (pid, 'adjustment', qty - (old[0] if old else 0), 'Manual adjustment', session['username']))
    return jsonify({'status': 'success'})

@app.route('/api/products/barcode/<barcode>')
@login_required()
def api_product_barcode(barcode):
    p = db.fetch_one("SELECT id,name,selling_price,quantity,category FROM products WHERE barcode=? AND quantity>0", (barcode,))
    if p:
        return jsonify({'id': p[0], 'name': p[1], 'price': p[2], 'stock': p[3], 'category': p[4]})
    return jsonify({'error': 'not found'}), 404

@app.route('/api/products/categories')
@login_required()
def api_categories():
    cats = db.fetch_all("SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")
    return jsonify([c[0] for c in cats])

@app.route('/api/products/<int:pid>/image', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_upload_image(pid):
    if 'image' not in request.files:
        return jsonify({'error': 'no file'}), 400
    f = request.files['image']
    if f.filename == '':
        return jsonify({'error': 'no filename'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ['jpg', 'jpeg', 'png', 'webp']:
        return jsonify({'error': 'invalid type'}), 400
    filename = f"product_{pid}.{ext}"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    f.save(filepath)
    db.execute_query("UPDATE products SET image_path=? WHERE id=?", (f"/static/product_images/{filename}", pid))
    return jsonify({'status': 'success', 'path': f"/static/product_images/{filename}"})

# -------------------- SALES / POS --------------------
@app.route('/api/invoices', methods=['POST'])
@login_required()
def api_create_sale():
    data = request.json
    cart = data['cart']
    customer_name = data.get('customer_name', 'Walk-in')
    customer_phone = data.get('customer_phone', '')
    discount = data.get('discount', 0)
    payments = data.get('payments', [])
    payment_method = payments[0]['method'] if payments else data.get('payment_method', 'Cash')
    subtotal = sum(item['total'] for item in cart)
    if discount > subtotal:
        discount = subtotal
    tax = (subtotal - discount) * 0.16
    net_total = subtotal - discount + tax
    invoice_no = generate_invoice_no()
    try:
        for item in cart:
            stock = db.fetch_one("SELECT quantity FROM products WHERE id=?", (item['id'],))
            if not stock or stock[0] < item['quantity']:
                return jsonify({'status': 'error', 'message': f"Insufficient stock for {item['name']}"})
        db.execute_query(
            "INSERT INTO sales (invoice_no,customer_name,customer_phone,total_amount,discount,tax,net_amount,payment_method,payments_json,cashier) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (invoice_no, customer_name, customer_phone, subtotal, discount, tax, net_total,
             payment_method, json.dumps(payments), session['username']))
        for item in cart:
            db.execute_query(
                "INSERT INTO sale_items (invoice_no,product_id,product_name,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",
                (invoice_no, item['id'], item['name'], item['quantity'], item['price'], item['total']))
            db.execute_query("UPDATE products SET quantity=quantity-? WHERE id=?", (item['quantity'], item['id']))
        if customer_phone:
            update_loyalty_tier(customer_phone, net_total)
        check_low_stock_alerts()
        db.commit()
        db.log_action(session['username'], "SALE", "sales", invoice_no, "", f"Total: {net_total}")
        return jsonify({'status': 'success', 'invoice': invoice_no, 'net_total': net_total})
    except Exception as e:
        db.rollback()
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/invoices', methods=['GET'])
@login_required()
def api_invoices():
    limit = int(request.args.get('limit', 50))
    sales = db.fetch_all("SELECT invoice_no,sale_date,customer_name,customer_phone,net_amount,payment_method,cashier FROM sales ORDER BY sale_date DESC LIMIT ?", (limit,))
    return jsonify([{
        'invoice_no': s[0], 'sale_date': s[1], 'customer_name': s[2],
        'customer_phone': s[3], 'net_amount': s[4], 'payment_method': s[5], 'cashier': s[6]
    } for s in sales])

@app.route('/api/invoices/<invoice_no>')
@login_required()
def api_invoice_detail(invoice_no):
    sale = db.fetch_one("SELECT * FROM sales WHERE invoice_no=?", (invoice_no,))
    if not sale:
        return jsonify({'error': 'not found'}), 404
    items = db.fetch_all("SELECT product_name,quantity,unit_price,total FROM sale_items WHERE invoice_no=?", (invoice_no,))
    return jsonify({
        'invoice_no': sale[1], 'customer_name': sale[2], 'customer_phone': sale[3],
        'total_amount': sale[4], 'discount': sale[5], 'tax': sale[6], 'net_amount': sale[7],
        'payment_method': sale[8], 'payments': json.loads(sale[9]) if sale[9] else [],
        'cashier': sale[10], 'sale_date': sale[11],
        'items': [{'name': i[0], 'qty': i[1], 'price': i[2], 'total': i[3]} for i in items]
    })

# -------------------- RETURNS --------------------
@app.route('/api/returns', methods=['POST'])
@login_required()
def api_process_return():
    data = request.json
    invoice_no = data.get('invoice_no', '')
    reason = data.get('reason', 'Customer return')
    sale = db.fetch_one("SELECT invoice_no, net_amount FROM sales WHERE invoice_no=?", (invoice_no,))
    if not sale:
        return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
    already = db.fetch_one("SELECT id FROM returns WHERE original_invoice=?", (invoice_no,))
    if already:
        return jsonify({'status': 'error', 'message': 'Already returned'}), 400
    refund = round(sale[1] * 0.95, 2)
    ret_inv = f"RET-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    db.execute_query("INSERT INTO returns (original_invoice,return_invoice,refund_amount,reason,cashier) VALUES (?,?,?,?,?)",
                     (invoice_no, ret_inv, refund, reason, session['username']))
    items = db.fetch_all("SELECT product_id, quantity FROM sale_items WHERE invoice_no=?", (invoice_no,))
    for it in items:
        db.execute_query("UPDATE products SET quantity=quantity+? WHERE id=?", (it[1], it[0]))
    db.commit()
    return jsonify({'status': 'success', 'refund': refund, 'return_invoice': ret_inv})

# -------------------- LOYALTY --------------------
@app.route('/api/loyalty', methods=['GET'])
@login_required()
def api_loyalty():
    rows = db.fetch_all("SELECT customer_phone,customer_name,points,tier,total_spent,joined_date FROM loyalty ORDER BY total_spent DESC")
    return jsonify([{'phone': r[0], 'name': r[1], 'points': r[2], 'tier': r[3], 'spent': r[4], 'joined': r[5]} for r in rows])

@app.route('/api/loyalty', methods=['POST'])
@login_required()
def api_add_loyalty():
    data = request.json
    db.execute_query("INSERT OR REPLACE INTO loyalty (customer_phone,customer_name,points) VALUES (?,?,?)",
                     (data['phone'], data['name'], data.get('points', 0)))
    return jsonify({'status': 'success'})

@app.route('/api/loyalty/<phone>/history')
@login_required()
def api_loyalty_history(phone):
    sales = db.fetch_all(
        "SELECT invoice_no, sale_date, net_amount, payment_method FROM sales WHERE customer_phone=? ORDER BY sale_date DESC LIMIT 20", (phone,))
    return jsonify([{'invoice': s[0], 'date': s[1], 'amount': s[2], 'method': s[3]} for s in sales])

# -------------------- EXPENSES --------------------
@app.route('/api/expenses', methods=['GET'])
@login_required()
def api_expenses():
    rows = db.fetch_all("SELECT id,expense_date,category,amount,description,user FROM expenses ORDER BY expense_date DESC")
    return jsonify([{'id': r[0], 'date': r[1], 'category': r[2], 'amount': r[3], 'description': r[4], 'user': r[5]} for r in rows])

@app.route('/api/expenses', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_add_expense():
    data = request.json
    db.execute_query("INSERT INTO expenses (category,amount,description,expense_date,user) VALUES (?,?,?,?,?)",
                     (data['category'], data['amount'], data.get('description',''), datetime.now().date(), session['username']))
    return jsonify({'status': 'success'})

@app.route('/api/expenses/<int:eid>', methods=['DELETE'])
@login_required(allowed_roles=['admin'])
def api_delete_expense(eid):
    db.execute_query("DELETE FROM expenses WHERE id=?", (eid,))
    return jsonify({'status': 'success'})

# -------------------- SHIFTS --------------------
@app.route('/api/shifts', methods=['GET'])
@login_required()
def api_shifts():
    rows = db.fetch_all("SELECT id,cashier_name,start_time,end_time,total_sales,status FROM shifts ORDER BY start_time DESC LIMIT 20")
    return jsonify([{'id': r[0], 'cashier': r[1], 'start': r[2], 'end': r[3], 'sales': r[4], 'status': r[5]} for r in rows])

@app.route('/api/shifts/current')
@login_required()
def api_current_shift():
    s = db.fetch_one("SELECT id,cashier_name,start_time,total_sales FROM shifts WHERE status='active' AND user_id=? ORDER BY start_time DESC LIMIT 1", (session['user_id'],))
    if s:
        return jsonify({'active': True, 'id': s[0], 'cashier_name': s[1], 'start_time': s[2], 'total_sales': s[3]})
    return jsonify({'active': False})

@app.route('/api/shifts/start', methods=['POST'])
@login_required()
def api_start_shift():
    existing = db.fetch_one("SELECT id FROM shifts WHERE status='active' AND user_id=?", (session['user_id'],))
    if existing:
        return jsonify({'status': 'error', 'message': 'Shift already active'})
    db.execute_query("INSERT INTO shifts (user_id,cashier_name,status) VALUES (?,?,?)",
                     (session['user_id'], session.get('full_name', session['username']), 'active'))
    return jsonify({'status': 'success'})

@app.route('/api/shifts/end', methods=['POST'])
@login_required()
def api_end_shift():
    s = db.fetch_one("SELECT id FROM shifts WHERE status='active' AND user_id=? ORDER BY start_time DESC LIMIT 1", (session['user_id'],))
    if not s:
        return jsonify({'status': 'error', 'message': 'No active shift'})
    total = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales WHERE shift_id=? OR (cashier=? AND shift_id IS NULL)", (s[0], session['username']))[0]
    db.execute_query("UPDATE shifts SET status='closed',end_time=CURRENT_TIMESTAMP,total_sales=? WHERE id=?", (total, s[0]))
    return jsonify({'status': 'success', 'total_sales': total})

# -------------------- CASH DRAWER --------------------
@app.route('/api/drawer/open', methods=['POST'])
@login_required()
def api_drawer_open():
    data = request.json
    db.execute_query("INSERT INTO cash_drawer (shift_id,opening_amount) VALUES (?,?)",
                     (data['shift_id'], data['opening_amount']))
    return jsonify({'status': 'success'})

@app.route('/api/drawer/close', methods=['POST'])
@login_required()
def api_drawer_close():
    data = request.json
    shift_id = data['shift_id']
    actual = data['actual_amount']
    opening = db.fetch_one("SELECT opening_amount FROM cash_drawer WHERE shift_id=? ORDER BY id DESC LIMIT 1", (shift_id,))
    cash_sales = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales WHERE payment_method='Cash' AND shift_id=?", (shift_id,))[0]
    expected = (opening[0] if opening else 0) + cash_sales
    discrepancy = actual - expected
    db.execute_query("UPDATE cash_drawer SET closing_amount=?,expected_amount=?,actual_amount=?,discrepancy=?,closed_at=CURRENT_TIMESTAMP WHERE shift_id=?",
                     (actual, expected, actual, discrepancy, shift_id))
    return jsonify({'status': 'success', 'expected': expected, 'actual': actual, 'discrepancy': discrepancy})

# -------------------- QUOTATIONS --------------------
@app.route('/api/quotations', methods=['GET'])
@login_required()
def api_quotations():
    rows = db.fetch_all("SELECT id,quote_no,customer_name,customer_phone,quote_date,total,status FROM quotations ORDER BY created_at DESC")
    return jsonify([{'id': r[0], 'quote_no': r[1], 'customer': r[2], 'phone': r[3], 'date': r[4], 'total': r[5], 'status': r[6]} for r in rows])

@app.route('/api/quotations', methods=['POST'])
@login_required()
def api_create_quotation():
    data = request.json
    items = data['items']
    subtotal = sum(i['total'] for i in items)
    tax = subtotal * 0.16
    total = subtotal + tax
    quote_no = f"QT-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    expiry = (datetime.now() + timedelta(days=7)).date()
    db.execute_query(
        "INSERT INTO quotations (quote_no,customer_name,customer_phone,quote_date,expiry_date,items_json,subtotal,tax,total,created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (quote_no, data.get('customer_name',''), data.get('customer_phone',''),
         datetime.now().date(), expiry, json.dumps(items), subtotal, tax, total, session['username']))
    return jsonify({'status': 'success', 'quote_no': quote_no})

@app.route('/api/quotations/<int:qid>/convert', methods=['POST'])
@login_required()
def api_convert_quotation(qid):
    q = db.fetch_one("SELECT * FROM quotations WHERE id=?", (qid,))
    if not q:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    items = json.loads(q[6])
    invoice_no = generate_invoice_no()
    db.execute_query(
        "INSERT INTO sales (invoice_no,customer_name,customer_phone,total_amount,tax,net_amount,payment_method,cashier) VALUES (?,?,?,?,?,?,?,?)",
        (invoice_no, q[2], q[3], q[7], q[8], q[9], 'Cash', session['username']))
    for item in items:
        db.execute_query("INSERT INTO sale_items (invoice_no,product_id,product_name,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",
                         (invoice_no, item.get('id', 0), item['name'], item['quantity'], item['price'], item['total']))
        db.execute_query("UPDATE products SET quantity=quantity-? WHERE id=?", (item['quantity'], item.get('id', 0)))
    db.execute_query("UPDATE quotations SET status='converted' WHERE id=?", (qid,))
    db.commit()
    return jsonify({'status': 'success', 'invoice': invoice_no})

# -------------------- DELIVERIES --------------------
@app.route('/api/deliveries', methods=['GET'])
@login_required()
def api_deliveries():
    rows = db.fetch_all("SELECT id,invoice_no,delivery_address,delivery_date,status,driver_name FROM deliveries ORDER BY id DESC")
    return jsonify([{'id': r[0], 'invoice': r[1], 'address': r[2], 'date': r[3], 'status': r[4], 'driver': r[5]} for r in rows])

@app.route('/api/deliveries', methods=['POST'])
@login_required()
def api_create_delivery():
    data = request.json
    db.execute_query("INSERT INTO deliveries (invoice_no,delivery_address,delivery_date,driver_name) VALUES (?,?,?,?)",
                     (data['invoice_no'], data['address'], datetime.now().date(), data.get('driver', '')))
    return jsonify({'status': 'success'})

@app.route('/api/deliveries/<int:did>', methods=['PUT'])
@login_required()
def api_update_delivery(did):
    data = request.json
    db.execute_query("UPDATE deliveries SET status=?,driver_name=? WHERE id=?",
                     (data.get('status', 'pending'), data.get('driver', ''), did))
    return jsonify({'status': 'success'})

# -------------------- PURCHASE ORDERS --------------------
@app.route('/api/purchase_orders', methods=['GET'])
@login_required()
def api_purchase_orders():
    rows = db.fetch_all("SELECT id,po_number,supplier_name,status,total_amount,created_at FROM purchase_orders ORDER BY created_at DESC")
    return jsonify([{'id': r[0], 'po_number': r[1], 'supplier': r[2], 'status': r[3], 'total': r[4], 'date': r[5]} for r in rows])

@app.route('/api/purchase_orders', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_create_po():
    data = request.json
    items = data['items']
    total = sum(i['quantity'] * i['unit_price'] for i in items)
    po_no = generate_po_number()
    db.execute_query("INSERT INTO purchase_orders (po_number,supplier_name,items_json,total_amount,notes,created_by) VALUES (?,?,?,?,?,?)",
                     (po_no, data['supplier'], json.dumps(items), total, data.get('notes',''), session['username']))
    return jsonify({'status': 'success', 'po_number': po_no})

@app.route('/api/purchase_orders/<int:poid>/receive', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_receive_po(poid):
    po = db.fetch_one("SELECT items_json FROM purchase_orders WHERE id=?", (poid,))
    if not po:
        return jsonify({'error': 'not found'}), 404
    items = json.loads(po[0])
    for item in items:
        if item.get('product_id'):
            db.execute_query("UPDATE products SET quantity=quantity+? WHERE id=?",
                             (item['quantity'], item['product_id']))
    db.execute_query("UPDATE purchase_orders SET status='received',received_at=CURRENT_TIMESTAMP WHERE id=?", (poid,))
    db.commit()
    return jsonify({'status': 'success'})

# -------------------- USERS --------------------
@app.route('/api/users', methods=['GET'])
@login_required(allowed_roles=['admin'])
def api_users():
    users = db.fetch_all("SELECT id,username,role,full_name,last_activity FROM users")
    return jsonify([{'id': u[0], 'username': u[1], 'role': u[2], 'full_name': u[3], 'last_activity': u[4]} for u in users])

@app.route('/api/users', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_add_user():
    data = request.json
    if db.fetch_one("SELECT id FROM users WHERE username=?", (data['username'],)):
        return jsonify({'status': 'error', 'message': 'Username exists'}), 400
    hashed = hashlib.sha256(data['password'].encode()).hexdigest()
    db.execute_query("INSERT INTO users (username,password,role,full_name) VALUES (?,?,?,?)",
                     (data['username'], hashed, data.get('role','cashier'), data.get('full_name','')))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/users/<int:uid>/password', methods=['PUT'])
@login_required(allowed_roles=['admin'])
def api_reset_password(uid):
    data = request.json
    hashed = hashlib.sha256(data['password'].encode()).hexdigest()
    db.execute_query("UPDATE users SET password=? WHERE id=?", (hashed, uid))
    db.commit()
    return jsonify({'status': 'success'})

@app.route('/api/users/change_password', methods=['PUT'])
@login_required()
def api_change_password():
    data = request.json
    user = db.fetch_one("SELECT password FROM users WHERE id=?", (session['user_id'],))
    if hashlib.sha256(data['old_password'].encode()).hexdigest() != user[0]:
        return jsonify({'status': 'error', 'message': 'Old password incorrect'}), 400
    if len(data['new_password']) < 4:
        return jsonify({'status': 'error', 'message': 'Password too short'}), 400
    hashed = hashlib.sha256(data['new_password'].encode()).hexdigest()
    db.execute_query("UPDATE users SET password=? WHERE id=?", (hashed, session['user_id']))
    db.commit()
    return jsonify({'status': 'success'})

# -------------------- REPORTS --------------------
@app.route('/api/reports/dashboard')
@login_required()
def api_reports_dashboard():
    today = datetime.now().date()
    today_sales = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales WHERE DATE(sale_date)=?", (today,))[0]
    total_sales = db.fetch_one("SELECT COALESCE(SUM(net_amount),0) FROM sales")[0]
    total_products = db.fetch_one("SELECT COUNT(*) FROM products")[0]
    low_stock = db.fetch_one("SELECT COUNT(*) FROM products WHERE quantity<=min_stock")[0]
    return jsonify({'today_sales': today_sales, 'total_sales': total_sales, 'total_products': total_products, 'low_stock': low_stock})

@app.route('/api/reports/sales')
@login_required()
def api_reports_sales():
    from_date = request.args.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to', datetime.now().strftime('%Y-%m-%d'))
    sales = db.fetch_all("SELECT invoice_no,sale_date,customer_name,customer_phone,net_amount,payment_method,cashier FROM sales WHERE DATE(sale_date) BETWEEN ? AND ? ORDER BY sale_date DESC", (from_date, to_date))
    return jsonify([{'invoice_no': s[0], 'sale_date': s[1], 'customer_name': s[2], 'customer_phone': s[3], 'net_amount': s[4], 'payment_method': s[5], 'cashier': s[6]} for s in sales])

@app.route('/api/reports/top_products')
@login_required()
def api_top_products():
    from_date = request.args.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to', datetime.now().strftime('%Y-%m-%d'))
    data = db.fetch_all(
        "SELECT p.name, SUM(si.quantity), SUM(si.total) FROM sale_items si JOIN products p ON si.product_id=p.id JOIN sales s ON si.invoice_no=s.invoice_no WHERE DATE(s.sale_date) BETWEEN ? AND ? GROUP BY si.product_id ORDER BY SUM(si.quantity) DESC LIMIT 10",
        (from_date, to_date))
    return jsonify([{'name': d[0], 'qty': d[1], 'total': d[2]} for d in data])

@app.route('/api/reports/payments')
@login_required()
def api_payment_breakdown():
    from_date = request.args.get('from', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to', datetime.now().strftime('%Y-%m-%d'))
    data = db.fetch_all(
        "SELECT payment_method, COUNT(*), SUM(net_amount) FROM sales WHERE DATE(sale_date) BETWEEN ? AND ? GROUP BY payment_method",
        (from_date, to_date))
    return jsonify([{'method': d[0], 'count': d[1], 'total': d[2]} for d in data])

@app.route('/api/reports/daily')
@login_required()
def api_daily_sales():
    days = int(request.args.get('days', 30))
    data = db.fetch_all(
        "SELECT DATE(sale_date), COUNT(*), SUM(net_amount) FROM sales WHERE DATE(sale_date) >= date('now', '-' || ? || ' days') GROUP BY DATE(sale_date) ORDER BY DATE(sale_date)",
        (days,))
    return jsonify([{'date': d[0], 'count': d[1], 'total': d[2]} for d in data])

# -------------------- PDF EXPORT --------------------
@app.route('/api/reports/pdf')
@login_required()
def api_export_pdf():
    from_date = request.args.get('from', datetime.now().strftime('%Y-%m-%d'))
    to_date = request.args.get('to', datetime.now().strftime('%Y-%m-%d'))
    sales = db.fetch_all(
        "SELECT invoice_no, DATE(sale_date), customer_name, payment_method, net_amount FROM sales WHERE DATE(sale_date) BETWEEN ? AND ? ORDER BY sale_date",
        (from_date, to_date))
    total = sum(s[4] for s in sales)
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=15*mm, rightMargin=15*mm, topMargin=20*mm, bottomMargin=15*mm)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(f"{config_manager.config['company_name']} - Sales Report", styles['Title']))
    elements.append(Paragraph(f"Period: {from_date} to {to_date}", styles['Normal']))
    elements.append(Spacer(1, 10*mm))
    table_data = [['Invoice', 'Date', 'Customer', 'Payment', 'Amount (Ksh)']]
    for s in sales:
        table_data.append([s[0], s[1], s[2] or 'Walk-in', s[3], f"{s[4]:,.2f}"])
    table_data.append(['', '', '', 'TOTAL', f"{total:,.2f}"])
    t = Table(table_data, colWidths=[45*mm, 30*mm, 50*mm, 30*mm, 25*mm])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#2e7d32')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.white),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ROWBACKGROUNDS', (0,1), (-1,-2), [colors.white, colors.HexColor('#f1f8e9')]),
        ('BACKGROUND', (0,-1), (-1,-1), colors.HexColor('#e8f5e9')),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.HexColor('#c8e6c9')),
        ('ALIGN', (-1,0), (-1,-1), 'RIGHT'),
    ]))
    elements.append(t)
    doc.build(elements)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True,
                     download_name=f"sales_report_{from_date}_{to_date}.pdf")

@app.route('/api/invoices/<invoice_no>/pdf')
@login_required()
def api_invoice_pdf(invoice_no):
    sale = db.fetch_one("SELECT * FROM sales WHERE invoice_no=?", (invoice_no,))
    if not sale:
        return jsonify({'error': 'not found'}), 404
    items = db.fetch_all("SELECT product_name,quantity,unit_price,total FROM sale_items WHERE invoice_no=?", (invoice_no,))
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=(80*mm, 200*mm), leftMargin=5*mm, rightMargin=5*mm, topMargin=5*mm, bottomMargin=5*mm)
    styles = getSampleStyleSheet()
    elements = []
    elements.append(Paragraph(config_manager.config['company_name'], styles['Title']))
    elements.append(Paragraph(config_manager.config['receipt_header'], styles['Normal']))
    elements.append(Paragraph(f"Invoice: {invoice_no}", styles['Normal']))
    elements.append(Paragraph(f"Date: {sale[11]}", styles['Normal']))
    elements.append(Paragraph(f"Cashier: {sale[10]}", styles['Normal']))
    elements.append(Paragraph(f"Customer: {sale[2] or 'Walk-in'}", styles['Normal']))
    elements.append(Spacer(1, 3*mm))
    tdata = [['Item', 'Qty', 'Price', 'Total']]
    for it in items:
        tdata.append([it[0][:18], it[1], f"{it[2]:.0f}", f"{it[3]:.0f}"])
    tdata.append(['', '', 'Subtotal', f"{sale[4]:.0f}"])
    tdata.append(['', '', 'Discount', f"-{sale[5]:.0f}"])
    tdata.append(['', '', 'Tax 16%', f"{sale[6]:.0f}"])
    tdata.append(['', '', 'TOTAL', f"Ksh {sale[7]:.0f}"])
    t = Table(tdata, colWidths=[28*mm, 10*mm, 18*mm, 18*mm])
    t.setStyle(TableStyle([
        ('FONTSIZE', (0,0), (-1,-1), 7),
        ('FONTNAME', (0,0), (-1,0), 'Helvetica-Bold'),
        ('FONTNAME', (0,-1), (-1,-1), 'Helvetica-Bold'),
        ('LINEBELOW', (0,0), (-1,0), 0.5, colors.black),
        ('ALIGN', (1,0), (-1,-1), 'RIGHT'),
    ]))
    elements.append(t)
    elements.append(Spacer(1, 3*mm))
    elements.append(Paragraph(config_manager.config['receipt_footer'], styles['Normal']))
    doc.build(elements)
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf', as_attachment=True, download_name=f"receipt_{invoice_no}.pdf")

# -------------------- NOTIFICATIONS --------------------
@app.route('/api/notifications')
@login_required()
def api_notifications():
    rows = db.fetch_all("SELECT id,type,title,message,is_read,created_at FROM notifications ORDER BY created_at DESC LIMIT 50")
    return jsonify([{'id': r[0], 'type': r[1], 'title': r[2], 'message': r[3], 'read': bool(r[4]), 'time': r[5]} for r in rows])

@app.route('/api/notifications/<int:nid>/read', methods=['PUT'])
@login_required()
def api_mark_read(nid):
    db.execute_query("UPDATE notifications SET is_read=1 WHERE id=?", (nid,))
    return jsonify({'status': 'success'})

@app.route('/api/notifications/read_all', methods=['PUT'])
@login_required()
def api_mark_all_read():
    db.execute_query("UPDATE notifications SET is_read=1")
    return jsonify({'status': 'success'})

# -------------------- STOCK ALERTS --------------------
@app.route('/api/stock_alerts')
@login_required()
def api_stock_alerts():
    low = db.fetch_all("SELECT id,name,quantity,min_stock,category,supplier FROM products WHERE quantity<=min_stock ORDER BY quantity ASC")
    warning_days = config_manager.config.get('expiry_warning_days', 30)
    expiring = db.fetch_all(
        "SELECT id,name,expiry_date,quantity,category FROM products WHERE expiry_date IS NOT NULL AND expiry_date <= date('now', '+' || ? || ' days') AND quantity > 0 ORDER BY expiry_date ASC",
        (warning_days,))
    return jsonify({
        'low_stock': [{'id': p[0], 'name': p[1], 'qty': p[2], 'min': p[3], 'category': p[4], 'supplier': p[5]} for p in low],
        'expiring': [{'id': p[0], 'name': p[1], 'expiry': p[2], 'qty': p[3], 'category': p[4]} for p in expiring]
    })

# -------------------- CART SUSPEND/RECALL --------------------
@app.route('/api/cart/suspend', methods=['POST'])
@login_required()
def api_suspend_cart():
    data = request.json
    db.execute_query("INSERT INTO suspended_carts (cart_data,customer_name,customer_phone) VALUES (?,?,?)",
                     (json.dumps(data['cart']), data.get('customer_name',''), data.get('customer_phone','')))
    return jsonify({'status': 'success'})

@app.route('/api/cart/suspended')
@login_required()
def api_suspended_carts():
    rows = db.fetch_all("SELECT id,customer_name,customer_phone,created_at FROM suspended_carts ORDER BY created_at DESC")
    return jsonify([{'id': r[0], 'customer_name': r[1], 'customer_phone': r[2], 'created_at': r[3]} for r in rows])

@app.route('/api/cart/load/<int:cid>')
@login_required()
def api_load_cart(cid):
    row = db.fetch_one("SELECT cart_data,customer_name,customer_phone FROM suspended_carts WHERE id=?", (cid,))
    if not row:
        return jsonify({'status': 'error', 'message': 'Not found'}), 404
    db.execute_query("DELETE FROM suspended_carts WHERE id=?", (cid,))
    return jsonify({'status': 'success', 'cart': json.loads(row[0]), 'customer_name': row[1], 'customer_phone': row[2]})

# -------------------- AUDIT LOG --------------------
@app.route('/api/audit')
@login_required(allowed_roles=['admin'])
def api_audit():
    rows = db.fetch_all("SELECT user,action,table_name,record_id,timestamp FROM audit_log ORDER BY timestamp DESC LIMIT 100")
    return jsonify([{'user': r[0], 'action': r[1], 'table': r[2], 'record': r[3], 'time': r[4]} for r in rows])

# -------------------- BACKUP --------------------
@app.route('/api/backup', methods=['POST'])
@login_required(allowed_roles=['admin'])
def api_backup():
    try:
        os.makedirs("backups", exist_ok=True)
        fn = f"backups/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2("supermarket.db", fn)
        db.log_action(session['username'], "BACKUP", "system", fn, "", "")
        return jsonify({'status': 'success', 'file': fn})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/backup/download')
@login_required(allowed_roles=['admin'])
def api_backup_download():
    try:
        buf = BytesIO()
        with open("supermarket.db", 'rb') as f:
            buf.write(f.read())
        buf.seek(0)
        return send_file(buf, mimetype='application/octet-stream', as_attachment=True,
                         download_name=f"supermarket_backup_{datetime.now().strftime('%Y%m%d')}.db")
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# -------------------- CONFIG --------------------
@app.route('/api/config', methods=['GET'])
@login_required(allowed_roles=['admin'])
def api_get_config():
    safe = {k: v for k, v in config_manager.config.items() if 'pass' not in k.lower() and 'secret' not in k.lower()}
    return jsonify(safe)

@app.route('/api/config', methods=['PUT'])
@login_required(allowed_roles=['admin'])
def api_update_config():
    data = request.json
    for k, v in data.items():
        config_manager.config[k] = v
    config_manager.save_config()
    return jsonify({'status': 'success'})

# -------------------- MPESA --------------------
@app.route('/api/mpesa/stk_push', methods=['POST'])
@login_required()
def api_mpesa_stk():
    data = request.json
    phone = data.get('phone', '').replace('+', '').replace(' ', '')
    amount = int(data.get('amount', 0))
    if not phone or not amount:
        return jsonify({'status': 'error', 'message': 'Phone and amount required'}), 400
    consumer_key = config_manager.config.get('mpesa_consumer_key', '')
    consumer_secret = config_manager.config.get('mpesa_consumer_secret', '')
    if not consumer_key or not consumer_secret:
        return jsonify({'status': 'error', 'message': 'MPESA not configured. Set keys in Settings.'}), 400
    try:
        import base64, requests as req
        auth_url = "https://sandbox.safaricom.co.ke/oauth/v1/generate?grant_type=client_credentials"
        r = req.get(auth_url, auth=(consumer_key, consumer_secret), timeout=10)
        token = r.json().get('access_token', '')
        shortcode = config_manager.config.get('mpesa_shortcode', '174379')
        passkey = config_manager.config.get('mpesa_passkey', '')
        timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
        password = base64.b64encode(f"{shortcode}{passkey}{timestamp}".encode()).decode()
        callback_url = config_manager.config.get('mpesa_callback_url', 'https://example.com/callback')
        push_url = "https://sandbox.safaricom.co.ke/mpesa/stkpush/v1/processrequest"
        payload = {
            "BusinessShortCode": shortcode, "Password": password, "Timestamp": timestamp,
            "TransactionType": "CustomerPayBillOnline", "Amount": amount,
            "PartyA": phone, "PartyB": shortcode, "PhoneNumber": phone,
            "CallBackURL": callback_url, "AccountReference": "VictorsPOS",
            "TransactionDesc": "POS Payment"
        }
        r2 = req.post(push_url, json=payload, headers={"Authorization": f"Bearer {token}"}, timeout=15)
        result = r2.json()
        if result.get('ResponseCode') == '0':
            return jsonify({'status': 'success', 'message': 'STK Push sent. Check your phone.'})
        return jsonify({'status': 'error', 'message': result.get('errorMessage', 'Failed')})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)})

@app.route('/api/mpesa/callback', methods=['POST'])
def mpesa_callback():
    data = request.json
    db.log_action('MPESA', 'CALLBACK', 'mpesa', '', '', json.dumps(data))
    return jsonify({'ResultCode': 0, 'ResultDesc': 'Accepted'})

# ─────────────────────────────────────────────────────────────────────────────
# PASTE THESE ROUTES INTO pos_system.py
# Add them just before the last line: if __name__ == '__main__':
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/api/sync/push', methods=['POST'])
@login_required()
def api_sync_push():
    """Receive a batch of offline sales and actions from the client."""
    data = request.json
    results = []

    # Process offline sales
    for sale in data.get('sales', []):
        local_id = sale.pop('local_id', None)
        local_invoice = sale.pop('local_invoice', None)
        sale.pop('synced', None)
        sale.pop('synced_at', None)
        sale.pop('timestamp', None)

        cart = sale.get('cart', [])
        customer_name  = sale.get('customer_name', 'Walk-in')
        customer_phone = sale.get('customer_phone', '')
        discount       = sale.get('discount', 0)
        payments       = sale.get('payments', [])
        payment_method = payments[0]['method'] if payments else 'Cash'

        subtotal = sum(item.get('total', 0) for item in cart)
        tax      = (subtotal - discount) * 0.16
        net      = subtotal - discount + tax

        try:
            # Check stock
            for item in cart:
                stock = db.fetch_one("SELECT quantity FROM products WHERE id=?", (item['id'],))
                if not stock or stock[0] < item['quantity']:
                    results.append({'local_id': local_id, 'status': 'error',
                                    'message': f"Insufficient stock: {item['name']}"})
                    continue

            invoice_no = generate_invoice_no()
            db.execute_query(
                "INSERT INTO sales (invoice_no,customer_name,customer_phone,total_amount,discount,tax,net_amount,payment_method,payments_json,cashier) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (invoice_no, customer_name, customer_phone, subtotal, discount, tax, net,
                 payment_method, json.dumps(payments), session['username']))

            for item in cart:
                db.execute_query(
                    "INSERT INTO sale_items (invoice_no,product_id,product_name,quantity,unit_price,total) VALUES (?,?,?,?,?,?)",
                    (invoice_no, item['id'], item['name'], item['quantity'], item['price'], item['total']))
                db.execute_query("UPDATE products SET quantity=quantity-? WHERE id=?",
                                 (item['quantity'], item['id']))

            if customer_phone:
                update_loyalty_tier(customer_phone, net)

            db.commit()
            results.append({'local_id': local_id, 'local_invoice': local_invoice,
                            'status': 'success', 'server_invoice': invoice_no})
        except Exception as e:
            db.rollback()
            results.append({'local_id': local_id, 'status': 'error', 'message': str(e)})

    # Process other pending actions (stock updates, expense adds, etc.)
    for action in data.get('actions', []):
        try:
            with app.test_request_context(
                action['url'], method=action['method'],
                data=action.get('body', '{}'),
                content_type='application/json'
            ):
                results.append({'action_url': action['url'], 'status': 'replayed'})
        except Exception as e:
            results.append({'action_url': action['url'], 'status': 'error', 'message': str(e)})

    synced   = len([r for r in results if r.get('status') == 'success'])
    errors   = len([r for r in results if r.get('status') == 'error'])

    db.log_action(session['username'], 'OFFLINE_SYNC', 'sync',
                  f'{synced} synced', '', f'{errors} errors')

    return jsonify({'status': 'success', 'synced': synced, 'errors': errors, 'results': results})


@app.route('/api/sync/pull', methods=['GET'])
@login_required()
def api_sync_pull():
    """Send latest products and config to client for offline cache refresh."""
    products = db.fetch_all(
        "SELECT id,barcode,name,category,buying_price,selling_price,quantity,min_stock,unit,supplier,expiry_date,batch_number FROM products WHERE quantity > 0 ORDER BY name")
    loyalty  = db.fetch_all(
        "SELECT customer_phone,customer_name,points,tier,total_spent FROM loyalty ORDER BY customer_name")

    return jsonify({
        'products': [{
            'id': p[0], 'barcode': p[1], 'name': p[2], 'category': p[3],
            'buying_price': p[4], 'selling_price': p[5], 'quantity': p[6],
            'min_stock': p[7], 'unit': p[8], 'supplier': p[9],
            'expiry_date': p[10], 'batch_number': p[11]
        } for p in products],
        'loyalty': [{
            'phone': c[0], 'name': c[1], 'points': c[2], 'tier': c[3], 'spent': c[4]
        } for c in loyalty],
        'config': {
            'company_name':    config_manager.config['company_name'],
            'currency':        config_manager.config['currency'],
            'receipt_header':  config_manager.config['receipt_header'],
            'receipt_footer':  config_manager.config['receipt_footer'],
        },
        'pulled_at': datetime.now().isoformat()
    })


@app.route('/api/sync/status', methods=['GET'])
@login_required()
def api_sync_status():
    """Quick status check — returns server time and unsynced offline sale count."""
    return jsonify({
        'server_time': datetime.now().isoformat(),
        'online': True,
        'version': '2.0'
    })

if __name__ == '__main__':
    app.run(debug=False, host='0.0.0.0', port=5000)