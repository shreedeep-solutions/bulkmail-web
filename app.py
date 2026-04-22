import os, re, json, smtplib, threading, base64, hashlib, secrets
from datetime import datetime, timedelta
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# ── app setup ──
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY']         = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///bulkmail.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

db = SQLAlchemy(app)

# ── domain lock ──
ALLOWED_DOMAINS = {'bulkemail.shreedeepsolutions.com', 'bulkmail-pro.onrender.com', '127.0.0.1', 'localhost'}

def domain_check():
    host = request.host.split(':')[0]
    if host not in ALLOWED_DOMAINS:
        return render_template('locked.html', host=host), 403
    return None

@app.before_request
def enforce_domain():
    # Skip static files
    if request.endpoint and request.endpoint.startswith('static'):
        return
    block = domain_check()
    if block:
        return block

# ── models ──
class User(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    username     = db.Column(db.String(80), unique=True, nullable=False)
    password     = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role         = db.Column(db.String(20), default='staff')   # admin | staff
    active       = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_login   = db.Column(db.DateTime, nullable=True)
    logs         = db.relationship('SendLog', backref='user', lazy=True)

class SendLog(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    user_id    = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient  = db.Column(db.String(200))
    subject    = db.Column(db.String(300))
    files      = db.Column(db.String(500))
    status     = db.Column(db.String(20))   # success | failed
    sent_at    = db.Column(db.DateTime, default=datetime.utcnow)

class GmailConfig(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    gmail        = db.Column(db.String(200))
    app_password = db.Column(db.String(200))

# ── auth decorators ──
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.active:
            session.clear()
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or user.role != 'admin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ── init db ──
def init_db():
    with app.app_context():
        db.create_all()
        # Create default admin if none exists
        if not User.query.filter_by(role='admin').first():
            admin = User(
                username='admin',
                password=generate_password_hash('Admin@1234'),
                display_name='Administrator',
                role='admin'
            )
            db.session.add(admin)
            db.session.commit()
            print("✅ Default admin created — username: admin, password: Admin@1234")
            print("⚠️  CHANGE THIS PASSWORD IMMEDIATELY after first login!")

# ─────────────────────────────────────────
# AUTH ROUTES
# ─────────────────────────────────────────
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip().lower()
        password = request.form.get('password', '')
        user = User.query.filter_by(username=username, active=True).first()
        if user and check_password_hash(user.password, password):
            session.permanent = True
            session['user_id']   = user.id
            session['user_name'] = user.display_name
            session['user_role'] = user.role
            user.last_login = datetime.utcnow()
            db.session.commit()
            return redirect(url_for('admin_panel') if user.role == 'admin' else url_for('dashboard'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ─────────────────────────────────────────
# DASHBOARD (staff)
# ─────────────────────────────────────────
@app.route('/dashboard')
@login_required
def dashboard():
    user = User.query.get(session['user_id'])
    return render_template('dashboard.html', user=user)

# ─────────────────────────────────────────
# ADMIN PANEL
# ─────────────────────────────────────────
@app.route('/admin')
@admin_required
def admin_panel():
    users = User.query.order_by(User.created_at.desc()).all()
    logs  = SendLog.query.order_by(SendLog.sent_at.desc()).limit(100).all()
    cfg   = GmailConfig.query.first()
    return render_template('admin.html', users=users, logs=logs, cfg=cfg,
                           current_user=User.query.get(session['user_id']))

@app.route('/admin/add_user', methods=['POST'])
@admin_required
def add_user():
    data = request.json
    username = data.get('username', '').strip().lower()
    if not username or not data.get('password') or not data.get('display_name'):
        return jsonify({'ok': False, 'message': 'All fields are required'})
    if User.query.filter_by(username=username).first():
        return jsonify({'ok': False, 'message': 'Username already exists'})
    user = User(
        username=username,
        password=generate_password_hash(data['password']),
        display_name=data['display_name'].strip(),
        role=data.get('role', 'staff')
    )
    db.session.add(user)
    db.session.commit()
    return jsonify({'ok': True, 'message': f'User {username} created successfully'})

@app.route('/admin/toggle_user/<int:uid>', methods=['POST'])
@admin_required
def toggle_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == session['user_id']:
        return jsonify({'ok': False, 'message': "You can't deactivate yourself"})
    user.active = not user.active
    db.session.commit()
    return jsonify({'ok': True, 'active': user.active})

@app.route('/admin/delete_user/<int:uid>', methods=['POST'])
@admin_required
def delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.id == session['user_id']:
        return jsonify({'ok': False, 'message': "You can't delete yourself"})
    SendLog.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/reset_password/<int:uid>', methods=['POST'])
@admin_required
def reset_password(uid):
    user = User.query.get_or_404(uid)
    new_pw = request.json.get('password', '')
    if len(new_pw) < 6:
        return jsonify({'ok': False, 'message': 'Password must be at least 6 characters'})
    user.password = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/save_gmail', methods=['POST'])
@admin_required
def save_gmail():
    data = request.json
    cfg = GmailConfig.query.first()
    if not cfg:
        cfg = GmailConfig()
        db.session.add(cfg)
    cfg.gmail        = data.get('gmail', '').strip()
    cfg.app_password = data.get('app_password', '').strip()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/test_gmail', methods=['POST'])
@admin_required
def test_gmail():
    cfg = GmailConfig.query.first()
    if not cfg or not cfg.gmail:
        return jsonify({'ok': False, 'message': 'No Gmail configured yet'})
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(cfg.gmail, cfg.app_password)
        return jsonify({'ok': True, 'message': 'Login successful!'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})

# ─────────────────────────────────────────
# EMAIL API
# ─────────────────────────────────────────
@app.route('/api/preview', methods=['POST'])
@login_required
def preview():
    data = request.json
    row  = data.get('row', {})
    subj = _render(data.get('subject', ''), row)
    body = _render(data.get('body', ''), row)
    return jsonify({'subject': subj, 'body': body})

send_jobs = {}   # job_id -> {status, log}

@app.route('/api/send', methods=['POST'])
@login_required
def send_emails():
    cfg = GmailConfig.query.first()
    if not cfg or not cfg.gmail:
        return jsonify({'ok': False, 'message': 'Gmail not configured. Ask admin.'})

    data        = request.json
    rows        = data.get('rows', [])
    subject_tpl = data.get('subject', '')
    body_tpl    = data.get('body', '')
    cc_list     = [e.strip() for e in data.get('cc','').split(',') if e.strip()]
    bcc_list    = [e.strip() for e in data.get('bcc','').split(',') if e.strip()]
    pdf_data    = data.get('pdf_data', {})
    user_id     = session['user_id']

    # Group by email
    groups = {}
    for row in rows:
        email = str(row.get('Email', '')).strip()
        if not email: continue
        fname = str(row.get('File_Name', '')).strip()
        if email not in groups:
            groups[email] = {'row': row, 'pdfs': []}
        if fname and fname in pdf_data:
            groups[email]['pdfs'].append(fname)

    job_id = secrets.token_hex(8)
    send_jobs[job_id] = {'running': True, 'done': False,
                         'total': len(groups), 'sent': 0, 'failed': 0, 'log': []}

    gmail_addr = cfg.gmail
    gmail_pass = cfg.app_password

    def do_send():
        job = send_jobs[job_id]
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=15)
            server.ehlo(); server.starttls(); server.ehlo()
            server.login(gmail_addr, gmail_pass)
        except Exception as e:
            job['log'].append({'type': 'error', 'msg': f'Login failed: {e}'})
            job['running'] = False; job['done'] = True
            return

        with app.app_context():
            for email, info in groups.items():
                row  = info['row']
                subj = _render(subject_tpl, row)
                body = _render(body_tpl, row)

                msg = MIMEMultipart()
                msg['From']    = gmail_addr
                msg['To']      = email
                msg['Subject'] = subj
                if cc_list: msg['Cc'] = ', '.join(cc_list)
                msg.attach(MIMEText(body, 'plain'))

                for fname in info['pdfs']:
                    try:
                        pdf_bytes = base64.b64decode(pdf_data[fname])
                        part = MIMEBase('application', 'octet-stream')
                        part.set_payload(pdf_bytes)
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition', f'attachment; filename="{fname}"')
                        msg.attach(part)
                    except Exception as e:
                        job['log'].append({'type': 'warn', 'msg': f'Attach error {fname}: {e}'})

                try:
                    server.sendmail(gmail_addr, [email] + cc_list + bcc_list, msg.as_string())
                    job['log'].append({'type': 'success', 'msg': f'Sent to {email} ({len(info["pdfs"])} file(s))'})
                    job['sent'] += 1
                    log = SendLog(user_id=user_id, recipient=email, subject=subj,
                                  files=', '.join(info['pdfs']), status='success')
                    db.session.add(log)
                except Exception as e:
                    job['log'].append({'type': 'error', 'msg': f'Failed: {email} — {e}'})
                    job['failed'] += 1
                    log = SendLog(user_id=user_id, recipient=email, subject=subj,
                                  files=', '.join(info['pdfs']), status='failed')
                    db.session.add(log)

            db.session.commit()
        server.quit()
        job['running'] = False; job['done'] = True

    threading.Thread(target=do_send, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})

@app.route('/api/send_status/<job_id>')
@login_required
def send_status(job_id):
    job = send_jobs.get(job_id, {})
    return jsonify(job)

# ─────────────────────────────────────────
# CHANGE OWN PASSWORD
# ─────────────────────────────────────────
@app.route('/api/change_password', methods=['POST'])
@login_required
def change_password():
    data   = request.json
    user   = User.query.get(session['user_id'])
    old_pw = data.get('old_password', '')
    new_pw = data.get('new_password', '')
    if not check_password_hash(user.password, old_pw):
        return jsonify({'ok': False, 'message': 'Current password is incorrect'})
    if len(new_pw) < 6:
        return jsonify({'ok': False, 'message': 'New password must be at least 6 characters'})
    user.password = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({'ok': True})

# ─────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────
def _render(template, row):
    for k, v in row.items():
        template = template.replace(f'{{{k}}}', str(v) if v else '')
    return template

# ─────────────────────────────────────────
# ENTRY
# ─────────────────────────────────────────
if __name__ == '__main__':
    init_db()
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
