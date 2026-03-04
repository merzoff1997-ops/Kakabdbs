from telebot import TeleBot
import requests

TELEGRAM_BOT_TOKEN = "7614755054:AAF1hHbplyjWUhNBM654G50X8wO9vVdHK0E"

bot = TeleBot(TELEGRAM_BOT_TOKEN)

API_URL = "https://api.dig.ai/generate"

user_history = {}


def get_ai_response(user_id: int, prompt: str) -> str:
    try:
        history = user_history.get(user_id, [])
        payload = {"prompt": prompt, "history": history}
        headers = {"Content-Type": "application/json"}

        response = requests.post(API_URL, json=payload, headers=headers, timeout=30)
        response.raise_for_status()
        data = response.json()

        answer = data.get("response") or data.get("text") or data.get("result") or str(data)

        history.append({"role": "user", "content": prompt})
        history.append({"role": "assistant", "content": answer})
        user_history[user_id] = history[-20:]

        return answer

    except requests.exceptions.ConnectionError:
        return "❌ Не удалось подключиться к серверу."
    except requests.exceptions.Timeout:
        return "⏳ Сервер не ответил вовремя. Попробуй ещё раз."
    except requests.exceptions.HTTPError as e:
        return f"❌ Ошибка сервера: {e.response.status_code}"
    except Exception as e:
        return f"❌ Ошибка: {e}"


@bot.message_handler(commands=["start", "help"])
def send_welcome(message):
    bot.reply_to(message, (
        "👋 Привет!\n\n"
        "/start — начать\n"
        "/help  — помощь\n"
        "/clear — очистить историю диалога\n\n"
        "Просто напиши что-нибудь — отвечу!"
    ))


@bot.message_handler(commands=["clear"])
def clear_history(message):
    user_history.pop(message.from_user.id, None)
    bot.reply_to(message, "🗑 История диалога очищена!")


@bot.message_handler(func=lambda m: True)
def handle_message(message):
    bot.reply_to(message, "⏳ Думаю...")
    response = get_ai_response(message.from_user.id, message.text)
    bot.reply_to(message, response)


if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling(timeout=60, long_polling_timeout=30)