import os, json, random, time, threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup as MK, InlineKeyboardButton as BTN

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
CHANNEL_ID = os.environ.get("CHANNEL_ID", "").strip()
DB_FILE = "questions.json"

if not TOKEN or not OWNER_ID:
    raise ValueError("اضبط BOT_TOKEN و OWNER_ID في متغيرات البيئة")

bot = telebot.TeleBot(TOKEN)

DEFAULT_Q = [
    "كيف كان يومك اليوم من 1 إلى 10؟ وليش؟",
    "أول شيء تعمله بعد الدراسة أو الشغل مباشرة؟",
    "لو عندك ساعة إضافية اليوم بدون التزامات، وين تقضيها؟",
    "عادة بسيطة فرقت معك إيجابياً الفترة الأخيرة؟",
    "شيء تبدأ فيه بحماس ثم يقل حماسك؟ وكيف تتغلب عليه؟",
    "أجمل لحظة مرت عليك هذا الأسبوع؟",
    "لو تقدر تتقن مهارة واحدة خلال شهر، وش تختار؟",
    "كيف تفرّغ ضغط الدراسة أو اليوم الطويل؟",
    "كتاب أو مقطع أو جملة علقت في ذهنك مؤخراً؟",
    "شيء تتمنى تبدأ فيه من بكرة ولا تأجله؟",
]

pending = {}      # {req_id: {"uid": ..., "text": ...}}
user_mode = {}    # {uid: "add_q" | "answer"}
last_q = {}       # {uid: question_text}
auto_cfg = {"enabled": False, "time": ""}  # time format "HH:MM"


def load_q():
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    save_q(DEFAULT_Q[:])
    return DEFAULT_Q[:]


def save_q(q_list):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(q_list, f, ensure_ascii=False, indent=2)


questions = load_q()


def target_ch():
    return int(CHANNEL_ID) if CHANNEL_ID.lstrip("-").isdigit() else CHANNEL_ID


def user_menu():
    m = MK()
    m.add(BTN("🎲 سؤال عشوائي", callback_data="rand"))
    m.add(BTN("➕ اقتراح سؤال", callback_data="ask_add"), BTN("✍️ إرسال إجابة", callback_data="ask_ans"))
    return m


def owner_menu():
    m = MK()
    m.add(BTN("📋 عرض كل الأسئلة", callback_data="list_q"), BTN("🗑 حذف سؤال", callback_data="del_hint"))
    m.add(BTN("📢 نشر سؤال الآن", callback_data="pub_now"), BTN("⚡ نشر تلقائي (كل 24س)", callback_data="auto_24h"))
    m.add(BTN("⏰ ضبط وقت محدد", callback_data="set_time"), BTN("⏹ إيقاف الجدولة", callback_data="stop_auto"))
    return m


def post_to_channel(q_text):
    if not CHANNEL_ID:
        return False, "لم تضبط CHANNEL_ID"
    try:
        bot.send_message(target_ch(), f"💬 **سؤال اليوم**\n\n{q_text}", parse_mode="Markdown")
        return True, "تم النشر بنجاح"
    except Exception as e:
        return False, str(e)


@bot.message_handler(commands=["start", "admin", "list", "del", "time"])
def cmds(msg):
    uid = msg.from_user.id
    txt = msg.text.strip()

    if txt.startswith("/del"):
        if uid != OWNER_ID:
            return
        parts = txt.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].isdigit():
            bot.reply_to(msg, "اكتب: `/del رقم_السؤال`", parse_mode="Markdown")
            return
        idx = int(parts[1]) - 1
        if 0 <= idx < len(questions):
            removed = questions.pop(idx)
            save_q(questions)
            bot.reply_to(msg, f"🗑 تم حذف السؤال #{idx+1}:\n{removed}")
        else:
            bot.reply_to(msg, "رقم السؤال غير موجود.")
        return

    if txt.startswith("/time"):
        if uid != OWNER_ID:
            return
        parts = txt.split(maxsplit=1)
        if len(parts) == 2 and len(parts[1].strip()) == 5 and parts[1].strip()[2] == ":":
            t = parts[1].strip()
            auto_cfg["enabled"] = True
            auto_cfg["time"] = t
            bot.reply_to(msg, f"⏰ تم ضبط النشر اليومي الساعة `{t}`", parse_mode="Markdown")
        else:
            bot.reply_to(msg, "الصيغة: `/time 20:00` (بنظام 24 ساعة)", parse_mode="Markdown")
        return

    if txt == "/list" and uid == OWNER_ID:
        send_all_questions(msg.chat.id)
        return

    if uid == OWNER_ID:
        status = f"مفعل ({auto_cfg['time'] or 'كل 24س'})" if auto_cfg["enabled"] else "متوقف"
        bot.send_message(
            msg.chat.id,
            f"👑 **لوحة المالك** | عدد الأسئلة: `{len(questions)}` | الجدولة: `{status}`",
            reply_markup=owner_menu(),
            parse_mode="Markdown",
        )
    else:
        bot.send_message(msg.chat.id, "أهلاً بك ✨ اختر ما تريد:", reply_markup=user_menu())


def send_all_questions(chat_id):
    if not questions:
        bot.send_message(chat_id, "لا توجد أسئلة حالياً.")
        return
    chunk = ""
    for i, q in enumerate(questions, 1):
        line = f"{i}. {q}\n"
        if len(chunk) + len(line) > 3800:
            bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line
    if chunk:
        bot.send_message(chat_id, f"📋 **الأسئلة الكاملة:**\n\n{chunk}", parse_mode="Markdown")


@bot.callback_query_handler(func=lambda c: True)
def callbacks(c):
    uid = c.from_user.id
    data = c.data

    if data == "rand":
        if not questions:
            bot.answer_callback_query(c.id, "لا توجد أسئلة")
            return
        q = random.choice(questions)
        last_q[uid] = q
        bot.send_message(c.chat.id, f"🎲 **سؤالك:**\n{q}", parse_mode="Markdown")
        bot.answer_callback_query(c.id)

    elif data == "ask_add":
        user_mode[uid] = "add_q"
        bot.send_message(c.chat.id, "✍️ اكتب سؤالك المقترح الآن وسأرسله للمالك للمراجعة:")
        bot.answer_callback_query(c.id)

    elif data == "ask_ans":
        user_mode[uid] = "answer"
        q = last_q.get(uid, questions[0] if questions else "سؤال عام")
        last_q[uid] = q
        bot.send_message(c.chat.id, f"اكتب إجابتك على:\n*{q}*", parse_mode="Markdown")
        bot.answer_callback_query(c.id)

    elif data.startswith("ok_") and uid == OWNER_ID:
        rid = data[3:]
        item = pending.pop(rid, None)
        if item:
            questions.append(item["text"])
            save_q(questions)
            bot.edit_message_text(f"✅ تمت الموافقة وإضافة السؤال (#{len(questions)}):\n{item['text']}", c.message.chat.id, c.message.message_id)
            try:
                bot.send_message(item["uid"], "🎉 تمت الموافقة على سؤالك وإضافته لبنك الأسئلة!")
            except Exception:
                pass
        bot.answer_callback_query(c.id, "تم القبول")

    elif data.startswith("no_") and uid == OWNER_ID:
        rid = data[3:]
        item = pending.pop(rid, None)
        if item:
            bot.edit_message_text(f"❌ تم رفض السؤال:\n{item['text']}", c.message.chat.id, c.message.message_id)
            try:
                bot.send_message(item["uid"], "عذراً، لم يتم اعتماد سؤالك هذه المرة.")
            except Exception:
                pass
        bot.answer_callback_query(c.id, "تم الرفض")

    elif uid == OWNER_ID:
        if data == "list_q":
            send_all_questions(c.message.chat.id)
        elif data == "del_hint":
            bot.send_message(c.message.chat.id, "لإلغاء/حذف سؤال استخدم الأمر:\n`/del رقم_السؤال`\nمثال: `/del 2`", parse_mode="Markdown")
        elif data == "pub_now":
            if not questions:
                bot.answer_callback_query(c.id, "القائمة فارغة")
                return
            q = random.choice(questions)
            ok, info = post_to_channel(q)
            bot.send_message(c.message.chat.id, f"{'✅' if ok else '⚠️'} {info}\nالسؤال: {q}")
        elif data == "auto_24h":
            auto_cfg["enabled"] = True
            auto_cfg["time"] = ""
            bot.send_message(c.message.chat.id, "⚡ تم تفعيل النشر التلقائي كل 24 ساعة.")
        elif data == "set_time":
            bot.send_message(c.message.chat.id, "أرسل وقت النشر اليومي بصيغة `/time HH:MM` مثل:\n`/time 21:00`", parse_mode="Markdown")
        elif data == "stop_auto":
            auto_cfg["enabled"] = False
            auto_cfg["time"] = ""
            bot.send_message(c.message.chat.id, "⏹ تم إيقاف النشر التلقائي/المجدول.")
        bot.answer_callback_query(c.id)


@bot.message_handler(func=lambda m: True)
def on_text(m):
    uid = m.from_user.id
    mode = user_mode.pop(uid, None)

    if mode == "add_q":
        rid = f"{uid}_{int(time.time()*1000)}"
        pending[rid] = {"uid": uid, "text": m.text.strip()}
        mk = MK().add(
            BTN("✅ قبول", callback_data=f"ok_{rid}"),
            BTN("❌ رفض", callback_data=f"no_{rid}"),
        )
        uname = f"@{m.from_user.username}" if m.from_user.username else m.from_user.first_name
        bot.send_message(OWNER_ID, f"📩 **سؤال مقترح من {uname}** (`{uid}`):\n\n{m.text}", reply_markup=mk, parse_mode="Markdown")
        bot.reply_to(m, "⏳ تم إرسال سؤالك للمالك للمراجعة.")
        return

    if mode == "answer":
        q = last_q.get(uid, "سؤال عام")
        uname = f"@{m.from_user.username}" if m.from_user.username else m.from_user.first_name
        bot.send_message(OWNER_ID, f"💬 **إجابة من {uname}**\n❓ {q}\n💭 {m.text}", parse_mode="Markdown")
        bot.reply_to(m, "✅ تم إرسال إجابتك!")
        return

    bot.send_message(m.chat.id, "استخدم الأزرار للبدء:", reply_markup=owner_menu() if uid == OWNER_ID else user_menu())


def scheduler_loop():
    last_fired_min = ""
    last_24h = time.time()
    while True:
        time.sleep(20)
        if not auto_cfg["enabled"] or not questions:
            continue
        if auto_cfg["time"]:
            now_hm = time.strftime("%H:%M")
            if now_hm == auto_cfg["time"] and last_fired_min != now_hm:
                last_fired_min = now_hm
                post_to_channel(random.choice(questions))
        else:
            if time.time() - last_24h >= 86400:
                last_24h = time.time()
                post_to_channel(random.choice(questions))


app = Flask(__name__)


@app.route("/")
def ping():
    return "OK", 200


if __name__ == "__main__":
    threading.Thread(target=scheduler_loop, daemon=True).start()
    threading.Thread(target=bot.infinity_polling, kwargs={"skip_pending": True}, daemon=True).start()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
