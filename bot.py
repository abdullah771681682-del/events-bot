import os
import json
import html
import random
import time
import threading
from flask import Flask
import telebot
from telebot.types import InlineKeyboardMarkup as MK, InlineKeyboardButton as BTN

TOKEN = os.environ.get("BOT_TOKEN", "").strip()
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
DEFAULT_CH = os.environ.get("CHANNEL_ID", "").strip()

Q_FILE = "questions.json"
CH_FILE = "user_channels.json"
REACT_FILE = "reactions.json"

if not TOKEN or not OWNER_ID:
    raise ValueError("يرجى ضبط BOT_TOKEN و OWNER_ID في متغيرات البيئة.")

bot = telebot.TeleBot(TOKEN, parse_mode="HTML")

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


def load_json(path, fallback):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    save_json(path, fallback)
    return fallback


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


questions = load_json(Q_FILE, DEFAULT_Q[:])
user_channels = load_json(CH_FILE, {})
reactions_db = load_json(REACT_FILE, {})

user_state = {}      # {uid: {"mode": ..., "target_uid": ..., "post_id": ...}}
last_q = {}          # {uid: question_text}
pending_q = {}       # {rid: {"uid": ..., "text": ...}}
pending_posts = {}   # {pid: {"uid": ..., "text": ..., "channel": ...}}
auto_cfg = {"enabled": False, "time": ""}


def norm_ch(ch):
    ch = (ch or "").strip()
    if not ch:
        return None
    if not ch.startswith("@") and not ch.lstrip("-").isdigit():
        ch = "@" + ch
    return int(ch) if ch.lstrip("-").isdigit() else ch


def get_user_ch(uid):
    return norm_ch(user_channels.get(str(uid), DEFAULT_CH))


def reaction_markup(post_key):
    counts = reactions_db.get(post_key, {"fire": 0, "idea": 0, "talk": 0})
    m = MK()
    m.add(
        BTN(f"🔥 {counts.get('fire', 0)}", callback_data=f"react|{post_key}|fire"),
        BTN(f"💡 {counts.get('idea', 0)}", callback_data=f"react|{post_key}|idea"),
        BTN(f"🤔 {counts.get('talk', 0)}", callback_data=f"react|{post_key}|talk"),
    )
    return m


def publish_content(target, title, body):
    ch = norm_ch(target)
    if not ch:
        return False, "لم يتم تحديد قناة للنشر."
    post_key = f"p_{int(time.time() * 1000)}_{random.randint(10, 99)}"
    reactions_db[post_key] = {"fire": 0, "idea": 0, "talk": 0}
    save_json(REACT_FILE, reactions_db)

    text = f"<b>{html.escape(title)}</b>\n\n{html.escape(body)}"
    try:
        bot.send_message(ch, text, reply_markup=reaction_markup(post_key))
        return True, f"تم النشر بنجاح في {html.escape(str(target))}"
    except Exception:
        reactions_db.pop(post_key, None)
        save_json(REACT_FILE, reactions_db)
        return False, "تعذر النشر؛ تأكد أن البوت مشرف في القناة وله صلاحية إرسال الرسائل."


def user_menu(uid):
    ch_label = user_channels.get(str(uid), "غير مضافة")
    m = MK()
    m.add(BTN("🎲 سؤال عشوائي", callback_data="rand"), BTN("✍️ إرسال إجابة", callback_data="ask_ans"))
    m.add(BTN("➕ اقتراح سؤال", callback_data="ask_add"), BTN("📰 إنشاء منشور", callback_data="make_post"))
    m.add(BTN(f"🔗 قناتي ({ch_label})", callback_data="set_my_ch"), BTN("📢 نشر لقناتي", callback_data="pub_my_ch"))
    m.add(BTN("📩 مراسلة المطور", callback_data="msg_dev"))
    return m


def owner_menu():
    sched = f"مفعل ({auto_cfg['time'] or 'كل 24س'})" if auto_cfg["enabled"] else "متوقف"
    m = MK()
    m.add(BTN("📋 عرض كل الأسئلة", callback_data="list_q"), BTN("➕ إضافة سؤال للمالك", callback_data="owner_add_q"))
    m.add(BTN("🗑 حذف سؤال", callback_data="del_hint"), BTN("📢 نشر سؤال الآن", callback_data="pub_now"))
    m.add(BTN("⚡ تلقائي (24س)", callback_data="auto_24h"), BTN("⏰ ضبط وقت", callback_data="set_time"))
    m.add(BTN("⏹ إيقاف الجدولة", callback_data="stop_auto"), BTN(f"📊 الحالة: {sched}", callback_data="status_info"))
    return m


def send_all_questions(chat_id):
    if not questions:
        bot.send_message(chat_id, "لا توجد أسئلة محفوظة حالياً.")
        return
    chunk = ""
    for i, q in enumerate(questions, 1):
        line = f"{i}. {html.escape(q)}\n"
        if len(chunk) + len(line) > 3800:
            bot.send_message(chat_id, chunk)
            chunk = ""
        chunk += line
    if chunk:
        bot.send_message(chat_id, f"<b>📋 قائمة الأسئلة الكاملة:</b>\n\n{chunk}")


def save_channel_for_user(msg, raw_ch):
    uid = msg.from_user.id
    ch_clean = (raw_ch or "").strip()
    if not ch_clean.startswith("@") and not ch_clean.lstrip("-").isdigit():
        ch_clean = "@" + ch_clean
    ch = norm_ch(ch_clean)
    if not ch:
        bot.reply_to(msg, "⚠️ يوزر أو رقم القناة غير صحيح.")
        return
    try:
        test_msg = bot.send_message(ch, "✅ تم ربط البوت بنجاح لنشر الفعاليات والمنشورات هنا!")
        bot.delete_message(ch, test_msg.message_id)
        user_channels[str(uid)] = ch_clean
        save_json(CH_FILE, user_channels)
        bot.reply_to(
            msg,
            f"🎉 تم ربط قناتك <code>{html.escape(ch_clean)}</code> بنجاح!",
            reply_markup=user_menu(uid),
        )
    except Exception:
        bot.reply_to(
            msg,
            f"⚠️ لم أتمكن من النشر في <code>{html.escape(ch_clean)}</code>.\n"
            "ارفع البوت <b>مشرفاً (Admin)</b> في القناة مع صلاحية إرسال الرسائل، ثم أرسل اليوزر مرة أخرى.",
        )


@bot.message_handler(commands=["start", "help", "admin", "list", "del", "time", "channel"])
def cmds(msg):
    uid = msg.from_user.id
    txt = (msg.text or "").strip()
    user_state.pop(uid, None)

    if txt.startswith("/channel"):
        parts = txt.split(maxsplit=1)
        if len(parts) < 2:
            bot.reply_to(msg, "أرسل يوزر القناة مثل:\n<code>/channel @my_channel</code>")
            return
        save_channel_for_user(msg, parts[1])
        return

    if txt.startswith("/del") and uid == OWNER_ID:
        parts = txt.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].isdigit():
            bot.reply_to(msg, "استخدم: <code>/del رقم_السؤال</code>")
            return
        idx = int(parts[1]) - 1
        if 0 <= idx < len(questions):
            removed = questions.pop(idx)
            save_json(Q_FILE, questions)
            bot.reply_to(msg, f"🗑 تم حذف السؤال #{idx + 1}:\n{html.escape(removed)}")
        else:
            bot.reply_to(msg, "⚠️ رقم السؤال غير موجود.")
        return

    if txt.startswith("/time") and uid == OWNER_ID:
        parts = txt.split(maxsplit=1)
        if len(parts) == 2 and len(parts[1].strip()) == 5 and parts[1].strip()[2] == ":":
            auto_cfg["enabled"] = True
            auto_cfg["time"] = parts[1].strip()
            bot.reply_to(msg, f"⏰ تم ضبط النشر اليومي الساعة <code>{html.escape(auto_cfg['time'])}</code>")
        else:
            bot.reply_to(msg, "الصيغة الصحيحة: <code>/time 20:00</code>")
        return

    if txt == "/list" and uid == OWNER_ID:
        send_all_questions(msg.chat.id)
        return

    if uid == OWNER_ID:
        bot.send_message(
            msg.chat.id,
            f"👑 <b>لوحة المالك</b> | عدد الأسئلة: <code>{len(questions)}</code>",
            reply_markup=owner_menu(),
        )

    bot.send_message(
        msg.chat.id,
        "أهلاً بك في <b>بوت الفعاليات والمنشورات</b> ✨\nاختر الخدمة المطلوبة من الأزرار:",
        reply_markup=user_menu(uid),
    )


@bot.callback_query_handler(func=lambda c: True)
def callbacks(c):
    uid = c.from_user.id
    data = c.data or ""

    if data.startswith("react|"):
        _, post_key, r_type = data.split("|", 2)
        if post_key not in reactions_db:
            reactions_db[post_key] = {"fire": 0, "idea": 0, "talk": 0}
        reactions_db[post_key][r_type] = reactions_db[post_key].get(r_type, 0) + 1
        save_json(REACT_FILE, reactions_db)
        try:
            bot.edit_message_reply_markup(
                c.message.chat.id,
                c.message.message_id,
                reply_markup=reaction_markup(post_key),
            )
        except Exception:
            pass
        bot.answer_callback_query(c.id, "تم تسجيل تفاعلك ✨")
        return

    if data == "rand":
        if not questions:
            bot.answer_callback_query(c.id, "لا توجد أسئلة حالياً")
            return
        q = random.choice(questions)
        last_q[uid] = q
        bot.send_message(c.message.chat.id, f"🎲 <b>سؤال الفعالية:</b>\n\n{html.escape(q)}")
        bot.answer_callback_query(c.id)

    elif data == "ask_ans":
        q = last_q.get(uid, questions[0] if questions else "مشاركة عامة")
        last_q[uid] = q
        user_state[uid] = {"mode": "answer"}
        bot.send_message(c.message.chat.id, f"✍️ اكتب إجابتك على السؤال:\n<i>{html.escape(q)}</i>")
        bot.answer_callback_query(c.id)

    elif data == "ask_add":
        user_state[uid] = {"mode": "suggest_q"}
        bot.send_message(c.message.chat.id, "➕ اكتب سؤالك المقترح لإرساله للمالك للمراجعة:")
        bot.answer_callback_query(c.id)

    elif data == "make_post":
        user_state[uid] = {"mode": "make_post"}
        bot.send_message(
            c.message.chat.id,
            "📰 اكتب نص المنشور الذي تريد تجهيزه وعرضه على المالك لاعتماده أو تعديله:",
        )
        bot.answer_callback_query(c.id)

    elif data == "set_my_ch":
        user_state[uid] = {"mode": "set_ch"}
        bot.send_message(
            c.message.chat.id,
            "🔗 أرسل الآن <b>يوزر قناتك</b> (مثل <code>@channel_name</code>) أو رقمها بعد رفع البوت كمشرف فيها:",
        )
        bot.answer_callback_query(c.id)

    elif data == "pub_my_ch":
        ch = get_user_ch(uid)
        if not ch:
            bot.send_message(c.message.chat.id, "⚠️ لم تربط قناتك بعد! اضغط على <b>قناتي</b> أولاً.")
            bot.answer_callback_query(c.id)
            return
        if not questions:
            bot.answer_callback_query(c.id, "القائمة فارغة")
            return
        q = random.choice(questions)
        last_q[uid] = q
        ok, info = publish_content(ch, "💬 سؤال اليوم", q)
        bot.send_message(c.message.chat.id, f"{'✅' if ok else '⚠️'} {info}\n<b>السؤال:</b> {html.escape(q)}")
        bot.answer_callback_query(c.id)

    elif data == "msg_dev":
        user_state[uid] = {"mode": "msg_dev"}
        bot.send_message(c.message.chat.id, "📩 اكتب رسالتك للمطور وسيتم إيصالها مباشرة:")
        bot.answer_callback_query(c.id)

    elif data.startswith("reply_user|") and uid == OWNER_ID:
        target_uid = int(data.split("|", 1)[1])
        user_state[uid] = {"mode": "reply_user", "target_uid": target_uid}
        bot.send_message(c.message.chat.id, f"↩️ اكتب ردك لإرساله للمستخدم <code>{target_uid}</code>:")
        bot.answer_callback_query(c.id)

    elif data.startswith("q_ok|") and uid == OWNER_ID:
        rid = data.split("|", 1)[1]
        item = pending_q.pop(rid, None)
        if item:
            questions.append(item["text"])
            save_json(Q_FILE, questions)
            bot.edit_message_text(
                f"✅ تم قبول السؤال وإضافته (#{len(questions)}):\n{html.escape(item['text'])}",
                c.message.chat.id,
                c.message.message_id,
            )
            try:
                bot.send_message(item["uid"], "🎉 تمت الموافقة على سؤالك وإضافته إلى بنك الأسئلة!")
            except Exception:
                pass
        bot.answer_callback_query(c.id, "تم القبول")

    elif data.startswith("q_no|") and uid == OWNER_ID:
        rid = data.split("|", 1)[1]
        item = pending_q.pop(rid, None)
        if item:
            bot.edit_message_text(
                f"❌ تم رفض السؤال المقترح:\n{html.escape(item['text'])}",
                c.message.chat.id,
                c.message.message_id,
            )
            try:
                bot.send_message(item["uid"], "عذراً، لم يتم اعتماد سؤالك هذه المرة.")
            except Exception:
                pass
        bot.answer_callback_query(c.id, "تم الرفض")

    elif data.startswith("p_pub|") and uid == OWNER_ID:
        pid = data.split("|", 1)[1]
        post = pending_posts.pop(pid, None)
        if post:
            dest = post.get("channel") or DEFAULT_CH
            ok, info = publish_content(dest, "📰 منشور الفعالية", post["text"])
            bot.edit_message_text(
                f"{'✅' if ok else '⚠️'} {info}\n\n{html.escape(post['text'])}",
                c.message.chat.id,
                c.message.message_id,
            )
            try:
                bot.send_message(post["uid"], f"{'🎉 تم اعتماد ونشر منشورك!' if ok else '⚠️ تعذر النشر: ' + info}")
            except Exception:
                pass
        bot.answer_callback_query(c.id)

    elif data.startswith("p_edit|") and uid == OWNER_ID:
        pid = data.split("|", 1)[1]
        if pid in pending_posts:
            user_state[uid] = {"mode": "edit_post", "post_id": pid}
            bot.send_message(
                c.message.chat.id,
                f"✏️ أرسل النص الجديد للمنشور لاعتماده:\n\n<i>{html.escape(pending_posts[pid]['text'])}</i>",
            )
        bot.answer_callback_query(c.id)

    elif data.startswith("p_no|") and uid == OWNER_ID:
        pid = data.split("|", 1)[1]
        post = pending_posts.pop(pid, None)
        if post:
            bot.edit_message_text(
                f"❌ تم رفض المنشور:\n{html.escape(post['text'])}",
                c.message.chat.id,
                c.message.message_id,
            )
            try:
                bot.send_message(post["uid"], "عذراً، لم يتم اعتماد المنشور حالياً.")
            except Exception:
                pass
        bot.answer_callback_query(c.id, "تم الرفض")

    elif uid == OWNER_ID:
        if data == "list_q":
            send_all_questions(c.message.chat.id)
        elif data == "owner_add_q":
            user_state[uid] = {"mode": "owner_add_q"}
            bot.send_message(c.message.chat.id, "➕ اكتب السؤال الجديد لإضافته مباشرة إلى القائمة:")
        elif data == "del_hint":
            bot.send_message(
                c.message.chat.id,
                "لحذف سؤال استخدم الأمر:\n<code>/del رقم_السؤال</code>\nمثال: <code>/del 3</code>",
            )
        elif data == "pub_now":
            if not questions:
                bot.answer_callback_query(c.id, "القائمة فارغة")
                return
            q = random.choice(questions)
            ok, info = publish_content(DEFAULT_CH, "💬 سؤال اليوم", q)
            bot.send_message(c.message.chat.id, f"{'✅' if ok else '⚠️'} {info}\n<b>السؤال:</b> {html.escape(q)}")
        elif data == "auto_24h":
            auto_cfg["enabled"] = True
            auto_cfg["time"] = ""
            bot.send_message(c.message.chat.id, "⚡ تم تفعيل النشر التلقائي كل 24 ساعة.")
        elif data == "set_time":
            bot.send_message(c.message.chat.id, "⏰ أرسل الوقت بصيغة: <code>/time 20:30</code>")
        elif data == "stop_auto":
            auto_cfg["enabled"] = False
            auto_cfg["time"] = ""
            bot.send_message(c.message.chat.id, "⏹ تم إيقاف الجدولة التلقائية.")
        elif data == "status_info":
            sched = f"مفعل ({auto_cfg['time'] or 'كل 24س'})" if auto_cfg["enabled"] else "متوقف"
            bot.send_message(
                c.message.chat.id,
                f"📊 <b>حالة البوت:</b>\n• عدد الأسئلة: <code>{len(questions)}</code>\n• الجدولة: <code>{html.escape(sched)}</code>\n• القناة الافتراضية: <code>{html.escape(DEFAULT_CH or 'غير محددة')}</code>",
            )
        bot.answer_callback_query(c.id)


@bot.message_handler(func=lambda m: True)
def on_text(m):
    uid = m.from_user.id
    text_in = (m.text or "").strip()
    st = user_state.pop(uid, None)
    mode = st.get("mode") if st else None

    if not text_in:
        bot.reply_to(m, "⚠️ يرجى إرسال نص واضح.")
        return

    uname = f"@{m.from_user.username}" if m.from_user.username else html.escape(m.from_user.first_name)

    if mode == "set_ch":
        save_channel_for_user(m, text_in)
        return

    if mode == "answer":
        q = last_q.get(uid, "مشاركة عامة")
        bot.send_message(
            OWNER_ID,
            f"💬 <b>إجابة جديدة من {uname}</b> (<code>{uid}</code>)\n"
            f"❓ <b>السؤال:</b> {html.escape(q)}\n"
            f"💭 <b>الإجابة:</b> {html.escape(text_in)}",
        )
        bot.reply_to(m, "✅ تم إرسال إجابتك بنجاح!")
        return

    if mode == "suggest_q":
        rid = f"q_{uid}_{int(time.time() * 1000)}"
        pending_q[rid] = {"uid": uid, "text": text_in}
        mk = MK().add(
            BTN("✅ قبول", callback_data=f"q_ok|{rid}"),
            BTN("❌ رفض", callback_data=f"q_no|{rid}"),
        )
        bot.send_message(
            OWNER_ID,
            f"➕ <b>اقتراح سؤال من {uname}</b> (<code>{uid}</code>):\n\n{html.escape(text_in)}",
            reply_markup=mk,
        )
        bot.reply_to(m, "⏳ تم إرسال سؤالك للمالك لمراجعته.")
        return

    if mode == "make_post":
        pid = f"p_{uid}_{int(time.time() * 1000)}"
        user_ch = user_channels.get(str(uid), DEFAULT_CH)
        pending_posts[pid] = {"uid": uid, "text": text_in, "channel": user_ch}
        mk = MK().add(
            BTN("✅ نشر للقناة", callback_data=f"p_pub|{pid}"),
            BTN("✏️ تعديل النص", callback_data=f"p_edit|{pid}"),
            BTN("❌ رفض", callback_data=f"p_no|{pid}"),
        )
        bot.send_message(
            OWNER_ID,
            f"📰 <b>منشور جديد للمراجعة من {uname}</b> (<code>{uid}</code>)\n"
            f"🎯 <b>الوجهة:</b> <code>{html.escape(user_ch or 'القناة الافتراضية')}</code>\n\n"
            f"{html.escape(text_in)}",
            reply_markup=mk,
        )
        bot.reply_to(m, "⏳ تم إرسال منشورك للمالك للمراجعة والاعتماد.")
        return

    if mode == "edit_post" and uid == OWNER_ID:
        pid = st.get("post_id")
        post = pending_posts.pop(pid, None)
        if post:
            dest = post.get("channel") or DEFAULT_CH
            ok, info = publish_content(dest, "📰 منشور الفعالية", text_in)
            bot.send_message(uid, f"{'✅' if ok else '⚠️'} {info}\n\n{html.escape(text_in)}")
            try:
                bot.send_message(post["uid"], f"{'✏️ تم تعديل ونشر منشورك!' if ok else '⚠️ تعذر النشر: ' + info}")
            except Exception:
                pass
        return

    if mode == "owner_add_q" and uid == OWNER_ID:
        questions.append(text_in)
        save_json(Q_FILE, questions)
        bot.reply_to(m, f"✅ تمت إضافة السؤال رقم #{len(questions)} إلى القائمة.")
        return

    if mode == "msg_dev":
        mk = MK().add(BTN("↩️ رد على المستخدم", callback_data=f"reply_user|{uid}"))
        bot.send_message(
            OWNER_ID,
            f"📩 <b>رسالة للمطور من {uname}</b> (<code>{uid}</code>):\n\n{html.escape(text_in)}",
            reply_markup=mk,
        )
        bot.reply_to(m, "✅ وصلت رسالتك للمطور وسيتم الرد عليك قريباً.")
        return

    if mode == "reply_user" and uid == OWNER_ID:
        target_uid = st.get("target_uid")
        try:
            bot.send_message(target_uid, f"💌 <b>رد من المطور:</b>\n\n{html.escape(text_in)}")
            bot.reply_to(m, "✅ تم إرسال ردك للمستخدم.")
        except Exception:
            bot.reply_to(m, "⚠️ تعذر إرسال الرد للمستخدم.")
        return

    bot.send_message(m.chat.id, "اختر من القائمة بالأسفل:", reply_markup=user_menu(uid))


def scheduler_loop():
    last_fired_min = ""
    last_24h = time.time()
    while True:
        time.sleep(20)
        if not auto_cfg["enabled"] or not questions:
            continue
        if 
