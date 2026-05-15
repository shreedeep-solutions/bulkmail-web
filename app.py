import os, re, smtplib, threading, base64, secrets, io
from datetime import datetime, timedelta
from functools import wraps
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders

from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, flash, send_file)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash

# ── app setup ──
app = Flask(__name__, instance_relative_config=True)
app.config['SECRET_KEY']                  = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PERMANENT_SESSION_LIFETIME']  = timedelta(hours=8)
app.config['SESSION_COOKIE_HTTPONLY']     = True
app.config['SESSION_COOKIE_SAMESITE']     = 'Lax'
app.config['MAX_CONTENT_LENGTH']          = 200 * 1024 * 1024  # 200MB

db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance', 'bulkmail.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'

db = SQLAlchemy(app)

# ── domain lock ──
ALLOWED_DOMAINS = {'bulkemail.shreedeepsolutions.com', '127.0.0.1', 'localhost'}

@app.before_request
def enforce_domain():
    if request.endpoint and request.endpoint.startswith('static'):
        return
    host = request.host.split(':')[0]
    if host not in ALLOWED_DOMAINS:
        return render_template('locked.html', host=host), 403

# ══════════════════════════════════════════
# MODELS
# ══════════════════════════════════════════

class Organization(db.Model):
    id         = db.Column(db.Integer, primary_key=True)
    name       = db.Column(db.String(150), nullable=False)
    slug       = db.Column(db.String(80), unique=True, nullable=False)
    plan       = db.Column(db.String(20), default='free')
    active     = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    users      = db.relationship('User', backref='org', lazy=True)
    gmail_cfg  = db.relationship('GmailConfig', backref='org', uselist=False, lazy=True)
    send_logs  = db.relationship('SendLog', backref='org', lazy=True)

class User(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    org_id       = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    username     = db.Column(db.String(80), nullable=False)
    password     = db.Column(db.String(200), nullable=False)
    display_name = db.Column(db.String(120), nullable=False)
    role         = db.Column(db.String(20), default='staff')
    active       = db.Column(db.Boolean, default=True)
    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    last_login   = db.Column(db.DateTime, nullable=True)
    logs         = db.relationship('SendLog', backref='user', lazy=True)
    __table_args__ = (db.UniqueConstraint('org_id', 'username', name='uq_org_username'),)

class GmailConfig(db.Model):
    id           = db.Column(db.Integer, primary_key=True)
    org_id       = db.Column(db.Integer, db.ForeignKey('organization.id'), unique=True, nullable=False)
    gmail        = db.Column(db.String(200))
    app_password = db.Column(db.String(200))

class SendLog(db.Model):
    id        = db.Column(db.Integer, primary_key=True)
    org_id    = db.Column(db.Integer, db.ForeignKey('organization.id'), nullable=False)
    user_id   = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    recipient = db.Column(db.String(200))
    subject   = db.Column(db.String(300))
    files     = db.Column(db.String(500))
    status    = db.Column(db.String(20))
    sent_at   = db.Column(db.DateTime, default=datetime.utcnow)

# ══════════════════════════════════════════
# AUTH DECORATORS
# ══════════════════════════════════════════

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or not user.active or not user.org.active:
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
        if not user or user.role not in ('admin', 'superadmin'):
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def superadmin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or user.role != 'superadmin':
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def get_current_user():
    return User.query.get(session.get('user_id'))

# ══════════════════════════════════════════
# DB INIT
# ══════════════════════════════════════════

def init_db():
    with app.app_context():
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        db.create_all()
        try:
            if not Organization.query.first():
                org = Organization(name='Shreedeep Solutions', slug='shreedeep')
                db.session.add(org)
                db.session.flush()
                admin = User(org_id=org.id, username='admin',
                             password=generate_password_hash('Admin@1234'),
                             display_name='Administrator', role='superadmin')
                db.session.add(admin)
                db.session.commit()
                print("✅ Superadmin created — admin / Admin@1234")
        except Exception as e:
            db.session.rollback()
            print(f"Init note: {e}")

init_db()

# ══════════════════════════════════════════
# AUTH ROUTES
# ══════════════════════════════════════════

@app.route('/')
def index():
    return redirect(url_for('dashboard') if 'user_id' in session else url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username','').strip().lower()
        password = request.form.get('password','')
        user = User.query.filter_by(username=username, active=True).first()
        if user and check_password_hash(user.password, password) and user.org.active:
            session.permanent = True
            session['user_id']   = user.id
            session['org_id']    = user.org_id
            session['user_role'] = user.role
            user.last_login = datetime.utcnow()
            db.session.commit()
            if user.role == 'superadmin': return redirect(url_for('superadmin_panel'))
            if user.role == 'admin':      return redirect(url_for('admin_panel'))
            return redirect(url_for('dashboard'))
        flash('Invalid username or password', 'error')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

# ══════════════════════════════════════════
# SUPERADMIN — all organizations
# ══════════════════════════════════════════

@app.route('/superadmin')
@superadmin_required
def superadmin_panel():
    orgs = Organization.query.order_by(Organization.created_at.desc()).all()
    return render_template('superadmin.html', orgs=orgs, current_user=get_current_user())

@app.route('/superadmin/add_org', methods=['POST'])
@superadmin_required
def add_org():
    data = request.json
    name           = data.get('name','').strip()
    slug           = re.sub(r'[^a-z0-9]','-', data.get('slug','').strip().lower())
    admin_name     = data.get('admin_name','').strip()
    admin_username = data.get('admin_username','').strip().lower()
    admin_password = data.get('admin_password','')
    if not all([name, slug, admin_name, admin_username, admin_password]):
        return jsonify({'ok': False, 'message': 'All fields required'})
    if Organization.query.filter_by(slug=slug).first():
        return jsonify({'ok': False, 'message': 'Slug already taken'})
    org = Organization(name=name, slug=slug, plan=data.get('plan','free'))
    db.session.add(org)
    db.session.flush()
    db.session.add(User(org_id=org.id, username=admin_username,
                        password=generate_password_hash(admin_password),
                        display_name=admin_name, role='admin'))
    db.session.commit()
    return jsonify({'ok': True, 'message': f'"{name}" created successfully!'})

@app.route('/superadmin/toggle_org/<int:oid>', methods=['POST'])
@superadmin_required
def toggle_org(oid):
    org = Organization.query.get_or_404(oid)
    org.active = not org.active
    db.session.commit()
    return jsonify({'ok': True, 'active': org.active})

@app.route('/superadmin/delete_org/<int:oid>', methods=['POST'])
@superadmin_required
def delete_org(oid):
    org = Organization.query.get_or_404(oid)
    SendLog.query.filter_by(org_id=oid).delete()
    User.query.filter_by(org_id=oid).delete()
    if org.gmail_cfg: db.session.delete(org.gmail_cfg)
    db.session.delete(org)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/superadmin/update_plan/<int:oid>', methods=['POST'])
@superadmin_required
def update_plan(oid):
    org = Organization.query.get_or_404(oid)
    org.plan = request.json.get('plan','free')
    db.session.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════
# ADMIN — own org only
# ══════════════════════════════════════════

@app.route('/admin')
@admin_required
def admin_panel():
    cu    = get_current_user()
    org   = cu.org
    users = User.query.filter_by(org_id=org.id).order_by(User.created_at.desc()).all()
    logs  = SendLog.query.filter_by(org_id=org.id).order_by(SendLog.sent_at.desc()).limit(100).all()
    cfg   = GmailConfig.query.filter_by(org_id=org.id).first()
    return render_template('admin.html', users=users, logs=logs, cfg=cfg,
                           current_user=cu, org=org)

@app.route('/admin/add_user', methods=['POST'])
@admin_required
def add_user():
    data     = request.json
    org_id   = session['org_id']
    username = data.get('username','').strip().lower()
    if not all([username, data.get('password'), data.get('display_name')]):
        return jsonify({'ok': False, 'message': 'All fields required'})
    if User.query.filter_by(org_id=org_id, username=username).first():
        return jsonify({'ok': False, 'message': 'Username already exists'})
    db.session.add(User(org_id=org_id, username=username,
                        password=generate_password_hash(data['password']),
                        display_name=data['display_name'].strip(),
                        role=data.get('role','staff')))
    db.session.commit()
    return jsonify({'ok': True, 'message': f'User {username} created!'})

@app.route('/admin/toggle_user/<int:uid>', methods=['POST'])
@admin_required
def toggle_user(uid):
    user = User.query.get_or_404(uid)
    if user.org_id != session['org_id']:
        return jsonify({'ok': False, 'message': 'Unauthorized'})
    if user.id == session['user_id']:
        return jsonify({'ok': False, 'message': "Can't deactivate yourself"})
    user.active = not user.active
    db.session.commit()
    return jsonify({'ok': True, 'active': user.active})

@app.route('/admin/delete_user/<int:uid>', methods=['POST'])
@admin_required
def delete_user(uid):
    user = User.query.get_or_404(uid)
    if user.org_id != session['org_id'] or user.id == session['user_id']:
        return jsonify({'ok': False, 'message': 'Unauthorized'})
    SendLog.query.filter_by(user_id=uid).delete()
    db.session.delete(user)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/reset_password/<int:uid>', methods=['POST'])
@admin_required
def reset_password(uid):
    user = User.query.get_or_404(uid)
    if user.org_id != session['org_id']:
        return jsonify({'ok': False, 'message': 'Unauthorized'})
    pw = request.json.get('password','')
    if len(pw) < 6:
        return jsonify({'ok': False, 'message': 'Min 6 characters'})
    user.password = generate_password_hash(pw)
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/save_gmail', methods=['POST'])
@admin_required
def save_gmail():
    data   = request.json
    org_id = session['org_id']
    cfg    = GmailConfig.query.filter_by(org_id=org_id).first()
    if not cfg:
        cfg = GmailConfig(org_id=org_id)
        db.session.add(cfg)
    cfg.gmail        = data.get('gmail','').strip()
    cfg.app_password = data.get('app_password','').strip()
    db.session.commit()
    return jsonify({'ok': True})

@app.route('/admin/test_gmail', methods=['POST'])
@admin_required
def test_gmail():
    cfg = GmailConfig.query.filter_by(org_id=session['org_id']).first()
    if not cfg or not cfg.gmail:
        return jsonify({'ok': False, 'message': 'No Gmail configured'})
    try:
        with smtplib.SMTP('smtp.gmail.com', 587, timeout=15) as s:
            s.ehlo(); s.starttls(); s.ehlo()
            s.login(cfg.gmail, cfg.app_password)
        return jsonify({'ok': True, 'message': 'Login successful!'})
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})

# ══════════════════════════════════════════
# DASHBOARD
# ══════════════════════════════════════════

@app.route('/dashboard')
@login_required
def dashboard():
    return render_template('dashboard.html', user=get_current_user())

# ══════════════════════════════════════════
# EMAIL API — progress fix: log appended, never reset
# ══════════════════════════════════════════

send_jobs = {}

@app.route('/api/preview', methods=['POST'])
@login_required
def preview():
    data = request.json
    return jsonify({'subject': _render(data.get('subject',''), data.get('row',{})),
                    'body':    _render(data.get('body',''),    data.get('row',{}))})

@app.route('/api/send', methods=['POST'])
@login_required
def send_emails():
    org_id = session['org_id']
    cfg    = GmailConfig.query.filter_by(org_id=org_id).first()
    if not cfg or not cfg.gmail:
        return jsonify({'ok': False, 'message': 'Gmail not configured. Ask your admin.'})

    data        = request.json
    rows        = data.get('rows', [])
    subject_tpl = data.get('subject', '')
    body_tpl    = data.get('body', '')
    cc_list     = [e.strip() for e in data.get('cc','').split(',') if e.strip()]
    bcc_list    = [e.strip() for e in data.get('bcc','').split(',') if e.strip()]
    pdf_data    = data.get('pdf_data', {})
    user_id     = session['user_id']

    groups = {}
    for row in rows:
        email = str(row.get('Email','')).strip()
        if not email: continue
        fname = str(row.get('File_Name','')).strip()
        if email not in groups:
            groups[email] = {'row': row, 'pdfs': []}
        if fname and fname in pdf_data:
            groups[email]['pdfs'].append(fname)

    job_id = secrets.token_hex(8)
    # PROGRESS FIX: log is append-only, never cleared by server
    send_jobs[job_id] = {
        'running': True, 'done': False,
        'total': len(groups), 'sent': 0, 'failed': 0,
        'log': [{'type':'info','msg':f'Starting — {len(groups)} recipient(s)...'}]
    }

    gmail_addr = cfg.gmail
    gmail_pass = cfg.app_password

    def do_send():
        job = send_jobs[job_id]
        try:
            server = smtplib.SMTP('smtp.gmail.com', 587, timeout=15)
            server.ehlo(); server.starttls(); server.ehlo()
            server.login(gmail_addr, gmail_pass)
        except Exception as e:
            job['log'].append({'type':'error','msg':f'Login failed: {e}'})
            job['running'] = False; job['done'] = True
            return

        with app.app_context():
            for email, info in groups.items():
                row  = info['row']
                subj = _render(subject_tpl, row)
                body = _render(body_tpl, row)
                msg  = MIMEMultipart()
                msg['From']    = gmail_addr
                msg['To']      = email
                msg['Subject'] = subj
                if cc_list: msg['Cc'] = ', '.join(cc_list)
                msg.attach(MIMEText(body, 'plain'))
                for fname in info['pdfs']:
                    try:
                        part = MIMEBase('application','octet-stream')
                        part.set_payload(base64.b64decode(pdf_data[fname]))
                        encoders.encode_base64(part)
                        part.add_header('Content-Disposition',f'attachment; filename="{fname}"')
                        msg.attach(part)
                    except Exception as e:
                        job['log'].append({'type':'warn','msg':f'Attach error {fname}: {e}'})
                try:
                    server.sendmail(gmail_addr,[email]+cc_list+bcc_list,msg.as_string())
                    job['sent'] += 1
                    job['log'].append({'type':'success','msg':f'✅ {email} ({len(info["pdfs"])} file(s))'})
                    db.session.add(SendLog(org_id=org_id,user_id=user_id,recipient=email,
                                          subject=subj,files=', '.join(info['pdfs']),status='success'))
                except Exception as e:
                    job['failed'] += 1
                    job['log'].append({'type':'error','msg':f'❌ {email} — {e}'})
                    db.session.add(SendLog(org_id=org_id,user_id=user_id,recipient=email,
                                          subject=subj,files=', '.join(info['pdfs']),status='failed'))
            db.session.commit()

        server.quit()
        job['log'].append({'type':'info','msg':'─────────────────────────────'})
        job['log'].append({'type':'info','msg':f'Done. ✅ {job["sent"]} sent  ❌ {job["failed"]} failed'})
        job['running'] = False
        job['done']    = True

    threading.Thread(target=do_send, daemon=True).start()
    return jsonify({'ok': True, 'job_id': job_id})

@app.route('/api/send_status/<job_id>')
@login_required
def send_status(job_id):
    return jsonify(send_jobs.get(job_id,
                   {'done':True,'total':0,'sent':0,'failed':0,'log':[]}))

# ══════════════════════════════════════════
# PDF SPLITTER
# ══════════════════════════════════════════

@app.route('/api/split_pdf', methods=['POST'])
@login_required
def split_pdf():
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError:
        return jsonify({'ok': False, 'message': 'pypdf library missing'}), 500

    if 'pdf' not in request.files:
        return jsonify({'ok': False, 'message': 'No PDF uploaded'})

    pdf_file  = request.files['pdf']
    reader    = PdfReader(pdf_file)
    total     = len(reader.pages)
    if total == 0:
        return jsonify({'ok': False, 'message': 'PDF has no pages'})

    import zipfile
    base_name = os.path.splitext(pdf_file.filename)[0]
    zip_buf   = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, page in enumerate(reader.pages):
            writer = PdfWriter()
            writer.add_page(page)
            page_buf = io.BytesIO()
            writer.write(page_buf)
            zf.writestr(f'{base_name}_page_{i+1:03d}.pdf', page_buf.getvalue())

    zip_buf.seek(0)
    return send_file(zip_buf, mimetype='application/zip', as_attachment=True,
                     download_name=f'{base_name}_split_{total}pages.zip')

# ══════════════════════════════════════════
# CHANGE OWN PASSWORD
# ══════════════════════════════════════════

@app.route('/api/change_password', methods=['POST'])
@login_required
def change_password():
    data   = request.json
    user   = get_current_user()
    old_pw = data.get('old_password','')
    new_pw = data.get('new_password','')
    if not check_password_hash(user.password, old_pw):
        return jsonify({'ok': False, 'message': 'Current password incorrect'})
    if len(new_pw) < 6:
        return jsonify({'ok': False, 'message': 'Min 6 characters'})
    user.password = generate_password_hash(new_pw)
    db.session.commit()
    return jsonify({'ok': True})

# ══════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════

def _render(template, row):
    for k, v in row.items():
        template = template.replace(f'{{{k}}}', str(v) if v else '')
    return template

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
