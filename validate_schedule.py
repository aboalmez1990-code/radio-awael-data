#!/usr/bin/env python3
"""
مدقّق شبكة برامج راديو الأوائل
==============================
يفحص schedule.json قبل نشره ويمنع رفع ملف يكسر التطبيق.

هذا المدقّق هو الحماية الأساسية لهذه المعمارية: بما أن قاعدة البيانات
ملف نصّي يحرّره إنسان، فخطأ فاصلة واحد يُفرغ صفحة البرامج لكل
المستخدمين. تشغيله تلقائياً عند كل تعديل يحوّل أهشّ نقطة في النظام
إلى نقطة محمية.

التشغيل محلياً:  python validate_schedule.py
                python validate_schedule.py path/to/schedule.json
"""

import json
import sys
import re
from datetime import date, datetime

VALID_DAYS  = {"sun", "mon", "tue", "wed", "thu", "fri", "sat"}
VALID_TYPES = {"cancelled", "replaced", "special"}
TIME_RE     = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
DATE_RE     = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# python weekday(): 0=الإثنين … 6=الأحد
WEEKDAY_KEYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]

errors   = []
warnings = []


def err(msg):
    errors.append(msg)


def warn(msg):
    warnings.append(msg)


def check_time(value, where):
    if not isinstance(value, str) or not TIME_RE.match(value.strip()):
        err(f"{where}: وقت غير صالح {value!r} — الصيغة المطلوبة HH:mm بنظام 24 ساعة")
        return None
    h, m = value.strip().split(":")
    return int(h) * 60 + int(m)


def main(path="schedule.json"):
    # ── ١. الملف يُقرأ ويُحلَّل ──────────────────────────────────
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except FileNotFoundError:
        print(f"✗ الملف غير موجود: {path}")
        return 1

    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"✗ JSON غير صالح في السطر {e.lineno} العمود {e.colno}: {e.msg}")
        print("  الأسباب الشائعة: فاصلة زائدة قبل ] أو }، أو علامة تنصيص ناقصة.")
        return 1

    if not isinstance(doc, dict):
        print("✗ الجذر يجب أن يكون كائناً { } لا قائمة")
        return 1

    # ── ٢. الحقول العامة ───────────────────────────────────────
    if "programs" not in doc or not isinstance(doc["programs"], list):
        err("الحقل programs مفقود أو ليس قائمة")
        report()
        return 1

    if not doc["programs"]:
        err("قائمة programs فارغة — التطبيق سيتجاهل هذا الملف ويعود للنسخة المدمجة")

    if "timeZone" not in doc:
        warn("لا يوجد timeZone — سيُستخدم utcOffsetHours أو +3 افتراضاً")

    if "updatedAt" in doc and doc["updatedAt"]:
        try:
            datetime.fromisoformat(str(doc["updatedAt"]).replace("Z", "+00:00"))
        except ValueError:
            warn(f"updatedAt غير قابل للتحليل: {doc['updatedAt']!r}")

    # ── ٣. البرامج ─────────────────────────────────────────────
    ids = {}
    # (program_id, weekday_key) لكل موعد فعلي — نستخدمه للتحقق من الاستثناءات
    slot_index = set()

    for i, p in enumerate(doc["programs"]):
        tag = f"programs[{i}]"
        if not isinstance(p, dict):
            err(f"{tag}: ليس كائناً")
            continue

        pid = p.get("id")
        if not pid or not isinstance(pid, str) or not pid.strip():
            err(f"{tag}: الحقل id مفقود أو فارغ")
        else:
            pid = pid.strip()
            tag = f"البرنامج «{p.get('name', pid)}»"
            if pid in ids:
                err(f"{tag}: المعرّف id مكرّر «{pid}» — سبق استخدامه في programs[{ids[pid]}]")
            ids[pid] = i

        if not p.get("name", "").strip():
            err(f"{tag}: الحقل name مفقود أو فارغ")

        slots = p.get("slots")
        if not isinstance(slots, list) or not slots:
            # برنامج بلا مواعيد لن يظهر أبداً — تحذير لا خطأ
            warn(f"{tag}: بلا مواعيد (slots) — لن يظهر في أي يوم")
            continue

        for j, s in enumerate(slots):
            stag = f"{tag} / الموعد {j + 1}"
            if not isinstance(s, dict):
                err(f"{stag}: ليس كائناً")
                continue

            days = s.get("days")
            if not isinstance(days, list) or not days:
                err(f"{stag}: الحقل days مفقود أو فارغ")
                days = []

            norm_days = []
            for d in days:
                dk = str(d).strip().lower()
                if dk not in VALID_DAYS:
                    err(f"{stag}: يوم غير معروف {d!r} — المسموح: "
                        + " ".join(sorted(VALID_DAYS)))
                else:
                    if dk in norm_days:
                        warn(f"{stag}: اليوم {dk} مذكور مرتين")
                    norm_days.append(dk)

            start = check_time(s.get("start", ""), stag + " (start)")
            end   = check_time(s.get("end", ""),   stag + " (end)")

            if start is not None and end is not None:
                if start == end:
                    err(f"{stag}: وقت البدء والانتهاء متطابقان ({s.get('start')}) — مدة صفر")
                elif end < start:
                    # مسموح ومقصود: يعبر منتصف الليل
                    pass

            if pid:
                for dk in norm_days:
                    slot_index.add((pid, dk))

    # ── ٤. تعارض المواعيد في نفس اللحظة ────────────────────────
    # ليس خطأً بالضرورة (قد يكون مقصوداً) لكن الأغلب أنه سهو
    by_day = {}
    for i, p in enumerate(doc["programs"]):
        if not isinstance(p, dict):
            continue
        for s in p.get("slots") or []:
            if not isinstance(s, dict):
                continue
            st = check_time_silent(s.get("start", ""))
            en = check_time_silent(s.get("end", ""))
            if st is None or en is None:
                continue
            if en <= st:
                en += 24 * 60
            for d in s.get("days") or []:
                dk = str(d).strip().lower()
                if dk in VALID_DAYS:
                    by_day.setdefault(dk, []).append((st, en, p.get("name", "?")))

    for dk, items in by_day.items():
        items.sort()
        for a, b in zip(items, items[1:]):
            if b[0] < a[1]:
                warn(f"تعارض في {dk}: «{a[2]}» ({fmt(a[0])}–{fmt(a[1])}) "
                     f"يتقاطع مع «{b[2]}» ({fmt(b[0])}–{fmt(b[1])})")

    # ── ٥. الاستثناءات ─────────────────────────────────────────
    overrides = doc.get("overrides") or []
    if not isinstance(overrides, list):
        err("الحقل overrides ليس قائمة")
        overrides = []

    for i, ov in enumerate(overrides):
        tag = f"overrides[{i}]"
        if not isinstance(ov, dict):
            err(f"{tag}: ليس كائناً")
            continue

        ds = str(ov.get("date", "")).strip()
        if not DATE_RE.match(ds):
            err(f"{tag}: تاريخ غير صالح {ds!r} — الصيغة المطلوبة yyyy-MM-dd")
            continue

        try:
            d = date.fromisoformat(ds)
        except ValueError:
            err(f"{tag}: تاريخ غير موجود في التقويم: {ds}")
            continue

        dk  = WEEKDAY_KEYS[d.weekday()]
        tag = f"استثناء {ds} ({dk})"

        ty = str(ov.get("type", "")).strip().lower()
        if ty not in VALID_TYPES:
            err(f"{tag}: نوع غير معروف {ov.get('type')!r} — المسموح: "
                + " ".join(sorted(VALID_TYPES)))
            continue

        pid = str(ov.get("programId", "")).strip()
        if not pid:
            err(f"{tag}: الحقل programId مفقود")
            continue
        if pid not in ids:
            err(f"{tag}: البرنامج «{pid}» غير موجود في قائمة programs")
            continue

        if ty in ("cancelled", "replaced"):
            # ⚠️ الفحص الأهم: هل للبرنامج موعد في هذا اليوم من الأسبوع أصلاً؟
            # استثناء لا يطابق شيئاً لا يُنتج خطأً في التطبيق — يُتجاهل بصمت،
            # وهذا أسوأ: تظن أنك ألغيت برنامجاً وهو ما زال معروضاً.
            if (pid, dk) not in slot_index:
                err(f"{tag}: نوع {ty} للبرنامج «{pid}» لكن البرنامج لا يُبثّ "
                    f"يوم {dk} أصلاً — الاستثناء بلا أثر. تحقّق من التاريخ.")

        if ty == "replaced":
            wid = str(ov.get("withProgramId", "")).strip()
            if not wid:
                err(f"{tag}: نوع replaced يحتاج withProgramId")
            elif wid not in ids:
                err(f"{tag}: البرنامج البديل «{wid}» غير موجود")

        if ty == "special":
            st = check_time(ov.get("start", ""), tag + " (start)")
            en = check_time(ov.get("end", ""),   tag + " (end)")
            if st is not None and en is not None and st == en:
                err(f"{tag}: البدء والانتهاء متطابقان — مدة صفر")

        if d < date.today():
            warn(f"{tag}: التاريخ في الماضي — يمكن حذفه لتنظيف الملف")

    report()
    return 1 if errors else 0


def check_time_silent(v):
    if not isinstance(v, str) or not TIME_RE.match(v.strip()):
        return None
    h, m = v.strip().split(":")
    return int(h) * 60 + int(m)


def fmt(mins):
    return f"{(mins // 60) % 24:02d}:{mins % 60:02d}"


def report():
    if warnings:
        print(f"\n⚠️  تنبيهات ({len(warnings)}) — لا تمنع النشر:")
        for w in warnings:
            print(f"   • {w}")

    if errors:
        print(f"\n✗ أخطاء ({len(errors)}) — يجب إصلاحها:")
        for e in errors:
            print(f"   • {e}")
        print("\nلم يُقبل الملف.")
    else:
        print("\n✓ الملف سليم وجاهز للنشر.")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "schedule.json"))
