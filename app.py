from flask import Flask, render_template, request, jsonify, session, redirect, url_for
from flask_pymongo import PyMongo
from werkzeug.security import generate_password_hash, check_password_hash
from bson.objectid import ObjectId
from datetime import datetime, timedelta
import os, random, string

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'nexabank-secret-key-change-in-prod')
app.config["MONGO_URI"] = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/nexabank')
mongo = PyMongo(app)

def gen_account():
    return ''.join(random.choices(string.digits, k=12))

def current_user():
    if 'user_id' not in session:
        return None
    return mongo.db.users.find_one({'_id': ObjectId(session['user_id'])})

# ── Pages ──────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('index'))
    return render_template('dashboard.html')

# ── Auth API ───────────────────────────────────────────────────────────────
@app.route('/api/register', methods=['POST'])
def register():
    d = request.get_json()
    name, email, password, phone = d.get('name','').strip(), d.get('email','').strip().lower(), d.get('password',''), d.get('phone','').strip()
    if not all([name, email, password, phone]):
        return jsonify({'success': False, 'message': 'All fields are required'}), 400
    if mongo.db.users.find_one({'email': email}):
        return jsonify({'success': False, 'message': 'Email already registered'}), 409
    acc = gen_account()
    while mongo.db.users.find_one({'account_number': acc}):
        acc = gen_account()
    user = {'name': name, 'email': email, 'password': generate_password_hash(password),
            'phone': phone, 'account_number': acc, 'balance': 1000.00,
            'savings_balance': 0.00, 'created_at': datetime.utcnow(), 'avatar': name[0].upper()}
    rid = mongo.db.users.insert_one(user).inserted_id
    mongo.db.transactions.insert_one({
        'user_id': rid, 'type': 'credit', 'category': 'bonus', 'amount': 1000.00,
        'description': '🎁 Welcome Bonus', 'balance_after': 1000.00,
        'timestamp': datetime.utcnow(), 'status': 'completed', 'recipient': ''
    })
    session['user_id'] = str(rid)
    return jsonify({'success': True, 'message': 'Account created!'})

@app.route('/api/login', methods=['POST'])
def login():
    d = request.get_json()
    user = mongo.db.users.find_one({'email': d.get('email','').strip().lower()})
    if not user or not check_password_hash(user['password'], d.get('password','')):
        return jsonify({'success': False, 'message': 'Invalid email or password'}), 401
    session['user_id'] = str(user['_id'])
    return jsonify({'success': True})

@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'success': True})

# ── User API ───────────────────────────────────────────────────────────────
@app.route('/api/me')
def me():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    return jsonify({'success': True, 'user': {
        'name': u['name'], 'email': u['email'], 'phone': u['phone'],
        'account_number': u['account_number'], 'balance': u['balance'],
        'savings_balance': u.get('savings_balance', 0.0),
        'avatar': u.get('avatar', u['name'][0].upper()),
        'member_since': u['created_at'].strftime('%B %Y')
    }})

# ── Transactions API ───────────────────────────────────────────────────────
@app.route('/api/transactions')
def transactions():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    page = int(request.args.get('page', 1))
    limit = 10
    txns = list(mongo.db.transactions.find({'user_id': u['_id']}, sort=[('timestamp', -1)]).skip((page-1)*limit).limit(limit))
    total = mongo.db.transactions.count_documents({'user_id': u['_id']})
    return jsonify({'success': True, 'total': total,
        'pages': (total + limit - 1) // limit,
        'transactions': [{
            'id': str(t['_id']), 'type': t['type'], 'category': t.get('category','transfer'),
            'amount': t['amount'], 'description': t['description'],
            'balance_after': t.get('balance_after', 0), 'recipient': t.get('recipient',''),
            'timestamp': t['timestamp'].strftime('%b %d, %Y · %I:%M %p'),
            'status': t.get('status','completed')
        } for t in txns]
    })

@app.route('/api/transfer', methods=['POST'])
def transfer():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    d = request.get_json()
    acc, amount, desc = d.get('account_number','').strip(), float(d.get('amount',0)), d.get('description','Transfer').strip()
    if amount <= 0:
        return jsonify({'success': False, 'message': 'Enter a valid amount'}), 400
    if amount > u['balance']:
        return jsonify({'success': False, 'message': 'Insufficient funds'}), 400
    if acc == u['account_number']:
        return jsonify({'success': False, 'message': 'Cannot transfer to yourself'}), 400
    recipient = mongo.db.users.find_one({'account_number': acc})
    if not recipient:
        return jsonify({'success': False, 'message': 'Account not found'}), 404
    nb = u['balance'] - amount
    rnb = recipient['balance'] + amount
    mongo.db.users.update_one({'_id': u['_id']}, {'$set': {'balance': nb}})
    mongo.db.users.update_one({'_id': recipient['_id']}, {'$set': {'balance': rnb}})
    now = datetime.utcnow()
    mongo.db.transactions.insert_one({'user_id': u['_id'], 'type': 'debit', 'category': 'transfer',
        'amount': amount, 'description': desc or f'Transfer to {recipient["name"]}',
        'recipient': recipient['name'], 'balance_after': nb, 'timestamp': now, 'status': 'completed'})
    mongo.db.transactions.insert_one({'user_id': recipient['_id'], 'type': 'credit', 'category': 'transfer',
        'amount': amount, 'description': f'Transfer from {u["name"]}',
        'recipient': u['name'], 'balance_after': rnb, 'timestamp': now, 'status': 'completed'})
    return jsonify({'success': True, 'message': f'${amount:,.2f} sent to {recipient["name"]}', 'new_balance': nb})

@app.route('/api/deposit', methods=['POST'])
def deposit():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    d = request.get_json()
    amount, method = float(d.get('amount', 0)), d.get('method', 'Card')
    if amount <= 0 or amount > 50000:
        return jsonify({'success': False, 'message': 'Amount must be $1–$50,000'}), 400
    nb = u['balance'] + amount
    mongo.db.users.update_one({'_id': u['_id']}, {'$set': {'balance': nb}})
    mongo.db.transactions.insert_one({'user_id': u['_id'], 'type': 'credit', 'category': 'deposit',
        'amount': amount, 'description': f'Deposit via {method}', 'balance_after': nb,
        'timestamp': datetime.utcnow(), 'status': 'completed', 'recipient': ''})
    return jsonify({'success': True, 'message': f'${amount:,.2f} deposited!', 'new_balance': nb})

@app.route('/api/savings', methods=['POST'])
def savings():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    d = request.get_json()
    amount, direction = float(d.get('amount', 0)), d.get('direction', 'to_savings')
    if amount <= 0:
        return jsonify({'success': False, 'message': 'Invalid amount'}), 400
    if direction == 'to_savings':
        if amount > u['balance']:
            return jsonify({'success': False, 'message': 'Insufficient checking balance'}), 400
        nc, ns = u['balance'] - amount, u.get('savings_balance', 0) + amount
        desc, t = 'Move to Savings', 'debit'
    else:
        if amount > u.get('savings_balance', 0):
            return jsonify({'success': False, 'message': 'Insufficient savings balance'}), 400
        nc, ns = u['balance'] + amount, u.get('savings_balance', 0) - amount
        desc, t = 'Move from Savings', 'credit'
    mongo.db.users.update_one({'_id': u['_id']}, {'$set': {'balance': nc, 'savings_balance': ns}})
    mongo.db.transactions.insert_one({'user_id': u['_id'], 'type': t, 'category': 'savings',
        'amount': amount, 'description': desc, 'balance_after': nc,
        'timestamp': datetime.utcnow(), 'status': 'completed', 'recipient': ''})
    return jsonify({'success': True, 'message': f'${amount:,.2f} moved!', 'checking': nc, 'savings': ns})

@app.route('/api/analytics')
def analytics():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    six_ago = datetime.utcnow() - timedelta(days=180)
    pipeline = [
        {'$match': {'user_id': u['_id'], 'timestamp': {'$gte': six_ago}}},
        {'$group': {'_id': {'month': {'$month': '$timestamp'}, 'year': {'$year': '$timestamp'}, 'type': '$type'}, 'total': {'$sum': '$amount'}}},
        {'$sort': {'_id.year': 1, '_id.month': 1}}
    ]
    results = list(mongo.db.transactions.aggregate(pipeline))
    months = {}
    for r in results:
        key = f"{r['_id']['year']}-{r['_id']['month']:02d}"
        if key not in months:
            months[key] = {'income': 0, 'expense': 0}
        if r['_id']['type'] == 'credit':
            months[key]['income'] = round(r['total'], 2)
        else:
            months[key]['expense'] = round(r['total'], 2)
    cats = list(mongo.db.transactions.aggregate([
        {'$match': {'user_id': u['_id'], 'type': 'debit'}},
        {'$group': {'_id': '$category', 'total': {'$sum': '$amount'}}},
        {'$sort': {'total': -1}}
    ]))
    return jsonify({'success': True,
        'monthly': [{'month': k, **v} for k, v in sorted(months.items())],
        'categories': [{'name': c['_id'], 'amount': round(c['total'], 2)} for c in cats]
    })

@app.route('/api/profile', methods=['POST'])
def update_profile():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    d = request.get_json()
    updates = {}
    if d.get('name'):
        updates['name'] = d['name'].strip()
        updates['avatar'] = d['name'][0].upper()
    if d.get('phone'):
        updates['phone'] = d['phone'].strip()
    if updates:
        mongo.db.users.update_one({'_id': u['_id']}, {'$set': updates})
    return jsonify({'success': True, 'message': 'Profile updated!'})

@app.route('/api/change-password', methods=['POST'])
def change_password():
    u = current_user()
    if not u:
        return jsonify({'success': False}), 401
    d = request.get_json()
    if not check_password_hash(u['password'], d.get('current_password', '')):
        return jsonify({'success': False, 'message': 'Current password is wrong'}), 400
    new_p = d.get('new_password', '')
    if len(new_p) < 6:
        return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
    mongo.db.users.update_one({'_id': u['_id']}, {'$set': {'password': generate_password_hash(new_p)}})
    return jsonify({'success': True, 'message': 'Password changed!'})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
