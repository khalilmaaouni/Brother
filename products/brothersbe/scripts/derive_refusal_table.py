#!/usr/bin/env python3
"""Recompute the published refusal remainder of the approval identity proof.

A FIGURE PRINTED IN A SHIPPED DOCUMENT IS A CLAIM. This project refuses to
report what it did not examine, and a measurement nobody can re-derive is
exactly the false assurance it exists to prevent: docs/KNOWN-LIMITS.md carried
"Hindi 0 of 45" for a whole release while an independent measurement of the
same pool put it near eighteen percent, because the number was typed once by
hand and the code underneath it moved.

So the numbers in that section are not typed. They are the output of this
script, pasted into the page inside a block marked `derived-by:
scripts/derive_refusal_table.py`, and an eval re-runs this script and fails
when the page and the script disagree. Run it yourself:

    python3 scripts/derive_refusal_table.py            print the block
    python3 scripts/derive_refusal_table.py --check    compare against the page

WHAT IS MEASURED, exactly, so a stranger can judge whether it answers their
question:

  refusal remainder   For each script, every unordered pair of two DIFFERENT
                      real names from a pool of ten (45 pairs), recorded as a
                      name with NO email address, run through the same
                      comparison the approval gate runs. A pair is "unproven"
                      when the gate cannot certify the two are different
                      people. Unproven is not a failure of the person: the gate
                      says so and names the escape (record an email address
                      that differs from the author's), and that escape is
                      exercised in the last column, which re-runs every
                      unproven pair with a distinct email on each side.

  names read as       Every name in both pools through `answered()`, which is
  placeholders        what 29 call sites ask before quoting a field's content.
                      A real name counted here is a person being told their
                      name is a note to self. The published figure must be 0.

The pools are ordinary given and family names, ten per script per pool, two
DISJOINT pools per script so the two measurements do not share inputs. They
name no person this project has worked with.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(REPO, "tools"))

import sbe_checks as checks                                    # noqa: E402
import sbe_gate as gate                                        # noqa: E402

# The date the pools below were last measured on this code. Written down rather
# than read from the clock: a block that changes every midnight would fail its
# own eval for a reason that is not a change in what the tools do.
MEASURED = "2026-07-27"

# Pool A (the pair measurement) and pool B (disjoint, joins A for the reading
# measurement). Ten names each, twelve scripts, 240 names.
POOLS = {
    "Amharic": (
        ["አበበ በቀለ", "ሰላም ታደሰ", "ሙሉጌታ ወርቁ", "ትዕግስት አለሙ", "ገብረ ሚካኤል",
         "ሄለን ደስታ", "ተስፋዬ ገታቸው", "ማርታ ኃይሌ", "ዳዊት አሰፋ", "ብርሃኔ ግርማ"],
        ["ዮሐንስ መኩሪያ", "አልማዝ ተክሌ", "ሲሳይ ደጀኔ", "ፀሐይ በላይ", "ካሳሁን አበራ",
         "መሰረት ጎሳ", "ኤልያስ ሙሉ", "ሮማን ጥላሁን", "ናትናኤል ዘውዱ", "ፍቅርተ አዳነ"]),
    "Arabic": (
        ["محمد الأمين", "فاطمة الزهراء", "أحمد بن علي", "ليلى حسن", "يوسف الخطيب",
         "نور الدين", "سارة المنصوري", "خالد العتيبي", "مريم الشامي", "عمر الفاروق"],
        ["زينب الحداد", "طارق النجار", "هدى السالم", "رامي القاسم", "أمل الحربي",
         "باسم الشريف", "نادية الجابري", "سامي المهدي", "ريم العنزي", "وليد الصبان"]),
    "Armenian": (
        ["Արամ Հակոբյան", "Անահիտ Սարգսյան", "Վահան Գրիգորյան", "Լիլիթ Մկրտչյան",
         "Գևորգ Պետրոսյան", "Նարինե Մարտիրոսյան", "Հայկ Ավետիսյան",
         "Սիրանուշ Ղազարյան", "Տիգրան Բաղդասարյան", "Մարիամ Ոսկանյան"],
        ["Սուրեն Խաչատրյան", "Ազնիվ Համբարձումյան", "Դավիթ Մանուկյան",
         "Գայանե Ստեփանյան", "Ռուբեն Հովհաննիսյան", "Անի Կարապետյան",
         "Սամվել Ասատրյան", "Լուսինե Բարսեղյան", "Արտակ Ղուկասյան", "Զարուհի Մելքոնյան"]),
    "Georgian": (
        ["ბადრი ქუთელია", "ნინო ბერიძე", "გიორგი მაისურაძე", "თამარ ლომიძე",
         "დავით კიკნაძე", "ეკატერინე ჯანელიძე", "ლევან ჩხეიძე", "სოფიო გელაშვილი",
         "ზურაბ თევზაძე", "მარიამ ხარაძე"],
        ["ირაკლი ნოზაძე", "ქეთევან შენგელია", "ვახტანგ აბრამიშვილი", "ლიკა ცქიტიშვილი",
         "ოთარ გოგიაშვილი", "ნანა კვარაცხელია", "ალექსანდრე მამულაშვილი",
         "თეონა ბოლქვაძე", "რევაზ ჩიქოვანი", "მაკა დოლიძე"]),
    "Greek": (
        ["Ελένη Παπαδάκη", "Γιώργος Νικολάου", "Μαρία Δημητρίου", "Νίκος Αντωνίου",
         "Σοφία Γεωργίου", "Κώστας Ιωάννου", "Δήμητρα Βασιλείου", "Ανδρέας Παππάς",
         "Χριστίνα Μακρή", "Θανάσης Λέκκας"],
        ["Ιωάννα Σταύρου", "Πέτρος Καραγιάννης", "Αγγελική Ροδίτη", "Στέλιος Μανώλης",
         "Ευαγγελία Τσάκα", "Μιχάλης Ζαφείρης", "Δέσποινα Κουρή", "Βαγγέλης Ντούλας",
         "Φωτεινή Σπανού", "Λάμπρος Χατζής"]),
    "Hebrew": (
        ["אבי כהן", "נועה לוי", "יוסי מזרחי", "שירה פרץ", "דוד ביטון",
         "מיכל אזולאי", "איתי שפירא", "תמר גולן", "רון אלבז", "ליאת ברוך"],
        ["עומר דהן", "יעל אשכנזי", "ניר חדד", "אורית סבן", "גיל אמסלם",
         "רחל נחום", "אלון פרידמן", "הילה עמר", "ערן שלום", "דנה רוזן"]),
    "Hindi": (
        ["नेहा त्रिवेदी", "राहुल शर्मा", "प्रिया वर्मा", "अमित कुमार", "सुनीता यादव",
         "विकास गुप्ता", "अंजलि सिंह", "संजय मिश्रा", "पूजा जोशी", "मनोज पटेल"],
        ["दीपक चौहान", "कविता रेड्डी", "अरुण नायर", "स्नेहा देसाई", "रवि शंकर",
         "मीरा बंसल", "गौरव सक्सेना", "शालिनी राव", "आदित्य पांडे", "रेखा दुबे"]),
    "Japanese": (
        ["山田太郎", "佐藤花子", "鈴木一郎", "高橋美咲", "田中健二",
         "伊藤さくら", "渡辺翔太", "中村結衣", "小林大輔", "加藤陽子"],
        ["吉田直樹", "山本明日香", "佐々木涼", "松本千尋", "井上拓海",
         "木村美穂", "林勇気", "清水楓", "山口和也", "森田玲奈"]),
    "Korean": (
        ["김민준", "이서연", "박지훈", "최수빈", "정하은",
         "강도현", "조예린", "윤태양", "임소율", "한지우"],
        ["오세훈", "신유나", "서준호", "황보라", "문지호",
         "배수진", "백승우", "남궁민", "홍채원", "전다영"]),
    "Russian": (
        ["Дмитрий Соколов", "Анна Иванова", "Сергей Кузнецов", "Ольга Морозова",
         "Иван Петров", "Мария Волкова", "Алексей Смирнов", "Екатерина Новикова",
         "Николай Лебедев", "Татьяна Козлова"],
        ["Павел Орлов", "Юлия Фёдорова", "Роман Никитин", "Светлана Егорова",
         "Виктор Зайцев", "Надежда Титова", "Артём Белов", "Ксения Громова",
         "Борис Макаров", "Вера Панова"]),
    "Thai": (
        ["สมชาย ใจดี", "สุดา ทองดี", "ประเสริฐ แสงทอง", "มาลี พรหมมา", "วิชัย รัตนกุล",
         "นภา ศรีสุข", "ธีระ วงศ์ไทย", "กมล บุญมี", "อรทัย จันทร์เพ็ญ", "ชาญชัย พูลสุข"],
        ["ปรีชา สมบูรณ์", "ดวงใจ อินทร์ทอง", "สมพงษ์ เกิดผล", "วรรณา ปานทอง",
         "อนันต์ ชูเกียรติ", "พิมพ์ใจ ดำรงค์", "เอกชัย มณีรัตน์", "สุภาพร แก้วใส",
         "ณรงค์ ทวีสุข", "จันทนา บัวทอง"]),
    "Vietnamese": (
        ["Nguyễn Thị Hoa", "Trần Văn Nam", "Lê Minh Tuấn", "Phạm Thu Hà",
         "Hoàng Đức Anh", "Vũ Ngọc the senior reviewer", "Đặng Quốc Bảo", "Bùi Thanh Mai",
         "Đỗ Hữu Phước", "Ngô Kim Chi"],
        ["Dương Bích Ngọc", "Phan Trọng Nghĩa", "Lý Thùy Dương", "Trịnh Hoài Nam",
         "Hồ Xuân Mai", "Cao Bá Long", "Đinh Thị Nhung", "Mai Văn Khánh",
         "Tạ Quang Huy", "Lưu Ngọc Bình"]),
}


def _unproven(a, b, email_a="", email_b=""):
    """True when the gate cannot certify these two identities are different.

    The gate's own comparison, reached through its own parser, so this script
    measures the shipped behavior rather than a paraphrase of it: an email
    difference the host can read is proof and short-circuits, otherwise the
    name sets decide.
    """
    ea, na = gate._rendered_identity_parts(a + ((" <%s>" % email_a) if email_a else ""))
    eb, nb = gate._rendered_identity_parts(b + ((" <%s>" % email_b) if email_b else ""))
    if ea and eb:
        return any(checks.could_render_same(x, y) for x in sorted(ea) for y in sorted(eb))
    return any(checks.name_sets_could_collide(x, y)
               or checks.could_render_same("".join(x), "".join(y))
               for x in na for y in nb)


def measure():
    """[(script, pairs, unproven, percent, unproven after the escape, unproven
    after a same-mailbox escape)], plus the reading measurement, as (rows,
    names read as placeholders, names read)."""
    rows, misread, total = [], [], 0
    for script in sorted(POOLS):
        pool_a, pool_b = POOLS[script]
        pairs = [(pool_a[i], pool_a[j])
                 for i in range(len(pool_a)) for j in range(i + 1, len(pool_a))]
        unproven = [(a, b) for a, b in pairs if _unproven(a, b)]
        # The escape the refusal sentence names, exercised rather than asserted:
        # every unproven pair re-run with a distinct email address on each side.
        escaped = [(a, b) for n, (a, b) in enumerate(unproven)
                   if _unproven(a, b, "approver%d@example.org" % n,
                                "author%d@example.net" % n)]
        # The caveat the column above cannot show by itself: a DIFFERENT-
        # LOOKING email is not always a different mailbox. Re-run the same
        # unproven pairs with the remedy sentence's own words taken literally
        # ("record an email address that differs from the author's") but
        # answered with a gmail dot alias of ONE mailbox on each side, and
        # count how many the gate still correctly declines to certify. This
        # is why the escape column above must never be read as "any two
        # distinct-looking addresses close the remainder": on gmail and
        # googlemail this one does not, by the host's own aliasing, and the
        # gate is right to keep refusing it.
        alias_unescaped = [(a, b) for n, (a, b) in enumerate(unproven)
                           if _unproven(a, b, "first.last%d@gmail.com" % n,
                                        "firstlast%d@gmail.com" % n)]
        rows.append((script, len(pairs), len(unproven),
                     int(round(100.0 * len(unproven) / len(pairs))),
                     len(escaped), len(alias_unescaped)))
        for name in pool_a + pool_b:
            total += 1
            if checks.answered(name) is None:
                misread.append((script, name))
    return rows, misread, total


def block():
    """The exact text the shipped page must carry."""
    rows, misread, total = measure()
    out = [
        "Recomputed by scripts/derive_refusal_table.py on %s." % MEASURED,
        "Pools of 10 real names per script, 45 unordered pairs each, name only,",
        "no email address recorded. \"Unproven\" means the gate declines to certify",
        "the two are different people and says so; it is never a silent pass.",
        "",
        "script        pairs  unproven  percent  still unproven with distinct emails"
        "  still unproven with a gmail dot-alias",
    ]
    for script, pairs, unproven, pct, escaped, alias_unescaped in rows:
        out.append("%-12s %6d %9d %8d  %34d  %37d"
                   % (script, pairs, unproven, pct, escaped, alias_unescaped))
    out += [
        "",
        "Real names read as placeholders by the vacuity backstop: %d of %d names"
        % (len(misread), total),
        "across %d scripts, two disjoint pools of 10 per script." % len(rows),
    ]
    if misread:
        out.append("Misread: " + ", ".join("%s %s" % (s, n) for s, n in misread[:8]))
    return "\n".join(out)


# The pages that carry a block derived by this script. Named here as well as
# marked in the page, so a page that silently DROPS its derived block is a
# failure rather than a vacuous pass: the checker below reports a page that
# carries no block at all, and absence never reads as agreement.
DERIVED_PAGES = ("docs/KNOWN-LIMITS.md",)
MARKER = "derived-by: scripts/derive_refusal_table.py"


def published(text):
    """The derived block a page carries, or None when it carries none."""
    lines = text.splitlines()
    for n, line in enumerate(lines):
        if MARKER not in line:
            continue
        rest = lines[n + 1:]
        while rest and not rest[0].strip():
            rest.pop(0)
        if not rest or not rest[0].startswith("```"):
            return None
        body = []
        for l in rest[1:]:
            if l.startswith("```"):
                return "\n".join(body)
            body.append(l)
        return None
    return None


def check():
    """0 when every derived page agrees with a fresh measurement."""
    want = block()
    problems = []
    for rel in DERIVED_PAGES:
        path = os.path.join(REPO, rel)
        try:
            text = open(path, encoding="utf-8").read()
        except OSError as e:
            problems.append("%s could not be read (%s), so nothing was compared"
                            % (rel, e.strerror or e))
            continue
        got = published(text)
        if got is None:
            problems.append("%s carries no block marked `%s`, so its figures are "
                            "typed rather than derived" % (rel, MARKER))
        elif got.rstrip() != want.rstrip():
            problems.append("%s disagrees with a fresh measurement" % rel)
    for p in problems:
        print("STALE: %s" % p)
    if not problems:
        print("every derived block matches a fresh measurement")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv[1:] else (print(block()) or 0))
