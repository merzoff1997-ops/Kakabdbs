"""
OUROBOROS — Telegram AI бот
Текст: Pollinations.ai (бесплатно, без ключей)
Картинки: Pollinations.ai (бесплатно, без ключей)
Запасной текст: OpenRouter (если есть ключ)
"""

import os, time, random, json, logging, urllib.parse
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler,
    CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)

# ══════════════════════════════════════════════════════
# НАСТРОЙКИ
# ══════════════════════════════════════════════════════

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN",     "7614755054:AAF1hHbplyjWUhNBM654G50X8wO9vVdHK0E")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")  # опционально
BOT_NAME           = os.getenv("BOT_NAME",           "OUROBOROS")
MEMORY_FILE        = "memory.json"

# ══════════════════════════════════════════════════════
# МОДЕЛИ ДЛЯ ТЕКСТА (Pollinations — бесплатно, без ключа)
# ══════════════════════════════════════════════════════

# Pollinations text models (OpenAI-совместимый эндпоинт, ключ не нужен)
POLLINATIONS_TEXT_URL = "https://text.pollinations.ai/openai"
POLLINATIONS_MODELS   = [
    "openai",          # GPT-4o
    "openai-large",    # GPT-4o large
    "gemini",          # Gemini
    "gemini-large",    # Gemini large
    "claude",          # Claude
    "deepseek",        # DeepSeek
    "mistral",         # Mistral
    "llama",           # Llama
    "qwen-coder",      # Qwen Coder
]

# OpenRouter модели (запасные, нужен ключ)
OPENROUTER_MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-v3-0324:free",
    "qwen/qwen3-4b:free",
    "google/gemma-3-4b-it:free",
    "mistralai/mistral-7b-instruct:free",
]

# ══════════════════════════════════════════════════════
# МОДЕЛИ ДЛЯ ИЗОБРАЖЕНИЙ (все бесплатно)
# ══════════════════════════════════════════════════════

IMAGE_PROVIDERS = [
    # Pollinations — несколько моделей
    {"name": "flux",         "url": "https://image.pollinations.ai/prompt/{prompt}?model=flux&width=1024&height=1024&seed={seed}&nologo=true&enhance=true"},
    {"name": "turbo",        "url": "https://image.pollinations.ai/prompt/{prompt}?model=turbo&width=1024&height=1024&seed={seed}&nologo=true"},
    {"name": "grok-imagine", "url": "https://image.pollinations.ai/prompt/{prompt}?model=grok-imagine&width=1024&height=1024&seed={seed}&nologo=true"},
    {"name": "nanobanana",   "url": "https://image.pollinations.ai/prompt/{prompt}?model=nanobanana&width=1024&height=1024&seed={seed}&nologo=true"},
    {"name": "seedream",     "url": "https://image.pollinations.ai/prompt/{prompt}?model=seedream&width=1024&height=1024&seed={seed}&nologo=true"},
]

# ══════════════════════════════════════════════════════
# ПРОМПТ
# ══════════════════════════════════════════════════════

SYSTEM_PROMPT = f"""Ты — {BOT_NAME}, автономный ИИ-агент в Telegram.
- Отвечай на языке собеседника
- Думай самостоятельно, анализируй глубоко
- Помни весь контекст разговора
- Никогда не говори "не могу" — всегда находи решение
- Если просят картинку — скажи использовать /image описание"""

# ══════════════════════════════════════════════════════
# СОСТОЯНИЕ
# ══════════════════════════════════════════════════════

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

model_cooldowns = {}   # model -> unban timestamp
provider_fails  = {}   # provider name -> fail count
histories       = {}
user_settings   = {}
image_count     = {}


# ══════════════════════════════════════════════════════
# ПАМЯТЬ
# ══════════════════════════════════════════════════════

def load_memory():
    global histories, user_settings, image_count
    try:
        if os.path.exists(MEMORY_FILE):
            d = json.load(open(MEMORY_FILE))
            histories     = d.get("histories",   {})
            user_settings = d.get("settings",    {})
            image_count   = d.get("image_count", {})
            log.info(f"Память загружена: {len(histories)} диалогов")
    except Exception as e:
        log.warning(f"Память: {e}")

def save_memory():
    try:
        json.dump(
            {"histories": histories, "settings": user_settings, "image_count": image_count},
            open(MEMORY_FILE, "w"), ensure_ascii=False, indent=2
        )
    except Exception as e:
        log.warning(f"Сохранение: {e}")


# ══════════════════════════════════════════════════════
# ТЕКСТОВЫЙ AI — Pollinations (основной, без ключа)
# ══════════════════════════════════════════════════════

async def ask_pollinations(messages: list) -> tuple[str, str]:
    """Запрашивает Pollinations text API — бесплатно, ключ не нужен."""
    now = time.time()
    avail = [m for m in POLLINATIONS_MODELS if model_cooldowns.get(f"poll_{m}", 0) < now]
    if not avail:
        model_cooldowns.clear()
        avail = POLLINATIONS_MODELS

    for model in avail[:5]:
        try:
            async with httpx.AsyncClient(timeout=45, follow_redirects=True) as c:
                r = await c.post(
                    POLLINATIONS_TEXT_URL,
                    headers={"Content-Type": "application/json"},
                    json={
                        "model":       model,
                        "messages":    messages,
                        "max_tokens":  2048,
                        "temperature": 0.7,
                        "seed":        random.randint(1, 99999),
                    }
                )
            if r.status_code == 429:
                model_cooldowns[f"poll_{model}"] = time.time() + 120
                continue
            if r.status_code != 200:
                model_cooldowns[f"poll_{model}"] = time.time() + 60
                continue

            choices = (r.json().get("choices") or [])
            if not choices:
                continue
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if len(text) < 3:
                continue

            log.info(f"✅ Pollinations/{model} → {len(text)} симв")
            return text, f"pollinations/{model}"

        except httpx.TimeoutException:
            model_cooldowns[f"poll_{model}"] = time.time() + 180
        except Exception as e:
            log.error(f"Pollinations/{model}: {e}")

    raise RuntimeError("pollinations_failed")


async def ask_openrouter(messages: list) -> tuple[str, str]:
    """Запасной вариант через OpenRouter (нужен ключ)."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError("no_openrouter_key")

    now   = time.time()
    avail = [m for m in OPENROUTER_MODELS if model_cooldowns.get(m, 0) < now]
    if not avail:
        avail = OPENROUTER_MODELS

    for model in avail[:4]:
        try:
            async with httpx.AsyncClient(timeout=45) as c:
                r = await c.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                        "Content-Type":  "application/json",
                    },
                    json={"model": model, "messages": messages, "max_tokens": 2048}
                )
            if r.status_code in (401, 403):
                raise RuntimeError("invalid_key")
            if r.status_code != 200:
                model_cooldowns[model] = time.time() + 120
                continue

            choices = (r.json().get("choices") or [])
            if not choices:
                continue
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if len(text) < 3:
                continue

            log.info(f"✅ OpenRouter/{model.split('/')[1]} → {len(text)} симв")
            return text, model.split("/")[1].replace(":free", "")

        except RuntimeError:
            raise
        except Exception as e:
            log.error(f"OpenRouter: {e}")
            model_cooldowns[model] = time.time() + 60

    raise RuntimeError("openrouter_failed")


async def ask_ai(messages: list) -> tuple[str, str]:
    """Пробует Pollinations, потом OpenRouter."""
    try:
        return await ask_pollinations(messages)
    except RuntimeError:
        pass
    try:
        return await ask_openrouter(messages)
    except RuntimeError:
        pass
    raise RuntimeError("Все AI сервисы недоступны. Попробуй через минуту.")


# ══════════════════════════════════════════════════════
# ГЕНЕРАЦИЯ ИЗОБРАЖЕНИЙ
# ══════════════════════════════════════════════════════

async def translate_to_en(text: str) -> str:
    """Переводит промпт на английский через Pollinations."""
    if all(ord(c) < 128 for c in text):
        return text  # уже английский
    try:
        result, _ = await ask_pollinations([
            {"role": "system", "content": "Translate the following to English for image generation. Return ONLY the English translation, nothing else."},
            {"role": "user",   "content": text}
        ])
        return result.strip()
    except Exception:
        return text

async def generate_image(prompt: str, style: str = "flux") -> bytes | None:
    """
    Пробует все image providers по очереди.
    Возвращает байты изображения или None.
    """
    seed = random.randint(1, 999999)
    enc  = urllib.parse.quote(prompt)

    # Стиль влияет на промпт
    style_suffix = {
        "flux":         ", photorealistic, ultra detailed, 4k",
        "turbo":        ", high quality, detailed",
        "grok-imagine": ", creative, artistic",
        "nanobanana":   ", gemini image generation, high quality",
        "seedream":     ", dreamlike, artistic, beautiful",
        "anime":        ", anime style, vibrant colors, studio quality",
        "art":          ", digital art, artstation, concept art",
        "minimal":      ", minimalist, clean design, elegant",
    }

    # Выбираем провайдер
    model = style if style in [p["name"] for p in IMAGE_PROVIDERS] else "flux"

    # Специальные стили → меняем промпт, используем flux
    if style in ("anime", "art", "minimal"):
        prompt = prompt + style_suffix.get(style, "")
        enc    = urllib.parse.quote(prompt)
        model  = "flux"

    # Сортируем: менее проваливавшиеся первыми
    providers = sorted(
        IMAGE_PROVIDERS,
        key=lambda p: provider_fails.get(p["name"], 0)
    )
    # Ставим выбранную модель первой
    providers = sorted(providers, key=lambda p: 0 if p["name"] == model else 1)

    for provider in providers:
        url = provider["url"].format(prompt=enc, seed=seed)
        try:
            log.info(f"🎨 Пробую {provider['name']}: {url[:80]}...")
            async with httpx.AsyncClient(
                timeout=90,
                follow_redirects=True,   # ← КЛЮЧЕВОЙ ФИКС
                headers={"User-Agent": "Mozilla/5.0"}
            ) as c:
                r = await c.get(url)

            log.info(f"   HTTP {r.status_code}, Content-Type: {r.headers.get('content-type','?')}, Size: {len(r.content)}")

            if r.status_code == 200 and len(r.content) > 1000:
                ct = r.headers.get("content-type", "")
                if "image" in ct or r.content[:4] in (b'\xff\xd8\xff\xe0', b'\x89PNG', b'GIF8', b'RIFF'):
                    log.info(f"✅ Изображение получено от {provider['name']} ({len(r.content)} байт)")
                    provider_fails[provider["name"]] = 0
                    return r.content

            provider_fails[provider["name"]] = provider_fails.get(provider["name"], 0) + 1
            log.warning(f"   ❌ {provider['name']}: плохой ответ")

        except httpx.TimeoutException:
            provider_fails[provider["name"]] = provider_fails.get(provider["name"], 0) + 1
            log.warning(f"   ⏱️ {provider['name']}: таймаут")
        except Exception as e:
            provider_fails[provider["name"]] = provider_fails.get(provider["name"], 0) + 1
            log.error(f"   ❌ {provider['name']}: {e}")

    return None


# ══════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════

def get_history(cid):
    if cid not in histories: histories[cid] = []
    return histories[cid]

def trim(h, n=30):
    if len(h) > n: h[:] = h[-n:]

def get_system(cid):
    style = (user_settings.get(cid) or {}).get("style", "")
    return SYSTEM_PROMPT + (f"\nСтиль: {style}" if style else "")

def sname(m):
    return m.split("/")[-1].replace(":free", "")

def is_img_req(text):
    kws = ["нарисуй", "сгенерируй картинку", "создай картинку",
           "создай изображение", "нарисуй мне", "изобрази",
           "draw me", "generate image", "create image",
           "хочу картинку", "сделай картинку", "покажи картинку"]
    return any(k in text.lower() for k in kws)


# ══════════════════════════════════════════════════════
# КОМАНДЫ
# ══════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "друг"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Как генерировать картинки?", callback_data="image_help")],
        [InlineKeyboardButton("🧹 Сбросить диалог", callback_data="reset"),
         InlineKeyboardButton("📊 Статус",          callback_data="status")],
        [InlineKeyboardButton("⚡ Кратко",   callback_data="style_short"),
         InlineKeyboardButton("📝 Подробно", callback_data="style_long")],
    ])
    await update.message.reply_text(
        f"Привет, {name}\\! Я — *{BOT_NAME}* 🤖\n\n"
        f"*Умею:*\n"
        f"• 💬 Отвечать на любые вопросы\n"
        f"• 🎨 Генерировать изображения: `/image кот`\n"
        f"• 🧠 Помнить наш разговор\n"
        f"• 💻 Помогать с кодом и текстами\n\n"
        f"Пиши что угодно\\!",
        parse_mode="MarkdownV2", reply_markup=kb
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    histories.pop(cid, None)
    save_memory()
    await update.message.reply_text("🧹 Диалог сброшен!")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid   = str(update.effective_chat.id)
    fails = {k: v for k, v in provider_fails.items() if v > 0}
    await update.message.reply_text(
        f"📊 *{BOT_NAME} Status*\n\n"
        f"🧠 Текст: Pollinations.ai \\(бесплатно\\)\n"
        f"🎨 Картинки: {len(IMAGE_PROVIDERS)} провайдеров\n"
        f"💬 Диалогов: `{len(histories)}`\n"
        f"🖼️ Твоих картинок: `{image_count.get(cid, 0)}`\n"
        f"⚠️ Проблемных провайдеров: `{len(fails)}`",
        parse_mode="MarkdownV2"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"*{BOT_NAME} — команды*\n\n"
        f"`/image описание` — сгенерировать картинку\n"
        f"`/reset` — сбросить диалог\n"
        f"`/status` — статус\n"
        f"`/style` — стиль ответов\n\n"
        f"*Стили для /image:*\n"
        f"`--anime` — аниме\n"
        f"`--art` — цифровое искусство\n"
        f"`--minimal` — минимализм\n"
        f"`--dream` — мечтательный\n\n"
        f"*Примеры:*\n"
        f"`/image самурай на рассвете --anime`\n"
        f"`/image котёнок в космосе`",
        parse_mode="Markdown"
    )

async def cmd_style(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Кратко",      callback_data="style_short"),
         InlineKeyboardButton("📝 Подробно",    callback_data="style_long")],
        [InlineKeyboardButton("😄 Неформально", callback_data="style_casual"),
         InlineKeyboardButton("👔 Формально",   callback_data="style_formal")],
        [InlineKeyboardButton("👨‍💻 Технически",  callback_data="style_tech"),
         InlineKeyboardButton("🔄 По умолчанию", callback_data="style_reset")],
    ])
    await update.message.reply_text("Выбери стиль:", reply_markup=kb)

async def cmd_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid    = str(update.effective_chat.id)
    prompt = " ".join(ctx.args) if ctx.args else ""

    if not prompt:
        await update.message.reply_text(
            "🎨 Укажи что нарисовать:\n"
            "`/image котёнок в космосе`\n"
            "`/image самурай --anime`\n"
            "`/image logo --minimal`",
            parse_mode="Markdown"
        )
        return

    # Стиль из флага
    style = "flux"
    for flag, s in [("--anime","anime"),("--art","art"),("--minimal","minimal"),("--dream","seedream"),("--turbo","turbo")]:
        if flag in prompt:
            prompt = prompt.replace(flag, "").strip()
            style  = s
            break

    msg = await update.message.reply_text("🎨 Генерирую изображение...")
    try:
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")

        # Переводим на английский
        en_prompt = await translate_to_en(prompt)
        log.info(f"Промпт: '{prompt}' → '{en_prompt}' [{style}]")

        img_bytes = await generate_image(en_prompt, style)

        if img_bytes:
            image_count[cid] = image_count.get(cid, 0) + 1
            save_memory()
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"regen|{prompt[:50]}|{style}"),
                InlineKeyboardButton("🎨 Другой стиль", callback_data=f"restyle|{prompt[:50]}"),
            ]])
            await msg.delete()
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=img_bytes,
                caption=f"🎨 *{prompt}*\n_стиль: {style}_",
                parse_mode="Markdown",
                reply_markup=kb
            )
        else:
            await msg.edit_text(
                "❌ Не удалось сгенерировать изображение.\n\n"
                "Попробуй:\n"
                "• Другое описание\n"
                "• `/image` немного позже\n"
                "• Другой стиль: `--turbo` или `--art`",
                parse_mode="Markdown"
            )
    except Exception as e:
        log.error(f"cmd_image: {e}")
        await msg.edit_text("❌ Ошибка. Попробуй ещё раз через минуту.")


# ══════════════════════════════════════════════════════
# КНОПКИ
# ══════════════════════════════════════════════════════

STYLES = {
    "style_short":  "Отвечай кратко, максимум 2-3 предложения.",
    "style_long":   "Отвечай подробно с примерами.",
    "style_casual": "Общайся неформально как с другом.",
    "style_formal": "Общайся строго формально.",
    "style_tech":   "Давай технические точные ответы с кодом.",
    "style_reset":  "",
}
SNAMES = {
    "style_short":  "⚡ Краткий",
    "style_long":   "📝 Подробный",
    "style_casual": "😄 Неформальный",
    "style_formal": "👔 Формальный",
    "style_tech":   "👨‍💻 Технический",
    "style_reset":  "🔄 По умолчанию",
}

async def handle_button(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    await q.answer()
    cid = str(q.message.chat_id)
    d   = q.data

    if d == "reset":
        histories.pop(cid, None); save_memory()
        await q.edit_message_text("🧹 Диалог сброшен!")
        return

    if d == "status":
        await q.edit_message_text(
            f"🧠 Текст: Pollinations.ai\n"
            f"🎨 Картинки: {len(IMAGE_PROVIDERS)} провайдеров\n"
            f"🖼️ Твоих картинок: {image_count.get(cid, 0)}"
        ); return

    if d == "image_help":
        await q.edit_message_text(
            "🎨 *Генерация изображений*\n\n"
            "`/image описание`\n\n*Примеры:*\n"
            "`/image котёнок в космосе`\n"
            "`/image самурай --anime`\n"
            "`/image sunset mountains --art`\n"
            "`/image logo --minimal`",
            parse_mode="Markdown"
        ); return

    if d in STYLES:
        if cid not in user_settings: user_settings[cid] = {}
        user_settings[cid]["style"] = STYLES[d]; save_memory()
        await q.edit_message_text(f"✅ Стиль: *{SNAMES[d]}*", parse_mode="Markdown")
        return

    if d.startswith("regen|"):
        parts  = d.split("|", 2)
        prompt = parts[1] if len(parts) > 1 else "landscape"
        style  = parts[2] if len(parts) > 2 else "flux"
        await q.answer("🎨 Генерирую новый вариант...")
        try:
            en = await translate_to_en(prompt)
            img_bytes = await generate_image(en, style)
            if img_bytes:
                image_count[cid] = image_count.get(cid, 0) + 1
                save_memory()
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"regen|{prompt}|{style}"),
                    InlineKeyboardButton("🎨 Другой стиль", callback_data=f"restyle|{prompt}"),
                ]])
                await ctx.bot.send_photo(
                    chat_id=q.message.chat_id,
                    photo=img_bytes,
                    caption=f"🎨 *{prompt}* (новый вариант)\n_стиль: {style}_",
                    parse_mode="Markdown", reply_markup=kb
                )
        except Exception as e:
            log.error(f"regen: {e}")
        return

    if d.startswith("restyle|"):
        prompt = d.split("|", 1)[1]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🌟 Flux",   callback_data=f"regen|{prompt}|flux"),
             InlineKeyboardButton("⚡ Turbo",  callback_data=f"regen|{prompt}|turbo")],
            [InlineKeyboardButton("🎌 Аниме",  callback_data=f"regen|{prompt}|anime"),
             InlineKeyboardButton("🎨 Арт",    callback_data=f"regen|{prompt}|art")],
            [InlineKeyboardButton("✨ Dream",   callback_data=f"regen|{prompt}|seedream"),
             InlineKeyboardButton("🤖 Grok",   callback_data=f"regen|{prompt}|grok-imagine")],
        ])
        await q.edit_message_reply_markup(reply_markup=kb)
        return


# ══════════════════════════════════════════════════════
# ГЛАВНЫЙ ОБРАБОТЧИК
# ══════════════════════════════════════════════════════

async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    chat_id   = update.effective_chat.id
    cid       = str(chat_id)
    user_text = (update.message.text or "").strip()
    if not user_text: return

    if is_img_req(user_text):
        for kw in ["нарисуй мне", "нарисуй", "сгенерируй картинку", "создай картинку",
                   "создай изображение", "изобрази", "draw me", "generate image",
                   "хочу картинку", "сделай картинку"]:
            if kw in user_text.lower():
                p = user_text.lower().replace(kw, "").strip(" ,.!?")
                if p:
                    ctx.args = p.split()
                    await cmd_image(update, ctx)
                    return
        await update.message.reply_text(
            "🎨 Что нарисовать?\n`/image описание`",
            parse_mode="Markdown"
        )
        return

    history = get_history(cid)
    history.append({"role": "user", "content": user_text})
    trim(history)
    messages = [{"role": "system", "content": get_system(cid)}] + history

    await ctx.bot.send_chat_action(chat_id=chat_id, action="typing")

    try:
        reply, model = await ask_ai(messages)
    except RuntimeError as e:
        await update.message.reply_text(f"😔 {e}"); return

    history.append({"role": "assistant", "content": reply})
    save_memory()

    footer = f"\n\n_· {sname(model)}_"
    if len(reply) + len(footer) > 4096:
        reply = reply[:4090 - len(footer)] + "..."
    try:
        await update.message.reply_text(reply + footer, parse_mode="Markdown")
    except Exception:
        await update.message.reply_text(reply)


# ══════════════════════════════════════════════════════
# ЗАПУСК
# ══════════════════════════════════════════════════════

def main():
    load_memory()
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("reset",  cmd_reset))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("help",   cmd_help))
    app.add_handler(CommandHandler("style",  cmd_style))
    app.add_handler(CommandHandler("image",  cmd_image))
    app.add_handler(CallbackQueryHandler(handle_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    log.info(f"🤖 {BOT_NAME} запущен!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
