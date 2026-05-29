# -*- coding: utf-8 -*-
import telebot
import subprocess
import os
import zipfile
import tempfile
import shutil
from telebot import types
import time
from datetime import datetime, timedelta
import psutil
import sqlite3
import json
import logging
import signal
import threading
import re
import sys
import atexit
import requests
import hashlib
import mimetypes
import struct

# --- Flask Keep Alive ---
from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "Bot is running securely!"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run_flask)
    t.daemon = True
    t.start()

# ==================== إعدادات الحماية ====================
MAX_FILES_PER_USER = 20           
MAX_RUNNING_SCRIPTS_PER_USER = 5  
MAX_SCRIPT_RUNTIME_HOURS = 24     
CPU_LIMIT_PERCENT = 50            
MEMORY_LIMIT_MB = 256             
RATE_LIMIT_PER_MINUTE = 10        
MAX_LOG_SIZE_MB = 5               
# =========================================================

# --- Configuration ---
TOKEN = '8698961705:AAEADEVwDORAgV5OIhoOYWEiTOx1PvXGiN8'
OWNER_ID = 7952746203
ADMIN_ID = 7939265907
YOUR_USERNAME = '@TVXSSS'
UPDATE_CHANNEL = 'https://t.me/sadeiq'

# ==================== إعدادات النظام ====================
FREE_TRIAL_DAYS = 1              
DAILY_BONUS_HOURS = 10           
REFERRAL_POINTS = 5              
REFERRAL_BONUS_NEW = 3           
REQUIRED_POINTS_PER_UPLOAD = 1   
# ========================================================

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
UPLOAD_BOTS_DIR = os.path.join(BASE_DIR, 'upload_bots')
IROTECH_DIR = os.path.join(BASE_DIR, 'inf')
DATABASE_PATH = os.path.join(IROTECH_DIR, 'bot_data.db')

os.makedirs(UPLOAD_BOTS_DIR, exist_ok=True)
os.makedirs(IROTECH_DIR, exist_ok=True)

bot = telebot.TeleBot(TOKEN)

# --- Data structures ---
bot_scripts = {}
user_subscriptions = {}
user_daily_bonus = {}
user_points = {}
user_files = {}
active_users = set()
# المالك مضاف تلقائياً للمشرفين
admin_ids = {OWNER_ID, ADMIN_ID}
bot_locked = False
malicious_files = {}
user_command_timestamps = {}

# --- Logging ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Command Button Layouts ---
COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel", "📤 Upload File"],
    ["📂 Check Files", "⚡ Bot Speed"],
    ["💰 Points", "🎁 Daily Bonus"],
    ["👥 Referral", "📅 Subscription"],
    ["📊 Statistics", "📞 Contact Owner"]
]

ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC = [
    ["📢 Updates Channel", "📤 Upload File"],
    ["📂 Check Files", "⚡ Bot Speed"],
    ["💰 Points", "🎁 Daily Bonus"],
    ["👥 Referral", "📅 Subscription"],
    ["👥 Users List", "💳 Subscriptions"],
    ["📢 Broadcast", "🔒 Lock Bot"],
    ["🚫 Blocked Files", "🔓 Unblock User"],
    ["👑 Admin Panel", "📊 Statistics"],
    ["📞 Contact Owner"]
]

# ==================== أنماط الكشف عن الملفات الضارة (بدون فحص التوكن) ====================

MALICIOUS_PATTERNS = [
    r'while\s+True\s*:\s*os\.fork', r'for\s+_\s+in\s+range\(\d{5,}\)',
    r'threading\.Thread.*while\s+True', r'os\.fork\(\)',
    r'os\.system', r'subprocess\.', r'eval\(', r'exec\(', r'__import__\(',
    r'rm -rf', r'del ', r'os\.remove', r'shutil\.rmtree', r'os\.unlink',
    r'os\.rmdir', r'pathlib.*\.unlink', r'glob\.glob.*os\.remove',
    r'requests\.post.*telegram', r'sqlmap', r'stealer', r'grabber',
    r'subprocess\.call.*\|\|', r'eval\(input\(', r'__import__\([\'"]os[\'"]\)\.system',
    r'base64\.b64decode.*eval', r'exec\(.*base64',
    r'open\([\'"].*[\'"],\s*[\'"]w[\'"]\).*write', r'shutil\.copy.*\.py',
    r'shutil\.move.*\.py', r'pathlib.*\.write_text',
    r'socket\.socket.*\.connect', r'requests\.get\([\'"]https?://.*\.onion',
    r'urllib\.request\.urlopen',
    r'cryptography\.fernet', r'pyaes', r'Crypto\.Cipher', r'encrypt\(',
    r'decrypt\(', r'ransomware', r'locker',
    r'signal\.signal\(signal\.SIGTERM', r'atexit\.register',
    r'malware', r'trojan', r'virus', r'backdoor', r'keylogger',
    r'rootkit', r'exploit', r'payload', r'botnet', r'rat',
    # تم إزالة أنماط فحص التوكن تماماً
]

MALICIOUS_EXTENSIONS = ['.exe', '.dll', '.bat', '.cmd', '.scr', '.com', '.vbs', '.ps1', '.sh', '.bin']

# ==================== دوال التحقق من الصلاحيات ====================

def is_owner_or_admin(user_id):
    """التحقق مما إذا كان المستخدم مالكاً أو مشرفاً"""
    return user_id == OWNER_ID or user_id in admin_ids

def check_rate_limit(user_id):
    """التحقق من معدل الأوامر - المالك معفي"""
    if user_id == OWNER_ID:
        return True
    now = time.time()
    if user_id not in user_command_timestamps:
        user_command_timestamps[user_id] = []
    user_command_timestamps[user_id] = [t for t in user_command_timestamps[user_id] if now - t < 60]
    if len(user_command_timestamps[user_id]) >= RATE_LIMIT_PER_MINUTE:
        return False
    user_command_timestamps[user_id].append(now)
    return True

def get_user_running_scripts_count(user_id):
    count = 0
    for key in bot_scripts:
        if key.startswith(f"{user_id}_"):
            count += 1
    return count

def check_script_limits(user_id):
    """التحقق من حدود التشغيل - المالك غير محدود"""
    if user_id == OWNER_ID:
        return True, "المالك غير محدود"
    running_count = get_user_running_scripts_count(user_id)
    if running_count >= MAX_RUNNING_SCRIPTS_PER_USER:
        return False, f"وصلت للحد الأقصى ({MAX_RUNNING_SCRIPTS_PER_USER}) بوتات متزامنة"
    files_count = len(user_files.get(user_id, []))
    if files_count >= MAX_FILES_PER_USER:
        return False, f"وصلت للحد الأقصى ({MAX_FILES_PER_USER}) ملفات"
    return True, "OK"

def monitor_script_resources(script_key, process_pid, owner_id):
    def monitor():
        try:
            process = psutil.Process(process_pid)
            start_time = time.time()
            while script_key in bot_scripts:
                if owner_id != OWNER_ID and time.time() - start_time > MAX_SCRIPT_RUNTIME_HOURS * 3600:
                    logger.warning(f"Script {script_key} exceeded max runtime, stopping...")
                    stop_script(script_key)
                    break
                try:
                    cpu_percent = process.cpu_percent(interval=1)
                    memory_mb = process.memory_info().rss / 1024 / 1024
                    if cpu_percent > CPU_LIMIT_PERCENT:
                        logger.warning(f"Script {script_key} exceeded CPU limit")
                        stop_script(script_key)
                        break
                    if memory_mb > MEMORY_LIMIT_MB:
                        logger.warning(f"Script {script_key} exceeded memory limit")
                        stop_script(script_key)
                        break
                except:
                    pass
                time.sleep(30)
        except:
            pass
    threading.Thread(target=monitor, daemon=True).start()

def is_malicious_file(content, filename, user_id):
    """التحقق من الملفات الضارة - المالك معفي تماماً"""
    # المالك معفي من الفحص مهما كان الملف
    if user_id == OWNER_ID:
        return False, "المالك معفي من الفحص"
    
    # فحص الامتداد
    ext = os.path.splitext(filename)[1].lower()
    if ext in MALICIOUS_EXTENSIONS:
        return True, f"امتداد ضار: {ext}"
    
    # فحص المحتوى للمستخدمين العاديين فقط
    try:
        content_str = content.decode('utf-8', errors='ignore').lower()
        for pattern in MALICIOUS_PATTERNS:
            if re.search(pattern, content_str, re.IGNORECASE):
                return True, f"نشاط مشبوه: {pattern[:50]}"
    except:
        pass
    
    return False, "آمن"

def log_malicious_file(user_id, filename, reason):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS malicious_files
                     (user_id INTEGER, filename TEXT, reason TEXT, date TEXT)''')
        c.execute('INSERT INTO malicious_files VALUES (?, ?, ?, ?)',
                  (user_id, filename, reason, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        if user_id != OWNER_ID:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('''CREATE TABLE IF NOT EXISTS blocked_users
                         (user_id INTEGER PRIMARY KEY, block_until TEXT)''')
            block_until = datetime.now() + timedelta(hours=24)
            c.execute('INSERT OR REPLACE INTO blocked_users VALUES (?, ?)',
                      (user_id, block_until.isoformat()))
            conn.commit()
            conn.close()
        return True
    except Exception as e:
        logger.error(f"Error logging malicious file: {e}")
        return False

def is_user_blocked(user_id):
    if user_id == OWNER_ID:
        return False, None
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT block_until FROM blocked_users WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            block_until = datetime.fromisoformat(result[0])
            if block_until > datetime.now():
                hours_left = int((block_until - datetime.now()).total_seconds() / 3600)
                return True, f"محظور لمدة {hours_left} ساعة"
            else:
                conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
                c = conn.cursor()
                c.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
                conn.commit()
                conn.close()
                return False, None
        return False, None
    except Exception as e:
        return False, None

def unblock_user(user_id, admin_id):
    if admin_id != OWNER_ID and admin_id not in admin_ids:
        return False, "ليس لديك صلاحية"
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM blocked_users WHERE user_id = ?', (user_id,))
        deleted = c.rowcount > 0
        conn.commit()
        conn.close()
        if deleted:
            return True, f"✅ تم رفع الحظر عن المستخدم `{user_id}`"
        else:
            return False, f"❌ المستخدم `{user_id}` ليس محظوراً"
    except Exception as e:
        return False, f"❌ خطأ: {e}"

def get_malicious_files_list():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, filename, reason, date FROM malicious_files ORDER BY date DESC LIMIT 50')
        results = c.fetchall()
        conn.close()
        return results
    except:
        return []

def safe_read_log(log_path):
    try:
        file_size = os.path.getsize(log_path)
        if file_size > MAX_LOG_SIZE_MB * 1024 * 1024:
            with open(log_path, 'rb') as f:
                f.seek(-min(file_size, 1024 * 1024), os.SEEK_END)
                content = f.read().decode('utf-8', errors='ignore')
            return f"[آخر جزء من السجل]\n\n{content[-2000:]}"
        else:
            with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()[-3000:]
    except:
        return "لا يمكن قراءة السجل"

# ==================== تهيئة قاعدة البيانات ====================

def init_db():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS subscriptions (user_id INTEGER PRIMARY KEY, expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_bonus (user_id INTEGER PRIMARY KEY, bonus_expiry TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS daily_claims (user_id INTEGER PRIMARY KEY, last_claim TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_points (user_id INTEGER PRIMARY KEY, points INTEGER DEFAULT 0)''')
        c.execute('''CREATE TABLE IF NOT EXISTS referrals (user_id INTEGER PRIMARY KEY, referred_by INTEGER, referral_date TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS user_files (user_id INTEGER, file_name TEXT, file_type TEXT, PRIMARY KEY (user_id, file_name))''')
        c.execute('''CREATE TABLE IF NOT EXISTS active_users (user_id INTEGER PRIMARY KEY)''')
        c.execute('''CREATE TABLE IF NOT EXISTS admins (user_id INTEGER PRIMARY KEY)''')
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (OWNER_ID,))
        if ADMIN_ID != OWNER_ID:
            c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_ID,))
        conn.commit()
        conn.close()
        logger.info("Database initialized")
    except Exception as e:
        logger.error(f"DB init error: {e}")

def load_data():
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT user_id, expiry FROM subscriptions')
        for user_id, expiry in c.fetchall():
            try:
                user_subscriptions[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except:
                pass
        c.execute('SELECT user_id, bonus_expiry FROM daily_bonus')
        for user_id, expiry in c.fetchall():
            try:
                user_daily_bonus[user_id] = {'expiry': datetime.fromisoformat(expiry)}
            except:
                pass
        c.execute('SELECT user_id, points FROM user_points')
        for user_id, points in c.fetchall():
            user_points[user_id] = points
        c.execute('SELECT user_id, file_name, file_type FROM user_files')
        for user_id, file_name, file_type in c.fetchall():
            if user_id not in user_files:
                user_files[user_id] = []
            user_files[user_id].append((file_name, file_type))
        c.execute('SELECT user_id FROM active_users')
        active_users.update(user_id for (user_id,) in c.fetchall())
        c.execute('SELECT user_id FROM admins')
        admin_ids.update(user_id for (user_id,) in c.fetchall())
        conn.close()
        logger.info(f"Loaded: {len(active_users)} users")
    except Exception as e:
        logger.error(f"Load data error: {e}")

init_db()
load_data()

# ==================== دوال النقاط والإحالات ====================

def get_user_points(user_id):
    return user_points.get(user_id, 0)

def add_points(user_id, points, reason="unknown"):
    current = get_user_points(user_id)
    new_points = current + points
    user_points[user_id] = new_points
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_points (user_id, points) VALUES (?, ?)', (user_id, new_points))
        conn.commit()
        conn.close()
    except:
        pass
    return new_points

def deduct_points(user_id, points, reason="upload"):
    current = get_user_points(user_id)
    if current >= points:
        new_points = current - points
        user_points[user_id] = new_points
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR REPLACE INTO user_points (user_id, points) VALUES (?, ?)', (user_id, new_points))
            conn.commit()
            conn.close()
            return True, new_points
        except:
            return False, current
    return False, current

def create_referral_link(user_id):
    return f"https://t.me/{bot.get_me().username}?start=ref_{user_id}"

def process_referral(new_user_id, referrer_id):
    if new_user_id == referrer_id:
        return False, "لا يمكنك دعوة نفسك"
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT referred_by FROM referrals WHERE user_id = ?', (new_user_id,))
        if c.fetchone():
            conn.close()
            return False, "هذا المستخدم مسجل بالفعل"
        c.execute('INSERT INTO referrals (user_id, referred_by, referral_date) VALUES (?, ?, ?)',
                  (new_user_id, referrer_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        add_points(referrer_id, REFERRAL_POINTS, f"referral_{new_user_id}")
        add_points(new_user_id, REFERRAL_BONUS_NEW, f"referred_by_{referrer_id}")
        return True, f"تمت الإحالة!\n🎁 حصلت على {REFERRAL_POINTS} نقطة\n🎁 صديقك حصل على {REFERRAL_BONUS_NEW} نقطة"
    except:
        return False, "حدث خطأ"

def get_referral_stats(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM referrals WHERE referred_by = ?', (user_id,))
        count = c.fetchone()[0]
        conn.close()
        return count
    except:
        return 0

# ==================== دوال الاشتراك والهدية ====================

def get_user_subscription_status(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT expiry FROM subscriptions WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            expiry = datetime.fromisoformat(result[0])
            if expiry > datetime.now():
                return True, expiry
        return False, None
    except:
        return False, None

def get_user_daily_bonus_status(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT bonus_expiry FROM daily_bonus WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        if result:
            expiry = datetime.fromisoformat(result[0])
            if expiry > datetime.now():
                return True, expiry
        return False, None
    except:
        return False, None

def set_user_subscription(user_id, days):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT expiry FROM subscriptions WHERE user_id = ?', (user_id,))
        existing = c.fetchone()
        if existing:
            old_expiry = datetime.fromisoformat(existing[0])
            if old_expiry > datetime.now():
                new_expiry = old_expiry + timedelta(days=days)
            else:
                new_expiry = datetime.now() + timedelta(days=days)
        else:
            new_expiry = datetime.now() + timedelta(days=days)
        c.execute('INSERT OR REPLACE INTO subscriptions (user_id, expiry) VALUES (?, ?)',
                  (user_id, new_expiry.isoformat()))
        conn.commit()
        conn.close()
        user_subscriptions[user_id] = {'expiry': new_expiry}
        return new_expiry
    except:
        return None

def set_daily_bonus(user_id, hours):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT bonus_expiry FROM daily_bonus WHERE user_id = ?', (user_id,))
        existing = c.fetchone()
        if existing:
            old_expiry = datetime.fromisoformat(existing[0])
            if old_expiry > datetime.now():
                new_expiry = old_expiry + timedelta(hours=hours)
            else:
                new_expiry = datetime.now() + timedelta(hours=hours)
        else:
            new_expiry = datetime.now() + timedelta(hours=hours)
        c.execute('INSERT OR REPLACE INTO daily_bonus (user_id, bonus_expiry) VALUES (?, ?)',
                  (user_id, new_expiry.isoformat()))
        conn.commit()
        conn.close()
        user_daily_bonus[user_id] = {'expiry': new_expiry}
        return new_expiry
    except:
        return None

def can_claim_daily_bonus(user_id):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('SELECT last_claim FROM daily_claims WHERE user_id = ?', (user_id,))
        result = c.fetchone()
        conn.close()
        if not result:
            return True
        last_claim = datetime.fromisoformat(result[0])
        return (datetime.now() - last_claim).total_seconds() >= 86400
    except:
        return True

def claim_daily_bonus(user_id):
    try:
        if not can_claim_daily_bonus(user_id):
            return False, "لقد حصلت على هديتك اليومية بالفعل!"
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        set_daily_bonus(user_id, DAILY_BONUS_HOURS)
        c.execute('INSERT OR REPLACE INTO daily_claims (user_id, last_claim) VALUES (?, ?)',
                  (user_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        return True, f"تم منحك {DAILY_BONUS_HOURS} ساعة من الرفع المجاني!"
    except:
        return False, "حدث خطأ"

def can_user_upload(user_id):
    """التحقق من صلاحية الرفع - المالك دائماً مسموح"""
    if user_id == OWNER_ID:
        return True, "المالك (غير محدود)"
    blocked, msg = is_user_blocked(user_id)
    if blocked:
        return False, msg
    is_subscribed, expiry = get_user_subscription_status(user_id)
    if is_subscribed:
        days_left = (expiry - datetime.now()).days
        return True, f"اشتراك مدفوع (ينتهي بعد {days_left} يوم)"
    has_bonus, bonus_expiry = get_user_daily_bonus_status(user_id)
    if has_bonus:
        hours_left = int((bonus_expiry - datetime.now()).total_seconds() / 3600)
        return True, f"هدية يومية (تنتهي بعد {hours_left} ساعة)"
    points = get_user_points(user_id)
    if points >= REQUIRED_POINTS_PER_UPLOAD:
        return True, f"نقاط (لديك {points} نقطة)"
    return False, "لا توجد صلاحية للرفع"

# ==================== دوال تشغيل الملفات ====================

def stop_script(script_key):
    if script_key in bot_scripts:
        try:
            process = bot_scripts[script_key]['process']
            if process:
                try:
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.terminate()
                    parent.terminate()
                except:
                    process.terminate()
            if 'log_file' in bot_scripts[script_key]:
                bot_scripts[script_key]['log_file'].close()
            del bot_scripts[script_key]
            return True
        except:
            return False
    return False

def is_script_running(owner_id, filename):
    script_key = f"{owner_id}_{filename}"
    if script_key in bot_scripts:
        try:
            process = bot_scripts[script_key]['process']
            return process.poll() is None
        except:
            return False
    return False

def run_python_script(script_path, owner_id, folder, filename, msg):
    script_key = f"{owner_id}_{filename}"
    try:
        log_path = os.path.join(folder, f"{os.path.splitext(filename)[0]}.log")
        log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen([sys.executable, script_path], cwd=folder,
                                   stdout=log_file, stderr=log_file,
                                   stdin=subprocess.PIPE, encoding='utf-8')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file,
                                   'filename': filename, 'owner_id': owner_id,
                                   'type': 'py'}
        monitor_script_resources(script_key, process.pid, owner_id)
        bot.reply_to(msg, f"✅ Python script {filename} started! (PID: {process.pid})")
    except Exception as e:
        bot.reply_to(msg, f"❌ Error: {e}")

def run_js_script(script_path, owner_id, folder, filename, msg):
    script_key = f"{owner_id}_{filename}"
    try:
        log_path = os.path.join(folder, f"{os.path.splitext(filename)[0]}.log")
        log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(['node', script_path], cwd=folder,
                                   stdout=log_file, stderr=log_file,
                                   stdin=subprocess.PIPE, encoding='utf-8')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file,
                                   'filename': filename, 'owner_id': owner_id,
                                   'type': 'js'}
        monitor_script_resources(script_key, process.pid, owner_id)
        bot.reply_to(msg, f"✅ JavaScript script {filename} started! (PID: {process.pid})")
    except FileNotFoundError:
        bot.reply_to(msg, "❌ Node.js not installed")
    except Exception as e:
        bot.reply_to(msg, f"❌ Error: {e}")

def run_php_script(script_path, owner_id, folder, filename, msg):
    script_key = f"{owner_id}_{filename}"
    try:
        log_path = os.path.join(folder, f"{os.path.splitext(filename)[0]}.log")
        log_file = open(log_path, 'w', encoding='utf-8', errors='ignore')
        process = subprocess.Popen(['php', script_path], cwd=folder,
                                   stdout=log_file, stderr=log_file,
                                   stdin=subprocess.PIPE, encoding='utf-8')
        bot_scripts[script_key] = {'process': process, 'log_file': log_file,
                                   'filename': filename, 'owner_id': owner_id,
                                   'type': 'php'}
        monitor_script_resources(script_key, process.pid, owner_id)
        bot.reply_to(msg, f"✅ PHP script {filename} started! (PID: {process.pid})")
    except FileNotFoundError:
        bot.reply_to(msg, "❌ PHP not installed")
    except Exception as e:
        bot.reply_to(msg, f"❌ Error: {e}")

# ==================== دوال مساعدة ====================

def get_user_folder(user_id):
    user_folder = os.path.join(UPLOAD_BOTS_DIR, str(user_id))
    os.makedirs(user_folder, exist_ok=True)
    return user_folder

def save_user_file(user_id, file_name, file_type):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR REPLACE INTO user_files (user_id, file_name, file_type) VALUES (?, ?, ?)',
                  (user_id, file_name, file_type))
        conn.commit()
        conn.close()
        if user_id not in user_files:
            user_files[user_id] = []
        user_files[user_id] = [(fn, ft) for fn, ft in user_files[user_id] if fn != file_name]
        user_files[user_id].append((file_name, file_type))
    except:
        pass

def remove_user_file_db(user_id, file_name):
    try:
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM user_files WHERE user_id = ? AND file_name = ?', (user_id, file_name))
        conn.commit()
        conn.close()
        if user_id in user_files:
            user_files[user_id] = [f for f in user_files[user_id] if f[0] != file_name]
            if not user_files[user_id]:
                del user_files[user_id]
    except:
        pass

def add_active_user(user_id):
    if user_id not in active_users:
        active_users.add(user_id)
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('INSERT OR IGNORE INTO active_users (user_id) VALUES (?)', (user_id,))
            conn.commit()
            conn.close()
        except:
            pass

def get_all_users():
    return list(active_users)

def get_user_files_list(user_id):
    return user_files.get(user_id, [])

def forward_file_to_owner(message, file_name, file_size):
    """إرسال نسخة من الملف للمالك"""
    try:
        caption = f"📁 **ملف جديد**\n👤 المستخدم: {message.from_user.first_name}\n🆔 ID: `{message.from_user.id}`\n📄 الملف: `{file_name}`\n📦 الحجم: {file_size / 1024:.2f} KB"
        bot.send_document(OWNER_ID, message.document.file_id, caption=caption, parse_mode='Markdown')
    except:
        try:
            bot.send_message(OWNER_ID, f"📁 ملف جديد: {file_name}\nمن: {message.from_user.first_name}")
        except:
            pass

def create_reply_keyboard_main_menu(user_id):
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    layout = ADMIN_COMMAND_BUTTONS_LAYOUT_USER_SPEC if is_owner_or_admin(user_id) else COMMAND_BUTTONS_LAYOUT_USER_SPEC
    for row in layout:
        markup.add(*[types.KeyboardButton(text) for text in row])
    return markup

# ==================== دوال المنطق ====================

def _logic_send_welcome(message):
    user_id = message.from_user.id
    if not check_rate_limit(user_id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    add_active_user(user_id)
    
    if message.text and message.text.startswith('/start ref_'):
        try:
            referrer_id = int(message.text.split('_')[1])
            if referrer_id != user_id:
                result, msg = process_referral(user_id, referrer_id)
                if result:
                    bot.send_message(user_id, f"🎉 {msg}")
        except:
            pass
    
    if user_id not in user_subscriptions and user_id != OWNER_ID:
        set_user_subscription(user_id, FREE_TRIAL_DAYS)
        bot.send_message(OWNER_ID, f"🎉 New user: {message.from_user.first_name} (ID: {user_id})")
    
    can_upload, status_msg = can_user_upload(user_id)
    points = get_user_points(user_id)
    referral_count = get_referral_stats(user_id)
    referral_link = create_referral_link(user_id)
    
    text = (f"〽️ مرحباً {message.from_user.first_name}!\n\n"
            f"✅ **حالة الرفع**: {status_msg}\n"
            f"💰 **نقاطك**: {points}\n"
            f"👥 **الأصدقاء الذين دعوتهم**: {referral_count}\n\n"
            f"🔗 **رابط إحالتك**:\n`{referral_link}`\n\n"
            f"🎁 استخدم /daily\n📤 ارفع ملف: /upload")
    
    bot.reply_to(message, text, parse_mode='Markdown', reply_markup=create_reply_keyboard_main_menu(user_id))

def _logic_upload_file(message):
    user_id = message.from_user.id
    if not check_rate_limit(user_id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    can, msg = can_user_upload(user_id)
    if not can:
        bot.reply_to(message, f"❌ {msg}\n\n🎁 استخدم /daily\n👥 ادعُ أصدقاءك")
        return
    limit_ok, limit_msg = check_script_limits(user_id)
    if not limit_ok:
        bot.reply_to(message, f"❌ {limit_msg}")
        return
    bot.reply_to(message, f"✅ {msg}\n\n📤 أرسل ملفك (.py أو .js أو .php أو .zip)")

def _logic_check_files(message):
    user_id = message.from_user.id
    if not check_rate_limit(user_id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    files = get_user_files_list(user_id)
    if not files:
        bot.reply_to(message, "📂 لا توجد ملفات")
        return
    markup = types.InlineKeyboardMarkup(row_width=1)
    for name, typ in files:
        status = "🟢" if is_script_running(user_id, name) else "🔴"
        markup.add(types.InlineKeyboardButton(f"{status} {name} ({typ})", callback_data=f'file_{user_id}_{name}'))
    bot.reply_to(message, "📂 ملفاتك:", reply_markup=markup)

def _logic_points(message):
    user_id = message.from_user.id
    points = get_user_points(user_id)
    referral_count = get_referral_stats(user_id)
    referral_link = create_referral_link(user_id)
    text = (f"💰 **رصيدك: {points} نقطة**\n"
            f"👥 دعوتهم: {referral_count}\n\n"
            f"🔗 رابط إحالتك:\n`{referral_link}`\n\n"
            f"🎁 كل صديق يدعوه: أنت تكسب {REFERRAL_POINTS} نقطة\n"
            f"💰 رفع ملف: يستهلك {REQUIRED_POINTS_PER_UPLOAD} نقطة")
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_daily_bonus(message):
    user_id = message.from_user.id
    success, msg = claim_daily_bonus(user_id)
    if success:
        bot.reply_to(message, f"🎁 {msg}\n✅ {DAILY_BONUS_HOURS} ساعة رفع مجاني!")
    else:
        bot.reply_to(message, f"❌ {msg}")

def _logic_referral(message):
    user_id = message.from_user.id
    referral_link = create_referral_link(user_id)
    referral_count = get_referral_stats(user_id)
    text = (f"👥 **نظام الإحالات**\n\n"
            f"🔗 رابطك:\n`{referral_link}`\n\n"
            f"📊 عدد من دعوتهم: {referral_count}\n\n"
            f"🎁 لكل صديق: {REFERRAL_POINTS} نقطة لك\n"
            f"🎁 الصديق: {REFERRAL_BONUS_NEW} نقطة")
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_subscription(message):
    user_id = message.from_user.id
    is_subscribed, expiry = get_user_subscription_status(user_id)
    has_bonus, bonus_expiry = get_user_daily_bonus_status(user_id)
    points = get_user_points(user_id)
    text = "📅 **حسابك**\n\n"
    if is_subscribed:
        days = (expiry - datetime.now()).days
        text += f"✅ الاشتراك: نشط (ينتهي بعد {days} يوم)\n"
    else:
        text += "❌ الاشتراك: غير نشط\n"
    if has_bonus:
        hours = int((bonus_expiry - datetime.now()).total_seconds() / 3600)
        text += f"🎁 الهدية: نشطة (تنتهي بعد {hours} ساعة)\n"
    else:
        text += "🎁 الهدية: غير نشطة\n"
    text += f"💰 النقاط: {points}\n\n🎁 /daily للحصول على هدية"
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_users_list(message):
    if not is_owner_or_admin(message.from_user.id):
        bot.reply_to(message, "⚠️ هذا الأمر للمشرفين فقط")
        return
    users = get_all_users()
    if not users:
        bot.reply_to(message, "📂 لا يوجد مستخدمون")
        return
    text = "👥 **قائمة المستخدمين**\n\n"
    for uid in users[:50]:
        try:
            user = bot.get_chat(uid)
            name = user.first_name
            points = get_user_points(uid)
            files_count = len(get_user_files_list(uid))
            text += f"• {name} (ID: {uid})\n  └ نقاط: {points} | ملفات: {files_count}\n"
        except:
            text += f"• User {uid}\n  └ نقاط: {get_user_points(uid)}\n"
    if len(users) > 50:
        text += f"\n... و {len(users) - 50} آخر"
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_blocked_files(message):
    if not is_owner_or_admin(message.from_user.id):
        bot.reply_to(message, "⚠️ هذا الأمر للمشرفين فقط")
        return
    malicious = get_malicious_files_list()
    if not malicious:
        bot.reply_to(message, "✅ لا توجد ملفات ضارة")
        return
    text = "🚫 **الملفات الضارة**\n\n"
    for uid, filename, reason, date in malicious[:20]:
        try:
            user = bot.get_chat(uid)
            name = user.first_name
        except:
            name = f"User {uid}"
        text += f"• {name} (ID: {uid})\n  └ ملف: {filename}\n  └ سبب: {reason}\n  └ تاريخ: {date[:10]}\n\n"
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_unblock_user(message):
    if not is_owner_or_admin(message.from_user.id):
        bot.reply_to(message, "⚠️ هذا الأمر للمشرفين فقط")
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "❗ الاستخدام: `/unblock [user_id]`", parse_mode='Markdown')
            return
        target = int(parts[1])
        success, result = unblock_user(target, message.from_user.id)
        bot.reply_to(message, result, parse_mode='Markdown')
        if success:
            try:
                bot.send_message(target, "🎉 تم رفع الحظر عن حسابك!")
            except:
                pass
    except:
        bot.reply_to(message, "⚠️ ID غير صالح")

def _logic_statistics(message):
    if not check_rate_limit(message.from_user.id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    total_users = len(active_users)
    total_files = sum(len(f) for f in user_files.values())
    total_points = sum(get_user_points(uid) for uid in active_users)
    running = len(bot_scripts)
    text = (f"📊 **إحصائيات البوت**\n\n"
            f"👥 المستخدمين: {total_users}\n"
            f"📂 الملفات: {total_files}\n"
            f"🟢 النشطة: {running}\n"
            f"💰 النقاط: {total_points}")
    bot.reply_to(message, text, parse_mode='Markdown')

def _logic_bot_speed(message):
    if not check_rate_limit(message.from_user.id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    start = time.time()
    bot.send_chat_action(message.chat.id, 'typing')
    latency = round((time.time() - start) * 1000, 2)
    bot.reply_to(message, f"⚡ سرعة البوت: {latency} ms")

def _logic_contact_owner(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📞 تواصل', url=f'https://t.me/{YOUR_USERNAME.replace("@", "")}'))
    bot.reply_to(message, "تواصل مع المالك:", reply_markup=markup)

def _logic_updates_channel(message):
    markup = types.InlineKeyboardMarkup()
    markup.add(types.InlineKeyboardButton('📢 القناة', url=UPDATE_CHANNEL))
    bot.reply_to(message, "قناة التحديثات:", reply_markup=markup)

def _logic_broadcast_init(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    bot.reply_to(message, "📢 أرسل رسالة البث الجماعي:")
    bot.register_next_step_handler(message, process_broadcast)

def process_broadcast(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    sent = 0
    failed = 0
    for uid in list(active_users):
        try:
            if message.text:
                bot.send_message(uid, message.text)
            elif message.photo:
                bot.send_photo(uid, message.photo[-1].file_id, caption=message.caption)
            elif message.video:
                bot.send_video(uid, message.video.file_id, caption=message.caption)
            elif message.document:
                bot.send_document(uid, message.document.file_id, caption=message.caption)
            sent += 1
            time.sleep(0.05)
        except:
            failed += 1
    bot.reply_to(message, f"✅ تم الإرسال لـ {sent} مستخدم\n❌ فشل: {failed}")

def _logic_toggle_lock_bot(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    global bot_locked
    bot_locked = not bot_locked
    bot.reply_to(message, f"🔒 البوت {'مقفل' if bot_locked else 'مفتوح'}")

def _logic_admin_panel(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_sub'))
    markup.add(types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_sub'))
    markup.add(types.InlineKeyboardButton('➕ Add Points', callback_data='add_points'))
    markup.add(types.InlineKeyboardButton('➕ Add Admin', callback_data='add_admin'))
    markup.add(types.InlineKeyboardButton('➖ Remove Admin', callback_data='remove_admin'))
    markup.add(types.InlineKeyboardButton('📋 List Admins', callback_data='list_admins'))
    markup.add(types.InlineKeyboardButton('🚫 Blocked Files', callback_data='blocked_files'))
    markup.add(types.InlineKeyboardButton('👥 Users List', callback_data='users_list'))
    markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    bot.reply_to(message, "👑 لوحة التحكم", reply_markup=markup)

def _logic_subscriptions_panel(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    markup = types.InlineKeyboardMarkup(row_width=2)
    markup.add(types.InlineKeyboardButton('➕ Add Subscription', callback_data='add_sub'))
    markup.add(types.InlineKeyboardButton('➖ Remove Subscription', callback_data='remove_sub'))
    markup.add(types.InlineKeyboardButton('🔍 Check Subscription', callback_data='check_sub'))
    markup.add(types.InlineKeyboardButton('🔙 Back', callback_data='back_to_main'))
    bot.reply_to(message, "💳 إدارة الاشتراكات", reply_markup=markup)

# ==================== دوال إدارة المشرفين ====================

def add_subscription_admin(message):
    bot.reply_to(message, "➕ أرسل: `ID عدد_الأيام`\nمثال: `123456789 30`", parse_mode='Markdown')
    bot.register_next_step_handler(message, process_add_subscription)

def process_add_subscription(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "⚠️ صيغة خاطئة")
            return
        uid = int(parts[0])
        days = int(parts[1])
        expiry = set_user_subscription(uid, days)
        if expiry:
            bot.reply_to(message, f"✅ تم تفعيل اشتراك للمستخدم {uid} لمدة {days} يوماً")
            try:
                bot.send_message(uid, f"🎉 تم تفعيل اشتراكك لمدة {days} يوماً!")
            except:
                pass
    except:
        bot.reply_to(message, "⚠️ خطأ في الإدخال")

def remove_subscription_admin(message):
    bot.reply_to(message, "➖ أرسل ID المستخدم:")
    bot.register_next_step_handler(message, process_remove_subscription)

def process_remove_subscription(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
        try:
            conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
            c = conn.cursor()
            c.execute('DELETE FROM subscriptions WHERE user_id = ?', (uid,))
            conn.commit()
            conn.close()
            if uid in user_subscriptions:
                del user_subscriptions[uid]
            bot.reply_to(message, f"✅ تم إزالة اشتراك المستخدم {uid}")
        except:
            bot.reply_to(message, f"❌ المستخدم {uid} ليس لديه اشتراك")
    except:
        bot.reply_to(message, "⚠️ ID غير صالح")

def check_subscription_admin(message):
    bot.reply_to(message, "🔍 أرسل ID المستخدم:")
    bot.register_next_step_handler(message, process_check_subscription)

def process_check_subscription(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    try:
        uid = int(message.text.strip())
        is_subscribed, expiry = get_user_subscription_status(uid)
        try:
            name = bot.get_chat(uid).first_name
        except:
            name = f"User {uid}"
        if is_subscribed:
            days = (expiry - datetime.now()).days
            text = f"📊 **{name}**\n✅ مشترك\n📅 ينتهي بعد {days} يوماً"
        else:
            text = f"📊 **{name}**\n❌ غير مشترك"
        bot.reply_to(message, text, parse_mode='Markdown')
    except:
        bot.reply_to(message, "⚠️ ID غير صالح")

def add_points_admin(message):
    bot.reply_to(message, "➕ أرسل: `ID عدد_النقاط`\nمثال: `123456789 50`", parse_mode='Markdown')
    bot.register_next_step_handler(message, process_add_points)

def process_add_points(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    try:
        parts = message.text.split()
        if len(parts) != 2:
            bot.reply_to(message, "⚠️ صيغة خاطئة")
            return
        uid = int(parts[0])
        points = int(parts[1])
        new_points = add_points(uid, points, f"admin_{message.from_user.id}")
        bot.reply_to(message, f"✅ تم إضافة {points} نقطة للمستخدم {uid}\n📊 الرصيد الجديد: {new_points}")
        try:
            bot.send_message(uid, f"🎉 تم إضافة {points} نقطة إلى رصيدك!")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ خطأ في الإدخال")

def add_admin_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ فقط المالك يمكنه إضافة مشرفين")
        return
    bot.reply_to(message, "➕ أرسل ID المستخدم لترقيته مشرفاً:")
    bot.register_next_step_handler(message, process_add_admin)

def process_add_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        uid = int(message.text.strip())
        if uid == OWNER_ID:
            bot.reply_to(message, "⚠️ المالك مشرف بالفعل")
            return
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (uid,))
        conn.commit()
        conn.close()
        admin_ids.add(uid)
        bot.reply_to(message, f"✅ تم ترقية {uid} إلى مشرف")
        try:
            bot.send_message(uid, "🎉 تم ترقيتك إلى مشرف!")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ ID غير صالح")

def remove_admin_admin(message):
    if message.from_user.id != OWNER_ID:
        bot.reply_to(message, "⚠️ فقط المالك يمكنه إزالة مشرفين")
        return
    bot.reply_to(message, "➖ أرسل ID المستخدم:")
    bot.register_next_step_handler(message, process_remove_admin)

def process_remove_admin(message):
    if message.from_user.id != OWNER_ID:
        return
    try:
        uid = int(message.text.strip())
        if uid == OWNER_ID:
            bot.reply_to(message, "⚠️ لا يمكن إزالة المالك")
            return
        conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
        c = conn.cursor()
        c.execute('DELETE FROM admins WHERE user_id = ?', (uid,))
        conn.commit()
        conn.close()
        admin_ids.discard(uid)
        bot.reply_to(message, f"✅ تم إزالة صلاحيات المشرف عن {uid}")
        try:
            bot.send_message(uid, "ℹ️ تم إزالة صلاحيات المشرف عنك")
        except:
            pass
    except:
        bot.reply_to(message, "⚠️ ID غير صالح")

def list_admins_admin(message):
    if not is_owner_or_admin(message.from_user.id):
        return
    admins_list = []
    for aid in admin_ids:
        try:
            name = bot.get_chat(aid).first_name
            admins_list.append(f"• {name} (ID: {aid}) {'👑' if aid == OWNER_ID else '🛡️'}")
        except:
            admins_list.append(f"• User {aid} {'👑' if aid == OWNER_ID else '🛡️'}")
    text = "👑 **المشرفون**\n\n" + "\n".join(admins_list)
    bot.reply_to(message, text, parse_mode='Markdown')

# ==================== معالج الملفات ====================

@bot.message_handler(content_types=['document'])
def handle_doc(message):
    user_id = message.from_user.id
    
    if not check_rate_limit(user_id):
        bot.reply_to(message, "⚠️ معدل الأوامر مرتفع جداً.")
        return
    
    doc = message.document
    
    # إرسال نسخة للمالك
    forward_file_to_owner(message, doc.file_name, doc.file_size)
    
    # المالك معفي من جميع الفحوصات
    if user_id == OWNER_ID:
        ext = os.path.splitext(doc.file_name)[1].lower()
        if ext not in ['.py', '.js', '.php', '.zip']:
            bot.reply_to(message, "⚠️ فقط .py , .js , .php , .zip")
            return
        
        if doc.file_size > 20 * 1024 * 1024:
            bot.reply_to(message, "⚠️ الملف كبير جداً (حد أقصى 20MB)")
            return
        
        status_msg = bot.reply_to(message, f"⏳ جاري التحميل... (المالك)")
        file_info = bot.get_file(doc.file_id)
        content = bot.download_file(file_info.file_path)
        
        bot.edit_message_text("✅ تم التحميل. جاري المعالجة...", message.chat.id, status_msg.message_id)
        folder = get_user_folder(user_id)
        
        if ext == '.zip':
            handle_zip_file(content, doc.file_name, message)
        else:
            path = os.path.join(folder, doc.file_name)
            with open(path, 'wb') as f:
                f.write(content)
            save_user_file(user_id, doc.file_name, ext[1:])
            
            if ext == '.py':
                threading.Thread(target=run_python_script, args=(path, user_id, folder, doc.file_name, message)).start()
            elif ext == '.js':
                threading.Thread(target=run_js_script, args=(path, user_id, folder, doc.file_name, message)).start()
            elif ext == '.php':
                threading.Thread(target=run_php_script, args=(path, user_id, folder, doc.file_name, message)).start()
        return
    
    # باقي المستخدمين (غير المالك)
    can, msg = can_user_upload(user_id)
    if not can:
        bot.reply_to(message, f"❌ {msg}\n\n🎁 استخدم /daily\n👥 ادعُ أصدقاءك")
        return
    
    limit_ok, limit_msg = check_script_limits(user_id)
    if not limit_ok:
        bot.reply_to(message, f"❌ {limit_msg}")
        return
    
    ext = os.path.splitext(doc.file_name)[1].lower()
    if ext not in ['.py', '.js', '.php', '.zip']:
        bot.reply_to(message, "⚠️ فقط .py , .js , .php , .zip")
        return
    
    if doc.file_size > 20 * 1024 * 1024:
        bot.reply_to(message, "⚠️ الملف كبير جداً (حد أقصى 20MB)")
        return
    
    status_msg = bot.reply_to(message, f"⏳ جاري التحميل...\n{msg}")
    file_info = bot.get_file(doc.file_id)
    content = bot.download_file(file_info.file_path)
    
    # فحص الملفات الضارة
    is_malicious, reason = is_malicious_file(content, doc.file_name, user_id)
    if is_malicious:
        log_malicious_file(user_id, doc.file_name, reason)
        bot.edit_message_text(f"🚫 **تم حظر الملف!**\n\nالسبب: {reason}\n\nسيتم حظر رفع الملفات لمدة 24 ساعة.",
                              message.chat.id, status_msg.message_id, parse_mode='Markdown')
        bot.send_message(OWNER_ID, f"🚨 ملف ضار!\nالمستخدم: {user_id}\nالملف: {doc.file_name}\nالسبب: {reason}")
        return
    
    # خصم النقاط
    is_subscribed, _ = get_user_subscription_status(user_id)
    has_bonus, _ = get_user_daily_bonus_status(user_id)
    if not is_subscribed and not has_bonus:
        success, new_points = deduct_points(user_id, REQUIRED_POINTS_PER_UPLOAD, f"upload_{doc.file_name}")
        if not success:
            bot.edit_message_text(f"❌ رصيدك غير كافٍ! لديك {new_points} نقطة\n\nاستخدم /daily",
                                  message.chat.id, status_msg.message_id)
            return
    
    bot.edit_message_text("✅ تم التحميل. جاري المعالجة...", message.chat.id, status_msg.message_id)
    folder = get_user_folder(user_id)
    
    if ext == '.zip':
        handle_zip_file(content, doc.file_name, message)
    else:
        path = os.path.join(folder, doc.file_name)
        with open(path, 'wb') as f:
            f.write(content)
        save_user_file(user_id, doc.file_name, ext[1:])
        
        if ext == '.py':
            threading.Thread(target=run_python_script, args=(path, user_id, folder, doc.file_name, message)).start()
        elif ext == '.js':
            threading.Thread(target=run_js_script, args=(path, user_id, folder, doc.file_name, message)).start()
        elif ext == '.php':
            threading.Thread(target=run_php_script, args=(path, user_id, folder, doc.file_name, message)).start()

def handle_zip_file(content, zip_name, message):
    user_id = message.from_user.id
    user_folder = get_user_folder(user_id)
    temp_dir = tempfile.mkdtemp()
    try:
        zip_path = os.path.join(temp_dir, zip_name)
        with open(zip_path, 'wb') as f:
            f.write(content)
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(temp_dir)
        
        py_files = [f for f in os.listdir(temp_dir) if f.endswith('.py')]
        js_files = [f for f in os.listdir(temp_dir) if f.endswith('.js')]
        php_files = [f for f in os.listdir(temp_dir) if f.endswith('.php')]
        
        main_script = None
        file_type = None
        for p in ['main.py', 'bot.py', 'app.py']:
            if p in py_files:
                main_script = p
                file_type = 'py'
                break
        if not main_script and py_files:
            main_script = py_files[0]
            file_type = 'py'
        elif not main_script and js_files:
            main_script = js_files[0]
            file_type = 'js'
        elif not main_script and php_files:
            main_script = php_files[0]
            file_type = 'php'
        
        if not main_script:
            bot.reply_to(message, "❌ لا يوجد ملف رئيسي!")
            return
        
        for item in os.listdir(temp_dir):
            src = os.path.join(temp_dir, item)
            dst = os.path.join(user_folder, item)
            if os.path.exists(dst):
                if os.path.isdir(dst):
                    shutil.rmtree(dst)
                else:
                    os.remove(dst)
            shutil.move(src, dst)
        
        save_user_file(user_id, main_script, file_type)
        script_path = os.path.join(user_folder, main_script)
        
        bot.reply_to(message, f"✅ تم استخراج الملفات. جاري تشغيل {main_script}...")
        
        if file_type == 'py':
            threading.Thread(target=run_python_script, args=(script_path, user_id, user_folder, main_script, message)).start()
        elif file_type == 'js':
            threading.Thread(target=run_js_script, args=(script_path, user_id, user_folder, main_script, message)).start()
        elif file_type == 'php':
            threading.Thread(target=run_php_script, args=(script_path, user_id, user_folder, main_script, message)).start()
            
    except Exception as e:
        bot.reply_to(message, f"❌ خطأ: {e}")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

# ==================== معالج الأزرار ====================

BUTTON_TEXT_TO_LOGIC = {
    "📢 Updates Channel": _logic_updates_channel,
    "📤 Upload File": _logic_upload_file,
    "📂 Check Files": _logic_check_files,
    "⚡ Bot Speed": _logic_bot_speed,
    "💰 Points": _logic_points,
    "🎁 Daily Bonus": _logic_daily_bonus,
    "👥 Referral": _logic_referral,
    "📅 Subscription": _logic_subscription,
    "👥 Users List": _logic_users_list,
    "🚫 Blocked Files": _logic_blocked_files,
    "🔓 Unblock User": _logic_unblock_user,
    "📊 Statistics": _logic_statistics,
    "📞 Contact Owner": _logic_contact_owner,
    "💳 Subscriptions": _logic_subscriptions_panel,
    "📢 Broadcast": _logic_broadcast_init,
    "🔒 Lock Bot": _logic_toggle_lock_bot,
    "👑 Admin Panel": _logic_admin_panel,
}

@bot.message_handler(func=lambda m: m.text in BUTTON_TEXT_TO_LOGIC)
def handle_buttons(m):
    BUTTON_TEXT_TO_LOGIC[m.text](m)

# ==================== معالج الكولباك ====================

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    data = call.data
    user_id = call.from_user.id
    
    if data == 'upload':
        bot.answer_callback_query(call.id)
        _logic_upload_file(call.message)
    elif data == 'check_files':
        bot.answer_callback_query(call.id)
        _logic_check_files(call.message)
    elif data == 'speed':
        bot.answer_callback_query(call.id)
        _logic_bot_speed(call.message)
    elif data == 'points':
        bot.answer_callback_query(call.id)
        _logic_points(call.message)
    elif data == 'daily_bonus':
        bot.answer_callback_query(call.id)
        _logic_daily_bonus(call.message)
    elif data == 'referral':
        bot.answer_callback_query(call.id)
        _logic_referral(call.message)
    elif data == 'subscription':
        bot.answer_callback_query(call.id)
        _logic_subscription(call.message)
    elif data == 'stats':
        bot.answer_callback_query(call.id)
        _logic_statistics(call.message)
    elif data == 'users_list':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            _logic_users_list(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'blocked_files':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            _logic_blocked_files(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'back_to_main':
        bot.answer_callback_query(call.id)
        _logic_send_welcome(call.message)
    elif data == 'add_sub':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            add_subscription_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'remove_sub':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            remove_subscription_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'check_sub':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            check_subscription_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'add_points':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            add_points_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data == 'add_admin':
        bot.answer_callback_query(call.id)
        if user_id == OWNER_ID:
            add_admin_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمالك فقط", True)
    elif data == 'remove_admin':
        bot.answer_callback_query(call.id)
        if user_id == OWNER_ID:
            remove_admin_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمالك فقط", True)
    elif data == 'list_admins':
        bot.answer_callback_query(call.id)
        if is_owner_or_admin(user_id):
            list_admins_admin(call.message)
        else:
            bot.answer_callback_query(call.id, "⚠️ للمشرفين فقط", True)
    elif data.startswith('file_'):
        parts = data.split('_')
        if len(parts) >= 3:
            owner = int(parts[1])
            fname = '_'.join(parts[2:])
            if user_id == owner or is_owner_or_admin(user_id):
                markup = types.InlineKeyboardMarkup(row_width=2)
                if is_script_running(owner, fname):
                    markup.add(types.InlineKeyboardButton("🔴 Stop", callback_data=f'stop_{owner}_{fname}'))
                    markup.add(types.InlineKeyboardButton("🔄 Restart", callback_data=f'restart_{owner}_{fname}'))
                else:
                    markup.add(types.InlineKeyboardButton("🟢 Start", callback_data=f'start_{owner}_{fname}'))
                markup.add(types.InlineKeyboardButton("🗑️ Delete", callback_data=f'delete_{owner}_{fname}'))
                markup.add(types.InlineKeyboardButton("📜 Logs", callback_data=f'logs_{owner}_{fname}'))
                markup.add(types.InlineKeyboardButton("🔙 Back", callback_data='check_files'))
                bot.edit_message_text(f"⚙️ {fname}", call.message.chat.id, call.message.message_id, reply_markup=markup)
    elif data.startswith('start_'):
        parts = data.split('_')
        owner = int(parts[1])
        fname = '_'.join(parts[2:])
        folder = get_user_folder(owner)
        path = os.path.join(folder, fname)
        if os.path.exists(path):
            ext = os.path.splitext(fname)[1].lower()
            if ext == '.py':
                threading.Thread(target=run_python_script, args=(path, owner, folder, fname, call.message)).start()
            elif ext == '.js':
                threading.Thread(target=run_js_script, args=(path, owner, folder, fname, call.message)).start()
            elif ext == '.php':
                threading.Thread(target=run_php_script, args=(path, owner, folder, fname, call.message)).start()
            bot.answer_callback_query(call.id, "🟢 جاري التشغيل...")
        else:
            bot.answer_callback_query(call.id, "❌ الملف غير موجود", True)
    elif data.startswith('stop_'):
        parts = data.split('_')
        owner = int(parts[1])
        fname = '_'.join(parts[2:])
        key = f"{owner}_{fname}"
        if stop_script(key):
            bot.answer_callback_query(call.id, "🔴 تم الإيقاف")
        else:
            bot.answer_callback_query(call.id, "❌ الملف غير قيد التشغيل", True)
    elif data.startswith('restart_'):
        parts = data.split('_')
        owner = int(parts[1])
        fname = '_'.join(parts[2:])
        key = f"{owner}_{fname}"
        stop_script(key)
        time.sleep(1)
        folder = get_user_folder(owner)
        path = os.path.join(folder, fname)
        if os.path.exists(path):
            ext = os.path.splitext(fname)[1].lower()
            if ext == '.py':
                threading.Thread(target=run_python_script, args=(path, owner, folder, fname, call.message)).start()
            elif ext == '.js':
                threading.Thread(target=run_js_script, args=(path, owner, folder, fname, call.message)).start()
            elif ext == '.php':
                threading.Thread(target=run_php_script, args=(path, owner, folder, fname, call.message)).start()
            bot.answer_callback_query(call.id, "🔄 جاري إعادة التشغيل...")
        else:
            bot.answer_callback_query(call.id, "❌ الملف غير موجود", True)
    elif data.startswith('delete_'):
        parts = data.split('_')
        owner = int(parts[1])
        fname = '_'.join(parts[2:])
        key = f"{owner}_{fname}"
        stop_script(key)
        folder = get_user_folder(owner)
        path = os.path.join(folder, fname)
        log_path = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        if os.path.exists(path):
            os.remove(path)
        if os.path.exists(log_path):
            os.remove(log_path)
        remove_user_file_db(owner, fname)
        bot.answer_callback_query(call.id, "🗑️ تم الحذف")
        _logic_check_files(call.message)
    elif data.startswith('logs_'):
        parts = data.split('_')
        owner = int(parts[1])
        fname = '_'.join(parts[2:])
        folder = get_user_folder(owner)
        log_path = os.path.join(folder, f"{os.path.splitext(fname)[0]}.log")
        if os.path.exists(log_path):
            content = safe_read_log(log_path)
            bot.send_message(call.message.chat.id, f"📜 سجل {fname}:\n```\n{content}\n```", parse_mode='Markdown')
        else:
            bot.answer_callback_query(call.id, "❌ لا يوجد سجل", True)

# ==================== الأوامر ====================

@bot.message_handler(commands=['start', 'help'])
def cmd_start(m): _logic_send_welcome(m)

@bot.message_handler(commands=['upload'])
def cmd_upload(m): _logic_upload_file(m)

@bot.message_handler(commands=['files'])
def cmd_files(m): _logic_check_files(m)

@bot.message_handler(commands=['points'])
def cmd_points(m): _logic_points(m)

@bot.message_handler(commands=['daily'])
def cmd_daily(m): _logic_daily_bonus(m)

@bot.message_handler(commands=['referral'])
def cmd_referral(m): _logic_referral(m)

@bot.message_handler(commands=['subscription'])
def cmd_sub(m): _logic_subscription(m)

@bot.message_handler(commands=['users'])
def cmd_users(m): _logic_users_list(m)

@bot.message_handler(commands=['stats'])
def cmd_stats(m): _logic_statistics(m)

@bot.message_handler(commands=['speed'])
def cmd_speed(m): _logic_bot_speed(m)

@bot.message_handler(commands=['blocked'])
def cmd_blocked(m): _logic_blocked_files(m)

@bot.message_handler(commands=['unblock'])
def cmd_unblock(m): _logic_unblock_user(m)

@bot.message_handler(commands=['broadcast'])
def cmd_broadcast(m): _logic_broadcast_init(m)

@bot.message_handler(commands=['lock'])
def cmd_lock(m): _logic_toggle_lock_bot(m)

@bot.message_handler(commands=['admin'])
def cmd_admin(m): _logic_admin_panel(m)

# ==================== التنظيف ====================

def cleanup():
    for key, info in bot_scripts.items():
        try:
            if 'process' in info:
                process = info['process']
                try:
                    parent = psutil.Process(process.pid)
                    for child in parent.children(recursive=True):
                        child.terminate()
                    parent.terminate()
                except:
                    process.terminate()
            if 'log_file' in info:
                info['log_file'].close()
        except:
            pass
    logger.info("Cleanup done")

atexit.register(cleanup)

# ==================== التشغيل ====================

if __name__ == '__main__':
    logger.info("🤖 Bot Starting with Full Features...")
    logger.info(f"Owner ID: {OWNER_ID} (exempt from all checks)")
    logger.info(f"Admins: {admin_ids}")
    keep_alive()
    while True:
        try:
            bot.infinity_polling(timeout=60)
        except Exception as e:
            logger.error(f"Polling error: {e}")
            time.sleep(5)