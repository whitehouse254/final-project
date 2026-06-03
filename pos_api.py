# pos_api.py - Complete Backend for Victor's Supermarket POS with built‑in DB Admin
import os
import hashlib
import random
import json
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, session, render_template_string
from flask_cors import CORS
import psycopg2
from psycopg2.extras import RealDictCursor

app = Flask(__name__)
app.secret_key = 'supermarket-secret-key'
CORS(app, origins=['http://localhost:5001', 'http://127.0.0.1:5001'])

# PostgreSQL connection
def get_db():
    return psycopg2.connect(
        host='localhost',
        database='pos_db',
        user='pos_user',
        password='pos_password',
        port=5432
    )

def query_db(sql, params=None, fetch_one=False, commit=False):
    conn = get_db()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(sql, params or ())
    if commit:
        conn.commit()
        result = None
    elif fetch_one:
        result = cur.fetchone()
    else:
        result = cur.fetchall()
    cur.close()
    conn.close()
    return result

# -------------------- INITIALIZE DATABASE TABLES --------------------
def init_db():
    # Create tables
    tables = """
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        username TEXT UNIQUE NOT NULL,
        password TEXT NOT NULL,
        role TEXT DEFAULT 'cashier',
        full_name TEXT,
        last_activity TIMESTAMP,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS products (
        id SERIAL PRIMARY KEY,
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
        id SERIAL PRIMARY KEY,
        invoice_no TEXT UNIQUE,
        customer_name TEXT,
        customer_phone TEXT,
        total_amount REAL,
        discount REAL DEFAULT 0,
        tax REAL DEFAULT 0,
        net_amount REAL,
        payment_method TEXT,
        cashier TEXT,
        sale_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        shift_id INTEGER
    );
    CREATE TABLE IF NOT EXISTS sale_items (
        id SERIAL PRIMARY KEY,
        invoice_no TEXT,
        product_id INTEGER,
        product_name TEXT,
        quantity INTEGER,
        unit_price REAL,
        total REAL,
        returned BOOLEAN DEFAULT FALSE
    );
    CREATE TABLE IF NOT EXISTS shifts (
        id SERIAL PRIMARY KEY,
        user_id INTEGER,
        cashier_name TEXT,
        start_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        end_time TIMESTAMP,
        total_sales REAL DEFAULT 0,
        status TEXT DEFAULT 'active'
    );
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        username TEXT,
        action TEXT,
        table_name TEXT,
        record_id TEXT,
        old_value TEXT,
        new_value TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS returns (
        id SERIAL PRIMARY KEY,
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
        id SERIAL PRIMARY KEY,
        category TEXT,
        amount REAL,
        description TEXT,
        expense_date DATE,
        username TEXT
    );
    CREATE TABLE IF NOT EXISTS quotations (
        id SERIAL PRIMARY KEY,
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
        id SERIAL PRIMARY KEY,
        invoice_no TEXT,
        delivery_address TEXT,
        delivery_date DATE,
        status TEXT DEFAULT 'pending',
        driver_name TEXT,
        tracking_info TEXT
    );
    """
    for statement in tables.split(';'):
        if statement.strip():
            query_db(statement, commit=True)

    # Seed admin user
    admin = query_db("SELECT id FROM users WHERE username='victor'", fetch_one=True)
    if not admin:
        hashed = hashlib.sha256("victor@123".encode()).hexdigest()
        query_db("INSERT INTO users (username, password, role, full_name) VALUES (%s, %s, %s, %s)",
                 ("victor", hashed, "admin", "Victor Admin"), commit=True)
        print("Admin user created: victor / victor@123")

    # Seed cashier user
    cashier = query_db("SELECT id FROM users WHERE username='cashier'", fetch_one=True)
    if not cashier:
        hashed = hashlib.sha256("cashier".encode()).hexdigest()
        query_db("INSERT INTO users (username, password, role, full_name) VALUES (%s, %s, %s, %s)",
                 ("cashier", hashed, "cashier", "Store Cashier"), commit=True)

    # Seed more cashiers if needed
    cashiers = [("john", "john123", "John Doe"), ("mary", "mary123", "Mary Smith")]
    for uname, pwd, fname in cashiers:
        if not query_db("SELECT id FROM users WHERE username=%s", (uname,), fetch_one=True):
            hashed = hashlib.sha256(pwd.encode()).hexdigest()
            query_db("INSERT INTO users (username, password, role, full_name) VALUES (%s, %s, %s, %s)",
                     (uname, hashed, "cashier", fname), commit=True)

    # Seed 500+ products if empty
    count = query_db("SELECT COUNT(*) as cnt FROM products", fetch_one=True)
    if count['cnt'] == 0:
        seed_products()

    # Seed loyalty customers if empty
    loyalty_count = query_db("SELECT COUNT(*) as cnt FROM loyalty", fetch_one=True)
    if loyalty_count['cnt'] == 0:
        seed_loyalty()

def seed_products():
    categories = {
        "Grains & Cereals": ["Rice (1kg)", "Rice (5kg)", "Maize Flour (1kg)", "Wheat Flour (2kg)", "Oats (500g)"],
        "Dairy & Eggs": ["Fresh Milk (1L)", "Yogurt (500ml)", "Cheese (250g)", "Butter (250g)", "Eggs (12pcs)"],
        "Beverages": ["Mineral Water (1L)", "Soda (330ml)", "Juice (1L)", "Coffee (50g)", "Tea Bags (100)"],
        "Snacks": ["Potato Chips (100g)", "Chocolate Bar (50g)", "Biscuits (100g)", "Peanuts (100g)", "Cookies (150g)"],
        "Fruits": ["Apples (1kg)", "Bananas (1 bunch)", "Oranges (1kg)", "Mangoes (1kg)", "Grapes (500g)"],
        "Meat": ["Beef (1kg)", "Chicken Whole", "Pork (500g)", "Fish Fillet (500g)", "Sausages (8pcs)"],
        "Household": ["Detergent (1kg)", "Dish Soap (500ml)", "Toilet Paper (4 rolls)", "Trash Bags (10pcs)", "Sponges (3pcs)"],
        "Personal Care": ["Shampoo (250ml)", "Toothpaste (100g)", "Soap Bar", "Deodorant", "Lotion (200ml)"]
    }
    units = ["pcs", "kg", "L", "g", "ml", "pack"]
    product_count = 0
    for cat, names in categories.items():
        for name in names:
            for _ in range(5):  # create multiple variations
                if product_count >= 520:
                    break
                buying_price = round(random.uniform(10, 800), 2)
                selling_price = round(buying_price * random.uniform(1.2, 1.8), 2)
                quantity = random.randint(20, 500)
                min_stock = random.randint(5, 30)
                unit = random.choice(units)
                barcode = f"890{random.randint(1000000000, 9999999999)}"
                supplier = f"Supplier {random.randint(1,20)}"
                query_db(
                    "INSERT INTO products (barcode, name, category, buying_price, selling_price, quantity, min_stock, unit, supplier) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (barcode, f"{name} (var {_+1})", cat, buying_price, selling_price, quantity, min_stock, unit, supplier),
                    commit=True
                )
                product_count += 1
                if product_count >= 520:
                    break
        if product_count >= 520:
            break
    print(f"✅ Seeded {product_count} products.")

def seed_loyalty():
    names = [
        "Alice Wanjiku", "Brian Kimani", "Carol Otieno", "David Mwangi", "Eunice Achieng",
        "Francis Omondi", "Grace Nduta", "Henry Kipchoge", "Irene Chebet", "James Kariuki",
        "Kennedy Ochieng", "Lilian Njeri", "Michael Mburu", "Nancy Wambui", "Oscar Maina",
        "Peter Njoroge", "Quinter Akinyi", "Ruth Akoth", "Samuel Odhiambo", "Teresa Adhiambo",
        "Victor Mwangi", "Joyce Wairimu", "Simon Kariuki", "Faith Muthoni", "Joseph Kamau"
    ]
    for name in names:
        phone = f"07{random.randint(10000000, 99999999)}"
        points = random.randint(0, 800)
        spent = random.randint(0, 50000)
        query_db(
            "INSERT INTO loyalty (customer_name, customer_phone, points, total_spent) VALUES (%s, %s, %s, %s)",
            (name, phone, points, spent),
            commit=True
        )
    print(f"✅ Seeded {len(names)} loyalty customers.")

# -------------------- API ENDPOINTS --------------------
@app.route('/api/health', methods=['GET'])
def health():
    return jsonify({'status': 'ok', 'database': 'postgresql'})

# ----- Products -----
@app.route('/api/products', methods=['GET'])
def get_products():
    products = query_db("SELECT id, name, selling_price, quantity, category, min_stock FROM products ORDER BY name")
    return jsonify(products)

@app.route('/api/products', methods=['POST'])
def create_product():
    data = request.json
    barcode = data.get('barcode') or f"890{random.randint(1000000000, 9999999999)}"
    name = data['name']
    selling_price = data['selling_price']
    quantity = data.get('quantity', 0)
    min_stock = data.get('min_stock', 5)
    category = data.get('category', '')
    supplier = data.get('supplier', '')
    query_db(
        "INSERT INTO products (barcode, name, selling_price, quantity, min_stock, category, supplier) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (barcode, name, selling_price, quantity, min_stock, category, supplier), commit=True
    )
    return jsonify({'status': 'success'}), 201

@app.route('/api/products/<int:pid>', methods=['PUT'])
def update_product(pid):
    data = request.json
    query_db("UPDATE products SET name=%s, selling_price=%s, quantity=%s, min_stock=%s, category=%s, supplier=%s WHERE id=%s",
             (data['name'], data['selling_price'], data['quantity'], data.get('min_stock',5), data.get('category',''), data.get('supplier',''), pid), commit=True)
    return jsonify({'status': 'success'})

@app.route('/api/products/<int:pid>', methods=['DELETE'])
def delete_product(pid):
    query_db("DELETE FROM products WHERE id=%s", (pid,), commit=True)
    return jsonify({'status': 'success'})

@app.route('/api/products/<int:pid>/stock', methods=['PUT'])
def update_stock(pid):
    data = request.json
    query_db("UPDATE products SET quantity=%s WHERE id=%s", (data['quantity'], pid), commit=True)
    return jsonify({'status': 'success'})

# ----- Sales (Invoices) -----
@app.route('/api/invoices', methods=['POST'])
def create_invoice():
    data = request.json
    cart = data['cart']
    customer_name = data.get('customer_name', '')
    customer_phone = data.get('customer_phone', '')
    payment_method = data.get('payment_method', 'Cash')
    discount = data.get('discount', 0)
    subtotal = sum(item['total'] for item in cart)
    tax = (subtotal - discount) * 0.16
    net_total = subtotal - discount + tax
    invoice_no = f"INV-{datetime.now().strftime('%Y%m%d')}-{random.randint(1,9999):04d}"
    cashier = session.get('username', 'cashier')
    active_shift = query_db("SELECT id FROM shifts WHERE user_id=(SELECT id FROM users WHERE username=%s) AND status='active'", (cashier,), fetch_one=True)
    shift_id = active_shift['id'] if active_shift else None

    try:
        query_db(
            "INSERT INTO sales (invoice_no, customer_name, customer_phone, total_amount, discount, tax, net_amount, payment_method, cashier, shift_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (invoice_no, customer_name, customer_phone, subtotal, discount, tax, net_total, payment_method, cashier, shift_id), commit=True
        )
        for item in cart:
            query_db("INSERT INTO sale_items (invoice_no, product_id, product_name, quantity, unit_price, total) VALUES (%s,%s,%s,%s,%s,%s)",
                     (invoice_no, item['id'], item['name'], item['quantity'], item['price'], item['total']), commit=True)
            query_db("UPDATE products SET quantity = quantity - %s WHERE id = %s", (item['quantity'], item['id']), commit=True)
        if shift_id:
            query_db("UPDATE shifts SET total_sales = total_sales + %s WHERE id = %s", (net_total, shift_id), commit=True)
        if customer_phone:
            points = int(subtotal * 0.01)
            existing = query_db("SELECT customer_phone FROM loyalty WHERE customer_phone=%s", (customer_phone,), fetch_one=True)
            if not existing:
                query_db("INSERT INTO loyalty (customer_phone, customer_name, points) VALUES (%s,%s,%s)", (customer_phone, customer_name or "Guest", points), commit=True)
            else:
                query_db("UPDATE loyalty SET points = points + %s, total_spent = total_spent + %s WHERE customer_phone=%s", (points, net_total, customer_phone), commit=True)
        return jsonify({'status': 'success', 'invoice': invoice_no, 'net_total': net_total})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/invoices', methods=['GET'])
def get_invoices():
    invoices = query_db("SELECT invoice_no, customer_name, net_amount, payment_method, sale_date FROM sales ORDER BY sale_date DESC LIMIT 200")
    return jsonify(invoices)

# ----- Returns -----
@app.route('/api/returns', methods=['POST'])
def process_return():
    data = request.json
    invoice_no = data['invoice_no']
    sale = query_db("SELECT net_amount FROM sales WHERE invoice_no=%s", (invoice_no,), fetch_one=True)
    if not sale:
        return jsonify({'status': 'error', 'message': 'Invoice not found'}), 404
    refund = sale['net_amount'] * 0.95
    ret_inv = f"RET-{datetime.now().strftime('%Y%m%d%H%M%S')}"
    items = query_db("SELECT product_id, quantity FROM sale_items WHERE invoice_no=%s", (invoice_no,))
    try:
        query_db("INSERT INTO returns (original_invoice, return_invoice, refund_amount, cashier) VALUES (%s,%s,%s,%s)",
                 (invoice_no, ret_inv, refund, session.get('username','cashier')), commit=True)
        for it in items:
            query_db("UPDATE products SET quantity = quantity + %s WHERE id = %s", (it['quantity'], it['product_id']), commit=True)
        return jsonify({'status': 'success', 'refund': refund})
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

# ----- Reports -----
@app.route('/api/reports/dashboard', methods=['GET'])
def dashboard_stats():
    today = datetime.now().date()
    total_products = query_db("SELECT COUNT(*) as cnt FROM products", fetch_one=True)
    low_stock = query_db("SELECT COUNT(*) as cnt FROM products WHERE quantity <= min_stock", fetch_one=True)
    today_sales = query_db("SELECT COALESCE(SUM(net_amount),0) as total, COUNT(*) as cnt FROM sales WHERE DATE(sale_date)=%s", (today,), fetch_one=True)
    total_sales = query_db("SELECT COALESCE(SUM(net_amount),0) as total FROM sales", fetch_one=True)
    return jsonify({
        'total_products': total_products['cnt'],
        'low_stock': low_stock['cnt'],
        'today_sales': float(today_sales['total']),
        'today_transactions': today_sales['cnt'],
        'total_sales': float(total_sales['total'])
    })

@app.route('/api/reports/sales', methods=['GET'])
def sales_report():
    sales = query_db("SELECT invoice_no, customer_name, net_amount, sale_date FROM sales ORDER BY sale_date DESC LIMIT 200")
    return jsonify(sales)

# ----- Loyalty -----
@app.route('/api/loyalty', methods=['GET'])
def get_loyalty():
    customers = query_db("SELECT customer_name, customer_phone, points, total_spent FROM loyalty ORDER BY points DESC")
    return jsonify(customers)

@app.route('/api/loyalty', methods=['POST'])
def add_loyalty():
    data = request.json
    name = data.get('name')
    phone = data.get('phone')
    points = data.get('points', 0)
    if not name or not phone:
        return jsonify({'error': 'Name and phone required'}), 400
    query_db("INSERT INTO loyalty (customer_name, customer_phone, points) VALUES (%s, %s, %s) ON CONFLICT (customer_phone) DO UPDATE SET customer_name=EXCLUDED.customer_name, points=EXCLUDED.points",
             (name, phone, points), commit=True)
    return jsonify({'status': 'success'})

# ----- Expenses -----
@app.route('/api/expenses', methods=['GET'])
def get_expenses():
    expenses = query_db("SELECT id, category, amount, description, expense_date FROM expenses ORDER BY expense_date DESC")
    return jsonify(expenses)

@app.route('/api/expenses', methods=['POST'])
def add_expense():
    data = request.json
    query_db("INSERT INTO expenses (category, amount, description, expense_date, username) VALUES (%s,%s,%s,%s,%s)",
             (data['category'], data['amount'], data.get('description',''), datetime.now().date(), session.get('username','admin')), commit=True)
    return jsonify({'status': 'success'})

# ----- Shifts -----
@app.route('/api/shifts/current', methods=['GET'])
def current_shift():
    user = query_db("SELECT id FROM users WHERE username=%s", (session.get('username','cashier'),), fetch_one=True)
    if user:
        shift = query_db("SELECT id, cashier_name, start_time FROM shifts WHERE user_id=%s AND status='active'", (user['id'],), fetch_one=True)
        return jsonify({'active': shift is not None, 'cashier_name': shift['cashier_name'] if shift else None, 'start_time': shift['start_time'] if shift else None})
    return jsonify({'active': False})

@app.route('/api/shifts/start', methods=['POST'])
def start_shift():
    user = query_db("SELECT id FROM users WHERE username=%s", (session.get('username','cashier'),), fetch_one=True)
    if user:
        query_db("UPDATE shifts SET end_time=CURRENT_TIMESTAMP, status='ended' WHERE user_id=%s AND status='active'", (user['id'],), commit=True)
        query_db("INSERT INTO shifts (user_id, cashier_name, status) VALUES (%s, %s, 'active')", (user['id'], session.get('full_name','Cashier')), commit=True)
    return jsonify({'status': 'success'})

@app.route('/api/shifts/end', methods=['POST'])
def end_shift():
    user = query_db("SELECT id FROM users WHERE username=%s", (session.get('username','cashier'),), fetch_one=True)
    if user:
        query_db("UPDATE shifts SET end_time=CURRENT_TIMESTAMP, status='ended' WHERE user_id=%s AND status='active'", (user['id'],), commit=True)
    return jsonify({'status': 'success'})

@app.route('/api/shifts', methods=['GET'])
def get_shifts():
    shifts = query_db("SELECT s.id, u.username, s.cashier_name, s.start_time, s.end_time, s.total_sales, s.status FROM shifts s JOIN users u ON s.user_id=u.id ORDER BY s.start_time DESC")
    return jsonify(shifts)

# ----- Quotations -----
@app.route('/api/quotations', methods=['GET'])
def get_quotations():
    quotes = query_db("SELECT id, quote_no, customer_name, quote_date, total, status FROM quotations ORDER BY id DESC")
    return jsonify(quotes)

@app.route('/api/quotations', methods=['POST'])
def create_quotation():
    data = request.json
    quote_no = f"QT-{datetime.now().strftime('%Y%m%d')}-{random.randint(1,9999):04d}"
    expiry = (datetime.now() + timedelta(days=7)).date()
    items_json = json.dumps(data['items'])
    subtotal = data['subtotal']
    tax = subtotal * 0.16
    total = subtotal + tax
    query_db(
        "INSERT INTO quotations (quote_no, customer_name, customer_phone, quote_date, expiry_date, items_json, subtotal, tax, total, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (quote_no, data.get('customer_name',''), data.get('customer_phone',''), datetime.now().date(), expiry, items_json, subtotal, tax, total, session.get('username','admin')), commit=True
    )
    return jsonify({'status': 'success'})

@app.route('/api/quotations/<int:qid>/convert', methods=['POST'])
def convert_quotation(qid):
    quote = query_db("SELECT * FROM quotations WHERE id=%s", (qid,), fetch_one=True)
    if not quote:
        return jsonify({'status': 'error', 'message': 'Quotation not found'}), 404
    items = json.loads(quote['items_json'])
    subtotal = quote['subtotal']
    tax = quote['tax']
    total = quote['total']
    invoice_no = f"INV-{datetime.now().strftime('%Y%m%d')}-{random.randint(1,9999):04d}"
    query_db("INSERT INTO sales (invoice_no, customer_name, customer_phone, total_amount, tax, net_amount, payment_method, cashier) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
             (invoice_no, quote['customer_name'], quote['customer_phone'], subtotal, tax, total, 'Quote Conversion', session.get('username','cashier')), commit=True)
    for item in items:
        query_db("INSERT INTO sale_items (invoice_no, product_id, product_name, quantity, unit_price, total) VALUES (%s,%s,%s,%s,%s,%s)",
                 (invoice_no, item['id'], item['name'], item['qty'], item['price'], item['price']*item['qty']), commit=True)
        query_db("UPDATE products SET quantity = quantity - %s WHERE id = %s", (item['qty'], item['id']), commit=True)
    query_db("UPDATE quotations SET status='converted' WHERE id=%s", (qid,), commit=True)
    return jsonify({'status': 'success', 'invoice': invoice_no})

# ----- Deliveries -----
@app.route('/api/deliveries', methods=['GET'])
def get_deliveries():
    deliveries = query_db("SELECT id, invoice_no, delivery_address, driver_name, tracking_info, status FROM deliveries ORDER BY delivery_date DESC")
    return jsonify(deliveries)

@app.route('/api/deliveries', methods=['POST'])
def create_delivery():
    data = request.json
    query_db("INSERT INTO deliveries (invoice_no, delivery_address, delivery_date, driver_name, tracking_info, status) VALUES (%s,%s,%s,%s,%s,'pending')",
             (data['invoice_no'], data['address'], datetime.now().date(), data.get('driver',''), data.get('tracking','')), commit=True)
    return jsonify({'status': 'success'})

@app.route('/api/deliveries/<int:did>', methods=['PUT'])
def update_delivery(did):
    data = request.json
    query_db("UPDATE deliveries SET status=%s WHERE id=%s", (data['status'], did), commit=True)
    return jsonify({'status': 'success'})

# ----- User Management (Admin only) -----
@app.route('/api/users', methods=['GET'])
def get_users():
    users = query_db("SELECT id, username, full_name, role FROM users ORDER BY id")
    return jsonify(users)

@app.route('/api/users', methods=['POST'])
def create_user():
    data = request.json
    hashed = hashlib.sha256(data['password'].encode()).hexdigest()
    query_db("INSERT INTO users (username, password, full_name, role) VALUES (%s, %s, %s, %s)",
             (data['username'], hashed, data.get('full_name',''), data.get('role','cashier')), commit=True)
    return jsonify({'status': 'success'}), 201

@app.route('/api/users/<int:uid>/password', methods=['PUT'])
def reset_user_password(uid):
    data = request.json
    hashed = hashlib.sha256(data['password'].encode()).hexdigest()
    query_db("UPDATE users SET password=%s WHERE id=%s", (hashed, uid), commit=True)
    return jsonify({'status': 'success'})

# ----- Authentication -----
@app.route('/api/login', methods=['POST'])
def login():
    data = request.json
    username = data['username']
    password = data['password']
    hashed = hashlib.sha256(password.encode()).hexdigest()
    user = query_db("SELECT id, username, role, full_name FROM users WHERE username=%s AND password=%s", (username, hashed), fetch_one=True)
    if user:
        session['user_id'] = user['id']
        session['username'] = user['username']
        session['role'] = user['role']
        session['full_name'] = user['full_name']
        query_db("UPDATE users SET last_activity=CURRENT_TIMESTAMP WHERE id=%s", (user['id'],), commit=True)
        return jsonify({'status': 'success', 'user': user})
    return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'success'})

# -------------------- ENHANCED DATABASE ADMIN (Web interface) --------------------
# Home template for dbadmin
DBADMIN_HOME_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Database Admin – Supermarket POS</title>
    <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
    <style>
        body { background: #f0f2f5; font-family: monospace; padding: 20px; }
        .container { max-width: 1400px; margin: auto; }
        h1 { color: #1e466e; }
        .table-list { display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px; }
        .table-list a { background: white; padding: 8px 15px; border-radius: 20px; text-decoration: none; color: #1e466e; border: 1px solid #ccc; }
        .table-list a:hover { background: #0077b6; color: white; }
        .query-box { margin: 20px 0; }
        textarea { width: 100%; font-family: monospace; }
        .result-table { overflow-x: auto; margin-top: 20px; }
        table { width: 100%; border-collapse: collapse; background: white; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background: #0077b6; color: white; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📁 Database Admin – PostgreSQL</h1>
        <div class="table-list">
            {% for t in tables %}
                <a href="/dbadmin/table/{{ t }}">{{ t }}</a>
            {% endfor %}
        </div>
        <div class="query-box card p-3">
            <h5>Run SQL Query</h5>
            <form method="post">
                <textarea name="sql" rows="4" class="form-control" placeholder="SELECT * FROM products LIMIT 10;">{{ sql }}</textarea>
                <button type="submit" class="btn btn-primary mt-2">Execute</button>
            </form>
        </div>
        {% if result %}
            <div class="result-table">
                <h5>Result:</h5>
                {{ result|safe }}
            </div>
        {% endif %}
    </div>
</body>
</html>
'''

@app.route('/dbadmin', methods=['GET', 'POST'])
def dbadmin():
    """Database admin homepage: list tables and run SQL queries."""
    if 'user_id' not in session:
        return "Please login first (use /api/login via POST)", 401

    if request.method == 'POST':
        sql = request.form.get('sql', '')
        result = None
        if sql.strip():
            conn = get_db()
            cur = conn.cursor()
            try:
                cur.execute(sql)
                if sql.strip().upper().startswith('SELECT'):
                    rows = cur.fetchall()
                    colnames = [desc[0] for desc in cur.description] if cur.description else []
                    # Build HTML table
                    result = '<div style="overflow-x:auto;"><table class="table table-bordered table-striped"><thead></td>'
                    for col in colnames:
                        result += f'<th>{col}</th>'
                    result += '</thead><tbody>'
                    for row in rows:
                        result += '<tr>'
                        for val in row:
                            result += f'<td>{val if val is not None else "<i>NULL</i>"}</td>'
                        result += '</table>'
                    result += '</tbody></table></div>'
                    result += f'<p class="text-muted">{len(rows)} rows returned.</p>'
                else:
                    conn.commit()
                    result = f'<div class="alert alert-success">Query executed. {cur.rowcount} row(s) affected.</div>'
            except Exception as e:
                result = f'<div class="alert alert-danger">Error: {str(e)}</div>'
            finally:
                conn.close()
        else:
            result = '<div class="alert alert-warning">Please enter a SQL query.</div>'
        # Get table list again
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
        tables = [row[0] for row in cur.fetchall()]
        conn.close()
        return render_template_string(DBADMIN_HOME_TEMPLATE, tables=tables, result=result, sql=sql)
    else:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
        tables = [row[0] for row in cur.fetchall()]
        conn.close()
        return render_template_string(DBADMIN_HOME_TEMPLATE, tables=tables, result=None, sql='')

@app.route('/dbadmin/table/<table_name>')
def dbadmin_view_table(table_name):
    """View a single table with pagination and actions (edit/delete)."""
    if 'user_id' not in session:
        return "Please login first", 401

    page = request.args.get('page', 1, type=int)
    per_page = 50
    offset = (page - 1) * per_page

    conn = get_db()
    cur = conn.cursor()
    try:
        # Get column names
        cur.execute(f"SELECT column_name FROM information_schema.columns WHERE table_name='{table_name}' ORDER BY ordinal_position")
        columns = [row[0] for row in cur.fetchall()]
        if not columns:
            conn.close()
            return f"Table '{table_name}' not found.", 404

        # Get row count
        cur.execute(f"SELECT COUNT(*) FROM {table_name}")
        total_rows = cur.fetchone()[0]
        total_pages = (total_rows + per_page - 1) // per_page

        # Get data
        cur.execute(f"SELECT * FROM {table_name} LIMIT %s OFFSET %s", (per_page, offset))
        rows = cur.fetchall()
    except Exception as e:
        conn.close()
        return f"Error: {e}", 500
    conn.close()

    # Build HTML
    html = f'''
    <div class="container">
        <h2>Table: {table_name}</h2>
        <div class="mb-3">
            <a href="/dbadmin" class="btn btn-secondary">← Back to Admin</a>
            <button class="btn btn-danger" onclick="if(confirm('Delete ALL rows? This cannot be undone.')) window.location='/dbadmin/truncate/{table_name}';">Truncate Table</button>
        </div>
        <div class="table-responsive">
            <table class="table table-bordered table-striped">
                <thead>
                    <tr>
                        {"".join(f"<th>{col}</th>" for col in columns)}
                        <th>Actions</th>
                    </tr>
                </thead>
                <tbody>
    '''
    for idx, row in enumerate(rows):
        html += '<tr>'
        for val in row:
            html += f'<td>{val if val is not None else "<i>NULL</i>"}</td>'
        # Edit and delete buttons
        row_id = row[0]  # assumes first column is primary key (usually 'id')
        html += f'''
            <td>
                <button class="btn btn-sm btn-warning" data-bs-toggle="modal" data-bs-target="#editModal{idx}">Edit</button>
                <a href="/dbadmin/delete/{table_name}/{row_id}" class="btn btn-sm btn-danger" onclick="return confirm('Delete this row?')">Delete</a>
            </td>
        </tr>
        <!-- Edit Modal -->
        <div class="modal fade" id="editModal{idx}" tabindex="-1">
            <div class="modal-dialog">
                <div class="modal-content">
                    <form action="/dbadmin/update/{table_name}/{row_id}" method="post">
                        <div class="modal-header"><h5>Edit Row (ID={row_id})</h5><button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
                        <div class="modal-body">
        '''
        for i, col in enumerate(columns):
            html += f'''
                <div class="mb-2">
                    <label>{col}</label>
                    <input type="text" name="{col}" class="form-control" value="{row[i]}">
                </div>
            '''
        html += '''
                        </div>
                        <div class="modal-footer">
                            <button type="submit" class="btn btn-primary">Save</button>
                            <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        </div>
                    </form>
                </div>
            </div>
        </div>
        '''

    html += f'''
                </tbody>
            </table>
        </div>
        <nav><ul class="pagination">
            {"<li class='page-item'><a class='page-link' href='?page=" + str(page-1) + "'>Previous</a></li>" if page > 1 else ""}
            <li class='page-item disabled'><span class='page-link'>Page {page} of {total_pages}</span></li>
            {"<li class='page-item'><a class='page-link' href='?page=" + str(page+1) + "'>Next</a></li>" if page < total_pages else ""}
        </ul></nav>
        <p class="text-muted">Total rows: {total_rows}</p>
    </div>
    '''
    return render_template_string(f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>Table: {table_name}</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css" rel="stylesheet">
            <style>body {{ background: #f0f2f5; padding: 20px; }}</style>
        </head>
        <body>{html}</body>
        </html>
    ''')

@app.route('/dbadmin/update/<table_name>/<int:row_id>', methods=['POST'])
def dbadmin_update(table_name, row_id):
    """Update a single row (assumes primary key column is 'id')."""
    if 'user_id' not in session:
        return "Please login first", 401

    set_clauses = []
    values = []
    for key, value in request.form.items():
        set_clauses.append(f"{key} = %s")
        values.append(value)
    values.append(row_id)
    query = f"UPDATE {table_name} SET {', '.join(set_clauses)} WHERE id = %s"
    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(query, tuple(values))
        conn.commit()
    except Exception as e:
        conn.close()
        return f"Update error: {e}"
    conn.close()
    return redirect(f"/dbadmin/table/{table_name}")

@app.route('/dbadmin/delete/<table_name>/<int:row_id>')
def dbadmin_delete(table_name, row_id):
    """Delete a single row (assumes primary key column is 'id')."""
    if 'user_id' not in session:
        return "Please login first", 401

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(f"DELETE FROM {table_name} WHERE id = %s", (row_id,))
        conn.commit()
    except Exception as e:
        conn.close()
        return f"Delete error: {e}"
    conn.close()
    return redirect(f"/dbadmin/table/{table_name}")

@app.route('/dbadmin/truncate/<table_name>')
def dbadmin_truncate(table_name):
    """Delete all rows from a table and reset auto‑increment."""
    if 'user_id' not in session:
        return "Please login first", 401

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY")
        conn.commit()
    except Exception as e:
        conn.close()
        return f"Truncate error: {e}"
    conn.close()
    return redirect(f"/dbadmin/table/{table_name}")

# -------------------- RUN THE APP --------------------
if __name__ == '__main__':
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000)