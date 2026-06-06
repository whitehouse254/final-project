import sqlite3
import datetime
import hashlib
import random
import os
import shutil
import json
from datetime import datetime, timedelta
from flask import Flask, request, redirect, url_for, session, jsonify

app = Flask(__name__)
app.secret_key = 'supermarket-secret-key-change-in-production'


# ==================== CONFIGURATION MANAGER ====================
class ConfigManager:
    DEFAULT_CONFIG = {
        "company_name": "VICTOR'S SUPER MARKET",
        "currency": "Ksh",
        "tax_rates": [
            {"name": "VAT 16%", "rate": 0.16, "categories": ["general", "electronics", "beverages", "snacks"]}],
        "payment_methods": ["Cash", "Card", "MPESA", "Bank Transfer", "Voucher"],
        "loyalty_points_per_ksh": 0.01,
        "low_stock_threshold": 5,
        "auto_backup": True,
        "backup_interval_days": 1,
        "receipt_header": "THANK YOU FOR SHOPPING WITH US!",
        "receipt_footer": "Visit again!",
        "enable_branch_support": False,
        "branches": [{"id": 1, "name": "Main Store", "location": "Nairobi"}]
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
            return self.DEFAULT_CONFIG

    def save_config(self, config=None):
        with open(self.config_path, 'w') as f:
            json.dump(config or self.config, f, indent=4)

    def get_tax_rate(self, product_category):
        for t in self.config["tax_rates"]:
            if product_category in t.get("categories", []):
                return t["rate"]
        return 0.16


config_manager = ConfigManager()


# ==================== DATABASE ====================
class Database:
    def __init__(self, db_name="supermarket.db"):
        self.conn = sqlite3.connect(db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        self.add_activity_column()
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
                cash_tendered REAL,
                change_given REAL,
                cashier TEXT,
                sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                branch_id INTEGER DEFAULT 1
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
                product_id INTEGER,
                product_name TEXT,
                quantity INTEGER,
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
        ''')
        self.conn.commit()

    def add_activity_column(self):
        try:
            self.cursor.execute("ALTER TABLE users ADD COLUMN last_activity TIMESTAMP")
            self.conn.commit()
        except sqlite3.OperationalError:
            pass  # column already exists

    def populate_initial_data(self):
        # Admin
        hashed_admin = hashlib.sha256("victor@123".encode()).hexdigest()
        if not self.fetch_one("SELECT id FROM users WHERE username='victor'"):
            self.execute_query(
                "INSERT INTO users (username, password, role, full_name, last_activity) VALUES (?,?,?,?,?)",
                ("victor", hashed_admin, "admin", "Victor Admin", None))

        # Cashiers with various names
        cashiers_data = [
            ("cashier", "", "cashier", "Store Cashier"),
            ("john", "john123", "cashier", "John Doe"),
            ("mary", "mary123", "cashier", "Mary Smith"),
            ("peter", "peter123", "cashier", "Peter Omondi"),
            ("sarah", "sarah123", "cashier", "Sarah Wanjiku"),
            ("james", "james123", "cashier", "James Mwangi")
        ]
        for username, password, role, full_name in cashiers_data:
            if not self.fetch_one("SELECT id FROM users WHERE username=?", (username,)):
                hashed = hashlib.sha256(password.encode()).hexdigest()
                self.execute_query(
                    "INSERT INTO users (username, password, role, full_name, last_activity) VALUES (?,?,?,?,?)",
                    (username, hashed, role, full_name, None))

        # Suppliers
        if self.fetch_one("SELECT COUNT(*) FROM suppliers")[0] == 0:
            for i in range(1, 21):
                name = f"Supplier {i}"
                contact = f"Contact Person {i}"
                phone = f"07{random.randint(10000000, 99999999)}"
                email = f"supplier{i}@mail.com"
                address = f"Address {i}, Nairobi"
                self.execute_query("INSERT INTO suppliers (name,contact_person,phone,email,address) VALUES (?,?,?,?,?)",
                                   (name, contact, phone, email, address))

        # Loyalty customers
        first_names = ['John', 'Jane', 'Michael', 'Sarah', 'David', 'Linda', 'James', 'Mary', 'Robert', 'Patricia']
        last_names = ['Doe', 'Smith', 'Johnson', 'Brown', 'Williams', 'Jones', 'Garcia', 'Miller', 'Davis', 'Wilson']
        if self.fetch_one("SELECT COUNT(*) FROM loyalty")[0] == 0:
            for _ in range(50):
                name = f"{random.choice(first_names)} {random.choice(last_names)}"
                phone = f"07{random.randint(10000000, 99999999)}"
                points = random.randint(0, 5000)
                spent = random.randint(0, 50000)
                self.execute_query(
                    "INSERT OR IGNORE INTO loyalty (customer_name, customer_phone, points, total_spent) VALUES (?,?,?,?)",
                    (name, phone, points, spent))

        self.populate_products()
        self.conn.commit()

    def populate_products(self):
        self.cursor.execute("SELECT COUNT(*) FROM products")
        if self.cursor.fetchone()[0] > 0:
            return

        self.cursor.execute("SELECT name FROM suppliers")
        suppliers = [row[0] for row in self.cursor.fetchall()]
        if not suppliers:
            suppliers = ["General Supplier"]

        categories = {
            "Grains & Cereals": ["Rice (1kg)", "Rice (5kg)", "Maize Flour (1kg)", "Wheat Flour (1kg)", "Oats (500g)"],
            "Dairy & Eggs": ["Fresh Milk (1L)", "Yogurt (500ml)", "Cheese (250g)", "Butter (250g)", "Eggs (12pcs)"],
            "Beverages": ["Mineral Water (1L)", "Soda (330ml)", "Juice (1L)", "Coffee (50g)", "Tea Bags (100)"],
            "Snacks": ["Potato Chips (100g)", "Chocolate Bar (50g)", "Biscuits (100g)", "Peanuts (100g)",
                       "Cookies (150g)"],
            "Fruits": ["Apples (1kg)", "Bananas (1 bunch)", "Oranges (1kg)", "Mangoes (1kg)", "Grapes (500g)"],
            "Meat": ["Beef (1kg)", "Chicken Whole", "Pork (500g)", "Fish Fillet (500g)", "Sausages (8pcs)"]
        }
        units = ["pcs", "kg", "L", "g", "ml", "pack"]
        product_count = 0

        for category, product_names in categories.items():
            for name in product_names:
                if product_count >= 100:
                    break
                buying_price = round(random.uniform(10, 800), 2)
                selling_price = round(buying_price * random.uniform(1.2, 1.8), 2)
                quantity = random.randint(20, 500)
                min_stock = random.randint(5, 30)
                unit = random.choice(units)
                barcode = f"890{random.randint(1000000000, 9999999999)}"
                supplier = random.choice(suppliers)
                self.cursor.execute(
                    '''INSERT INTO products (barcode, name, category, buying_price, selling_price, quantity, min_stock, unit, supplier) VALUES (?,?,?,?,?,?,?,?,?)''',
                    (barcode, name, category, buying_price, selling_price, quantity, min_stock, unit, supplier))
                product_count += 1
                self.cursor.execute(
                    '''INSERT INTO stock_movements (product_id, movement_type, quantity, reason, user) VALUES (?, 'stock_in', ?, 'Initial stock', 'system')''',
                    (product_count, quantity))
        self.conn.commit()
        print(f"✅ Added {product_count} products.")

    def execute_query(self, query, params=()):
        self.cursor.execute(query, params)

    def fetch_all(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor.fetchall()

    def fetch_one(self, query, params=()):
        self.cursor.execute(query, params)
        return self.cursor.fetchone()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def log_action(self, user, action, table, rid, old="", new=""):
        self.execute_query(
            "INSERT INTO audit_log (user,action,table_name,record_id,old_value,new_value) VALUES (?,?,?,?,?,?)",
            (user, action, table, str(rid), str(old), str(new)))
        self.commit()

    def close(self):
        self.conn.close()


db = Database()


# ==================== BEFORE REQUEST ====================
@app.before_request
def update_user_activity():
    if 'user_id' in session:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute_query("UPDATE users SET last_activity = ? WHERE id = ?", (now, session['user_id']))
        db.commit()


# ==================== HELPER FUNCTIONS ====================
def login_required(allowed_roles=None):
    def decorator(f):
        def wrapper(*args, **kwargs):
            if 'user_id' not in session:
                return redirect(url_for('login'))
            if allowed_roles and session.get('role') not in allowed_roles:
                return "Access denied", 403
            return f(*args, **kwargs)

        wrapper.__name__ = f.__name__
        return wrapper

    return decorator


def generate_invoice_no():
    today = datetime.now().date()
    last = db.fetch_one("SELECT invoice_no FROM sales WHERE DATE(sale_date) = ? ORDER BY id DESC LIMIT 1", (today,))
    if last:
        parts = last[0].split('-')
        last_num = int(parts[-1])
        seq = last_num + 1
    else:
        seq = 1
    return f"INV-{today.strftime('%Y%m%d')}-{seq:04d}"


def render_page(title, content_html):
    company = config_manager.config['company_name']
    year = datetime.now().year
    username = session.get('username', 'Guest')
    role = session.get('role', '')
    return f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{company} - {title}</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/css/bootstrap.min.css" rel="stylesheet">
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0-beta3/css/all.min.css">
    <style>
        :root {{ --primary: #0d6e4d; --primary-dark: #0a5a3e; --accent: #f39c12; --secondary: #2c3e50; }}
        body {{ background: linear-gradient(135deg, #e0eafc 0%, #cfdef3 100%); font-family: 'Segoe UI', sans-serif; }}
        .navbar {{ background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%) !important; }}
        .sidebar {{ background: var(--secondary); min-height: calc(100vh - 70px); }}
        .sidebar a {{ color: #ecf0f1; display: flex; align-items: center; gap: 12px; padding: 0.85rem 1.5rem; text-decoration: none; transition: 0.2s; border-left: 3px solid transparent; }}
        .sidebar a:hover {{ background: #1e2b38; border-left-color: var(--accent); }}
        .stat-card {{ background: white; border-radius: 1rem; padding: 1.25rem; box-shadow: 0 10px 25px -5px rgba(0,0,0,0.05); transition: transform 0.2s; }}
        .stat-card:hover {{ transform: translateY(-3px); }}
        .btn {{ border-radius: 0.5rem; font-weight: 600; transition: 0.2s; }}
        .btn-primary {{ background: var(--primary); }}
        .btn-primary:hover {{ background: var(--primary-dark); transform: translateY(-2px); }}
        .table {{ background: white; border-radius: 0.75rem; overflow: hidden; }}
        .card {{ border-radius: 1rem; border: none; box-shadow: 0 4px 12px rgba(0,0,0,0.05); }}
        footer {{ text-align: center; margin-top: 2rem; padding: 1rem; color: #6c757d; }}
    </style>
</head>
<body>
    <nav class="navbar navbar-dark">
        <div class="container-fluid">
            <span class="navbar-brand"><i class="fas fa-store me-2"></i>{company}</span>
            <div class="d-flex align-items-center">
                <span class="navbar-text me-3"><i class="fas fa-user-circle me-1"></i>{username} ({role})</span>
                <a href="/logout" class="btn btn-outline-light btn-sm"><i class="fas fa-sign-out-alt me-1"></i>Logout</a>
            </div>
        </div>
    </nav>
    <div class="container-fluid">
        <div class="row">
            <div class="col-md-2 p-0 sidebar">
                <div class="mt-3">
                    <a href="/dashboard"><i class="fas fa-tachometer-alt"></i> Dashboard</a>
                    <a href="/pos"><i class="fas fa-shopping-cart"></i> Point of Sale</a>
                    {('<a href="/inventory"><i class="fas fa-boxes"></i> Inventory</a>' if role == 'admin' else '')}
                    <a href="/reports"><i class="fas fa-chart-line"></i> Reports</a>
                    {('<a href="/invoices"><i class="fas fa-file-invoice"></i> Invoices</a>' if role == 'admin' else '')}
                    {('<a href="/users"><i class="fas fa-users"></i> Users</a>' if role == 'admin' else '')}
                    {('<a href="/cashier_activity"><i class="fas fa-user-clock"></i> Cashier Activity</a>' if role == 'admin' else '')}
                    <a href="/change_password"><i class="fas fa-key"></i> Change Password</a>
                    <a href="/stock_alerts"><i class="fas fa-exclamation-triangle"></i> Stock Alerts</a>
                    <a href="/returns"><i class="fas fa-undo-alt"></i> Returns</a>
                    <a href="/loyalty"><i class="fas fa-gem"></i> Loyalty</a>
                    {('<a href="/expenses"><i class="fas fa-money-bill-wave"></i> Expenses</a>' if role == 'admin' else '')}
                    {('<a href="/backup"><i class="fas fa-database"></i> Backup</a>' if role == 'admin' else '')}
                    <a href="/charts"><i class="fas fa-chart-pie"></i> Charts</a>
                </div>
            </div>
            <div class="col-md-10 p-4">
                {content_html}
                <footer>&copy; {year} {company} – All rights reserved</footer>
            </div>
        </div>
    </div>
    <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.0-alpha1/dist/js/bootstrap.bundle.min.js"></script>
</body>
</html>'''


# ==================== ROUTES ====================
@app.route('/')
def index():
    return redirect(url_for('login'))


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        hashed = hashlib.sha256(password.encode()).hexdigest()
        user = db.fetch_one("SELECT id, username, role, full_name FROM users WHERE username=? AND password=?",
                            (username, hashed))
        if user:
            session['user_id'] = user[0]
            session['username'] = user[1]
            session['role'] = user[2]
            session['full_name'] = user[3]
            db.log_action(username, "LOGIN", "users", user[0], "", "Success")
            # Update last activity immediately
            now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            db.execute_query("UPDATE users SET last_activity = ? WHERE id = ?", (now, user[0]))
            db.commit()
            return redirect(url_for('dashboard'))
        else:
            content = '''
            <div class="row justify-content-center"><div class="col-md-4"><div class="card"><div class="card-header">Login</div><div class="card-body">
            <div class="alert alert-danger">Invalid credentials</div>
            <form method="post"><div class="mb-3"><label>Username</label><input type="text" name="username" class="form-control" required></div>
            <div class="mb-3"><label>Password</label><input type="password" name="password" class="form-control"></div>
            <button type="submit" class="btn btn-primary w-100">LOGIN</button></form>
            <div class="mt-3 text-center small">Demo: victor/victor@123 (admin) | cashier/[blank] (cashier) | john/john123 (cashier)</div>
            </div></div></div></div>'''
            return render_page("Login", content)
    content = '''
    <div class="row justify-content-center"><div class="col-md-4"><div class="card"><div class="card-header">Login</div><div class="card-body">
    <form method="post"><div class="mb-3"><label>Username</label><input type="text" name="username" class="form-control" required></div>
    <div class="mb-3"><label>Password</label><input type="password" name="password" class="form-control"></div>
    <button type="submit" class="btn btn-primary w-100">LOGIN</button></form>
    </div></div></div></div>'''
    return render_page("Login", content)


@app.route('/logout')
def logout():
    if 'user_id' in session:
        db.log_action(session['username'], "LOGOUT", "users", session['user_id'], "", "User logged out")
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        db.execute_query("UPDATE users SET last_activity = ? WHERE id = ?", (now, session['user_id']))
        db.commit()
    session.clear()
    return redirect(url_for('login'))


# -------------------- DASHBOARD --------------------
@app.route('/dashboard')
@login_required()
def dashboard():
    today = datetime.now().date()
    currency = config_manager.config['currency']
    today_total = db.fetch_one("SELECT COALESCE(SUM(net_amount),0), COUNT(*) FROM sales WHERE DATE(sale_date)=?",
                               (today,))
    top_products = db.fetch_all(
        "SELECT p.name, SUM(si.quantity) as qty FROM sale_items si JOIN sales s ON si.invoice_no = s.invoice_no JOIN products p ON si.product_id = p.id WHERE DATE(s.sale_date)=? GROUP BY si.product_id ORDER BY qty DESC LIMIT 5",
        (today,))
    low_stock = db.fetch_all("SELECT name, quantity, min_stock FROM products WHERE quantity <= min_stock LIMIT 5")
    total_products = db.fetch_one("SELECT COUNT(*) FROM products")[0]
    low_stock_count = db.fetch_one("SELECT COUNT(*) FROM products WHERE quantity <= min_stock")[0]
    content = f'''
    <div class="row mb-4">
        <div class="col-md-4"><div class="stat-card"><i class="fas fa-chart-line fa-2x text-primary"></i><div class="stat-number">{currency} {today_total[0]:,.2f}</div><div>Today's Sales</div><div class="text-muted">{today_total[1]} transactions</div></div></div>
        <div class="col-md-4"><div class="stat-card"><i class="fas fa-exclamation-triangle fa-2x text-warning"></i><div class="stat-number">{low_stock_count}</div><div>Low Stock Items</div><div class="text-muted">out of {total_products} products</div></div></div>
        <div class="col-md-4"><div class="stat-card"><i class="fas fa-trophy fa-2x text-success"></i><div class="stat-number">⭐ Top Product</div><div>{top_products[0][0] if top_products else 'None'}</div><div class="text-muted">Sold: {top_products[0][1] if top_products else 0} units</div></div></div>
    </div>
    <div class="row">
        <div class="col-md-6"><div class="card"><div class="card-header"><i class="fas fa-chart-simple me-2"></i>Today's Top Products</div><div class="card-body"><ul class="list-group">{''.join(f'<li class="list-group-item d-flex justify-content-between"><span>{p[0]}</span><span class="badge bg-primary rounded-pill">{p[1]} sold</span></li>' for p in top_products)}</ul></div></div></div>
        <div class="col-md-6"><div class="card"><div class="card-header"><i class="fas fa-bell me-2"></i>Low Stock Alerts</div><div class="card-body">{'<ul class="list-group">' + ''.join(f'<li class="list-group-item d-flex justify-content-between"><span>{l[0]}</span><span class="badge bg-danger">Stock: {l[1]} / Min: {l[2]}</span></li>' for l in low_stock) + '</ul>' if low_stock else '<div class="alert alert-success">All stock levels are healthy ✅</div>'}</div></div></div>
    </div>
    '''
    return render_page("Dashboard", content)


# -------------------- POS WITH RECEIPT MODAL & CASHIER NAME --------------------
@app.route('/pos', methods=['GET', 'POST'])
@login_required(allowed_roles=['admin', 'cashier'])
def pos():
    if request.method == 'POST':
        data = request.json
        cart = data.get('cart', [])
        customer_name = data.get('customer_name', '')
        customer_phone = data.get('customer_phone', '')
        discount_amount = float(data.get('discount', 0))
        payment_method = data.get('payment_method', 'Cash')
        if not cart:
            return jsonify({'status': 'error', 'message': 'Cart is empty'})
        subtotal = sum(item['total'] for item in cart)
        if discount_amount > subtotal:
            discount_amount = subtotal
        taxable = subtotal - discount_amount
        tax = taxable * 0.16
        net_total = taxable + tax
        invoice_no = generate_invoice_no()
        try:
            db.conn.execute("BEGIN TRANSACTION")
            for item in cart:
                stock_row = db.fetch_one("SELECT quantity FROM products WHERE id=?", (item['id'],))
                if not stock_row or stock_row[0] < item['quantity']:
                    db.rollback()
                    return jsonify({'status': 'error', 'message': f"Insufficient stock for {item['name']}"})
            db.cursor.execute(
                "INSERT INTO sales (invoice_no, customer_name, customer_phone, total_amount, discount, tax, net_amount, payment_method, cashier) VALUES (?,?,?,?,?,?,?,?,?)",
                (invoice_no, customer_name, customer_phone, subtotal, discount_amount, tax, net_total, payment_method,
                 session['username']))
            for item in cart:
                db.cursor.execute(
                    "INSERT INTO sale_items (invoice_no, product_id, product_name, quantity, unit_price, total) VALUES (?,?,?,?,?,?)",
                    (invoice_no, item['id'], item['name'], item['quantity'], item['price'], item['total']))
                db.cursor.execute("UPDATE products SET quantity = quantity - ? WHERE id=?",
                                  (item['quantity'], item['id']))
            if customer_phone:
                points = int(subtotal * 0.01)
                db.cursor.execute(
                    "UPDATE loyalty SET points = points + ?, total_spent = total_spent + ? WHERE customer_phone=?",
                    (points, net_total, customer_phone))
                if db.cursor.rowcount == 0:
                    db.cursor.execute("INSERT INTO loyalty (customer_phone, customer_name, points) VALUES (?,?,?)",
                                      (customer_phone, customer_name, points))
            db.commit()
            db.log_action(session['username'], "SALE", "sales", invoice_no, "", f"Total: {net_total}")
            return jsonify({'status': 'success', 'invoice': invoice_no, 'net_total': net_total})
        except Exception as e:
            db.rollback()
            return jsonify({'status': 'error', 'message': str(e)})
    else:
        products = db.fetch_all(
            "SELECT id, name, selling_price, quantity, category FROM products WHERE quantity > 0 ORDER BY name")
        customers = db.fetch_all("SELECT customer_name, customer_phone FROM loyalty ORDER BY customer_name")
        currency = config_manager.config['currency']
        company = config_manager.config['company_name']
        receipt_header = config_manager.config['receipt_header']
        receipt_footer = config_manager.config['receipt_footer']
        cashier_name = session.get('full_name', session.get('username', 'Cashier'))

        product_rows = ""
        for p in products:
            product_rows += f'''
            <tr data-category="{p[4]}"><td>{p[0]}</td><td>{p[1]}</td><td>{currency} {p[2]}</td><td>{p[3]}</td><td>{p[4]}</td>
            <td><button class="btn btn-sm btn-success add-to-cart" data-id="{p[0]}" data-name="{p[1]}" data-price="{p[2]}" data-stock="{p[3]}"><i class="fas fa-cart-plus"></i> Add</button></td></tr>'''
        customer_options = "".join(f'<option value="{c[1]}">{c[0]} - {c[1]}</option>' for c in customers)
        categories = sorted(set(p[4] for p in products if p[4]))
        category_options = '<option value="all">All Categories</option>' + ''.join(
            f'<option value="{cat}">{cat}</option>' for cat in categories)

        content = f'''
        <div class="row">
            <div class="col-md-7"><div class="card"><div class="card-header"><i class="fas fa-box-open me-2"></i>Products</div><div class="card-body">
            <div class="row mb-2"><div class="col-md-6"><input type="text" id="barcodeInput" placeholder="Scan barcode" class="form-control"></div><div class="col-md-6"><select id="categoryFilter" class="form-select">{category_options}</select></div></div>
            <input type="text" id="search" class="form-control mb-3" placeholder="Search...">
            <div style="height:500px; overflow-y:auto;"><table class="table table-sm"><thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Stock</th><th>Category</th><th></th></thead><tbody id="product-list">{product_rows}</tbody></table></div>
            </div></div></div>
            <div class="col-md-5"><div class="card"><div class="card-header"><i class="fas fa-shopping-cart me-2"></i>Cart</div><div class="card-body">
            <div style="height:300px; overflow-y:auto;"><table class="table table-sm"><thead><tr><th>Name</th><th>Qty</th><th>Price</th><th>Total</th><th></th></thead><tbody id="cart-items"></tbody></table></div><hr>
            <div class="mb-2"><label>Customer Name</label><input type="text" id="cust_name" class="form-control" list="customerList"></div>
            <div class="mb-2"><label>Customer Phone</label><input type="text" id="cust_phone" class="form-control" list="customerList"></div>
            <datalist id="customerList">{customer_options}</datalist>
            <div class="mb-2"><label>Discount (%)</label><input type="number" id="discount" class="form-control" value="0" min="0" max="50"></div>
            <div class="mb-2"><label>Payment Method</label><select id="paymentMethod" class="form-select"><option>Cash</option><option>Card</option><option>MPESA</option><option>Bank Transfer</option></select></div>
            <h4>Total: <span id="total">{currency} 0.00</span></h4>
            <button id="checkout-btn" class="btn btn-success w-100"><i class="fas fa-check-circle me-2"></i>Checkout</button>
            </div></div></div>
        </div>
        <!-- Receipt Modal -->
        <div class="modal fade" id="receiptModal" tabindex="-1"><div class="modal-dialog modal-lg"><div class="modal-content"><div class="modal-header bg-success text-white"><h5 class="modal-title"><i class="fas fa-receipt"></i> Receipt Preview</h5><button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button></div><div class="modal-body" id="receiptContent"></div><div class="modal-footer"><button type="button" class="btn btn-secondary" data-bs-dismiss="modal"><i class="fas fa-times"></i> Cancel</button><button type="button" id="confirmPurchaseBtn" class="btn btn-success"><i class="fas fa-check"></i> Confirm Purchase</button></div></div></div></div>
        <script>
            let cart = [];
            const currency = "{currency}";
            const company = "{company}";
            const receiptHeader = `{receipt_header}`;
            const receiptFooter = `{receipt_footer}`;
            const cashierName = "{cashier_name}";
            let pendingCheckoutData = null;
            function updateCartUI() {{
                let tbody = document.getElementById('cart-items');
                tbody.innerHTML = '';
                let total = 0;
                cart.forEach((item, idx) => {{
                    let row = tbody.insertRow();
                    row.insertCell(0).innerText = item.name;
                    row.insertCell(1).innerText = item.qty;
                    row.insertCell(2).innerText = currency + ' ' + item.price.toFixed(2);
                    row.insertCell(3).innerText = currency + ' ' + (item.price * item.qty).toFixed(2);
                    let delCell = row.insertCell(4);
                    let delBtn = document.createElement('button');
                    delBtn.innerHTML = '<i class="fas fa-trash-alt"></i>';
                    delBtn.className = 'btn btn-sm btn-danger';
                    delBtn.onclick = () => {{ cart.splice(idx,1); updateCartUI(); }};
                    delCell.appendChild(delBtn);
                    total += item.price * item.qty;
                }});
                document.getElementById('total').innerText = currency + ' ' + total.toFixed(2);
            }}
            function addToCart(id, name, price, stock) {{
                let qty = prompt('Enter quantity (max ' + stock + ')', '1');
                if(qty && !isNaN(qty) && qty>0 && qty<=stock) {{
                    let existing = cart.find(i => i.id == id);
                    if(existing) existing.qty += parseInt(qty);
                    else cart.push({{id: id, name: name, price: price, qty: parseInt(qty)}});
                    updateCartUI();
                }} else alert('Invalid quantity');
            }}
            function showReceiptPreview() {{
                let subtotal = cart.reduce((s,i)=> s + i.price * i.qty, 0);
                let discountPercent = parseFloat(document.getElementById('discount').value) || 0;
                let discountAmount = subtotal * discountPercent / 100;
                let taxable = subtotal - discountAmount;
                let tax = taxable * 0.16;
                let netTotal = taxable + tax;
                let paymentMethod = document.getElementById('paymentMethod').value;
                let customerName = document.getElementById('cust_name').value || 'Guest';
                let customerPhone = document.getElementById('cust_phone').value || '';
                let itemsHtml = '<table class="table table-sm"><thead><tr><th>Item</th><th>Qty</th><th>Price</th><th>Total</th></thead><tbody>';
                cart.forEach(item => {{
                    itemsHtml += `<tr><td>${{item.name}}</td><td>${{item.qty}}</td><td>{currency} ${{item.price.toFixed(2)}}</td><td>{currency} ${{(item.price * item.qty).toFixed(2)}}</td></tr>`;
                }});
                itemsHtml += '</tbody></table>';
                let receiptHtml = `
                    <div style="font-family: monospace; text-align: center;">
                        <h4>${{company}}</h4>
                        <p>${{receiptHeader}}<br>${{new Date().toLocaleString()}}<br><strong>Cashier:</strong> ${{cashierName}}</p>
                        <hr>
                        <p><strong>Customer:</strong> ${{customerName}} ${{customerPhone ? '('+customerPhone+')' : ''}}<br>
                        <strong>Payment:</strong> ${{paymentMethod}}<br>
                        <strong>Discount:</strong> {currency} ${{discountAmount.toFixed(2)}}</p>
                        ${{itemsHtml}}
                        <hr>
                        <p><strong>Subtotal:</strong> {currency} ${{subtotal.toFixed(2)}}<br>
                        <strong>Tax (16%):</strong> {currency} ${{tax.toFixed(2)}}<br>
                        <strong>Total:</strong> {currency} ${{netTotal.toFixed(2)}}</p>
                        <hr>
                        <p>${{receiptFooter}}</p>
                    </div>
                `;
                document.getElementById('receiptContent').innerHTML = receiptHtml;
                pendingCheckoutData = {{
                    cart: cart.map(i => ({{id: i.id, name: i.name, quantity: i.qty, price: i.price, total: i.price * i.qty}})),
                    customer_name: customerName,
                    customer_phone: customerPhone,
                    discount: discountAmount,
                    payment_method: paymentMethod
                }};
                let modal = new bootstrap.Modal(document.getElementById('receiptModal'));
                modal.show();
            }}
            document.addEventListener('DOMContentLoaded', function() {{
                document.getElementById('product-list').addEventListener('click', function(e) {{
                    let btn = e.target.closest('.add-to-cart');
                    if(btn) addToCart(btn.dataset.id, btn.dataset.name, parseFloat(btn.dataset.price), parseInt(btn.dataset.stock));
                }});
                document.getElementById('barcodeInput').addEventListener('keypress', function(e) {{
                    if(e.key === 'Enter') {{
                        let barcode = this.value;
                        if(!barcode) return;
                        fetch(`/product_by_barcode/${{barcode}}`).then(res=>res.json()).then(product=>{{
                            if(product && product.id) addToCart(product.id, product.name, product.price, product.stock);
                            else alert('Not found');
                            this.value='';
                        }});
                    }}
                }});
                document.getElementById('categoryFilter').addEventListener('change', function() {{
                    let selected = this.value;
                    document.querySelectorAll('#product-list tr').forEach(row => {{
                        row.style.display = (selected==='all' || row.dataset.category===selected) ? '' : 'none';
                    }});
                }});
                document.getElementById('search').addEventListener('keyup', function() {{
                    let term = this.value.toLowerCase();
                    document.querySelectorAll('#product-list tr').forEach(row => {{
                        let name = row.cells[1].innerText.toLowerCase();
                        row.style.display = name.includes(term) ? '' : 'none';
                    }});
                }});
                document.getElementById('cust_phone').addEventListener('change', function() {{
                    let phone = this.value;
                    let customerList = document.getElementById('customerList').options;
                    for(let opt of customerList) if(opt.value === phone) {{ document.getElementById('cust_name').value = opt.text.split(' - ')[0]; break; }}
                }});
                document.getElementById('checkout-btn').addEventListener('click', () => {{
                    if(cart.length === 0) {{ alert('Cart empty'); return; }}
                    let discountPercent = parseFloat(document.getElementById('discount').value) || 0;
                    if(discountPercent > 50) {{ alert('Max discount 50%'); return; }}
                    showReceiptPreview();
                }});
                document.getElementById('confirmPurchaseBtn').addEventListener('click', function() {{
                    if(!pendingCheckoutData) return;
                    fetch('/pos', {{
                        method: 'POST',
                        headers: {{'Content-Type': 'application/json'}},
                        body: JSON.stringify(pendingCheckoutData)
                    }}).then(res => res.json()).then(data => {{
                        if(data.status === 'success') {{
                            let modalEl = document.getElementById('receiptModal');
                            let modal = bootstrap.Modal.getInstance(modalEl);
                            modal.hide();
                            if(confirm('Sale complete! Print receipt?')) {{
                                let receiptText = `🏪 ${{company}}\\n${{receiptHeader}}\\nCashier: ${{cashierName}}\\nInvoice: ${{data.invoice}}\\nTotal: {currency} ${{data.net_total.toFixed(2)}}\\n${{receiptFooter}}`;
                                let printWin = window.open('', '_blank', 'width=400,height=600');
                                printWin.document.write('<pre>' + receiptText + '</pre>');
                                printWin.print();
                            }}
                            cart = [];
                            updateCartUI();
                            location.reload();
                        }} else {{ alert('Error: ' + data.message); }}
                    }}).catch(err => alert('Network error: ' + err));
                }});
            }});
        </script>'''
        return render_page("Point of Sale", content)


@app.route('/product_by_barcode/<barcode>')
@login_required(allowed_roles=['admin', 'cashier'])
def product_by_barcode(barcode):
    prod = db.fetch_one("SELECT id, name, selling_price, quantity FROM products WHERE barcode=? AND quantity>0",
                        (barcode,))
    return jsonify({'id': prod[0], 'name': prod[1], 'price': prod[2], 'stock': prod[3]}) if prod else jsonify({}), 404


# -------------------- CASHIER ACTIVITY MONITOR --------------------
@app.route('/cashier_activity')
@login_required(allowed_roles=['admin'])
def cashier_activity():
    from_date = request.args.get('from_date', (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to_date', datetime.now().strftime('%Y-%m-%d'))
    from_datetime = f"{from_date} 00:00:00"
    to_datetime = f"{to_date} 23:59:59"
    cashiers = db.fetch_all("SELECT id, username, full_name, last_activity FROM users WHERE role = 'cashier'")
    now = datetime.now()
    active_threshold = (now - timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    active_cashiers = [c for c in cashiers if c[3] and c[3] > active_threshold]
    history = db.fetch_all(
        "SELECT user, action, timestamp, record_id FROM audit_log WHERE table_name = 'users' AND (action = 'LOGIN' OR action = 'LOGOUT') AND timestamp BETWEEN ? AND ? ORDER BY timestamp DESC",
        (from_datetime, to_datetime))
    login_counts = db.fetch_all(
        "SELECT user, COUNT(*) FROM audit_log WHERE action = 'LOGIN' AND table_name = 'users' AND timestamp BETWEEN ? AND ? GROUP BY user",
        (from_datetime, to_datetime))
    content = f'''
    <h2><i class="fas fa-user-clock me-2"></i>Cashier Activity Monitor</h2>
    <div class="row mb-4">
        <div class="col-md-6"><div class="card"><div class="card-header bg-success text-white">🟢 Currently Active Cashiers (last 5 min)</div><div class="card-body">{''.join(f'<p><i class="fas fa-user-check text-success"></i> <strong>{c[2]}</strong> ({c[1]}) - Last activity: {c[3]}</p>' for c in active_cashiers) or '<p class="text-muted">No cashiers currently active.</p>'}</div></div></div>
        <div class="col-md-6"><div class="card"><div class="card-header bg-info text-white">📊 Login Counts (Selected Period)</div><div class="card-body">{''.join(f'<p><strong>{lc[0]}</strong>: {lc[1]} logins</p>' for lc in login_counts) or '<p class="text-muted">No logins in this period.</p>'}</div></div></div>
    </div>
    <div class="card"><div class="card-header"><form method="get" class="row g-3"><div class="col-auto"><label>From:</label><input type="datetime-local" name="from_date" value="{from_date}T00:00" class="form-control"></div><div class="col-auto"><label>To:</label><input type="datetime-local" name="to_date" value="{to_date}T23:59" class="form-control"></div><div class="col-auto align-self-end"><button type="submit" class="btn btn-primary">Filter</button></div><div class="col-auto align-self-end"><a href="/cashier_activity" class="btn btn-secondary">Reset</a></div></form></div><div class="card-body"><table class="table table-striped"><thead><tr><th>Cashier</th><th>Action</th><th>Timestamp</th><th>User ID</th></tr></thead><tbody>{''.join(f'<tr><td>{h[0]}</td><td>{h[1]}</td><td>{h[2]}</td><td>{h[3]}</td></tr>' for h in history) or '<tr><td colspan="4" class="text-center">No activity in this period.</td></tr>'}</tbody></table></div></div>'''
    return render_page("Cashier Activity", content)


# -------------------- OTHER ROUTES (INVENTORY, USERS, INVOICES, ETC.) --------------------
@app.route('/inventory')
@login_required(allowed_roles=['admin'])
def inventory():
    products = db.fetch_all(
        "SELECT id, barcode, name, category, buying_price, selling_price, quantity, min_stock, unit, supplier FROM products")
    currency = config_manager.config['currency']
    content = f'''
    <h2><i class="fas fa-boxes me-2"></i>Inventory</h2>
    <button class="btn btn-success mb-3" data-bs-toggle="modal" data-bs-target="#addModal"><i class="fas fa-plus"></i> Add Product</button>
    <table class="table table-bordered"><thead><tr><th>ID</th><th>Barcode</th><th>Name</th><th>Category</th><th>Buying</th><th>Selling</th><th>Stock</th><th>Min</th><th>Unit</th><th>Supplier</th><th>Actions</th></tr></thead><tbody>
    {''.join(f'<tr><td>{p[0]}</td><td>{p[1]}</td><td>{p[2]}</td><td>{p[3]}</td><td>{currency} {p[4]}</td><td>{currency} {p[5]}</td><td id="stock-{p[0]}">{p[6]}</td><td>{p[7]}</td><td>{p[8]}</td><td>{p[9]}</td><td><a href="/inventory/delete/{p[0]}" class="btn btn-danger btn-sm" onclick="return confirm(\'Delete?\')"><i class="fas fa-trash"></i></a> <button class="btn btn-warning btn-sm" onclick="updateStock({p[0]})"><i class="fas fa-edit"></i></button></td></tr>' for p in products)}
    </tbody></table>
    <div class="modal fade" id="addModal"><div class="modal-dialog"><div class="modal-content"><form method="post" action="/inventory/add"><div class="modal-header"><h5>Add Product</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body">... (standard fields) ...</div><div class="modal-footer"><button type="submit" class="btn btn-primary">Save</button></div></form></div></div></div>
    <script>function updateStock(pid){{ let newQty=prompt('Enter new quantity:'); if(newQty && !isNaN(newQty) && newQty>=0){{ fetch(`/update_stock/${{pid}}?qty=${{newQty}}`,{{method:'POST'}}).then(()=>location.reload()); }} else alert('Invalid quantity'); }}</script>'''
    return render_page("Inventory", content)


@app.route('/inventory/add', methods=['POST'])
@login_required(allowed_roles=['admin'])
def add_product():
    name = request.form['name']
    barcode = request.form.get('barcode') or f"890{random.randint(1000000000, 9999999999)}"
    category = request.form.get('category', '')
    buying_price = float(request.form.get('buying_price', 0))
    selling_price = float(request.form['selling_price'])
    quantity = int(request.form.get('quantity', 0))
    min_stock = int(request.form.get('min_stock', 5))
    unit = request.form.get('unit', 'pcs')
    supplier = request.form.get('supplier', '')
    if selling_price < 0 or quantity < 0 or min_stock < 0:
        return "Negative values not allowed", 400
    db.execute_query(
        "INSERT INTO products (barcode,name,category,buying_price,selling_price,quantity,min_stock,unit,supplier) VALUES (?,?,?,?,?,?,?,?,?)",
        (barcode, name, category, buying_price, selling_price, quantity, min_stock, unit, supplier))
    db.commit()
    db.log_action(session['username'], "INSERT", "products", name, "", f"Added product {name}")
    return redirect(url_for('inventory'))


@app.route('/update_stock/<int:pid>', methods=['POST'])
@login_required(allowed_roles=['admin'])
def update_stock(pid):
    qty = request.args.get('qty')
    if qty is None or not qty.isdigit() or int(qty) < 0:
        return 'Invalid quantity', 400
    db.execute_query("UPDATE products SET quantity = ? WHERE id=?", (int(qty), pid))
    db.commit()
    db.log_action(session['username'], "UPDATE_STOCK", "products", pid, "", f"New quantity: {qty}")
    return '', 204


@app.route('/inventory/delete/<int:pid>')
@login_required(allowed_roles=['admin'])
def delete_product(pid):
    db.execute_query("DELETE FROM products WHERE id=?", (pid,))
    db.commit()
    return redirect(url_for('inventory'))


@app.route('/users', methods=['GET', 'POST'])
@login_required(allowed_roles=['admin'])
def users():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form.get('password', '')
        role = request.form['role']
        full_name = request.form['full_name']
        if not username or not full_name:
            return "Username and full name required", 400
        if db.fetch_one("SELECT id FROM users WHERE username=?", (username,)):
            return "Username already exists", 400
        hashed = hashlib.sha256(password.encode()).hexdigest()
        db.execute_query("INSERT INTO users (username, password, role, full_name, last_activity) VALUES (?,?,?,?,?)",
                         (username, hashed, role, full_name, None))
        db.commit()
        db.log_action(session['username'], "INSERT", "users", username, "", f"Added user {username}")
        return redirect(url_for('users'))
    users_list = db.fetch_all("SELECT id, username, role, full_name FROM users")
    content = f'''
    <h2><i class="fas fa-users me-2"></i>User Management</h2>
    <button class="btn btn-success mb-3" data-bs-toggle="modal" data-bs-target="#addModal"><i class="fas fa-plus"></i> Add User</button>
    <table class="table"><thead><tr><th>ID</th><th>Username</th><th>Role</th><th>Full Name</th><th>Actions</th></tr></thead><tbody>
    {''.join(f'<tr><td>{u[0]}</td><td>{u[1]}</td><td>{u[2]}</td><td>{u[3]}</td><td><a href="/reset_password/{u[0]}" class="btn btn-sm btn-warning">Reset Password</a></td></tr>' for u in users_list)}
    </tbody></table>
    <div class="modal fade" id="addModal"><div class="modal-dialog"><div class="modal-content"><form method="post"><div class="modal-header"><h5>Add User</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-2"><label>Username</label><input type="text" name="username" class="form-control" required></div><div class="mb-2"><label>Password</label><input type="text" name="password" class="form-control"></div><div class="mb-2"><label>Role</label><select name="role" class="form-select"><option>admin</option><option>cashier</option></select></div><div class="mb-2"><label>Full Name</label><input type="text" name="full_name" class="form-control" required></div></div><div class="modal-footer"><button type="submit" class="btn btn-primary">Save</button><button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button></div></form></div></div></div>'''
    return render_page("Users", content)


@app.route('/reset_password/<int:uid>')
@login_required(allowed_roles=['admin'])
def reset_password(uid):
    new_pass = "password123"
    hashed = hashlib.sha256(new_pass.encode()).hexdigest()
    db.execute_query("UPDATE users SET password=? WHERE id=?", (hashed, uid))
    db.commit()
    db.log_action(session['username'], "RESET_PASSWORD", "users", uid, "", "Password reset")
    return f"Password for user ID {uid} reset to 'password123'"


@app.route('/change_password', methods=['GET', 'POST'])
@login_required()
def change_password():
    if request.method == 'POST':
        old = request.form['old_password']
        new = request.form['new_password']
        confirm = request.form['confirm_password']
        if new != confirm:
            return "New passwords do not match", 400
        user = db.fetch_one("SELECT password FROM users WHERE id=?", (session['user_id'],))
        if hashlib.sha256(old.encode()).hexdigest() != user[0]:
            return "Old password incorrect", 400
        if len(new) < 4:
            return "Password must be at least 4 characters", 400
        db.execute_query("UPDATE users SET password=? WHERE id=?",
                         (hashlib.sha256(new.encode()).hexdigest(), session['user_id']))
        db.commit()
        db.log_action(session['username'], "CHANGE_PASSWORD", "users", session['user_id'], "", "Password changed")
        return "Password changed successfully! <a href='/dashboard'>Go to Dashboard</a>"
    content = '''
    <div class="row justify-content-center"><div class="col-md-4"><div class="card"><div class="card-header">Change Password</div><div class="card-body">
    <form method="post"><div class="mb-3"><label>Old Password</label><input type="password" name="old_password" class="form-control" required></div>
    <div class="mb-3"><label>New Password</label><input type="password" name="new_password" class="form-control" required></div>
    <div class="mb-3"><label>Confirm New Password</label><input type="password" name="confirm_password" class="form-control" required></div>
    <button type="submit" class="btn btn-primary w-100">Change Password</button></form>
    </div></div></div></div>'''
    return render_page("Change Password", content)


@app.route('/invoices')
@login_required(allowed_roles=['admin'])
def invoices():
    invs = db.fetch_all(
        "SELECT invoice_no, sale_date, customer_name, net_amount FROM sales ORDER BY sale_date DESC LIMIT 50")
    currency = config_manager.config['currency']
    content = f'<h2><i class="fas fa-file-invoice me-2"></i>Invoices</h2><table class="table"><thead><tr><th>Invoice No</th><th>Date</th><th>Customer</th><th>Total</th><th>Actions</th></tr></thead><tbody>{"".join(f"<tr><td>{i[0]}</td><td>{i[1]}</td><td>{i[2] or 'Guest'}</td><td>{currency} {i[3]}</td><td><a href='/invoice/{i[0]}' class='btn btn-sm btn-info'>View</a></td></tr>" for i in invs)}</tbody></table>'
    return render_page("Invoices", content)


@app.route('/invoice/<invoice_no>')
@login_required()
def view_invoice(invoice_no):
    sale = db.fetch_one("SELECT * FROM sales WHERE invoice_no=?", (invoice_no,))
    if not sale:
        return "Invoice not found", 404
    items = db.fetch_all("SELECT product_name, quantity, unit_price, total FROM sale_items WHERE invoice_no=?",
                         (invoice_no,))
    company = config_manager.config['company_name']
    currency = config_manager.config['currency']
    content = f'''
    <div class="card"><div class="card-header">Invoice {invoice_no}</div><div class="card-body">
    <p><strong>Date:</strong> {sale[13]}<br><strong>Cashier:</strong> {sale[12]}<br><strong>Customer:</strong> {sale[2] or 'Guest'} {sale[3] and '(' + sale[3] + ')'}</p>
    <table class="table"><thead><tr><th>Product</th><th>Qty</th><th>Unit Price</th><th>Total</th></tr></thead><tbody>{"".join(f"<tr><td>{it[0]}</td><td>{it[1]}</td><td>{currency} {it[2]}</td><td>{currency} {it[3]}</td></tr>" for it in items)}</tbody></table>
    <h4>Subtotal: {currency} {sale[4]}<br>Discount: {currency} {sale[5]}<br>Tax: {currency} {sale[6]}<br><strong>Net Total: {currency} {sale[7]}</strong></h4>
    <button class="btn btn-primary" onclick="window.print()"><i class="fas fa-print"></i> Print</button>
    <a href="/invoices" class="btn btn-secondary">Back</a>
    </div></div>'''
    return render_page(f"Invoice {invoice_no}", content)


@app.route('/backup')
@login_required(allowed_roles=['admin'])
def backup():
    try:
        os.makedirs("backups", exist_ok=True)
        fn = f"backups/backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
        shutil.copy2("supermarket.db", fn)
        return f"Backup saved to {fn} <a href='/dashboard'>Go back</a>"
    except Exception as e:
        return f"Backup failed: {str(e)}"


# -------------------- ADDITIONAL ROUTES (reports, stock_alerts, returns, loyalty, expenses, charts) --------------------
@app.route('/reports')
@login_required()
def reports():
    from_date = request.args.get('from_date', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    to_date = request.args.get('to_date', datetime.now().strftime('%Y-%m-%d'))
    sales = db.fetch_all(
        "SELECT DATE(sale_date), COUNT(*), SUM(net_amount) FROM sales WHERE DATE(sale_date) BETWEEN ? AND ? GROUP BY DATE(sale_date)",
        (from_date, to_date))
    top = db.fetch_all(
        "SELECT p.name, SUM(si.quantity), SUM(si.total) FROM sale_items si JOIN products p ON si.product_id=p.id JOIN sales s ON si.invoice_no=s.invoice_no WHERE DATE(s.sale_date) BETWEEN ? AND ? GROUP BY si.product_id ORDER BY SUM(si.quantity) DESC LIMIT 10",
        (from_date, to_date))
    payments = db.fetch_all(
        "SELECT payment_method, COUNT(*), SUM(net_amount) FROM sales WHERE DATE(sale_date) BETWEEN ? AND ? GROUP BY payment_method",
        (from_date, to_date))
    currency = config_manager.config['currency']
    sales_rows = ''.join(f'<tr><td>{s[0]}</td><td>{s[1]}</td><td>{currency} {s[2]}</td></tr>' for s in sales)
    top_rows = ''.join(f'<tr><td>{t[0]}</td><td>{t[1]}</td><td>{currency} {t[2]}</td></tr>' for t in top)
    payment_rows = ''.join(f'<tr><td>{p[0]}</td><td>{p[1]}</td><td>{currency} {p[2]}</td></tr>' for p in payments)
    content = f'<h2>Reports</h2><form method="get" class="row g-3 mb-4"><div class="col-auto"><input type="date" name="from_date" value="{from_date}" class="form-control"></div><div class="col-auto"><input type="date" name="to_date" value="{to_date}" class="form-control"></div><div class="col-auto"><button type="submit" class="btn btn-primary">Generate</button></div></form><h4>Daily Sales</h4><table class="table"><thead><tr><th>Date</th><th>Transactions</th><th>Total</th></tr></thead><tbody>{sales_rows}</tbody></table><h4>Top Products</h4><table class="table"><thead><tr><th>Product</th><th>Quantity</th><th>Revenue</th></tr></thead><tbody>{top_rows}</tbody></table><h4>Payment Methods</h4><table class="table"><thead><tr><th>Method</th><th>Count</th><th>Amount</th></tr></thead><tbody>{payment_rows}</tbody></table>'
    return render_page("Reports", content)


@app.route('/stock_alerts')
@login_required()
def stock_alerts():
    low = db.fetch_all("SELECT name, quantity, min_stock FROM products WHERE quantity <= min_stock")
    if low:
        rows = ''.join(f'<tr><td>{l[0]}</td><td>{l[1]}</td><td>{l[2]}</td></tr>' for l in low)
        content = f'<h2>Low Stock Alerts</h2><table class="table"><thead><tr><th>Product</th><th>Stock</th><th>Min</th></tr></thead><tbody>{rows}</tbody></table>'
    else:
        content = '<h2>Low Stock Alerts</h2><div class="alert alert-success">All stock levels are healthy ✅</div>'
    return render_page("Stock Alerts", content)


@app.route('/returns', methods=['GET', 'POST'])
@login_required()
def returns():
    if request.method == 'POST':
        invoice = request.form['invoice']
        reason = request.form['reason']
        sale = db.fetch_one("SELECT invoice_no, net_amount FROM sales WHERE invoice_no=?", (invoice,))
        if sale:
            refund = sale[1] * 0.95
            ret_inv = f"RET-{datetime.now().strftime('%Y%m%d%H%M%S')}"
            db.execute_query(
                "INSERT INTO returns (original_invoice, return_invoice, refund_amount, reason, cashier) VALUES (?,?,?,?,?)",
                (invoice, ret_inv, refund, reason, session['username']))
            items = db.fetch_all("SELECT product_id, quantity FROM sale_items WHERE invoice_no=?", (invoice,))
            for it in items:
                db.execute_query("UPDATE products SET quantity = quantity + ? WHERE id=?", (it[1], it[0]))
            db.commit()
            currency = config_manager.config['currency']
            return render_page("Returns",
                               f'<div class="alert alert-success">Return processed, refund: {currency} {refund}</div><a href="/returns" class="btn btn-secondary">Back</a>')
        else:
            return render_page("Returns",
                               '<div class="alert alert-danger">Invoice not found</div><form method="post"><div class="mb-3"><label>Invoice</label><input type="text" name="invoice" class="form-control" required></div><div class="mb-3"><label>Reason</label><select name="reason" class="form-select"><option>Damaged</option><option>Wrong item</option><option>Expired</option></select></div><button type="submit" class="btn btn-warning">Process Return</button></form>')
    return render_page("Returns",
                       '<form method="post"><div class="mb-3"><label>Invoice</label><input type="text" name="invoice" class="form-control" required></div><div class="mb-3"><label>Reason</label><select name="reason" class="form-select"><option>Damaged</option><option>Wrong item</option><option>Expired</option></select></div><button type="submit" class="btn btn-warning">Process Return</button></form>')


@app.route('/loyalty')
@login_required()
def loyalty():
    rows = db.fetch_all(
        "SELECT customer_name, customer_phone, points, tier, total_spent FROM loyalty ORDER BY points DESC")
    currency = config_manager.config['currency']
    add_modal = '<button class="btn btn-success mb-3" data-bs-toggle="modal" data-bs-target="#addModal">+ Register Customer</button><div class="modal fade" id="addModal"><div class="modal-dialog"><div class="modal-content"><form method="post" action="/loyalty/add"><div class="modal-header"><h5>Register Customer</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div><div class="modal-body"><div class="mb-2"><label>Name</label><input type="text" name="name" class="form-control" required></div><div class="mb-2"><label>Phone</label><input type="text" name="phone" class="form-control" required></div></div><div class="modal-footer"><button type="submit" class="btn btn-primary">Save</button></div></form></div></div></div>' if \
    session['role'] == 'admin' else ''
    content = f'<h2>Loyalty Program</h2>{add_modal}<table class="table"><thead><tr><th>Name</th><th>Phone</th><th>Points</th><th>Tier</th><th>Spent</th></tr></thead><tbody>{"".join(f"<tr><td>{r[0]}</td><td>{r[1]}</td><td>{r[2]}</td><td>{r[3]}</td><td>{currency} {r[4]}</td></tr>" for r in rows)}</tbody></table>'
    return render_page("Loyalty", content)


@app.route('/loyalty/add', methods=['POST'])
@login_required(allowed_roles=['admin'])
def add_loyalty():
    name = request.form['name']
    phone = request.form['phone']
    db.execute_query("INSERT OR REPLACE INTO loyalty (customer_name, customer_phone) VALUES (?,?)", (name, phone))
    db.commit()
    return redirect(url_for('loyalty'))


@app.route('/expenses', methods=['GET', 'POST'])
@login_required(allowed_roles=['admin'])
def expenses():
    if request.method == 'POST':
        category = request.form['category']
        amount = float(request.form['amount'])
        description = request.form['description']
        db.execute_query("INSERT INTO expenses (category, amount, description, expense_date, user) VALUES (?,?,?,?,?)",
                         (category, amount, description, datetime.now().date(), session['username']))
        db.commit()
        return redirect(url_for('expenses'))
    rows = db.fetch_all(
        "SELECT expense_date, category, amount, description, user FROM expenses ORDER BY expense_date DESC")
    total = sum(r[2] for r in rows)
    currency = config_manager.config['currency']
    table_rows = ''.join(
        f'<tr><td>{e[0]}</td><td>{e[1]}</td><td>{currency} {e[2]}</td><td>{e[3]}</td><td>{e[4]}</td></tr>' for e in
        rows)
    content = f'<h2>Expenses</h2><form method="post" class="row g-3 mb-4"><div class="col-auto"><input type="text" name="category" placeholder="Category" required class="form-control"></div><div class="col-auto"><input type="number" step="0.01" name="amount" placeholder="Amount" required class="form-control"></div><div class="col-auto"><input type="text" name="description" placeholder="Description" class="form-control"></div><div class="col-auto"><button type="submit" class="btn btn-primary">Add</button></div></form><table class="table"><thead><tr><th>Date</th><th>Category</th><th>Amount</th><th>Description</th><th>User</th></tr></thead><tbody>{table_rows}</tbody></table><h4>Total: {currency} {total}</h4>'
    return render_page("Expenses", content)


@app.route('/charts')
@login_required()
def charts():
    currency = config_manager.config['currency']
    content = f'''
    <h2>Sales Charts</h2>
    <canvas id="salesChart" width="800" height="400"></canvas>
    <canvas id="categoryChart" width="800" height="400"></canvas>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <script>
        fetch('/api/sales_trend').then(res=>res.json()).then(data=>{{ new Chart(document.getElementById('salesChart'), {{ type: 'line', data: {{ labels: data.dates, datasets: [{{ label: 'Revenue ({currency})', data: data.amounts, borderColor: '#0d6e4d', fill: false }}] }} }}) }});
        fetch('/api/category_sales').then(res=>res.json()).then(data=>{{ new Chart(document.getElementById('categoryChart'), {{ type: 'pie', data: {{ labels: data.categories, datasets: [{{ data: data.sales, backgroundColor: ['#0d6e4d','#f39c12','#e67e22','#2c3e50','#1abc9c'] }}] }} }}) }});
    </script>'''
    return render_page("Charts", content)


@app.route('/api/sales_trend')
@login_required()
def api_sales_trend():
    data = db.fetch_all(
        "SELECT DATE(sale_date), SUM(net_amount) FROM sales WHERE sale_date >= date('now', '-30 days') GROUP BY DATE(sale_date) ORDER BY sale_date")
    return jsonify({'dates': [d[0] for d in data], 'amounts': [d[1] for d in data]})


@app.route('/api/category_sales')
@login_required()
def api_category_sales():
    data = db.fetch_all(
        "SELECT p.category, SUM(si.total) FROM sale_items si JOIN products p ON si.product_id=p.id GROUP BY p.category ORDER BY SUM(si.total) DESC LIMIT 5")
    return jsonify({'categories': [c[0] for c in data], 'sales': [c[1] for c in data]})


if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)