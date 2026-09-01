"""最低限生活リズムサークル Bot

起床・睡眠・家事・食事・入浴を「ボタン1タップ」で記録し、
毎晩の判定で「自分で決めた最低限」を守れなかった人を #こら に晒す。
データは SQLite（VM上のファイル）に保存。外部サービス不要。
"""
import discord
from discord import app_commands
from discord.ext import tasks
import aiosqlite
import os
import re
from datetime import datetime, timezone, timedelta

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
JST = timezone(timedelta(hours=9))

CATEGORY_NAME = "最低限生活リズム"
# key -> (チャンネル名, パネルの説明)
CH = {
    "wake": ("起床", "☀️ 起きたら押す／🌙 寝る前に押す。睡眠時間は自動で計算されます。"),
    "meal": ("ごはん", "🍚 食べたら押す。**写真を投げるだけ**でも時間帯から自動で記録されます。"),
    "chore": ("家事", "🧹 やった家事を押す。洗濯は5工程に分かれています。"),
    "bath": ("おふろ", "🛁 お風呂に入ったら押す。"),
    "kora": ("こら", "毎晩の判定で、最低限を守れなかった人が晒される場所。"),
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
  meals_min INTEGER, chores_week INTEGER, updated_at INTEGER
);
CREATE TABLE IF NOT EXISTS events(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id TEXT NOT NULL, kind TEXT NOT NULL, sub TEXT, ts INTEGER NOT NULL, day TEXT NOT NULL, note TEXT
);
CREATE INDEX IF NOT EXISTS ev_user_day ON events(user_id, day);
CREATE INDEX IF NOT EXISTS ev_user_kind_ts ON events(user_id, kind, ts);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""
db = None

async def db_init():
    global db
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    await db.executescript(SCHEMA)
    await db.commit()

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
    return bool(u["wake_deadline"] or u["sleep_min"] or u["bath_daily"] or u["meals_min"] or u["chores_week"])

def settings_text(u):
    parts = []
    parts.append(f"☀️ 起床 {u['wake_deadline']} まで" if u["wake_deadline"] else "☀️ 起床 —")
    parts.append(f"🌙 睡眠 {fmt_hours(u['sleep_min'])} 以上" if u["sleep_min"] else "🌙 睡眠 —")
    parts.append("🛁 入浴 毎日" if u["bath_daily"] else "🛁 入浴 —")
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
    results = []
    for u in users:
        misses = await build_misses(u, day, d1, d2, is_sunday)
        if misses:
            results.append((u, misses))
    tag = "（手動判定）" if manual else ""
    if not users:
        await kora_ch.send(f"📋 {day} の判定{tag}：まだ誰も最低限を設定していません。`/saitei` で決めよう。")
    elif not results:
        await kora_ch.send(f"🎉 {day} の判定{tag}：**全員が最低限を守りました！** えらい！！")
    else:
        await kora_ch.send(f"📋 {day} の判定{tag}：{len(results)}/{len(users)} 人が最低限を守れませんでした。")
        for u, misses in results:
            await kora_ch.send(f"{emoji} <@{u['id']}> **こら！**\n" + "\n".join("・" + m for m in misses))
    return f"判定完了：{len(results)}/{len(users)} 人が未達"

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
        except Exception as e:
            print(f"judge error: {e}", flush=True)

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
    print("====================================", flush=True)

@bot.event
async def on_guild_join(guild):
    if GLOBAL_CMDS is not None:
        await sync_guild_commands(guild)

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
                "・**kaji** 週の最低家事回数（0で解除。日曜夜に判定）\n\n"
                f"毎晩 **{JUDGE_HOUR}:00** に判定し、守れなかった人は #こら に名指しで晒されます。\n"
                "`/nakama` で同じ起床時刻の仲間が見られます。`/kiroku` で自分の記録を確認。"
            ), color=discord.Color.gold()))
    await interaction.followup.send(
        "✅ セットアップ完了\n" + (f"新規作成：{', '.join('#' + n for n in made)}\n" if made else "既存チャンネルを再利用しました\n")
        + f"判定時刻：毎晩 {JUDGE_HOUR}:00 → #こら\n"
        + f"叱り絵文字：`:{KORA_EMOJI_NAME}:`（{kora_emoji(guild)}）", ephemeral=True)

async def nakama_of(uid, wake_deadline):
    if not wake_deadline:
        return []
    async with db.execute("SELECT name FROM users WHERE wake_deadline=? AND id<>? ORDER BY name", (wake_deadline, str(uid))) as c:
        return [r["name"] for r in await c.fetchall()]

@bot.tree.command(name="saitei", description="自分の「最低限」を設定する（指定した項目だけ更新）")
@app_commands.describe(kishou="起床の締切 例 7:00（「なし」で解除）", suimin="最低睡眠時間(h) 例 6（0で解除）",
                       nyuyoku="毎日入浴する", shokuji="1日の最低食事回数 1〜3（0で解除）", kaji="週の最低家事回数（0で解除）")
async def saitei_command(interaction, kishou: str = None, suimin: float = None, nyuyoku: bool = None,
                         shokuji: int = None, kaji: int = None):
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
    if any(v is not None for v in (kishou, suimin, nyuyoku, shokuji, kaji)):
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
    today = [
        "☀️ 起床：" + (hhmm(datetime.fromtimestamp(w[0]["ts"], JST)) if w else "未報告"),
        "🌙 睡眠：" + (fmt_hours(float(s[-1]["note"])) if s else "未記録"),
        "🍚 食事：" + ("、".join(f"{m['sub']}" + (f"({m['note']})" if m["note"] else "") for m in meals) if meals else "未報告"),
        "🧹 家事：" + ("、".join(CHORE_LABEL[c["sub"]] for c in chores) if chores else "なし"),
        "🛁 入浴：" + ("済" if bath else "未報告"),
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
    emb.add_field(name="最低限の設定", value=settings_text(u), inline=False)
    await interaction.response.send_message(embed=emb, ephemeral=True)

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
