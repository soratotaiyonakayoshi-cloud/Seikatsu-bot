"""時間割PDF（工学部・農学部）から科目マスタ JSON を生成する。

使い方:  PYTHONUTF8=1 .venv/Scripts/python data/build_courses.py [--show] [--grep 語]
出力:    data/courses_2026_kouki.json
        [{code, name, teacher, room, faculty, dept, cls, year, slots, term}]

セル構造（pdfplumber の find_tables でセル矩形を取り、単語座標から行を組み立てる）:
  工学部: 科目名(複数行可) / 担当(複数行可) / 教室 コード     …コードが末尾
  農学部: 科目名(複数行可) / コード / 教室 [担当] / 担当         …コードが中間
1セルに複数科目（教養の選択肢・語学の複数クラスなど）が縦に並ぶ。
斜線で左右2科目に割られたセルは、単語のx座標で左右に分割してから解析する。
"""
import json, os, re, sys, unicodedata
import pdfplumber

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = {
    "工": os.path.join(HERE, "src", "2026_kouki_kogakubu.pdf"),
    "農": os.path.join(HERE, "src", "2026_kouki_nogakubu.pdf"),
}
OUT = os.path.join(HERE, "courses_2026_kouki.json")
TERM = "2026後期"

# 時間割コード。工学部=小文字1-2字+4桁（教職は V+4桁、字間に空白が入ることがある）。農学部=英字2字+4桁+クラス小文字(任意)
CODE_RE = {
    "工": re.compile(r"(?<![A-Za-z])(?:[a-z] ?[a-z]? ?\d{4}|V ?\d{4})(?![A-Za-z0-9])"),
    "農": re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{4}[a-z]?(?![A-Za-z0-9])"),
}
# 教室。工学部=L+4桁 など。農学部=「1講─25」「2─22」「本─22」「4─32」など
ROOM_RE = {
    "工": re.compile(r"L\d{4}|グリーンホール|7号館\S*|\d+号館\S*"),
    "農": re.compile(r"[0-9A-Z本新]\S{0,3}[─\-‐－]\S+|L\d{4}|小金井\S*|\d+号館\S*|体育館\S*"),
}
DEPT = {
    "工": {"L": "生命工学科", "B": "生体医用システム工学科", "C": "応用化学科", "U": "化学物理工学科",
          "M": "機械システム工学科", "A": "知能情報システム工学科"},
    "農": {"An": "生物生産学科", "Bn": "応用生物科学科", "En": "環境資源科学科",
          "Rn": "地域生態システム学科", "Vn": "共同獣医学科"},
}
DAYS = "月火水木金土"
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"
CIRC_RE = re.compile(r"^〔(\d+)〕")
NOTE_RE = re.compile(r"^[（(]?注\d*[）)]?$|^※|^[（(]\d+注[）)]$|対象$|クラス指定|^[（(]?★[）)]?$|^[（(]?[◆◇]|^[（(]遠隔[）)]$|^\d※|^教職$|^\(\d+回|^全\d+回|^[()（）]+$")
NAME_HINT = re.compile(r"演習|実験|実習|概論|入門|講義|基礎|応用|化学|物理|数学|英語|工学|生物|環境|科学|ｸﾗｽ|クラス|[ⅠⅡⅢⅣⅤ]|(?:学|論|法|史|語)$|^English|Academic|Writing|Reading"
                       r"|プロジェクト|セミナー|システム|ステップアップ|コミュニケーション|テクノロジー|プログラミング|デザイン|マーケティング|エンジニアリング|バイオ|ケミカル|エネルギー|エレクトロニクス|データ|ネットワーク|アセスメント|リサイクル|コントロール|プロセス|インターンシップ|アントレプレナー|マップ|ビジネス|キャリア|プランニング|体育館|ケーション")
INLINE_NOTE_RE = re.compile(r"[（(]遠隔[）)]|※\d*|\d※|[（(]?★[）)]?|[（(]注\d*[）)]")

def z2h(s):
    """NFKC正規化。丸数字は「5」に潰れてしまうので 〔5〕 の形で保護する。"""
    s = re.sub(r"[①-⑩]", lambda m: f"〔{ord(m.group(0)) - ord('①') + 1}〕", s)
    return unicodedata.normalize("NFKC", s)

def teacherish(line):
    t = INLINE_NOTE_RE.sub("", line).strip()
    if not t:
        return False
    if "教員" in t or t.endswith("ほか") or t in ("未定", "(未定)"):
        return True
    if CIRC_RE.match(t):
        return True
    if NAME_HINT.search(t):
        return False
    core = re.sub(r"[（()）]", "", t)
    parts = [p.strip() for p in re.split(r"[・,、]", core) if p.strip()]
    return bool(parts) and all(re.fullmatch(r"[一-鿿々〆ぁ-んァ-ヶーA-Za-z.]{1,8}", p) for p in parts) and len(core) <= 30

def join_name(lines):
    out = ""
    for l in lines:
        if out and re.search(r"[A-Za-z]$", out) and re.match(r"[A-Za-z]", l):
            out += " "
        out += l
    return out

def clean_name(name):
    name = INLINE_NOTE_RE.sub("", name or "")
    return fix_parens(CIRC_RE.sub("", name).strip())

def clean_teacher(t):
    t = INLINE_NOTE_RE.sub("", t or "")
    t = re.sub(r"〔(\d+)〕", lambda m: CIRCLED[int(m.group(1)) - 1] if 1 <= int(m.group(1)) <= 10 else "", t)
    return fix_parens(t.strip(" ・,、"))

def bad_name(n):
    """解析失敗っぽい科目名（教室記号や担当名が混ざっている）"""
    return (not n) or ("─" in n) or bool(re.match(r"^[（(]", n)) or bool(re.search(r"\s", n) and teacherish(n.split()[0]))

# ------------------------------------------------------------
#  セル解析
# ------------------------------------------------------------
def parse_cell_kogaku(lines):
    """工学部: [科目名…][担当…] 教室 コード"""
    code_re, room_re = CODE_RE["工"], ROOM_RE["工"]
    out, buf, prev_name = [], [], None
    for line in lines:
        codes = code_re.findall(line)
        if not codes:
            buf.append(line)
            continue
        rest = code_re.sub(" ", line)
        rooms = room_re.findall(rest)
        rest = re.sub(r"\s+", " ", room_re.sub(" ", rest)).strip()
        pre = list(buf)
        if rest:
            pre.append(rest)
        rooms += [l for l in pre if room_re.fullmatch(l)]
        pre = [l for l in pre if not room_re.fullmatch(l)]
        teachers = []
        while len(pre) >= 2 and teacherish(pre[-1]) and not pre[-2].endswith(("・", "、")):
            teachers.insert(0, pre.pop())
        if len(pre) == 1 and prev_name and teacherish(pre[0]) and not teachers:
            teachers.insert(0, pre.pop())
            name = prev_name
        else:
            name = clean_name(join_name(pre)) or prev_name or ""
        teacher = clean_teacher("・".join(teachers))
        for c in codes:
            out.append({"name": name, "teacher": teacher, "room": rooms[0] if rooms else "", "code": c.replace(" ", "")})
        prev_name = name or prev_name
        buf = []
    return out

def parse_cell_nogaku(lines):
    """農学部: [科目名…] コード 教室 [担当] [担当…]"""
    code_re, room_re = CODE_RE["農"], ROOM_RE["農"]
    out, buf, cur, prev_name = [], [], None, None
    def post(line):
        nonlocal cur
        if cur is None:
            return False
        m = room_re.match(line)
        if m:
            if not cur["room"]:
                cur["room"] = m.group(0)
            rest = line[m.end():].strip()
            if rest and teacherish(rest) and not cur["teacher"]:
                cur["teacher"] = rest
            return True
        if teacherish(line) and (not cur["teacher"] or line.startswith("・") or cur["teacher"].endswith("・")):
            cur["teacher"] = (cur["teacher"] + line) if cur["teacher"] else line
            return True
        return False
    for line in lines:
        codes = code_re.findall(line)
        if codes:
            first = code_re.search(line)
            before = CIRC_RE.sub("", line[:first.start()]).strip()
            after = re.sub(r"\s+", " ", code_re.sub(" ", line[first.start():])).strip()
            if before and not room_re.match(before) and not teacherish(before):
                buf.append(before)
            name = clean_name(join_name(buf)) or prev_name or ""
            explicit = bool(buf)
            for c in codes:
                cur = {"name": name, "teacher": "", "room": "", "code": c, "_explicit": explicit}
                out.append(cur)
            prev_name = name or prev_name
            buf = []
            if after:
                post(after)
            continue
        if not buf and post(line):
            continue
        buf.append(line)
    # 科目名がコードより後ろに来るレイアウト（左右分割セルの片側など）：余った行を名前として採用
    if buf and out and not out[-1]["_explicit"]:
        nm = clean_name(join_name(buf))
        if nm:
            for e in out:
                if not e["_explicit"] and e["name"] == out[-1]["name"]:
                    e["name"] = nm
    for e in out:
        e["teacher"] = clean_teacher(e["teacher"])
        e.pop("_explicit", None)
    return out

PARSER = {"工": parse_cell_kogaku, "農": parse_cell_nogaku}

# ------------------------------------------------------------
#  セルの文字列化（単語座標→行。左右2科目セルは分割）
# ------------------------------------------------------------
def _cy(w):
    return (w["top"] + w["bottom"]) / 2

def fix_parens(t):
    """行頭・行末に取り残された片カッコを落とす（全角カッコの縦位置ズレ対策）"""
    t = t.replace(")(", "").replace("）（", "")
    opens, closes = t.count("(") + t.count("（"), t.count(")") + t.count("）")
    while t and t[0] in "()（）" and (opens != closes or len(t) == 1):
        t = t[1:]; opens, closes = t.count("(") + t.count("（"), t.count(")") + t.count("）")
    while t and t[-1] in "()（）" and (opens != closes or len(t) == 1):
        t = t[:-1]; opens, closes = t.count("(") + t.count("（"), t.count(")") + t.count("）")
    return t.strip()

def words_to_lines(ws):
    ws = [w for w in ws if w["text"].strip() not in ("(", ")", "（", "）")]
    ws = sorted(ws, key=lambda w: (_cy(w), w["x0"]))
    lines, cur, base = [], [], None
    for w in ws:
        if base is not None and _cy(w) - base > 4.0:
            lines.append(cur)
            cur, base = [], None
        if base is None:
            base = _cy(w)
        cur.append(w)
    if cur:
        lines.append(cur)
    texts = []
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])
        texts.append(fix_parens(" ".join(w["text"] for w in ln)))
    return texts

def clean_lines(texts):
    out = []
    for t in texts:
        t = z2h(t).strip()
        if t and not NOTE_RE.search(t):
            out.append(t)
    return out

def cell_columns(page, bbox, fac):
    """セルbbox → [行リスト, ...]（通常1要素。左右2科目セルは2要素）"""
    try:
        crop = page.crop(bbox, strict=False)
        words = crop.extract_words(x_tolerance=1.5, y_tolerance=3)
    except Exception:
        return []
    bx0, btop, bx1, bbot = bbox
    words = [w for w in words if bx0 - 1 <= (w["x0"] + w["x1"]) / 2 <= bx1 + 1 and btop + 2.5 <= _cy(w) <= bbot + 1]
    if not words:
        return []
    lines = clean_lines(words_to_lines(words))
    code_re, room_re = CODE_RE[fac], ROOM_RE[fac]
    need_split = False
    for ln in lines:
        cs = code_re.findall(ln)
        if len(cs) >= 2:
            need_split = True
        if fac == "農" and cs:
            m = code_re.search(ln)
            if room_re.search(ln[:m.start()]):
                need_split = True
        if fac == "農" and not cs:
            m = room_re.search(ln)
            if m and m.start() > 0 and teacherish(ln[:m.start()].strip()):
                need_split = True  # 「大倉 1講─25 澤ほか」＝2列が混ざった行
    if not need_split and sum(1 for ln in lines if code_re.search(ln)) >= 2:
        # 単純解析で科目名が壊れていれば分割を試す
        if any(bad_name(e["name"]) for e in PARSER[fac](lines)):
            need_split = True
    if need_split:
        def ok(a, b):
            la, lb = clean_lines(words_to_lines(a)), clean_lines(words_to_lines(b))
            if a and b and any(code_re.search(l) for l in la) and any(code_re.search(l) for l in lb):
                return [la, lb]
            return None
        # 1) 縦割り
        for w in sorted(words, key=lambda w: w["x0"]):
            mid = w["x0"] - 0.5
            left = [x for x in words if x["x1"] <= mid + 1]
            right = [x for x in words if x["x0"] >= mid - 1]
            if len(left) + len(right) != len(words):
                continue
            r = ok(left, right)
            if r:
                return r
        # 2) 対角線。2科目は「左上／右下」か「左下／右上」に分かれる。コードの位置関係で向きを決める
        x0, top, x1, bottom = bbox
        W, H = max(x1 - x0, 1), max(bottom - top, 1)
        cw = sorted([w for w in words if code_re.search(z2h(w["text"]))], key=lambda w: w["x0"])
        orients = ["ll", "ul"]
        if len(cw) >= 2 and _cy(cw[0]) > _cy(cw[-1]):
            orients = ["ul", "ll"]  # 左のコードが下 → 左下／右上 → 分ける線は左上→右下
        for orient in orients:
            a, b = [], []
            for w in words:
                cx, cy = (w["x0"] + w["x1"]) / 2, _cy(w)
                t = (cx - x0) / W
                diag_y = (top + t * H) if orient == "ul" else (bottom - t * H)
                (a if cy < diag_y else b).append(w)
            r = ok(a, b)
            if r and not any(bad_name(e["name"]) for side in r for e in PARSER[fac](side)):
                return r
    return [lines]

def cell_text(page, bbox):
    if bbox is None:
        return ""
    try:
        return (page.crop(bbox, strict=False).extract_text() or "").strip()
    except Exception:
        return ""

def header_map(page, table):
    row0, row1 = table.rows[0].cells, table.rows[1].cells
    cols, day = {}, None
    for i, (b0, b1) in enumerate(zip(row0, row1)):
        d = z2h(cell_text(page, b0)).strip()
        if d and d[0] in DAYS and len(d) <= 2:
            day = d[0]
        p = z2h(cell_text(page, b1)).strip()
        if day and re.fullmatch(r"[1-6]", p):
            cols[i] = (day, int(p))
    return cols

def year_of(s):
    s = z2h(s or "").replace("\n", "").replace(" ", "")
    m = re.fullmatch(r"([1-6])年次?", s)
    return int(m.group(1)) if m else None

def extract_timetable(fac):
    recs = []
    with pdfplumber.open(SRC[fac]) as pdf:
        page = pdf.pages[0]
        tables = page.find_tables()
        table = max(tables, key=lambda t: len(t.rows) * max(len(r.cells) for r in t.rows))
        cols = header_map(page, table)
        year, cls = None, None
        for row in table.rows[2:]:
            cells = row.cells
            c0 = cell_text(page, cells[0]) if cells else ""
            y = year_of(c0)
            if y:
                year = y
            elif c0.strip():
                year = None  # 理系教養科目・学部共通・留学生 など
            if fac == "工":
                c = z2h(cell_text(page, cells[2]) if len(cells) > 2 else "").strip()
                if c:
                    cls = c
                dept = DEPT[fac].get(cls[:1], "") if (cls and year) else ""
            else:
                c = z2h(cell_text(page, cells[1]) if len(cells) > 1 else "").strip()
                if c in DEPT[fac]:
                    cls = c
                dept = DEPT[fac].get(cls, "") if (cls and year) else ""
            for i, bbox in enumerate(cells):
                if i not in cols or bbox is None:
                    continue
                day, period = cols[i]
                for col_lines in cell_columns(page, bbox, fac):
                    for e in PARSER[fac](col_lines):
                        recs.append({**e, "faculty": fac, "dept": dept, "cls": cls if year else "", "year": year,
                                     "day": day, "period": period, "term": TERM})
    return recs

def extract_intensive_nogaku():
    """農学部2ページ目：集中講義・不定期開講科目一覧"""
    recs = []
    with pdfplumber.open(SRC["農"]) as pdf:
        if len(pdf.pages) < 2:
            return recs
        for table in pdf.pages[1].extract_tables():
            for row in table:
                cells = [z2h(c or "").replace("\n", " ").strip() for c in row]
                idx = next((i for i, c in enumerate(cells) if CODE_RE["農"].search(c)), None)
                if idx is None:
                    continue
                code = CODE_RE["農"].search(cells[idx]).group(0)
                after = [c for c in cells[idx + 1:] if c]
                name = after[0] if after else ""
                teacher = after[1] if len(after) > 1 and len(after[1]) <= 30 else ""
                ym = re.search(r"([1-6])", cells[1]) if len(cells) > 1 else None
                recs.append({"name": clean_name(name), "teacher": clean_teacher(teacher), "room": "", "code": code,
                             "faculty": "農", "dept": "", "cls": "", "year": int(ym.group(1)) if ym else None,
                             "day": "集中", "period": 0, "term": TERM})
    return recs

def merge(recs):
    by = {}
    for r in recs:
        k = r["code"]
        slot = f"{r['day']}{r['period']}" if r["day"] != "集中" else "集中"
        if k not in by:
            by[k] = {kk: v for kk, v in r.items() if kk not in ("day", "period")}
            by[k]["slots"] = [slot]
        else:
            m = by[k]
            if slot not in m["slots"]:
                m["slots"].append(slot)
            for f in ("name", "teacher", "room", "dept", "cls"):
                if not m[f] and r[f]:
                    m[f] = r[f]
            if not m["year"] and r["year"]:
                m["year"] = r["year"]
    return sorted(by.values(), key=lambda x: (x["faculty"], x["year"] or 9, x["code"]))

def fmt(m):
    return f"  {m['faculty']} {m['year'] or '-'} {m['cls']:3} {m['code']:9} {m['name'][:24]:24} | {m['teacher'][:16]:16} | {m['room']:10} | {','.join(m['slots'])}"

if __name__ == "__main__":
    recs = extract_timetable("工") + extract_timetable("農") + extract_intensive_nogaku()
    merged = merge(recs)
    noname = [m for m in merged if not m["name"]]
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=0)
    nt = sum(1 for m in merged if not m["teacher"])
    print(f"raw={len(recs)} merged={len(merged)} 工={sum(m['faculty']=='工' for m in merged)} "
          f"農={sum(m['faculty']=='農' for m in merged)} 名前なし={len(noname)} 担当なし={nt}")
    if "--show" in sys.argv:
        import random
        random.seed(7)
        for m in random.sample(merged, 45):
            print(fmt(m))
        print("---- 名前なし ----")
        for m in noname[:20]:
            print("  ", m)
    if "--grep" in sys.argv:
        key = sys.argv[sys.argv.index("--grep") + 1]
        for m in merged:
            if key in m["code"] or key in m["name"] or key in m["teacher"]:
                print(fmt(m))
