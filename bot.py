"""最低限生活リズムサークル Bot

起床・睡眠・家事・食事・入浴を「ボタン1タップ」で記録し、
毎晩の判定で「自分で決めた最低限」を守れなかった人を #叱責👹 に晒す。
データは SQLite（VM上のファイル）に保存。外部サービス不要。
"""
import discord
from discord import app_commands
from discord.ext import tasks
import aiosqlite
import asyncio
import io
import json
import os
import re
import unicodedata
import zlib
from datetime import datetime, timezone, timedelta, date

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ============================================================
#  設定
# ============================================================
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
DB_PATH = os.getenv("DB_PATH", "seikatsu.db")
JUDGE_HOUR = int(os.getenv("JUDGE_HOUR", "23"))
KORA_EMOJI_NAME = os.getenv("KORA_EMOJI", "こら")
ERAI_EMOJI_NAME = os.getenv("ERAI_EMOJI", "えらい")   # 達成した人に付ける絵文字（無ければ ✨）
RADIO_TIME = os.getenv("RADIO_TIME", "06:30")          # ラジオ体操の開始時刻(HH:MM)
RADIO_MP3 = os.getenv("RADIO_MP3", "radio.mp3")        # 音源ファイル（リポジトリには含めない。VMに直接置く）
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
RADIO_VC_NAME = "ラジオ体操🏃"
# 改名前の旧チャンネル名（/setup が既存チャンネルを見つけて改名するために使う）
OLD_CH_NAMES = {"wake": "起床", "meal": "ごはん", "chore": "家事", "bath": "おふろ", "kora": "こら",
                "kadai": "課題", "tsushinbo": "つうしんぼ", "settei": "設定"}
OLD_RADIO_VC_NAME = "ラジオ体操"
REMIND_HOUR = int(os.getenv("REMIND_HOUR", "8"))     # 課題リマインドを流す時刻（時）
GAKUSHU_URL = os.getenv("GAKUSHU_URL", "https://gakushu-rpg.pages.dev")   # みんなで暗記！！連携先
GAKUSHU_SECRET = os.getenv("GAKUSHU_SECRET", "")     # Cloudflare側 VC_SECRET と同じ値。空なら連携オフ
WEATHER_LAT = float(os.getenv("WEATHER_LAT", "35.68"))   # 朝の天気（Open-Meteo・キー不要）。既定=府中
WEATHER_LON = float(os.getenv("WEATHER_LON", "139.48"))
MEMBERS_INTENT = os.getenv("MEMBERS_INTENT", "0") == "1"   # 1にすると新規参加者を #はじめに📖 で歓迎（Developer PortalでSERVER MEMBERS INTENTをONにすること）
COURSES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses_2026_kouki.json")
JST = timezone(timedelta(hours=9))

CATEGORY_NAME = "最低限生活リズム"
# key -> (チャンネル名, パネルの説明)
CH = {
    "hajimeni": ("はじめに📖", "参加者向けのガイド。困ったら `/help`。"),
    "jikoshokai": ("自己紹介🙋", "📝 ボタンでフォームから自己紹介カードを投稿（あとから更新OK）。"),
    "roles": ("ロール🏷", "リアクションで学部・学年・生活形態のロールを付け外し。"),
    "wake": ("起床🌅", "☀️ 起きたら押す／🌙 寝る前に押す（睡眠時間は自動計算）／😴 二度寝したら正直に押す。\n🏃 で毎朝のラジオ体操の呼び出し（メンション）をON/OFF。"),
    "meal": ("ごはん🍚", "🍚 食べたら押す。**写真を投げるだけ**でも時間帯から自動で記録されます。"),
    "chore": ("家事🧹", "🧹 やった家事を押す。洗濯は5工程に分かれています。"),
    "erai": ("えらい🌟", "最低限とは別に、今日がんばったことを報告して褒め合う場所。🌟ボタン（または /erai）から。判定はされません。"),
    "bath": ("おふろ🛁", "🛁 お風呂に入ったら押す。🪥 歯磨きも（1日に何回でも）。"),
    "kora": ("叱責👹", "毎晩の判定で、最低限を守れなかった人が晒される場所。"),
    "tsushinbo": ("つうしんぼ📮", "毎週日曜の夜に、その週の通信簿（達成率ランキング・各賞）が届く場所。"),
    "kadai": ("課題📚", "`/jikanwari add` で履修科目を登録 → 気づいた人が `/kadai add` → 同じ科目の履修者だけに通知＆リマインド。"),
    "settei": ("設定🔧", "`/saitei` で自分の最低限を決める。`/kojin add` で自分だけの項目を追加し、📝ボタンで毎日チェック。`/oyasumi` でお休み申告。"),
}
CHORES = [  # (key, ラベル, 絵文字, 行)
    ("cook", "料理", "🍳", 0), ("clean", "掃除", "🧹", 0), ("dish", "皿洗い", "🍽️", 0), ("trash", "ごみ捨て", "🗑", 0),
    ("wash_run", "洗濯機を回した", "🫧", 1), ("wash_in", "洗濯物を取り込んだ", "📥", 1),
    ("dry_run", "乾燥機を回した", "🌀", 2), ("dry_in", "乾燥機から取り込んだ", "📤", 2),
    ("hang", "干した", "🧺", 2),
]
CHORE_LABEL = {k: f"{e}{l}" for k, l, e, _ in CHORES}
MEALS = [("朝", "🍚"), ("昼", "🍱"), ("夜", "🍽️"), ("間食", "🍩")]
MEAL_EMOJI = dict(MEALS)
MAIN_MEALS = ("朝", "昼", "夜")

# ============================================================
#  時刻ユーティリティ
# ============================================================
def now_jst():
    return datetime.now(JST)

def day_str(dt):
    return dt.strftime("%Y-%m-%d")

def hhmm(dt):
    return dt.strftime("%H:%M")

def parse_hhmm(s):
    """'7:00' '07:00' '7時30分' '2330' などを 'HH:MM' に正規化。無効なら None。"""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[:：時]?\s*(\d{2})?\s*分?\s*", s or "")
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return f"{h:02d}:{mi:02d}"

def week_range(dt):
    """その日を含む週（月曜〜日曜）の day 文字列。"""
    mon = dt - timedelta(days=dt.weekday())
    return day_str(mon), day_str(mon + timedelta(days=6))

def infer_meal_sub(dt):
    h = dt.hour + dt.minute / 60
    if h < 10.5:
        return "朝"
    if h < 15:
        return "昼"
    if h < 17.5:
        return "間食"
    return "夜"

def bed_dt_from_hhmm(hh, wake_dt):
    """就寝時刻(HH:MM)を、起床時刻より前になる直近の日時として解釈。"""
    h, m = map(int, hh.split(":"))
    bed = wake_dt.replace(hour=h, minute=m, second=0, microsecond=0)
    if bed >= wake_dt:
        bed -= timedelta(days=1)
    return bed

def fmt_hours(x):
    return f"{x:.1f}h"

# ============================================================
#  DB（SQLite）
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id TEXT PRIMARY KEY, name TEXT,
  wake_deadline TEXT, sleep_min REAL, bath_daily INTEGER NOT NULL DEFAULT 0,
  meals_min INTEGER, chores_week INTEGER, updated_at INTEGER,
  radio_daily INTEGER NOT NULL DEFAULT 0, radio_notify INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL, kind TEXT NOT NULL, sub TEXT, ts INTEGER NOT NULL, day TEXT NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS ev_user_day ON events(user_id, day);
CREATE INDEX IF NOT EXISTS ev_user_kind_ts ON events(user_id, kind, ts);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS courses(
  code TEXT PRIMARY KEY, name TEXT NOT NULL, nname TEXT NOT NULL, teacher TEXT, room TEXT,
  faculty TEXT, dept TEXT, cls TEXT, year INTEGER, slots TEXT, term TEXT, custom INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS courses_nname ON courses(nname);
CREATE TABLE IF NOT EXISTS user_courses(user_id TEXT NOT NULL, code TEXT NOT NULL, PRIMARY KEY(user_id, code));
CREATE TABLE IF NOT EXISTS assignments(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, title TEXT NOT NULL, note TEXT,
  due_ts INTEGER NOT NULL, created_by TEXT, created_at INTEGER, closed INTEGER NOT NULL DEFAULT 0, msg_id TEXT
);
CREATE TABLE IF NOT EXISTS assignment_done(assignment_id INTEGER NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY(assignment_id, user_id));
CREATE TABLE IF NOT EXISTS assignment_reminded(assignment_id INTEGER NOT NULL, stage TEXT NOT NULL, PRIMARY KEY(assignment_id, stage));
CREATE TABLE IF NOT EXISTS daily_results(day TEXT NOT NULL, user_id TEXT NOT NULL, achieved INTEGER NOT NULL, misses TEXT, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS efforts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, text TEXT NOT NULL, day TEXT NOT NULL, ts INTEGER, msg_id TEXT);
CREATE TABLE IF NOT EXISTS goal_reviews(month TEXT NOT NULL, user_id TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(month, user_id));
CREATE TABLE IF NOT EXISTS meal_praise(event_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(event_id, user_id));
CREATE TABLE IF NOT EXISTS effort_praise(effort_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(effort_id, user_id));
CREATE TABLE IF NOT EXISTS intros(user_id TEXT PRIMARY KEY, f1 TEXT, f2 TEXT, f3 TEXT, f4 TEXT, f5 TEXT, msg_id TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS off_days(day TEXT NOT NULL, user_id TEXT NOT NULL, reason TEXT, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS custom_items(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER);
CREATE TABLE IF NOT EXISTS custom_checks(day TEXT NOT NULL, user_id TEXT NOT NULL, item_id INTEGER NOT NULL, PRIMARY KEY(day, user_id, item_id));
"""
db = None

async def db_init():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()
    # 既存DBへの列追加（既にあれば失敗するだけなので無視）
    for m in ("ALTER TABLE users ADD COLUMN radio_daily INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN radio_notify INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN holiday_shift INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN kaji_cook INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN kaji_clean INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN kaji_dish INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN kaji_wash INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN kaji_since TEXT",
              "ALTER TABLE users ADD COLUMN kaji_trash INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN teeth_min INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN wake_set_day TEXT",
              "ALTER TABLE users ADD COLUMN sleep_set_day TEXT",
              "ALTER TABLE users ADD COLUMN bath_set_day TEXT",
              "ALTER TABLE users ADD COLUMN meals_set_day TEXT",
              "ALTER TABLE users ADD COLUMN teeth_set_day TEXT",
              "ALTER TABLE users ADD COLUMN radio_set_day TEXT",
              "ALTER TABLE users ADD COLUMN chores_set_day TEXT"):
        try:
            await db.execute(m)
            await db.commit()
        except Exception:
            pass
    await load_courses_master()

async def meta_get(key, default=None):
    async with db.execute("SELECT value FROM meta WHERE key=?", (key,)) as c:
        r = await c.fetchone()
    return r["value"] if r else default

async def meta_set(key, value):
    await db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                     (key, str(value)))
    await db.commit()

async def ensure_user(member):
    await db.execute(
        "INSERT INTO users(id,name,updated_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
        (str(member.id), member.display_name, int(now_jst().timestamp())))
    await db.commit()

async def get_user(uid):
    async with db.execute("SELECT * FROM users WHERE id=?", (str(uid),)) as c:
        return await c.fetchone()

async def add_event(uid, kind, sub=None, note=None, ts_dt=None):
    dt = ts_dt or now_jst()
    cur = await db.execute("INSERT INTO events(user_id,kind,sub,ts,day,note) VALUES(?,?,?,?,?,?)",
                           (str(uid), kind, sub, int(dt.timestamp()), day_str(dt), note))
    await db.commit()
    return cur.lastrowid

async def events_on(uid, day, kind):
    async with db.execute("SELECT * FROM events WHERE user_id=? AND day=? AND kind=? ORDER BY ts",
                          (str(uid), day, kind)) as c:
        return await c.fetchall()

async def last_bed_within(uid, now, hours=20):
    since = int((now - timedelta(hours=hours)).timestamp())
    async with db.execute("SELECT ts FROM events WHERE user_id=? AND kind='bed' AND ts>=? ORDER BY ts DESC LIMIT 1",
                          (str(uid), since)) as c:
        r = await c.fetchone()
    return datetime.fromtimestamp(r["ts"], JST) if r else None

async def count_events_between(uid, kind, d1, d2):
    async with db.execute("SELECT COUNT(*) AS n FROM events WHERE user_id=? AND kind=? AND day BETWEEN ? AND ?",
                          (str(uid), kind, d1, d2)) as c:
        return (await c.fetchone())["n"]

async def distinct_main_meals(uid, day):
    async with db.execute("SELECT DISTINCT sub FROM events WHERE user_id=? AND day=? AND kind='meal' AND sub IN ('朝','昼','夜')",
                          (str(uid), day)) as c:
        return [r["sub"] for r in await c.fetchall()]

# ============================================================
#  判定ロジック（純粋関数に近い形にしてテストしやすく）
# ============================================================
# 家事の種類ごとの最低頻度（「N日に1回」）。洗濯は5工程のどれかをやれば「洗濯した」扱い
KAJI_CATS = [
    ("kaji_cook", "料理", "🍳", ("cook",)),
    ("kaji_clean", "掃除", "🧹", ("clean",)),
    ("kaji_dish", "皿洗い", "🍽️", ("dish",)),
    ("kaji_trash", "ごみ捨て", "🗑", ("trash",)),
    ("kaji_wash", "洗濯", "🧺", ("wash_run", "wash_in", "dry_run", "dry_in", "hang")),
]

def kaji_interval_text(n):
    return "毎日" if n == 1 else f"{n}日に1回"

async def kaji_status(u, day):
    """設定済みの家事について {label, emoji, n, last, gap, due}。due=今日やらないと未達"""
    out = []
    since = u["kaji_since"] or day
    d0 = date.fromisoformat(day)
    for col, label, emoji, subs in KAJI_CATS:
        n = u[col] or 0
        if n <= 0:
            continue
        q = ",".join("?" * len(subs))
        async with db.execute(f"SELECT MAX(day) AS d FROM events WHERE user_id=? AND kind='chore' AND sub IN ({q}) AND day<=?",
                              (str(u["id"]), *subs, day)) as c:
            last = (await c.fetchone())["d"]
        base = last if (last and last >= since) else since
        gap = (d0 - date.fromisoformat(base)).days
        out.append({"label": label, "emoji": emoji, "n": n, "last": last, "gap": gap, "due": gap >= n})
    return out

async def build_misses(u, day, d1, d2, is_sunday):
    """ユーザーの設定と当日の記録から、未達項目の文字列リストを返す。"""
    misses = []
    uid = u["id"]
    dl = effective_deadline(u, date.fromisoformat(day))
    if dl and u["wake_set_day"] != day:
        w = await events_on(uid, day, "wake")
        if not w:
            misses.append(f"☀️ 起床 未報告（{dl} まで）")
        else:
            t = hhmm(datetime.fromtimestamp(w[0]["ts"], JST))
            if t > dl:
                misses.append(f"☀️ 寝坊 {dl} まで → {t}")
    if u["sleep_min"] and u["sleep_set_day"] != day:
        s = await events_on(uid, day, "sleep")
        if not s:
            misses.append("🌙 睡眠時間 未報告")
        else:
            hrs = float(s[-1]["note"] or 0)
            if hrs < u["sleep_min"]:
                misses.append(f"🌙 睡眠不足 {fmt_hours(hrs)}（最低 {fmt_hours(u['sleep_min'])}）")
    if u["bath_daily"] and u["bath_set_day"] != day:
        if not await events_on(uid, day, "bath"):
            misses.append("🛁 入浴 未報告")
    if u["radio_daily"] and u["radio_set_day"] != day:
        if not await events_on(uid, day, "radio"):
            misses.append("🏃 ラジオ体操 未参加")
    if u["meals_min"] and u["meals_set_day"] != day:
        n = len(await distinct_main_meals(uid, day))
        if n < u["meals_min"]:
            misses.append(f"🍚 食事 {n}/{u['meals_min']} 回")
    if u["chores_week"] and is_sunday and u["chores_set_day"] != day:
        n = await count_events_between(uid, "chore", d1, d2)
        if n < u["chores_week"]:
            misses.append(f"🧹 家事 今週 {n}/{u['chores_week']} 回")
    if u["teeth_min"] and u["teeth_set_day"] != day:
        n = len(await events_on(uid, day, "teeth"))
        if n < u["teeth_min"]:
            misses.append(f"🪥 歯磨き {n}/{u['teeth_min']} 回")
    # 家事の種類ごとの頻度（/kaji）
    for st in await kaji_status(u, day):
        if st["due"]:
            misses.append(f"{st['emoji']} {st['label']} " + ("今日やってない（毎日）" if st["n"] == 1 else f"{st['gap']}日やってない（{st['n']}日に1回）"))
    # 自分で決めた項目（/kojin）
    async with db.execute("SELECT name, created_at FROM custom_items WHERE user_id=? AND id NOT IN "
                          "(SELECT item_id FROM custom_checks WHERE user_id=? AND day=?) ORDER BY id", (uid, uid, day)) as c:
        for r in await c.fetchall():
            if day_str(datetime.fromtimestamp(r["created_at"] or 0, JST)) == day:
                continue  # 今日追加した項目は明日から
            misses.append(f"📝 {r['name']} 未チェック")
    return misses

def effective_deadline(u, dt):
    """起床締切。土日は holiday_shift（分）だけ後ろにずらす"""
    dl = u["wake_deadline"]
    if not dl:
        return None
    shift = u["holiday_shift"] or 0
    if shift and dt.weekday() >= 5:
        h, m = map(int, dl.split(":"))
        t = min(h * 60 + m + shift, 23 * 60 + 59)
        return f"{t // 60:02d}:{t % 60:02d}"
    return dl

async def all_items_skipped(u, day):
    """この日、実際に判定される項目が1つも無い（すべて今日設定・未設定）＝設定初日の人"""
    if any(v and sd != day for v, sd in (
            (u["wake_deadline"], u["wake_set_day"]), (u["sleep_min"], u["sleep_set_day"]),
            (u["bath_daily"], u["bath_set_day"]), (u["teeth_min"], u["teeth_set_day"]),
            (u["meals_min"], u["meals_set_day"]), (u["radio_daily"], u["radio_set_day"]),
            (u["chores_week"], u["chores_set_day"]))):
        return False
    for col, _l, _e, _s in KAJI_CATS:
        if u[col] and (u["kaji_since"] or day) != day:
            return False
    async with db.execute("SELECT created_at FROM custom_items WHERE user_id=?", (str(u["id"]),)) as c:
        for r in await c.fetchall():
            if day_str(datetime.fromtimestamp(r["created_at"] or 0, JST)) != day:
                return False
    return True

def has_any_setting(u):
    return bool(u["wake_deadline"] or u["sleep_min"] or u["bath_daily"] or u["meals_min"] or u["chores_week"] or u["radio_daily"]
                or u["kaji_cook"] or u["kaji_clean"] or u["kaji_dish"] or u["kaji_wash"] or u["kaji_trash"] or u["teeth_min"])

def settings_text(u):
    parts = []
    parts.append((f"☀️ 起床 {u['wake_deadline']} まで" + (f"（土日は +{(u['holiday_shift'] or 0) / 60:g}h）" if u["holiday_shift"] else "")) if u["wake_deadline"] else "☀️ 起床 —")
    parts.append(f"🌙 睡眠 {fmt_hours(u['sleep_min'])} 以上" if u["sleep_min"] else "🌙 睡眠 —")
    parts.append("🛁 入浴 毎日" if u["bath_daily"] else "🛁 入浴 —")
    parts.append(f"🪥 歯磨き 1日{u['teeth_min']}回" if u["teeth_min"] else "🪥 歯磨き —")
    parts.append("🏃 ラジオ体操 毎日" if u["radio_daily"] else "🏃 ラジオ体操 —")
    parts.append(f"🍚 食事 1日{u['meals_min']}回" if u["meals_min"] else "🍚 食事 —")
    kaji = [f"{e} {lb} {kaji_interval_text(u[col])}" for col, lb, e, _ in KAJI_CATS if u[col]]
    if kaji:
        parts.append("🧹 家事 " + "／".join(kaji) + (f"（＋合計 週{u['chores_week']}回）" if u["chores_week"] else ""))
    else:
        parts.append(f"🧹 家事 週{u['chores_week']}回（合計）" if u["chores_week"] else "🧹 家事 —（/kaji で種類ごとに設定）")
    return "\n".join(parts)

# ============================================================
#  Bot 本体
# ============================================================
class SeikatsuBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # #ごはん🍚 の写真投稿検知に必要（Developer PortalでONにする）
        if MEMBERS_INTENT:
            intents.members = True
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await db_init()
        self.add_view(WakeView())
        self.add_view(MealView())
        self.add_view(ChoreView())
        self.add_view(BathView())
        self.add_view(SetteiView())
        self.add_view(IntroView())
        self.add_dynamic_items(DoneButton, MealFixButton, PraiseButton, MealPraiseButton, GoalReviewButton)
        self.add_view(EraiView())
        # コマンドの同期はグローバルではなくサーバー単位で行う（即時反映）。on_ready 参照。

bot = SeikatsuBot()

def kora_emoji(guild):
    e = discord.utils.get(guild.emojis, name=KORA_EMOJI_NAME) if guild else None
    return str(e) if e else "👹"

def erai_emoji(guild):
    e = discord.utils.get(guild.emojis, name=ERAI_EMOJI_NAME) if guild else None
    return str(e) if e else "✨"

async def get_ch(key):
    cid = await meta_get("ch_" + key)
    return bot.get_channel(int(cid)) if cid else None

def panel_embed(key):
    name, desc = CH[key]
    return discord.Embed(title=f"📋 {name} パネル", description=desc, color=discord.Color.gold())

VIEW_FACTORY = {}

async def bump_panel(key):
    """パネルを常にチャンネルの一番下に置き直す（ボタンをスクロールなしで押せるように）。"""
    ch = await get_ch(key)
    if not ch:
        return
    old = await meta_get("panel_" + key)
    if old:
        try:
            m = await ch.fetch_message(int(old))
            await m.delete()
        except Exception:
            pass
    msg = await ch.send(embed=panel_embed(key), view=VIEW_FACTORY[key]())
    await meta_set("panel_" + key, msg.id)

async def post_log(key, text):
    ch = await get_ch(key)
    if ch:
        await ch.send(text)
        await bump_panel(key)

# ------------------------------------------------------------
#  起床・睡眠
# ------------------------------------------------------------
async def record_wake(interaction, wake_dt, bed_dt, already_responded=False):
    user = interaction.user
    await add_event(user.id, "wake", ts_dt=wake_dt)
    sleep_txt = "睡眠時間 未記録"
    if bed_dt:
        hrs = (wake_dt - bed_dt).total_seconds() / 3600
        await add_event(user.id, "sleep", note=f"{hrs:.2f}", ts_dt=wake_dt)
        sleep_txt = f"睡眠 {fmt_hours(hrs)}"
    u = await get_user(user.id)
    late = ""
    dl = effective_deadline(u, wake_dt) if u else None
    if dl and hhmm(wake_dt) > dl:
        late = f" ⚠️ 締切 {dl} を過ぎてます"
    msg = f"✅ {hhmm(wake_dt)} 起床（{sleep_txt}）{late}"
    digest = await today_digest(user.id, wake_dt)
    if digest:
        msg += "\n" + digest
    if already_responded:
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)
    await post_log("wake", f"☀️ **{user.display_name}** {hhmm(wake_dt)} 起床（{sleep_txt}）{late}")

class SleepModal(discord.ui.Modal, title="🌙 昨夜は何時に寝た？"):
    bed = discord.ui.TextInput(label="就寝時刻（例 23:30）※空欄なら睡眠時間は記録しない", required=False, max_length=10)

    async def on_submit(self, interaction):
        wake_dt = now_jst()
        raw = self.bed.value.strip()
        if not raw:
            await record_wake(interaction, wake_dt, None)
            return
        hh = parse_hhmm(raw)
        if not hh:
            await interaction.response.send_message("⚠️ 時刻の形式が読めませんでした（例 23:30）。起床だけ記録します。", ephemeral=True)
            await record_wake(interaction, wake_dt, None, already_responded=True)
            return
        bed_dt = bed_dt_from_hhmm(hh, wake_dt)
        await add_event(interaction.user.id, "bed", ts_dt=bed_dt)
        await record_wake(interaction, wake_dt, bed_dt)

class WakeView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="☀️ 起きた", style=discord.ButtonStyle.success, custom_id="sk_wake")
    async def wake(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        existing = await events_on(user.id, day_str(now), "wake")
        if existing:
            t = hhmm(datetime.fromtimestamp(existing[0]["ts"], JST))
            await interaction.response.send_message(f"今日はもう {t} に起床報告済みです。二度寝から帰ってきたなら 😴 を押してね。", ephemeral=True)
            return
        bed_dt = await last_bed_within(user.id, now)
        if bed_dt is None:
            await interaction.response.send_modal(SleepModal())
            return
        await record_wake(interaction, now, bed_dt)

    @discord.ui.button(label="🌙 おやすみ", style=discord.ButtonStyle.primary, custom_id="sk_bed")
    async def bed(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "bed", ts_dt=now)
        await interaction.response.send_message(f"🌙 {hhmm(now)} おやすみ。起きたら ☀️ を押してね。", ephemeral=True)
        await post_log("wake", f"🌙 **{user.display_name}** {hhmm(now)} おやすみ")

    @discord.ui.button(label="😴 二度寝した", style=discord.ButtonStyle.secondary, custom_id="sk_nizone")
    async def nizone(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "nizone")
        deg = len(await events_on(user.id, day_str(now), "nizone")) + 1  # 1回押したら「二度寝」
        await interaction.response.send_message(f"😴 {hhmm(now)}　本日 {deg} 度寝を記録しました。おかえりなさい。", ephemeral=True)
        await post_log("wake", f"😴 **{user.display_name}** {hhmm(now)} 二度寝から生還（本日 {deg} 度寝）")

    @discord.ui.button(label="🏃 ラジオ体操の呼び出し ON/OFF", style=discord.ButtonStyle.secondary, custom_id="sk_radio_toggle", row=1)
    async def radio_toggle(self, interaction, button):
        user = interaction.user
        await ensure_user(user)
        u = await get_user(user.id)
        new = 0 if u["radio_notify"] else 1
        await db.execute("UPDATE users SET radio_notify=? WHERE id=?", (new, str(user.id)))
        await db.commit()
        state = "ON" if new else "OFF"
        extra = "（開始時にメンションで呼びます）" if new else ""
        await interaction.response.send_message(f"🏃 毎朝 {RADIO_TIME} のラジオ体操の呼び出し：**{state}**{extra}", ephemeral=True)

# ------------------------------------------------------------
#  食事
# ------------------------------------------------------------
class MealModal(discord.ui.Modal):
    def __init__(self, sub):
        super().__init__(title=f"{MEAL_EMOJI[sub]} {sub}ごはん")
        self.sub = sub
        self.what = discord.ui.TextInput(label="何を食べた？（空欄OK）", required=False, max_length=100)
        self.add_item(self.what)

    async def on_submit(self, interaction):
        user = interaction.user
        note = self.what.value.strip() or None
        await ensure_user(user)
        await add_event(user.id, "meal", self.sub, note=note)
        await interaction.response.send_message(f"✅ {self.sub}ごはんを記録しました。📸 写真も残したいときは、このチャンネルに写真を投稿すると自動で記録されます。", ephemeral=True)
        await post_log("meal", f"{MEAL_EMOJI[self.sub]} **{user.display_name}** {self.sub}ごはん" + (f"：{note}" if note else ""))

class MealButton(discord.ui.Button):
    def __init__(self, sub, emoji):
        super().__init__(label=f"{emoji} {sub}", style=discord.ButtonStyle.secondary, custom_id=f"sk_meal_{sub}")
        self.sub = sub

    async def callback(self, interaction):
        await interaction.response.send_modal(MealModal(self.sub))

class MealView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for sub, emoji in MEALS:
            self.add_item(MealButton(sub, emoji))

# ------------------------------------------------------------
#  家事
# ------------------------------------------------------------
class ChoreButton(discord.ui.Button):
    def __init__(self, key, label, emoji, row):
        super().__init__(label=f"{emoji} {label}", style=discord.ButtonStyle.secondary, custom_id=f"sk_chore_{key}", row=row)
        self.key = key

    async def callback(self, interaction):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "chore", self.key)
        d1, d2 = week_range(now)
        n = await count_events_between(user.id, "chore", d1, d2)
        await interaction.response.send_message(f"✅ {CHORE_LABEL[self.key]} を記録（今週 {n} 回目）", ephemeral=True)
        await post_log("chore", f"{CHORE_LABEL[self.key]} **{user.display_name}**（今週 {n} 回目）")

class ChoreView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, label, emoji, row in CHORES:
            self.add_item(ChoreButton(key, label, emoji, row))

# ------------------------------------------------------------
#  入浴
# ------------------------------------------------------------
class BathView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🛁 お風呂入った", style=discord.ButtonStyle.primary, custom_id="sk_bath")
    async def bath(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "bath")
        n = len(await events_on(user.id, day_str(now), "bath"))
        extra = f"（今日 {n} 回目）" if n >= 2 else ""
        praise = " きれい好き！" if n >= 2 else ""
        await interaction.response.send_message(f"✅ {hhmm(now)} 入浴を記録しました{extra}。{praise}", ephemeral=True)
        await post_log("bath", f"🛁 **{user.display_name}** {hhmm(now)} 入浴{extra}")

    @discord.ui.button(label="🪥 歯磨きした", style=discord.ButtonStyle.secondary, custom_id="sk_teeth")
    async def teeth(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "teeth")
        n = len(await events_on(user.id, day_str(now), "teeth"))
        u = await get_user(user.id)
        goal = f"／目標 {u['teeth_min']} 回" if u and u["teeth_min"] else ""
        await interaction.response.send_message(f"✅ {hhmm(now)} 歯磨き（今日 {n} 回目{goal}）", ephemeral=True)
        await post_log("bath", f"🪥 **{user.display_name}** {hhmm(now)} 歯磨き（今日 {n} 回目）")

VIEW_FACTORY.update({"wake": WakeView, "meal": MealView, "chore": ChoreView, "bath": BathView})

# ------------------------------------------------------------
#  #ごはん🍚 に写真を投げたら自動記録
# ------------------------------------------------------------
@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    meal_ch = await meta_get("ch_meal")
    if not meal_ch or message.channel.id != int(meal_ch) or not message.attachments:
        return
    if not any((a.content_type or "").startswith("image/") for a in message.attachments):
        return
    now = now_jst()
    sub = infer_meal_sub(now)
    await ensure_user(message.author)
    eid = await add_event(message.author.id, "meal", sub, note=(message.content.strip() or "写真")[:100])
    try:
        await message.add_reaction("✅")
    except Exception:
        pass
    view = discord.ui.View(timeout=None)
    for s2, e2 in MEALS:
        view.add_item(MealFixButton(eid, message.author.id, s2, e2, current=sub))
    view.add_item(MealPraiseButton(eid, 0))
    await message.reply(f"📸 {MEAL_EMOJI[sub]} **{sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）",
                        mention_author=False, view=view)
    await bump_panel("meal")

class MealPraiseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_mp:(?P<eid>\d+)"):
    """ごはん写真への「うまそう！」。週間で一番👏を集めた写真が 🍜飯テロ賞"""
    def __init__(self, eid, count=0):
        super().__init__(discord.ui.Button(label=f"👏 うまそう！（{count}）", style=discord.ButtonStyle.primary,
                                           custom_id=f"sk_mp:{eid}", row=1))
        self.eid = int(eid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["eid"])

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        async with db.execute("SELECT user_id, sub FROM events WHERE id=? AND kind='meal'", (self.eid,)) as c:
            ev = await c.fetchone()
        if not ev:
            await interaction.response.send_message("この記録は見つかりませんでした。", ephemeral=True)
            return
        if ev["user_id"] == uid:
            await interaction.response.send_message("自分のごはんを褒めるのは、心の中でどうぞ 🍚", ephemeral=True)
            return
        await ensure_user(interaction.user)
        async with db.execute("SELECT 1 FROM meal_praise WHERE event_id=? AND user_id=?", (self.eid, uid)) as c:
            already = await c.fetchone()
        if already:
            await db.execute("DELETE FROM meal_praise WHERE event_id=? AND user_id=?", (self.eid, uid))
        else:
            await db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,?)", (self.eid, uid, day_str(now_jst())))
        await db.commit()
        async with db.execute("SELECT COUNT(*) AS n FROM meal_praise WHERE event_id=?", (self.eid,)) as c:
            n = (await c.fetchone())["n"]
        view = discord.ui.View(timeout=None)
        author_id = int(ev["user_id"])
        for s2, e2 in MEALS:
            view.add_item(MealFixButton(self.eid, author_id, s2, e2, current=ev["sub"]))
        view.add_item(MealPraiseButton(self.eid, n))
        await interaction.response.edit_message(view=view)

async def meshitero_winners(d1, d2):
    """期間中に一番👏を集めた写真の投稿者（同数は全員）と👏数"""
    async with db.execute("SELECT mp.event_id, COUNT(*) AS n FROM meal_praise mp WHERE mp.day BETWEEN ? AND ? GROUP BY mp.event_id",
                          (d1, d2)) as c:
        rows = await c.fetchall()
    if not rows:
        return [], 0
    best = max(r["n"] for r in rows)
    names = []
    for r in rows:
        if r["n"] != best:
            continue
        async with db.execute("SELECT user_id FROM events WHERE id=?", (r["event_id"],)) as c:
            ev = await c.fetchone()
        if ev:
            u = await get_user(ev["user_id"])
            nm = (u["name"] if u else None) or "？"
            if nm not in names:
                names.append(nm)
    return names, best

class MealFixButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_mf:(?P<eid>\d+):(?P<uid>\d+):(?P<sub>朝|昼|夜|間食)"):
    """写真投稿の食事種類を選び直すボタン（投稿した本人だけ有効）"""
    def __init__(self, eid, uid, sub, emoji, current=None):
        super().__init__(discord.ui.Button(
            label=f"{emoji} {sub}", custom_id=f"sk_mf:{eid}:{uid}:{sub}",
            style=discord.ButtonStyle.success if sub == current else discord.ButtonStyle.secondary))
        self.eid, self.uid, self.sub = int(eid), int(uid), sub

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["eid"], match["uid"], match["sub"], MEAL_EMOJI.get(match["sub"], ""), current=None)

    async def callback(self, interaction):
        if interaction.user.id != self.uid:
            await interaction.response.send_message("投稿した本人だけが変更できます。", ephemeral=True)
            return
        await db.execute("UPDATE events SET sub=? WHERE id=? AND user_id=? AND kind='meal'", (self.sub, self.eid, str(self.uid)))
        await db.commit()
        view = discord.ui.View(timeout=None)
        for s2, e2 in MEALS:
            view.add_item(MealFixButton(self.eid, self.uid, s2, e2, current=self.sub))
        async with db.execute("SELECT COUNT(*) AS n FROM meal_praise WHERE event_id=?", (self.eid,)) as c:
            n = (await c.fetchone())["n"]
        view.add_item(MealPraiseButton(self.eid, n))
        await interaction.response.edit_message(
            content=f"📸 {MEAL_EMOJI[self.sub]} **{self.sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）", view=view)

# ------------------------------------------------------------
#  毎晩の判定 → #叱責👹
# ------------------------------------------------------------
async def streak_of(uid, day):
    """day を含めて遡った連続達成日数。お休み申告の日は飛ばし（途切れない）、記録の無い日・未達の日で途切れる"""
    since = (date.fromisoformat(day) - timedelta(days=400)).isoformat()
    async with db.execute("SELECT day, achieved FROM daily_results WHERE user_id=? AND day<=? AND day>=?", (str(uid), day, since)) as c:
        res = {r["day"]: r["achieved"] for r in await c.fetchall()}
    async with db.execute("SELECT day FROM off_days WHERE user_id=? AND day<=? AND day>=?", (str(uid), day, since)) as c:
        off = {r["day"] for r in await c.fetchall()}
    n, d = 0, date.fromisoformat(day)
    for _ in range(400):
        ds = d.isoformat()
        if ds in off:
            d -= timedelta(days=1)
            continue
        if not res.get(ds):
            break
        n += 1
        d -= timedelta(days=1)
    return n

MILESTONES = (3, 7, 14, 30, 50, 100, 365)

async def gakushu_report(uid, name, day, achieved, streak, misses_n):
    """みんなで暗記！！(gakushu-rpg) へ判定結果を送る（達成日はメダル付与・🌅生活ランキング）。未設定なら何もしない"""
    if not GAKUSHU_SECRET:
        return
    import aiohttp
    payload = {"secret": GAKUSHU_SECRET, "uid": str(uid), "name": name, "day": day,
               "achieved": 1 if achieved else 0, "streak": streak, "misses": misses_n}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.post(GAKUSHU_URL.rstrip("/") + "/api/seikatsu", json=payload) as r:
                if r.status >= 300:
                    print(f"gakushu 連携エラー {r.status}: {await r.text()}", flush=True)
    except Exception as e:
        print(f"gakushu 連携失敗: {e!r}", flush=True)

async def judge(guild, manual=False):
    now = now_jst()
    day = day_str(now)
    d1, d2 = week_range(now)
    is_sunday = now.weekday() == 6
    kora_ch = await get_ch("kora")
    if not kora_ch:
        return "❌ #叱責👹 チャンネルが未設定です（/setup を実行してください）"
    async with db.execute("SELECT * FROM users") as c:
        all_users = await c.fetchall()
    async with db.execute("SELECT DISTINCT user_id FROM custom_items") as c:
        custom_uids = {r["user_id"] for r in await c.fetchall()}
    async with db.execute("SELECT user_id, reason FROM off_days WHERE day=?", (day,)) as c:
        off = {r["user_id"]: (r["reason"] or "") for r in await c.fetchall()}
    users = [u for u in all_users if has_any_setting(u) or u["id"] in custom_uids]
    resting = [u for u in users if u["id"] in off]
    users = [u for u in users if u["id"] not in off]
    emoji = kora_emoji(guild)
    results, achievers, celebrate, first_day = [], [], [], []
    for u in users:
        if await all_items_skipped(u, day):
            first_day.append(u)
            await db.execute("INSERT OR IGNORE INTO off_days(day,user_id,reason) VALUES(?,?,?)", (day, u["id"], "設定初日"))
            await db.commit()  # お休み扱いにしてストリークを途切れさせない
            continue
        misses = await build_misses(u, day, d1, d2, is_sunday)
        achieved = 0 if misses else 1
        await db.execute("INSERT INTO daily_results(day,user_id,achieved,misses) VALUES(?,?,?,?) "
                         "ON CONFLICT(day,user_id) DO UPDATE SET achieved=excluded.achieved, misses=excluded.misses",
                         (day, u["id"], achieved, "\n".join(misses)))
        await db.commit()
        streak = await streak_of(u["id"], day)
        if streak > (u["best_streak"] or 0):
            await db.execute("UPDATE users SET best_streak=? WHERE id=?", (streak, u["id"]))
            await db.commit()
        if misses:
            results.append((u, misses))
        else:
            achievers.append((u, streak))
            if streak in MILESTONES:
                celebrate.append(f"🎊 <@{u['id']}> が **{streak}日連続** 達成！ {erai_emoji(guild)}")
        await gakushu_report(u["id"], u["name"] or "", day, achieved, streak, len(misses))
        try:
            await apply_streak_title(guild, u["id"], streak)
        except Exception as e:
            print(f"段位エラー: {e!r}", flush=True)
    tag = "（手動判定）" if manual else ""
    if not users:
        await kora_ch.send(f"📋 {day} の判定{tag}：まだ誰も最低限を設定していません。`/saitei` で決めよう。")
    elif not results:
        await kora_ch.send(f"{erai_emoji(guild)} {day} の判定{tag}：**全員が最低限を守りました！** えらい！！")
    else:
        await kora_ch.send(f"📋 {day} の判定{tag}：{len(results)}/{len(users) - len(first_day)} 人に叱責の儀を行います👹")
        for u, misses in results:
            await kora_ch.send(f"{emoji} <@{u['id']}> **こら！**\n" + "\n".join("・" + m for m in misses))
    if achievers:
        achievers.sort(key=lambda x: -x[1])
        await kora_ch.send(f"{erai_emoji(guild)} 達成：" + "、".join(f"**{u['name']}**" + (f" 🔥{st}日" if st >= 2 else "") for u, st in achievers))
    for line in celebrate:
        await kora_ch.send(line)
    if resting:
        await kora_ch.send("🛌 お休み：" + "、".join(f"**{u['name']}**" + (f"（{off[u['id']]}）" if off[u["id"]] else "") for u in resting))
    if first_day:
        await kora_ch.send("🍀 設定初日（判定は明日から）：" + "、".join(f"**{u['name']}**" for u in first_day))
    return f"判定完了：{len(results)}/{len(users)} 人が未達"

# ------------------------------------------------------------
#  週次通信簿（日曜の判定後に自動投稿。/tsushinbo で手動）
# ------------------------------------------------------------
def _find_cjk_font():
    for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
              "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
              "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/msgothic.ttc"):
        if os.path.exists(f):
            return f
    return None

def render_week_chart(d1, series):
    """起床時刻・睡眠時間の週間グラフ（PNG bytes）。matplotlib が無ければ None"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None
    fp = None
    f = _find_cjk_font()
    if f:
        fp = font_manager.FontProperties(fname=f)
    days = [date.fromisoformat(d1) + timedelta(days=i) for i in range(7)]
    labels = [f"{d.month}/{d.day}({DAY_CHARS[d.weekday()]})" for d in days]
    x = list(range(7))
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6.4), dpi=120)
    for sr in series:
        ax1.plot(x, [sr["wake"].get(d.isoformat(), float("nan")) for d in days], marker="o", label=sr["name"])
        ax2.plot(x, [sr["sleep"].get(d.isoformat(), float("nan")) for d in days], marker="s", label=sr["name"])
    ax1.set_title("起床時刻", fontproperties=fp); ax1.set_ylabel("時", fontproperties=fp)
    ax2.set_title("睡眠時間", fontproperties=fp); ax2.set_ylabel("時間", fontproperties=fp)
    for ax in (ax1, ax2):
        ax.set_xticks(x); ax.set_xticklabels(labels, fontproperties=fp); ax.grid(alpha=0.3)
        if series:
            ax.legend(prop=fp, fontsize=8, loc="best")
    ax1.set_ylim(4, 14); ax2.set_ylim(0, 12)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf

async def period_summary(guild, d1, d2, kind="week", manual=False):
    """kind='week'：今週の通信簿（#つうしんぼ📮）／kind='month'：月間表彰"""
    now = now_jst()
    ch = await get_ch("tsushinbo") or await get_ch("kora")
    if not ch:
        return "❌ #つうしんぼ📮 が未設定です（/setup を実行してください）"
    async with db.execute("SELECT user_id, COUNT(*) AS judged, SUM(achieved) AS ach, GROUP_CONCAT(misses, '\n') AS mtext "
                          "FROM daily_results WHERE day BETWEEN ? AND ? GROUP BY user_id", (d1, d2)) as c:
        jr = {r["user_id"]: r for r in await c.fetchall()}
    async with db.execute("SELECT DISTINCT user_id FROM events WHERE day BETWEEN ? AND ?", (d1, d2)) as c:
        uids = {r["user_id"] for r in await c.fetchall()} | set(jr)
    stats, series = [], []
    for uid in uids:
        u = await get_user(uid)
        name = (u["name"] if u else None) or uid
        async with db.execute("SELECT kind, sub, ts, day, note FROM events WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
            evs = await c.fetchall()
        wake_days = {}
        for e in evs:
            if e["kind"] == "wake":
                wake_days.setdefault(e["day"], datetime.fromtimestamp(e["ts"], JST))  # 1日1回目だけ
        wake_min = [w.hour * 60 + w.minute for w in wake_days.values()]
        sleep_days = {}
        for e in evs:
            if e["kind"] == "sleep" and e["note"]:
                sleep_days[e["day"]] = float(e["note"])
        sleeps = list(sleep_days.values())
        j = jr.get(uid)
        mtext = (j["mtext"] or "") if j else ""
        stats.append({
            "uid": uid, "name": name,
            "judged": j["judged"] if j else 0, "ach": (j["ach"] or 0) if j else 0,
            "streak": await streak_of(uid, day_str(now)),
            "wake_n": len(wake_min), "wake_avg": (sum(wake_min) / len(wake_min)) if wake_min else None,
            "sleep_avg": (sum(sleeps) / len(sleeps)) if sleeps else None,
            "chores": sum(1 for e in evs if e["kind"] == "chore"),
            "meals": sum(1 for e in evs if e["kind"] == "meal"),
            "baths": len({e["day"] for e in evs if e["kind"] == "bath"}),
            "teeth": sum(1 for e in evs if e["kind"] == "teeth"),
            "nizone": sum(1 for e in evs if e["kind"] == "nizone"),
            "efforts": 0, "praises": 0,
            "radio": sum(1 for e in evs if e["kind"] == "radio"),
            "late": mtext.count("寝坊"), "miss_n": sum(1 for l in mtext.split("\n") if l.strip()),
        })
        if kind == "week" and (wake_days or sleep_days):
            series.append({"name": name, "wake": {d: w.hour + w.minute / 60 for d, w in wake_days.items()}, "sleep": sleep_days})
    async with db.execute("SELECT user_id, COUNT(*) AS n FROM efforts WHERE day BETWEEN ? AND ? GROUP BY user_id", (d1, d2)) as c:
        eff = {r["user_id"]: r["n"] for r in await c.fetchall()}
    async with db.execute("SELECT user_id, COUNT(*) AS n FROM effort_praise WHERE day BETWEEN ? AND ? GROUP BY user_id", (d1, d2)) as c:
        pr = {r["user_id"]: r["n"] for r in await c.fetchall()}
    for x in stats:
        x["efforts"] = eff.get(x["uid"], 0)
        x["praises"] = pr.get(x["uid"], 0)
    label = "今週" if kind == "week" else f"{int(d1[5:7])}月"
    if not stats:
        await ch.send(f"📮 {label}（{d1}〜{d2}）は記録がありませんでした。")
        return "記録なし"
    stats.sort(key=lambda x: x["uid"])
    judged = [x for x in stats if x["judged"] > 0]
    judged.sort(key=lambda x: (-(x["ach"] / x["judged"]), -x["ach"], -x["streak"]))
    lines = []
    for i, x in enumerate(judged[:15]):
        rate = round(x["ach"] * 100 / x["judged"])
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} **{x['name']}**　達成 {x['ach']}/{x['judged']}日（{rate}%）" + (f"　🔥{x['streak']}日連続" if x["streak"] >= 2 else ""))
    def award(title, key, pick_min=False, need=lambda x: True, fmt=lambda v: str(v)):
        """同点は全員表彰"""
        cands = [x for x in stats if x[key] is not None and need(x)]
        if not cands:
            return None
        best_val = min(x[key] for x in cands) if pick_min else max(x[key] for x in cands)
        if not pick_min and not best_val:
            return None
        winners = [x for x in cands if x[key] == best_val]
        return f"{title}：" + "、".join(f"**{w['name']}**" for w in winners) + f"（{fmt(best_val)}）"
    kaikin_need = 3 if kind == "week" else 15
    kaikin = [x for x in judged if x["judged"] >= kaikin_need and x["ach"] == x["judged"]]
    mt_names, mt_best = await meshitero_winners(d1, d2)
    awards = [a for a in (
        ("👑 皆勤賞：" + "、".join(f"**{x['name']}**" for x in kaikin)) if kaikin else None,
        award("🌅 早起き賞", "wake_avg", pick_min=True, need=lambda x: x["wake_n"] >= 3, fmt=lambda v: f"平均 {int(v)//60}:{int(v)%60:02d}"),
        award("🛌 ぐっすり賞", "sleep_avg", need=lambda x: x["sleep_avg"] is not None, fmt=lambda v: f"平均 {v:.1f}h"),
        award("🧹 家事賞", "chores", fmt=lambda v: f"{v}回"),
        award("🍚 ごはん賞", "meals", fmt=lambda v: f"{v}回報告"),
        ("🍜 飯テロ賞：" + "、".join(f"**{n}**" for n in mt_names) + f"（👏{mt_best}）") if mt_names else None,
        award("🛁 きれい好き賞", "baths", fmt=lambda v: f"{v}日"),
        award("🪥 歯磨き賞", "teeth", fmt=lambda v: f"{v}回"),
        award("🌟 がんばり屋賞", "efforts", fmt=lambda v: f"{v}件"),
        award("👏 ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),
        award("🏃 ラジオ体操賞", "radio", fmt=lambda v: f"{v}回"),
        award("🐷 寝坊賞", "late", fmt=lambda v: f"{v}回"),
        award("😴 二度寝賞", "nizone", fmt=lambda v: f"{v}回"),
        award("👹 こら賞", "miss_n", fmt=lambda v: f"未達 {v}件"),
    ) if a]
    d1s, d2s = d1[5:].replace("-", "/"), d2[5:].replace("-", "/")
    if kind == "week":
        emb = discord.Embed(title=f"📮 今週の通信簿（{d1s}〜{d2s}）" + ("（手動）" if manual else ""), color=discord.Color.gold())
    else:
        emb = discord.Embed(title=f"🏆 {label}の月間表彰（{d1s}〜{d2s}）" + ("（手動）" if manual else ""), color=discord.Color.purple())
        if judged:
            mvp = judged[0]
            emb.description = f"👑 **月間MVP：{mvp['name']}**　達成 {mvp['ach']}/{mvp['judged']}日（{round(mvp['ach']*100/mvp['judged'])}%）"
    emb.add_field(name="🏆 最低限 達成率ランキング", value="\n".join(lines)[:1024] if lines else "判定対象の人がいませんでした（/saitei で設定）", inline=False)
    if awards:
        emb.add_field(name=("🎖 今週の各賞" if kind == "week" else "🎖 月間各賞"), value="\n".join(awards)[:1024], inline=False)
    emb.set_footer(text="来週もほどほどに、最低限を守ろう" if kind == "week" else "来月もほどほどに、最低限を守ろう")
    file = None
    if kind == "week" and series:
        try:
            buf = await asyncio.to_thread(render_week_chart, d1, series)
            if buf:
                file = discord.File(buf, filename="week.png")
                emb.set_image(url="attachment://week.png")
        except Exception as e:
            print(f"グラフ生成エラー: {e!r}", flush=True)
    if file:
        await ch.send(embed=emb, file=file)
    else:
        await ch.send(embed=emb)
    thread_note = ""
    if guild is not None:
        try:
            if kind == "week":
                nebou = [x for x in stats if x["late"]]
                best_late = max((x["late"] for x in nebou), default=0)
                await set_exclusive_title(guild, TITLE_KAIKIN[0], [x["uid"] for x in kaikin])
                await set_exclusive_title(guild, TITLE_NEBOU[0], [x["uid"] for x in nebou if x["late"] == best_late] if best_late else [])
            else:
                await set_exclusive_title(guild, TITLE_MVP[0], [judged[0]["uid"]] if judged else [])
                notes = [await post_goal_review(guild, now), await post_goal_thread(guild, ch, now)]
                thread_note = "\n".join(n for n in notes if n)
        except Exception as e:
            print(f"称号/宣言スレ エラー: {e!r}", flush=True)
    return (f"{'通信簿' if kind == 'week' else '月間表彰'}を投稿しました（{len(stats)}人）"
            + ((chr(10) + thread_note) if thread_note else ""))

GOAL_RESULTS = [("o", "◯ 達成した！", discord.ButtonStyle.success),
                ("d", "△ おしかった", discord.ButtonStyle.secondary),
                ("x", "✕ 来月こそ", discord.ButtonStyle.danger)]
GOAL_LABEL = {r: lab for r, lab, _ in GOAL_RESULTS}

async def goal_review_state(month):
    async with db.execute("SELECT user_id, result FROM goal_reviews WHERE month=?", (month,)) as c:
        rows = await c.fetchall()
    by = {"o": [], "d": [], "x": []}
    for r in rows:
        by.setdefault(r["result"], []).append(r["user_id"])
    return by

async def goal_review_tally(month):
    by = await goal_review_state(month)
    parts = []
    for r, lab, _ in GOAL_RESULTS:
        uids = by.get(r, [])
        if uids:
            names = []
            for uid in uids:
                u = await get_user(uid)
                names.append((u["name"] if u else None) or f"<@{uid}>")
            parts.append(f"{lab[0]} {len(uids)}人（{'、'.join(names)}）")
    return "　".join(parts)

async def _goal_review_view(month):
    by = await goal_review_state(month)
    view = discord.ui.View(timeout=None)
    for r, lab, st in GOAL_RESULTS:
        view.add_item(GoalReviewButton(month, r, len(by.get(r, []))))
    return view

class GoalReviewButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_gr:(?P<month>\d{4}-\d{2}):(?P<res>[odx])"):
    def __init__(self, month, res, count=0):
        lab, st = next(((lab, st) for r, lab, st in GOAL_RESULTS if r == res), ("?", discord.ButtonStyle.secondary))
        super().__init__(discord.ui.Button(label=f"{lab}（{count}）", style=st, custom_id=f"sk_gr:{month}:{res}"))
        self.month, self.res = month, res

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["month"], match["res"])

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        await ensure_user(interaction.user)
        async with db.execute("SELECT result FROM goal_reviews WHERE month=? AND user_id=?", (self.month, uid)) as c:
            cur = await c.fetchone()
        if cur and cur["result"] == self.res:
            await db.execute("DELETE FROM goal_reviews WHERE month=? AND user_id=?", (self.month, uid))
        else:
            await db.execute("INSERT INTO goal_reviews(month,user_id,result) VALUES(?,?,?) "
                             "ON CONFLICT(month,user_id) DO UPDATE SET result=excluded.result", (self.month, uid, self.res))
        await db.commit()
        base = interaction.message.content.split("\n📊 ")[0]
        tally = await goal_review_tally(self.month)
        await interaction.response.edit_message(content=base + (f"\n📊 {tally}" if tally else ""),
                                                view=await _goal_review_view(self.month))

async def post_goal_review(guild, now):
    """月末に、その月の宣言スレッドへ「答え合わせ」を投稿（◯△✕の自己申告ボタン・月1回）"""
    key_m = f"{now.year}-{now.month:02d}"
    tid = await meta_get(f"goal_thread_{key_m}")
    if not tid or not guild:
        return ""
    th = guild.get_thread(int(tid))
    if th is None:
        try:
            th = await guild.fetch_channel(int(tid))
        except Exception:
            return ""
    if not isinstance(th, discord.Thread):
        return ""
    if await meta_get(f"goal_review_{key_m}"):
        return "答え合わせは投稿済みです"
    uids = []
    try:
        async for m in th.history(limit=200):
            if not m.author.bot and str(m.author.id) not in uids:
                uids.append(str(m.author.id))
    except Exception as e:
        print(f"宣言スレッド履歴取得失敗: {e!r}", flush=True)
    mention = " ".join(f"<@{u}>" for u in reversed(uids))
    msg = await th.send((f"🎬 **{now.month}月の宣言、答え合わせの時間です！**\n"
                         "宣言はどうだった？ 下のボタンで自己申告どうぞ（押し直しOK・もう一度押すと取り消し）\n" + mention).rstrip(),
                        view=await _goal_review_view(key_m))
    await meta_set(f"goal_review_{key_m}", msg.id)
    return "答え合わせを宣言スレッドに投稿しました"

async def post_goal_thread(guild, ch, now):
    """月間表彰の直後に、翌月の目標宣言スレッドを作る（月に1回だけ）。
    前回スレッド化に失敗していた場合（権限不足など）は、既存メッセージのスレッド化を再試行する。
    戻り値は状態の一言（/tsushinbo の結果に表示）"""
    nxt = (now + timedelta(days=1)).replace(day=1)  # 「明日が属する月」＝月末実行なら翌月、月の途中の手動実行ならその月
    key = f"goal_thread_{nxt.year}-{nxt.month:02d}"
    title = f"🎯 {nxt.month}月の目標宣言"
    perm_hint = "⚠️ 宣言スレッドが作れません。サーバー設定→ロール→Botのロールに「公開スレッドの作成」を付けてください"
    stored = await meta_get(key)
    if stored:
        existing = guild.get_thread(int(stored)) if guild else None
        if existing is not None:
            return "宣言スレッドは作成済みです"
        try:
            m = await ch.fetch_message(int(stored))
        except Exception:
            return "宣言スレッドは作成済みです"
        try:
            th = await m.create_thread(name=title)
            await meta_set(key, th.id)
            await meta_set("goal_thread_last", th.id)
            return "宣言スレッドを作成しました（前回の失敗分を復旧）"
        except Exception as e:
            print(f"宣言スレッド再作成失敗: {e!r}", flush=True)
            return f"{perm_hint}（{e}）"
    if guild and not ch.permissions_for(guild.me).create_public_threads:
        print("宣言スレッド: 公開スレッドの作成 権限がありません", flush=True)
    prev = await meta_get("goal_thread_last")
    extra = f"\n🔙 先月の宣言の答え合わせもどうぞ → <#{prev}>" if prev else ""
    msg = await ch.send(f"🎯 **{nxt.month}月の目標宣言、募集！**\nスレッドに一言どうぞ。来月末の表彰で答え合わせします。{extra}")
    try:
        th = await msg.create_thread(name=title)
        await meta_set(key, th.id)
        await meta_set("goal_thread_last", th.id)
        return "宣言スレッドを作成しました"
    except Exception as e:
        print(f"宣言スレッド作成失敗: {e!r}", flush=True)
        await meta_set(key, msg.id)  # メッセージIDを記録し、権限が付いたら次回スレッド化する
        return f"{perm_hint}（{e}）"

async def weekly_summary(guild, manual=False):
    d1, d2 = week_range(now_jst())
    return await period_summary(guild, d1, d2, "week", manual)

async def monthly_summary(guild, manual=False):
    now = now_jst()
    d1 = now.replace(day=1)
    last = (d1 + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return await period_summary(guild, day_str(d1), day_str(last), "month", manual)

@tasks.loop(minutes=1)
async def judge_loop():
    now = now_jst()
    if now.hour < JUDGE_HOUR:
        return
    day = day_str(now)
    if await meta_get("last_judge_day") == day:
        return
    await meta_set("last_judge_day", day)  # 先に記録して二重実行を防ぐ
    for g in bot.guilds:
        try:
            await judge(g)
            if now.weekday() == 6:
                await weekly_summary(g)
            if (now + timedelta(days=1)).month != now.month:
                await monthly_summary(g)
        except Exception as e:
            print(f"judge error: {e!r}", flush=True)

# ------------------------------------------------------------
#  朝のラジオ体操（指定時刻にVCへ入って音源を流す。mezamashi-bot の再生ロジックを流用）
# ------------------------------------------------------------
async def play_radio(guild, manual=False):
    vc_ch = await get_ch("radio")
    if not vc_ch:
        return "❌ ラジオ体操用VCが未設定です（/setup を実行してください）"
    if not os.path.exists(RADIO_MP3):
        return f"❌ 音源が見つかりません: {os.path.abspath(RADIO_MP3)}"
    wake_ch = await get_ch("wake")
    async with db.execute("SELECT id FROM users WHERE radio_notify=1") as c:
        notify_ids = [r["id"] for r in await c.fetchall()]
    mention = " ".join(f"<@{i}>" for i in notify_ids)
    if wake_ch:
        lead = "" if manual else "（1分後にスタート）"
        weather = await fetch_weather()
        await wake_ch.send(f"🏃 **{RADIO_TIME} ラジオ体操はじまるよ！**{lead} {vc_ch.mention} に集合〜"
                           + (f"\n🌤 今日の天気：{weather}" if weather else "") + (f"\n{mention}" if mention else ""))
    if not manual:
        await asyncio.sleep(60)
    try:
        vc = await vc_ch.connect(timeout=20, reconnect=False)
    except Exception as e:
        print(f"🔊 ラジオ体操 VC接続失敗: {e!r}", flush=True)
        return f"❌ VC接続失敗: {e}"
    present = {}
    try:
        vc.play(discord.FFmpegPCMAudio(RADIO_MP3, executable=FFMPEG_PATH))
        while vc.is_playing():
            for m in vc_ch.members:
                if not m.bot:
                    present[m.id] = m
            await asyncio.sleep(2)
    except Exception as e:
        print(f"🔊 ラジオ体操 再生エラー: {e!r}", flush=True)
    finally:
        try:
            await vc.disconnect(force=True)
        except Exception:
            pass
    day = day_str(now_jst())
    names = []
    for m in present.values():
        await ensure_user(m)
        if not await events_on(m.id, day, "radio"):
            await add_event(m.id, "radio")
        names.append(m.display_name)
    if wake_ch:
        await wake_ch.send("🏃 ラジオ体操おつかれさま！ 参加：" + ("、".join(names) if names else "誰もいなかった…😢"))
        await bump_panel("wake")
    return f"再生完了（参加 {len(names)} 人）"

@tasks.loop(minutes=1)
async def radio_loop():
    now = now_jst()
    if hhmm(now) != RADIO_TIME:
        return
    day = day_str(now)
    if await meta_get("last_radio_day") == day:
        return
    await meta_set("last_radio_day", day)
    for g in bot.guilds:
        try:
            await play_radio(g)
        except Exception as e:
            print(f"radio error: {e!r}", flush=True)

# ------------------------------------------------------------
#  スラッシュコマンドの同期（サーバー単位＝即時反映。グローバル登録は最長1時間かかるので使わない）
# ------------------------------------------------------------
GLOBAL_CMDS = None   # デコレータで登録されたコマンド定義の退避先

async def sync_guild_commands(guild):
    for c in GLOBAL_CMDS:
        bot.tree.add_command(c, guild=guild, override=True)
    try:
        synced = await bot.tree.sync(guild=guild)
        print(f"コマンド同期 {guild.name}: {', '.join('/' + c.name for c in synced)}", flush=True)
    except discord.Forbidden:
        print(f"❌ コマンド同期失敗 {guild.name}: Botの招待URLに applications.commands スコープが無い可能性。"
              "OAuth2 URL Generator で bot + applications.commands を選んで再招待してください。", flush=True)
    except Exception as e:
        print(f"❌ コマンド同期エラー {guild.name}: {e!r}", flush=True)

_synced_once = False

@bot.event
async def on_ready():
    global GLOBAL_CMDS, _synced_once
    print("====================================", flush=True)
    print(f"ログイン成功: {bot.user.name}", flush=True)
    if not _synced_once:
        _synced_once = True
        GLOBAL_CMDS = list(bot.tree.get_commands())
        for g in bot.guilds:
            await sync_guild_commands(g)
        # 以前のグローバル登録が残っていると候補が二重に出るので空にする
        bot.tree.clear_commands(guild=None)
        try:
            await bot.tree.sync()
        except Exception as e:
            print(f"グローバルコマンド削除エラー: {e!r}", flush=True)
    if not judge_loop.is_running():
        judge_loop.start()
    if not radio_loop.is_running():
        radio_loop.start()
    if not remind_loop.is_running():
        remind_loop.start()
    await load_role_msgs()
    print("====================================", flush=True)

@bot.event
async def on_guild_join(guild):
    if GLOBAL_CMDS is not None:
        await sync_guild_commands(guild)


# ============================================================
#  履修科目と課題リマインド
#  ・科目マスタ = data/courses_2026_kouki.json（時間割PDFから生成）。内部キーは時間割コード
#  ・履修登録は科目名のオートコンプリート。マスタに無い科目は自由入力で追加できる（custom=1）
#  ・課題は「同じ科目を履修している人」だけに通知（ロールは使わない）
# ============================================================
def norm_text(s):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).casefold()

async def load_courses_master():
    """JSONの科目マスタをDBへ取り込む（起動時・冪等）。自由入力の科目は残す。"""
    if not os.path.exists(COURSES_JSON):
        print(f"科目マスタが見つかりません: {COURSES_JSON}", flush=True)
        return 0
    with open(COURSES_JSON, encoding="utf-8") as f:
        rows = json.load(f)
    for r in rows:
        await db.execute(
            "INSERT INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) VALUES(?,?,?,?,?,?,?,?,?,?,?,0) "
            "ON CONFLICT(code) DO UPDATE SET name=excluded.name,nname=excluded.nname,teacher=excluded.teacher,room=excluded.room,"
            "faculty=excluded.faculty,dept=excluded.dept,cls=excluded.cls,year=excluded.year,slots=excluded.slots,term=excluded.term",
            (r["code"], r["name"], norm_text(r["name"]), r.get("teacher") or "", r.get("room") or "", r.get("faculty") or "",
             r.get("dept") or "", r.get("cls") or "", r.get("year"), ",".join(r.get("slots") or []), r.get("term") or ""))
    await db.commit()
    print(f"科目マスタ {len(rows)} 件を読み込みました", flush=True)
    return len(rows)

def course_label(c, with_code=False):
    """オートコンプリート／表示用の1行ラベル（100字以内）"""
    bits = []
    if c["teacher"]:
        bits.append(c["teacher"])
    if c["slots"]:
        bits.append(c["slots"])
    tag = ""
    if c["faculty"]:
        tag = c["faculty"] + (f"{c['year']}年" if c["year"] else "") + (f" {c['cls']}" if c["cls"] else "")
    s = c["name"] + (f"（{'・'.join(bits)}）" if bits else "") + (f" {tag}" if tag else "") + (f" [{c['code']}]" if with_code else "")
    return s[:100]

async def search_courses(q, limit=25):
    nq = norm_text(q)
    if not nq:
        async with db.execute("SELECT * FROM courses ORDER BY custom DESC, faculty, year, name LIMIT ?", (limit,)) as c:
            return await c.fetchall()
    like = f"%{nq}%"
    async with db.execute(
        "SELECT * FROM courses WHERE nname LIKE ? OR code LIKE ? "
        "ORDER BY CASE WHEN nname LIKE ? THEN 0 ELSE 1 END, custom DESC, faculty, year, name, code LIMIT ?",
        (like, f"%{q.strip()}%", f"{nq}%", limit)) as c:
        return await c.fetchall()

async def get_course(code):
    async with db.execute("SELECT * FROM courses WHERE code=?", (code,)) as c:
        return await c.fetchone()

async def user_course_rows(uid):
    async with db.execute("SELECT c.* FROM user_courses u JOIN courses c ON c.code=u.code WHERE u.user_id=? ORDER BY c.slots, c.name",
                          (str(uid),)) as c:
        return await c.fetchall()

async def takers_of(code):
    async with db.execute("SELECT user_id FROM user_courses WHERE code=?", (code,)) as c:
        return [r["user_id"] for r in await c.fetchall()]

async def ensure_custom_course(name):
    """自由入力の科目を登録（同名があればそれを返す）"""
    nn = norm_text(name)
    async with db.execute("SELECT * FROM courses WHERE nname=? ORDER BY custom LIMIT 1", (nn,)) as c:
        r = await c.fetchone()
    if r:
        return r
    code = "x%08x" % (zlib.crc32(nn.encode("utf-8")) & 0xffffffff)
    await db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) VALUES(?,?,?,'','','','','',NULL,'','',1)",
                     (code, name.strip()[:60], nn))
    await db.commit()
    return await get_course(code)

def parse_due(s):
    """'10/15' '10/15 23:59' '10月15日' '2026/10/15 17:00' → datetime(JST)。不正なら None。時刻省略は 23:59"""
    t = unicodedata.normalize("NFKC", s or "").strip()
    m = re.fullmatch(r"(?:(\d{4})[/年])?\s*(\d{1,2})[/月]\s*(\d{1,2})日?\s*(?:(\d{1,2})[:時]\s*(\d{2})?分?)?", t)
    if not m:
        return None
    now = now_jst()
    y = int(m.group(1)) if m.group(1) else now.year
    mo, d = int(m.group(2)), int(m.group(3))
    h = int(m.group(4)) if m.group(4) else 23
    mi = int(m.group(5)) if m.group(5) else (59 if m.group(4) is None else 0)
    try:
        due = datetime(y, mo, d, h, mi, tzinfo=JST)
    except ValueError:
        return None
    if not m.group(1) and due < now - timedelta(days=1):
        due = due.replace(year=y + 1)  # 年を省略して過去日なら来年扱い
    return due

WEEKDAY_JA = "月火水木金土日"

def fmt_due(dt):
    return f"{dt.month}/{dt.day}({WEEKDAY_JA[dt.weekday()]}) {dt:%H:%M}"

def days_left(due_dt, now=None):
    now = now or now_jst()
    return (due_dt.date() - now.date()).days

async def assignment_row(aid):
    async with db.execute("SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code WHERE a.id=?", (aid,)) as c:
        return await c.fetchone()

async def done_set(aid):
    async with db.execute("SELECT user_id FROM assignment_done WHERE assignment_id=?", (aid,)) as c:
        return {r["user_id"] for r in await c.fetchall()}

class DoneButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_kadai_done:(?P<id>\d+)"):
    def __init__(self, aid):
        super().__init__(discord.ui.Button(label="✅ 終わった", style=discord.ButtonStyle.success, custom_id=f"sk_kadai_done:{aid}"))
        self.aid = aid

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]))

    async def callback(self, interaction):
        a = await assignment_row(self.aid)
        if not a:
            await interaction.response.send_message("この課題は見つかりませんでした。", ephemeral=True)
            return
        uid = str(interaction.user.id)
        done = await done_set(self.aid)
        if uid in done:
            await db.execute("DELETE FROM assignment_done WHERE assignment_id=? AND user_id=?", (self.aid, uid))
            msg = f"⬜ 「{a['title']}」を未完了に戻しました。"
        else:
            await db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?,?)", (self.aid, uid))
            await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (uid, a["code"]))  # 押した人は履修者扱い
            msg = f"✅ 「{a['title']}」を完了にしました。おつかれさま！"
        await db.commit()
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            takers = await takers_of(a["code"])
            done = await done_set(self.aid)
            emb = interaction.message.embeds[0] if interaction.message.embeds else None
            if emb:
                emb.set_footer(text=f"完了 {len([t for t in takers if t in done])}/{len(takers)} 人")
                await interaction.message.edit(embed=emb)
        except Exception:
            pass

def kadai_embed(a, course, takers, done):
    due = datetime.fromtimestamp(a["due_ts"], JST)
    left = days_left(due)
    left_txt = "今日まで！" if left == 0 else ("期限切れ" if left < 0 else f"あと {left} 日")
    emb = discord.Embed(title=f"📚 {course['name']}", description=f"**{a['title']}**" + (f"\n{a['note']}" if a["note"] else ""),
                        color=discord.Color.red() if left <= 1 else discord.Color.gold())
    emb.add_field(name="⏰ 期限", value=f"{fmt_due(due)}（{left_txt}）", inline=True)
    if course["teacher"] or course["slots"]:
        emb.add_field(name="科目", value=" / ".join(x for x in (course["teacher"], course["slots"]) if x), inline=True)
    emb.set_footer(text=f"完了 {len([t for t in takers if t in done])}/{len(takers)} 人 ・ #{a['id']}")
    return emb

async def post_assignment(a, course, header):
    ch = await get_ch("kadai")
    if not ch:
        return None
    takers = await takers_of(a["code"])
    done = await done_set(a["id"])
    todo = [t for t in takers if t not in done]
    mention = " ".join(f"<@{t}>" for t in todo) if todo else "（対象者なし）"
    view = discord.ui.View(timeout=None)
    view.add_item(DoneButton(a["id"]))
    msg = await ch.send(f"{header}\n{mention}", embed=kadai_embed(a, course, takers, done), view=view)
    return msg

async def remind_assignments():
    """3日前・前日・当日の朝にリマインド。期限を1日過ぎたら自動クローズ。"""
    now = now_jst()
    async with db.execute("SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code WHERE a.closed=0 ORDER BY a.due_ts") as c:
        rows = await c.fetchall()
    for a in rows:
        due = datetime.fromtimestamp(a["due_ts"], JST)
        left = days_left(due, now)
        if left < -1:
            await db.execute("UPDATE assignments SET closed=1 WHERE id=?", (a["id"],))
            await db.commit()
            continue
        stage = {3: "d3", 1: "d1", 0: "d0"}.get(left)
        if not stage:
            continue
        async with db.execute("SELECT 1 FROM assignment_reminded WHERE assignment_id=? AND stage=?", (a["id"], stage)) as c2:
            if await c2.fetchone():
                continue
        await db.execute("INSERT OR IGNORE INTO assignment_reminded(assignment_id,stage) VALUES(?,?)", (a["id"], stage))
        await db.commit()
        takers = await takers_of(a["code"])
        done = await done_set(a["id"])
        if not [t for t in takers if t not in done]:
            continue
        head = {"d3": "⏰ 3日前リマインド", "d1": "⏰ **明日が期限！**", "d0": "🚨 **今日が期限！！**"}[stage]
        course = await get_course(a["code"])
        await post_assignment(a, course, head)

@tasks.loop(minutes=1)
async def remind_loop():
    now = now_jst()
    if now.hour < REMIND_HOUR:
        return
    day = day_str(now)
    if await meta_get("last_remind_day") == day:
        return
    await meta_set("last_remind_day", day)
    try:
        await remind_assignments()
    except Exception as e:
        print(f"remind error: {e!r}", flush=True)

# ---- /jikanwari ----
jikanwari = app_commands.Group(name="jikanwari", description="履修科目の登録・確認")

async def ac_all_courses(interaction, current):
    rows = await search_courses(current, 24)
    choices = [app_commands.Choice(name=course_label(r), value=r["code"]) for r in rows]
    q = (current or "").strip()
    if q and not any(norm_text(r["name"]) == norm_text(q) for r in rows):
        choices.insert(0, app_commands.Choice(name=f"＋「{q[:40]}」をマスタに無い科目として登録", value=("new:" + q)[:100]))
    return choices[:25]

async def ac_my_courses(interaction, current):
    rows = await user_course_rows(interaction.user.id)
    nq = norm_text(current)
    out = [app_commands.Choice(name=course_label(r), value=r["code"]) for r in rows if not nq or nq in r["nname"] or nq in r["code"].casefold()]
    return out[:25]

async def resolve_course_value(v):
    if v.startswith("new:"):
        return await ensure_custom_course(v[4:])
    return await get_course(v)

@jikanwari.command(name="add", description="履修科目を登録（科目名で検索。最大5つまで一度に）")
@app_commands.describe(kamoku="科目名で検索", kamoku2="2つ目", kamoku3="3つ目", kamoku4="4つ目", kamoku5="5つ目")
@app_commands.autocomplete(kamoku=ac_all_courses, kamoku2=ac_all_courses, kamoku3=ac_all_courses, kamoku4=ac_all_courses, kamoku5=ac_all_courses)
async def jikanwari_add(interaction, kamoku: str, kamoku2: str = None, kamoku3: str = None, kamoku4: str = None, kamoku5: str = None):
    user = interaction.user
    await ensure_user(user)
    added, lines = 0, []
    for v in (kamoku, kamoku2, kamoku3, kamoku4, kamoku5):
        if not v:
            continue
        c = await resolve_course_value(v)
        if not c:
            lines.append(f"❓ `{v}` は見つかりませんでした（候補から選んでください）")
            continue
        await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
        added += 1
        others = [t for t in await takers_of(c["code"]) if t != str(user.id)]
        lines.append(f"✅ {course_label(c)}" + (f"　👥 他 {len(others)} 人" if others else "　👥 最初の一人！"))
    await db.commit()
    total = len(await user_course_rows(user.id))
    await interaction.response.send_message("\n".join(lines) + f"\n\n📚 登録科目 {total} 件。`/jikanwari list` で確認、`/kadai add` で課題登録。", ephemeral=True)

@jikanwari.command(name="list", description="自分の履修科目と、同じ科目の仲間の人数")
async def jikanwari_list(interaction):
    rows = await user_course_rows(interaction.user.id)
    if not rows:
        await interaction.response.send_message("まだ履修科目がありません。`/jikanwari add` で登録しよう！", ephemeral=True)
        return
    lines = []
    for r in rows:
        n = len(await takers_of(r["code"])) - 1
        lines.append(f"・{course_label(r)}" + (f"　👥{n}" if n > 0 else ""))
    await interaction.response.send_message(embed=discord.Embed(title=f"📚 {interaction.user.display_name} の履修科目（{len(rows)}）",
                                            description="\n".join(lines)[:4000], color=discord.Color.gold()), ephemeral=True)

@jikanwari.command(name="remove", description="履修科目を外す")
@app_commands.describe(kamoku="外す科目")
@app_commands.autocomplete(kamoku=ac_my_courses)
async def jikanwari_remove(interaction, kamoku: str):
    await db.execute("DELETE FROM user_courses WHERE user_id=? AND code=?", (str(interaction.user.id), kamoku))
    await db.commit()
    c = await get_course(kamoku)
    await interaction.response.send_message(f"🗑 {course_label(c) if c else kamoku} を外しました。", ephemeral=True)

bot.tree.add_command(jikanwari)

# ---- /kadai ----
kadai = app_commands.Group(name="kadai", description="課題の登録・確認（同じ科目の履修者に通知）")

async def ac_open_assignments(interaction, current):
    uid = str(interaction.user.id)
    async with db.execute(
        "SELECT a.id, a.title, a.due_ts, c.name FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND (a.created_by=? OR a.code IN (SELECT code FROM user_courses WHERE user_id=?)) ORDER BY a.due_ts LIMIT 25",
        (uid, uid)) as c:
        rows = await c.fetchall()
    nq = norm_text(current)
    out = []
    for r in rows:
        label = f"{r['name']}：{r['title']}（{fmt_due(datetime.fromtimestamp(r['due_ts'], JST))}）"
        if not nq or nq in norm_text(label):
            out.append(app_commands.Choice(name=label[:100], value=str(r["id"])))
    return out

@kadai.command(name="add", description="課題を登録して、同じ科目の履修者に通知")
@app_commands.describe(kamoku="科目（自分の履修科目から）", kigen="期限 例: 10/15 ／ 10/15 17:00（時刻省略は23:59）", naiyou="課題の内容 例: レポート提出", memo="補足（任意）")
@app_commands.autocomplete(kamoku=ac_my_courses)
async def kadai_add(interaction, kamoku: str, kigen: str, naiyou: str, memo: str = None):
    user = interaction.user
    await ensure_user(user)
    c = await get_course(kamoku)
    if not c:
        await interaction.response.send_message("科目は候補から選んでください（先に `/jikanwari add` で履修登録）。", ephemeral=True)
        return
    due = parse_due(kigen)
    if not due:
        await interaction.response.send_message("⚠️ 期限の形式が読めませんでした。例: `10/15` `10/15 17:00` `10月15日`", ephemeral=True)
        return
    await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
    cur = await db.execute("INSERT INTO assignments(code,title,note,due_ts,created_by,created_at) VALUES(?,?,?,?,?,?)",
                           (c["code"], naiyou.strip()[:100], (memo or "").strip()[:300] or None, int(due.timestamp()), str(user.id), int(now_jst().timestamp())))
    aid = cur.lastrowid
    await db.commit()
    a = await assignment_row(aid)
    msg = await post_assignment(a, c, f"📌 **{user.display_name}** が課題を登録しました")
    if msg:
        await db.execute("UPDATE assignments SET msg_id=? WHERE id=?", (str(msg.id), aid))
        await db.commit()
    n = len(await takers_of(c["code"]))
    await interaction.response.send_message(f"✅ 登録しました：**{c['name']}**「{naiyou}」 期限 {fmt_due(due)}（履修者 {n} 人に通知。3日前・前日・当日朝にもリマインドします）", ephemeral=True)

@kadai.command(name="list", description="自分の履修科目の課題一覧（期限順）")
async def kadai_list(interaction):
    uid = str(interaction.user.id)
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND (a.created_by=? OR a.code IN (SELECT code FROM user_courses WHERE user_id=?)) ORDER BY a.due_ts",
        (uid, uid)) as c:
        rows = await c.fetchall()
    if not rows:
        await interaction.response.send_message("いま登録されている課題はありません 🎉", ephemeral=True)
        return
    lines = []
    for a in rows:
        done = uid in await done_set(a["id"])
        due = datetime.fromtimestamp(a["due_ts"], JST)
        left = days_left(due)
        lines.append(f"{'✅' if done else '⬜'} **{a['cname']}**：{a['title']}　{fmt_due(due)}" + ("" if done else f"（{'今日！' if left == 0 else f'あと{left}日'}）"))
    await interaction.response.send_message(embed=discord.Embed(title="📚 課題一覧", description="\n".join(lines)[:4000], color=discord.Color.gold()), ephemeral=True)

@kadai.command(name="done", description="課題を完了にする（投稿の✅ボタンでもOK）")
@app_commands.describe(kadai_id="課題")
@app_commands.autocomplete(kadai_id=ac_open_assignments)
async def kadai_done(interaction, kadai_id: str):
    a = await assignment_row(int(kadai_id))
    if not a:
        await interaction.response.send_message("課題が見つかりません。", ephemeral=True)
        return
    await db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?,?)", (a["id"], str(interaction.user.id)))
    await db.commit()
    await interaction.response.send_message(f"✅ 「{a['title']}」を完了にしました。", ephemeral=True)

@kadai.command(name="delete", description="課題を取り下げる（登録者または同じ科目の履修者）")
@app_commands.describe(kadai_id="課題")
@app_commands.autocomplete(kadai_id=ac_open_assignments)
async def kadai_delete(interaction, kadai_id: str):
    a = await assignment_row(int(kadai_id))
    if not a:
        await interaction.response.send_message("課題が見つかりません。", ephemeral=True)
        return
    await db.execute("UPDATE assignments SET closed=1 WHERE id=?", (a["id"],))
    await db.commit()
    ch = await get_ch("kadai")
    if ch:
        await ch.send(f"🗑 **{a['cname']}**「{a['title']}」は **{interaction.user.display_name}** が取り下げました。")
    await interaction.response.send_message("取り下げました。", ephemeral=True)

bot.tree.add_command(kadai)


# ============================================================
#  朝の天気（Open-Meteo・キー不要）／起床時ダイジェスト／お休み申告／個人項目（カスタム最低限）
# ============================================================
DAY_CHARS = "月火水木金土日"
PERIOD_START = {1: "8:45", 2: "10:30", 3: "13:00", 4: "14:45", 5: "16:30", 6: "18:15"}
WMO = {0: "☀️快晴", 1: "🌤晴れ", 2: "⛅晴れ時々くもり", 3: "☁️くもり", 45: "🌫霧", 48: "🌫霧", 51: "🌦霧雨", 53: "🌦霧雨", 55: "🌧霧雨",
       56: "🌧みぞれ", 57: "🌧みぞれ", 61: "🌧雨", 63: "🌧雨", 65: "🌧強い雨", 66: "🌧みぞれ", 67: "🌧みぞれ", 71: "🌨雪", 73: "🌨雪", 75: "❄️大雪",
       77: "🌨雪", 80: "🌦にわか雨", 81: "🌧にわか雨", 82: "⛈激しい雨", 85: "🌨にわか雪", 86: "🌨にわか雪", 95: "⛈雷雨", 96: "⛈雷雨", 99: "⛈雷雨"}

async def fetch_weather():
    """今日の天気を1行で。失敗したら空文字"""
    import aiohttp
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max&timezone=Asia%2FTokyo&forecast_days=1")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return ""
                d = (await r.json())["daily"]
        code = int(d["weather_code"][0])
        return (f"{WMO.get(code, '🌡')}　最高 {d['temperature_2m_max'][0]:.0f}℃／最低 {d['temperature_2m_min'][0]:.0f}℃"
                + (f"　☔ {int(d['precipitation_probability_max'][0])}%" if d.get("precipitation_probability_max") and d["precipitation_probability_max"][0] is not None else ""))
    except Exception as e:
        print(f"天気取得失敗: {e!r}", flush=True)
        return ""

async def today_digest(uid, now):
    """起床報告の返事に添える「今日の授業」「未完了の課題」"""
    dc = DAY_CHARS[now.weekday()]
    classes = []
    for c in await user_course_rows(uid):
        for slot in (c["slots"] or "").split(","):
            slot = slot.strip()
            if len(slot) >= 2 and slot[0] == dc and slot[1:].isdigit():
                classes.append((int(slot[1:]), c))
    classes.sort(key=lambda x: x[0])
    lines = []
    if classes:
        lines.append("📅 今日の授業：" + "／".join(f"{p}限({PERIOD_START.get(p, '')}) {c['name']}" + (f" {c['room']}" if c["room"] else "") for p, c in classes))
    async with db.execute(
        "SELECT a.title, a.due_ts, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND a.code IN (SELECT code FROM user_courses WHERE user_id=?) "
        "AND a.id NOT IN (SELECT assignment_id FROM assignment_done WHERE user_id=?) ORDER BY a.due_ts LIMIT 5", (str(uid), str(uid))) as c:
        rows = await c.fetchall()
    if rows:
        lines.append("📚 未完了の課題：" + "／".join(f"{r['cname']}「{r['title']}」{fmt_due(datetime.fromtimestamp(r['due_ts'], JST))}" for r in rows))
    u = await get_user(uid)
    if u:
        due = [st for st in await kaji_status(u, day_str(now)) if st["due"]]
        if due:
            lines.append("🧹 今日やる家事：" + "／".join(f"{st['emoji']}{st['label']}（{kaji_interval_text(st['n'])}）" for st in due))
    return "\n".join(lines)

def parse_day_spec(spec, now):
    """'' / 今日 / 明日 / 10/15 / 10/15-10/17 → ['YYYY-MM-DD', ...]。不正なら None"""
    t = unicodedata.normalize("NFKC", spec or "").strip()
    if not t or t in ("今日", "きょう"):
        return [day_str(now)]
    if t in ("明日", "あした"):
        return [day_str(now + timedelta(days=1))]
    parts = [x for x in re.split(r"\s*[-〜~～]\s*", t) if x]
    if not parts or len(parts) > 2:
        return None
    ds = [parse_due(x) for x in parts]
    if any(d is None for d in ds):
        return None
    a, b = ds[0].date(), ds[-1].date()
    if b < a or (b - a).days > 31:
        return None
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]

@bot.tree.command(name="kaji", description="家事の最低頻度を種類ごとに設定（N日に1回。0で解除）")
@app_commands.describe(ryouri="料理：何日に1回 例 1=毎日", souji="掃除：何日に1回 例 7", sara="皿洗い：何日に1回 例 1", sentaku="洗濯：何日に1回 例 3（5工程のどれかで「やった」扱い）", gomi="ごみ捨て：何日に1回 例 3")
async def kaji_command(interaction, ryouri: int = None, souji: int = None, sara: int = None, sentaku: int = None, gomi: int = None):
    user = interaction.user
    await ensure_user(user)
    changed = False
    for col, v in (("kaji_cook", ryouri), ("kaji_clean", souji), ("kaji_dish", sara), ("kaji_wash", sentaku), ("kaji_trash", gomi)):
        if v is not None:
            await db.execute(f"UPDATE users SET {col}=? WHERE id=?", (max(0, min(30, v)), str(user.id)))
            changed = True
    if changed:
        await db.execute("UPDATE users SET kaji_since=? WHERE id=?", (day_str(now_jst()), str(user.id)))  # 設定日を起点にカウント
    await db.commit()
    u = await get_user(user.id)
    sts = await kaji_status(u, day_str(now_jst()))
    if not sts:
        await interaction.response.send_message("🧹 家事の頻度は未設定です。例：`/kaji ryouri:1 sentaku:3 souji:7`（料理は毎日・洗濯は3日に1回・掃除は週1）", ephemeral=True)
        return
    lines = [f"{st['emoji']} **{st['label']}**　{kaji_interval_text(st['n'])}　最後：{st['last'][5:].replace('-', '/') if st['last'] else '未記録'}"
             + ("　⚠️ 今日やる日" if st["due"] else f"　あと {st['n'] - st['gap']} 日") for st in sts]
    await interaction.response.send_message("🧹 **家事の最低頻度**（設定日から数えます）\n" + "\n".join(lines), ephemeral=True)
    if changed:
        settei = await get_ch("settei")
        if settei:
            await settei.send(f"🧹 **{user.display_name}** が家事の頻度を設定：" + "／".join(f"{st['emoji']}{st['label']} {kaji_interval_text(st['n'])}" for st in sts))

@bot.tree.command(name="oyasumi", description="お休み申告（その日は判定されず、連続達成も途切れない）")
@app_commands.describe(riyuu="理由 例: 帰省／体調不良（「なし」で取り消し）", hi="日付 例: 明日／10/15／10/15-10/17（省略=今日）")
async def oyasumi_command(interaction, riyuu: str, hi: str = None):
    user = interaction.user
    await ensure_user(user)
    days = parse_day_spec(hi, now_jst())
    if not days:
        await interaction.response.send_message("⚠️ 日付の形式が読めませんでした。例: `明日` `10/15` `10/15-10/17`", ephemeral=True)
        return
    span = days[0][5:].replace("-", "/") + ("" if len(days) == 1 else "〜" + days[-1][5:].replace("-", "/"))
    if riyuu.strip() in ("なし", "取消", "取り消し", "解除"):
        await db.executemany("DELETE FROM off_days WHERE day=? AND user_id=?", [(d, str(user.id)) for d in days])
        await db.commit()
        await interaction.response.send_message(f"🛌 {span} のお休み申告を取り消しました。", ephemeral=True)
        return
    await db.executemany("INSERT INTO off_days(day,user_id,reason) VALUES(?,?,?) ON CONFLICT(day,user_id) DO UPDATE SET reason=excluded.reason",
                         [(d, str(user.id), riyuu.strip()[:40]) for d in days])
    await db.commit()
    await interaction.response.send_message(f"🛌 {span} をお休みにしました（{riyuu}）。その日は判定されず、連続達成も途切れません。", ephemeral=True)
    settei = await get_ch("settei")
    if settei:
        await settei.send(f"🛌 **{user.display_name}** は {span} お休み（{riyuu}）")

# ---- 個人項目（自分だけの最低限） ----
kojin = app_commands.Group(name="kojin", description="自分だけの最低限項目（薬・ストレッチなど）")

async def my_items(uid):
    async with db.execute("SELECT id, name FROM custom_items WHERE user_id=? ORDER BY id", (str(uid),)) as c:
        return await c.fetchall()

async def my_checked(uid, day):
    async with db.execute("SELECT item_id FROM custom_checks WHERE user_id=? AND day=?", (str(uid), day)) as c:
        return {r["item_id"] for r in await c.fetchall()}

async def mycheck_text(uid, day):
    items, checked = await my_items(uid), await my_checked(uid, day)
    n = sum(1 for i in items if i["id"] in checked)
    return f"📝 **今日のチェック**（{day}）　✅ {n}/{len(items)}" + ("　🎉 全部できた！" if items and n == len(items) else "")

class MyCheckButton(discord.ui.Button):
    def __init__(self, item, checked, row):
        super().__init__(label=("✅ " if checked else "⬜ ") + item["name"][:70],
                         style=discord.ButtonStyle.success if checked else discord.ButtonStyle.secondary, row=row)
        self.item_id = item["id"]

    async def callback(self, interaction):
        uid, day = str(interaction.user.id), day_str(now_jst())
        if self.item_id in await my_checked(uid, day):
            await db.execute("DELETE FROM custom_checks WHERE day=? AND user_id=? AND item_id=?", (day, uid, self.item_id))
        else:
            await db.execute("INSERT OR IGNORE INTO custom_checks(day,user_id,item_id) VALUES(?,?,?)", (day, uid, self.item_id))
        await db.commit()
        await interaction.response.edit_message(content=await mycheck_text(uid, day), view=await MyCheckView.build(uid, day))

class MyCheckView(discord.ui.View):
    def __init__(self, items, checked):
        super().__init__(timeout=900)
        for i, it in enumerate(items[:25]):
            self.add_item(MyCheckButton(it, it["id"] in checked, row=min(i // 5, 4)))

    @classmethod
    async def build(cls, uid, day):
        return cls(await my_items(uid), await my_checked(uid, day))

async def send_mycheck(interaction):
    uid, day = str(interaction.user.id), day_str(now_jst())
    if not await my_items(uid):
        await interaction.response.send_message("まだ自分の項目がありません。`/kojin add namae:薬を飲む` のように追加すると、ここに毎日のチェックリストが出ます。", ephemeral=True)
        return
    await interaction.response.send_message(await mycheck_text(uid, day), view=await MyCheckView.build(uid, day), ephemeral=True)

class SetteiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 今日のチェック（自分の項目）", style=discord.ButtonStyle.primary, custom_id="sk_mycheck")
    async def mycheck(self, interaction, button):
        await ensure_user(interaction.user)
        await send_mycheck(interaction)

VIEW_FACTORY["settei"] = SetteiView

async def ac_my_items(interaction, current):
    nq = norm_text(current)
    return [app_commands.Choice(name=i["name"][:100], value=str(i["id"])) for i in await my_items(interaction.user.id)
            if not nq or nq in norm_text(i["name"])][:25]

@kojin.command(name="add", description="自分だけの最低限項目を追加（毎日チェック。未チェックは判定で叱られる）")
@app_commands.describe(namae="項目名 例: 薬を飲む／外に出る／ストレッチ")
async def kojin_add(interaction, namae: str):
    user = interaction.user
    await ensure_user(user)
    items = await my_items(user.id)
    if len(items) >= 20:
        await interaction.response.send_message("項目は20個までです。", ephemeral=True)
        return
    nm = namae.strip()[:40]
    if any(norm_text(i["name"]) == norm_text(nm) for i in items):
        await interaction.response.send_message(f"「{nm}」はもう登録されています。", ephemeral=True)
        return
    await db.execute("INSERT INTO custom_items(user_id,name,created_at) VALUES(?,?,?)", (str(user.id), nm, int(now_jst().timestamp())))
    await db.commit()
    await interaction.response.send_message(f"📝 「{nm}」を追加しました（{len(items) + 1}件）。#設定🔧 の 📝 ボタンか `/kojin list` で毎日チェックしてね。", ephemeral=True)

@kojin.command(name="remove", description="自分の項目を削除")
@app_commands.describe(koumoku="削除する項目")
@app_commands.autocomplete(koumoku=ac_my_items)
async def kojin_remove(interaction, koumoku: str):
    await db.execute("DELETE FROM custom_items WHERE id=? AND user_id=?", (int(koumoku), str(interaction.user.id)))
    await db.execute("DELETE FROM custom_checks WHERE item_id=?", (int(koumoku),))
    await db.commit()
    await interaction.response.send_message("🗑 削除しました。", ephemeral=True)

@kojin.command(name="list", description="今日のチェックリストを開く")
async def kojin_list(interaction):
    await ensure_user(interaction.user)
    await send_mycheck(interaction)

bot.tree.add_command(kojin)


# ============================================================
#  参加者向けチュートリアル（#はじめに📖 に常設・/help でいつでも）
# ============================================================
async def make_readonly(ch, guild, reason=""):
    """@everyone の書き込みを禁止しつつ、Bot 自身は投稿・編集・リアクションできるようにする。
    ・チャンネルの上書き設定は Bot にも効くので、先に Bot への許可を付けないと Bot が 403 になる
    ・Discord は「自分が持っていない権限」を上書きで付与できない（403）ので、Bot が持つ権限だけを付ける
    ・Bot への許可が付けられなかった場合は、禁止だけ残ると詰むので読み取り専用化を諦める"""
    me = guild.me
    gp = me.guild_permissions
    wanted = ("send_messages", "embed_links", "attach_files", "add_reactions", "read_message_history", "manage_messages")
    grant = {k: True for k in wanted if gp.administrator or getattr(gp, k, False)}
    try:
        await ch.set_permissions(me, reason="Botの投稿用", **grant)
    except Exception as e:
        print(f"#{ch.name} のBot権限設定失敗: {e!r}", flush=True)
        SETUP_ERRORS.append(f"#{ch.name} にBotの投稿許可を付けられませんでした（{e}）→ 読み取り専用にせず誰でも書ける状態にしています")
        try:
            await ch.set_permissions(guild.default_role, send_messages=None, reason="Bot許可を付けられないため読み取り専用を解除")
        except Exception:
            pass
        return
    try:
        await ch.set_permissions(guild.default_role, send_messages=False, reason=reason)
    except Exception as e:
        print(f"#{ch.name} の読み取り専用化失敗: {e!r}", flush=True)
        SETUP_ERRORS.append(f"#{ch.name} を読み取り専用にできませんでした（{e}）")

SETUP_ERRORS = []

async def upsert_message(key, ch, embed, view=None):
    """meta に保存したメッセージがあれば編集、無ければ投稿（/setup を何度実行しても増えない）。
    権限不足で投稿できない場合は None を返し、/setup の結果に理由を出す"""
    mid = await meta_get(key)
    if mid:
        try:
            m = await ch.fetch_message(int(mid))
            await (m.edit(embed=embed, view=view) if view is not None else m.edit(embed=embed))
            return m
        except Exception:
            pass
    try:
        m = await (ch.send(embed=embed, view=view) if view is not None else ch.send(embed=embed))
    except discord.Forbidden:
        SETUP_ERRORS.append(f"#{ch.name} に投稿できません（Botの「メッセージを送信」権限を確認）")
        return None
    await meta_set(key, m.id)
    return m

def tutorial_embeds():
    gold = discord.Color.gold()
    e1 = discord.Embed(
        title="📖 ようこそ「最低限生活リズムサークル」へ",
        description=(
            "ここは **最低限の生活リズムを、みんなで（ゆるく・晒し合いながら）守る** サークルです。\n\n"
            "やることはシンプル：**起きたら押す・食べたら押す・やったら押す。**\n"
            f"ボタンを押すだけで記録され、毎晩 **{JUDGE_HOUR}:00** に「自分で決めた最低限」を守れたか判定されます。"
            "守れなかった人は #叱責👹 に名指しで晒されます（ネタとして楽しむ場所です👹）。\n\n"
            "記録はぜんぶ **自己申告**。正直に押すのがこのサークルの流儀です。"
        ), color=gold)
    e2 = discord.Embed(title="🚀 はじめの5分でやること", color=gold)
    e2.add_field(name="⓪ 自己紹介とロール", inline=False, value=(
        "#自己紹介🙋 の 📝 ボタンでカードを投稿（あとから更新OK）。#ロール🏷 のリアクションで学部・学年・生活形態のロールも付けよう。"))
    e2.add_field(name="① 自分の「最低限」を決める", inline=False, value=(
        "#設定🔧 で `/saitei` を実行。例：\n`/saitei kishou:8:00 suimin:6 nyuyoku:True shokuji:2`\n"
        "→ 8時までに起きる・6時間寝る・毎日お風呂・1日2食。**全部決めなくてOK**、決めた項目だけ判定されます。"
        "土日をゆるめたい人は `kyujitsu:2`（締切+2時間）。"))
    e2.add_field(name="② 起きたら ☀️、寝る前に 🌙", inline=False, value=(
        "#起床🌅 のボタンを押すだけ。睡眠時間は自動計算。☀️の返事に **今日の授業と未完了の課題** が出ます。"))
    e2.add_field(name="③ 履修科目を登録", inline=False, value=(
        "`/jikanwari add kamoku:` に科目名を打つと候補が出ます（一度に5つまで）。"
        "同じ科目の誰かが課題を登録すると #課題📚 で通知が届き、期限の3日前・前日・当日朝にリマインドされます。"))
    e2.add_field(name="④ ラジオ体操に参加（任意）", inline=False, value=(
        f"毎朝 **{RADIO_TIME}** に 🔊ラジオ体操🏃 で音源が流れます。#起床🌅 の 🏃 ボタンで呼び出しをONにすると、開始時にメンションで起こされます。"))
    e2.add_field(name="⑤ 自分だけの項目（任意）", inline=False, value=(
        "`/kojin add namae:薬を飲む` のように追加すると、#設定🔧 の 📝 ボタンで毎日チェックできます。未チェックは判定で叱られます。"))
    e3 = discord.Embed(title="🔁 1日の流れ", color=gold, description=(
        f"**朝**　☀️ 起きた → {RADIO_TIME} ラジオ体操🏃\n"
        "**日中**　🍚 食べたら押す（#ごはん🍚 に写真を投げるだけでもOK）／🧹 家事をしたら押す／📚 課題に気づいたら `/kadai add`\n"
        "**夜**　🛁 お風呂・🪥 歯磨きをしたら押す → 🌙 おやすみ\n"
        f"**{JUDGE_HOUR}:00**　判定。未達は #叱責👹 で名指し、達成した人は ✨ で称えられ、連続日数 🔥 が伸びる（3・7・14・30日…でお祝い）\n"
        "**日曜の夜**　#つうしんぼ📮 に今週の通信簿（達成率ランキング・各賞・起床/睡眠グラフ）。月末は 🏆 月間MVP"))
    e4 = discord.Embed(title="🗺 チャンネル案内", color=gold, description=(
        "#はじめに📖　このガイド\n"
        "#自己紹介🙋　📝 自己紹介カード\n"
        "#ロール🏷　リアクションで学部・学年・生活形態のロール\n"
        "#起床🌅　☀️起きた／🌙おやすみ／🏃ラジオ体操の呼び出しON/OFF\n"
        "#ごはん🍚　🍚朝 🍱昼 🍽️夜 🍩間食（写真でも記録）\n"
        "#家事🧹　🍳料理 🧹掃除 🍽️皿洗い ＋ 洗濯5工程\n"
        "#おふろ🛁　🛁お風呂入った／🪥歯磨きした\n"
        "#課題📚　課題の通知とリマインド。✅で完了\n"
        "#叱責👹　毎晩の判定結果。晒される場所\n"
        "#つうしんぼ📮　週の通信簿と月間表彰\n"
        "#設定🔧　`/saitei` `/kojin` `/oyasumi` の案内。📝 今日のチェック\n"
        "🔊ラジオ体操🏃　朝の体操VC"))
    e5 = discord.Embed(title="⌨️ コマンド早見表", color=gold, description=(
        "**最低限**　`/saitei` 設定（指定した項目だけ更新）／`/kaji` 家事を種類ごとに「N日に1回」／`/nakama` 同じ起床時刻の仲間／`/kiroku` 自分の記録と連続日数\n"
        "**お休み**　`/oyasumi riyuu:帰省 hi:10/15-10/17`（判定なし・連続達成も途切れない。`riyuu:なし` で取消）\n"
        "**科目**　`/jikanwari add` 登録／`list` 一覧／`remove` 外す\n"
        "**課題**　`/kadai add` 登録／`list` 一覧／`done` 完了／`delete` 取り下げ\n"
        "**自分の項目**　`/kojin add` 追加／`list` チェックリスト／`remove` 削除\n"
        "**自己紹介**　`/jikoshokai`（#自己紹介🙋 の 📝 ボタンでも）\n"
        "**このガイド**　`/help`"))
    e6 = discord.Embed(title="🤝 ルールと心がまえ", color=gold, description=(
        "・晒しはネタ。叱るときは愛をこめて（こら！スタンプ推奨）\n"
        "・記録は自己申告。盛らない、隠さない\n"
        "・無理な日は `/oyasumi`。休むのも最低限のうち\n"
        "・最低限は人それぞれ。人の最低限を笑わない\n"
        "・Botの不具合・要望は管理者まで"))
    return [e1, e2, e3, e4, e5, e6]

@bot.tree.command(name="help", description="使い方のかんたんガイド（自分にだけ表示）")
async def help_command(interaction):
    embs = tutorial_embeds()
    await interaction.response.send_message(embeds=[embs[1], embs[4]], ephemeral=True)

@bot.event
async def on_member_join(member):
    if member.bot:
        return
    haj = await get_ch("hajimeni")
    if haj:
        try:
            await haj.send(f"👋 ようこそ {member.mention}！まずは ① #設定🔧 で `/saitei` を実行して自分の最低限を決めて、② #起床🌅 で ☀️ を押してみよう。詳しくはこのチャンネルの上のガイドを読んでね。")
        except Exception as e:
            print(f"歓迎メッセージ失敗: {e!r}", flush=True)


# ============================================================
#  自己紹介（フォーム→カード）とリアクションロール
# ============================================================
ROLE_GROUPS = {
    "gakubu": ("🎓 学部", [("🏭", "🏭工学部", 0x3b82f6), ("🌱", "🌱農学部", 0x22c55e)]),
    "gakunen": ("📚 学年", [("1️⃣", "1年", 0xf59e0b), ("2️⃣", "2年", 0xef4444), ("3️⃣", "3年", 0xa855f7), ("4️⃣", "4年+", 0x06b6d4)]),
    "seikatsu": ("🏠 生活形態", [("🏠", "🏠一人暮らし", 0xf97316), ("👪", "👪実家", 0xec4899), ("🛌", "🛏寮・下宿", 0x14b8a6)]),
}
ROLE_MSG_IDS = {}   # message_id -> group key
OLD_ROLE_NAMES = {"🛏寮・下宿": ("🛏寮",)}   # 改名前の名前（/setup が既存ロールを改名して引き継ぐ）

def _norm_role(s):
    return re.sub(r"[\s️]", "", s or "")

def find_role(guild, name):
    n = _norm_role(name)
    return next((r for r in guild.roles if _norm_role(r.name) == n), None)

async def ensure_roles(guild):
    created = []
    for gk, (title, items) in ROLE_GROUPS.items():
        for emoji, name, color in items:
            if find_role(guild, name) is None:
                old = next((r for on in OLD_ROLE_NAMES.get(name, ()) if (r := find_role(guild, on))), None)
                if old is not None:
                    try:
                        await old.edit(name=name, reason="ロール名の変更（/setup）")
                        created.append(f"{old.name}→{name}（改名）")
                        continue
                    except Exception as e:
                        print(f"ロール改名失敗 {old.name}: {e!r}", flush=True)
                try:
                    await guild.create_role(name=name, colour=discord.Colour(color), mentionable=True, reason="自己紹介ロール（/setup）")
                    created.append(name)
                except Exception as e:
                    print(f"ロール作成失敗 {name}: {e!r}", flush=True)
    return created

async def post_role_panels(ch, old_channels=()):
    for gk, (title, items) in ROLE_GROUPS.items():
        mid = await meta_get(f"rolemsg_{gk}")
        if mid:
            try:
                await ch.fetch_message(int(mid))
            except Exception:
                for oc in old_channels:  # 以前は別チャンネルに置いていた → 古いパネルを削除
                    try:
                        m = await oc.fetch_message(int(mid))
                        await m.delete()
                        break
                    except Exception:
                        pass
                ROLE_MSG_IDS.pop(int(mid), None)
        emb = discord.Embed(title=title, color=discord.Color.gold(),
                            description="自分に合うリアクションを押すとロールが付きます（別のを押すと切替、外すとロールも外れます）\n\n"
                                        + "　".join(f"{e} {n}" for e, n, _ in items))
        msg = await upsert_message(f"rolemsg_{gk}", ch, emb)
        if not msg:
            SETUP_ERRORS.append(f"ロールパネル「{title}」を #{ch.name} に投稿できませんでした")
            continue
        ROLE_MSG_IDS[msg.id] = gk
        for e, _, _ in items:
            try:
                await msg.add_reaction(e)
            except Exception as ex:
                print(f"リアクション追加失敗 {e}: {ex!r}", flush=True)
                SETUP_ERRORS.append(f"「{title}」に {e} のリアクションを付けられません：{ex}")
    # 診断：Botのロールが付与対象より上か
    guild = ch.guild
    me_top = guild.me.top_role
    low = [n for _, items in ROLE_GROUPS.values() for _, n, _ in items if (r := find_role(guild, n)) and r.position >= me_top.position]
    if low:
        SETUP_ERRORS.append("Botのロール「" + me_top.name + "」が次のロールより下にあるため付与できません → サーバー設定→ロールでBotのロールを上へ： " + "、".join(low))
    missing = [n for _, items in ROLE_GROUPS.values() for _, n, _ in items if not find_role(guild, n)]
    if missing:
        SETUP_ERRORS.append("ロールが作れていません（Botに「ロールの管理」権限が必要）： " + "、".join(missing))

async def load_role_msgs():
    for gk in ROLE_GROUPS:
        mid = await meta_get(f"rolemsg_{gk}")
        if mid:
            ROLE_MSG_IDS[int(mid)] = gk

# ---- 称号ロール（判定結果からBotが自動で付け外し） ----
TITLE_STREAK = [(30, "文句なしの文化人", 0xff4d00), (7, "そこそこ文化人", 0xff7a1a), (3, "かけだし文化人", 0xffa64d)]
OLD_TITLE_NAMES = {"文句なしの文化人": ("🔥生活リズムの化身", "🔥リズムの化身"), "そこそこ文化人": ("🔥生活リズムの達人", "🔥リズムの達人"), "かけだし文化人": ("🔥生活リズム見習い", "🔥リズム見習い")}
TITLE_KAIKIN = ("👑今週の皆勤", 0xf5c518)
TITLE_NEBOU = ("🐷今週の寝坊神", 0xec4899)
TITLE_MVP = ("🏆月間MVP", 0xa855f7)
ALL_TITLES = TITLE_STREAK + [(0, TITLE_KAIKIN[0], TITLE_KAIKIN[1]), (0, TITLE_NEBOU[0], TITLE_NEBOU[1]), (0, TITLE_MVP[0], TITLE_MVP[1])]

def streak_title_for(streak):
    return next((name for n, name, _ in TITLE_STREAK if streak >= n), None)

async def ensure_title_roles(guild):
    created = []
    for _, name, color in ALL_TITLES:
        if find_role(guild, name) is None:
            old_r = next((r for on in OLD_TITLE_NAMES.get(name, ()) if (r := find_role(guild, on))), None)
            if old_r is not None:
                try:
                    await old_r.edit(name=name, reason="称号ロール名の変更（/setup）")
                    created.append(f"{name}（改名）")
                    continue
                except Exception as e:
                    print(f"称号ロール改名失敗 {name}: {e!r}", flush=True)
            try:
                await guild.create_role(name=name, colour=discord.Colour(color), mentionable=False, reason="称号ロール（/setup）")
                created.append(name)
            except Exception as e:
                print(f"称号ロール作成失敗 {name}: {e!r}", flush=True)
    return created

async def get_member_any(guild, uid):
    m = guild.get_member(int(uid))
    if m is None:
        try:
            m = await guild.fetch_member(int(uid))
        except Exception:
            return None
    return m

async def set_exclusive_title(guild, role_name, uids):
    """役職を uids だけが持つ状態にする（前回の保持者は meta で追跡＝メンバー全取得の権限が要らない）"""
    role = find_role(guild, role_name)
    if not role:
        return
    key = "title_" + re.sub(r"\W+", "", role_name)
    prev = json.loads(await meta_get(key) or "[]")
    uids = [str(u) for u in uids]
    for uid in prev:
        if uid not in uids:
            m = await get_member_any(guild, uid)
            if m and role in m.roles:
                try:
                    await m.remove_roles(role, reason="称号の更新")
                except Exception as e:
                    print(f"称号剥奪失敗 {role_name}/{uid}: {e!r}", flush=True)
    for uid in uids:
        m = await get_member_any(guild, uid)
        if m and role not in m.roles:
            try:
                await m.add_roles(role, reason="称号の付与")
            except Exception as e:
                print(f"称号付与失敗 {role_name}/{uid}: {e!r}", flush=True)
    await meta_set(key, json.dumps(uids))

async def apply_streak_title(guild, uid, streak):
    """🔥連続の段位（3/7/30日）を1つだけ持たせる"""
    target = streak_title_for(streak)
    m = await get_member_any(guild, uid)
    if not m:
        return
    for _, name, _c in TITLE_STREAK:
        role = find_role(guild, name)
        if not role:
            continue
        try:
            if name == target and role not in m.roles:
                await m.add_roles(role, reason=f"連続達成 {streak} 日")
            elif name != target and role in m.roles:
                await m.remove_roles(role, reason="連続達成の段位更新")
        except Exception as e:
            print(f"段位更新失敗 {name}/{uid}: {e!r}", flush=True)

async def _reaction_role(payload, add):
    gk = ROLE_MSG_IDS.get(payload.message_id)
    if not gk or not payload.guild_id or (bot.user and payload.user_id == bot.user.id):
        return
    guild = bot.get_guild(payload.guild_id)
    if not guild:
        return
    em = _norm_role(str(payload.emoji))
    items = ROLE_GROUPS[gk][1]
    target = next((n for e, n, _ in items if _norm_role(e) == em), None)
    if not target:
        return
    role = find_role(guild, target)
    if not role:
        return
    member = guild.get_member(payload.user_id)
    if member is None:
        try:
            member = await guild.fetch_member(payload.user_id)
        except Exception:
            return
    if member.bot:
        return
    try:
        if add:
            others = [r for r in (find_role(guild, n) for _, n, _ in items if n != target) if r and r in member.roles]
            if others:
                await member.remove_roles(*others, reason="ロール切替")
            if role not in member.roles:
                await member.add_roles(role, reason="リアクションロール")
        elif role in member.roles:
            await member.remove_roles(role, reason="リアクション解除")
    except discord.Forbidden:
        print("❌ ロール付与に失敗：Botのロールが対象ロールより上にあるか、「ロールの管理」権限を確認してください", flush=True)
        try:
            ch = guild.get_channel(payload.channel_id)
            if ch:
                await ch.send(f"⚠️ {member.mention} ロール「{target}」を付けられませんでした（Botのロールの位置か「ロールの管理」権限を確認してください）", delete_after=20)
        except Exception:
            pass
    except Exception as e:
        print(f"ロール操作エラー: {e!r}", flush=True)

@bot.event
async def on_raw_reaction_add(payload):
    await _reaction_role(payload, True)

@bot.event
async def on_raw_reaction_remove(payload):
    await _reaction_role(payload, False)

# ---- 自己紹介 ----
INTRO_FIELDS = [
    ("👤 呼び名・学部学科・学年・生活形態", "例：ひさ／工学部 生命工学科 2年／一人暮らし", discord.TextStyle.short, True, 100),
    ("😵 生活で苦手なこと・朝型/夜型・寝がちな時間", "例：皿が溜まる。完全に夜型で2時就寝", discord.TextStyle.paragraph, False, 200),
    ("🍚 いつものごはん・得意料理・ハマってること", "例：冷凍うどん。カレーは得意。最近はスプラ", discord.TextStyle.paragraph, False, 200),
    ("👹 叱られ方の希望／🤝 できること・してほしいこと", "例：ガツンと来てOK／モーニングコールできます", discord.TextStyle.paragraph, False, 200),
    ("🎯 今学期の一言目標", "例：1限に生きて行く", discord.TextStyle.short, False, 100),
]

def intro_template_embed():
    return discord.Embed(
        title="🙋 自己紹介のテンプレ",
        color=discord.Color.gold(),
        description=(
            "下の **📝 自己紹介を書く／更新する** を押すとフォームが出ます。あとから何度でも更新OK（カードが上書きされます）。\n\n"
            "**① 呼び名・学部学科・学年・生活形態**　例：ひさ／工学部 生命工学科 2年／一人暮らし\n"
            "**② 生活で苦手なこと・朝型/夜型・寝がちな時間**　例：皿が溜まる。完全に夜型で2時就寝\n"
            "**③ いつものごはん・得意料理・最近ハマってること**　例：冷凍うどん。カレーは得意。スプラ\n"
            "**④ 叱られ方の希望／できること・してほしいこと**　例：ガツンと来てOK／モーニングコールできます\n"
            "**⑤ 今学期の一言目標**　例：1限に生きて行く\n\n"
            "カードには起床目標・連続達成・履修科目数・ロールが自動で添えられます。"
            "書き終わったら、#ロール🏷 のリアクションで **学部・学年・生活形態のロール** も付けてね。\n"
            "※ 本名・住所・連絡先は書かないでね。"
        ))

async def intro_card(member, row):
    emb = discord.Embed(title=f"🙋 {member.display_name}", color=discord.Color.gold())
    try:
        emb.set_thumbnail(url=member.display_avatar.url)
    except Exception:
        pass
    for (label, _, _, _, _), key in zip(INTRO_FIELDS, ("f1", "f2", "f3", "f4", "f5")):
        v = (row[key] or "").strip()
        if v:
            emb.add_field(name=label, value=v[:1024], inline=False)
    u = await get_user(member.id)
    day = day_str(now_jst())
    streak = await streak_of(member.id, day)
    n = len(await user_course_rows(member.id))
    tags = [r.name for r in getattr(member, "roles", []) if any(_norm_role(r.name) == _norm_role(nm) for _, items in ROLE_GROUPS.values() for _, nm, _ in items)]
    foot = f"☀️ 起床目標 {u['wake_deadline'] if u and u['wake_deadline'] else '未設定'} ・ 🔥 {streak}日連続 ・ 📚 履修 {n}科目"
    if tags:
        foot += " ・ 🏷 " + " ".join(tags)
    emb.set_footer(text=foot)
    return emb

async def post_intro_card(member):
    async with db.execute("SELECT * FROM intros WHERE user_id=?", (str(member.id),)) as c:
        row = await c.fetchone()
    ch = await get_ch("jikoshokai")
    if not row or not ch:
        return None
    emb = await intro_card(member, row)
    if row["msg_id"]:
        try:
            m = await ch.fetch_message(int(row["msg_id"]))
            await m.edit(embed=emb)
            return m
        except Exception:
            pass
    m = await ch.send(embed=emb)
    await db.execute("UPDATE intros SET msg_id=? WHERE user_id=?", (str(m.id), str(member.id)))
    await db.commit()
    await bump_intro_panel(ch)
    return m

async def bump_intro_panel(ch):
    """テンプレ＋📝ボタンをチャンネルの一番下に置き直す（カードで流れないように）"""
    mid = await meta_get("intro_panel")
    if mid:
        try:
            old = await ch.fetch_message(int(mid))
            await old.delete()
        except Exception:
            pass
    m = await ch.send(embed=intro_template_embed(), view=IntroView())
    await meta_set("intro_panel", m.id)

class IntroModal(discord.ui.Modal, title="🙋 自己紹介"):
    def __init__(self, defaults=None):
        super().__init__()
        defaults = defaults or {}
        self.inputs = []
        for i, (label, ph, style, req, mx) in enumerate(INTRO_FIELDS, 1):
            ti = discord.ui.TextInput(label=label[:45], placeholder=ph[:100], style=style, required=req, max_length=mx,
                                      default=(defaults.get(f"f{i}") or None))
            self.add_item(ti)
            self.inputs.append(ti)

    async def on_submit(self, interaction):
        member = interaction.user
        await ensure_user(member)
        vals = [t.value.strip() for t in self.inputs]
        await db.execute("INSERT INTO intros(user_id,f1,f2,f3,f4,f5,updated_at) VALUES(?,?,?,?,?,?,?) "
                         "ON CONFLICT(user_id) DO UPDATE SET f1=excluded.f1,f2=excluded.f2,f3=excluded.f3,f4=excluded.f4,f5=excluded.f5,updated_at=excluded.updated_at",
                         (str(member.id), *vals, int(now_jst().timestamp())))
        await db.commit()
        m = await post_intro_card(member)
        await interaction.response.send_message("✅ 自己紹介を投稿しました！" + (f" → {m.jump_url}" if m else "（#自己紹介🙋 が未設定です。/setup を実行してください）"), ephemeral=True)

async def open_intro_modal(interaction):
    async with db.execute("SELECT * FROM intros WHERE user_id=?", (str(interaction.user.id),)) as c:
        row = await c.fetchone()
    await interaction.response.send_modal(IntroModal(dict(row) if row else None))

class IntroView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 自己紹介を書く／更新する", style=discord.ButtonStyle.primary, custom_id="sk_intro")
    async def intro(self, interaction, button):
        await open_intro_modal(interaction)

@bot.tree.command(name="jikoshokai", description="自己紹介を書く／更新する（#自己紹介🙋 にカードが投稿されます）")
async def jikoshokai_command(interaction):
    await open_intro_modal(interaction)


# ============================================================
#  今日のえらい（最低限とは別の「がんばった」報告と褒め合い。判定対象外）
# ============================================================
async def _praisers(eid):
    async with db.execute("SELECT user_id FROM effort_praise WHERE effort_id=?", (eid,)) as c:
        return [r["user_id"] for r in await c.fetchall()]

def _effort_content(name, text, praisers, names):
    line = f"🌟 **{name}** の「今日のえらい」\n> {text}"
    if praisers:
        line += f"\n👏 × {len(praisers)}　{'、'.join(names)}"
    return line

async def _praiser_names(uids):
    names = []
    for uid in uids:
        u = await get_user(uid)
        names.append((u["name"] if u else None) or "？")
    return names

class PraiseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_praise:(?P<eid>\d+)"):
    def __init__(self, eid, count=0):
        super().__init__(discord.ui.Button(label=f"👏 えらい！（{count}）", style=discord.ButtonStyle.success,
                                           custom_id=f"sk_praise:{eid}"))
        self.eid = int(eid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["eid"])

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        async with db.execute("SELECT user_id FROM efforts WHERE id=?", (self.eid,)) as c:
            row = await c.fetchone()
        if not row:
            await interaction.response.send_message("この報告は見つかりませんでした。", ephemeral=True)
            return
        if row["user_id"] == uid:
            await interaction.response.send_message("自分で自分を褒めるのは、心の中でどうぞ 🌟", ephemeral=True)
            return
        await ensure_user(interaction.user)
        if uid in await _praisers(self.eid):
            await db.execute("DELETE FROM effort_praise WHERE effort_id=? AND user_id=?", (self.eid, uid))
            note = "👏 を取り消しました。"
        else:
            await db.execute("INSERT OR IGNORE INTO effort_praise(effort_id,user_id,day) VALUES(?,?,?)", (self.eid, uid, day_str(now_jst())))
            note = "褒めました！ 👏"
        await db.commit()
        praisers = await _praisers(self.eid)
        names = await _praiser_names(praisers)
        author = await get_user(row["user_id"])
        async with db.execute("SELECT text FROM efforts WHERE id=?", (self.eid,)) as c:
            text = (await c.fetchone())["text"]
        view = discord.ui.View(timeout=None)
        view.add_item(PraiseButton(self.eid, len(praisers)))
        try:
            await interaction.response.edit_message(
                content=_effort_content((author["name"] if author else "？"), text, praisers, names), view=view)
        except Exception:
            await interaction.response.send_message(note, ephemeral=True)

class EraiModal(discord.ui.Modal, title="🌟 今日のえらい"):
    what = discord.ui.TextInput(label="今日がんばったこと（最低限じゃなくてOK）", style=discord.TextStyle.paragraph,
                                placeholder="例：授業に全部出た／課題を締切3日前に出した／散歩した", required=True, max_length=200)

    async def on_submit(self, interaction):
        user = interaction.user
        await ensure_user(user)
        now = now_jst()
        text = self.what.value.strip()
        cur = await db.execute("INSERT INTO efforts(user_id,text,day,ts) VALUES(?,?,?,?)",
                               (str(user.id), text, day_str(now), int(now.timestamp())))
        eid = cur.lastrowid
        await db.commit()
        ch = await get_ch("erai")
        if not ch:
            await interaction.response.send_message("記録しました（#えらい🌟 が未設定です。/setup を実行してください）", ephemeral=True)
            return
        view = discord.ui.View(timeout=None)
        view.add_item(PraiseButton(eid, 0))
        msg = await ch.send(_effort_content(user.display_name, text, [], []), view=view)
        await db.execute("UPDATE efforts SET msg_id=? WHERE id=?", (str(msg.id), eid))
        await db.commit()
        try:
            await msg.add_reaction(erai_emoji(interaction.guild))
        except Exception:
            pass
        await interaction.response.send_message("🌟 投稿しました！えらい！", ephemeral=True)
        await bump_panel("erai")

class EraiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🌟 今日がんばったことを報告", style=discord.ButtonStyle.primary, custom_id="sk_erai")
    async def erai(self, interaction, button):
        await interaction.response.send_modal(EraiModal())

VIEW_FACTORY["erai"] = EraiView

@bot.tree.command(name="erai", description="今日がんばったことを報告する（最低限とは別・判定なし・褒め合い用）")
async def erai_command(interaction):
    await interaction.response.send_modal(EraiModal())

# ============================================================
#  スラッシュコマンド
# ============================================================
@bot.tree.command(name="setup", description="【管理者用】チャンネル一式とパネルを作成します（何度実行しても安全）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    SETUP_ERRORS.clear()
    cat = discord.utils.get(guild.categories, name=CATEGORY_NAME) or await guild.create_category(CATEGORY_NAME)
    made = []
    renamed = []
    async def find_or_rename(key, name, old_name, chans, create):
        ch = None
        old_id = await meta_get("ch_" + key)
        if old_id:
            ch = guild.get_channel(int(old_id))
        if ch is None:
            ch = discord.utils.get(chans, name=name) or discord.utils.get(chans, name=old_name)
        if ch is None:
            ch = await create(name)
            made.append(name)
        elif ch.name != name:
            before = ch.name
            try:
                await ch.edit(name=name, reason="チャンネル名を絵文字つきに変更")
                renamed.append(f"#{before} → #{name}")
            except Exception as e:
                print(f"改名失敗 {before}: {e!r}", flush=True)
        await meta_set("ch_" + key, ch.id)
        return ch
    for key, (name, _) in CH.items():
        await find_or_rename(key, name, OLD_CH_NAMES.get(key, name), guild.text_channels,
                             lambda n: guild.create_text_channel(n, category=cat))
    vc = await find_or_rename("radio", RADIO_VC_NAME, OLD_RADIO_VC_NAME, guild.voice_channels,
                              lambda n: guild.create_voice_channel(n, category=cat))
    haj = await get_ch("hajimeni")
    if haj:
        await make_readonly(haj, guild, "ガイド用チャンネルは読み取り専用")
        for i, emb in enumerate(tutorial_embeds()):
            await upsert_message(f"tutorial_{i}", haj, emb)
    role_note = ""
    jik = await get_ch("jikoshokai")
    rch = await get_ch("roles")
    if jik:
        await upsert_message("intro_panel", jik, intro_template_embed(), view=IntroView())
    if rch:
        await make_readonly(rch, guild, "ロール選択はリアクションのみ")
        created = await ensure_roles(guild)
        created += await ensure_title_roles(guild)
        await post_role_panels(rch, old_channels=[c for c in (jik,) if c])
        n_panels = 0
        for gk in ROLE_GROUPS:
            if await meta_get(f"rolemsg_{gk}"):
                n_panels += 1
        role_note = "ロール：" + (f"作成 {', '.join(created)}" if created else "既存を利用") + f"／パネル {n_panels}/3 件 → {rch.mention}\n"
    audio_state = "あり ✅" if os.path.exists(RADIO_MP3) else f"なし ⚠️ VMに {RADIO_MP3} を置いてください"
    await meta_set("guild_id", guild.id)
    for key in VIEW_FACTORY:
        await bump_panel(key)
    settei = await get_ch("settei")
    if settei:
        await upsert_message("guide_msg", settei, discord.Embed(
            title="🛠 最低限の決め方",
            description=(
                "`/saitei` で **自分の最低限** を設定します（指定した項目だけ更新）。\n"
                "例：`/saitei kishou:7:00 suimin:6 nyuyoku:True shokuji:2 kaji:3`\n\n"
                "・**kishou** 起床の締切（`なし` で解除）\n"
                "・**suimin** 最低睡眠時間 h（0で解除）\n"
                "・**nyuyoku** 毎日入浴する（True/False）\n"
                "・**hamigaki** 1日の最低歯磨き回数 例 2（0で解除）\n"
                "・**shokuji** 1日の最低食事回数 1〜3（0で解除。間食は数えない）\n"
                "・**kaji** 週の最低家事回数（合計・0で解除。日曜夜に判定）\n"
                "　種類ごとに決めたい人は `/kaji ryouri:1 sentaku:3 souji:7 gomi:3`（N日に1回・毎日判定）\n"
                "・**rajio** 毎朝のラジオ体操に参加する（True/False）\n\n"
                f"🏃 ラジオ体操は毎朝 **{RADIO_TIME}** に 🔊ラジオ体操🏃 で自動再生。#起床🌅 の 🏃 ボタンで呼び出し（メンション）のON/OFF。\n"
                "📚 **課題**：`/jikanwari add` で履修科目を登録（科目名で検索・最大5つずつ）→ 気づいた人が `/kadai add` で課題を登録すると、"
                f"同じ科目の履修者だけに #課題📚 で通知。3日前・前日・当日 {REMIND_HOUR}:00 に未完了の人へリマインド。投稿の ✅ で完了。\n\n"
                "🔥 **連続達成**：判定で未達ゼロの日が続くと連続日数が伸びる（3・7・14・30日…で祝福）。`/kiroku` で確認。\n"
                "📮 **通信簿**：毎週日曜の判定後に #つうしんぼ📮 へ達成率ランキング・各賞・起床/睡眠グラフ。月末は月間表彰（MVP）。\n"
                "🛌 **お休み申告**：`/oyasumi riyuu:帰省 hi:10/15-10/17` → その日は判定されず、連続達成も途切れない。\n"
                "📝 **自分だけの項目**：`/kojin add namae:薬を飲む` → #設定🔧 の 📝 ボタンで毎日チェック。未チェックは判定対象。\n"
                "🛌 **休日設定**：`/saitei kyujitsu:2` で土日は起床締切を2時間遅らせる。\n"
                f"{'🎮 達成した日は みんなで暗記！！ で 🎫メダル（連続ボーナスあり）。🌅生活ランキングにも反映。' if GAKUSHU_SECRET else ''}\n"
                f"毎晩 **{JUDGE_HOUR}:00** に判定し、守れなかった人は #叱責👹 に名指しで晒されます。\n"
                "`/nakama` で同じ起床時刻の仲間が見られます。`/kiroku` で自分の記録を確認。"
            ), color=discord.Color.gold()))
    await interaction.followup.send(
        "✅ セットアップ完了\n" + (f"改名：{', '.join(renamed)}\n" if renamed else "") + (f"新規作成：{', '.join('#' + n for n in made)}\n" if made else "既存チャンネルを再利用しました\n")
        + f"判定時刻：毎晩 {JUDGE_HOUR}:00 → #叱責👹\n"
        + f"叱り絵文字：`:{KORA_EMOJI_NAME}:`（{kora_emoji(guild)}）\n"
        + role_note
        + f"ラジオ体操：毎朝 {RADIO_TIME} に {vc.mention} で再生（音源 {audio_state}）"
        + ("\n⚠️ " + "\n⚠️ ".join(dict.fromkeys(SETUP_ERRORS)) if SETUP_ERRORS else ""), ephemeral=True)

async def nakama_of(uid, wake_deadline):
    if not wake_deadline:
        return []
    async with db.execute("SELECT name FROM users WHERE wake_deadline=? AND id<>? ORDER BY name", (wake_deadline, str(uid))) as c:
        return [r["name"] for r in await c.fetchall()]

@bot.tree.command(name="saitei", description="自分の「最低限」を設定する（指定した項目だけ更新）")
@app_commands.describe(kishou="起床の締切 例 7:00（「なし」で解除）", suimin="最低睡眠時間(h) 例 6（0で解除）",
                       nyuyoku="毎日入浴する", shokuji="1日の最低食事回数 1〜3（0で解除）", kaji="週の最低家事回数（0で解除）",
                       rajio="毎朝のラジオ体操に参加する", kyujitsu="土日は起床締切を何時間遅らせるか 例 2（0で解除）",
                       hamigaki="1日の最低歯磨き回数 例 2（0で解除）")
async def saitei_command(interaction, kishou: str = None, suimin: float = None, nyuyoku: bool = None,
                         shokuji: int = None, kaji: int = None, rajio: bool = None, kyujitsu: float = None, hamigaki: int = None):
    user = interaction.user
    await ensure_user(user)
    today = day_str(now_jst())
    if kishou is not None:
        if kishou.strip() in ("なし", "none", "-", "0"):
            await db.execute("UPDATE users SET wake_deadline=NULL WHERE id=?", (str(user.id),))
        else:
            hh = parse_hhmm(kishou)
            if not hh:
                await interaction.response.send_message("⚠️ 起床時刻の形式が読めませんでした（例 7:00）", ephemeral=True)
                return
            await db.execute("UPDATE users SET wake_deadline=?, wake_set_day=? WHERE id=?", (hh, today, str(user.id)))
    if suimin is not None:
        await db.execute("UPDATE users SET sleep_min=?, sleep_set_day=? WHERE id=?", (suimin if suimin > 0 else None, today, str(user.id)))
    if nyuyoku is not None:
        await db.execute("UPDATE users SET bath_daily=?, bath_set_day=? WHERE id=?", (1 if nyuyoku else 0, today, str(user.id)))
    if rajio is not None:
        await db.execute("UPDATE users SET radio_daily=?, radio_set_day=? WHERE id=?", (1 if rajio else 0, today, str(user.id)))
    if hamigaki is not None:
        await db.execute("UPDATE users SET teeth_min=?, teeth_set_day=? WHERE id=?", (max(0, min(10, hamigaki)), today, str(user.id)))
    if kyujitsu is not None:
        await db.execute("UPDATE users SET holiday_shift=?, wake_set_day=? WHERE id=?", (int(max(0.0, min(12.0, kyujitsu)) * 60), today, str(user.id)))
    if shokuji is not None:
        await db.execute("UPDATE users SET meals_min=?, meals_set_day=? WHERE id=?", (min(3, shokuji) if shokuji > 0 else None, today, str(user.id)))
    if kaji is not None:
        await db.execute("UPDATE users SET chores_week=?, chores_set_day=? WHERE id=?", (kaji if kaji > 0 else None, today, str(user.id)))
    await db.commit()
    u = await get_user(user.id)
    mates = await nakama_of(user.id, u["wake_deadline"])
    mate_txt = ""
    if u["wake_deadline"]:
        mate_txt = f"\n\n👥 同じ {u['wake_deadline']} 起床の仲間：" + ("、".join(mates) if mates else "まだいない（最初の一人！）")
    changed_any = any(v is not None for v in (kishou, suimin, nyuyoku, shokuji, kaji, rajio, kyujitsu, hamigaki))
    grace = "\n\n🍀 今日設定・変更した項目は、**明日から**判定に入ります（初日から叱られない仕様）。" if changed_any else ""
    await interaction.response.send_message(f"🛠 **{user.display_name} の最低限**\n{settings_text(u)}{mate_txt}{grace}", ephemeral=True)
    if any(v is not None for v in (kishou, suimin, nyuyoku, shokuji, kaji, rajio, kyujitsu, hamigaki)):
        settei = await get_ch("settei")
        if settei:
            line = f"🛠 **{user.display_name}** が最低限を更新：" + " / ".join(settings_text(u).split("\n"))
            if u["wake_deadline"] and mates:
                line += f"\n　👥 同じ {u['wake_deadline']} 起床：{'、'.join(mates)}"
            await settei.send(line)

@bot.tree.command(name="nakama", description="起床時刻ごとの仲間一覧を表示")
async def nakama_command(interaction):
    async with db.execute("SELECT name, wake_deadline FROM users ORDER BY wake_deadline, name") as c:
        rows = await c.fetchall()
    groups = {}
    unset = []
    for r in rows:
        if r["wake_deadline"]:
            groups.setdefault(r["wake_deadline"], []).append(r["name"])
        else:
            unset.append(r["name"])
    if not groups:
        await interaction.response.send_message("まだ誰も起床時刻を設定していません。`/saitei kishou:7:00` のように決めよう！")
        return
    lines = [f"**{t}** 起床（{len(names)}人）：{'、'.join(names)}" for t, names in sorted(groups.items())]
    if unset:
        lines.append(f"未設定：{'、'.join(unset)}")
    await interaction.response.send_message(embed=discord.Embed(title="👥 起床時刻の仲間", description="\n".join(lines), color=discord.Color.gold()))

@bot.tree.command(name="kiroku", description="自分の今日・今週の記録を確認")
async def kiroku_command(interaction):
    user = interaction.user
    await ensure_user(user)
    now = now_jst()
    day = day_str(now)
    d1, d2 = week_range(now)
    w = await events_on(user.id, day, "wake")
    s = await events_on(user.id, day, "sleep")
    meals = await events_on(user.id, day, "meal")
    chores = await events_on(user.id, day, "chore")
    bath = await events_on(user.id, day, "bath")
    radio = await events_on(user.id, day, "radio")
    today = [
        "☀️ 起床：" + (hhmm(datetime.fromtimestamp(w[0]["ts"], JST)) if w else "未報告"),
        "🌙 睡眠：" + (fmt_hours(float(s[-1]["note"])) if s else "未記録"),
        "🍚 食事：" + ("、".join(f"{m['sub']}" + (f"({m['note']})" if m["note"] else "") for m in meals) if meals else "未報告"),
        "🧹 家事：" + ("、".join(CHORE_LABEL[c["sub"]] for c in chores) if chores else "なし"),
        "🛁 入浴：" + ((f"済（{len(bath)}回）" if len(bath) >= 2 else "済") if bath else "未報告"),
        "🪥 歯磨き：" + (f"{len(await events_on(user.id, day, 'teeth'))} 回"),
        "😴 二度寝：" + (lambda k: f"{k} 回（本日 {k + 1} 度寝）" if k else "なし（えらい）")(len(await events_on(user.id, day, "nizone"))),
        "🏃 ラジオ体操：" + ("参加" if radio else "—"),
        "🌟 今日のえらい：" + str(len(await db.execute_fetchall("SELECT 1 FROM efforts WHERE user_id=? AND day=?", (str(user.id), day)))) + " 件",
    ]
    async with db.execute("SELECT COUNT(*) AS n FROM custom_items WHERE user_id=?", (str(user.id),)) as c:
        n_items = (await c.fetchone())["n"]
    if n_items:
        async with db.execute("SELECT COUNT(*) AS n FROM custom_checks WHERE user_id=? AND day=?", (str(user.id), day)) as c:
            n_done = (await c.fetchone())["n"]
        today.append(f"📝 個人項目：✅ {n_done}/{n_items}")
    async with db.execute("SELECT reason FROM off_days WHERE user_id=? AND day=?", (str(user.id), day)) as c:
        offr = await c.fetchone()
    if offr:
        today.append(f"🛌 今日はお休み申告中（{offr['reason'] or '理由なし'}）")
    wake_days = await count_events_between(user.id, "wake", d1, d2)
    chore_n = await count_events_between(user.id, "chore", d1, d2)
    async with db.execute("SELECT AVG(CAST(note AS REAL)) AS a FROM events WHERE user_id=? AND kind='sleep' AND day BETWEEN ? AND ?",
                          (str(user.id), d1, d2)) as c:
        avg = (await c.fetchone())["a"]
    week = [f"起床報告 {wake_days} 日", f"平均睡眠 {fmt_hours(avg) if avg else '—'}", f"家事 {chore_n} 回"]
    u = await get_user(user.id)
    emb = discord.Embed(title=f"📖 {user.display_name} の記録", color=discord.Color.gold())
    emb.add_field(name=f"今日（{day}）", value="\n".join(today), inline=False)
    emb.add_field(name=f"今週（{d1}〜{d2}）", value=" / ".join(week), inline=False)
    st = await streak_of(user.id, day)
    emb.add_field(name="🔥 連続達成", value=f"{st} 日（自己ベスト {max(st, u['best_streak'] or 0)} 日）", inline=False)
    emb.add_field(name="最低限の設定", value=settings_text(u), inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)

@bot.tree.command(name="rajio", description="【管理者用】今すぐラジオ体操を流す（テスト用）")
@app_commands.checks.has_permissions(administrator=True)
async def rajio_command(interaction):
    await interaction.response.defer(ephemeral=True)
    res = await play_radio(interaction.guild, manual=True)
    await interaction.followup.send(res, ephemeral=True)

@bot.tree.command(name="tsushinbo", description="【管理者用】今週の通信簿を今すぐ投稿する（テスト用）")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(tsuki="True にすると今月の月間表彰を投稿")
async def tsushinbo_command(interaction, tsuki: bool = False):
    await interaction.response.defer(ephemeral=True)
    res = await (monthly_summary if tsuki else weekly_summary)(interaction.guild, manual=True)
    await interaction.followup.send(res, ephemeral=True)

@bot.tree.command(name="hantei", description="【管理者用】今すぐ判定を実行する（テスト用）")
@app_commands.checks.has_permissions(administrator=True)
async def hantei_command(interaction):
    await interaction.response.defer(ephemeral=True)
    res = await judge(interaction.guild, manual=True)
    await interaction.followup.send(res, ephemeral=True)

@bot.tree.error
async def on_app_command_error(interaction, error):
    if isinstance(error, app_commands.MissingPermissions):
        msg = "❌ このコマンドは管理者のみ使えます。"
    else:
        msg = f"❌ エラー: {error}"
        if "50013" in str(error) or "Missing Permissions" in str(error):
            msg += "\n💡 Botの権限不足です。サーバー設定→ロール→Botのロールに「チャンネルの管理」「ロールの管理」「メッセージの送信」「メッセージの管理」があるか、Botのロールが付与対象ロールより上にあるか確認してください。"
        print(f"command error: {error!r}", flush=True)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

# --- 起動 ---
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ 環境変数 DISCORD_BOT_TOKEN が設定されていません。.env を確認してください。", flush=True)
