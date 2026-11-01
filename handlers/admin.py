from __future__ import annotations

from datetime import date

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy import func, select

from config import settings
from db import SessionLocal
from models import Course, Lead, PendingReply, Receipt, ReceiptStatus, ReplyStatus
from services import (
    KEY_BUSINESS_CONNECTION,
    KEY_RECEIPTS_GROUP,
    KEY_REQUISITES,
    add_faq,
    del_faq,
    del_link,
    get_setting,
    list_faq,
    list_links,
    set_link,
    set_setting,
)
from states import AddCourse, AddFaq

router = Router(name="admin")


def _is_admin(message: Message) -> bool:
    return bool(message.from_user and message.from_user.id in settings.admin_ids)


@router.message(Command("id", "whoami"))
async def cmd_id(message: Message) -> None:
    """Открыто всем — помогает узнать Telegram ID (для настройки NASIBA_CHAT_ID)."""
    await message.answer(f"Sizning Telegram ID: `{message.from_user.id}`", parse_mode="Markdown")


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    if not _is_admin(message):
        # Если боту в НОРМАЛЬНЫЙ чат пишет педагог — вежливо направим в личку Насибы.
        await message.answer("Assalomu alaykum! Nasibaga yozing, men yordam beraman 🙏")
        return
    await message.answer(
        "Salom! Aloqadaman 🤝\n\n"
        "Lichkangizda nima qilaman:\n"
        "• pedagoglarning kurslar bo'yicha savollariga javob beraman;\n"
        "• cheklarni qabul qilib tekshiraman (dubl, boshqa karta, summa);\n"
        "• bahsli holat va to'lovni tasdiqlash uchun sizga ko'rsataman.\n\n"
        "⚡️ Milliy kursini tez sozlash (bitta buyruq bilan):\n"
        "/setupmilliy — Milliy kursini yaratish (boshlang'ich sinflar)\n"
        "/setprice milliy 300000 — narx\n"
        "/setchannel milliy https://t.me/... — ochiq kanal\n"
        "/setstart milliy 2026-11-15 — boshlanish sanasi (sir)\n"
        "/bindgroup milliy — yopiq guruhni biriktirish (guruh ichida)\n\n"
        "Qolgani:\n"
        "/courses — kurslar (narx, sanalar)\n"
        "/addcourse — kursni qo'lda qo'shish (usta rejim)\n"
        "/requisites — to'lov rekvizitlarini kiritish\n"
        "/bindreceipts — barcha cheklarni arxiv-guruhga yig'ish (guruh ichida yuboring)\n"
        "/faq — tez-tez so'raladigan savol-javoblar (bot ular bilan aqlliroq javob beradi)\n"
        "/links — havolalar (kanal, instagram) — bot so'rovga ko'ra beradi\n"
        "/stats — statistika\n"
        "/id — Telegram ID ni bilish\n\n"
        "📋 Bosqichma-bosqich yo'riqnoma (nima tayyor, nima qoldi): /help"
    )


@router.message(Command("help", "setup", "guide"))
async def cmd_help(message: Message) -> None:
    """Живой чеклист настройки: показывает, что уже готово ✅ и что осталось ⬜️,
    с командой для каждого шага. Нетехническому человеку — идти сверху вниз."""
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        conn = await get_setting(session, KEY_BUSINESS_CONNECTION)
        requisites = await get_setting(session, KEY_REQUISITES)
        receipts_grp = await get_setting(session, KEY_RECEIPTS_GROUP)
        milliy = await _get_course(session, "milliy")

    def mark(ok) -> str:
        return "✅" if ok else "⬜️"

    has_course = milliy is not None
    lines = [
        "🧭 Sozlash yo'riqnomasi — yuqoridan pastga bosqichma-bosqich boring:",
        "",
        f"{mark(conn)} 1. Botni lichkaga ulash",
        "   Telegram sozlamalari → Biznes uchun Telegram → Chat-botlar → botni qo'shing,",
        "   «Xabarlarga javob berish» huquqi bilan.",
        "",
        f"{mark(requisites)} 2. To'lov rekvizitlari",
        "   /requisites 8600 1234 5678 9012, Nasiba X.",
        "",
        f"{mark(has_course)} 3. Milliy kursini yaratish",
        "   /setupmilliy",
        "",
        f"{mark(bool(milliy and milliy.price_sum))} 4. Kurs narxini kiritish",
        "   /setprice milliy 300000",
        "",
        f"{mark(bool(milliy and milliy.channel_url))} 5. Ochiq kanal (bot hammaga beradi)",
        "   /setchannel milliy https://t.me/...",
        "",
        f"{mark(bool(milliy and milliy.start_date))} 6. Boshlanish sanasi (sir) — yoki muddatsiz",
        "   sana: /setstart milliy 2026-11-15   ·   doimiy qabul: /setstart milliy -",
        "",
        f"{mark(bool(milliy and milliy.private_chat_id))} 7. Yopiq guruh (bir martalik havolalar)",
        "   meni yopiq guruhga admin qilib qo'shing va o'sha yerda yuboring: /bindgroup milliy",
        "",
        f"{mark(receipts_grp)} 8. Cheklar arxiv-guruhi (ixtiyoriy)",
        "   yopiq guruh yarating, meni qo'shing va o'sha yerda yuboring: /bindreceipts",
        "",
        "Kurslarni ko'rish: /courses · Statistika: /stats · Bu menyu yana: /help",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("stats"))
async def cmd_stats(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        leads = await session.scalar(select(func.count(Lead.id)))
        pending = await session.scalar(
            select(func.count(PendingReply.id)).where(
                PendingReply.status == ReplyStatus.pending.value
            )
        )
        rows = await session.execute(
            select(Receipt.status, func.count(Receipt.id)).group_by(Receipt.status)
        )
        by_status = {s: c for s, c in rows.all()}
    lines = [
        "📊 Statistika",
        f"Yozgan pedagoglar: {leads or 0}",
        f"Sizning qaroringizni kutmoqda: {pending or 0}",
        "",
        "Cheklar:",
        f"• toza: {by_status.get(ReceiptStatus.clean.value, 0)}",
        f"• tasdiqlangan: {by_status.get(ReceiptStatus.approved.value, 0)}",
        f"• 🚨 dubl: {by_status.get(ReceiptStatus.duplicate.value, 0)}",
        f"• ⚠️ boshqa karta: {by_status.get(ReceiptStatus.wrong_card.value, 0)}",
        f"• rad etilgan: {by_status.get(ReceiptStatus.rejected.value, 0)}",
    ]
    await message.answer("\n".join(lines))


@router.message(Command("requisites"))
async def cmd_requisites(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        if not command.args:
            cur = await get_setting(session, KEY_REQUISITES)
            await message.answer(
                "Pedagoglar uchun joriy rekvizitlar:\n\n"
                + (cur or "(kiritilmagan)")
                + "\n\nO'zgartirish uchun: /requisites <karta va ism matni>"
            )
            return
        await set_setting(session, KEY_REQUISITES, command.args.strip())
        await session.commit()
    await message.answer("✅ Rekvizitlar yangilandi. Endi men ularni pedagoglarga beraman.")


@router.message(Command("links"))
async def cmd_links(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        links = await list_links(session)
    if not links:
        await message.answer(
            "Hali havolalar yo'q.\n"
            "Qo'shish: /addlink <nom> <havola>\n"
            "Masalan: /addlink kanal https://t.me/nasiba"
        )
        return
    out = ["🔗 Havolalar (bot so'rovga ko'ra pedagoglarga beradi):"]
    for name, url in links.items():
        out.append(f"• {name}: {url}")
    out.append("\nQo'shish: /addlink <nom> <havola>\nO'chirish: /dellink <nom>")
    await message.answer("\n".join(out))


@router.message(Command("addlink"))
async def cmd_addlink(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Format: /addlink <nom> <havola>\nMasalan: /addlink kanal https://t.me/nasiba")
        return
    name, url = parts[0], parts[1].strip()
    async with SessionLocal() as session:
        await set_link(session, name, url)
        await session.commit()
    await message.answer(f"✅ «{name}» havolasi saqlandi. Endi bot uni pedagog so'raganda beradi.")


@router.message(Command("dellink"))
async def cmd_dellink(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    name = (command.args or "").strip()
    if not name:
        await message.answer("Format: /dellink <nom>")
        return
    async with SessionLocal() as session:
        ok = await del_link(session, name)
        await session.commit()
    await message.answer("✅ O'chirildi." if ok else "Bunday havola yo'q.")


# --- частые вопросы (FAQ): админ пополняет «словарь» бота ---

@router.message(Command("faq"))
async def cmd_faq(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        faqs = await list_faq(session)
    if not faqs:
        await message.answer(
            "Hali tez-tez so'raladigan savollar yo'q.\n\n"
            "Qo'shish: /addfaq — avval savolni, keyin javobni so'rayman.\n"
            "Shundan keyin bot shunga o'xshash savollarga o'zi chiroyli javob beradi "
            "(Nasibaga yubormaydi)."
        )
        return
    out = ["📖 Tez-tez so'raladigan savol-javoblar (bot ular asosida javob beradi):\n"]
    for f in faqs:
        pic = " 📷" if f.photo_file_id else ""
        body = f.answer or "(rasm)"
        out.append(f"#{f.id}{pic}\n❓ {f.question}\n💬 {body}\n")
    out.append("Qo'shish: /addfaq (rasm ham qo'yish mumkin) · O'chirish: /delfaq <raqam>")
    await message.answer("\n".join(out))


@router.message(Command("addfaq"))
async def cmd_addfaq(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AddFaq.waiting_question)
    await message.answer(
        "❓ Savolni yozing — pedagoglar qanday so'rashi mumkin bo'lsa, shunday "
        "(masalan: «Kurs qancha turadi?» yoki «Qachon boshlanadi?»):"
    )


@router.message(AddFaq.waiting_question)
async def faq_question(message: Message, state: FSMContext) -> None:
    await state.update_data(question=(message.text or "").strip())
    await state.set_state(AddFaq.waiting_answer)
    await message.answer(
        "💬 Endi shu savolga javobni yuboring.\n"
        "• Oddiy matn yozsangiz — bot shu asosda javob beradi.\n"
        "• Rasm/skrin qo'ymoqchi bo'lsangiz — rasmni yuboring (izoh/matnni rasm tagiga "
        "yozsangiz ham bo'ladi). Bot o'sha savolga shu rasmni ko'rsatadi 📷"
    )


@router.message(AddFaq.waiting_answer)
async def faq_answer(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    question = data.get("question", "")
    # Фото/скрин (можно с подписью) ИЛИ обычный текст.
    photo_file_id = message.photo[-1].file_id if message.photo else None
    if photo_file_id:
        answer = (message.caption or "").strip()
    else:
        answer = (message.text or "").strip()
    if not question or (not answer and not photo_file_id):
        await message.answer("Bo'sh javob — bekor qilindi. Qaytadan: /addfaq")
        await state.clear()
        return
    await state.clear()
    async with SessionLocal() as session:
        item = await add_faq(session, question, answer, photo_file_id)
        await session.commit()
        fid = item.id
    extra = " (📷 rasm bilan)" if photo_file_id else ""
    await message.answer(
        f"✅ Saqlandi (#{fid}){extra}. Endi bot shunga o'xshash savollarga o'zi javob beradi.\n"
        "Ro'yxat: /faq"
    )


@router.message(Command("delfaq"))
async def cmd_delfaq(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    raw = (command.args or "").strip().lstrip("#")
    if not raw.isdigit():
        await message.answer("Format: /delfaq <raqam>. Raqamni /faq ro'yxatidan oling.")
        return
    async with SessionLocal() as session:
        ok = await del_faq(session, int(raw))
        await session.commit()
    await message.answer("✅ O'chirildi." if ok else "Bunday savol topilmadi.")


@router.message(Command("bindgroup"))
async def cmd_bindgroup(message: Message, command: CommandObject) -> None:
    """Отправляется ВНУТРИ закрытой группы (где бот — админ). Привязывает группу к курсу,
    чтобы бот выдавал одноразовые инвайт-ссылки именно в неё."""
    if not _is_admin(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "Bu buyruqni yopiq guruh ICHIDA yuborish kerak (men u yerda adminman): "
            "/bindgroup <kurs_kaliti>, masalan /bindgroup milliy"
        )
        return
    key = (command.args or "").strip().lower()
    if not key:
        await message.answer("Kurs kalitini ko'rsating: /bindgroup milliy")
        return
    async with SessionLocal() as session:
        c = (await session.execute(select(Course).where(Course.key == key))).scalar_one_or_none()
        if c is None:
            await message.answer(f"[{key}] kursi topilmadi. Avval qo'shing: /addcourse")
            return
        c.private_chat_id = message.chat.id
        await session.commit()
        name = c.name
    await message.answer(
        f"✅ Bu guruh «{name}» kursiga biriktirildi. Endi to'lovdan keyin men shu yerga "
        f"bir martalik havolalar beraman. Men «Havola orqali taklif qilish» huquqli admin ekanimga ishonch hosil qiling."
    )


@router.message(Command("bindreceipts"))
async def cmd_bindreceipts(message: Message) -> None:
    """Отправляется ВНУТРИ группы-архива (бот должен быть в ней). Запоминает группу,
    куда бот складывает все присланные чеки + данные педагога."""
    if not _is_admin(message):
        return
    if message.chat.type not in ("group", "supergroup"):
        await message.answer(
            "Bu buyruqni cheklar yig'iladigan guruh ICHIDA yuborish kerak "
            "(men o'sha guruhda bo'lishim shart): shunchaki /bindreceipts"
        )
        return
    async with SessionLocal() as session:
        await set_setting(session, KEY_RECEIPTS_GROUP, str(message.chat.id))
        await session.commit()
    await message.answer(
        "✅ Tayyor. Endi har bir yuborilgan chekni pedagog ma'lumotlari bilan shu yerga "
        "joylayman — ko'rinadigan arxiv (baza) bo'ladi.\n\n"
        "⚠️ Bu guruhni yopiq saqlang, minimal odam bilan: bu yerda to'lov ma'lumotlari bor."
    )


@router.message(Command("courses"))
async def cmd_courses(message: Message) -> None:
    if not _is_admin(message):
        return
    async with SessionLocal() as session:
        rows = (await session.execute(select(Course))).scalars().all()
    if not rows:
        await message.answer("Hali kurslar yo'q. Qo'shing: /addcourse")
        return
    out = ["📚 Kurslar:"]
    for c in rows:
        price = f"{c.price_sum:,}".replace(",", " ") + " so'm" if c.price_sum else "narx —"
        mark = "" if c.is_active else " (yashirin)"
        line = f"• [{c.key}] {c.name} — {price}{mark}"
        if c.channel_url:
            line += f"\n   📢 ochiq kanal (hammaga beraman): {c.channel_url}"
        start = c.start_date.isoformat() if c.start_date else "—"
        if c.private_chat_id:
            line += f"\n   🔒 yopiq guruh: bir martalik havolalar YOQILGAN · boshlanish {start}"
        elif c.private_access or c.start_date:
            line += f"\n   🔒 yopiq guruh (to'lovdan keyin): {c.private_access or '—'} · boshlanish {start}"
        out.append(line)
    out.append("\nQo'shish/yangilash: /addcourse")
    await message.answer("\n".join(out))


# --- быстрая настройка курса (по одной команде, без мастера) ---

MILLIY_NAME = "Milliy sertifikat — boshlang'ich sinf o'qituvchilari"
MILLIY_DESC = (
    "Boshlang'ich sinf o'qituvchilarini Milliy sertifikat imtihoniga tayyorlash. "
    "Bitta kurs, imtihonning 3 blokini ham qamrab oladi: "
    "1) Ona tili va o'qish savodxonligi; "
    "2) Matematika va metodika; "
    "3) Pedagogik mahorat (tabiiy fanlar + pedagogika). "
    "Imtihon formati: 90 savol. Boshlang'ich sinf o'qituvchilari uchun."
)


async def _get_course(session, key: str):
    return (
        await session.execute(select(Course).where(Course.key == key))
    ).scalar_one_or_none()


@router.message(Command("setupmilliy"))
async def cmd_setupmilliy(message: Message, command: CommandObject) -> None:
    """Создаёт/обновляет единственный курс Milliy с готовым названием и описанием.
    Цену можно сразу передать: /setupmilliy 300000."""
    if not _is_admin(message):
        return
    digits = "".join(ch for ch in (command.args or "") if ch.isdigit())
    price = int(digits) if digits else None
    async with SessionLocal() as session:
        c = await _get_course(session, "milliy")
        if c is None:
            c = Course(key="milliy", name=MILLIY_NAME, description=MILLIY_DESC, is_active=True)
            session.add(c)
        else:
            c.name = MILLIY_NAME
            c.description = MILLIY_DESC
            c.is_active = True
        if price is not None:
            c.price_sum = price
        await session.commit()
        cur_price = c.price_sum
    price_line = f"{cur_price:,}".replace(",", " ") + " so'm" if cur_price else "kiritilmagan ⚠️"
    await message.answer(
        "✅ «Milliy sertifikat (boshlang'ich sinflar)» kursi tayyor.\n\n"
        f"Narx: {price_line}\n\n"
        "To'ldirish qoldi (har biri bitta buyruq):\n"
        "• /setprice milliy 300000 — narx (chek summasini solishtirish uchun kerak)\n"
        "• /setchannel milliy https://t.me/... — ochiq kanal (bot hammaga beradi)\n"
        "• /setstart milliy 2026-11-15 — boshlanish sanasi (sir, bot to'lovdan keyin aytadi)\n"
        "• /bindgroup milliy — yopiq guruh ICHIDA yuboring (men u yerda adminman)\n\n"
        "Tekshirish: /courses"
    )


@router.message(Command("setprice"))
async def cmd_setprice(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer("Format: /setprice <kalit> <summa>\nMasalan: /setprice milliy 300000")
        return
    key = parts[0].lower()
    digits = "".join(ch for ch in parts[1] if ch.isdigit())
    if not digits:
        await message.answer("Summa raqam bo'lishi kerak. Masalan: /setprice milliy 300000")
        return
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"[{key}] kursi topilmadi. Avval /setupmilliy yoki /addcourse.")
            return
        c.price_sum = int(digits)
        await session.commit()
        name = c.name
    pretty = f"{int(digits):,}".replace(",", " ")
    await message.answer(f"✅ «{name}» kursi narxi: {pretty} so'm. Endi chek summasini shunga qarab tekshiraman.")


@router.message(Command("setchannel"))
async def cmd_setchannel(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            "Format: /setchannel <kalit> <havola>\nMasalan: /setchannel milliy https://t.me/nasiba"
        )
        return
    key, url = parts[0].lower(), parts[1].strip()
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"[{key}] kursi topilmadi. Avval /setupmilliy yoki /addcourse.")
            return
        c.channel_url = url
        await session.commit()
        name = c.name
    await message.answer(f"✅ «{name}» kursining ochiq kanali kiritildi. Bot uni qiziqtirish uchun hammaga beradi.")


@router.message(Command("setstart"))
async def cmd_setstart(message: Message, command: CommandObject) -> None:
    if not _is_admin(message):
        return
    parts = (command.args or "").split()
    if len(parts) < 2:
        await message.answer(
            "Format: /setstart <kalit> <YYYY-MM-DD>\nMasalan: /setstart milliy 2026-11-15\n"
            "Yoki /setstart milliy - — kursni MUDDATSIZ qilish (doimiy qabul)."
        )
        return
    key = parts[0].lower()
    clear = parts[1].lower() in {"-", "нет", "0", "yoq", "yo'q", "doimiy", "бессрочно"}
    start = None
    if not clear:
        try:
            start = date.fromisoformat(parts[1])
        except ValueError:
            await message.answer(
                "Sana YYYY-MM-DD formatida, yoki muddatsiz kurs uchun «-».\n"
                "Masalan: /setstart milliy 2026-11-15"
            )
            return
    async with SessionLocal() as session:
        c = await _get_course(session, key)
        if c is None:
            await message.answer(f"[{key}] kursi topilmadi. Avval /setupmilliy yoki /addcourse.")
            return
        c.start_date = start
        await session.commit()
        name = c.name
    if start:
        await message.answer(
            f"✅ «{name}» boshlanish sanasi: {start.isoformat()}. Bu SIR — bot uni "
            "faqat to'lov tasdiqlangach aytadi."
        )
    else:
        await message.answer(
            f"✅ «{name}» kursi — MUDDATSIZ (doimiy qabul). Bot istalgan vaqtda qo'shilish "
            "mumkinligini aytadi va kursni yangi pedagoglarga tavsiya qilishda davom etadi."
        )


# --- добавление курса пошагово ---

@router.message(Command("addcourse"))
async def cmd_addcourse(message: Message, state: FSMContext) -> None:
    if not _is_admin(message):
        return
    await state.set_state(AddCourse.waiting_key)
    await message.answer("Kursning qisqa kaliti (lotincha, masalan milliy):")


@router.message(AddCourse.waiting_key)
async def ac_key(message: Message, state: FSMContext) -> None:
    await state.update_data(key=message.text.strip().lower())
    await state.set_state(AddCourse.waiting_name)
    await message.answer("Kurs nomi:")


@router.message(AddCourse.waiting_name)
async def ac_name(message: Message, state: FSMContext) -> None:
    await state.update_data(name=message.text.strip())
    await state.set_state(AddCourse.waiting_price)
    await message.answer("Narx so'mda (faqat raqam, yoki 0 — ko'rsatmaslik uchun):")


@router.message(AddCourse.waiting_price)
async def ac_price(message: Message, state: FSMContext) -> None:
    digits = "".join(ch for ch in (message.text or "") if ch.isdigit())
    await state.update_data(price=int(digits) if digits else 0)
    await state.set_state(AddCourse.waiting_start)
    await message.answer(
        "YOPIQ guruh boshlanish sanasi YYYY-MM-DD formatida (sir — bot uni faqat "
        "to'lovdan keyin aytadi), yoki «-»:"
    )


@router.message(AddCourse.waiting_start)
async def ac_start(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    start = None
    if raw and raw != "-":
        try:
            start = date.fromisoformat(raw)
        except ValueError:
            await message.answer("Sanaga o'xshamaydi. YYYY-MM-DD yoki «-» ni qaytaring:")
            return
    await state.update_data(start=start.isoformat() if start else None)
    await state.set_state(AddCourse.waiting_group)
    await message.answer("Guruh / toifa (yoki «-»):")


@router.message(AddCourse.waiting_group)
async def ac_group(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(group=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_channel)
    await message.answer(
        "Yo'nalishning OCHIQ kanaliga havola (bot uni qiziqtirish uchun hammaga beradi), yoki «-»:"
    )


@router.message(AddCourse.waiting_channel)
async def ac_channel(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(channel=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_private)
    await message.answer(
        "YOPIQ guruhga havola (bot uni FAQAT to'lov tasdiqlangach beradi), yoki «-»:"
    )


@router.message(AddCourse.waiting_private)
async def ac_private(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    await state.update_data(private=None if raw == "-" else raw)
    await state.set_state(AddCourse.waiting_desc)
    await message.answer("Qisqa tavsif (nima kiradi, kimga, davomiyligi) yoki «-»:")


@router.message(AddCourse.waiting_desc)
async def ac_desc(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()
    raw = (message.text or "").strip()
    desc = "" if raw == "-" else raw
    start = date.fromisoformat(data["start"]) if data.get("start") else None
    async with SessionLocal() as session:
        existing = (
            await session.execute(select(Course).where(Course.key == data["key"]))
        ).scalar_one_or_none()
        if existing:
            existing.name = data["name"]
            existing.price_sum = data["price"]
            existing.start_date = start
            existing.group_label = data.get("group")
            existing.channel_url = data.get("channel")
            existing.private_access = data.get("private")
            existing.description = desc
            existing.is_active = True
        else:
            session.add(Course(
                key=data["key"], name=data["name"], price_sum=data["price"],
                start_date=start, group_label=data.get("group"),
                channel_url=data.get("channel"), private_access=data.get("private"),
                description=desc,
            ))
        await session.commit()
    await message.answer(f"✅ «{data['name']}» kursi saqlandi. Tekshiring: /courses")
