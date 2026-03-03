"""
OUROBOROS — Автономный Telegram AI бот
Хостинг: bothost.com (Python)
Генерация изображений: Pollinations.ai (бесплатно, без ключа)
"""

import os, time, random, json, logging, urllib.parse
import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, MessageHandler,
    CommandHandler, CallbackQueryHandler,
    filters, ContextTypes
)

TELEGRAM_TOKEN     = os.getenv("TELEGRAM_TOKEN",     "7614755054:AAF1hHbplyjWUhNBM654G50X8wO9vVdHK0E")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "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")
BOT_NAME           = os.getenv("BOT_NAME", "OUROBOROS")
MEMORY_FILE        = "memory.json"

MODELS = [
    "meta-llama/llama-3.3-70b-instruct:free",
    "deepseek/deepseek-r1-0528:free",
    "deepseek/deepseek-v3-0324:free",
    "qwen/qwen3-coder-480b-a35b-instruct:free",
    "qwen/qwen3-next-80b-a3b-instruct:free",
    "nvidia/llama-3.3-nemotron-super-49b-v1:free",
    "nousresearch/hermes-3-llama-3.1-405b:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemma-3-27b-it:free",
    "arcee-ai/trinity-large-preview:free",
    "openai/gpt-oss-120b:free",
    "openai/gpt-oss-20b:free",
    "qwen/qwen3-4b:free",
    "google/gemma-3-4b-it:free",
    "meta-llama/llama-3.2-3b-instruct:free",
    "mistralai/mistral-7b-instruct:free",
]

SYSTEM_PROMPT = f"""Ты — {BOT_NAME}, автономный ИИ-агент в Telegram.
- Отвечай на языке собеседника
- Думай самостоятельно, анализируй
- Помни контекст разговора
- Никогда не говори "не могу" — всегда находи решение
- Если просят картинку — подскажи использовать /image описание"""

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
log = logging.getLogger("bot")

model_cooldowns = {}
histories       = {}
user_settings   = {}
image_count     = {}

def load_memory():
    global histories, user_settings, image_count
    try:
        if os.path.exists(MEMORY_FILE):
            d = json.load(open(MEMORY_FILE))
            histories     = d.get("histories",   {})
            user_settings = d.get("settings",    {})
            image_count   = d.get("image_count", {})
            log.info(f"Память: {len(histories)} диалогов")
    except Exception as e:
        log.warning(f"Память: {e}")

def save_memory():
    try:
        json.dump({"histories": histories, "settings": user_settings,
                   "image_count": image_count},
                  open(MEMORY_FILE, "w"), ensure_ascii=False, indent=2)
    except Exception as e:
        log.warning(f"Сохранение: {e}")

def available_models():
    now = time.time()
    a = [m for m in MODELS if model_cooldowns.get(m, 0) < now]
    return a if a else MODELS

def ban(m, s=120):
    model_cooldowns[m] = time.time() + s

async def ask_ai(messages):
    candidates = available_models()
    pool = candidates[:4]
    random.shuffle(pool)
    for model in pool + candidates[4:8]:
        try:
            async with httpx.AsyncClient(timeout=50) as c:
                r = await c.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                             "Content-Type": "application/json",
                             "HTTP-Referer": "https://t.me/bot",
                             "X-Title": BOT_NAME},
                    json={"model": model, "messages": messages,
                          "max_tokens": 2048, "temperature": 0.7}
                )
            if r.status_code == 429: ban(model, 300); continue
            if r.status_code != 200: ban(model, 120); continue
            choices = (r.json().get("choices") or [])
            if not choices: ban(model, 60); continue
            text = ((choices[0].get("message") or {}).get("content") or "").strip()
            if len(text) < 3: ban(model, 60); continue
            return text, model
        except httpx.TimeoutException: ban(model, 180)
        except Exception as e: log.error(f"{model}: {e}"); ban(model, 60)
    raise RuntimeError("Все модели недоступны, попробуй через минуту")

async def generate_image(prompt, style="realistic"):
    styles = {
        "realistic": "photorealistic, 4k, detailed",
        "art":       "digital art, artstation, beautiful",
        "anime":     "anime style, vibrant, detailed",
        "minimal":   "minimalist, clean, elegant",
    }
    enhanced = f"{prompt}, {styles.get(style, styles['realistic'])}"
    encoded  = urllib.parse.quote(enhanced)
    seed     = random.randint(1, 99999)
    return f"https://image.pollinations.ai/prompt/{encoded}?width=1024&height=1024&seed={seed}&nologo=true"

async def translate_prompt(prompt):
    if all(ord(c) < 128 for c in prompt):
        return prompt
    try:
        text, _ = await ask_ai([
            {"role": "system", "content": "Translate to English for image generation. Return ONLY translated text."},
            {"role": "user", "content": prompt}
        ])
        return text.strip()
    except:
        return prompt

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
    kws = ["нарисуй", "сгенерируй картинку", "создай картинку", "создай изображение",
           "нарисуй мне", "изобрази", "draw me", "generate image", "create image",
           "хочу картинку", "сделай картинку"]
    return any(k in text.lower() for k in kws)

# ── Команды ─────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    name = update.effective_user.first_name or "друг"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Как генерировать картинки?", callback_data="image_help")],
        [InlineKeyboardButton("🧹 Сбросить диалог", callback_data="reset"),
         InlineKeyboardButton("📊 Статус", callback_data="status")],
        [InlineKeyboardButton("⚡ Кратко", callback_data="style_short"),
         InlineKeyboardButton("📝 Подробно", callback_data="style_long")],
    ])
    await update.message.reply_text(
        f"Привет, {name}! Я — *{BOT_NAME}* 🤖\n\n"
        f"*Умею:*\n"
        f"• 💬 Отвечать на любые вопросы\n"
        f"• 🎨 Генерировать изображения: `/image котёнок`\n"
        f"• 🧠 Помнить наш разговор\n"
        f"• 💻 Помогать с кодом и текстами\n\n"
        f"Пиши что угодно!",
        parse_mode="Markdown", reply_markup=kb
    )

async def cmd_reset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid = str(update.effective_chat.id)
    histories.pop(cid, None)
    save_memory()
    await update.message.reply_text("🧹 Диалог сброшен!")

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    avail  = available_models()
    now    = time.time()
    banned = [m for m, t in model_cooldowns.items() if t > now]
    cid    = str(update.effective_chat.id)
    await update.message.reply_text(
        f"📊 *{BOT_NAME} Status*\n\n"
        f"✅ Моделей: `{len(avail)}/{len(MODELS)}`\n"
        f"⏳ На паузе: `{len(banned)}`\n"
        f"🏆 Активная: `{sname(avail[0])}`\n"
        f"🎨 Твоих картинок: `{image_count.get(cid, 0)}`",
        parse_mode="Markdown"
    )

async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f"*{BOT_NAME} — команды*\n\n"
        f"`/start` — главное меню\n"
        f"`/image описание` — сгенерировать картинку\n"
        f"`/reset` — сбросить диалог\n"
        f"`/status` — статус бота\n"
        f"`/style` — стиль ответов\n\n"
        f"*Стили для /image:*\n"
        f"По умолчанию — фотореализм\n"
        f"`--art` — цифровое искусство\n"
        f"`--anime` — аниме стиль\n"
        f"`--minimal` — минимализм\n\n"
        f"*Пример:*\n"
        f"`/image самурай на рассвете --anime`",
        parse_mode="Markdown"
    )

async def cmd_style(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Кратко",      callback_data="style_short"),
         InlineKeyboardButton("📝 Подробно",    callback_data="style_long")],
        [InlineKeyboardButton("😄 Неформально", callback_data="style_casual"),
         InlineKeyboardButton("👔 Формально",   callback_data="style_formal")],
        [InlineKeyboardButton("👨‍💻 Технический", callback_data="style_tech"),
         InlineKeyboardButton("🔄 По умолчанию", callback_data="style_reset")],
    ])
    await update.message.reply_text("Выбери стиль:", reply_markup=kb)

async def cmd_image(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cid    = str(update.effective_chat.id)
    prompt = " ".join(ctx.args) if ctx.args else ""
    if not prompt:
        await update.message.reply_text(
            "🎨 Укажи описание:\n`/image котёнок в космосе`\n"
            "`/image самурай --anime`\n`/image logo --minimal`",
            parse_mode="Markdown"
        )
        return

    style = "realistic"
    for flag, s in [("--art","art"),("--anime","anime"),("--minimal","minimal")]:
        if flag in prompt:
            prompt = prompt.replace(flag, "").strip()
            style  = s
            break

    msg = await update.message.reply_text("🎨 Генерирую...")
    try:
        await ctx.bot.send_chat_action(chat_id=update.effective_chat.id, action="upload_photo")
        en  = await translate_prompt(prompt)
        url = await generate_image(en, style)
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(url)
        if r.status_code == 200:
            image_count[cid] = image_count.get(cid, 0) + 1
            save_memory()
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"regen|{prompt}|{style}")
            ]])
            await msg.delete()
            await ctx.bot.send_photo(
                chat_id=update.effective_chat.id,
                photo=r.content,
                caption=f"🎨 *{prompt}*\n_стиль: {style}_",
                parse_mode="Markdown",
                reply_markup=kb
            )
        else:
            await msg.edit_text("❌ Ошибка. Попробуй другой запрос.")
    except Exception as e:
        log.error(f"Изображение: {e}")
        await msg.edit_text("❌ Не удалось сгенерировать. Попробуй ещё раз.")

# ── Кнопки ──────────────────────────────────────────

STYLES = {
    "style_short":  "Отвечай кратко, 2-3 предложения максимум.",
    "style_long":   "Отвечай подробно с примерами.",
    "style_casual": "Общайся неформально как с другом.",
    "style_formal": "Общайся строго формально.",
    "style_tech":   "Давай технические ответы с кодом.",
    "style_reset":  "",
}
SNAMES = {
    "style_short":"⚡ Краткий","style_long":"📝 Подробный",
    "style_casual":"😄 Неформальный","style_formal":"👔 Формальный",
    "style_tech":"👨‍💻 Технический","style_reset":"🔄 По умолчанию",
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
        avail = available_models()
        await q.edit_message_text(
            f"✅ Моделей: {len(avail)}/{len(MODELS)}\n🏆 `{sname(avail[0])}`",
            parse_mode="Markdown"
        ); return
    if d == "image_help":
        await q.edit_message_text(
            "🎨 *Генерация изображений*\n\n"
            "`/image описание`\n\n*Примеры:*\n"
            "`/image котёнок в космосе`\n"
            "`/image самурай --anime`\n"
            "`/image sunset mountains --art`",
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
        style  = parts[2] if len(parts) > 2 else "realistic"
        await q.answer("🎨 Генерирую новый вариант...")
        try:
            en  = await translate_prompt(prompt)
            url = await generate_image(en, style)
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.get(url)
            if r.status_code == 200:
                image_count[cid] = image_count.get(cid, 0) + 1
                save_memory()
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"regen|{prompt}|{style}")
                ]])
                await ctx.bot.send_photo(
                    chat_id=q.message.chat_id,
                    photo=r.content,
                    caption=f"🎨 *{prompt}* (новый вариант)\n_стиль: {style}_",
                    parse_mode="Markdown", reply_markup=kb
                )
        except Exception as e:
            log.error(f"Regen: {e}")
        return

# ── Сообщения ────────────────────────────────────────

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
            "🎨 Что нарисовать? Напиши:\n`/image описание`",
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
    except:
        await update.message.reply_text(reply)

# ── Запуск ───────────────────────────────────────────

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
