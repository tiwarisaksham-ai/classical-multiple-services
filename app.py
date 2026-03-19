import os, hmac, hashlib, uuid, csv, io, json, secrets, threading, time, math
from datetime import datetime, timedelta
from functools import wraps
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, Response
import sqlite3

# Optional deps
try: from twilio.rest import Client as TwilioClient; TWILIO_AVAILABLE=True
except: TWILIO_AVAILABLE=False
try: import razorpay as _rzp_mod; RAZORPAY_AVAILABLE=True
except: RAZORPAY_AVAILABLE=False
try: import smtplib; from email.mime.text import MIMEText; from email.mime.multipart import MIMEMultipart; EMAIL_AVAILABLE=True
except: EMAIL_AVAILABLE=False

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", secrets.token_hex(32))
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=8)

# ── ENV CONFIG ──────────────────────────────────────
RZP_KEY_ID      = os.environ.get("RAZORPAY_KEY_ID",     "rzp_live_SSxh7gcBFWIkna")
RZP_KEY_SECRET  = os.environ.get("RAZORPAY_KEY_SECRET", "uAf1gPWyc5uoBNlhxJ4Zu7Ax")
TWILIO_SID      = os.environ.get("TWILIO_ACCOUNT_SID",  "")
TWILIO_AUTH     = os.environ.get("TWILIO_AUTH_TOKEN",   "")
TWILIO_FROM     = os.environ.get("TWILIO_WHATSAPP_FROM","whatsapp:+14155238886")
ADMIN_PASSWORD  = os.environ.get("ADMIN_PASSWORD",      "Admin@CMS#2024!")
ADMIN_PIN       = os.environ.get("ADMIN_PIN",           "1234")   # 2FA PIN
OWNER_EMAIL     = os.environ.get("OWNER_EMAIL",         "")
SMTP_HOST       = os.environ.get("SMTP_HOST",           "smtp.gmail.com")
SMTP_PORT       = int(os.environ.get("SMTP_PORT",       "587"))
SMTP_USER       = os.environ.get("SMTP_USER",           "")
SMTP_PASS       = os.environ.get("SMTP_PASS",           "")
BUSINESS_PHONE  = os.environ.get("BUSINESS_PHONE",      "9453492793")
MONTHLY_TARGET  = int(os.environ.get("MONTHLY_TARGET",  "50000"))

DEFAULT_PRICES = {
    "Split AC Jetpump Service":499,"Split AC Foam & Chemical Service":649,
    "Window AC Jetpump Service":449,"Window AC Foam & Chemical Service":549,
    "Split AC Repair":299,"Window AC Repair":299,
    "Split AC Installation":1199,"Window AC Installation":699,
    "Split AC Uninstallation":599,"Window AC Uninstallation":499,
    "Split AC Gas Filling":2799,"Window AC Gas Filling":2799,
    "Outdoor Unit Cleaning":599,
}
CITIES = ["Lucknow","Kanpur","Agra","Sitapur","Bareilly","Rae Bareli",
          "Varanasi","Gorakhpur","Jhansi","Mathura","Ambedkar Nagar",
          "Allahabad","Meerut","Noida","Ghaziabad","Barabanki","Ayodhya","Azamgarh"]

# ── DB ──────────────────────────────────────────────
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.db")
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def get_prices():
    try:
        with get_db() as conn:
            rows = conn.execute("SELECT service_name,price FROM service_prices").fetchall()
            if rows: return {r["service_name"]:r["price"] for r in rows}
    except: pass
    return DEFAULT_PRICES.copy()

def generate_vendor_id():
    return "CMS-VND-" + datetime.now().strftime("%Y%m") + str(uuid.uuid4())[:5].upper()

def generate_booking_id():
    return "CMS" + datetime.now().strftime("%Y%m%d") + str(uuid.uuid4())[:6].upper()

def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS bookings(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL, mobile TEXT NOT NULL,
            city TEXT NOT NULL, area TEXT DEFAULT '', pincode TEXT DEFAULT '',
            service_type TEXT NOT NULL, amount INTEGER NOT NULL,
            discount INTEGER DEFAULT 0, final_amount INTEGER NOT NULL,
            payment_mode TEXT DEFAULT 'online', payment_status TEXT DEFAULT 'PENDING',
            razorpay_order_id TEXT, razorpay_payment_id TEXT, razorpay_signature TEXT,
            status TEXT DEFAULT 'BOOKED', remark TEXT DEFAULT '',
            scheduled_at TEXT DEFAULT '', vendor_id INTEGER DEFAULT NULL,
            is_repeat INTEGER DEFAULT 0, visit_count INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS vendors(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_code TEXT UNIQUE,
            name TEXT NOT NULL, mobile TEXT NOT NULL,
            city TEXT NOT NULL, skills TEXT DEFAULT '',
            availability TEXT DEFAULT 'FREE',
            status TEXT DEFAULT 'ACTIVE',
            aadhar_number TEXT DEFAULT '',
            aadhar_verified INTEGER DEFAULT 0,
            rating REAL DEFAULT 5.0, total_jobs INTEGER DEFAULT 0,
            total_earnings REAL DEFAULT 0,
            commission_pct REAL DEFAULT 70.0,
            bank_account TEXT DEFAULT '', bank_ifsc TEXT DEFAULT '',
            upi_id TEXT DEFAULT '',
            address TEXT DEFAULT '',
            joined_date TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS vendor_payments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_id INTEGER NOT NULL,
            booking_id TEXT,
            amount REAL NOT NULL,
            payment_type TEXT DEFAULT 'COMMISSION',
            payment_mode TEXT DEFAULT 'UPI',
            status TEXT DEFAULT 'PENDING',
            note TEXT DEFAULT '',
            paid_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS service_prices(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            service_name TEXT UNIQUE NOT NULL, price INTEGER NOT NULL,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS booking_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            booking_id TEXT NOT NULL, action TEXT NOT NULL,
            note TEXT DEFAULT '', by_user TEXT DEFAULT 'admin',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS services_catalog(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL, category TEXT NOT NULL,
            base_price INTEGER NOT NULL, description TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS inventory(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL, category TEXT DEFAULT 'General',
            current_stock REAL DEFAULT 0,
            unit TEXT DEFAULT 'pcs', min_stock REAL DEFAULT 5,
            cost_per_unit REAL DEFAULT 0,
            vendor_id INTEGER DEFAULT NULL,
            last_updated DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS inventory_logs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL, change_qty REAL NOT NULL,
            change_type TEXT DEFAULT 'USE',
            note TEXT DEFAULT '', booking_id TEXT DEFAULT '',
            by_user TEXT DEFAULT 'admin',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS revenue_targets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            month TEXT UNIQUE NOT NULL,
            target INTEGER NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS notifications(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL, message TEXT NOT NULL,
            type TEXT DEFAULT 'info',
            is_read INTEGER DEFAULT 0,
            booking_id TEXT DEFAULT '',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS admins(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL, password TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            last_login DATETIME)""")

        conn.execute("""CREATE TABLE IF NOT EXISTS login_attempts(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ip TEXT, attempts INTEGER DEFAULT 0,
            locked_until DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP)""")

        # Seed admin
        try: conn.execute("INSERT INTO admins(username,password,role) VALUES(?,?,?)",("admin",ADMIN_PASSWORD,"superadmin"))
        except sqlite3.IntegrityError: pass

        # Seed prices
        if conn.execute("SELECT COUNT(*) FROM service_prices").fetchone()[0]==0:
            for s,p in DEFAULT_PRICES.items():
                try: conn.execute("INSERT INTO service_prices(service_name,price) VALUES(?,?)",(s,p))
                except: pass

        # Seed catalog
        if conn.execute("SELECT COUNT(*) FROM services_catalog").fetchone()[0]==0:
            cats=[("Split AC Jetpump Service","AC Cleaning",499,"Jetpump cleaning Split AC"),
                  ("Split AC Foam & Chemical Service","AC Cleaning",649,"Foam chemical Split AC"),
                  ("Window AC Jetpump Service","AC Cleaning",449,"Jetpump cleaning Window AC"),
                  ("Window AC Foam & Chemical Service","AC Cleaning",549,"Foam chemical Window AC"),
                  ("Outdoor Unit Cleaning","AC Cleaning",599,"Outdoor condenser unit deep clean"),
                  ("Split AC Repair","AC Repair",299,"Repair Split AC"),("Window AC Repair","AC Repair",299,"Repair Window AC"),
                  ("Split AC Installation","Installation",1199,"Install Split AC"),("Window AC Installation","Installation",699,"Install Window AC"),
                  ("Split AC Uninstallation","Uninstallation",599,"Remove Split AC"),("Window AC Uninstallation","Uninstallation",499,"Remove Window AC"),
                  ("Split AC Gas Filling","Gas Filling",2799,"Gas refill Split AC"),("Window AC Gas Filling","Gas Filling",2799,"Gas refill Window AC")]
            for r in cats:
                try: conn.execute("INSERT INTO services_catalog(name,category,base_price,description) VALUES(?,?,?,?)",r)
                except: pass

        # Seed inventory
        if conn.execute("SELECT COUNT(*) FROM inventory").fetchone()[0]==0:
            items=[("R22 Gas Cylinder","Gas",5,"cylinder",2,3500),
                   ("R32 Gas Cylinder","Gas",5,"cylinder",2,4200),
                   ("AC Foam Cleaner","Chemicals",20,"bottle",5,180),
                   ("Chemical Solution","Chemicals",15,"litre",5,250),
                   ("Copper Pipe (per metre)","Pipe",50,"metre",10,120),
                   ("Electrical Cable","Electrical",30,"metre",10,80),
                   ("Drain Pipe","Pipe",20,"metre",5,60),
                   ("Wall Bracket","Hardware",10,"pcs",3,350),
                   ("Capacitor","Spares",15,"pcs",5,280),
                   ("Thermostat","Spares",8,"pcs",3,650)]
            for it in items:
                try: conn.execute("INSERT INTO inventory(item_name,category,current_stock,unit,min_stock,cost_per_unit) VALUES(?,?,?,?,?,?)",it)
                except: pass

        # Seed monthly target
        month = datetime.now().strftime("%Y-%m")
        try: conn.execute("INSERT INTO revenue_targets(month,target) VALUES(?,?)",(month,MONTHLY_TARGET))
        except: pass
        conn.commit()
    print("[DB] Ready.")

def migrate_db():
    cols_needed=[
        ("bookings","remark","ALTER TABLE bookings ADD COLUMN remark TEXT DEFAULT ''"),
        ("bookings","scheduled_at","ALTER TABLE bookings ADD COLUMN scheduled_at TEXT DEFAULT ''"),
        ("bookings","vendor_id","ALTER TABLE bookings ADD COLUMN vendor_id INTEGER DEFAULT NULL"),
        ("bookings","updated_at","ALTER TABLE bookings ADD COLUMN updated_at DATETIME DEFAULT CURRENT_TIMESTAMP"),
        ("bookings","payment_mode","ALTER TABLE bookings ADD COLUMN payment_mode TEXT DEFAULT 'online'"),
        ("bookings","is_repeat","ALTER TABLE bookings ADD COLUMN is_repeat INTEGER DEFAULT 0"),
        ("bookings","visit_count","ALTER TABLE bookings ADD COLUMN visit_count INTEGER DEFAULT 1"),
        ("vendors","vendor_code","ALTER TABLE vendors ADD COLUMN vendor_code TEXT"),
        ("vendors","aadhar_number","ALTER TABLE vendors ADD COLUMN aadhar_number TEXT DEFAULT ''"),
        ("vendors","aadhar_verified","ALTER TABLE vendors ADD COLUMN aadhar_verified INTEGER DEFAULT 0"),
        ("vendors","availability","ALTER TABLE vendors ADD COLUMN availability TEXT DEFAULT 'FREE'"),
        ("vendors","total_earnings","ALTER TABLE vendors ADD COLUMN total_earnings REAL DEFAULT 0"),
        ("vendors","commission_pct","ALTER TABLE vendors ADD COLUMN commission_pct REAL DEFAULT 70.0"),
        ("vendors","bank_account","ALTER TABLE vendors ADD COLUMN bank_account TEXT DEFAULT ''"),
        ("vendors","bank_ifsc","ALTER TABLE vendors ADD COLUMN bank_ifsc TEXT DEFAULT ''"),
        ("vendors","upi_id","ALTER TABLE vendors ADD COLUMN upi_id TEXT DEFAULT ''"),
        ("vendors","address","ALTER TABLE vendors ADD COLUMN address TEXT DEFAULT ''"),
        ("vendors","joined_date","ALTER TABLE vendors ADD COLUMN joined_date TEXT DEFAULT ''"),
    ]
    with get_db() as conn:
        for tbl,col,sql in cols_needed:
            try:
                existing=[r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
                if col not in existing: conn.execute(sql); print(f"[DB] Added {tbl}.{col}")
            except Exception as e: print(f"[DB] skip {tbl}.{col}: {e}")
        # Add vendor_code to existing vendors if missing
        try:
            vs=conn.execute("SELECT id FROM vendors WHERE vendor_code IS NULL OR vendor_code=''").fetchall()
            for v in vs:
                conn.execute("UPDATE vendors SET vendor_code=? WHERE id=?",(generate_vendor_id(),v["id"]))
        except: pass
        conn.commit()
    print("[DB] Migration done.")

init_db()
migrate_db()

# ── Razorpay ────────────────────────────────────────
_rzp=None
def get_rzp():
    global _rzp
    if _rzp is None and RAZORPAY_AVAILABLE:
        try: _rzp=_rzp_mod.Client(auth=(RZP_KEY_ID,RZP_KEY_SECRET))
        except Exception as e: print(f"[RZP] {e}")
    return _rzp

# ── Notifications ────────────────────────────────────
def push_notif(title,message,ntype="info",booking_id=""):
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO notifications(title,message,type,booking_id) VALUES(?,?,?,?)",(title,message,ntype,booking_id))
            conn.commit()
    except: pass

# ── WhatsApp ─────────────────────────────────────────
def send_whatsapp(mobile,msg):
    if not(TWILIO_AVAILABLE and TWILIO_SID and TWILIO_AUTH): return False
    try:
        TwilioClient(TWILIO_SID,TWILIO_AUTH).messages.create(
            from_=TWILIO_FROM,to=f"whatsapp:+91{mobile}",body=msg)
        return True
    except Exception as e: print(f"[WA] {e}"); return False

def notify_booking(bid,name,mobile,service,amount,pay_mode):
    msg=(f"✅ *Booking Confirmed!*\n"
         f"ID: *{bid}*\n"
         f"Name: {name}\n"
         f"Service: {service}\n"
         f"Amount: ₹{amount}\n"
         f"Mode: {pay_mode}\n"
         f"⏱ Technician in 60–90 min\n"
         f"📞 Support: {BUSINESS_PHONE}")
    send_whatsapp(mobile,msg)
    # Owner notify
    send_whatsapp(BUSINESS_PHONE,f"🔔 *New Booking!*\nID: {bid}\nCustomer: {name} ({mobile})\nService: {service}\nCity: \nAmt: ₹{amount}")
    push_notif(f"New Booking: {bid}",f"{name} — {service} — ₹{amount}","success",bid)

def notify_vendor(vendor_mobile,bid,name,city,service,scheduled):
    msg=(f"🔧 *New Job Assigned!*\n"
         f"Booking: *{bid}*\n"
         f"Customer: {name}\n"
         f"City: {city}\n"
         f"Service: {service}\n"
         f"Schedule: {scheduled or 'ASAP — 60-90 min'}\n"
         f"📞 Contact: {BUSINESS_PHONE}")
    send_whatsapp(vendor_mobile,msg)

# ── Email ─────────────────────────────────────────────
def send_email(to,subject,html_body):
    if not(EMAIL_AVAILABLE and SMTP_USER and SMTP_PASS and to): return
    try:
        msg=MIMEMultipart("alternative")
        msg["Subject"]=subject; msg["From"]=SMTP_USER; msg["To"]=to
        msg.attach(MIMEText(html_body,"html"))
        with smtplib.SMTP(SMTP_HOST,SMTP_PORT) as s:
            s.ehlo(); s.starttls(); s.login(SMTP_USER,SMTP_PASS); s.sendmail(SMTP_USER,to,msg.as_string())
    except Exception as e: print(f"[EMAIL] {e}")

# ── Auto-assign logic ────────────────────────────────
def auto_assign_vendor(bid,city,service):
    try:
        with get_db() as conn:
            v=conn.execute("""SELECT id,name,mobile FROM vendors
                WHERE city=? AND status='ACTIVE' AND availability='FREE'
                ORDER BY total_jobs ASC, rating DESC LIMIT 1""",(city,)).fetchone()
            if v:
                conn.execute("UPDATE bookings SET vendor_id=? WHERE booking_id=?",(v["id"],bid))
                conn.execute("UPDATE vendors SET availability='BUSY' WHERE id=?",(v["id"],))
                conn.commit()
                notify_vendor(v["mobile"],bid,"Customer",city,service,"")
                log_action(bid,"AUTO_ASSIGNED",f"Auto-assigned to {v['name']}")
                return v["name"]
    except Exception as e: print(f"[AUTO-ASSIGN] {e}")
    return None

# ── Repeat customer check ────────────────────────────
def check_repeat(mobile):
    try:
        with get_db() as conn:
            cnt=conn.execute("SELECT COUNT(*) FROM bookings WHERE mobile=?",(mobile,)).fetchone()[0]
            return cnt
    except: return 0

# ── Log action ────────────────────────────────────────
def log_action(bid,action,note="",by="admin"):
    try:
        with get_db() as conn:
            conn.execute("INSERT INTO booking_logs(booking_id,action,note,by_user) VALUES(?,?,?,?)",(bid,action,note,by))
            conn.execute("UPDATE bookings SET updated_at=CURRENT_TIMESTAMP WHERE booking_id=?",(bid,))
            conn.commit()
    except Exception as e: print(f"[LOG] {e}")

# ── Security ──────────────────────────────────────────
def get_client_ip():
    return request.environ.get("HTTP_X_FORWARDED_FOR",request.remote_addr or "unknown").split(",")[0].strip()

def check_rate_limit(ip):
    try:
        with get_db() as conn:
            row=conn.execute("SELECT * FROM login_attempts WHERE ip=?",(ip,)).fetchone()
            if row:
                if row["locked_until"] and datetime.now()<datetime.fromisoformat(row["locked_until"]):
                    return False,f"Too many attempts. Try after {row['locked_until'][11:16]}"
                if row["attempts"]>=5:
                    locked=( datetime.now()+timedelta(minutes=15)).isoformat()
                    conn.execute("UPDATE login_attempts SET locked_until=? WHERE ip=?",(locked,ip))
                    conn.commit()
                    return False,"Account locked for 15 minutes"
        return True,""
    except: return True,""

def record_failed_attempt(ip):
    try:
        with get_db() as conn:
            row=conn.execute("SELECT * FROM login_attempts WHERE ip=?",(ip,)).fetchone()
            if row: conn.execute("UPDATE login_attempts SET attempts=attempts+1 WHERE ip=?",(ip,))
            else: conn.execute("INSERT INTO login_attempts(ip,attempts) VALUES(?,1)",(ip,))
            conn.commit()
    except: pass

def clear_attempts(ip):
    try:
        with get_db() as conn:
            conn.execute("DELETE FROM login_attempts WHERE ip=?",(ip,))
            conn.commit()
    except: pass

def admin_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
        if not session.get("admin_2fa"): return redirect(url_for("admin_2fa"))
        return f(*a,**kw)
    return dec

# ════════════════════════════════════════
# PUBLIC ROUTES
# ════════════════════════════════════════

@app.route("/")
def index():
    prices=get_prices()
    # Convert dict to list of objects for template compatibility
    service_prices_list = [{"service_name": k, "price": v} for k, v in prices.items()]
    return render_template("index.html",razorpay_key=RZP_KEY_ID,
                           business_phone=BUSINESS_PHONE,service_prices=service_prices_list)

@app.route("/create-order",methods=["POST"])
def create_order():
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("name","")).strip()
        mobile=str(d.get("mobile","")).strip()
        city=str(d.get("city","")).strip()
        area=str(d.get("area","")).strip()
        pincode=str(d.get("pincode","")).strip()
        service=str(d.get("service_type","")).strip()
        disc=int(d.get("discount",0))
        pay_mode=str(d.get("payment_mode","online"))  # half_online | cod_token | online
        full_amount_req=int(d.get("full_amount",0))

        if not all([name,mobile,city,service]):
            return jsonify({"error":"Fill all required fields"}),400
        if not mobile.isdigit() or len(mobile)!=10:
            return jsonify({"error":"Valid 10-digit mobile required"}),400

        prices=get_prices()
        if service not in prices:
            return jsonify({"error":"Invalid service selected"}),400

        base_price=prices[service]
        disc=min(disc,200)
        total_after_disc=max(base_price-disc,1)

        if pay_mode=="half_online":
            charge_now=max(1,math.ceil(total_after_disc/2))
            balance_due=total_after_disc-charge_now
        elif pay_mode=="cod_token":
            charge_now=99
            balance_due=max(0,total_after_disc-99)
        else:
            charge_now=total_after_disc
            balance_due=0

        bid=generate_booking_id()
        prev=check_repeat(mobile)
        is_repeat=1 if prev>0 else 0
        visit_count=prev+1

        rzp=get_rzp()
        if rzp:
            try:
                order=rzp.order.create({
                    "amount":charge_now*100,
                    "currency":"INR",
                    "receipt":bid,
                    "notes":{
                        "payment_mode":pay_mode,
                        "full_amount":str(total_after_disc),
                        "charge_now":str(charge_now),
                        "balance_due":str(balance_due)
                    }
                })
                oid=order["id"]
            except Exception as e:
                return jsonify({"error":f"Payment gateway error: {e}"}),500
        else:
            oid="demo_"+str(uuid.uuid4())[:8]

        with get_db() as conn:
            conn.execute("""INSERT INTO bookings
                (booking_id,name,mobile,city,area,pincode,service_type,
                 amount,discount,final_amount,razorpay_order_id,
                 payment_mode,payment_status,is_repeat,visit_count,remark)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (bid,name,mobile,city,area,pincode,service,
                 base_price,disc,total_after_disc,oid,
                 pay_mode,"PENDING",is_repeat,visit_count,
                 f"Now:Rs{charge_now} Balance:Rs{balance_due}"))
            conn.commit()

        log_action(bid,"CREATED",
            f"Mode:{pay_mode} Now:Rs{charge_now} Bal:Rs{balance_due}","customer")

        return jsonify({
            "booking_id":bid,
            "razorpay_order_id":oid,
            "order_id":oid,
            "amount":charge_now*100,
            "charge_now":charge_now,
            "full_amount":total_after_disc,
            "balance_due":balance_due,
            "payment_mode":pay_mode,
            "service":service,
            "name":name,
            "mobile":mobile,
            "key_id":RZP_KEY_ID,
            "is_repeat":is_repeat,
            "visit_count":visit_count
        })
    except Exception as e:
        print(f"[create-order] {e}")
        return jsonify({"error":"Server error. Please try again."}),500

@app.route("/verify-payment",methods=["POST"])
def verify_payment():
    try:
        d=request.get_json(force=True) or {}
        oid=str(d.get("razorpay_order_id",""))
        pid=str(d.get("razorpay_payment_id",""))
        sig=str(d.get("razorpay_signature",""))
        bid=str(d.get("booking_id",""))
        pay_mode=str(d.get("payment_mode","online"))
        full_amount=int(d.get("full_amount",0))
        paid_now=int(d.get("paid_now",0))

        exp=hmac.new(
            RZP_KEY_SECRET.encode(),
            "{oid}|{pid}".format(oid=oid,pid=pid).encode(),
            hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(exp,sig):
            return jsonify({"success":False,"error":"Payment signature mismatch"}),400

        with get_db() as conn:
            row=conn.execute("SELECT * FROM bookings WHERE booking_id=?",(bid,)).fetchone()
            if not row:
                return jsonify({"success":False,"error":"Booking not found"}),404

            balance_due=max(0,full_amount-paid_now)

            if pay_mode=="half_online":
                pay_status="HALF_PAID"
                remark="50%% online: Rs%d | Doorstep: Rs%d" % (paid_now,balance_due)
            elif pay_mode=="cod_token":
                pay_status="TOKEN_PAID"
                remark="Token Rs99 paid | Cash doorstep: Rs%d" % balance_due
            else:
                pay_status="PAID"
                remark="Full payment online"

            conn.execute(
                "UPDATE bookings SET payment_status=?,payment_mode=?,"
                "razorpay_payment_id=?,razorpay_signature=?,"
                "remark=?,updated_at=CURRENT_TIMESTAMP WHERE booking_id=?",
                (pay_status,pay_mode,pid,sig,remark,bid)
            )
            conn.commit()
            row=conn.execute("SELECT * FROM bookings WHERE booking_id=?",(bid,)).fetchone()

        if row:
            svc=row["service_type"]
            mob=row["mobile"]
            if pay_mode=="half_online":
                msg=("\u2705 *Booking Confirmed!*\n"
                     "ID: *"+bid+"*\n"
                     "Service: "+svc+"\n"
                     "Total: Rs."+str(full_amount)+"\n"
                     "\u2705 Paid Online: Rs."+str(paid_now)+"\n"
                     "Balance at doorstep: Rs."+str(balance_due)+"\n"
                     "\u23f1 Technician in 60-90 min\n"
                     "Support: "+BUSINESS_PHONE)
            elif pay_mode=="cod_token":
                msg=("\u2705 *Booking Confirmed!*\n"
                     "ID: *"+bid+"*\n"
                     "Service: "+svc+"\n"
                     "Total: Rs."+str(full_amount)+"\n"
                     "\u2705 Token Paid: Rs.99\n"
                     "Cash at doorstep: Rs."+str(balance_due)+"\n"
                     "\u23f1 Technician in 60-90 min\n"
                     "Support: "+BUSINESS_PHONE)
            else:
                msg=("\u2705 *Booking Confirmed!*\n"
                     "ID: *"+bid+"*\n"
                     "Service: "+svc+"\n"
                     "\u2705 Full Paid: Rs."+str(full_amount)+"\n"
                     "\u23f1 Technician in 60-90 min\n"
                     "Support: "+BUSINESS_PHONE)
            send_whatsapp(mob,msg)
            send_whatsapp(BUSINESS_PHONE,
                "\U0001f514 Payment!\n"
                "ID:"+bid+" | "+row["name"]+" ("+mob+")\n"
                "Service: "+svc+"\n"
                "Mode:"+pay_mode+" | Paid:Rs."+str(paid_now)+" | Bal:Rs."+str(balance_due))
            push_notif("Payment:"+bid,row["name"]+" Rs."+str(paid_now)+" paid","success",bid)
            auto_assign_vendor(bid,row["city"],svc)
        log_action(bid,"PAYMENT_SUCCESS",
            "Mode:%s Paid:Rs.%d RZP:%s" % (pay_mode,paid_now,pid))
        return jsonify({
            "success":True,
            "booking_id":bid,
            "payment_mode":pay_mode,
            "paid_now":paid_now,
            "balance_due":balance_due
        })
    except Exception as e:
        print("[verify] %s" % e)
        return jsonify({"success":False,"error":str(e)}),500


@app.route("/book",methods=["POST"])
def book():
    """COD fallback route"""
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("name","")).strip()
        mobile=str(d.get("mobile","")).strip()
        city=str(d.get("city","")).strip()
        area=str(d.get("area","")).strip()
        service=str(d.get("service_type","")).strip()
        disc=int(d.get("discount",0))
        if not all([name,mobile,city,service]):
            return jsonify({"error":"Fill all required fields"}),400
        if not mobile.isdigit() or len(mobile)!=10:
            return jsonify({"error":"Valid 10-digit mobile required"}),400
        prices=get_prices()
        base=prices.get(service,0)
        final=max(base-min(disc,200),1)
        bid=generate_booking_id()
        prev=check_repeat(mobile)
        with get_db() as conn:
            conn.execute(
                "INSERT INTO bookings(booking_id,name,mobile,city,area,service_type,"
                "amount,discount,final_amount,payment_mode,payment_status,is_repeat,visit_count,remark)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (bid,name,mobile,city,area,service,base,min(disc,200),final,
                 "cod","COD_PENDING",1 if prev>0 else 0,prev+1,"COD booking")
            )
            conn.commit()
        log_action(bid,"CREATED","COD booking","customer")
        return jsonify({"booking_id":bid,"final_amount":final})
    except Exception as e:
        print("[book] %s" % e)
        return jsonify({"error":"Server error"}),500

@app.route("/payment-failed",methods=["POST"])
def payment_failed():
    try:
        d=request.get_json(force=True) or {}
        bid=str(d.get("booking_id","")); mode=str(d.get("pay_mode","failed"))
        if bid:
            st="COD_PENDING" if mode=="cash" else "FAILED"
            with get_db() as conn:
                conn.execute("UPDATE bookings SET payment_status=?,payment_mode=? WHERE booking_id=?",(st,mode,bid))
                conn.commit()
                row=conn.execute("SELECT * FROM bookings WHERE booking_id=?",(bid,)).fetchone()
            if row and mode=="cash":
                notify_booking(bid,row["name"],row["mobile"],row["service_type"],row["final_amount"],"Cash on Service")
                auto_assign_vendor(bid,row["city"],row["service_type"])
            log_action(bid,f"PAYMENT_{st}",mode)
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

# ════════════════════════════════════════
# ADMIN AUTH — 2FA
# ════════════════════════════════════════

@app.route("/admin",methods=["GET","POST"])
def admin_login():
    if session.get("admin_logged_in") and session.get("admin_2fa"): return redirect(url_for("admin_dashboard"))
    ip=get_client_ip(); ok,err=check_rate_limit(ip)
    error=None
    if request.method=="POST":
        if not ok: error=err
        else:
            pwd=request.form.get("password","")
            if pwd==ADMIN_PASSWORD:
                session["admin_logged_in"]=True
                session["admin_2fa"]=False
                session.permanent=True
                clear_attempts(ip)
                return redirect(url_for("admin_2fa"))
            else:
                record_failed_attempt(ip)
                error="❌ Wrong password"
    return render_template("admin.html",page="login",error=error,locked=not ok,lock_msg=err)

@app.route("/admin/2fa",methods=["GET","POST"])
def admin_2fa():
    if not session.get("admin_logged_in"): return redirect(url_for("admin_login"))
    if session.get("admin_2fa"): return redirect(url_for("admin_dashboard"))
    error=None
    if request.method=="POST":
        pin=request.form.get("pin","")
        if pin==ADMIN_PIN:
            session["admin_2fa"]=True
            with get_db() as conn:
                conn.execute("UPDATE admins SET last_login=? WHERE username='admin'",(datetime.now().isoformat(),))
                conn.commit()
            return redirect(url_for("admin_dashboard"))
        else: error="❌ Wrong PIN"
    return render_template("admin.html",page="2fa",error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

# ════════════════════════════════════════
# ADMIN DASHBOARD
# ════════════════════════════════════════

@app.route("/admin/dashboard")
@admin_required
def admin_dashboard():
    with get_db() as conn:
        bookings=conn.execute("""SELECT b.*,v.name as vendor_name,v.mobile as vendor_mobile
            FROM bookings b LEFT JOIN vendors v ON b.vendor_id=v.id
            ORDER BY b.created_at DESC""").fetchall()
        stats={}
        stats["total"]=conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        stats["today"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE DATE(created_at)=DATE('now')").fetchone()[0]
        stats["paid"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE payment_status='PAID'").fetchone()[0]
        stats["cod"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE payment_status='COD_PENDING'").fetchone()[0]
        stats["pending"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE status='BOOKED'").fetchone()[0]
        stats["completed"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE status='COMPLETED'").fetchone()[0]
        stats["cancelled"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE status='CANCELLED'").fetchone()[0]
        stats["scheduled"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE status='SCHEDULED'").fetchone()[0]
        stats["revenue"]=conn.execute("SELECT COALESCE(SUM(final_amount),0) FROM bookings WHERE payment_status='PAID'").fetchone()[0]
        stats["cod_rev"]=conn.execute("SELECT COALESCE(SUM(final_amount),0) FROM bookings WHERE payment_status='COD_PENDING'").fetchone()[0]
        stats["month_rev"]=conn.execute("SELECT COALESCE(SUM(final_amount),0) FROM bookings WHERE payment_status='PAID' AND strftime('%Y-%m',created_at)=strftime('%Y-%m','now')").fetchone()[0]
        stats["repeat"]=conn.execute("SELECT COUNT(*) FROM bookings WHERE is_repeat=1").fetchone()[0]
        vendors=conn.execute("SELECT * FROM vendors ORDER BY status,city,name").fetchall()
        service_prices=conn.execute("SELECT * FROM service_prices ORDER BY service_name").fetchall()
        services_catalog=conn.execute("SELECT * FROM services_catalog ORDER BY category,name").fetchall()
        inventory=conn.execute("SELECT * FROM inventory ORDER BY category,item_name").fetchall()
        low_stock=[i for i in inventory if i["current_stock"]<=i["min_stock"]]
        unread_notifs=conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]
        notifs=conn.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 20").fetchall()
        # Charts
        daily_rev=conn.execute("""SELECT DATE(created_at) as day,COALESCE(SUM(final_amount),0) as total
            FROM bookings WHERE payment_status='PAID' AND created_at>=DATE('now','-6 days')
            GROUP BY DATE(created_at) ORDER BY day""").fetchall()
        svc_rev=conn.execute("""SELECT service_type,COUNT(*) as cnt,COALESCE(SUM(final_amount),0) as total
            FROM bookings WHERE payment_status='PAID' GROUP BY service_type ORDER BY total DESC LIMIT 8""").fetchall()
        city_data=conn.execute("""SELECT city,COUNT(*) as cnt FROM bookings GROUP BY city ORDER BY cnt DESC LIMIT 10""").fetchall()
        monthly_rev=conn.execute("""SELECT strftime('%Y-%m',created_at) as month,COALESCE(SUM(final_amount),0) as total
            FROM bookings WHERE payment_status='PAID' GROUP BY month ORDER BY month DESC LIMIT 6""").fetchall()
        # Target
        month_key=datetime.now().strftime("%Y-%m")
        target_row=conn.execute("SELECT target FROM revenue_targets WHERE month=?",(month_key,)).fetchone()
        month_target=target_row["target"] if target_row else MONTHLY_TARGET
        # Vendor payments
        vnd_payments=conn.execute("""SELECT vp.*,v.name as vendor_name
            FROM vendor_payments vp LEFT JOIN vendors v ON vp.vendor_id=v.id
            ORDER BY vp.created_at DESC LIMIT 50""").fetchall()
        # Calendar — bookings with scheduled dates
        scheduled_bk=conn.execute("""SELECT b.*,v.name as vendor_name FROM bookings b
            LEFT JOIN vendors v ON b.vendor_id=v.id
            WHERE b.scheduled_at!='' AND b.status NOT IN('COMPLETED','CANCELLED')
            ORDER BY b.scheduled_at""").fetchall()

    return render_template("admin.html",page="dashboard",
        bookings=bookings, stats=stats, vendors=vendors,
        service_prices=service_prices, services_catalog=services_catalog,
        inventory=inventory, low_stock=low_stock,
        unread_notifs=unread_notifs, notifs=notifs,
        vnd_payments=vnd_payments, scheduled_bk=scheduled_bk,
        month_target=month_target, cities=CITIES,
        daily_rev=json.dumps([{"day":r["day"],"total":r["total"]} for r in daily_rev]),
        svc_rev=json.dumps([{"name":r["service_type"],"cnt":r["cnt"],"total":r["total"]} for r in svc_rev]),
        city_data=json.dumps([{"city":r["city"],"cnt":r["cnt"]} for r in city_data]),
        monthly_rev=json.dumps([{"month":r["month"],"total":r["total"]} for r in monthly_rev]))

# ════════════════════════════════════════
# BOOKINGS CRUD
# ════════════════════════════════════════

@app.route("/admin/booking/add",methods=["POST"])
@admin_required
def admin_add_booking():
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("name","")).strip(); mobile=str(d.get("mobile","")).strip()
        city=str(d.get("city","")).strip(); area=str(d.get("area","")).strip()
        pincode=str(d.get("pincode","")).strip(); service=str(d.get("service_type","")).strip()
        remark=str(d.get("remark","")).strip(); pay_mode=str(d.get("payment_mode","cash")).strip()
        discount=int(d.get("discount",0)); scheduled=str(d.get("scheduled_at","")).strip()
        if not all([name,mobile,city,service]): return jsonify({"error":"Fill all required fields"}),400
        if not mobile.isdigit() or len(mobile)!=10: return jsonify({"error":"Valid 10-digit mobile"}),400
        prices=get_prices()
        if service not in prices: return jsonify({"error":"Invalid service"}),400
        base=prices[service]; disc=min(discount,base-1); final=max(base-disc,1)
        bid=generate_booking_id()
        pay_st="COD_PENDING" if pay_mode=="cash" else "PAID" if pay_mode=="paid" else "PENDING"
        status="SCHEDULED" if scheduled else "BOOKED"
        prev=check_repeat(mobile); is_repeat=1 if prev>0 else 0; visit_count=prev+1
        with get_db() as conn:
            conn.execute("""INSERT INTO bookings(booking_id,name,mobile,city,area,pincode,
                service_type,amount,discount,final_amount,payment_mode,payment_status,
                remark,scheduled_at,status,is_repeat,visit_count)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (bid,name,mobile,city,area,pincode,service,base,disc,final,
                 pay_mode,pay_st,remark,scheduled,status,is_repeat,visit_count))
            conn.commit()
        notify_booking(bid,name,mobile,service,final,pay_mode)
        auto_assign_vendor(bid,city,service)
        log_action(bid,"CREATED_ADMIN",f"Manual. Remark:{remark}")
        return jsonify({"success":True,"booking_id":bid,"is_repeat":is_repeat,"visit_count":visit_count})
    except Exception as e: print(f"[admin-add] {e}"); return jsonify({"error":str(e)}),500

@app.route("/admin/booking/update-status",methods=["POST"])
@admin_required
def update_booking_status():
    try:
        d=request.get_json(force=True) or {}
        bid=str(d.get("booking_id","")); status=str(d.get("status",""))
        remark=str(d.get("remark","")); sched=str(d.get("scheduled_at",""))
        if status not in ["BOOKED","COMPLETED","CANCELLED","SCHEDULED","PENDING"]:
            return jsonify({"error":"Invalid status"}),400
        with get_db() as conn:
            conn.execute("UPDATE bookings SET status=?,remark=?,scheduled_at=?,updated_at=CURRENT_TIMESTAMP WHERE booking_id=?",(status,remark,sched,bid))
            if status=="COMPLETED":
                row=conn.execute("SELECT vendor_id FROM bookings WHERE booking_id=?",(bid,)).fetchone()
                if row and row["vendor_id"]:
                    conn.execute("UPDATE vendors SET availability='FREE',total_jobs=total_jobs+1 WHERE id=?",(row["vendor_id"],))
            conn.commit()
        log_action(bid,f"STATUS_{status}",remark)
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/booking/delete/<bid>",methods=["POST"])
@admin_required
def delete_booking(bid):
    try:
        with get_db() as conn:
            row=conn.execute("SELECT vendor_id FROM bookings WHERE booking_id=?",(bid,)).fetchone()
            if row and row["vendor_id"]:
                conn.execute("UPDATE vendors SET availability='FREE' WHERE id=?",(row["vendor_id"],))
            conn.execute("DELETE FROM bookings WHERE booking_id=?",(bid,))
            conn.execute("DELETE FROM booking_logs WHERE booking_id=?",(bid,))
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/booking/assign-vendor",methods=["POST"])
@admin_required
def assign_vendor():
    try:
        d=request.get_json(force=True) or {}
        bid=str(d.get("booking_id","")); vid=d.get("vendor_id")
        with get_db() as conn:
            old=conn.execute("SELECT vendor_id FROM bookings WHERE booking_id=?",(bid,)).fetchone()
            if old and old["vendor_id"]:
                conn.execute("UPDATE vendors SET availability='FREE' WHERE id=?",(old["vendor_id"],))
            conn.execute("UPDATE bookings SET vendor_id=?,updated_at=CURRENT_TIMESTAMP WHERE booking_id=?",(vid,bid))
            vname="Unassigned"
            if vid:
                v=conn.execute("SELECT name,mobile FROM vendors WHERE id=?",(vid,)).fetchone()
                if v:
                    conn.execute("UPDATE vendors SET availability='BUSY' WHERE id=?",(vid,))
                    vname=v["name"]
                    row=conn.execute("SELECT * FROM bookings WHERE booking_id=?",(bid,)).fetchone()
                    if row: notify_vendor(v["mobile"],bid,row["name"],row["city"],row["service_type"],row["scheduled_at"])
            conn.commit()
        log_action(bid,"VENDOR_ASSIGNED",f"→ {vname}")
        return jsonify({"success":True,"vendor_name":vname})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/booking/logs/<bid>")
@admin_required
def booking_logs(bid):
    try:
        with get_db() as conn:
            logs=conn.execute("SELECT * FROM booking_logs WHERE booking_id=? ORDER BY created_at DESC",(bid,)).fetchall()
            booking=conn.execute("SELECT b.*,v.name as vendor_name FROM bookings b LEFT JOIN vendors v ON b.vendor_id=v.id WHERE b.booking_id=?",(bid,)).fetchone()
        return jsonify({"logs":[{"action":l["action"],"note":l["note"],"by":l["by_user"],"at":l["created_at"]} for l in logs],
                        "booking":dict(booking) if booking else {}})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/update-payment/<bid>",methods=["POST"])
@admin_required
def update_payment(bid):
    st=(request.get_json(force=True) or {}).get("status","PAID")
    with get_db() as conn:
        conn.execute("UPDATE bookings SET payment_status=?,updated_at=CURRENT_TIMESTAMP WHERE booking_id=?",(st,bid))
        conn.commit()
    log_action(bid,f"PAYMENT_{st}",f"Updated to {st}")
    return jsonify({"success":True})

@app.route("/admin/booking/edit",methods=["POST"])
@admin_required
def edit_booking():
    try:
        d=request.get_json(force=True) or {}
        bid=str(d.get("booking_id","")).strip()
        if not bid: return jsonify({"error":"Booking ID required"}),400
        name=str(d.get("name","")).strip()
        mobile=str(d.get("mobile","")).strip()
        city=str(d.get("city","")).strip()
        area=str(d.get("area","")).strip()
        pincode=str(d.get("pincode","")).strip()
        service=str(d.get("service_type","")).strip()
        amount=int(d.get("amount",0))
        discount=int(d.get("discount",0))
        final_amount=int(d.get("final_amount",max(amount-discount,0)))
        paymode=str(d.get("payment_mode","cash"))
        paystatus=str(d.get("payment_status","PENDING"))
        status=str(d.get("status","BOOKED"))
        sched=str(d.get("scheduled_at",""))
        addons=str(d.get("addons","")).strip()
        remark=str(d.get("remark","")).strip()
        if addons and remark:
            remark=f"[Add-ons: {addons}] {remark}"
        elif addons:
            remark=f"[Add-ons: {addons}]"
        with get_db() as conn:
            conn.execute("""UPDATE bookings SET name=?,mobile=?,city=?,area=?,pincode=?,
                service_type=?,amount=?,discount=?,final_amount=?,payment_mode=?,
                payment_status=?,status=?,scheduled_at=?,remark=?,updated_at=CURRENT_TIMESTAMP
                WHERE booking_id=?""",
                (name,mobile,city,area,pincode,service,amount,discount,final_amount,
                 paymode,paystatus,status,sched,remark,bid))
            conn.commit()
        log_action(bid,"EDIT",f"Booking edited by admin. Status:{status} Pay:{paystatus} Remark:{remark[:50]}")
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/vendors/bulk-import",methods=["POST"])
@admin_required
def bulk_import_vendors():
    try:
        d=request.get_json(force=True) or {}
        vendors=d.get("vendors",[])
        if not vendors: return jsonify({"error":"No vendor data provided"}),400
        imported=0; skipped=0
        with get_db() as conn:
            for v in vendors:
                name=str(v.get("name","")).strip()
                mobile=str(v.get("mobile","")).strip()
                city=str(v.get("city","Lucknow")).strip()
                if not name or not mobile or not mobile.isdigit() or len(mobile)!=10:
                    skipped+=1; continue
                existing=conn.execute("SELECT id FROM vendors WHERE mobile=?",(mobile,)).fetchone()
                if existing: skipped+=1; continue
                vcode=generate_vendor_id()
                conn.execute("""INSERT INTO vendors(vendor_code,name,mobile,city,skills,aadhar_number,
                    address,upi_id,bank_account,bank_ifsc,commission_pct,joined_date)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (vcode,name,mobile,city,
                     str(v.get("skills","AC Service")),
                     str(v.get("aadhar_number","")),
                     str(v.get("address","")),
                     str(v.get("upi_id","")),
                     str(v.get("bank_account","")),
                     str(v.get("bank_ifsc","")),
                     float(v.get("commission_pct",70)),
                     datetime.now().strftime("%Y-%m-%d")))
                imported+=1
            conn.commit()
        return jsonify({"success":True,"imported":imported,"skipped":skipped})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/whatsapp-blast",methods=["POST"])
@admin_required
def whatsapp_blast():
    try:
        d=request.get_json(force=True) or {}
        msg=str(d.get("message","")).strip()
        target=str(d.get("target","BOOKED"))
        if not msg: return jsonify({"error":"Message required"}),400
        with get_db() as conn:
            if target=="ALL":
                rows=conn.execute("SELECT DISTINCT mobile,name FROM bookings ORDER BY created_at DESC").fetchall()
            else:
                rows=conn.execute("SELECT DISTINCT mobile,name FROM bookings WHERE payment_status=? OR status=?",(target,target)).fetchall()
        sent=0; failed=0
        for r in rows[:50]:  # cap at 50
            personalized=msg.replace("{name}",r["name"])
            if send_whatsapp(r["mobile"],personalized): sent+=1
            else: failed+=1
            time.sleep(0.5)
        return jsonify({"success":True,"sent":sent,"failed":failed})
    except Exception as e: return jsonify({"error":str(e)}),500

# ════════════════════════════════════════
# VENDORS
# ════════════════════════════════════════

@app.route("/admin/vendor/add",methods=["POST"])
@admin_required
def add_vendor():
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("name","")).strip(); mobile=str(d.get("mobile","")).strip()
        city=str(d.get("city","")).strip(); skills=str(d.get("skills","")).strip()
        aadhar=str(d.get("aadhar","")).strip(); address=str(d.get("address","")).strip()
        upi=str(d.get("upi_id","")).strip(); bank=str(d.get("bank_account","")).strip()
        ifsc=str(d.get("bank_ifsc","")).strip(); commission=float(d.get("commission_pct",70))
        if not all([name,mobile,city]): return jsonify({"error":"Name, mobile, city required"}),400
        if not mobile.isdigit() or len(mobile)!=10: return jsonify({"error":"Valid 10-digit mobile"}),400
        vcode=generate_vendor_id()
        with get_db() as conn:
            cur=conn.execute("""INSERT INTO vendors(vendor_code,name,mobile,city,skills,aadhar_number,
                address,upi_id,bank_account,bank_ifsc,commission_pct,joined_date)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (vcode,name,mobile,city,skills,aadhar,address,upi,bank,ifsc,commission,datetime.now().strftime("%Y-%m-%d")))
            conn.commit(); vid=cur.lastrowid
        # Notify vendor
        send_whatsapp(mobile,f"🎉 Welcome to *Classic Multiple Services*!\nYour Vendor ID: *{vcode}*\nCity: {city}\nReport at: {BUSINESS_PHONE}\nProfile activated ✅")
        return jsonify({"success":True,"vendor_id":vid,"vendor_code":vcode,"name":name})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/vendor/update",methods=["POST"])
@admin_required
def update_vendor():
    try:
        d=request.get_json(force=True) or {}
        vid=int(d.get("id",0))
        with get_db() as conn:
            conn.execute("""UPDATE vendors SET name=?,mobile=?,city=?,skills=?,status=?,
                availability=?,aadhar_number=?,address=?,upi_id=?,bank_account=?,bank_ifsc=?,commission_pct=? WHERE id=?""",
                (d.get("name"),d.get("mobile"),d.get("city"),d.get("skills",""),
                 d.get("status","ACTIVE"),d.get("availability","FREE"),
                 d.get("aadhar",""),d.get("address",""),d.get("upi_id",""),
                 d.get("bank_account",""),d.get("bank_ifsc",""),
                 float(d.get("commission_pct",70)),vid))
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/vendor/delete/<int:vid>",methods=["POST"])
@admin_required
def delete_vendor(vid):
    with get_db() as conn:
        conn.execute("UPDATE vendors SET status='INACTIVE',availability='FREE' WHERE id=?",(vid,))
        conn.commit()
    return jsonify({"success":True})

@app.route("/admin/vendor/payment",methods=["POST"])
@admin_required
def record_vendor_payment():
    try:
        d=request.get_json(force=True) or {}
        vid=int(d.get("vendor_id",0)); amount=float(d.get("amount",0))
        ptype=str(d.get("payment_type","COMMISSION")); pmode=str(d.get("payment_mode","UPI"))
        note=str(d.get("note","")); bid=str(d.get("booking_id",""))
        if not vid or not amount: return jsonify({"error":"Vendor and amount required"}),400
        with get_db() as conn:
            conn.execute("""INSERT INTO vendor_payments(vendor_id,booking_id,amount,payment_type,payment_mode,status,note,paid_at)
                VALUES(?,?,?,?,?,?,?,?)""",(vid,bid,amount,ptype,pmode,"PAID",note,datetime.now().isoformat()))
            conn.execute("UPDATE vendors SET total_earnings=total_earnings+? WHERE id=?",(amount,vid))
            conn.commit()
            v=conn.execute("SELECT mobile,name FROM vendors WHERE id=?",(vid,)).fetchone()
        if v: send_whatsapp(v["mobile"],f"💰 Payment of ₹{amount} credited!\nMode: {pmode}\nNote: {note or 'Commission'}\nClassic Multiple Services")
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

# ════════════════════════════════════════
# INVENTORY
# ════════════════════════════════════════

@app.route("/admin/inventory/update",methods=["POST"])
@admin_required
def update_inventory():
    try:
        d=request.get_json(force=True) or {}
        iid=int(d.get("id",0)); change=float(d.get("change",0))
        ctype=str(d.get("type","ADD")); note=str(d.get("note",""))
        with get_db() as conn:
            if ctype=="ADD": conn.execute("UPDATE inventory SET current_stock=current_stock+?,last_updated=CURRENT_TIMESTAMP WHERE id=?",(change,iid))
            else: conn.execute("UPDATE inventory SET current_stock=MAX(0,current_stock-?),last_updated=CURRENT_TIMESTAMP WHERE id=?",(change,iid))
            conn.execute("INSERT INTO inventory_logs(item_id,change_qty,change_type,note,by_user) VALUES(?,?,?,?,?)",(iid,change,ctype,note,"admin"))
            # Check low stock
            item=conn.execute("SELECT * FROM inventory WHERE id=?",(iid,)).fetchone()
            if item and item["current_stock"]<=item["min_stock"]:
                push_notif(f"⚠ Low Stock: {item['item_name']}",f"Only {item['current_stock']} {item['unit']} left (min:{item['min_stock']})","warning")
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/inventory/add",methods=["POST"])
@admin_required
def add_inventory_item():
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("item_name","")).strip(); cat=str(d.get("category","General")).strip()
        stock=float(d.get("stock",0)); unit=str(d.get("unit","pcs")); minst=float(d.get("min_stock",5))
        cost=float(d.get("cost",0))
        if not name: return jsonify({"error":"Item name required"}),400
        with get_db() as conn:
            conn.execute("INSERT INTO inventory(item_name,category,current_stock,unit,min_stock,cost_per_unit) VALUES(?,?,?,?,?,?)",(name,cat,stock,unit,minst,cost))
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

# ════════════════════════════════════════
# PRICING & SERVICES
# ════════════════════════════════════════

@app.route("/admin/prices/update",methods=["POST"])
@admin_required
def update_prices():
    try:
        d=request.get_json(force=True) or {}
        with get_db() as conn:
            for item in d.get("prices",[]):
                svc=str(item.get("service","")).strip(); price=int(item.get("price",0))
                if svc and price>0:
                    conn.execute("INSERT INTO service_prices(service_name,price) VALUES(?,?) ON CONFLICT(service_name) DO UPDATE SET price=?,updated_at=CURRENT_TIMESTAMP",(svc,price,price))
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/service/add",methods=["POST"])
@admin_required
def add_service():
    try:
        d=request.get_json(force=True) or {}
        name=str(d.get("name","")).strip(); cat=str(d.get("category","")).strip()
        price=int(d.get("price",0)); desc=str(d.get("description","")).strip()
        if not all([name,cat,price]): return jsonify({"error":"Name, category, price required"}),400
        with get_db() as conn:
            conn.execute("INSERT INTO services_catalog(name,category,base_price,description) VALUES(?,?,?,?)",(name,cat,price,desc))
            conn.execute("INSERT INTO service_prices(service_name,price) VALUES(?,?) ON CONFLICT(service_name) DO UPDATE SET price=?",(name,price,price))
            conn.commit()
        return jsonify({"success":True})
    except sqlite3.IntegrityError: return jsonify({"error":"Service already exists"}),400
    except Exception as e: return jsonify({"error":str(e)}),500

@app.route("/admin/service/toggle/<int:sid>",methods=["POST"])
@admin_required
def toggle_service(sid):
    with get_db() as conn:
        conn.execute("UPDATE services_catalog SET is_active=1-is_active WHERE id=?",(sid,))
        conn.commit()
    return jsonify({"success":True})

# ════════════════════════════════════════
# REVENUE TARGET
# ════════════════════════════════════════

@app.route("/admin/target/set",methods=["POST"])
@admin_required
def set_target():
    try:
        d=request.get_json(force=True) or {}
        month=str(d.get("month",datetime.now().strftime("%Y-%m")))
        target=int(d.get("target",50000))
        with get_db() as conn:
            conn.execute("INSERT INTO revenue_targets(month,target) VALUES(?,?) ON CONFLICT(month) DO UPDATE SET target=?",(month,target,target))
            conn.commit()
        return jsonify({"success":True})
    except Exception as e: return jsonify({"error":str(e)}),500

# ════════════════════════════════════════
# NOTIFICATIONS
# ════════════════════════════════════════

@app.route("/admin/notifications/read",methods=["POST"])
@admin_required
def mark_notifs_read():
    with get_db() as conn:
        conn.execute("UPDATE notifications SET is_read=1")
        conn.commit()
    return jsonify({"success":True})

@app.route("/admin/notifications/list")
@admin_required
def list_notifs():
    with get_db() as conn:
        rows=conn.execute("SELECT * FROM notifications ORDER BY created_at DESC LIMIT 30").fetchall()
        unread=conn.execute("SELECT COUNT(*) FROM notifications WHERE is_read=0").fetchone()[0]
    return jsonify({"notifications":[dict(r) for r in rows],"unread":unread})

# ════════════════════════════════════════
# DAILY REPORT
# ════════════════════════════════════════

def send_daily_report():
    try:
        with get_db() as conn:
            today=conn.execute("SELECT COUNT(*) FROM bookings WHERE DATE(created_at)=DATE('now')").fetchone()[0]
            rev=conn.execute("SELECT COALESCE(SUM(final_amount),0) FROM bookings WHERE payment_status='PAID' AND DATE(created_at)=DATE('now')").fetchone()[0]
            pending=conn.execute("SELECT COUNT(*) FROM bookings WHERE status='BOOKED'").fetchone()[0]
            vendors=conn.execute("SELECT COUNT(*) FROM vendors WHERE status='ACTIVE'").fetchone()[0]
        msg=(f"📊 *CMS Daily Report — {datetime.now().strftime('%d %b %Y')}*\n\n"
             f"📋 Today's Bookings: {today}\n"
             f"💰 Today's Revenue: ₹{rev}\n"
             f"⏳ Pending Jobs: {pending}\n"
             f"👷 Active Technicians: {vendors}\n\n"
             f"CMS Admin: http://your-domain/admin")
        send_whatsapp(BUSINESS_PHONE,msg)
        html=(f"<h2>CMS Daily Report — {datetime.now().strftime('%d %b %Y')}</h2>"
              f"<p>Today's Bookings: <b>{today}</b></p>"
              f"<p>Today's Revenue: <b>₹{rev}</b></p>"
              f"<p>Pending Jobs: <b>{pending}</b></p>"
              f"<p>Active Technicians: <b>{vendors}</b></p>")
        if OWNER_EMAIL: send_email(OWNER_EMAIL,"CMS Daily Report",html)
    except Exception as e: print(f"[REPORT] {e}")

@app.route("/admin/send-daily-report",methods=["POST"])
@admin_required
def trigger_daily_report():
    threading.Thread(target=send_daily_report).start()
    return jsonify({"success":True,"message":"Daily report sent!"})

# ════════════════════════════════════════
# EXPORT
# ════════════════════════════════════════

@app.route("/admin/export-csv")
@admin_required
def export_csv():
    with get_db() as conn:
        rows=conn.execute("""SELECT b.*,v.name as vendor_name,v.vendor_code
            FROM bookings b LEFT JOIN vendors v ON b.vendor_id=v.id
            ORDER BY b.created_at DESC""").fetchall()
    out=io.StringIO(); w=csv.writer(out)
    w.writerow(["Booking ID","Name","Mobile","City","Area","Pincode","Service",
                "Amount","Discount","Final","Pay Mode","Payment Status","Status",
                "Remark","Vendor","Vendor ID","Scheduled","Repeat","Visit#","Created"])
    for r in rows:
        w.writerow([r["booking_id"],r["name"],r["mobile"],r["city"],r["area"],r["pincode"],
                    r["service_type"],r["amount"],r["discount"],r["final_amount"],
                    r["payment_mode"],r["payment_status"],r["status"],r["remark"],
                    r["vendor_name"] or "",r["vendor_code"] or "",r["scheduled_at"],
                    "YES" if r["is_repeat"] else "NO",r["visit_count"],r["created_at"]])
    out.seek(0)
    return Response(out.getvalue(),mimetype="text/csv",
                    headers={"Content-Disposition":"attachment; filename=cms_bookings.csv"})

@app.route("/api/prices")
def api_prices():
    return jsonify(get_prices())

@app.route("/health")
def health():
    return jsonify({"status":"ok","version":"4.0"})

if __name__=="__main__":
    print("\n"+"="*55)
    print("  CLASSIC MULTIPLE SERVICES  v4.0")
    print(f"  Website : http://0.0.0.0:5000  (or http://YOUR_IP:5000)")
    print(f"  Admin   : http://127.0.0.1:5000/admin")
    print(f"  Password: {ADMIN_PASSWORD}")
    print(f"  2FA PIN : {ADMIN_PIN}")
    print("="*55+"\n")
    app.run(debug=True,host="0.0.0.0",port=5000,use_reloader=False)