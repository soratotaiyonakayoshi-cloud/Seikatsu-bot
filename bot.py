"""最低限生活リズムサークル Bot

起床・睡眠・家事・食事・入浴を「ボタン1タップ」で記録し、
毎晩の判定で「自分で決めた最低限」を守れなかった人を #こら に晒す。
データは SQLite（VM上のファイル）に保存。外部サービス不要。
"""
import discord
from discord import app_commands
from discord.ext import tasks
import aiosqlite
import asyncio
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
RADIO_TIME = os.getenv("RADIO_TIME", "06:30")          # ラジオ体操の開始時刻(HH:MM)
RADIO_MP3 = os.getenv("RADIO_MP3", "radio.mp3")        # 音源ファイル（リポジトリには含めない。VMに直接置く）
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
RADIO_VC_NAME = "ラジオ体操"
REMIND_HOUR = int(os.getenv("REMIND_HOUR", "8"))     # 課題リマインドを流す時刻（時）
GAKUSHU_URL = os.getenv("GAKUSHU_URL", "https://gakushu-rpg.pages.dev")   # みんなで暗記！！連携先
GAKUSHU_SECRET = os.getenv("GAKUSHU_SECRET", "")     # Cloudflare側 VC_SECRET と同じ値。空なら連携オフ
COURSES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses_2026_kouki.json")
JST = timezone(timedelta(hours=9))

CATEGORY_NAME = "最低限生活リズム"
# key -> (チャンネル名, パネルの説明)
CH = {
    "wake": ("起床", "☀️ 起きたら押す／🌙 寝る前に押す。睡眠時間は自動で計算されます。\n🏃 で毎朝のラジオ体操の呼び出し（メンション）をON/OFF。"),
    "meal": ("ごはん", "🍚 食べたら押す。**写真を投げるだけ**でも時間帯から自動で記録されます。"),
    "chore": ("家事", "🧹 やった家事を押す。洗濯は5工程に分かれています。"),
    "bath": ("おふろ", "🛁 お風呂に入ったら押す。"),
    "kora": ("こら", "毎晩の判定で、最低限を守れなかった人が晒される場所。"),
    "tsushinbo": ("つうしんぼ", "毎週日曜の夜に、その週の通信簿（達成率ランキング・各賞）が届く場所。"),
    "kadai": ("課題", "`/jikanwari add` で履修科目を登録 → 気づいた人が `/kadai add` → 同じ科目の履修者だけに通知＆リマインド。"),
    "settei": ("設定", "`/saitei` で自分の最低限を決める。`/nakama` で同じ時間の仲間を見る。"),
}
CHORES = [  # (key, ラベル, 絵文字, 行)
    ("cook", "料理", "🍳", 0), ("clean", "掃除", "🧹", 0), ("dish", "皿洗い", "🍽️", 0),
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
              "ALTER TABLE users ADD COLUMN best_streak INTEGER NOT NULL DEFAULT 0"):
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
    await db.execute("INSERT INTO events(user_id,kind,sub,ts,day,note) VALUES(?,?,?,?,?,?)",
                     (str(uid), kind, sub, int(dt.timestamp()), day_str(dt), note))
    await db.commit()

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
async def build_misses(u, day, d1, d2, is_sunday):
    """ユーザーの設定と当日の記録から、未達項目の文字列リストを返す。"""
    misses = []
    uid = u["id"]
    if u["wake_deadline"]:
        w = await events_on(uid, day, "wake")
        if not w:
            misses.append(f"☀️ 起床 未報告（{u['wake_deadline']} まで）")
        else:
            t = hhmm(datetime.fromtimestamp(w[0]["ts"], JST))
            if t > u["wake_deadline"]:
                misses.append(f"☀️ 寝坊 {u['wake_deadline']} まで → {t}")
    if u["sleep_min"]:
        s = await events_on(uid, day, "sleep")
        if not s:
            misses.append("🌙 睡眠時間 未報告")
        else:
            hrs = float(s[-1]["note"] or 0)
            if hrs < u["sleep_min"]:
                misses.append(f"🌙 睡眠不足 {fmt_hours(hrs)}（最低 {fmt_hours(u['sleep_min'])}）")
    if u["bath_daily"]:
        if not await events_on(uid, day, "bath"):
            misses.append("🛁 入浴 未報告")
    if u["radio_daily"]:
        if not await events_on(uid, day, "radio"):
            misses.append("🏃 ラジオ体操 未参加")
    if u["meals_min"]:
        n = len(await distinct_main_meals(uid, day))
        if n < u["meals_min"]:
            misses.append(f"🍚 食事 {n}/{u['meals_min']} 回")
    if u["chores_week"] and is_sunday:
        n = await count_events_between(uid, "chore", d1, d2)
        if n < u["chores_week"]:
            misses.append(f"🧹 家事 今週 {n}/{u['chores_week']} 回")
    return misses

def has_any_setting(u):
    return bool(u["wake_deadline"] or u["sleep_min"] or u["bath_daily"] or u["meals_min"] or u["chores_week"] or u["radio_daily"])

def settings_text(u):
    parts = []
    parts.append(f"☀️ 起床 {u['wake_deadline']} まで" if u["wake_deadline"] else "☀️ 起床 —")
    parts.append(f"🌙 睡眠 {fmt_hours(u['sleep_min'])} 以上" if u["sleep_min"] else "🌙 睡眠 —")
    parts.append("🛁 入浴 毎日" if u["bath_daily"] else "🛁 入浴 —")
    parts.append("🏃 ラジオ体操 毎日" if u["radio_daily"] else "🏃 ラジオ体操 —")
    parts.append(f"🍚 食事 1日{u['meals_min']}回" if u["meals_min"] else "🍚 食事 —")
    parts.append(f"🧹 家事 週{u['chores_week']}回" if u["chores_week"] else "🧹 家事 —")
    return "\n".join(parts)

# ============================================================
#  Bot 本体
# ============================================================
class SeikatsuBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True  # #ごはん の写真投稿検知に必要（Developer PortalでONにする）
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await db_init()
        self.add_view(WakeView())
        self.add_view(MealView())
        self.add_view(ChoreView())
        self.add_view(BathView())
        self.add_dynamic_items(DoneButton)
        # コマンドの同期はグローバルではなくサーバー単位で行う（即時反映）。on_ready 参照。

bot = SeikatsuBot()

def kora_emoji(guild):
    e = discord.utils.get(guild.emojis, name=KORA_EMOJI_NAME) if guild else None
    return str(e) if e else "👹"

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
    if u and u["wake_deadline"] and hhmm(wake_dt) > u["wake_deadline"]:
        late = f" ⚠️ 締切 {u['wake_deadline']} を過ぎてます"
    msg = f"✅ {hhmm(wake_dt)} 起床（{sleep_txt}）{late}"
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
            await interaction.response.send_message(f"今日はもう {t} に起床報告済みです。", ephemeral=True)
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
        await interaction.response.send_message(f"✅ {self.sub}ごはんを記録しました。", ephemeral=True)
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
        if await events_on(user.id, day_str(now), "bath"):
            await interaction.response.send_message("今日はもう入浴報告済みです。きれい好き！", ephemeral=True)
            return
        await add_event(user.id, "bath")
        await interaction.response.send_message(f"✅ {hhmm(now)} 入浴を記録しました。", ephemeral=True)
        await post_log("bath", f"🛁 **{user.display_name}** {hhmm(now)} 入浴")

VIEW_FACTORY.update({"wake": WakeView, "meal": MealView, "chore": ChoreView, "bath": BathView})

# ------------------------------------------------------------
#  #ごはん に写真を投げたら自動記録
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
    await add_event(message.author.id, "meal", sub, note=(message.content.strip() or "写真")[:100])
    try:
        await message.add_reaction("✅")
    except Exception:
        pass
    await message.reply(f"{MEAL_EMOJI[sub]} {sub}ごはんとして記録しました（違ってたらボタンで報告してね）", mention_author=False)
    await bump_panel("meal")

# ------------------------------------------------------------
#  毎晩の判定 → #こら
# ------------------------------------------------------------
async def streak_of(uid, day):
    """day を含めて遡った連続達成日数（記録の無い日・未達の日で途切れる）"""
    async with db.execute("SELECT day, achieved FROM daily_results WHERE user_id=? AND day<=? ORDER BY day DESC LIMIT 400",
                          (str(uid), day)) as c:
        rows = await c.fetchall()
    n, expect = 0, date.fromisoformat(day)
    for r in rows:
        if date.fromisoformat(r["day"]) != expect or not r["achieved"]:
            break
        n += 1
        expect -= timedelta(days=1)
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
        return "❌ #こら チャンネルが未設定です（/setup を実行してください）"
    async with db.execute("SELECT * FROM users") as c:
        users = [u for u in await c.fetchall() if has_any_setting(u)]
    emoji = kora_emoji(guild)
    results, achievers, celebrate = [], [], []
    for u in users:
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
                celebrate.append(f"🎊 <@{u['id']}> が **{streak}日連続** 達成！")
        await gakushu_report(u["id"], u["name"] or "", day, achieved, streak, len(misses))
    tag = "（手動判定）" if manual else ""
    if not users:
        await kora_ch.send(f"📋 {day} の判定{tag}：まだ誰も最低限を設定していません。`/saitei` で決めよう。")
    elif not results:
        await kora_ch.send(f"🎉 {day} の判定{tag}：**全員が最低限を守りました！** えらい！！")
    else:
        await kora_ch.send(f"📋 {day} の判定{tag}：{len(results)}/{len(users)} 人が最低限を守れませんでした。")
        for u, misses in results:
            await kora_ch.send(f"{emoji} <@{u['id']}> **こら！**\n" + "\n".join("・" + m for m in misses))
    if achievers:
        achievers.sort(key=lambda x: -x[1])
        await kora_ch.send("✨ 達成：" + "、".join(f"**{u['name']}**" + (f" 🔥{st}日" if st >= 2 else "") for u, st in achievers))
    for line in celebrate:
        await kora_ch.send(line)
    return f"判定完了：{len(results)}/{len(users)} 人が未達"

# ------------------------------------------------------------
#  週次通信簿（日曜の判定後に自動投稿。/tsushinbo で手動）
# ------------------------------------------------------------
async def weekly_summary(guild, manual=False):
    now = now_jst()
    d1, d2 = week_range(now)
    ch = await get_ch("tsushinbo") or await get_ch("kora")
    if not ch:
        return "❌ #つうしんぼ が未設定です（/setup を実行してください）"
    async with db.execute("SELECT user_id, COUNT(*) AS judged, SUM(achieved) AS ach, GROUP_CONCAT(misses, '\n') AS mtext "
                          "FROM daily_results WHERE day BETWEEN ? AND ? GROUP BY user_id", (d1, d2)) as c:
        jr = {r["user_id"]: r for r in await c.fetchall()}
    async with db.execute("SELECT DISTINCT user_id FROM events WHERE day BETWEEN ? AND ?", (d1, d2)) as c:
        uids = {r["user_id"] for r in await c.fetchall()} | set(jr)
    stats = []
    for uid in uids:
        u = await get_user(uid)
        name = (u["name"] if u else None) or uid
        async with db.execute("SELECT kind, sub, ts, day, note FROM events WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
            evs = await c.fetchall()
        wakes = [datetime.fromtimestamp(e["ts"], JST) for e in evs if e["kind"] == "wake"]
        wake_days = {}
        for w in wakes:  # 1日1回目だけ
            wake_days.setdefault(day_str(w), w)
        wake_min = [w.hour * 60 + w.minute for w in wake_days.values()]
        sleeps = [float(e["note"]) for e in evs if e["kind"] == "sleep" and e["note"]]
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
            "radio": sum(1 for e in evs if e["kind"] == "radio"),
            "late": mtext.count("寝坊"), "miss_n": sum(1 for l in mtext.split("\n") if l.strip()),
        })
    if not stats:
        await ch.send(f"📮 今週（{d1}〜{d2}）は記録がありませんでした。")
        return "記録なし"
    judged = [x for x in stats if x["judged"] > 0]
    judged.sort(key=lambda x: (-(x["ach"] / x["judged"]), -x["ach"], -x["streak"]))
    lines = []
    for i, x in enumerate(judged):
        rate = round(x["ach"] * 100 / x["judged"])
        medal = ["🥇", "🥈", "🥉"][i] if i < 3 else f"{i + 1}."
        lines.append(f"{medal} **{x['name']}**　達成 {x['ach']}/{x['judged']}日（{rate}%）" + (f"　🔥{x['streak']}日連続" if x["streak"] >= 2 else ""))
    stats.sort(key=lambda x: x["uid"])
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
    awards = [a for a in (
        ("👑 皆勤賞：" + "、".join(f"**{x['name']}**" for x in judged if x["judged"] >= 3 and x["ach"] == x["judged"])) if any(x["judged"] >= 3 and x["ach"] == x["judged"] for x in judged) else None,
        award("🌅 早起き賞", "wake_avg", pick_min=True, need=lambda x: x["wake_n"] >= 3, fmt=lambda v: f"平均 {int(v)//60}:{int(v)%60:02d}"),
        award("🛌 ぐっすり賞", "sleep_avg", need=lambda x: x["sleep_avg"] is not None, fmt=lambda v: f"平均 {v:.1f}h"),
        award("🧹 家事賞", "chores", fmt=lambda v: f"{v}回"),
        award("🍚 ごはん賞", "meals", fmt=lambda v: f"{v}回報告"),
        award("🛁 きれい好き賞", "baths", fmt=lambda v: f"{v}日"),
        award("🏃 ラジオ体操賞", "radio", fmt=lambda v: f"{v}回"),
        award("🐷 寝坊賞", "late", fmt=lambda v: f"{v}回"),
        award("👹 こら賞", "miss_n", fmt=lambda v: f"未達 {v}件"),
    ) if a]
    emb = discord.Embed(title=f"📮 今週の通信簿（{d1[5:].replace('-', '/')}〜{d2[5:].replace('-', '/')}）" + ("（手動）" if manual else ""),
                        color=discord.Color.gold())
    emb.add_field(name="🏆 最低限 達成率ランキング", value="\n".join(lines)[:1024] if lines else "判定対象の人がいませんでした（/saitei で設定）", inline=False)
    if awards:
        emb.add_field(name="🎖 今週の各賞", value="\n".join(awards)[:1024], inline=False)
    emb.set_footer(text="来週もほどほどに、最低限を守ろう")
    await ch.send(embed=emb)
    return f"通信簿を投稿しました（{len(stats)}人）"

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
        await wake_ch.send(f"🏃 **{RADIO_TIME} ラジオ体操はじまるよ！**{lead} {vc_ch.mention} に集合〜 {mention}".rstrip())
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
#  スラッシュコマンド
# ============================================================
@bot.tree.command(name="setup", description="【管理者用】チャンネル一式とパネルを作成します（何度実行しても安全）")
@app_commands.checks.has_permissions(administrator=True)
async def setup_command(interaction):
    await interaction.response.defer(ephemeral=True)
    guild = interaction.guild
    cat = discord.utils.get(guild.categories, name=CATEGORY_NAME) or await guild.create_category(CATEGORY_NAME)
    made = []
    for key, (name, _) in CH.items():
        ch = discord.utils.get(guild.text_channels, name=name)
        if ch is None:
            ch = await guild.create_text_channel(name, category=cat)
            made.append(name)
        await meta_set("ch_" + key, ch.id)
    vc = discord.utils.get(guild.voice_channels, name=RADIO_VC_NAME)
    if vc is None:
        vc = await guild.create_voice_channel(RADIO_VC_NAME, category=cat)
        made.append("🔊" + RADIO_VC_NAME)
    await meta_set("ch_radio", vc.id)
    audio_state = "あり ✅" if os.path.exists(RADIO_MP3) else f"なし ⚠️ VMに {RADIO_MP3} を置いてください"
    await meta_set("guild_id", guild.id)
    for key in VIEW_FACTORY:
        await bump_panel(key)
    settei = await get_ch("settei")
    if settei:
        await settei.send(embed=discord.Embed(
            title="🛠 最低限の決め方",
            description=(
                "`/saitei` で **自分の最低限** を設定します（指定した項目だけ更新）。\n"
                "例：`/saitei kishou:7:00 suimin:6 nyuyoku:True shokuji:2 kaji:3`\n\n"
                "・**kishou** 起床の締切（`なし` で解除）\n"
                "・**suimin** 最低睡眠時間 h（0で解除）\n"
                "・**nyuyoku** 毎日入浴する（True/False）\n"
                "・**shokuji** 1日の最低食事回数 1〜3（0で解除。間食は数えない）\n"
                "・**kaji** 週の最低家事回数（0で解除。日曜夜に判定）\n"
                "・**rajio** 毎朝のラジオ体操に参加する（True/False）\n\n"
                f"🏃 ラジオ体操は毎朝 **{RADIO_TIME}** に 🔊ラジオ体操 で自動再生。#起床 の 🏃 ボタンで呼び出し（メンション）のON/OFF。\n"
                "📚 **課題**：`/jikanwari add` で履修科目を登録（科目名で検索・最大5つずつ）→ 気づいた人が `/kadai add` で課題を登録すると、"
                f"同じ科目の履修者だけに #課題 で通知。3日前・前日・当日 {REMIND_HOUR}:00 に未完了の人へリマインド。投稿の ✅ で完了。\n\n"
                "🔥 **連続達成**：判定で未達ゼロの日が続くと連続日数が伸びる（3・7・14・30日…で祝福）。`/kiroku` で確認。\n"
                "📮 **通信簿**：毎週日曜の判定後に #つうしんぼ へ達成率ランキングと各賞（早起き賞・寝坊賞…）。\n"
                f"{'🎮 達成した日は みんなで暗記！！ で 🎫メダル（連続ボーナスあり）。🌅生活ランキングにも反映。' if GAKUSHU_SECRET else ''}\n"
                f"毎晩 **{JUDGE_HOUR}:00** に判定し、守れなかった人は #こら に名指しで晒されます。\n"
                "`/nakama` で同じ起床時刻の仲間が見られます。`/kiroku` で自分の記録を確認。"
            ), color=discord.Color.gold()))
    await interaction.followup.send(
        "✅ セットアップ完了\n" + (f"新規作成：{', '.join('#' + n for n in made)}\n" if made else "既存チャンネルを再利用しました\n")
        + f"判定時刻：毎晩 {JUDGE_HOUR}:00 → #こら\n"
        + f"叱り絵文字：`:{KORA_EMOJI_NAME}:`（{kora_emoji(guild)}）\n"
        + f"ラジオ体操：毎朝 {RADIO_TIME} に {vc.mention} で再生（音源 {audio_state}）", ephemeral=True)

async def nakama_of(uid, wake_deadline):
    if not wake_deadline:
        return []
    async with db.execute("SELECT name FROM users WHERE wake_deadline=? AND id<>? ORDER BY name", (wake_deadline, str(uid))) as c:
        return [r["name"] for r in await c.fetchall()]

@bot.tree.command(name="saitei", description="自分の「最低限」を設定する（指定した項目だけ更新）")
@app_commands.describe(kishou="起床の締切 例 7:00（「なし」で解除）", suimin="最低睡眠時間(h) 例 6（0で解除）",
                       nyuyoku="毎日入浴する", shokuji="1日の最低食事回数 1〜3（0で解除）", kaji="週の最低家事回数（0で解除）",
                       rajio="毎朝のラジオ体操に参加する")
async def saitei_command(interaction, kishou: str = None, suimin: float = None, nyuyoku: bool = None,
                         shokuji: int = None, kaji: int = None, rajio: bool = None):
    user = interaction.user
    await ensure_user(user)
    if kishou is not None:
        if kishou.strip() in ("なし", "none", "-", "0"):
            await db.execute("UPDATE users SET wake_deadline=NULL WHERE id=?", (str(user.id),))
        else:
            hh = parse_hhmm(kishou)
            if not hh:
                await interaction.response.send_message("⚠️ 起床時刻の形式が読めませんでした（例 7:00）", ephemeral=True)
                return
            await db.execute("UPDATE users SET wake_deadline=? WHERE id=?", (hh, str(user.id)))
    if suimin is not None:
        await db.execute("UPDATE users SET sleep_min=? WHERE id=?", (suimin if suimin > 0 else None, str(user.id)))
    if nyuyoku is not None:
        await db.execute("UPDATE users SET bath_daily=? WHERE id=?", (1 if nyuyoku else 0, str(user.id)))
    if rajio is not None:
        await db.execute("UPDATE users SET radio_daily=? WHERE id=?", (1 if rajio else 0, str(user.id)))
    if shokuji is not None:
        await db.execute("UPDATE users SET meals_min=? WHERE id=?", (min(3, shokuji) if shokuji > 0 else None, str(user.id)))
    if kaji is not None:
        await db.execute("UPDATE users SET chores_week=? WHERE id=?", (kaji if kaji > 0 else None, str(user.id)))
    await db.commit()
    u = await get_user(user.id)
    mates = await nakama_of(user.id, u["wake_deadline"])
    mate_txt = ""
    if u["wake_deadline"]:
        mate_txt = f"\n\n👥 同じ {u['wake_deadline']} 起床の仲間：" + ("、".join(mates) if mates else "まだいない（最初の一人！）")
    await interaction.response.send_message(f"🛠 **{user.display_name} の最低限**\n{settings_text(u)}{mate_txt}", ephemeral=True)
    if any(v is not None for v in (kishou, suimin, nyuyoku, shokuji, kaji, rajio)):
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
        "🛁 入浴：" + ("済" if bath else "未報告"),
        "🏃 ラジオ体操：" + ("参加" if radio else "—"),
    ]
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
async def tsushinbo_command(interaction):
    await interaction.response.defer(ephemeral=True)
    res = await weekly_summary(interaction.guild, manual=True)
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
