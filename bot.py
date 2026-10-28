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
import random
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
WAKE_GRACE_MIN = int(os.getenv("WAKE_GRACE_MIN", "10"))   # 起床締切の操作猶予（分）＝起きてからボタンを押すまでのタイムラグはノーカウント
WAKE_REMIND_MIN = int(os.getenv("WAKE_REMIND_MIN", "10"))  # ⏰起床リマインド（オプトイン）を締切の何分前に出すか
KORA_EMOJI_NAME = os.getenv("KORA_EMOJI", "こら")
ERAI_EMOJI_NAME = os.getenv("ERAI_EMOJI", "えらい")   # 達成した人に付ける絵文字（無ければ ✨）
RADIO_TIME = os.getenv("RADIO_TIME", "06:30")          # ラジオ体操の開始時刻(HH:MM)
RADIO_MP3 = os.getenv("RADIO_MP3", "radio.mp3")        # 音源ファイル（リポジトリには含めない。VMに直接置く）
FFMPEG_PATH = os.getenv("FFMPEG_PATH", "ffmpeg")
BACKUP_DIR = os.getenv("BACKUP_DIR", "backups")   # 毎晩の判定後に seikatsu.db を7世代バックアップ
RADIO_VC_NAME = "ラジオ体操🏃"
WORK_VC_NAME = "作業部屋🖥"
WORK_MIN_LOG_SEC = 600   # これ未満のセッションはログを流さない（時間は記録する）
BENKYOKAI_START = int(os.getenv("BENKYOKAI_START", "22"))   # 📖ミニ勉強会の時間帯（この間に作業部屋にいれば参加。0未満で無効）
BENKYOKAI_END = int(os.getenv("BENKYOKAI_END", "23"))
WORK_TO_GAKUSHU = os.getenv("WORK_TO_GAKUSHU", "1") == "1"   # 作業部屋の時間をみんなで暗記！！の作業時間にも加算
# 改名前の旧チャンネル名（/setup が既存チャンネルを見つけて改名するために使う）
OLD_CH_NAMES = {"wake": "起床", "meal": "ごはん", "chore": "家事", "bath": "おふろ", "kora": "こら",
                "kadai": "課題", "tsushinbo": "つうしんぼ", "settei": "設定", "animal": "どうぶつ📸"}
OLD_RADIO_VC_NAME = "ラジオ体操"
REMIND_HOUR = int(os.getenv("REMIND_HOUR", "8"))     # 課題リマインドを流す時刻（時）
GAKUSHU_URL = os.getenv("GAKUSHU_URL", "https://gakushu-rpg.pages.dev")   # みんなで暗記！！連携先
GAKUSHU_SECRET = os.getenv("GAKUSHU_SECRET", "")     # Cloudflare側 VC_SECRET と同じ値。空なら連携オフ
WEATHER_LAT = float(os.getenv("WEATHER_LAT", "35.68"))   # 朝の天気（Open-Meteo・キー不要）。既定=府中
WEATHER_LON = float(os.getenv("WEATHER_LON", "139.48"))
MEMBERS_INTENT = os.getenv("MEMBERS_INTENT", "0") == "1"   # 1にすると新規参加者を #はじめに📖 で歓迎（Developer PortalでSERVER MEMBERS INTENTをONにすること）
COURSES_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses_2026_kouki.json")
COURSES_EXTRA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses_extra.json")
HITOKOTO_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "hitokoto.json")
try:   # 🧪 実験の日付別カレンダー（科目コード→日付→実施内容。A/Bクラスで日程が違う実験用）
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "lab_schedule.json"), encoding="utf-8") as _f:
        LAB_SCHEDULE = {k: v for k, v in json.load(_f).items() if not k.startswith("_")}
except Exception:
    LAB_SCHEDULE = {}
JST = timezone(timedelta(hours=9))

CATEGORY_NAME = "最低限生活リズム"
# key -> (チャンネル名, パネルの説明)
CH = {
    "hajimeni": ("はじめに📖", "参加者向けのガイド。困ったら `/help`。"),
    "jikoshokai": ("自己紹介🙋", "📝 ボタンでフォームから自己紹介カードを投稿（あとから更新OK）。"),
    "roles": ("ロール🏷", "リアクションで学部・学年・生活形態のロールを付け外し。"),
    "wake": ("起床🌅", "☀️ 起きたら押す／🌙 寝る前に押す（睡眠時間は自動計算）／😴 二度寝したら正直に押す。\n"
             "🌙で寝た人は、☀️を押し忘れても朝の最初の発言・ボタン・VC入室で自動記録されます（押し忘れ救済）。\n"
             "🏭 夜勤の夜に押すと、翌朝の起床・睡眠・ラジオ体操は判定なし（ストリークも継続）。\n"
             "🏃 で毎朝のラジオ体操の呼び出し、⏰ で締切前の起床リマインド（自分の締切の10分前にメンション＝実質アラーム）をON/OFF。\n"
             "-# 今日・明日・今後の予定は #予定🗓 のボタンでいつでも確認できます（☀️の返事にも今日の予定が出ます）"),
    "meal": ("ごはん🍚", "🍚 食べたら押す。**写真を投げるだけ**でも時間帯から自動で記録されます。\n"
             "🧊 冷蔵庫＝期限が近い食品のメモ。登録すると期限の前日・当日の朝にお知らせ（3日過ぎたら自動で消えます）。"),
    "chore": ("家事🧹", "🧹 やった家事を押す。洗濯は5工程に分かれています。\n"
              "「n日に1回」の家事は毎晩20時にここでお知らせ：🗓 **明日やる日**の家事（前日のお知らせ）と、⚠️ 今日やる日なのにまだの家事。"),
    "erai": ("えらい🌟", "最低限とは別に、今日がんばったことを報告して褒め合う場所。🌟ボタン（または /erai）から。判定はされません。\n"
             "📌 やること宣言＝やることメモ（1行に1つ・まとめて宣言OK）。**期限**（例 `11/3`・`11/1-11/3`）をつけるとその日まで毎日残ります。"
             "空欄なら今日だけ。終わったら✅、期限の日の23時に引き継ぎ確認、放置なら翌朝ひっそり消えます（叱責なし）。"),
    "worklog": ("作業ログ🖥", f"🔊{WORK_VC_NAME} に入ると計測開始・出ると終了。入室中に ✍ ボタンで「何をやるか」をひとこと宣言できます。\n"
                "10分未満のセッションはログに残りません（時間は記録）。`/kiroku` に今週の作業時間、通信簿に🖥もくもく賞。\n"
                "🎶 音楽はこのパネルから：リクエスト（URL・検索語・プレイリスト）／🎵BGM／🌿環境音／⏯⏭⏹。\n"
                "📂 みんなのプレイリスト＝サーバー共有の選曲リスト（作る・流す・自分のは編集）／⭐ おすすめ曲・🎤おすすめアーティスト＝紹介文つきで登録（1人20件まで）／📜 いま流れている曲の歌詞（自分にだけ表示）／🎤 同期歌詞がある曲は自動で歌詞表示（🎤からずれ補正・OFF・自動のON/OFF）。\n"
                "-# 細かい操作はコマンドで：`/volume` `/loop` `/shuffle`。音源の追加は音声ファイルを投稿→右クリック→アプリ→「BGMに追加」"),
    "animal": ("どうぶつ🐄", "犬・猫・鳥・ぬいぐるみ・道端のなんか・ミニホース…癒しの写真や動画をどうぞ（判定なし・ただの癒し）。\n"
               "写真にはBotが🐾を付けます。1週間でいちばんリアクションを集めた写真の人は、通信簿で🐾癒し賞に。\n"
               "月末には、その月いちばんリアクションを集めた9枚で **🐾いやしアルバム** ができます。"),
    "bath": ("おふろ🛁", "🛁 お風呂に入ったら押す。🪥 歯磨きも（1日に何回でも）。"),
    "goods": ("使用期限📦", "マンスリーコンタクト・歯ブラシ・スポンジなど「開封からN日で交換」する品のリマインド。\n"
              "📦 開封した日に登録（定番品は日数入力も不要）→ 交換時期の前日・当日の朝にここでお知らせ → 🔄 を押すと次のサイクルへ。\n"
              "期限を過ぎても消えません（☀️の返事に⚠️が出続けます）。判定・叱責はされません。"),
    "kora": ("叱責👹", "毎晩の判定で、最低限を守れなかった人が晒される場所。"),
    "tsushinbo": ("つうしんぼ📮", "毎週日曜の夜に、その週の通信簿（達成率ランキング・各賞）が届く場所。"),
    "kadai": ("課題📚", "🎓 で履修科目を登録 → 気づいた人が ➕ で課題を登録 → 同じ科目の履修者だけに通知＆リマインド（3日前・前日・当日）。\n"
              "毎週出るプリントや出席フォームは 🔁 毎週課題に登録すれば自動で課題化。投稿の ✅ で完了、選択式で出さない課題は 🚫 で外せます。\n"
              "🗒 科目メモ＝教室変更・持ち物などを履修者で共有（授業の日の朝に表示）。コマンド派は `/jikanwari` `/kadai` でも。\n"
              "-# 大学の予定・試験日・カレンダーは #予定🗓 にまとまっています"),
    "yotei": ("予定🗓", "📅 **今日**・🌙 **明日**・📆 **今後**の予定＝天気・時間割・課題・試験・やること・大学の予定などのまとめ（自分にだけ表示）。\n"
              "🏫 **大学の予定**＝長期休暇・履修登録期間・行事などを**だれでも**登録（朝の予定・📆今後の予定・ここでのお知らせに出ます。"
              "長期休暇はその期間が自動で休暇モードに）。\n"
              "🙋 **じぶんの予定**＝🔒自分だけ（だれにも見えない）か📣みんなに見せる（#えらい🌟 で宣言＋3日前・当日に声かけ）。終わったら自動で消えます。\n"
              "📝 **試験日**＝科目ごとの試験日のカウントダウン（履修者へのお知らせは #課題📚）。\n"
              "⏱ **バイト・部活**＝タップで記録して月の合計時間を確認（よく使うシフトは「いつもの」に保存）。\n"
              "📊 **タイムライン**で数か月先までの予定を一望、🗓 **カレンダー**で月ごとに（バイト・部活・試験・実験も入ります。課題は #課題📚 で）。画像は自分にだけ表示。"),
    "settei": ("設定🔧", "🛠 ボタンから項目を選んで自分の最低限を設定（`/saitei` でまとめて設定も可）。毎週の定休日（曜日）もここで。\n"
               "`/kojin add` で自分だけの項目を追加し、📝ボタンで毎日チェック。`/oyasumi` で単発のお休み申告。"),
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
CREATE TABLE IF NOT EXISTS assignment_done(assignment_id INTEGER NOT NULL, user_id TEXT NOT NULL, skip INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(assignment_id, user_id));
CREATE TABLE IF NOT EXISTS assignment_reminded(assignment_id INTEGER NOT NULL, stage TEXT NOT NULL, PRIMARY KEY(assignment_id, stage));
CREATE TABLE IF NOT EXISTS daily_results(day TEXT NOT NULL, user_id TEXT NOT NULL, achieved INTEGER NOT NULL, misses TEXT, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS efforts(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, text TEXT NOT NULL, day TEXT NOT NULL, ts INTEGER, msg_id TEXT);
CREATE TABLE IF NOT EXISTS tips(id INTEGER PRIMARY KEY AUTOINCREMENT, thread_id TEXT UNIQUE, author_id TEXT, title TEXT, tags TEXT, day TEXT, ts INTEGER, expires TEXT);
CREATE TABLE IF NOT EXISTS tip_saves(tip_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(tip_id, user_id));
CREATE TABLE IF NOT EXISTS goal_reviews(month TEXT NOT NULL, user_id TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(month, user_id));
CREATE TABLE IF NOT EXISTS meal_praise(event_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(event_id, user_id));
CREATE TABLE IF NOT EXISTS effort_praise(effort_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(effort_id, user_id));
CREATE TABLE IF NOT EXISTS intros(user_id TEXT PRIMARY KEY, f1 TEXT, f2 TEXT, f3 TEXT, f4 TEXT, f5 TEXT, msg_id TEXT, updated_at INTEGER);
CREATE TABLE IF NOT EXISTS off_days(day TEXT NOT NULL, user_id TEXT NOT NULL, reason TEXT, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS custom_items(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER);
CREATE TABLE IF NOT EXISTS custom_checks(day TEXT NOT NULL, user_id TEXT NOT NULL, item_id INTEGER NOT NULL, PRIMARY KEY(day, user_id, item_id));
CREATE TABLE IF NOT EXISTS fridge_items(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL, due_day TEXT NOT NULL, created_day TEXT);
CREATE TABLE IF NOT EXISTS night_shifts(day TEXT NOT NULL, user_id TEXT NOT NULL, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS memos(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, text TEXT NOT NULL, day TEXT NOT NULL, ts INTEGER, msg_id TEXT, carried INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS memo_prompts(day TEXT NOT NULL, user_id TEXT NOT NULL, msg_id TEXT, PRIMARY KEY(day, user_id));
CREATE TABLE IF NOT EXISTS work_sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, start_ts INTEGER NOT NULL, end_ts INTEGER, day TEXT NOT NULL, note TEXT, msg_id TEXT);
CREATE TABLE IF NOT EXISTS goods_items(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, name TEXT NOT NULL, cycle_days INTEGER NOT NULL, due_day TEXT NOT NULL, opened_day TEXT);
CREATE TABLE IF NOT EXISTS attendance(user_id TEXT NOT NULL, code TEXT NOT NULL, absent INTEGER NOT NULL DEFAULT 0, limit_n INTEGER NOT NULL DEFAULT 4, PRIMARY KEY(user_id, code));
CREATE TABLE IF NOT EXISTS recurring_kadai(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, title TEXT NOT NULL,
  due_time TEXT, due_days TEXT, created_by TEXT, created_at INTEGER, active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS recurring_spawned(rule_id INTEGER NOT NULL, due_day TEXT NOT NULL, assignment_id INTEGER, PRIMARY KEY(rule_id, due_day));
CREATE TABLE IF NOT EXISTS exams(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, title TEXT NOT NULL DEFAULT '試験',
  exam_day TEXT NOT NULL, created_by TEXT, created_at INTEGER, closed INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS exam_reminded(exam_id INTEGER NOT NULL, stage TEXT NOT NULL, PRIMARY KEY(exam_id, stage));
CREATE TABLE IF NOT EXISTS user_slots(user_id TEXT NOT NULL, slot TEXT NOT NULL, label TEXT NOT NULL, place TEXT, PRIMARY KEY(user_id, slot));
CREATE TABLE IF NOT EXISTS play_history(id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, title TEXT, url TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS member_playlists(
  id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id TEXT NOT NULL, name TEXT NOT NULL, created_at INTEGER
);
CREATE TABLE IF NOT EXISTS member_playlist_tracks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, pl_id INTEGER NOT NULL, query TEXT NOT NULL, title TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS member_recs(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, query TEXT NOT NULL, title TEXT, comment TEXT, ts INTEGER,
  kind TEXT NOT NULL DEFAULT 'song'
);
CREATE TABLE IF NOT EXISTS member_rec_tracks(
  id INTEGER PRIMARY KEY AUTOINCREMENT, rec_id INTEGER NOT NULL, query TEXT NOT NULL, title TEXT, comment TEXT, ts INTEGER,
  added_by TEXT
);
CREATE TABLE IF NOT EXISTS univ_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL, kind TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL,
  created_by TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS private_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, title TEXT NOT NULL, start_day TEXT NOT NULL, end_day TEXT NOT NULL, ts INTEGER,
  shared INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS private_event_reminded(event_id INTEGER NOT NULL, stage TEXT NOT NULL, PRIMARY KEY(event_id, stage));
CREATE TABLE IF NOT EXISTS univ_event_reminded(event_id INTEGER NOT NULL, stage TEXT NOT NULL, PRIMARY KEY(event_id, stage));
CREATE TABLE IF NOT EXISTS time_events(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL, day TEXT NOT NULL,
  start_hm TEXT NOT NULL, end_hm TEXT NOT NULL, minutes INTEGER NOT NULL, note TEXT, ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_time_events_user_day ON time_events(user_id, day);
CREATE TABLE IF NOT EXISTS time_presets(
  id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, kind TEXT NOT NULL, start_hm TEXT NOT NULL,
  minutes INTEGER NOT NULL, note TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS lyrics_block(norm_key TEXT PRIMARY KEY, user_id TEXT, ts INTEGER);
CREATE TABLE IF NOT EXISTS course_notes(
  id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL, text TEXT NOT NULL, user_id TEXT, ts INTEGER
);
CREATE TABLE IF NOT EXISTS custom_lyrics(
  id INTEGER PRIMARY KEY AUTOINCREMENT, norm_key TEXT NOT NULL, title TEXT NOT NULL, text TEXT NOT NULL,
  user_id TEXT, ts INTEGER
);
CREATE INDEX IF NOT EXISTS idx_work_user_day ON work_sessions(user_id, day);
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
              "ALTER TABLE users ADD COLUMN chores_set_day TEXT",
              "ALTER TABLE users ADD COLUMN benkyou_min INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN benkyou_set_day TEXT",
              "ALTER TABLE memos ADD COLUMN done INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE users ADD COLUMN wake_remind INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE events ADD COLUMN msg_id TEXT",
              "ALTER TABLE recurring_kadai ADD COLUMN due_days TEXT",
              "ALTER TABLE users ADD COLUMN off_weekdays TEXT",
              "ALTER TABLE member_recs ADD COLUMN kind TEXT NOT NULL DEFAULT 'song'",
              "ALTER TABLE member_rec_tracks ADD COLUMN added_by TEXT",
              "ALTER TABLE assignment_done ADD COLUMN skip INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE private_events ADD COLUMN shared INTEGER NOT NULL DEFAULT 0",
              "ALTER TABLE memos ADD COLUMN until_day TEXT"):
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

async def count_events_between(uid, kind, d1, d2, sub=None):
    q = "SELECT COUNT(*) AS n FROM events WHERE user_id=? AND kind=? AND day BETWEEN ? AND ?"
    args = [str(uid), kind, d1, d2]
    if sub is not None:
        q += " AND sub=?"
        args.append(sub)
    async with db.execute(q, args) as c:
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
    yakin = await is_night_shift(uid, day)   # 夜勤の朝は起床・睡眠・ラジオ体操を判定しない
    dl = effective_deadline(u, date.fromisoformat(day))
    if dl and u["wake_set_day"] != day and not yakin:
        w = await events_on(uid, day, "wake")
        if not w:
            misses.append(f"☀️ 起床 未報告（{dl} まで）")
        else:
            t = hhmm(datetime.fromtimestamp(w[0]["ts"], JST))
            if t > grace_deadline(dl):   # 締切＋操作猶予を過ぎたら寝坊
                misses.append(f"☀️ 寝坊 {dl} まで → {t}")
    if u["sleep_min"] and u["sleep_set_day"] != day and not yakin:
        s = await events_on(uid, day, "sleep")
        if not s:
            misses.append("🌙 睡眠時間 未報告")
        else:
            hrs = sum(float(x["note"] or 0) for x in s)
            if hrs < u["sleep_min"]:
                misses.append(f"🌙 睡眠不足 {fmt_hours(hrs)}（最低 {fmt_hours(u['sleep_min'])}）")
    if u["bath_daily"] and u["bath_set_day"] != day:
        if not await events_on(uid, day, "bath"):
            misses.append("🛁 入浴 未報告")
    if u["radio_daily"] and u["radio_set_day"] != day and not yakin:
        if not await events_on(uid, day, "radio"):
            misses.append("🏃 ラジオ体操 未参加")
    if u["meals_min"] and u["meals_set_day"] != day:
        n = len(await distinct_main_meals(uid, day))
        if n < u["meals_min"]:
            misses.append(f"🍚 食事 {n}/{u['meals_min']} 回")
    if u["teeth_min"] and u["teeth_set_day"] != day:
        n = len(await events_on(uid, day, "teeth"))
        if n < u["teeth_min"]:
            misses.append(f"🪥 歯磨き {n}/{u['teeth_min']} 回")
    if u["benkyou_min"] and u["benkyou_set_day"] != day:
        studied = await fetch_studied(day)   # None=連携未設定/API不通→この項目は判定しない
        if studied is not None and str(uid) not in studied:
            misses.append("📖 勉強 未達（みんなで暗記！！で今日1問も解いてない）")
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

# ---- 🌸 長期休暇モード（春休み・夏休み）：期間中は授業系の表示・毎週課題を止め、起床締切をずらす ----
_BREAK = {"start": None, "end": None, "shift": 0, "name": "長期休暇"}   # /kyuka の手動設定（metaから起動時に読み込む）
_UNIV_BREAKS = []   # 🎓大学の予定で「長期休暇」として登録されたもの [(start, end, name)]
UNIV_BREAK_SHIFT = int(float(os.getenv("UNIV_BREAK_SHIFT_H", "2")) * 60)   # 大学の予定の長期休暇で起床締切をずらす幅

async def load_break():
    _BREAK.update(start=await meta_get("break_start"), end=await meta_get("break_end"),
                  shift=int(await meta_get("break_shift", "0") or 0), name=await meta_get("break_name", "長期休暇"))
    try:
        async with db.execute("SELECT title, start_day, end_day FROM univ_events WHERE kind='長期休暇'") as c:
            _UNIV_BREAKS[:] = [(r["start_day"], r["end_day"], r["title"]) for r in await c.fetchall()]
    except Exception:
        _UNIV_BREAKS[:] = []

def _iso(d):
    return d if isinstance(d, str) else (d.date() if isinstance(d, datetime) else d).isoformat()

def break_span(d):
    """d を含む長期休暇 {start,end,name,shift}。手動の /kyuka を優先。無ければ None"""
    k = _iso(d)
    if _BREAK["start"] and _BREAK["end"] and _BREAK["start"] <= k <= _BREAK["end"]:
        return {"start": _BREAK["start"], "end": _BREAK["end"], "name": _BREAK["name"], "shift": _BREAK["shift"]}
    for s, e, n in _UNIV_BREAKS:
        if s <= k <= e:
            return {"start": s, "end": e, "name": n, "shift": UNIV_BREAK_SHIFT}
    return None

def break_on(d):
    """d（date/datetime/ISO文字列）が長期休暇の期間内か"""
    return break_span(d) is not None

def _span_text(sp):
    s, e = sp["start"], sp["end"]
    return f"{sp['name']}（{int(s[5:7])}/{int(s[8:])}〜{int(e[5:7])}/{int(e[8:])}）"

def break_text(d=None):
    """d を含む休暇の表示名。d省略時は /kyuka の手動設定"""
    sp = break_span(d) if d is not None else ({"start": _BREAK["start"], "end": _BREAK["end"], "name": _BREAK["name"]}
                                               if _BREAK["start"] else None)
    return _span_text(sp) if sp else ""

def effective_deadline(u, dt):
    """起床締切。土日は holiday_shift（分）だけ後ろにずらす。長期休暇中は毎日、休暇用のずらし幅と大きいほう"""
    dl = u["wake_deadline"]
    if not dl:
        return None
    shift = u["holiday_shift"] or 0
    sp = break_span(dt)
    if sp and sp["shift"]:
        shift = max(shift, sp["shift"])
        h, m = map(int, dl.split(":"))
        t = min(h * 60 + m + shift, 23 * 60 + 59)
        return f"{t // 60:02d}:{t % 60:02d}"
    if shift and dt.weekday() >= 5:
        h, m = map(int, dl.split(":"))
        t = min(h * 60 + m + shift, 23 * 60 + 59)
        return f"{t // 60:02d}:{t % 60:02d}"
    return dl

def grace_deadline(dl):
    """判定用の締切＝表示上の締切＋操作猶予。起きてからスマホを開いてボタンを押すまでの数分で寝坊にしない"""
    if not dl:
        return None
    h, m = map(int, dl.split(":"))
    t = min(h * 60 + m + WAKE_GRACE_MIN, 23 * 60 + 59)
    return f"{t // 60:02d}:{t % 60:02d}"

# ---- 夜勤🏭（起床・睡眠・ラジオ体操だけの部分お休み） ----
def yakin_target_day(now):
    """夜勤で免除する日。昼前に押したら今朝（夜勤明け）、昼以降なら明日の朝"""
    d = now if now.hour < 12 else now + timedelta(days=1)
    return day_str(d)

async def is_night_shift(uid, day):
    async with db.execute("SELECT 1 FROM night_shifts WHERE day=? AND user_id=?", (day, str(uid))) as c:
        return (await c.fetchone()) is not None

async def all_items_skipped(u, day):
    """この日、実際に判定される項目が1つも無い（すべて今日設定・未設定）＝設定初日の人"""
    if any(v and sd != day for v, sd in (
            (u["wake_deadline"], u["wake_set_day"]), (u["sleep_min"], u["sleep_set_day"]),
            (u["bath_daily"], u["bath_set_day"]), (u["teeth_min"], u["teeth_set_day"]),
            (u["meals_min"], u["meals_set_day"]), (u["radio_daily"], u["radio_set_day"]),
            (u["benkyou_min"], u["benkyou_set_day"]))):
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
    return bool(u["wake_deadline"] or u["sleep_min"] or u["bath_daily"] or u["meals_min"] or u["radio_daily"]
                or u["kaji_cook"] or u["kaji_clean"] or u["kaji_dish"] or u["kaji_wash"] or u["kaji_trash"]
                or u["teeth_min"] or u["benkyou_min"])

def settings_text(u):
    parts = []
    parts.append((f"☀️ 起床 {u['wake_deadline']} まで" + (f"（土日は +{(u['holiday_shift'] or 0) / 60:g}h）" if u["holiday_shift"] else "")) if u["wake_deadline"] else "☀️ 起床 —")
    parts.append(f"🌙 睡眠 {fmt_hours(u['sleep_min'])} 以上" if u["sleep_min"] else "🌙 睡眠 —")
    parts.append("🛁 入浴 毎日" if u["bath_daily"] else "🛁 入浴 —")
    parts.append(f"🪥 歯磨き 1日{u['teeth_min']}回" if u["teeth_min"] else "🪥 歯磨き —")
    parts.append("🏃 ラジオ体操 毎日" if u["radio_daily"] else "🏃 ラジオ体操 —")
    parts.append(f"🍚 食事 1日{u['meals_min']}回" if u["meals_min"] else "🍚 食事 —")
    parts.append("📖 勉強 毎日（みんなで暗記！！で1問以上）" if u["benkyou_min"] else "📖 勉強 —")
    kaji = [f"{e} {lb} {kaji_interval_text(u[col])}" for col, lb, e, _ in KAJI_CATS if u[col]]
    parts.append(("🧹 家事 " + "／".join(kaji)) if kaji else "🧹 家事 —")
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
        self.add_view(KadaiPanelView())
        self.add_view(GoodsPanelView())
        self.add_view(SetteiView())
        self.add_view(MusicPanelView())
        self.add_view(YoteiPanelView())
        self.add_view(IntroView())
        self.add_dynamic_items(DoneButton, SkipButton, MealFixButton, PraiseButton, MealPraiseButton, GoalReviewButton, TipSaveButton,
                               MemoDoneButton, MemoCarryButton, MemoDropButton, WorkNoteButton, GoodsResetButton)
        self.add_view(EraiView())
        # コマンドの同期はグローバルではなくサーバー単位で行う（即時反映）。on_ready 参照。

bot = SeikatsuBot()

def kora_emoji(guild):
    e = discord.utils.get(guild.emojis, name=KORA_EMOJI_NAME) if guild else None
    return str(e) if e else "👹"

def erai_emoji(guild):
    e = discord.utils.get(guild.emojis, name=ERAI_EMOJI_NAME) if guild else None
    return str(e) if e else "✨"

# ------------------------------------------------------------
#  ローディング画面のひとこと 💡
#  ゲームのロード画面のTIPSよろしく、記録の返事や判定・ラジオ体操予告の
#  末尾に豆知識を小さい字で1行添える。ネタは data/hitokoto.json（1要素=1行）。
# ------------------------------------------------------------
try:
    with open(HITOKOTO_JSON, encoding="utf-8") as _f:
        HITOKOTO = [s.strip() for s in json.load(_f) if isinstance(s, str) and s.strip()]
except Exception as _e:
    print(f"hitokoto.json 読み込み失敗: {_e!r}", flush=True)
    HITOKOTO = []
_hitokoto_recent = []   # 直近に出したネタ（しばらく同じものを出さないため）

def hitokoto_suffix():
    """メッセージ末尾に添えるひとこと（サブテキスト1行）。ネタが無ければ空文字"""
    if not HITOKOTO:
        return ""
    pool = [s for s in HITOKOTO if s not in _hitokoto_recent] or HITOKOTO
    s = random.choice(pool)
    _hitokoto_recent.append(s)
    keep = min(40, len(HITOKOTO) // 2)
    while len(_hitokoto_recent) > keep:
        _hitokoto_recent.pop(0)
    return f"\n-# 💡 {s}"

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

async def refresh_panel(key):
    """/setup用のパネル更新：すでに最下部にあれば**その場で編集**（＝新着通知を出さない）。
    最下部でない・無い場合だけ置き直す。全チャンネルに新着が付く問題の対策"""
    ch = await get_ch(key)
    if not ch:
        return
    old = await meta_get("panel_" + key)
    if old:
        try:
            m = await ch.fetch_message(int(old))
            if ch.last_message_id == m.id:
                await m.edit(embed=panel_embed(key), view=VIEW_FACTORY[key]())
                return
            await m.delete()
        except Exception:
            pass
    try:
        msg = await ch.send(embed=panel_embed(key), view=VIEW_FACTORY[key]())
        await meta_set("panel_" + key, msg.id)
    except discord.Forbidden:
        SETUP_ERRORS.append(f"#{ch.name} にパネルを投稿できません（権限を確認）")

async def post_log(key, text):
    ch = await get_ch(key)
    if ch:
        await ch.send(text)
        await bump_panel(key)

class MyCheckShortcut(discord.ui.Button):
    """どの報告パネルからでも、自分の個人項目チェックリストを1タップで開く小ボタン"""
    def __init__(self, key, row=None):
        super().__init__(label="📝 チェック", style=discord.ButtonStyle.secondary, custom_id=f"sk_mycheck_{key}", row=row)

    async def callback(self, interaction):
        await ensure_user(interaction.user)
        await send_mycheck(interaction)

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
    if dl and hhmm(wake_dt) > grace_deadline(dl):
        late = f" ⚠️ 締切 {dl} を過ぎてます"
    msg = f"✅ {hhmm(wake_dt)} 起床（{sleep_txt}）{late}"
    if bed_dt and (wake_dt - bed_dt).total_seconds() / 3600 >= 15:
        msg += "\n⚠️ 睡眠が長すぎるかも？ 🌙の時刻ミスなら `/suimin jikan:7 uwagaki:True` で上書き修正できます（昨日以前は `hi:昨日` など）"
    digest = await today_digest(user.id, wake_dt)
    if digest:
        msg += "\n" + digest
    kwargs = {"ephemeral": True}
    if await my_items(user.id):   # 個人項目がある人はチェックリストをこの返事に直接くっつける
        msg += "\n" + await mycheck_text(user.id, day_str(wake_dt))
        kwargs["view"] = await MyCheckView.build(user.id, day_str(wake_dt))
    msg += hitokoto_suffix()
    if already_responded:
        await interaction.followup.send(msg, **kwargs)
    else:
        await interaction.response.send_message(msg, **kwargs)
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
        self.add_item(MyCheckShortcut("wake", row=1))

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
        exam_note = ""
        try:   # 明日が試験なら就寝時にそっと（チャンネルには鳴らさない）
            tomorrow = [e for e in await exam_rows_for(str(user.id)) if exam_days_left(e["exam_day"], now) == 1]
            if tomorrow:
                exam_note = "\n📝 明日は " + "・".join(f"**{e['cname']}**「{e['title']}」" for e in tomorrow[:3]) + "！ちゃんと寝るのがいちばんの試験対策"
        except Exception:
            pass
        await interaction.response.send_message(f"🌙 {hhmm(now)} おやすみ。起きたら ☀️ を押してね。{exam_note}" + hitokoto_suffix(), ephemeral=True)
        await post_log("wake", f"🌙 **{user.display_name}** {hhmm(now)} おやすみ")

    @discord.ui.button(label="😴 二度寝した", style=discord.ButtonStyle.secondary, custom_id="sk_nizone")
    async def nizone(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "nizone")
        deg = len(await events_on(user.id, day_str(now), "nizone")) + 1  # 1回押したら「二度寝」
        await interaction.response.send_message(f"😴 {hhmm(now)}　本日 {deg} 度寝を記録しました。おかえりなさい。" + hitokoto_suffix(), ephemeral=True)
        await post_log("wake", f"😴 **{user.display_name}** {hhmm(now)} 二度寝から生還（本日 {deg} 度寝）")

    @discord.ui.button(label="🏭 今夜は夜勤", style=discord.ButtonStyle.secondary, custom_id="sk_yakin", row=1)
    async def yakin(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        target = yakin_target_day(now)
        asa = "今朝" if target == day_str(now) else "明日の朝"
        if await is_night_shift(user.id, target):   # 2度押しで取り消し
            await db.execute("DELETE FROM night_shifts WHERE day=? AND user_id=?", (target, str(user.id)))
            await db.commit()
            await interaction.response.send_message(f"🏭 夜勤を取り消しました。{asa}の判定は通常に戻ります。", ephemeral=True)
            await post_log("wake", f"🏭 **{user.display_name}** の夜勤はなしになりました")
            return
        await db.execute("INSERT OR IGNORE INTO night_shifts(day,user_id) VALUES(?,?)", (target, str(user.id)))
        await db.commit()
        await interaction.response.send_message(
            f"🏭 夜勤を記録しました。{asa}の ☀️起床・🌙睡眠・🏃ラジオ体操 は判定されず、連続達成も途切れません。"
            f"ラジオ体操の呼び出しも止めます。ご安全に！（間違えたらもう一度押すと取り消し）" + hitokoto_suffix(), ephemeral=True)
        koukai = "夜勤明け" if target == day_str(now) else "今夜夜勤"
        await post_log("wake", f"🏭 **{user.display_name}** は{koukai}。ちょーえらい！")

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

    @discord.ui.button(label="⏰ 締切前リマインド ON/OFF", style=discord.ButtonStyle.secondary, custom_id="sk_wake_remind", row=1)
    async def wake_remind(self, interaction, button):
        user = interaction.user
        await ensure_user(user)
        u = await get_user(user.id)
        cur = u["wake_remind"] if "wake_remind" in u.keys() else 0
        new = 0 if cur else 1
        await db.execute("UPDATE users SET wake_remind=? WHERE id=?", (new, str(user.id)))
        await db.commit()
        if new:
            msg = (f"⏰ 起床リマインド **ON**：☀️の締切の{WAKE_REMIND_MIN}分前に未報告ならメンションで呼びます。"
                   f"スマホの通知をONにしておけば実質アラームです（土日の後ろ倒し・夜勤・お休みは鳴りません）。")
        else:
            msg = "⏰ 起床リマインド **OFF** にしました。"
        await interaction.response.send_message(msg + hitokoto_suffix(), ephemeral=True)

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
        streak = asagohan_note(await asagohan_streak(user.id)) if self.sub == "朝" else ""
        await interaction.response.send_message(f"✅ {self.sub}ごはんを記録しました。{streak}\n📸 写真も残したいときは、このチャンネルに写真を投稿すると自動で記録されます。" + hitokoto_suffix(), ephemeral=True)
        await post_log("meal", f"{MEAL_EMOJI[self.sub]} **{user.display_name}** {self.sub}ごはん" + (f"：{note}" if note else ""))

async def asagohan_streak(uid, today=None):
    """朝ごはんを食べた日が何日続いているか（今日まだなら昨日から数える＝朝のうちに途切れ扱いしない）。
    お休み申告の日は切らずに飛ばす。判定には使わない（えらいを数えるだけ）"""
    uid = str(uid)
    today = date.fromisoformat(today or day_str(now_jst()))
    since = (today - timedelta(days=400)).isoformat()
    async with db.execute("SELECT DISTINCT day FROM events WHERE user_id=? AND kind='meal' AND sub='朝' AND day>=?", (uid, since)) as c:
        ate = {r["day"] for r in await c.fetchall()}
    async with db.execute("SELECT day FROM off_days WHERE user_id=? AND day>=?", (uid, since)) as c:
        off = {r["day"] for r in await c.fetchall()}
    d = today if today.isoformat() in ate else today - timedelta(days=1)
    n = 0
    for _ in range(400):
        k = d.isoformat()
        if k in ate:
            n += 1
        elif k not in off:
            break
        d -= timedelta(days=1)
    return n

def asagohan_note(n):
    """朝ごはん記録の返事に添える一言（2日目から）"""
    if n < 2:
        return ""
    cheer = "🎉 節目！" if n in (7, 14, 30, 50, 100) else ""
    return f"\n🍳 朝ごはん **{n}日連続**！{cheer}"

class MealButton(discord.ui.Button):
    def __init__(self, sub, emoji):
        super().__init__(label=f"{emoji} {sub}", style=discord.ButtonStyle.secondary, custom_id=f"sk_meal_{sub}", row=0)
        self.sub = sub

    async def callback(self, interaction):
        await interaction.response.send_modal(MealModal(self.sub))

class MealView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for sub, emoji in MEALS:
            self.add_item(MealButton(sub, emoji))
        self.add_item(MyCheckShortcut("meal", row=1))

    @discord.ui.button(label="🧊 冷蔵庫に登録", style=discord.ButtonStyle.primary, custom_id="sk_fridge_add", row=1)
    async def fridge_add(self, interaction, button):
        await interaction.response.send_modal(FridgeAddModal())

    @discord.ui.button(label="🧊 冷蔵庫を見る・食べた", style=discord.ButtonStyle.secondary, custom_id="sk_fridge_list", row=1)
    async def fridge_list(self, interaction, button):
        await fridge_show(interaction)

    @discord.ui.button(label="📜 レシピカード", style=discord.ButtonStyle.secondary, custom_id="sk_recipe", row=1)
    async def recipe_btn(self, interaction, button):
        await interaction.response.send_modal(RecipeModal())

    @discord.ui.button(label="📖 じぶんの食卓", style=discord.ButtonStyle.secondary, custom_id="sk_gohan_album", row=1)
    async def meal_album_btn(self, interaction, button):
        await ensure_user(interaction.user)
        await interaction.response.defer(ephemeral=True, thinking=True)   # 写真の取り直しに数秒かかる
        now = now_jst()
        buf = await build_meal_album(day_str(now - timedelta(days=30)), day_str(now), uid=interaction.user.id,
                                     title=f"{_plain_name(interaction.user.display_name, 12)} の食卓", sub="この30日")
        if not buf:
            await interaction.followup.send("この30日に写真つきのごはん記録がまだありません。#ごはん🍚 に写真を投げると、ここに並んでいきます 📸", ephemeral=True)
            return
        await interaction.followup.send("📖 この30日のあなたの食卓です。ちゃんと食べててえらい！",
                                        file=discord.File(buf, filename="my_meals.png"), ephemeral=True)

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
        n = await count_events_between(user.id, "chore", d1, d2, sub=self.key)   # 押したボタンの回数だけ（全家事の合計にしない）
        await interaction.response.send_message(f"✅ {CHORE_LABEL[self.key]} を記録（今週 {n} 回目）" + hitokoto_suffix(), ephemeral=True)
        await post_log("chore", f"{CHORE_LABEL[self.key]} **{user.display_name}**（今週 {n} 回目）")

class ChoreView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        for key, label, emoji, row in CHORES:
            self.add_item(ChoreButton(key, label, emoji, row))
        self.add_item(MyCheckShortcut("chore", row=3))

# ------------------------------------------------------------
#  入浴
# ------------------------------------------------------------
class BathView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(MyCheckShortcut("bath"))

    @discord.ui.button(label="🛁 お風呂入った", style=discord.ButtonStyle.primary, custom_id="sk_bath")
    async def bath(self, interaction, button):
        user = interaction.user
        now = now_jst()
        await ensure_user(user)
        await add_event(user.id, "bath")
        n = len(await events_on(user.id, day_str(now), "bath"))
        extra = f"（今日 {n} 回目）" if n >= 2 else ""
        praise = " きれい好き！" if n >= 2 else ""
        await interaction.response.send_message(f"✅ {hhmm(now)} 入浴を記録しました{extra}。{praise}" + hitokoto_suffix(), ephemeral=True)
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
        await interaction.response.send_message(f"✅ {hhmm(now)} 歯磨き（今日 {n} 回目{goal}）" + hitokoto_suffix(), ephemeral=True)
        await post_log("bath", f"🪥 **{user.display_name}** {hhmm(now)} 歯磨き（今日 {n} 回目）")

VIEW_FACTORY.update({"wake": WakeView, "meal": MealView, "chore": ChoreView, "bath": BathView})

# ------------------------------------------------------------
#  #ごはん🍚 に写真を投げたら自動記録
# ------------------------------------------------------------
# ------------------------------------------------------------
#  ☀️押し忘れ救済（自動起床）
#  🌙を押して寝た人が、朝（就寝から3時間以上あと・4時以降）に最初の活動
#  （発言・ボタン操作・VC入室）をしたら、起きている証拠として☀️を自動記録。
#  睡眠時間も🌙から自動計算する。🌙が無い人は曖昧なので何もしない。
# ------------------------------------------------------------
_autowake_cache = {"day": None, "done": set()}

async def auto_wake(user, hint=""):
    now = now_jst()
    day = day_str(now)
    if _autowake_cache["day"] != day:
        _autowake_cache.update(day=day, done=set())
    uid = str(user.id)
    if uid in _autowake_cache["done"] or now.hour < 4:
        return
    u = await get_user(uid)
    if not u or not (u["wake_deadline"] or u["sleep_min"]):
        _autowake_cache["done"].add(uid)   # 起床系を設定していない人は以後この日はスキップ
        return
    if await events_on(uid, day, "wake"):
        _autowake_cache["done"].add(uid)
        return
    bed_dt = await last_bed_within(uid, now)
    if not bed_dt or (now - bed_dt) < timedelta(hours=3):
        return   # 🌙していない／就寝直後（まだ寝る前かもしれない）→ 何もしない
    _autowake_cache["done"].add(uid)
    await add_event(uid, "wake", ts_dt=now)
    hrs = (now - bed_dt).total_seconds() / 3600
    await add_event(uid, "sleep", note=f"{hrs:.2f}", ts_dt=now)
    dl = effective_deadline(u, now)
    late = f" ⚠️ 締切 {dl} 超過" if dl and hhmm(now) > grace_deadline(dl) else ""
    warn = "　⚠️ 睡眠が長すぎるかも？ `/suimin uwagaki:True` で修正できます" if hrs >= 15 else ""
    await post_log("wake", f"☀️ **{u['name'] or user.display_name}** {hhmm(now)} 起床（睡眠 {fmt_hours(hrs)}・{hint}から自動記録）{late}{warn}")

@bot.event
async def on_interaction(interaction):
    """どのボタン・コマンド操作も「起きている証拠」として押し忘れ救済に使う"""
    try:
        if not interaction.guild or interaction.user is None or interaction.user.bot:
            return
        if interaction.type not in (discord.InteractionType.application_command, discord.InteractionType.component):
            return   # モーダル送信・オートコンプリートは対象外（☀️フローとの二重記録防止）
        cid = (interaction.data or {}).get("custom_id", "")
        if cid in ("sk_wake", "sk_bed", "sk_nizone"):
            return   # 起床パネル自身の操作は本来のフローに任せる
        await auto_wake(interaction.user, "ボタン操作")
    except Exception as e:
        print(f"自動起床エラー: {e!r}", flush=True)

@bot.event
async def on_message(message):
    if message.author.bot or not message.guild:
        return
    try:
        await auto_wake(message.author, "発言")
    except Exception as e:
        print(f"自動起床エラー: {e!r}", flush=True)
    animal_ch = await meta_get("ch_animal")
    if animal_ch and message.channel.id == int(animal_ch) and message.attachments \
            and any((a.content_type or "").startswith(("image/", "video/")) for a in message.attachments):
        try:
            await message.add_reaction("🐾")   # 癒しの写真には自動で🐾。週間で一番リアクションを集めた人が癒し賞
        except Exception:
            pass
        return
    meal_ch = await meta_get("ch_meal")
    if not meal_ch or message.channel.id != int(meal_ch) or not message.attachments:
        return
    if not any((a.content_type or "").startswith("image/") for a in message.attachments):
        return
    rd = RECIPE_DRAFTS.get(str(message.author.id))
    if rd and rd.get("await") is not None:   # 📜レシピ下書きが画像待ちなら、食事記録ではなくレシピに回収
        att = next((a for a in message.attachments if (a.content_type or "").startswith("image/")), None)
        b = None
        if att:
            try:
                b = await att.read()
            except Exception:
                b = None
        if consume_recipe_photo(message.author.id, b):
            try:
                await message.delete()   # 回収済みの投稿は片付ける（消えた＝取り込み完了の合図）
            except Exception:
                try:
                    await message.add_reaction("📜")
                except Exception:
                    pass
            return
    now = now_jst()
    sub = infer_meal_sub(now)
    await ensure_user(message.author)
    eid = await add_event(message.author.id, "meal", sub, note=(message.content.strip() or "写真")[:100])
    await db.execute("UPDATE events SET msg_id=? WHERE id=?", (str(message.id), eid))   # アルバム用に元投稿を覚えておく
    await db.commit()
    try:
        await message.add_reaction("✅")
    except Exception:
        pass
    view = discord.ui.View(timeout=None)
    for s2, e2 in MEALS:
        view.add_item(MealFixButton(eid, message.author.id, s2, e2, current=sub))
    view.add_item(MealPraiseButton(eid, 0))
    streak = asagohan_note(await asagohan_streak(message.author.id)) if sub == "朝" else ""
    await message.reply(f"📸 {MEAL_EMOJI[sub]} **{sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）{streak}",
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

# ---- 📖 ごはんアルバム（写真つき記録をブランド配色のコラージュ1枚に） ----
_GOHAN_ART = {}

def _gohan_art(name):
    """art/gohan の手描き素材（部員作・bboxトリム済み透過PNG）。無ければ None"""
    if name in _GOHAN_ART:
        return _GOHAN_ART[name]
    img = None
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "art", "gohan", f"{name}.png")
    try:
        if os.path.exists(p):
            from PIL import Image as PImage
            img = PImage.open(p).convert("RGBA")
    except Exception:
        img = None
    _GOHAN_ART[name] = img
    return img

def _paste_art(img, art, x, y, h, angle=0, alpha=1.0):
    """高さhに合わせて素材を貼る（angleで回転・alphaで透かし）。貼った幅を返す"""
    w = round(art.width * h / art.height)
    a = art.resize((w, h))
    if angle:
        from PIL import Image as PImage
        a = a.rotate(angle, expand=True, resample=PImage.BICUBIC)
    if alpha < 1.0:
        a = a.copy()
        a.putalpha(a.getchannel("A").point(lambda v: int(v * alpha)))
    img.paste(a, (x, y), a)
    return a.width

def render_meal_album(photos, title, sub="", month=None, crown=False, title_art="title", side_art="bowl"):
    """photos: [(画像bytes, キャプション), ...] 最大9枚 → コラージュPNG。描けなければ None
    month指定時は手描き数字＋見出し（title_art=手描きレタリング素材名。Noneなら title の文字で描く）。
    side_art=見出し横の素材名。crown=Trueで1枚目（1位）に王冠とハート"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont, ImageOps
    except Exception:
        return None
    f = _find_cjk_font()
    if not f or not photos:
        return None
    fb = _find_cjk_font_bold() or f
    cols = min(3, len(photos))
    rows = -(-min(len(photos), 9) // cols)
    art_title = _gohan_art(title_art) if (month and title_art) else None
    use_digits = bool(month) and (art_title is not None or title_art is None)
    art_logo, art_spark = _gohan_art("logo"), _gohan_art("sparkle")
    art_bowl = _gohan_art(side_art) if side_art else None
    cell, cap_h, pad = 360, 34, 14
    head = 132 if (use_digits or art_logo) else 86
    W = pad + cols * (cell + pad)
    H = head + rows * (cell + cap_h + pad)
    img = PImage.new("RGB", (W, H), (247, 242, 232))
    d = ImageDraw.Draw(img)
    logo_w = round(art_logo.width * 104 / art_logo.height) if art_logo else 0
    if use_digits:
        x = pad + 8
        for ch in str(month):   # 手描き数字＋見出し（レタリング素材か文字）
            dg = _gohan_art(f"d{ch}")
            if dg:
                x += _paste_art(img, dg, x, 22, 80) + 6
            else:
                d.text((x, 26), ch, font=ImageFont.truetype(fb, 72), fill=(217, 112, 26))
                x += d.textlength(ch, font=ImageFont.truetype(fb, 72)) + 4
        if art_title:
            x += 6 + _paste_art(img, art_title, x + 6, 46, 56) + 16
        else:
            rest = title[len(str(month)):] if title.startswith(str(month)) else title
            tf = ImageFont.truetype(fb, 46)
            d.text((x + 8, 52), rest, font=tf, fill=(27, 24, 21))
            x += 8 + d.textlength(rest, font=tf) + 16
        x = int(x)
        if art_bowl:   # タイトルの横にごはん茶碗
            x += _paste_art(img, art_bowl, x, 18, 100) + 4
        if art_spark and x + 48 < W - pad - logo_w:
            _paste_art(img, art_spark, x, 16, 44)
    else:
        d.text((pad + 2, 42), title, font=ImageFont.truetype(fb, 34), fill=(27, 24, 21))
        tw = d.textlength(title, font=ImageFont.truetype(fb, 34))
        if art_bowl:
            _paste_art(img, art_bowl, int(pad + 2 + tw) + 16, 34, 64)
        elif sub:
            d.text((W - pad - logo_w - 14, 54), sub, font=ImageFont.truetype(f, 16), fill=(217, 112, 26), anchor="ra")
    if art_logo:   # 右上にサークルロゴ
        _paste_art(img, art_logo, W - pad - logo_w - 4, 14, 104)
    fc = ImageFont.truetype(f, 17)
    fe = None
    try:
        ef = _find_emoji_font()
        fe = ImageFont.truetype(ef, 17) if ef else None
    except Exception:
        pass

    def draw_cap(x, y, s):
        """キャプションを1文字ずつ、絵文字（👏など）だけ同梱NotoEmojiで描く（豆腐防止）"""
        cx = x
        for chs in s:
            use_e = fe is not None and ord(chs) >= 0x2600 and not (0x3000 <= ord(chs) <= 0x9FFF) \
                and not (0xF900 <= ord(chs) <= 0xFAFF) and not (0xFF00 <= ord(chs) <= 0xFFEF)
            fnt = fe if use_e else fc
            d.text((cx, y), chs, font=fnt, fill=(27, 24, 21))
            cx += d.textlength(chs, font=fnt)

    rng = random.Random(zlib.crc32(f"{title}:{month}:{len(photos)}".encode("utf-8")))   # 同じアルバムは同じ柄
    prev_tape = None
    for i, (b, cap) in enumerate(photos[:9]):
        x = pad + (i % cols) * (cell + pad)
        y = head + (i // cols) * (cell + cap_h + pad)
        try:
            ph = PImage.open(io.BytesIO(b)).convert("RGB")
            ph = ImageOps.exif_transpose(ph)
            ph = ImageOps.fit(ph, (cell, cell))
        except Exception:
            ph = PImage.new("RGB", (cell, cell), (237, 228, 211))
        img.paste(ph, (x, y))
        d.rectangle([x, y, x + cell - 1, y + cell - 1], outline=(226, 218, 203), width=2)
        tname = rng.choice([t for t in ("tape_red", "tape_teal", "tape_yellow") if t != prev_tape])
        prev_tape = tname
        tape = _gohan_art(tname)
        if tape:   # 各写真の上辺にマスキングテープ（色・角度・位置をランダムにしてアナログっぽく。直前と同色だけは避ける）
            tw = round(tape.width * 44 / tape.height)
            _paste_art(img, tape, x + cell // 2 - tw // 2 + rng.randint(-40, 40), y - 16, 44,
                       angle=rng.choice((-1, 1)) * rng.uniform(2.5, 7.5), alpha=0.88)
        if crown and i == 0:   # 👏1位の写真に王冠とハート
            cr, ht = _gohan_art("crown"), _gohan_art("hearts")
            if cr:
                _paste_art(img, cr, x - 26, y - 44, 120, angle=12)
            if ht:
                _paste_art(img, ht, x + cell - 100, y + cell - 104, 92)
        draw_cap(x + 4, y + cell + 6, cap[:26])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---- 📜 レシピカード（手描き素材のかわいい1枚画像に書き出す） ----
def _is_emoji_ch(ch):
    o = ord(ch)
    return o >= 0x2600 and not (0x3000 <= o <= 0x9FFF) and not (0xF900 <= o <= 0xFAFF) and not (0xFF00 <= o <= 0xFFEF)

def _mixed_w(d, s, f, fe):
    return sum(d.textlength(ch, font=(fe if fe and _is_emoji_ch(ch) else f)) for ch in s)

def _draw_mixed(d, x, y, s, f, fe, fill):
    cx = x
    for ch in s:
        fnt = fe if fe and _is_emoji_ch(ch) else f
        d.text((cx, y), ch, font=fnt, fill=fill)
        cx += d.textlength(ch, font=fnt)
    return cx - x

def _wrap_px(d, s, f, fe, maxw):
    """ピクセル幅で文字詰め折り返し（行頭に句読点が来ないよう軽く調整）"""
    lines, cur = [], ""
    for ch in s:
        if _mixed_w(d, cur + ch, f, fe) > maxw and cur:
            if ch in "。、！？）」｝]ゃゅょっんー" and len(cur) > 1:
                lines.append(cur[:-1])
                cur = cur[-1] + ch
            else:
                lines.append(cur)
                cur = ch
        else:
            cur += ch
    if cur:
        lines.append(cur)
    return lines or [""]

def parse_ingredient_lines(text, limit=14):
    """材料欄のパース：1行を (名前, 分量) に分ける。「卵 1個」「卵：1個」どちらもOK、分量なしは名前だけ"""
    out = []
    for l in (text or "").splitlines():
        l = l.strip().lstrip("・-･•●　 ").strip()
        if not l:
            continue
        m = re.split(r"[：:]", l, 1)
        if len(m) == 2:
            nm_, amt_ = m[0], m[1]
        else:
            parts = re.split(r"[\s　]+", l)
            nm_, amt_ = (" ".join(parts[:-1]), parts[-1]) if len(parts) >= 2 else (l, "")
        out.append((nm_.strip()[:40], amt_.strip()[:20]))
        if len(out) >= limit:
            break
    return out

def parse_recipe_lines(text, limit=14):
    out = []
    for l in (text or "").splitlines():
        l = l.strip().lstrip("・-･•●　 0123456789.①②③④⑤⑥⑦⑧⑨⑩").strip()
        if l:
            out.append(l[:60])
        if len(out) >= limit:
            break
    return out

def render_recipe_card(author, title, ingredients, steps, memo="", main_img=None, step_imgs=None):
    """レシピをブランド配色＋手描き素材の1枚カードに。main_img/step_imgs は画像bytes（任意）。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont, ImageOps
    except Exception:
        return None
    f0 = _find_cjk_font()
    if not f0:
        return None
    fb0 = _find_cjk_font_bold() or f0
    ef = _find_emoji_font()
    ft = ImageFont.truetype(fb0, 46)        # 料理名
    fh = ImageFont.truetype(fb0, 26)        # 見出し（素材が無いときのフォールバック）
    fr = ImageFont.truetype(f0, 22)         # 本文
    fs = ImageFont.truetype(f0, 18)         # 小さめ
    fe = ImageFont.truetype(ef, 22) if ef else None
    W, pad = 1000, 52
    cw = W - pad * 2
    probe = ImageDraw.Draw(PImage.new("RGB", (10, 10)))
    rng = random.Random(zlib.crc32(f"{author}:{title}".encode("utf-8")))

    def _load(b, box):
        try:
            p = PImage.open(io.BytesIO(b)).convert("RGB")
            p = ImageOps.exif_transpose(p)
            p.thumbnail(box)
            return p
        except Exception:
            return None
    main_ph = _load(main_img, (460, 560)) if main_img else None
    step_phs = {i: _load(b, (380, 260)) for i, b in (step_imgs or {}).items()}
    step_phs = {i: p for i, p in step_phs.items() if p}
    title_lines = _wrap_px(probe, title, ft, fe, cw - 150)
    # 材料は (名前, 分量) のタプルでも文字列でも受ける
    norm_ing = [(s, "") if isinstance(s, str) else (s[0], (s[1] or "").strip()) for s in ingredients]
    if main_ph:   # メイン画像があるときは画像の右に材料を並べて余白を使う（1段組）
        two_col = False
        ing_w = cw - main_ph.width - 40
    else:
        two_col = len(norm_ing) > 5
        ing_w = (cw // 2 - 30) if two_col else cw - 30
    ing = []
    for nm_, amt_ in norm_ing:
        aw = _mixed_w(probe, amt_, fr, fe) if amt_ else 0
        ing.append((amt_, aw, _wrap_px(probe, nm_, fr, fe, ing_w - 26 - (aw + 26 if amt_ else 0))))
    stp = [(i, _wrap_px(probe, s, fr, fe, cw - 70)) for i, s in enumerate(steps)]
    memo_lines = _wrap_px(probe, memo, fs, fe, cw - 180) if memo else []
    head_z, head_t = _gohan_art("head_zairyo"), _gohan_art("head_tsukurikata")
    hz_h, ht_h = 84, 44
    # 高さ見積もり
    y_title = 56
    h_title = len(title_lines) * 58 + 34
    h_ing_rows = [max(len(ing[k][2]) for k in (r, r + 1) if k < len(ing)) for r in range(0, len(ing), 2)] if two_col \
        else [len(l) for _, _, l in ing]
    ing_body = sum(r * 32 + 8 for r in h_ing_rows)
    if main_ph:   # 画像と材料の横並びセクション（背の高い方＋余白）
        h_ing = max(main_ph.height + 40, (hz_h if head_z else 52) + 12 + ing_body) + 30
    else:
        h_ing = (hz_h if head_z else 52) + 14 + ing_body + 18
    h_stp = (ht_h if head_t else 52) + 14 + sum(len(l) * 32 + 14 + (step_phs[i].height + 14 if i in step_phs else 0) for i, l in [(i, l) for i, l in stp]) + 10
    h_memo = (len(memo_lines) * 28 + 44) if memo_lines else 0
    H = y_title + h_title + h_ing + h_stp + h_memo + 70
    img = PImage.new("RGB", (W, H), (247, 242, 232))
    d = ImageDraw.Draw(img)
    y = y_title
    for tl in title_lines:
        _draw_mixed(d, pad + 4, y, tl, ft, fe, (27, 24, 21))
        y += 58
    bowl = _gohan_art("bowl")
    if bowl:
        _paste_art(img, bowl, W - pad - 96, y_title - 8, 92)
    spark = _gohan_art("sparkle")
    if spark:
        _paste_art(img, spark, W - pad - 140, y_title - 16, 40)
    d.text((pad + 6, y + 2), f"{_plain_name(author, 14)} のレシピ", font=fs, fill=(217, 112, 26))
    y += 34
    def _draw_ing(x0, yy, amt_, aw, lns):
        """材料1件：・名前 ……… 分量（分量は1行目の右端・点線リーダーつき）"""
        d.text((x0, yy + 4), "・", font=fr, fill=(217, 112, 26))
        nw = _draw_mixed(d, x0 + 26, yy + 4, lns[0], fr, fe, (27, 24, 21))
        if amt_:
            ax = x0 + ing_w - aw
            _draw_mixed(d, ax, yy + 4, amt_, fr, fe, (27, 24, 21))
            cx = x0 + 26 + nw + 12
            while cx < ax - 12:   # 点線リーダー
                d.ellipse([cx, yy + 22, cx + 2, yy + 24], fill=(176, 168, 154))
                cx += 9
        for ln in lns[1:]:
            yy += 32
            _draw_mixed(d, x0 + 26, yy + 4, ln, fr, fe, (27, 24, 21))

    def _draw_zairyo_head(x0, yy):
        if head_z:
            _paste_art(img, head_z, x0, yy + 6, hz_h)
            return yy + hz_h + 12
        d.text((x0, yy + 14), "ざいりょう", font=fh, fill=(27, 24, 21))
        d.line([x0, yy + 50, x0 + 150, yy + 50], fill=(217, 112, 26), width=3)
        return yy + 52 + 12

    if main_ph:   # メイン画像を左・材料を右に並べて余白を使う
        img.paste(main_ph, (pad, y + 16))
        d.rectangle([pad, y + 16, pad + main_ph.width - 1, y + 16 + main_ph.height - 1], outline=(226, 218, 203), width=2)
        tape = _gohan_art(rng.choice(("tape_red", "tape_teal", "tape_yellow")))
        if tape:
            tw = round(tape.width * 44 / tape.height)
            _paste_art(img, tape, pad + main_ph.width // 2 - tw // 2 + rng.randint(-40, 40), y, 44,
                       angle=rng.choice((-1, 1)) * rng.uniform(2, 6), alpha=0.88)
        rx = pad + main_ph.width + 40
        ry = _draw_zairyo_head(rx, y + 6)
        for amt_, aw, lns in ing:
            _draw_ing(rx, ry, amt_, aw, lns)
            ry += len(lns) * 32 + 8
        y = max(y + 16 + main_ph.height, ry) + 30
    else:
        y = _draw_zairyo_head(pad, y) + 2
        if two_col:
            for r in range(0, len(ing), 2):
                rh = 0
                for k in (r, r + 1):
                    if k >= len(ing):
                        continue
                    x0 = pad + (0 if k == r else cw // 2 + 10)
                    amt_, aw, lns = ing[k]
                    _draw_ing(x0, y, amt_, aw, lns)
                    rh = max(rh, len(lns))
                y += rh * 32 + 8
        else:
            for amt_, aw, lns in ing:
                _draw_ing(pad, y, amt_, aw, lns)
                y += len(lns) * 32 + 8
        y += 18
    # つくりかた（手描き見出し・手順番号は手描き数字）
    if head_t:
        htw = _paste_art(img, head_t, pad, y + 6, ht_h)
        hearts = _gohan_art("hearts")
        if hearts:   # ハートは「つくりかた」の右に
            _paste_art(img, hearts, pad + htw + 22, y + 2, 48)
        y += ht_h + 14
    else:
        d.text((pad, y + 14), "つくりかた", font=fh, fill=(27, 24, 21))
        d.line([pad, y + 50, pad + 150, y + 50], fill=(217, 112, 26), width=3)
        y += 52 + 14
    for i, (_, lns) in enumerate(stp):
        dg = _gohan_art(f"d{i + 1}") if i < 9 else None
        if dg:
            _paste_art(img, dg, pad + 2, y + 6, 26)
        else:
            d.text((pad, y + 4), f"{i + 1}.", font=fr, fill=(27, 24, 21))
        for ln in lns:
            _draw_mixed(d, pad + 44, y + 4, ln, fr, fe, (27, 24, 21))
            y += 32
        if i in step_phs:   # 工程の画像（任意）
            p = step_phs[i]
            img.paste(p, (pad + 44, y + 6))
            d.rectangle([pad + 44, y + 6, pad + 44 + p.width - 1, y + 6 + p.height - 1], outline=(226, 218, 203), width=2)
            y += p.height + 14
        y += 14
    # ひとこと（手描き見出し）
    if memo_lines:
        y += 8
        head_h = _gohan_art("head_hitokoto")
        if head_h:
            hw = _paste_art(img, head_h, pad, y + 2, 32)
            mx0 = pad + hw + 22
        else:
            _draw_mixed(d, pad, y + 2, "ひとこと：", fh, fe, (217, 112, 26))
            mx0 = pad + 140
        yy = y + 6
        for ln in memo_lines:
            _draw_mixed(d, mx0, yy, ln, fs, fe, (138, 131, 120))
            yy += 28
        y = yy + 8
    logo = _gohan_art("logo")
    if logo:
        lw = round(logo.width * 64 / logo.height)
        _paste_art(img, logo, W - pad - lw + 10, H - 86, 64)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

RECIPE_DRAFTS = {}   # uid -> 下書き（画像待ちの状態もここに持つ・再起動で消える使い捨て）
RECIPE_PHOTO_STASH = {}   # uid -> 右クリック「レシピカードにする」で選んだ写真（モーダル送信時にメイン画像へ）

@bot.tree.context_menu(name="レシピカードにする")
async def recipe_from_photo(interaction: discord.Interaction, message: discord.Message):
    """#ごはんに投稿済みの料理写真から、あとからレシピカードを作る（写真がメイン画像になる）"""
    att = next((a for a in message.attachments if (a.content_type or "").startswith("image/")), None)
    if not att:
        await interaction.response.send_message("この投稿には画像がありません。料理の写真を右クリック（長押し）→アプリ から使ってね。", ephemeral=True)
        return
    try:
        b = await att.read()
    except Exception:
        b = None
    if not b:
        await interaction.response.send_message("画像を読み込めませんでした。", ephemeral=True)
        return
    await ensure_user(interaction.user)
    RECIPE_PHOTO_STASH[str(interaction.user.id)] = b
    await interaction.response.send_modal(RecipeModal())

def consume_recipe_photo(uid, b):
    """#ごはんに投稿された画像をレシピ下書きに取り込む（取り込んだらTrue＝食事記録にしない）"""
    d = RECIPE_DRAFTS.get(str(uid))
    if not d or d.get("await") is None or not b:
        return False
    if d["await"] == "main":
        d["main"] = b
    else:
        d["imgs"][d["await"]] = b
    d["await"] = None
    return True

class RecipeModal(discord.ui.Modal, title="📜 レシピカードをつくる"):
    nm = discord.ui.TextInput(label="料理名", max_length=30, placeholder="例 悪魔の釜玉うどん")
    zairyo = discord.ui.TextInput(label="材料（1行に1つ「材料 分量」の順）", style=discord.TextStyle.paragraph, max_length=600,
                                  placeholder="冷凍うどん 1玉\n卵 1個\nめんつゆ 大さじ1\nラー油")
    tsukurikata = discord.ui.TextInput(label="作り方（1行に1手順）", style=discord.TextStyle.paragraph, max_length=1000,
                                       placeholder="うどんをレンジで3分\n卵とめんつゆをのせて混ぜる")
    kotsu = discord.ui.TextInput(label="ひとこと（任意）", required=False, max_length=80)

    async def on_submit(self, interaction):
        await ensure_user(interaction.user)
        ingredients = parse_ingredient_lines(self.zairyo.value)
        steps = parse_recipe_lines(self.tsukurikata.value, limit=12)
        if not ingredients or not steps:
            await interaction.response.send_message("⚠️ 材料と作り方をそれぞれ1行以上入れてください。", ephemeral=True)
            return
        stashed = RECIPE_PHOTO_STASH.pop(str(interaction.user.id), None)
        RECIPE_DRAFTS[str(interaction.user.id)] = {
            "title": self.nm.value.strip(), "ing": ingredients, "steps": steps,
            "memo": (self.kotsu.value or "").strip(), "main": stashed, "imgs": {}, "await": None}
        head = "📜 下書きができました！（メイン画像：選んだ写真を設定済み）\n" if stashed else "📜 下書きができました！\n"
        await interaction.response.send_message(
            head
            + "🖼 写真を付けるなら、下のプルダウンで場所を選んでから**このチャンネルに画像を投稿**してください（自動で回収します・食事記録にはなりません）。付けなくてもOK。\n"
            + "そろったら ✅ で完成です。", view=RecipeBuildView(steps), ephemeral=True)

class RecipeImgSelect(discord.ui.Select):
    def __init__(self, steps):
        opts = [discord.SelectOption(label="🖼 メイン画像（完成写真）", value="main")] + \
               [discord.SelectOption(label=f"🖼 工程{i + 1}：{s[:16]}"[:100], value=str(i)) for i, s in enumerate(steps[:12])]
        super().__init__(placeholder="🖼 写真を付ける場所を選ぶ → 次に画像を投稿", options=opts)

    async def callback(self, interaction):
        d = RECIPE_DRAFTS.get(str(interaction.user.id))
        if not d:
            await interaction.response.send_message("下書きが見つかりません（期限切れ）。もう一度 📜 からどうぞ。", ephemeral=True)
            return
        v = self.values[0]
        d["await"] = "main" if v == "main" else int(v)
        where = "メイン画像" if v == "main" else f"工程{int(v) + 1}"
        await interaction.response.send_message(f"📸 次にこのチャンネルへ投稿した画像を **{where}** にします（投稿は自動で回収します）", ephemeral=True)

class RecipeBuildView(discord.ui.View):
    def __init__(self, steps):
        super().__init__(timeout=900)
        self.add_item(RecipeImgSelect(steps))

    @discord.ui.button(label="✅ これで完成", style=discord.ButtonStyle.success)
    async def done(self, interaction, button):
        d = RECIPE_DRAFTS.pop(str(interaction.user.id), None)
        if not d:
            await interaction.response.send_message("下書きが見つかりません（期限切れ）。もう一度 📜 からどうぞ。", ephemeral=True)
            return
        await interaction.response.edit_message(content="🎨 カードを描いています…", view=None)
        buf = await asyncio.to_thread(render_recipe_card, interaction.user.display_name,
                                      d["title"], d["ing"], d["steps"], d["memo"], d["main"], d["imgs"])
        if not buf:
            await interaction.followup.send("カードを描けませんでした（フォント未検出）。", ephemeral=True)
            return
        await interaction.channel.send(f"📜 **{interaction.user.display_name}** さんのレシピ：**{d['title']}**",
                                       file=discord.File(buf, filename="recipe.png"))
        await bump_panel("meal")

    @discord.ui.button(label="❌ やめる", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction, button):
        RECIPE_DRAFTS.pop(str(interaction.user.id), None)
        await interaction.response.edit_message(content="やめました（下書きは消えました）。", view=None)

async def meal_photo_rows(d1, d2, uid=None, limit=9):
    """期間中の写真つき食事記録。uid指定=本人の新しい順／無指定=👏の多い順（ベスト飯）"""
    q = ("SELECT e.*, u.name AS uname, (SELECT COUNT(*) FROM meal_praise mp WHERE mp.event_id=e.id) AS pr "
         "FROM events e LEFT JOIN users u ON u.id=e.user_id "
         "WHERE e.kind='meal' AND e.msg_id IS NOT NULL AND e.day BETWEEN ? AND ?")
    args = [d1, d2]
    if uid is not None:
        q += " AND e.user_id=? ORDER BY e.ts DESC"
        args.append(str(uid))
    else:
        q += " ORDER BY pr DESC, e.ts DESC"
    q += " LIMIT ?"
    args.append(limit)
    async with db.execute(q, args) as c:
        return await c.fetchall()

async def _meal_channel():
    cid = await meta_get("ch_meal")
    if not cid:
        return None
    try:
        return bot.get_channel(int(cid)) or await bot.fetch_channel(int(cid))
    except Exception:
        return None

async def build_meal_album(d1, d2, uid=None, title="", sub="", month=None):
    """元投稿から写真を取り直してコラージュを生成（消された投稿は飛ばす）。無ければ None"""
    rows = await meal_photo_rows(d1, d2, uid=uid)
    ch = await _meal_channel()
    if not rows or not ch:
        return None
    photos = []
    for r in rows:
        try:
            m = await ch.fetch_message(int(r["msg_id"]))
            att = next((a for a in m.attachments if (a.content_type or "").startswith("image/")), None)
            if not att:
                continue
            b = await att.read()
        except Exception:
            continue
        if uid is None:
            cap = _plain_name(r["uname"] or "？", 12) + (f"　👏{r['pr']}" if r["pr"] else "")
        else:
            dd = date.fromisoformat(r["day"])
            cap = f"{dd.month}/{dd.day}　{r['sub'] or ''}ごはん" + (f"　👏{r['pr']}" if r["pr"] else "")
        photos.append((b, cap))
        if len(photos) >= 9:
            break
    if not photos:
        return None
    crown = uid is None and rows and (rows[0]["pr"] or 0) > 0   # 👏1位がいる月間アルバムだけ王冠
    return await asyncio.to_thread(render_meal_album, photos, title, sub, month, crown)

async def backfill_meal_msg_ids(days=62):
    """アルバム機能より前の写真つき食事記録にmsg_idを後付けする（#ごはん🍚の履歴と時刻で突き合わせ・初回のみ）"""
    if await meta_get("meal_msgid_backfill"):
        return 0
    ch = await _meal_channel()
    if not ch:
        return 0
    await meta_set("meal_msgid_backfill", "1")
    linked = 0
    after = now_jst() - timedelta(days=days)
    async for m in ch.history(limit=None, after=after, oldest_first=True):
        if m.author.bot or not any((a.content_type or "").startswith("image/") for a in m.attachments):
            continue
        ts = int(m.created_at.timestamp())
        async with db.execute(
            "SELECT id FROM events WHERE user_id=? AND kind='meal' AND msg_id IS NULL AND ABS(ts-?)<=300 "
            "ORDER BY ABS(ts-?) LIMIT 1", (str(m.author.id), ts, ts)) as c:
            r = await c.fetchone()
        if r:
            await db.execute("UPDATE events SET msg_id=? WHERE id=?", (str(m.id), r["id"]))
            linked += 1
    await db.commit()
    print(f"ごはん写真のmsg_id後付け: {linked}件", flush=True)
    return linked

@bot.tree.command(name="album", description="月のアルバムを作って投稿（ベスト飯→#ごはん🍚／いやし→#どうぶつ🐄・省略で先月）")
@app_commands.describe(tsuki="月（1〜12。省略すると先月）", shurui="どのアルバム？（既定=ベスト飯）")
@app_commands.choices(shurui=[app_commands.Choice(name="🍚 ベスト飯アルバム", value="meal"),
                              app_commands.Choice(name="🐾 いやしアルバム", value="iyashi")])
async def album_command(interaction, tsuki: int = None, shurui: app_commands.Choice[str] = None):
    await interaction.response.defer(ephemeral=True, thinking=True)
    if shurui and shurui.value == "iyashi":
        now = now_jst()
        if tsuki is None:
            base = now.replace(day=1) - timedelta(days=1)
            y, m = base.year, base.month
        elif 1 <= tsuki <= 12:
            y, m = (now.year if tsuki <= now.month else now.year - 1), tsuki
        else:
            await interaction.followup.send("月は 1〜12 で指定してください。", ephemeral=True)
            return
        d1, d2 = month_bounds(y, m)
        buf = await build_iyashi_album(d1, min(d2, day_str(now)), m)
        ch = await get_ch("animal")
        if not buf:
            await interaction.followup.send(f"{y}年{m}月は #どうぶつ🐄 に写真が見つかりませんでした。", ephemeral=True)
            return
        if ch:
            await ch.send(f"🐾 **{m}月のいやしアルバム**（{interaction.user.display_name} のリクエストでお届け）",
                          file=discord.File(buf, filename="iyashi_album.png"))
            await interaction.followup.send(f"✅ {m}月のいやしアルバムを #どうぶつ🐄 に投稿しました。", ephemeral=True)
        else:
            await interaction.followup.send(file=discord.File(buf, filename="iyashi_album.png"), ephemeral=True)
        return
    now = now_jst()
    if tsuki is None:
        base = now.replace(day=1) - timedelta(days=1)   # 先月
        y, m = base.year, base.month
    elif 1 <= tsuki <= 12:
        y, m = (now.year if tsuki <= now.month else now.year - 1), tsuki
    else:
        await interaction.followup.send("月は 1〜12 で指定してください。", ephemeral=True)
        return
    d1 = f"{y:04d}-{m:02d}-01"
    last = (datetime(y, m, 1, tzinfo=JST) + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    d2 = min(day_str(last), day_str(now))
    buf = await build_meal_album(d1, d2, title=f"{m}月のベスト飯アルバム", month=m)
    if not buf:
        await interaction.followup.send(f"{y}年{m}月は写真つきのごはん記録が見つかりませんでした。", ephemeral=True)
        return
    ch = await _meal_channel()
    if not ch:
        await interaction.followup.send(file=discord.File(buf, filename="meal_album.png"), ephemeral=True)
        return
    await ch.send(f"📖 **{m}月のベスト飯アルバム**（{interaction.user.display_name} のリクエストでお届け）",
                  file=discord.File(buf, filename="meal_album.png"))
    await bump_panel("meal")
    await interaction.followup.send(f"✅ {m}月のアルバムを #ごはん🍚 に投稿しました。", ephemeral=True)

async def post_monthly_meal_album(now=None):
    """月末、月間表彰の後に #ごはん🍚 へ今月のベスト飯アルバムを投稿"""
    now = now or now_jst()
    d1, d2 = day_str(now.replace(day=1)), day_str(now)
    buf = await build_meal_album(d1, d2, title=f"{now.month}月のベスト飯アルバム", sub="#最低限生活リズムサークル", month=now.month)
    ch = await _meal_channel()
    if not buf or not ch:
        return False
    await ch.send(f"📖 **{now.month}月のベスト飯アルバム**（👏を集めた飯テロたち。来月もうまそうなやつ待ってます）",
                  file=discord.File(buf, filename="meal_album.png"))
    await bump_panel("meal")
    return True

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

_study_cache = {"day": None, "ids": None, "ts": 0}

async def fetch_studied(day):
    """みんなで暗記！！でその日1問でも解いたDiscordIDの集合。未設定・不通なら None（＝📖は判定しない。冤罪防止）"""
    if not GAKUSHU_SECRET:
        return None
    now_ts = int(now_jst().timestamp())
    if _study_cache["day"] == day and now_ts - _study_cache["ts"] < 120:
        return _study_cache["ids"]
    ids = None
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.get(GAKUSHU_URL.rstrip("/") + "/api/seikatsu", params={"secret": GAKUSHU_SECRET, "day": day}) as r:
                if r.status < 300:
                    ids = set((await r.json()).get("studied") or [])
                else:
                    print(f"gakushu 勉強取得エラー {r.status}: {await r.text()}", flush=True)
    except Exception as e:
        print(f"gakushu 勉強取得失敗: {e!r}", flush=True)
    _study_cache.update(day=day, ids=ids, ts=now_ts)
    return ids

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
            line = f"{emoji} <@{u['id']}> **こら！**\n" + "\n".join("・" + m for m in misses)
            try:
                rx = await prescribe_tip(u["id"], day, misses)
            except Exception as e:
                print(f"処方TIPSエラー: {e!r}", flush=True)
                rx = ""
            if rx:
                line += "\n" + rx
            await kora_ch.send(line)
    if achievers:
        achievers.sort(key=lambda x: -x[1])
        await kora_ch.send(f"{erai_emoji(guild)} 達成：" + "、".join(f"**{u['name']}**" + (f" 🔥{st}日" if st >= 2 else "") for u, st in achievers))
    for line in celebrate:
        await kora_ch.send(line)
    if resting:
        await kora_ch.send("🛌 お休み：" + "、".join(f"**{u['name']}**" + (f"（{off[u['id']]}）" if off[u["id"]] else "") for u in resting))
    if first_day:
        await kora_ch.send("🍀 設定初日（判定は明日から）：" + "、".join(f"**{u['name']}**" for u in first_day))
    h = hitokoto_suffix()
    if h:
        await kora_ch.send(h.lstrip("\n"))
    try:
        await memo_carry_prompts(day)   # 未完了のやることメモ📌の引き継ぎ確認
    except Exception as e:
        print(f"memo prompt error: {e!r}", flush=True)
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

# ---- 🐾 いやしアルバム（#どうぶつ🐄 の月間ベスト9。動物もぬいぐるみも） ----
async def iyashi_photo_rows(d1, d2, limit_scan=1500):
    """期間内の画像投稿を (メッセージ, リアクション数) で集める。Botが自動で付けた🐾は数えない"""
    ch = await get_ch("animal")
    if not ch:
        return []
    start = datetime.fromisoformat(d1 + "T00:00:00").replace(tzinfo=JST)
    end = start + timedelta(days=(date.fromisoformat(d2) - date.fromisoformat(d1)).days + 1)
    out = []
    try:
        async for m in ch.history(limit=limit_scan, after=start, before=end):
            if m.author.bot or not any((a.content_type or "").startswith("image/") for a in m.attachments):
                continue
            n = sum(r.count - (1 if r.me else 0) for r in m.reactions)
            out.append((m, n))
    except Exception as e:
        print(f"いやしアルバム集計エラー: {e!r}", flush=True)
    out.sort(key=lambda x: (-x[1], x[0].created_at))
    return out

async def build_iyashi_album(d1, d2, month):
    rows = await iyashi_photo_rows(d1, d2)
    photos = []
    for m, n in rows:
        att = next((a for a in m.attachments if (a.content_type or "").startswith("image/")), None)
        try:
            b = await att.read()
        except Exception:
            continue
        photos.append((b, _plain_name(m.author.display_name, 12) + (f"　🐾{n}" if n else "")))
        if len(photos) >= 9:
            break
    if not photos:
        return None
    crown = rows[0][1] > 0
    return await asyncio.to_thread(render_meal_album, photos, f"{month}月のいやしアルバム", "", month, crown, None, None)

async def post_monthly_iyashi_album(now=None):
    """月末に #どうぶつ🐄 へ今月のいやしアルバム（写真が無い月は何もしない）"""
    now = now or now_jst()
    buf = await build_iyashi_album(day_str(now.replace(day=1)), day_str(now), now.month)
    ch = await get_ch("animal")
    if not buf or not ch:
        return False
    await ch.send(f"🐾 **{now.month}月のいやしアルバム**（いちばんリアクションを集めた子たち。来月もいやしをどうぞ）",
                  file=discord.File(buf, filename="iyashi_album.png"))
    return True

async def iyashi_winner(d1, d2):
    """#どうぶつ📸 で今週いちばんリアクションを集めた写真の投稿者（🐾癒し賞）。無ければ None"""
    ch = await get_ch("animal")
    if not ch:
        return None
    try:
        start = datetime.fromisoformat(d1 + "T00:00:00").replace(tzinfo=JST)
        end = start + timedelta(days=(date.fromisoformat(d2) - date.fromisoformat(d1)).days + 1)
        best_n, best_author = 0, None
        async for m in ch.history(limit=300, after=start, before=end):
            if m.author.bot or not m.attachments:
                continue
            n = sum(r.count for r in m.reactions)
            if n > best_n:
                best_n, best_author = n, m.author.display_name
        if best_author:
            return {"emoji": "🐾", "title": "癒し賞", "names": [best_author], "value": f"リアクション{best_n}"}
    except Exception as e:
        print(f"癒し賞集計エラー: {e!r}", flush=True)
    return None

# 通信簿カードの配色（Twitter勧誘パンフと同じブランドカラー）
CARD_CREAM, CARD_INK, CARD_ORANGE, CARD_AMBER = "#f7f2e8", "#1b1815", "#d9701a", "#f2a349"
CARD_MUTED, CARD_LINE, CARD_TRACK = "#8a8378", "#e2dacb", "#ede4d3"
CARD_SERIES = ["#d9701a", "#3d8f83", "#5b7fa6", "#8a6fae", "#c2527a", "#7a8a3f", "#b8860b", "#556270"]

def _plain_name(s, limit=10):
    """画像描画用に絵文字などフォントに無い文字を落とす（豆腐▯対策）"""
    out = "".join(ch for ch in str(s) if ord(ch) < 0x2600 or 0x3000 <= ord(ch) <= 0x9FFF
                  or 0xF900 <= ord(ch) <= 0xFAFF or 0xFF00 <= ord(ch) <= 0xFFEF).strip() or "？"
    return out[:limit] + ("…" if len(out) > limit else "")

def _find_cjk_font_bold():
    """見出し用の太字フォント。無ければ None（ストロークの擬似太字だけで頑張る）"""
    for f in ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
              "C:/Windows/Fonts/meiryob.ttc", "C:/Windows/Fonts/YuGothB.ttc"):
        if os.path.exists(f):
            return f
    return None

def _find_emoji_font():
    """モノクロ絵文字フォント（各賞アイコン用）。リポジトリ同梱のNoto Emoji優先"""
    for f in (os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts", "NotoEmoji.ttf"),
              "C:/Windows/Fonts/seguiemj.ttf"):
        if os.path.exists(f):
            return f
    return None

def render_tsushinbo_card(d1, d2, ranking, award_items, series, manual=False):
    """通信簿を1枚のカード画像に（ヘッダー・ランキング・各賞・週間グラフ）。描けなければ None → テキストにフォールバック"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    import matplotlib.patheffects as _pe
    fp = font_manager.FontProperties(fname=f)
    fb = _find_cjk_font_bold()
    fpb = font_manager.FontProperties(fname=fb or f)   # Boldファイルがあれば本物の太字
    ef = _find_emoji_font()
    efp = font_manager.FontProperties(fname=ef) if ef else None

    def _embold(t, lw=0.7):
        """同色ストロークの擬似太字（Boldフォントが無い環境でも見出しを太らせる）"""
        t.set_path_effects([_pe.withStroke(linewidth=lw, foreground=t.get_color())])
        return t
    n_r = max(1, len(ranking))
    n_a = (len(award_items) + 1) // 2
    h_rank, h_chart = 0.55 + 0.46 * n_r, 2.2
    h_awd = (0.55 + 0.42 * n_a) if award_items else 0.05
    body = [h_rank, h_awd, h_chart, h_chart]
    band_h, top_pad, gap_below = 0.62, 0.16, 0.30   # 帯の高さ・帯の上の余白・帯とランキングの間
    body_gaps = 0.38 * (sum(body) / len(body)) * (len(body) - 1)
    fig_h = top_pad + band_h + gap_below + sum(body) + body_gaps + 0.55
    fig = plt.figure(figsize=(8.6, fig_h), dpi=150)
    fig.patch.set_facecolor(CARD_CREAM)
    gs = fig.add_gridspec(4, 1, height_ratios=body, hspace=0.38,
                          left=0.06, right=0.965,
                          top=1 - (top_pad + band_h + gap_below) / fig_h, bottom=0.55 / fig_h)
    # ヘッダー（墨色の帯・左右いっぱい。位置はグリッドと独立に制御）
    axh = fig.add_axes([0.0, 1 - (top_pad + band_h) / fig_h, 1.0, band_h / fig_h])
    axh.axis("off")
    axh.add_patch(plt.Rectangle((0, 0), 1, 1, transform=axh.transAxes, color=CARD_INK, clip_on=False))
    _embold(axh.text(0.075, 0.5, "今週の通信簿", transform=axh.transAxes, fontproperties=fpb,
                     fontsize=19, color=CARD_CREAM, va="center"), 0.9)
    axh.text(0.94, 0.5, f"{d1[5:].replace('-', '/')} 〜 {d2[5:].replace('-', '/')}" + ("（手動）" if manual else ""),
             transform=axh.transAxes, fontproperties=fp, fontsize=11, color=CARD_AMBER, va="center", ha="right")
    # ランキング（達成率バー）
    axr = fig.add_subplot(gs[0])
    axr.axis("off")
    axr.set_xlim(0, 1)
    axr.set_ylim(0, n_r + 1.0)
    _embold(axr.text(0, n_r + 0.5, "最低限 達成率ランキング", fontproperties=fpb, fontsize=14, color=CARD_INK))
    if not ranking:
        axr.text(0.02, n_r - 0.5, "判定対象の人がいませんでした（/saitei で設定）", fontproperties=fp, fontsize=11, color=CARD_MUTED)
    medal_c = ["#d9a521", "#a8adb8", "#c08552"]
    for i, x in enumerate(ranking):
        y = n_r - 1 - i + 0.5
        axr.scatter([0.02], [y], s=290, color=(medal_c[i] if i < 3 else CARD_TRACK), zorder=2, clip_on=False)
        axr.text(0.02, y, str(i + 1), fontproperties=fpb, fontsize=10, ha="center", va="center", zorder=3,
                 color=CARD_INK if i < 3 else CARD_MUTED)
        axr.text(0.055, y, _plain_name(x["name"]), fontproperties=fpb, fontsize=12, color=CARD_INK, va="center")
        rate = x["ach"] / x["judged"] if x["judged"] else 0
        bx, bw = 0.34, 0.36
        axr.barh([y], [bw], left=bx, height=0.36, color=CARD_TRACK, zorder=1)
        if rate > 0:
            axr.barh([y], [bw * rate], left=bx, height=0.36, color=(CARD_ORANGE if rate >= 0.999 else CARD_AMBER), zorder=2)
        axr.text(bx + bw + 0.015, y, f"{x['ach']}/{x['judged']}日  {round(rate * 100)}%",
                 fontproperties=fp, fontsize=11, color=CARD_INK, va="center")
        if x["streak"] >= 2:
            axr.text(1.0, y, f"連続{x['streak']}日", fontproperties=fpb, fontsize=10.5, color=CARD_ORANGE, va="center", ha="right")
    # 各賞（2列）
    axa = fig.add_subplot(gs[1])
    axa.axis("off")
    if award_items:
        axa.set_xlim(0, 1)
        axa.set_ylim(0, n_a + 1.0)
        _embold(axa.text(0, n_a + 0.5, "今週の各賞", fontproperties=fpb, fontsize=14, color=CARD_INK))
        for idx, a in enumerate(award_items):
            col, row = idx % 2, idx // 2
            x0 = 0.0 if col == 0 else 0.52
            y = n_a - 1 - row + 0.5
            neta = a["title"] in ("寝坊賞", "二度寝賞", "こら賞")
            icon_c = CARD_MUTED if neta else CARD_ORANGE
            em = (a.get("emoji") or "").replace("️", "")   # VS16はフォントに無いので除去
            if efp and em:
                axa.text(x0 + 0.012, y, em, fontproperties=efp, fontsize=12, color=icon_c, ha="center", va="center")
            else:
                axa.scatter([x0 + 0.012], [y], s=60, color=icon_c, clip_on=False)
            axa.text(x0 + 0.035, y, a["title"], fontproperties=fpb, fontsize=10.5, color=CARD_INK, va="center")
            # 受賞者が多いと隣の列にはみ出すので「先頭＋ほかn名」に短縮。値（回数など）は必ず残し、名前側だけ縮める
            val = f"（{a['value']}）"
            if len(a["names"]) == 1:
                names = _plain_name(a["names"][0], max(3, 14 - len(val)))
            else:
                suffix = f" ほか{len(a['names']) - 1}名"
                names = _plain_name(a["names"][0], max(3, 14 - len(val) - len(suffix))) + suffix
            axa.text(x0 + 0.19, y, names + val, fontproperties=fp, fontsize=10, color="#4a453d", va="center")
    # 週間グラフ（起床・睡眠）
    days = [date.fromisoformat(d1) + timedelta(days=i) for i in range(7)]
    labels = [f"{d.month}/{d.day}({DAY_CHARS[d.weekday()]})" for d in days]
    xs = list(range(7))
    ax1 = fig.add_subplot(gs[2])
    ax2 = fig.add_subplot(gs[3])
    for ax, title in ((ax1, "起床時刻（時）"), (ax2, "睡眠時間（h）")):
        ax.set_facecolor(CARD_CREAM)
        for sp in ("top", "right"):
            ax.spines[sp].set_visible(False)
        for sp in ("left", "bottom"):
            ax.spines[sp].set_color(CARD_LINE)
        ax.tick_params(colors=CARD_MUTED, labelsize=9)
        ax.grid(axis="y", color=CARD_LINE, alpha=0.9, linewidth=0.8)
        _embold(ax.set_title(title, fontproperties=fpb, fontsize=12.5, color=CARD_INK, loc="left"), 0.6)
        ax.set_xticks(xs)
        ax.set_xticklabels(labels, fontproperties=fp)
    wake_all, sleep_all = [], []
    for si, sr in enumerate(series):
        ccol = CARD_SERIES[si % len(CARD_SERIES)]
        wv = [sr["wake"].get(d.isoformat(), float("nan")) for d in days]
        sv = [sr["sleep"].get(d.isoformat(), float("nan")) for d in days]
        wake_all += [v for v in wv if v == v]
        sleep_all += [v for v in sv if v == v]
        nm = _plain_name(sr["name"], 8)
        ax1.plot(xs, wv, marker="o", markersize=4.5, linewidth=2, color=ccol, label=nm)
        ax2.plot(xs, sv, marker="o", markersize=4.5, linewidth=2, color=ccol, label=nm)
    if wake_all:
        ax1.set_ylim(max(0.0, min(wake_all) - 1), max(wake_all) + 1)
    if sleep_all:
        ax2.set_ylim(max(0.0, min(sleep_all) - 1), max(sleep_all) + 1)
    if series:
        ax1.legend(prop=fp, fontsize=8, frameon=False, ncol=min(4, len(series)),
                   loc="lower right", bbox_to_anchor=(1.0, 1.0))   # グラフの外（右上）に置いて線と被らせない
    else:
        for ax in (ax1, ax2):
            ax.text(0.5, 0.5, "今週は記録がありませんでした", transform=ax.transAxes,
                    fontproperties=fp, fontsize=11, color=CARD_MUTED, ha="center", va="center")
    fig.text(0.5, 0.18 / fig_h, "来週もほどほどに、最低限を守ろう", fontproperties=fp, fontsize=9.5, color=CARD_MUTED, ha="center")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
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
                sleep_days[e["day"]] = sleep_days.get(e["day"], 0.0) + float(e["note"])
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
    def award(emoji, title, key, pick_min=False, need=lambda x: True, fmt=lambda v: str(v)):
        """同点は全員表彰。テキストにも通信簿カードにも使える構造で返す"""
        cands = [x for x in stats if x[key] is not None and need(x)]
        if not cands:
            return None
        best_val = min(x[key] for x in cands) if pick_min else max(x[key] for x in cands)
        if not pick_min and not best_val:
            return None
        winners = [x for x in cands if x[key] == best_val]
        return {"emoji": emoji, "title": title, "names": [w["name"] for w in winners], "value": fmt(best_val)}
    kaikin_need = 3 if kind == "week" else 15
    kaikin = [x for x in judged if x["judged"] >= kaikin_need and x["ach"] == x["judged"]]
    mt_names, mt_best = await meshitero_winners(d1, d2)
    award_items = [a for a in (
        {"emoji": "👑", "title": "皆勤賞", "names": [x["name"] for x in kaikin], "value": "全日達成"} if kaikin else None,
        award("🌅", "早起き賞", "wake_avg", pick_min=True, need=lambda x: x["wake_n"] >= 3, fmt=lambda v: f"平均 {int(v)//60}:{int(v)%60:02d}"),
        award("🛌", "ぐっすり賞", "sleep_avg", need=lambda x: x["sleep_avg"] is not None, fmt=lambda v: f"平均 {v:.1f}h"),
        award("🧹", "家事賞", "chores", fmt=lambda v: f"{v}回"),
        award("🍚", "ごはん賞", "meals", fmt=lambda v: f"{v}回報告"),
        {"emoji": "🍜", "title": "飯テロ賞", "names": mt_names, "value": f"拍手{mt_best}"} if mt_names else None,
        award("🛁", "きれい好き賞", "baths", fmt=lambda v: f"{v}日"),
        award("🪥", "歯磨き賞", "teeth", fmt=lambda v: f"{v}回"),
        award("🌟", "がんばり屋賞", "efforts", fmt=lambda v: f"{v}件"),
        award("👏", "ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),
        award("🏃", "ラジオ体操賞", "radio", fmt=lambda v: f"{v}回"),
        award("🐷", "寝坊賞", "late", fmt=lambda v: f"{v}回"),
        award("😴", "二度寝賞", "nizone", fmt=lambda v: f"{v}回"),
        award("👹", "こら賞", "miss_n", fmt=lambda v: f"未達 {v}件"),
    ) if a]
    try:
        wa = await work_award(d1, d2)
        if wa:
            award_items.append(wa)
    except Exception as e:
        print(f"もくもく賞集計エラー: {e!r}", flush=True)
    try:
        award_items.extend(await kadai_awards(d1, d2))
    except Exception as e:
        print(f"課題賞集計エラー: {e!r}", flush=True)
    if kind == "week":
        iw = await iyashi_winner(d1, d2)
        if iw:
            award_items.append(iw)
    awards = [f"{a['emoji']} {a['title']}：" + "、".join(f"**{n}**" for n in a["names"]) + f"（{a['value']}）" for a in award_items]
    d1s, d2s = d1[5:].replace("-", "/"), d2[5:].replace("-", "/")
    if kind == "week":
        emb = discord.Embed(title=f"📮 今週の通信簿（{d1s}〜{d2s}）" + ("（手動）" if manual else ""), color=discord.Color.gold())
    else:
        emb = discord.Embed(title=f"🏆 {label}の月間表彰（{d1s}〜{d2s}）" + ("（手動）" if manual else ""), color=discord.Color.purple())
        if judged:
            mvp = judged[0]
            emb.description = f"👑 **月間MVP：{mvp['name']}**　達成 {mvp['ach']}/{mvp['judged']}日（{round(mvp['ach']*100/mvp['judged'])}%）"
    card_buf = None
    if kind == "week":
        try:   # 週次はデザイン済みの通信簿カード（1枚画像）を優先。作れなければ従来のテキスト表示
            card_buf = await asyncio.to_thread(render_tsushinbo_card, d1, d2, judged[:10], award_items, series, manual)
        except Exception as e:
            print(f"通信簿カード生成エラー: {e!r}", flush=True)
    if not card_buf:
        emb.add_field(name="🏆 最低限 達成率ランキング", value="\n".join(lines)[:1024] if lines else "判定対象の人がいませんでした（/saitei で設定）", inline=False)
        if awards:
            emb.add_field(name=("🎖 今週の各賞" if kind == "week" else "🎖 月間各賞"), value="\n".join(awards)[:1024], inline=False)
    if kind == "week":
        try:
            async with db.execute("SELECT t.*, (SELECT COUNT(*) FROM tip_saves s WHERE s.tip_id=t.id) AS saves FROM tips t "
                                  "WHERE t.day BETWEEN ? AND ? ORDER BY saves DESC, t.ts DESC LIMIT 3", (d1, d2)) as c:
                trows = await c.fetchall()
            if trows:
                emb.add_field(name="📚 今週のTIPS", value="\n".join(tip_line(r) for r in trows)[:1024], inline=False)
        except Exception as e:
            print(f"今週のTIPS集計エラー: {e!r}", flush=True)
    emb.set_footer(text="来週もほどほどに、最低限を守ろう" if kind == "week" else "来月もほどほどに、最低限を守ろう")
    file = None
    if card_buf:
        file = discord.File(card_buf, filename="tsushinbo.png")
        emb.set_image(url="attachment://tsushinbo.png")
    elif kind == "week" and series:
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

async def backup_db(keep=7):
    """SQLiteのバックアップAPIで安全に世代コピー（毎晩の判定後に実行）"""
    import sqlite3
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dest = os.path.join(BACKUP_DIR, f"seikatsu-{day_str(now_jst())}.db")

    def _do():
        src = sqlite3.connect(DB_PATH)
        dst = sqlite3.connect(dest)
        with dst:
            src.backup(dst)
        src.close()
        dst.close()
    await asyncio.to_thread(_do)
    olds = sorted(f for f in os.listdir(BACKUP_DIR) if re.fullmatch(r"seikatsu-\d{4}-\d{2}-\d{2}\.db", f))
    for f in olds[:-keep]:
        try:
            os.remove(os.path.join(BACKUP_DIR, f))
        except Exception:
            pass
    print(f"バックアップ完了: {dest}（{len(olds[-keep:])}世代）", flush=True)
    return dest

# ------------------------------------------------------------
#  🔒 運用：管理者だけが見る #bot管理 へのエラー通知・yt-dlp自動更新・DBの外部バックアップ
#  （VMのログを見に行かなくても、壊れたら気づけて直し方がわかるように）
# ------------------------------------------------------------
BOTLOG_CH_NAME = "bot管理🔒"
_alert_last = {}   # key -> 最後に通知した時刻（同じ問題を連投しない）
_ops_hello = {"done": False}

async def ensure_botlog_channel(guild, cat):
    """管理者とBotだけが見える #bot管理🔒 を用意（/setupから・何度実行しても安全）"""
    ch = None
    cid = await meta_get("ch_botlog")
    if cid:
        ch = guild.get_channel(int(cid))
    if ch is None:
        ch = discord.utils.get(guild.text_channels, name=BOTLOG_CH_NAME)
    ow = {guild.default_role: discord.PermissionOverwrite(view_channel=False),
          guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, attach_files=True, embed_links=True)}
    if ch is None:
        ch = await guild.create_text_channel(BOTLOG_CH_NAME, category=cat, overwrites=ow,
                                             topic="Botの不調・自動メンテの記録（管理者だけに見えます）")
        await ch.send("🔒 ここはBotの管理用チャンネルです（管理者にだけ見えます）。\n"
                      "エラーと直し方・yt-dlpの自動更新・週1回のDBバックアップがここに届きます。詳しくは リポジトリの 保守マニュアル.md")
    else:
        try:
            await ch.edit(overwrites=ow)   # 非公開設定が崩れていたら直す
        except Exception as e:
            print(f"bot管理チャンネルの権限修正失敗: {e!r}", flush=True)
    await meta_set("ch_botlog", ch.id)
    return ch

async def bot_alert(text, key=None, cooldown_h=6.0, file=None):
    """#bot管理🔒 に通知する。同じkeyはcooldown_h時間に1回まで。チャンネルが無ければログだけ"""
    import time as _t
    if key:
        last = _alert_last.get(key, 0)
        if _t.time() - last < cooldown_h * 3600:
            return False
        _alert_last[key] = _t.time()
    print(f"[bot管理] {text}", flush=True)
    try:
        cid = await meta_get("ch_botlog")
        ch = bot.get_channel(int(cid)) if cid else None
        if ch:
            await ch.send(text[:1900], file=file)
            return True
    except Exception as e:
        print(f"bot管理への通知失敗: {e!r}", flush=True)
    return False

YT_ERROR_GUIDES = (   # yt-dlpのエラー文 → (通知key, 何が起きたか・直し方)
    (("sign in to confirm", "login_required", "not a bot"), "yt_cookie",
     "🍪 **YouTubeのCookieが切れたみたい**（「Sign in to confirm」）\n"
     "直し方：①PCのシークレットウィンドウでYouTubeにログイン ②拡張「Get cookies.txt LOCALLY」でExport ③**すぐウィンドウを閉じる**"
     "（開いたままだとCookieが無効化される）④`scp cookies.txt oracle:/home/ubuntu/seikatsu-bot/cookies.txt` ⑤`seikatsu-deploy`"),
    (("no video formats", "n challenge", "nsig", "requested format is not available", "signature extraction"), "yt_update",
     "🛠 **yt-dlpが古くなったみたい**（YouTube側の仕様変更）。いま自動で最新版に更新を試みます。\n"
     "直らないときは手動で：`ssh oracle` →`cd seikatsu-bot && venv/bin/pip install -U --pre \"yt-dlp[default]\"` → `sudo systemctl restart seikatsu-bot`"),
    (("page needs to be reloaded",), "yt_reload",
     "🔁 YouTubeが「ページの再読み込みが必要」と返しました。多くは一時的なものです。続くならCookieを取り直してください。"),
)

def classify_yt_error(msg):
    """yt-dlpの例外文 → (key, 案内文)。当てはまらなければ None"""
    low = (msg or "").lower()
    for words, key, guide in YT_ERROR_GUIDES:
        if any(w in low for w in words):
            return key, guide
    return None

async def report_yt_error(e):
    hit = classify_yt_error(repr(e))
    if not hit:
        return
    key, guide = hit
    if await bot_alert(guide + f"\n-# {repr(e)[:300]}", key=key):
        if key == "yt_update":
            asyncio.create_task(ytdlp_self_update("再生エラー"))

def _ytdlp_installed_version():
    """いまディスク上にあるyt-dlpのバージョン（別プロセスで確認＝読み込み済みモジュールに左右されない）"""
    import subprocess
    import sys as _sys
    try:
        r = subprocess.run([_sys.executable, "-c", "import yt_dlp.version as v; print(v.__version__)"],
                           capture_output=True, text=True, timeout=60)
        return (r.stdout or "").strip() or None
    except Exception:
        return None

_update_lock = {"busy": False}

async def ytdlp_self_update(reason=""):
    """yt-dlpを最新ナイトリーに更新し、変わっていたら再生が止まっているときに再起動する（Restart=alwaysで復帰）"""
    import sys as _sys
    if _update_lock["busy"]:
        return None
    _update_lock["busy"] = True
    try:
        before = await asyncio.to_thread(_ytdlp_installed_version)
        proc = await asyncio.create_subprocess_exec(
            _sys.executable, "-m", "pip", "install", "-q", "-U", "--pre", "yt-dlp[default]",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=600)
        except asyncio.TimeoutError:
            proc.kill()
            await bot_alert("⚠️ yt-dlpの自動更新がタイムアウトしました（10分）。手動更新をお願いします。", key="yt_upd_fail", cooldown_h=24)
            return None
        after = await asyncio.to_thread(_ytdlp_installed_version)
        if proc.returncode != 0 or not after:
            await bot_alert(f"⚠️ yt-dlpの自動更新に失敗しました（{reason}）\n-# {(out or b'').decode(errors='replace')[-300:]}",
                            key="yt_upd_fail", cooldown_h=24)
            return None
        if after == before:
            print(f"yt-dlpは最新（{after}）", flush=True)
            return False
        await bot_alert(f"⬆️ yt-dlpを更新しました：{before} → {after}（{reason}）。再生が止まっているタイミングで自動で再起動します。")
        asyncio.create_task(_restart_when_idle())
        return True
    finally:
        _update_lock["busy"] = False

async def _restart_when_idle(max_wait_min=180):
    """再生中は待ち、止まったら（最長3時間で打ち切り）プロセスを終了→systemdが10秒後に再起動"""
    for _ in range(max_wait_min):
        vc = _player.get("vc")
        if not (vc and vc.is_connected() and (vc.is_playing() or vc.is_paused())):
            break
        await asyncio.sleep(60)
    print("♻️ 更新を反映するため再起動します", flush=True)
    try:
        await db.commit()
    except Exception:
        pass
    os._exit(0)

async def offsite_backup():
    """DBを圧縮して #bot管理🔒 に添付＝VMが壊れても記録が残る（週1回）"""
    import gzip
    import shutil
    dest = await backup_db()
    gz = dest + ".gz"

    def _z():
        with open(dest, "rb") as f, gzip.open(gz, "wb") as g:
            shutil.copyfileobj(f, g)
    await asyncio.to_thread(_z)
    size_mb = os.path.getsize(gz) / 1024 / 1024
    try:
        if size_mb > 9.5:   # Discordの添付上限（ブーストなし10MB）
            await bot_alert(f"⚠️ DBが大きくなり（圧縮後 {size_mb:.1f}MB）Discordに添付できません。VMの `backups/` から手動で保存してください。",
                            key="bk_big", cooldown_h=24 * 6)
            return False
        ok = await bot_alert(f"💾 週1回のDBバックアップです（{day_str(now_jst())}・{size_mb:.2f}MB）。\n"
                             "-# 復元：VMで `gunzip` して `seikatsu.db` と差し替え → 再起動。手順は リポジトリの 保守マニュアル.md",
                             file=discord.File(gz, filename=os.path.basename(gz)))
        return ok
    finally:
        try:
            os.remove(gz)
        except Exception:
            pass

@bot.tree.command(name="kanri", description="【管理者用】Botのメンテ（状態確認・今すぐバックアップ・yt-dlp更新）")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(sousa="やること")
@app_commands.choices(sousa=[app_commands.Choice(name="状態を確認", value="status"),
                             app_commands.Choice(name="DBを今すぐ #bot管理🔒 にバックアップ", value="backup"),
                             app_commands.Choice(name="yt-dlpを今すぐ更新（変わったら自動で再起動）", value="update"),
                             app_commands.Choice(name="記念カードを試しに作る（自分にだけ）", value="anniv")])
async def kanri_command(interaction, sousa: app_commands.Choice[str]):
    await interaction.response.defer(ephemeral=True)
    if sousa.value == "anniv":
        f0 = date.fromisoformat(CIRCLE_FOUNDED)
        nxt = next(dd for dd in (date(f0.year + (f0.month + k - 1) // 12, (f0.month + k - 1) % 12 + 1, f0.day) for k in range(6, 1200, 6))
                   if dd > now_jst().date() and anniversary_label(dd))
        buf, _ = await build_anniversary(day_str(now_jst()), anniversary_label(nxt))
        await interaction.followup.send(f"🎉 試し作成（いまの時点の合計）。本番は **{nxt.month}/{nxt.day}（{anniversary_label(nxt)}）の9時** に #つうしんぼ へ自動投稿されます。",
                                        file=discord.File(buf, filename="anniversary-preview.png") if buf else discord.utils.MISSING,
                                        ephemeral=True)
        return
    if sousa.value == "backup":
        ok = await offsite_backup()
        await interaction.followup.send("💾 バックアップを #bot管理🔒 に送りました。" if ok else "⚠️ 送れませんでした（/setup で #bot管理🔒 を作ってね）",
                                        ephemeral=True)
        return
    if sousa.value == "update":
        r = await ytdlp_self_update("手動")
        await interaction.followup.send({True: "⬆️ 更新しました。再生が止まり次第、自動で再起動します。",
                                         False: "✅ yt-dlpはすでに最新でした。",
                                         None: "⚠️ 更新に失敗しました（#bot管理🔒 に詳細）。"}[r], ephemeral=True)
        return
    ver = await asyncio.to_thread(_ytdlp_installed_version)
    try:
        db_mb = os.path.getsize(DB_PATH) / 1024 / 1024
    except Exception:
        db_mb = 0
    vc = _player.get("vc")
    lines = [f"🤖 **Botの状態**",
             f"・yt-dlp：{ver or '不明'}",
             f"・YouTube Cookie：{'あり' if os.path.exists(YTDLP_COOKIES) else '⚠️ なし（YouTubeが再生できない）'}",
             f"・Spotify連携：{'設定あり' if SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET else '未設定'}",
             f"・DB：{db_mb:.2f}MB／最終外部バックアップ週：{await meta_get('ops_backup_week') or 'まだ'}",
             f"・音楽：{'再生中 ' + (_player.get('current') or '') if vc and vc.is_connected() and vc.is_playing() else ('VCで待機中' if vc and vc.is_connected() else 'VCにいない')}",
             f"・管理チャンネル：{'あり' if await meta_get('ch_botlog') else '⚠️ なし（/setup で作成）'}",
             f"・長期休暇モード：{(break_text(now_jst()) + '（期間中）') if break_on(now_jst()) else ((break_text() + '（/kyukaの予定）') if _BREAK['start'] else 'いまは期間外')}"
             f"（大学の予定の長期休暇 {len(_UNIV_BREAKS)} 件）"]
    await interaction.followup.send("\n".join(lines), ephemeral=True)

def parse_md_ex(s, ref):
    """'2/1' '2月1日' '2027/2/1' '27/2/1' '2027年2月1日' → (date, 年を書いたか)。年なしは ref の年。読めなければ (None, False)"""
    t = unicodedata.normalize("NFKC", (s or "")).strip().replace(".", "/")
    try:
        m = re.fullmatch(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", t)
        if m:
            return date(int(m[1]), int(m[2]), int(m[3])), True
        m = re.fullmatch(r"(\d{2})/(\d{1,2})/(\d{1,2})", t)   # 27/2/1 → 2027年
        if m:
            return date(2000 + int(m[1]), int(m[2]), int(m[3])), True
        m = re.fullmatch(r"(\d{1,2})[/月](\d{1,2})日?", t)
        if m:
            return date(ref.year, int(m[1]), int(m[2])), False
    except ValueError:
        return None, False
    return None, False

def parse_md(s, ref):
    """parse_md_ex の日付だけ版"""
    return parse_md_ex(s, ref)[0]

def parse_span(spec, now, past_ok_days=1):
    """日付1欄の入力 → (はじまり, おわり) のdate。'11/20' '2/10-4/4' '2027/2/10-4/4' '27/2/10〜27/4/4' など。
    年を書かなければ自動：おわりがはじまりより前なら年またぎ、過ぎた日付（past_ok_days日より前）なら来年のこと。
    年を書いたらその年のまま（おわりの年を省けば、はじまりの年から数える）。読めなければ None"""
    t = _norm_dashes(unicodedata.normalize("NFKC", spec or "").strip())
    t = re.sub(r"[\(（][月火水木金土日][\)）]", "", t)   # 11/3(火) の曜日は無視
    t = re.sub(r"(\d{4})-(\d{1,2})-(\d{1,2})", r"\1/\2/\3", t)   # 2027-02-10 は区切りと紛れないよう先にスラッシュへ
    parts = [p for p in re.split(r"\s*-\s*", t) if p]
    if not parts or len(parts) > 2:
        return None
    s, sy = parse_md_ex(parts[0], now)
    if not s:
        return None
    if len(parts) == 2:
        e, ey = parse_md_ex(parts[1], s if sy else now)
        if not e:
            return None
    else:
        e, ey = s, sy
    if e < s and not ey:   # 12/20〜1/10 のように年をまたぐ
        e = e.replace(year=e.year + 1)
    if e < s:
        return None
    if not sy and e < now.date() - timedelta(days=past_ok_days):
        try:
            s, e = s.replace(year=s.year + 1), e.replace(year=e.year + 1)
        except ValueError:   # 2/29
            s, e = s + timedelta(days=365), e + timedelta(days=365)
    return s, e

def fmt_full(d):
    """2027年2月10日(水)"""
    return f"{d.year}年{d.month}月{d.day}日({DAY_CHARS[d.weekday()]})"

def fmt_full_span(s, e):
    if s == e:
        return fmt_full(s)
    tail = f"{e.month}月{e.day}日({DAY_CHARS[e.weekday()]})" if e.year == s.year else fmt_full(e)
    return f"{fmt_full(s)}〜{tail}"

def shift_year(d, n):
    try:
        return d.replace(year=d.year + n)
    except ValueError:   # 2/29 → 2/28
        return d.replace(year=d.year + n, day=28)

class YearShiftView(discord.ui.View):
    """登録直後の確認に「◀ 1年前／1年後 ▶」＝年の自動判定が違ったときにワンタップで直す"""

    def __init__(self, table, row_id, uid, head):
        super().__init__(timeout=900)
        assert table in ("univ_events", "private_events")
        self.table, self.row_id, self.uid, self.head = table, row_id, str(uid), head

    async def _shift(self, interaction, n):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("登録した本人だけが直せます。", ephemeral=True)
            return
        async with db.execute(f"SELECT start_day, end_day FROM {self.table} WHERE id=?", (self.row_id,)) as c:   # tableは内部定数のみ
            r = await c.fetchone()
        if not r:
            await interaction.response.edit_message(content="⚠️ もう消えている予定です。", view=None)
            return
        s, e = shift_year(date.fromisoformat(r["start_day"]), n), shift_year(date.fromisoformat(r["end_day"]), n)
        await db.execute(f"UPDATE {self.table} SET start_day=?, end_day=? WHERE id=?", (s.isoformat(), e.isoformat(), self.row_id))
        await db.commit()
        await load_break()
        await interaction.response.edit_message(content=f"{self.head}\n📅 **{fmt_full_span(s, e)}**（年を直しました）", view=self)

    @discord.ui.button(label="◀ 1年前にする", style=discord.ButtonStyle.secondary)
    async def b_prev(self, interaction, button):
        await self._shift(interaction, -1)

    @discord.ui.button(label="1年後にする ▶", style=discord.ButtonStyle.secondary)
    async def b_next(self, interaction, button):
        await self._shift(interaction, +1)

async def set_break(start, end, shift_min, name):
    """長期休暇モードを保存（start/end=date。start=None で解除）"""
    if start is None:
        for k in ("break_start", "break_end"):
            await db.execute("DELETE FROM meta WHERE key=?", (k,))
        await db.commit()
    else:
        await meta_set("break_start", start.isoformat())
        await meta_set("break_end", end.isoformat())
        await meta_set("break_shift", str(int(shift_min)))
        await meta_set("break_name", name)
    await load_break()

@bot.tree.command(name="kyuka", description="【管理者用】長期休暇モード（春休み・夏休み）：時間割と毎週課題を止め、起床締切をずらす")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(kaishi="はじまり 例: 2/1", owari="おわり 例: 3/31", zurashi="起床締切を何時間遅らせるか（既定2・0でずらさない）",
                       namae="名前（既定 春休み）", kaijo="True で長期休暇モードを解除")
async def kyuka_command(interaction, kaishi: str = None, owari: str = None, zurashi: float = 2.0,
                        namae: str = "春休み", kaijo: bool = False):
    now = now_jst()
    if kaijo:
        await set_break(None, None, 0, "")
        await interaction.response.send_message("🌸 長期休暇モードを解除しました（時間割・毎週課題・起床締切が通常に戻ります）。", ephemeral=True)
        return
    if not kaishi or not owari:
        st = f"いまの設定：**{break_text()}**・起床締切 +{_BREAK['shift'] / 60:g}時間" + ("（期間中）" if break_on(now) else "") \
            if _BREAK["start"] else "いまは設定されていません。"
        await interaction.response.send_message(
            f"🌸 {st}\n-# 設定：`/kyuka kaishi:2/1 owari:3/31 zurashi:2`　解除：`/kyuka kaijo:True`", ephemeral=True)
        return
    s, e = parse_md(kaishi, now), parse_md(owari, now)
    if not s or not e:
        await interaction.response.send_message("⚠️ 日付が読めませんでした。例：`2/1` `3/31`", ephemeral=True)
        return
    if e < s:   # 12/20〜1/10 のように年をまたぐ
        e = e.replace(year=e.year + 1)
    if e < now.date():   # 過ぎた期間を書いたら来年のこととみなす
        s, e = s.replace(year=s.year + 1), e.replace(year=e.year + 1)
    shift = int(max(0.0, min(6.0, zurashi)) * 60)
    await set_break(s, e, shift, (namae or "長期休暇").strip()[:12])
    msg = (f"🌸 **{break_text()}** を設定しました。期間中は\n"
           "・☀️朝の予定に時間割を出さない\n・🔁毎週課題を自動で作らない（休み明けの朝から再開）\n"
           + (f"・起床の締切を毎日 **{shift / 60:g}時間** 遅らせる（各自の土日設定のほうが大きければそちら）\n" if shift else "・起床の締切はそのまま\n")
           + "・手で登録した課題と試験のお知らせは通常どおり")
    await interaction.response.send_message(msg, ephemeral=True)
    ch = await get_ch("wake")
    if ch:
        await ch.send(f"🌸 **{break_text()}モード**のお知らせ：期間中は時間割と毎週課題がお休み"
                      + (f"、起床の締切が **{shift / 60:g}時間** 遅くなります。" if shift else "になります。")
                      + "\n-# 休み明けは自動で元に戻ります。ゆるく、でも生活リズムは崩さずにいこう")

PLAY_HISTORY_KEEP_DAYS = int(os.getenv("PLAY_HISTORY_KEEP_DAYS", "20"))   # 再生履歴を残す日数（週間ヘビロテには7日あれば足りる）

async def prune_play_history(now=None):
    """古い再生履歴を消す（プレイリスト1回で最大50件たまるため）。消した件数を返す"""
    now = now or now_jst()
    cutoff = int((now - timedelta(days=PLAY_HISTORY_KEEP_DAYS)).timestamp())
    cur = await db.execute("DELETE FROM play_history WHERE ts < ?", (cutoff,))
    await db.commit()
    return cur.rowcount

async def ops_daily(now):
    """毎朝の運用タスク：火曜4時=yt-dlp更新／月曜5時=外部バックアップ（それぞれ週1回）"""
    wk = now.strftime("%G-W%V")
    if now.weekday() == 1 and now.hour == 4 and await meta_get("ops_ytupd_week") != wk:
        await meta_set("ops_ytupd_week", wk)
        await ytdlp_self_update("週1回の定期更新")
    if now.weekday() == 0 and now.hour == 5 and await meta_get("ops_backup_week") != wk:
        await meta_set("ops_backup_week", wk)
        await offsite_backup()
    await maybe_post_anniversary(now)   # 🎉 発足から半年・1周年…の9時に記念カード
    day = day_str(now)
    if now.hour == 4 and await meta_get("ops_prune_day") != day:   # 毎朝4時：古い再生履歴の掃除
        await meta_set("ops_prune_day", day)
        n = await prune_play_history(now)
        if n:
            print(f"🎵 再生履歴を{n}件削除（{PLAY_HISTORY_KEEP_DAYS}日より前）", flush=True)

# ---- ⏰ 起床リマインド（オプトイン）：締切のN分前に未報告ならメンション ----
_wakeremind_cache = {"day": None, "done": set()}

def wake_remind_target(u, now):
    """この人に⏰を出す時刻（HH:MM）。締切なし・計算不能なら None。土日の後ろ倒しも反映"""
    dl = effective_deadline(u, now)
    if not dl:
        return None
    h, m = map(int, dl.split(":"))
    t = h * 60 + m - WAKE_REMIND_MIN
    return None if t < 0 else f"{t // 60:02d}:{t % 60:02d}"

async def wake_remind_check(now):
    day = day_str(now)
    if _wakeremind_cache["day"] != day:
        _wakeremind_cache.update(day=day, done=set())
    async with db.execute("SELECT * FROM users WHERE wake_remind=1 AND wake_deadline IS NOT NULL") as c:
        users = await c.fetchall()
    if not users:
        return
    cur_min = now.hour * 60 + now.minute
    for u in users:
        uid = u["id"]
        if uid in _wakeremind_cache["done"]:
            continue
        target = wake_remind_target(u, now)
        dl = effective_deadline(u, now)
        if not target or not dl:
            continue
        th, tm = map(int, target.split(":"))
        dh, dm = map(int, dl.split(":"))
        if not (th * 60 + tm <= cur_min < dh * 60 + dm):
            continue   # リマインド時刻〜締切の窓の中でだけ・1回だけ
        _wakeremind_cache["done"].add(uid)
        if u["wake_set_day"] == day or await events_on(uid, day, "wake") or await is_night_shift(uid, day):
            continue   # 設定初日・報告済み・夜勤明けは鳴らさない
        async with db.execute("SELECT 1 FROM off_days WHERE day=? AND user_id=?", (day, uid)) as c:
            if await c.fetchone():
                continue
        await post_log("wake", f"⏰ <@{uid}> ☀️の締切（{dl}）まであと{WAKE_REMIND_MIN}分！起きてたらポチッと")

PREJUDGE_SKIP = ("☀️", "🌙", "🏃")   # 22時にはもう取り返せない項目（朝のこと）はリマインドしない

def actionable_misses(misses):
    """事前リマインドで知らせる価値のある（今からでも間に合う）未達だけ残す"""
    return [m for m in misses if not m.startswith(PREJUDGE_SKIP)]

KAJI_REMIND_BEFORE_H = int(os.getenv("KAJI_REMIND_BEFORE_H", "3"))   # 判定の何時間前に家事リマインドを出すか（0で無効）

async def kaji_due_lines(day):
    """今日やる日なのにまだの家事がある人の一覧行（お休み・設定初日の人は除く）"""
    async with db.execute("SELECT * FROM users") as c:
        users = await c.fetchall()
    async with db.execute("SELECT user_id FROM off_days WHERE day=?", (day,)) as c:
        off = {r["user_id"] for r in await c.fetchall()}
    lines = []
    for u in users:
        if u["id"] in off or not any((u[col] or 0) > 0 for col, _, _, _ in KAJI_CATS):
            continue
        due = [st for st in await kaji_status(u, day) if st["due"]]
        if due:
            lines.append(f"・<@{u['id']}>　" + "／".join(
                f"{st['emoji']}{st['label']}（{kaji_interval_text(st['n'])}・{st['gap']}日ぶり）" for st in due))
    return lines

async def kaji_tomorrow_lines(day):
    """明日やる日になる家事の一覧行（前日のお知らせ用）。毎日の家事・今日すでにやる日のもの・明日お休みの人は除く"""
    tomorrow = (date.fromisoformat(day) + timedelta(days=1)).isoformat()
    async with db.execute("SELECT * FROM users") as c:
        users = await c.fetchall()
    async with db.execute("SELECT user_id FROM off_days WHERE day=?", (tomorrow,)) as c:
        off = {r["user_id"] for r in await c.fetchall()}
    lines = []
    for u in users:
        if u["id"] in off or not any((u[col] or 0) > 1 for col, _, _, _ in KAJI_CATS):
            continue
        today_due = {st["label"] for st in await kaji_status(u, day) if st["due"]}
        soon = [st for st in await kaji_status(u, tomorrow) if st["due"] and st["n"] > 1 and st["label"] not in today_due]
        if soon:
            lines.append(f"・<@{u['id']}>　" + "／".join(f"{st['emoji']}{st['label']}（{kaji_interval_text(st['n'])}）" for st in soon))
    return lines

async def kaji_remind(day):
    """判定の数時間前に #家事🧹 で、明日やる日の家事（前日のお知らせ）と、今日やる日なのにまだの家事を1通で（1日1回）"""
    ch = await get_ch("chore")
    if not ch:
        return 0
    tomorrow_lines = await kaji_tomorrow_lines(day)
    today_lines = await kaji_due_lines(day)
    if not tomorrow_lines and not today_lines:
        return 0
    parts = []
    if tomorrow_lines:
        parts.append("🗓 **明日やる日の家事**（洗剤・ごみ袋の準備などはお早めに）\n" + "\n".join(tomorrow_lines))
    if today_lines:
        parts.append(f"⚠️ **今日やる日なのに、まだの家事**（{JUDGE_HOUR}:00 に判定・まだ間に合います）\n" + "\n".join(today_lines))
    await ch.send("🧹 **家事のお知らせ**\n" + "\n\n".join(parts)[:1850] + "\n-# やったら 🧹 のボタンで記録してね")
    await bump_panel("chore")
    return len(tomorrow_lines) + len(today_lines)

async def prejudge_reminder(day):
    """判定の1時間前に、まだ間に合う未達を本人メンションでそっと知らせる"""
    ch = await get_ch("kora")
    if not ch:
        return
    now = now_jst()
    d1, d2 = week_range(now)
    is_sunday = now.weekday() == 6
    async with db.execute("SELECT * FROM users") as c:
        all_users = await c.fetchall()
    async with db.execute("SELECT DISTINCT user_id FROM custom_items") as c:
        custom_uids = {r["user_id"] for r in await c.fetchall()}
    async with db.execute("SELECT user_id FROM off_days WHERE day=?", (day,)) as c:
        off = {r["user_id"] for r in await c.fetchall()}
    lines = []
    for u in all_users:
        if (not has_any_setting(u) and u["id"] not in custom_uids) or u["id"] in off:
            continue
        if await all_items_skipped(u, day):
            continue
        misses = actionable_misses(await build_misses(u, day, d1, d2, is_sunday))
        if misses:
            lines.append((f"・<@{u['id']}>　" + "／".join(misses))[:180])
    if not lines:
        return
    await ch.send(f"⏳ **判定まであと1時間！**（{JUDGE_HOUR}:00 に判定）いまからでも間に合う最低限：\n"
                  + "\n".join(lines[:25])
                  + "\n-# 🌙 を押してから寝ると、朝☀️を忘れても最初の活動で自動記録されます"
                  + hitokoto_suffix())

async def materialize_weekly_off(day):
    """毎週の定休日（users.off_weekdays）をその日の off_days に実体化する（毎日1回）。
    判定・ストリーク・事前/起床リマインドは off_days を見るので、これだけで全部が休みになる"""
    dc = DAY_CHARS[date.fromisoformat(day).weekday()]
    async with db.execute("SELECT id FROM users WHERE off_weekdays LIKE ?", (f"%{dc}%",)) as c:
        rows = await c.fetchall()
    n = 0
    for r in rows:
        cur = await db.execute("INSERT OR IGNORE INTO off_days(day,user_id,reason) VALUES(?,?,'定休')", (day, r["id"]))
        n += cur.rowcount
    await db.commit()
    return n

@tasks.loop(minutes=1)
async def judge_loop():
    now = now_jst()
    day = day_str(now)
    try:
        if await meta_get("weekly_off_day") != day:
            await meta_set("weekly_off_day", day)
            await materialize_weekly_off(day)
    except Exception as e:
        print(f"定休日エラー: {e!r}", flush=True)
    try:
        await wake_remind_check(now)   # ⏰起床リマインド（オプトインの人だけ）
    except Exception as e:
        print(f"起床リマインドエラー: {e!r}", flush=True)
    try:
        await ops_daily(now)   # 🔒 yt-dlpの週1自動更新・DBの外部バックアップ
    except Exception as e:
        await bot_alert(f"⚠️ 定期メンテでエラー：{e!r}"[:500], key="ops_err", cooldown_h=24)
    try:   # 🌙 24時をまわっても作業部屋に人がいたら、VCチャットからそっと声かけ（1日1回）
        if now.hour == 0 and await meta_get("oyasumi_nudge_day") != day:
            await meta_set("oyasumi_nudge_day", day)
            wch = await _work_vc_channel()
            if wch and any(not m.bot for m in wch.members):
                await wch.send("🌙 そろそろ寝てもいいんじゃない？")
    except Exception as e:
        print(f"おやすみ声かけエラー: {e!r}", flush=True)
    try:   # 📖ミニ勉強会：開始の合図と、終わりの参加発表（来なかった夜は何も言わない）
        # サーバーイベントはユーザーが「毎日繰り返し」で手動作成（Botは触らない。クライアントUIにはrecurrence設定がある）
        if BENKYOKAI_START >= 0:
            if now.hour == BENKYOKAI_START and await meta_get("benkyokai_open_day") != day:
                await meta_set("benkyokai_open_day", day)
                ch = await get_ch("worklog")
                if ch:
                    await ch.send(f"📖 **ミニ勉強会の時間**（{BENKYOKAI_START}:00〜{BENKYOKAI_END}:00）！🔊作業部屋に入るだけで参加となります。")
                if BENKYOKAI_BGM and bgm_files() and not _player.get("vc"):
                    try:
                        await start_bgm(auto=True)   # 勉強会BGMを自動で流す（23時に自動停止）
                    except Exception as e:
                        print(f"勉強会BGMエラー: {e!r}", flush=True)
            if now.hour >= BENKYOKAI_END and await meta_get("benkyokai_recap_day") != day:
                await meta_set("benkyokai_recap_day", day)
                await benkyokai_recap(day, now)
                if _player.get("auto"):   # 自動で流していた勉強会BGMは終了（手動の分には触らない）
                    await stop_player()
    except Exception as e:
        print(f"勉強会エラー: {e!r}", flush=True)
    try:
        await update_work_vc_status(now)   # VCサブタイトル（勉強会の開始/終了の切替もここで拾う）
    except Exception as e:
        print(f"VCステータスエラー: {e!r}", flush=True)
    kh = JUDGE_HOUR - KAJI_REMIND_BEFORE_H
    if KAJI_REMIND_BEFORE_H > 0 and kh >= 0 and now.hour == kh and await meta_get("last_kaji_remind_day") != day:
        await meta_set("last_kaji_remind_day", day)
        try:
            await kaji_remind(day)   # 🧹 n日に1回の家事：今日やる日なのにまだの人へ
        except Exception as e:
            print(f"家事リマインドエラー: {e!r}", flush=True)
    if JUDGE_HOUR >= 1 and now.hour == JUDGE_HOUR - 1 and await meta_get("last_prejudge_day") != day:
        await meta_set("last_prejudge_day", day)   # 先に記録して二重投稿を防ぐ
        try:
            await prejudge_reminder(day)
        except Exception as e:
            print(f"事前リマインドエラー: {e!r}", flush=True)
    if now.hour < JUDGE_HOUR:
        return
    if await meta_get("last_judge_day") == day:
        return
    await meta_set("last_judge_day", day)  # 先に記録して二重実行を防ぐ
    for g in bot.guilds:
        try:
            await judge(g)
            if now.weekday() == 6:
                await weekly_summary(g)
                try:
                    await kadai_forecast(now)   # 来週の課題予報を #課題📚 へ
                except Exception as e:
                    print(f"課題予報エラー: {e!r}", flush=True)
                try:
                    await post_heavy_rotation(now)   # 🎧 今週のヘビロテを #作業ログ🖥 へ
                except Exception as e:
                    print(f"ヘビロテエラー: {e!r}", flush=True)
            if (now + timedelta(days=1)).month != now.month:
                await monthly_summary(g)
                try:
                    await post_monthly_meal_album(now)   # 今月のベスト飯アルバムを #ごはん🍚 へ
                except Exception as e:
                    print(f"ごはんアルバムエラー: {e!r}", flush=True)
                try:
                    await post_monthly_iyashi_album(now)   # 🐾 今月のいやしアルバムを #どうぶつ🐄 へ
                except Exception as e:
                    print(f"いやしアルバムエラー: {e!r}", flush=True)
                try:   # 📖 個人の月間しおりは本人だけが受け取る形（数字を晒さない）。ここでは案内だけ
                    tch = await get_ch("tsushinbo")
                    if tch:
                        await tch.send(f"📖 **{now.month}月の「生活のしおり」ができました！**\n"
                                       "`/shiori` で自分だけの1枚（平均起床・睡眠・達成カレンダー）を受け取れます。SNSに貼るのも歓迎🌅")
                except Exception as e:
                    print(f"しおり案内エラー: {e!r}", flush=True)
        except Exception as e:
            print(f"judge error: {e!r}", flush=True)
    try:
        await backup_db()
    except Exception as e:
        print(f"バックアップ失敗: {e!r}", flush=True)

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
    async with db.execute("SELECT id FROM users WHERE radio_notify=1 AND id NOT IN "
                          "(SELECT user_id FROM night_shifts WHERE day=?)", (day_str(now_jst()),)) as c:
        notify_ids = [r["id"] for r in await c.fetchall()]   # 夜勤明けの人は呼ばない
    mention = " ".join(f"<@{i}>" for i in notify_ids)
    if wake_ch:
        lead = "" if manual else "（1分後にスタート）"
        weather = await fetch_weather()
        await wake_ch.send(f"🏃 **{RADIO_TIME} ラジオ体操はじまるよ！**{lead} {vc_ch.mention} に集合〜"
                           + (f"\n🌤 今日の天気：{weather}" if weather else "") + (f"\n{mention}" if mention else "")
                           + hitokoto_suffix())
    if not manual:
        await asyncio.sleep(60)
    if vc_ch.guild.voice_client:   # BGM等が流れていたらラジオ体操を優先して止める
        await stop_player()
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

# ------------------------------------------------------------
#  🎵 音楽プレイヤー（①作業用BGM=VMのmusic/を シャッフルループ ②/play=URL・検索語の再生）
#  1つのキューを共有：/playの曲が優先、キューが空でBGMモードなら自前音源を補充し続ける
# ------------------------------------------------------------
BGM_DIR = os.getenv("BGM_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "music"))
BENKYOKAI_BGM = os.getenv("BENKYOKAI_BGM", "1") == "1"   # 勉強会の時間に自動でBGMを流す
_player = {"vc": None, "queue": [], "bgm": False, "auto": False, "current": None,
           "volume": 1.0, "loop": "off", "cur_item": None}

def bgm_files():
    try:
        return sorted(f for f in os.listdir(BGM_DIR) if f.lower().endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac")))
    except Exception:
        return []

AMBIENT_DIR = os.path.join(BGM_DIR, "ambient")   # 環境音ループ素材（雨.mp3 焚き火.mp3 など）

def ambient_files():
    try:
        return sorted(f for f in os.listdir(AMBIENT_DIR) if f.lower().endswith((".mp3", ".m4a", ".ogg", ".wav", ".flac")))
    except Exception:
        return []

def ambient_ffmpeg_args(paths):
    """複数の環境音を無限ループで重ねるffmpeg引数 → (最後の入力=source, before_options, options)。
    追加入力はbefore側に置く（discord.pyは出力フォーマット指定をoptionsの前に挟むため、
    optionsに-iを入れると出力形式が壊れる）。パスにスペース不可。"""
    n = len(paths)
    if n == 1:
        return paths[0], "-stream_loop -1", ""
    before = " ".join(f"-stream_loop -1 -i {p}" for p in paths[:-1]) + " -stream_loop -1"
    labels = "".join(f"[{i}:a]" for i in range(n))
    opts = f"-filter_complex {labels}amix=inputs={n}:duration=longest:normalize=0[mix] -map [mix]"
    return paths[-1], before, opts

def looks_like_url(s):
    return bool(re.match(r"https?://", (s or "").strip()))

# YouTubeはデータセンターIPをbot扱いすることがある。TV/Androidクライアント偽装で回避を試み、
# それでもダメな場合は cookies.txt（ブラウザからエクスポートしてVMに置く）を使う
YTDLP_COOKIES = os.getenv("YTDLP_COOKIES", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"))

def _ytdlp_base_opts():
    # クライアントはyt-dlpの既定に任せる（PO Tokenプロバイダ＋Deno＋cookies.txtの3点セットで再生できる構成。
    # 詳細はVM側: bgutil-providerコンテナ(4416) / /usr/local/bin/deno / ~/seikatsu-bot/cookies.txt）
    o = {"quiet": True, "no_warnings": True}
    if os.path.exists(YTDLP_COOKIES):
        o["cookiefile"] = YTDLP_COOKIES
    return o

async def _ytdlp_resolve(query):
    """URLまたは検索語 → {url,title,duration,acodec,page,thumb} の辞書。失敗で None"""
    try:
        import yt_dlp
    except Exception:
        print("yt-dlp が入っていません（pip install -r requirements.txt）", flush=True)
        return None
    q = query.strip()
    if not looks_like_url(q):
        q = f"ytsearch1:{q}"
    # Opus音源を優先（パススルー再生＝再エンコードなしでCPU最小・音質劣化ゼロ）
    opts = {**_ytdlp_base_opts(), "format": "bestaudio[acodec=opus]/bestaudio/best", "noplaylist": True}

    def _ex():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(q, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return {"url": info["url"], "title": (info.get("title") or "？")[:80], "duration": info.get("duration"),
                    "acodec": info.get("acodec"), "page": info.get("webpage_url"), "thumb": info.get("thumbnail")}
    try:
        return await asyncio.to_thread(_ex)
    except Exception as e:
        print(f"yt-dlp失敗: {e!r}", flush=True)
        try:
            await report_yt_error(e)   # Cookie切れ・yt-dlp古い等なら #bot管理🔒 に直し方つきで通知（＋自動更新）
        except Exception:
            pass
        return None

PLAYLIST_MAX = 50   # プレイリストから一度に積む上限

def fmt_dur_sec(s):
    """秒数 → 36:59 / 1:02:03 形式"""
    s = int(s)
    h, r = divmod(s, 3600)
    m, sec = divmod(r, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"

async def _ytdlp_playlist(query):
    """プレイリストURLなら (タイトル, [(URL, 曲名), ...], meta{dur,thumb,page}) を軽量抽出で返す。単曲・失敗は None"""
    try:
        import yt_dlp
    except Exception:
        return None
    q = query.strip()
    if not looks_like_url(q):
        return None
    opts = {**_ytdlp_base_opts(), "extract_flat": "in_playlist", "noplaylist": False}

    def _ex():
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(q, download=False)
            ents = list(info.get("entries") or [])
            if len(ents) < 2:
                return None
            out = []
            for e in ents[:PLAYLIST_MAX]:
                u = e.get("url") or e.get("webpage_url")
                if u and not looks_like_url(u):
                    u = f"https://www.youtube.com/watch?v={u}"
                if u:
                    out.append((u, (e.get("title") or "？")[:80]))
            if not out:
                return None
            total = sum(e.get("duration") or 0 for e in ents[:PLAYLIST_MAX])
            ths = info.get("thumbnails") or (ents[0].get("thumbnails") if ents else None) or []
            meta = {"dur": total or None, "thumb": ths[-1].get("url") if ths else None,
                    "page": info.get("webpage_url") or q}
            return ((info.get("title") or "プレイリスト")[:60], out, meta)
    try:
        return await asyncio.to_thread(_ex)
    except Exception as e:
        print(f"yt-dlpプレイリスト失敗: {e!r}", flush=True)
        return None

# ------------------------------------------------------------
#  🟢 Spotify連携：音声はDRMで取れないので「曲名一覧」だけAPIでもらい、
#  1曲ずつYouTube検索（既存のyt_lazy）で再生する＝外部音楽Botと同じ方式。
#  要 .env: SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET（developer.spotify.comで無料発行）
# ------------------------------------------------------------
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID", "")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET", "")
_spotify_token = {"val": None, "exp": 0.0}

def spotify_url_parts(s):
    """SpotifyのURL → ('playlist'|'album'|'track', id)。違うURLなら None"""
    m = re.match(r"https?://open\.spotify\.com/(?:intl-[a-z-]+/)?(playlist|album|track)/([A-Za-z0-9]+)", (s or "").strip())
    return (m.group(1), m.group(2)) if m else None

async def _spotify_token_get():
    import time as _t
    import base64
    import aiohttp
    if _spotify_token["val"] and _t.time() < _spotify_token["exp"] - 60:
        return _spotify_token["val"]
    if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
        return None
    auth = base64.b64encode(f"{SPOTIFY_CLIENT_ID}:{SPOTIFY_CLIENT_SECRET}".encode()).decode()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.post("https://accounts.spotify.com/api/token", data={"grant_type": "client_credentials"},
                                 headers={"Authorization": f"Basic {auth}"}) as r:
                if r.status != 200:
                    print(f"Spotifyトークン失敗: HTTP {r.status}", flush=True)
                    return None
                d = await r.json()
    except Exception as e:
        print(f"Spotifyトークン失敗: {e!r}", flush=True)
        return None
    _spotify_token["val"] = d.get("access_token")
    _spotify_token["exp"] = _t.time() + int(d.get("expires_in", 3600))
    return _spotify_token["val"]

def _find_key_deep(o, key, depth=0):
    """ネストしたdict/listから最初に見つかったkeyの値を返す（埋め込みJSON用）"""
    if depth > 8:
        return None
    if isinstance(o, dict):
        if key in o:
            return o[key]
        for v in o.values():
            r = _find_key_deep(v, key, depth + 1)
            if r is not None:
                return r
    elif isinstance(o, list):
        for v in o:
            r = _find_key_deep(v, key, depth + 1)
            if r is not None:
                return r
    return None

async def _spotify_embed_tracks(sid):
    """プレイリストの曲一覧を埋め込みページ(__NEXT_DATA__)から取得。
    ※2025年からSpotifyは開発モードアプリにプレイリストの曲一覧APIを開放していない（403）ため、
    こちらが本命ルート。ページ構造変更で壊れたらAPIフォールバックに落ちる"""
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15),
                                         headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}) as sess:
            async with sess.get(f"https://open.spotify.com/embed/playlist/{sid}") as r:
                if r.status != 200:
                    print(f"Spotify埋め込み {r.status}", flush=True)
                    return None
                html = await r.text()
    except Exception as e:
        print(f"Spotify埋め込み失敗: {e!r}", flush=True)
        return None
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
    if not m:
        print("Spotify埋め込み: __NEXT_DATA__が見つからない（ページ構造変更？）", flush=True)
        await bot_alert("🟢 **Spotifyのプレイリストが読めなくなりました**（埋め込みページの仕組みが変わった可能性）。"
                        "単曲・アルバムURLは引き続き使えます。直すには `_spotify_embed_tracks` の読み取り部分の修正が必要です"
                        "（リポジトリの 保守マニュアル.md 参照）。", key="sp_embed", cooldown_h=48)
        return None
    try:
        data = json.loads(m.group(1))
        tl = _find_key_deep(data, "trackList")
    except Exception:
        return None
    out, total = [], 0
    for t in tl or []:
        name, artist = (t.get("title") or "").strip(), (t.get("subtitle") or "").strip()
        if name:
            out.append((f"{artist} {name}".strip(), (f"{name}／{artist}" if artist else name)[:80]))
            d = t.get("duration") or 0
            total += d / 1000 if d > 10000 else d   # 埋め込みはミリ秒
    thumb = None
    cover = _find_key_deep(data, "coverArt")
    if isinstance(cover, dict) and (cover.get("sources") or []):
        thumb = cover["sources"][-1].get("url")
    meta = {"dur": int(total) or None, "thumb": thumb, "page": f"https://open.spotify.com/playlist/{sid}"}
    return (out[:PLAYLIST_MAX], meta) if out else None

def _sp_entry(t):
    """Spotifyのtrackオブジェクト → (YouTube検索語, 表示名)"""
    arts = "、".join(a["name"] for a in (t.get("artists") or [])[:2])
    return (f"{arts} {t['name']}", f"{t['name']}／{arts}"[:80])

async def spotify_tracks(kind, sid):
    """playlist/album/track → (タイトル, [(検索語, 表示名), ...])。失敗は None"""
    import aiohttp
    tok = await _spotify_token_get()
    if not tok:
        return None
    H = {"Authorization": f"Bearer {tok}"}
    out = []
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as sess:
            if kind == "track":
                async with sess.get(f"https://api.spotify.com/v1/tracks/{sid}?market=JP", headers=H) as r:
                    if r.status != 200:
                        print(f"Spotify API(track) {r.status}: {(await r.text())[:200]}", flush=True)
                        return None
                    t = await r.json()
                imgs = ((t.get("album") or {}).get("images")) or []
                meta = {"dur": int((t.get("duration_ms") or 0) / 1000) or None,
                        "thumb": imgs[0].get("url") if imgs else None,
                        "page": f"https://open.spotify.com/track/{sid}"}
                return ((t.get("name") or "曲")[:60], [_sp_entry(t)], meta)
            if kind == "album":
                async with sess.get(f"https://api.spotify.com/v1/albums/{sid}?market=JP", headers=H) as r:
                    if r.status != 200:
                        print(f"Spotify API(album) {r.status}: {(await r.text())[:200]}", flush=True)
                        return None
                    d = await r.json()
                total = 0
                for t in ((d.get("tracks") or {}).get("items") or [])[:PLAYLIST_MAX]:
                    if t.get("name"):
                        out.append(_sp_entry(t))
                        total += (t.get("duration_ms") or 0) / 1000
                imgs = d.get("images") or []
                meta = {"dur": int(total) or None, "thumb": imgs[0].get("url") if imgs else None,
                        "page": f"https://open.spotify.com/album/{sid}"}
                return ((d.get("name") or "アルバム")[:60], out, meta) if out else None
            # playlist（※Spotify公式編集のプレイリストは2024年からAPI非公開＝404になる。ユーザー作成の公開プレイリストはOK）
            async with sess.get(f"https://api.spotify.com/v1/playlists/{sid}?market=JP&fields=name", headers=H) as r:
                if r.status != 200:
                    # 403「Active premium subscription required」=アプリ所有者が無料アカウント（2025年からPremium必須）
                    body = (await r.text())[:200]
                    print(f"Spotify API {r.status}: {body}", flush=True)
                    if "premium" in body.lower():
                        await bot_alert("🟢 **Spotify連携が止まりました**：アプリ所有者のSpotify Premiumが切れている可能性"
                                        "（Premium必須の仕様）。再加入すると数時間で復活します。", key="sp_premium", cooldown_h=48)
                    return None
                title = ((await r.json()).get("name") or "プレイリスト")[:60]
            emb = await _spotify_embed_tracks(sid)   # 曲一覧は埋め込みページが本命（APIは開発モードアプリに403）
            if emb:
                return (title, emb[0], emb[1])
            url = (f"https://api.spotify.com/v1/playlists/{sid}/tracks?market=JP&limit=100"
                   "&fields=items(track(name,artists(name))),next")
            while url and len(out) < PLAYLIST_MAX:
                async with sess.get(url, headers=H) as r:
                    if r.status != 200:
                        print(f"Spotify API(tracks) {r.status}: {(await r.text())[:200]}", flush=True)
                        break
                    d = await r.json()
                for it in d.get("items") or []:
                    t = it.get("track") or {}
                    if t.get("name"):
                        out.append(_sp_entry(t))
                url = d.get("next")
            return (title, out[:PLAYLIST_MAX], {"page": f"https://open.spotify.com/playlist/{sid}"}) if out else None
    except Exception as e:
        print(f"Spotify取得失敗: {e!r}", flush=True)
        return None

async def _work_vc_channel():
    wid = await meta_get("ch_workvc")
    return bot.get_channel(int(wid)) if wid else None

async def ensure_player_vc(prefer=None):
    """再生用のVC接続（既存接続は流用・別チャンネルなら移動）"""
    ch = prefer or await _work_vc_channel()
    if not ch:
        return None
    vc = ch.guild.voice_client
    if vc and vc.is_connected():
        if vc.channel.id != ch.id:
            try:
                await vc.move_to(ch)
            except Exception:
                pass
        return vc
    try:
        return await ch.connect(timeout=20, reconnect=False)
    except Exception as e:
        print(f"🎵 VC接続失敗: {e!r}", flush=True)
        return None

def _after_track(err):
    if err:
        print(f"🎵 再生エラー: {err!r}", flush=True)
    try:
        asyncio.run_coroutine_threadsafe(_play_next(), bot.loop)
    except Exception:
        pass

async def _announce_now_playing():
    """VCの内蔵チャットに再生中の曲を流す（サブタイトル表示と併用・みんなに見える）"""
    vc, cur = _player.get("vc"), _player.get("current")
    if not vc or not cur:
        return
    if _player.get("announced") == cur:   # 1曲リピート等で同じ曲が続くときは繰り返さない
        return
    _player["announced"] = cur
    it = _player.get("cur_item") or {}
    note = (it.get("note") or "")[:200]
    try:
        if it.get("type") in ("yt", "file"):   # 曲はサムネ・リンク付きのカードで（Jockie風）
            title = it.get("title") or os.path.splitext(os.path.basename(it.get("path", "")))[0] or cur
            e = discord.Embed(title=title[:256], url=it.get("page") or None, color=discord.Color.gold())
            e.set_author(name="♪ 再生中")
            if it.get("thumb"):
                e.set_thumbnail(url=it["thumb"])
            line2 = []
            if it.get("duration"):
                line2.append(fmt_dur_sec(it["duration"]))
            if it.get("by"):
                line2.append(f"リクエスト：{it['by']}")
            # 紹介文はフッター（小さいグレー字）＝控えめに
            foot = (_oneline(note) + "\n" if note else "") + "　".join(line2)
            if foot.strip():
                e.set_footer(text=foot[:2000])
            await vc.channel.send(embed=e, silent=True)
        else:   # 環境音などはテキストのまま
            await vc.channel.send(cur, silent=True)
    except Exception as e:
        print(f"🎵 再生告知失敗: {e!r}", flush=True)

def _schedule_leave_check():
    """無人かも？のとき、即退出せず60秒後に再確認してから退出する
    （チャンネル移動直後の人数情報ずれや、聴いている人の一瞬の出入りで切れないように）"""
    if _player.get("leaving"):
        return
    _player["leaving"] = True

    async def _t():
        try:
            await asyncio.sleep(60)
            vc = _player.get("vc")
            if vc and vc.is_connected() and not any(not m.bot for m in vc.channel.members):
                print("🎵 無人が続いたのでVCから退出", flush=True)
                await stop_player()
        finally:
            _player["leaving"] = False
    asyncio.create_task(_t())

async def _play_next():
    vc = _player.get("vc")
    if not vc or not vc.is_connected():
        return
    if not any(not m.bot for m in vc.channel.members):   # 無人っぽい→60秒猶予してから撤収（再生は続ける）
        _schedule_leave_check()
    item = None
    skip_once = _player.pop("skip_once", False)
    cur = _player.get("cur_item")
    if _player.get("loop") == "one" and cur and not skip_once:
        item = cur   # 1曲リピート（/skipで抜けられる）
    else:
        if _player.get("loop") == "all" and cur:
            _player["queue"].append(cur)   # キュー全体リピート：終わった曲を最後尾へ
        if _player["queue"]:
            item = _player["queue"].pop(0)
        elif _player["bgm"]:
            fs = bgm_files()
            if fs:
                item = {"type": "file", "path": os.path.join(BGM_DIR, random.choice(fs))}
    if not item:
        _player["current"] = None   # キューが尽きてもVCに居残る（解読キャッシュが温かいまま次の/playが速い）。退出は/stopか無人化で
        _player["cur_item"] = None
        _player["announced"] = None
        try:
            asyncio.create_task(update_work_vc_status())
        except Exception:
            pass
        return
    try:
        by = item.get("by")
        vol = _player.get("volume", 1.0)
        if item["type"] == "ambient":   # 🌿 環境音ミキサー（無限ループ・/skipか/stopで終了）
            src_path, before, opts = ambient_ffmpeg_args(item["paths"])
            pcm = discord.FFmpegPCMAudio(src_path, executable=FFMPEG_PATH, before_options=before, options=opts or None)
            _player["current"] = "🌿 環境音：" + "＋".join(item["names"])
            _player["cur_item"] = item
            vc.play(discord.PCMVolumeTransformer(pcm, volume=vol), after=_after_track,
                    bitrate=256, expected_packet_loss=0.05)
            asyncio.create_task(update_work_vc_status())
            asyncio.create_task(_announce_now_playing())
            return
        if item["type"] == "file":
            target, before = item["path"], None
            _player["current"] = f"🎵 {os.path.splitext(os.path.basename(item['path']))[0]}"
        else:
            if item["type"] == "yt_lazy":   # プレイリストの曲：再生直前にストリームURLを取得
                r = await _ytdlp_resolve(item["query"])
                if not r:
                    print(f"🎵 取得失敗につきスキップ: {item.get('title')}", flush=True)
                    return await _play_next()
                item = {**r, "type": "yt", "title": item.get("title") or r["title"], "by": by, "note": item.get("note")}
            target, before = item["url"], "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
            _player["current"] = f"▶ {item['title']}" + (f"（{by}）" if by else "")
        _player["cur_item"] = item
        if abs(vol - 1.0) < 0.01:
            # 音量100%ならOpusパススルー：元がOpus（YouTube音声）なら再エンコードなしで転送＝CPU激減→速度ブレ対策
            ac = item.get("acodec")
            if item["type"] != "file" and ac:
                # yt-dlpが教えてくれたコーデックをそのまま使う＝from_probeの「下見ffmpeg」1本
                # （プロセス起動＋ストリーム先読みのネットワーク往復）を省いて出だしを速くする
                if ac in ("opus", "libopus"):
                    src = discord.FFmpegOpusAudio(target, executable=FFMPEG_PATH, before_options=before, codec="opus")
                else:
                    src = discord.FFmpegOpusAudio(target, executable=FFMPEG_PATH, before_options=before, bitrate=256)
            else:   # ローカルファイルやコーデック不明は従来どおり下見してから
                src = await discord.FFmpegOpusAudio.from_probe(target, executable=FFMPEG_PATH, before_options=before, method="fallback")
        else:
            pcm = discord.FFmpegPCMAudio(target, executable=FFMPEG_PATH, before_options=before)
            src = discord.PCMVolumeTransformer(pcm, volume=vol)
        # 追加パラメータはPCM経路（音量≠100%）の内部エンコーダ向け高音質設定。Opusパススルー時は無視される
        vc.play(src, after=_after_track, bitrate=256, signal_type="music", expected_packet_loss=0.05)
        asyncio.create_task(_prefetch_next())   # 次の曲を再生中に裏で解決（曲間のラグをなくす）
        asyncio.create_task(update_work_vc_status())   # VCサブタイトルに再生中の曲を出す
        asyncio.create_task(_announce_now_playing())   # VC内蔵チャットにも流す
        asyncio.create_task(_maybe_auto_karaoke())     # 同期歌詞がある曲は自動で歌詞同期（既定ON）
    except Exception as e:
        print(f"🎵 play失敗: {e!r}", flush=True)
        _player["current"] = None

async def _prefetch_next():
    """キュー先頭が未解決（yt_lazy）なら、再生中のうちにストリームURLを取得しておく"""
    try:
        await asyncio.sleep(3)   # 再生バッファが安定してから裏作業を始める（出だしの速度ブレ対策）
        q = _player["queue"]
        if q and q[0].get("type") == "yt_lazy":
            r = await _ytdlp_resolve(q[0]["query"])
            if r and q and q[0].get("type") == "yt_lazy":   # 解決中にキューが動いていないか確認
                q[0] = {**r, "type": "yt", "title": q[0].get("title") or r["title"], "by": q[0].get("by"), "note": q[0].get("note")}
    except Exception as e:
        print(f"🎵 先読みエラー: {e!r}", flush=True)

async def start_bgm(auto=False):
    if not bgm_files():
        return "❌ 音源がありません（音声ファイルを投稿して右クリック→アプリ→「BGMに追加」で入れられます）"
    vc = await ensure_player_vc()
    if not vc:
        return "❌ 作業部屋VCに接続できませんでした"
    _player.update(vc=vc, bgm=True, auto=auto)
    if not vc.is_playing():
        await _play_next()
    return f"🎵 作業用BGMをはじめました（{len(bgm_files())}曲シャッフルループ・`/bgm stop` で停止）"

_warm_ts = {"t": 0.0}

async def warmup_player(reason=""):
    """PO Token発行＋n-sig解読を事前に走らせてキャッシュを温める（最初の/playのラグ対策・1時間に1回まで）"""
    import time as _t
    if _t.time() - _warm_ts["t"] < 3600:
        return
    _warm_ts["t"] = _t.time()
    r = await _ytdlp_resolve("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    print(f"🎵 プレイヤーのウォームアップ{'OK' if r else '失敗'}（{reason}）", flush=True)

async def stop_player():
    _player["queue"].clear()
    _player.update(bgm=False, auto=False, current=None, loop="off", cur_item=None, announced=None, karaoke=None)
    vc = _player.get("vc")
    _player["vc"] = None
    if vc and vc.is_connected():
        try:
            vc.stop()
            await vc.disconnect()
        except Exception:
            pass

bgm_grp = app_commands.Group(name="bgm", description="作業用BGM（自前音源のシャッフルループ・混雑知らず）")

@bgm_grp.command(name="start", description="作業部屋で作業用BGMを流す")
async def bgm_start(interaction):
    await interaction.response.defer()
    await interaction.followup.send(await start_bgm())

@bgm_grp.command(name="stop", description="BGM・再生をぜんぶ止めてVCから退出")
async def bgm_stop(interaction):
    await stop_player()
    await interaction.response.send_message("⏹ 止めました。")

@bgm_grp.command(name="list", description="入っているBGM音源の一覧")
async def bgm_list(interaction):
    fs = bgm_files()
    if not fs:
        await interaction.response.send_message("音源がまだありません。音声ファイルを投稿して右クリック→アプリ→「BGMに追加」で入れられるよ。", ephemeral=True)
        return
    await interaction.response.send_message("🎵 BGM音源：\n" + "\n".join(f"・{os.path.splitext(f)[0]}" for f in fs[:30]), ephemeral=True)

BGM_EXTS = (".mp3", ".m4a", ".ogg", ".wav", ".flac")
BGM_MAX_MB = 40

def _safe_bgm_name(name):
    """添付ファイル名を保存用に無害化（パス区切り除去・先頭ドット除去・80字まで）"""
    base = os.path.basename(str(name or "")).replace("\\", "").replace("/", "").strip()
    return base.lstrip(".")[:80]

@bot.tree.context_menu(name="BGMに追加")
async def add_bgm_ctx(interaction: discord.Interaction, message: discord.Message):
    """音声ファイル付きの投稿を右クリック→アプリ→BGMに追加 で、だれでもBGM音源を増やせる"""
    atts = [a for a in message.attachments if a.filename.lower().endswith(BGM_EXTS)]
    if not atts:
        await interaction.response.send_message("⚠️ 音声ファイル（mp3/m4a/ogg/wav/flac）が付いた投稿で使ってね。", ephemeral=True)
        return
    await interaction.response.defer()
    added, skipped = [], []
    for a in atts:
        if a.size > BGM_MAX_MB * 1024 * 1024:
            skipped.append(f"{a.filename}（{BGM_MAX_MB}MB超え）")
            continue
        name = _safe_bgm_name(a.filename)
        if not name:
            skipped.append(a.filename)
            continue
        stem, ext = os.path.splitext(name)
        path, i = os.path.join(BGM_DIR, name), 2
        while os.path.exists(path):   # 同名は(2)(3)…を付けて共存
            path = os.path.join(BGM_DIR, f"{stem}({i}){ext}")
            i += 1
        try:
            os.makedirs(BGM_DIR, exist_ok=True)
            await a.save(path)
            added.append(os.path.splitext(os.path.basename(path))[0])
        except Exception as e:
            print(f"🎵 BGM追加失敗: {e!r}", flush=True)
            skipped.append(a.filename)
    msg = ""
    if added:
        msg = "🎵 BGMに追加しました：" + "、".join(f"「{t}」" for t in added) + f"（ぜんぶで{len(bgm_files())}曲）"
    if skipped:
        msg += ("\n" if msg else "") + "⚠️ 追加できなかった：" + "、".join(skipped)
    await interaction.followup.send(msg or "⚠️ 追加できませんでした。")

class BgmRemoveSelect(discord.ui.Select):
    def __init__(self):
        fs = bgm_files()[:25]
        super().__init__(placeholder="削除する曲を選ぶ", min_values=1, max_values=max(len(fs), 1),
                         options=[discord.SelectOption(label=os.path.splitext(f)[0][:100], value=f) for f in fs])

    async def callback(self, interaction):
        done = []
        for v in self.values:
            try:
                os.remove(os.path.join(BGM_DIR, os.path.basename(v)))
                done.append(os.path.splitext(v)[0])
            except Exception as e:
                print(f"🎵 BGM削除失敗: {e!r}", flush=True)
        await interaction.response.edit_message(
            content=("🗑 削除しました：" + "、".join(f"「{t}」" for t in done)) if done else "⚠️ 削除できませんでした。", view=None)

@bgm_grp.command(name="remove", description="BGM音源を削除（選択式）")
async def bgm_remove(interaction):
    if not bgm_files():
        await interaction.response.send_message("音源がまだありません。", ephemeral=True)
        return
    v = discord.ui.View(timeout=300)
    v.add_item(BgmRemoveSelect())
    await interaction.response.send_message("削除する曲を選んでね（先頭25曲まで表示）", view=v, ephemeral=True)

bot.tree.add_command(bgm_grp)

@bot.tree.command(name="play", description="YouTube・Spotify等のURL・プレイリスト・検索語をVCで再生（キューに追加）")
@app_commands.describe(kyoku="URL／プレイリストURL（Spotify可）／検索語（例 lofi hiphop）")
async def play_command(interaction, kyoku: str):
    await interaction.response.defer()
    await do_play(interaction, kyoku)

async def do_play(interaction, kyoku, eph=False):
    """/playとパネル🎶の共通処理（呼び出し側でdefer済みであること）"""
    pl = None
    sp = spotify_url_parts(kyoku)
    if sp:   # 🟢 Spotify：曲一覧を取得して1曲ずつ再生キューへ
        if not (SPOTIFY_CLIENT_ID and SPOTIFY_CLIENT_SECRET):
            await interaction.followup.send("⚠️ Spotify連携が未設定です（VMの.envに SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET が必要）", ephemeral=eph)
            return
        pl = await spotify_tracks(*sp)
        if not pl:
            await interaction.followup.send("⚠️ Spotifyから曲一覧を取得できませんでした。\n"
                                            "-# 非公開プレイリストと、Spotify公式が編集したプレイリスト（Today's Top Hits等）は取れません。自分や他ユーザーが作った公開プレイリストならOK", ephemeral=eph)
            return
    # プレイリストっぽいURLのときだけプレイリスト抽出を試す（単曲URLの余計な1往復を省いて高速化）
    if not pl and looks_like_url(kyoku) and re.search(r"list=|/playlist|/sets/|/album", kyoku):
        pl = await _ytdlp_playlist(kyoku)
    single = None
    if not pl:
        single = await _ytdlp_resolve(kyoku)
        if not single:
            await interaction.followup.send("⚠️ 再生情報を取得できませんでした（URLを確認。直らなければ yt-dlp の更新が必要かも）", ephemeral=eph)
            return
    prefer = interaction.user.voice.channel if getattr(interaction.user, "voice", None) and interaction.user.voice else None
    vc = await ensure_player_vc(prefer)
    if not vc:
        await interaction.followup.send("❌ VCに接続できませんでした。どこかのVCに入ってから実行してね。", ephemeral=eph)
        return
    _player["vc"] = vc
    by = interaction.user.display_name
    ts0 = int(now_jst().timestamp())
    if pl:
        pl_title, tracks = pl[0], pl[1]
        meta = pl[2] if len(pl) > 2 else {}
        for u, t in tracks:
            _player["queue"].append({"type": "yt_lazy", "query": u, "title": t, "by": by})
            await db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES(?,?,?,?)",
                             (str(interaction.user.id), t, u, ts0))
        await db.commit()
        emb = discord.Embed(title="📃 プレイリストを追加" if len(tracks) > 1 else "⏭ キューに追加",
                            color=discord.Color.gold())
        name_v = f"[{pl_title}]({meta['page']})" if meta.get("page") else pl_title
        emb.add_field(name="プレイリスト" if len(tracks) > 1 else "曲", value=name_v, inline=True)
        emb.add_field(name="曲数", value=str(len(tracks)), inline=True)
        if meta.get("dur"):
            emb.add_field(name="合計", value=fmt_dur_sec(meta["dur"]), inline=True)
        if meta.get("thumb"):
            emb.set_thumbnail(url=meta["thumb"])
        note = f"-# 上限{PLAYLIST_MAX}曲で切りました" if len(tracks) >= PLAYLIST_MAX else ""
    else:
        _player["queue"].append({**single, "type": "yt", "by": by})
        await db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES(?,?,?,?)",
                         (str(interaction.user.id), single["title"], kyoku.strip() if looks_like_url(kyoku) else "", ts0))
        await db.commit()
        emb = discord.Embed(title="⏭ キューに追加", color=discord.Color.gold(),
                            description=f"[{single['title']}]({single['page']})" if single.get("page") else f"**{single['title']}**")
        if single.get("duration"):
            emb.add_field(name="長さ", value=fmt_dur_sec(single["duration"]), inline=True)
        emb.add_field(name="待ち", value=f"{len(_player['queue'])} 曲", inline=True)
        if single.get("thumb"):
            emb.set_thumbnail(url=single["thumb"])
        note = ""
    off = _bgm_mode_off()   # リクエストが来たらBGMモードは終了（終わったあと勝手にBGMへ戻らない）
    if off:
        note += ("\n" if note else "") + off.strip("\n")
    if not vc.is_playing():
        await _play_next()
        emb.set_footer(text=f"▶ 再生開始：{(_player.get('current') or '').lstrip('▶🎵 ')}"[:200])
    await interaction.followup.send(content=note or None, embed=emb, ephemeral=eph)

def queue_text():
    """いまの曲とキューの表示テキスト。何もなければ None"""
    cur, q = _player.get("current"), _player["queue"]
    if not cur and not q:
        return None
    loop_note = {"one": "（🔂 1曲リピート中）", "all": "（🔁 キューリピート中）"}.get(_player.get("loop"), "")
    lines = ([f"🎶 いま：{cur}{loop_note}"] if cur else []) + \
            [f"{i}. {it.get('title') or '？'}" + (f"（{it['by']}）" if it.get("by") else "") for i, it in enumerate(q[:10], 1)]
    if len(q) > 10:
        lines.append(f"…ほか {len(q) - 10} 曲")
    if _player.get("bgm"):
        lines.append("-# キューが空になったら作業用BGMに戻ります")
    return "\n".join(lines)

@bot.tree.command(name="queue", description="再生キューを見る")
async def queue_command(interaction):
    t = queue_text()
    await interaction.response.send_message(t or "キューは空です。`/play` で追加してね。", ephemeral=True)

class AmbientSelect(discord.ui.Select):
    def __init__(self, fs):
        opts = [discord.SelectOption(label=os.path.splitext(f)[0][:100], value=f) for f in fs[:25]]
        super().__init__(placeholder="🌿 重ねる環境音を選ぶ（複数OK・最大5つ）", options=opts,
                         min_values=1, max_values=min(5, len(opts)))

    async def callback(self, interaction):
        prefer = interaction.user.voice.channel if getattr(interaction.user, "voice", None) and interaction.user.voice else None
        vc = await ensure_player_vc(prefer)
        if not vc:
            await interaction.response.edit_message(content="❌ VCに接続できませんでした。どこかのVCに入ってから実行してね。", view=None)
            return
        _player["vc"] = vc
        names = [os.path.splitext(v)[0] for v in self.values]
        item = {"type": "ambient", "paths": [os.path.join(AMBIENT_DIR, v) for v in self.values], "names": names}
        _player["queue"].insert(0, item)
        if vc.is_playing() or vc.is_paused():
            _player["skip_once"] = True
            vc.stop()   # afterコールバックが先頭の環境音を再生する
        else:
            await _play_next()
        await interaction.response.edit_message(
            content="🌿 環境音をはじめました：**" + "＋".join(names) + "**\n-# 組み合わせを変えるならもう一度 `/ambient`、やめるなら `/skip` か `/stop`", view=None)

async def send_ambient_picker(interaction):
    """/ambientとパネル🌿の共通処理：環境音セレクトをephemeralで出す"""
    fs = ambient_files()
    if not fs:
        await interaction.response.send_message(
            "環境音の素材がまだありません。VMの `music/ambient/` に `雨.mp3` `焚き火.mp3` のようなループ音源を置いてね。", ephemeral=True)
        return
    view = discord.ui.View(timeout=300)
    view.add_item(AmbientSelect(fs))
    await interaction.response.send_message("🌿 **環境音ミキサー**（選んだ音を重ねて無限ループ）", view=view, ephemeral=True)

@bot.tree.command(name="ambient", description="環境音ミキサー（雨・焚き火・鳥などを重ねてループ再生）")
async def ambient_command(interaction):
    await send_ambient_picker(interaction)

@bot.tree.command(name="pause", description="再生を一時停止")
async def pause_command(interaction):
    vc = _player.get("vc")
    if not vc or not vc.is_playing():
        await interaction.response.send_message("いま何も流れていません。", ephemeral=True)
        return
    vc.pause()
    await interaction.response.send_message("⏸ 一時停止しました（`/resume` で再開）。")

@bot.tree.command(name="resume", description="一時停止中の再生を再開")
async def resume_command(interaction):
    vc = _player.get("vc")
    if not vc or not vc.is_paused():
        await interaction.response.send_message("一時停止中の曲はありません。", ephemeral=True)
        return
    vc.resume()
    await interaction.response.send_message("▶ 再開しました。")

@bot.tree.command(name="volume", description="音量を変える（10〜200%）")
@app_commands.describe(percent="音量％（既定100）")
async def volume_command(interaction, percent: int):
    v = max(10, min(200, percent)) / 100
    _player["volume"] = v
    vc = _player.get("vc")
    live = bool(vc and vc.source and hasattr(vc.source, "volume"))
    if live:
        vc.source.volume = v   # いま流れている曲にも即反映
    note = "" if live or not (vc and vc.is_playing()) else "（いまの曲は軽量モードで再生中のため、次の曲から反映）"
    await interaction.response.send_message(f"🔊 音量を {int(v * 100)}% にしました。{note}"
                                            + ("\n-# 100%に戻すとCPUにやさしい軽量モードで再生します" if abs(v - 1.0) >= 0.01 else ""))

@bot.tree.command(name="loop", description="リピート再生の切り替え")
@app_commands.describe(mode="リピートの種類")
@app_commands.choices(mode=[app_commands.Choice(name="オフ", value="off"),
                            app_commands.Choice(name="🔂 いまの曲を1曲リピート", value="one"),
                            app_commands.Choice(name="🔁 キュー全体をリピート", value="all")])
async def loop_command(interaction, mode: app_commands.Choice[str]):
    _player["loop"] = mode.value
    await interaction.response.send_message(f"{'🔂' if mode.value == 'one' else '🔁' if mode.value == 'all' else '➡'} リピート：**{mode.name}**")

@bot.tree.command(name="shuffle", description="キューをシャッフル")
async def shuffle_command(interaction):
    if len(_player["queue"]) < 2:
        await interaction.response.send_message("シャッフルするほどキューがありません。", ephemeral=True)
        return
    random.shuffle(_player["queue"])
    await interaction.response.send_message(f"🔀 {len(_player['queue'])} 曲をシャッフルしました。")

@bot.tree.command(name="join", description="BotをVCに呼ぶ（待機・準備だけして再生はしない）")
async def join_command(interaction):
    prefer = interaction.user.voice.channel if getattr(interaction.user, "voice", None) and interaction.user.voice else None
    vc = await ensure_player_vc(prefer)
    if not vc:
        await interaction.response.send_message("❌ VCに接続できませんでした。どこかのVCに入ってから実行してね。", ephemeral=True)
        return
    _player["vc"] = vc
    asyncio.create_task(warmup_player("/join"))   # 待機中に解読キャッシュを温めておく
    await interaction.response.send_message(f"🎧 {vc.channel.mention} で待機します。`/play` でいつでもどうぞ（退出は `/stop`）")

@bot.tree.command(name="skip", description="いま流れている曲をスキップ")
async def skip_command(interaction):
    vc = _player.get("vc")
    if not vc or not vc.is_playing():
        await interaction.response.send_message("いま何も流れていません。", ephemeral=True)
        return
    await interaction.response.send_message("⏭ スキップ！" + (f"（次：{_player['queue'][0]['title']}）" if _player["queue"] else ""))
    vc.stop()   # afterコールバックが次の曲を再生する

@bot.tree.command(name="stop", description="再生をぜんぶ止めてVCから退出")
async def stop_command(interaction):
    await stop_player()
    await interaction.response.send_message("⏹ 停止しました。")

# ------------------------------------------------------------
#  📂 みんなのプレイリスト＆⭐おすすめ曲（サーバー内プリセット＝URLの自動入力）
# ------------------------------------------------------------
REC_MAX = 20    # おすすめ曲の1人あたり上限（自分の削除セレクト25枠に収まる範囲）
REC_PAGE = 25   # 一覧セレクトの1ページあたり（Discordの上限25択→ページ送りで全曲見せる）

def parse_track_lines(text, limit=PLAYLIST_MAX):
    """モーダルの複数行入力 → 曲リスト（1行1曲・箇条書き記号は剥がす）"""
    out = []
    for ln in (text or "").splitlines():
        t = ln.strip().lstrip("・-*").strip()
        if t:
            out.append(t)
    return out[:limit]

async def _bg_fill_titles(table, rows):
    """URLで登録された曲の表示名を裏でyt-dlp解決して整える（失敗しても放置）"""
    for rid, q in rows:
        try:
            r = await _ytdlp_resolve(q)
            if r:
                await db.execute(f"UPDATE {table} SET title=? WHERE id=?", (r["title"], rid))   # tableは内部定数のみ
                await db.commit()
        except Exception:
            pass

async def pl_add_tracks(pid, lines):
    ts0 = int(now_jst().timestamp())
    urls = []
    for q in lines:
        cur = await db.execute("INSERT INTO member_playlist_tracks(pl_id,query,title,ts) VALUES(?,?,?,?)",
                               (pid, q, q[:80], ts0))
        if looks_like_url(q):
            urls.append((cur.lastrowid, q))
    await db.commit()
    if urls:
        asyncio.create_task(_bg_fill_titles("member_playlist_tracks", urls))

async def pl_create(owner_id, name, lines):
    cur = await db.execute("INSERT INTO member_playlists(owner_id,name,created_at) VALUES(?,?,?)",
                           (str(owner_id), (name or "プレイリスト")[:40], int(now_jst().timestamp())))
    pid = cur.lastrowid
    await pl_add_tracks(pid, lines)
    return pid

async def pl_all():
    async with db.execute(
        "SELECT p.id, p.name, p.owner_id, COALESCE(u.name,'?') AS oname, COUNT(t.id) AS n "
        "FROM member_playlists p LEFT JOIN member_playlist_tracks t ON t.pl_id=p.id "
        "LEFT JOIN users u ON u.id=p.owner_id GROUP BY p.id ORDER BY p.id DESC LIMIT 25") as c:
        return await c.fetchall()

async def pl_tracks(pid):
    async with db.execute("SELECT id, query, title FROM member_playlist_tracks WHERE pl_id=? ORDER BY id LIMIT ?",
                          (pid, PLAYLIST_MAX)) as c:
        return await c.fetchall()

REC_ARTIST_TRACK_MAX = 25   # 1アーティストに登録できる曲数（削除セレクト25枠）

_RENAMABLE_TABLES = ("member_playlist_tracks", "member_rec_tracks", "member_recs")

class TrackRenameModal(discord.ui.Modal, title="✏️ 曲名を変更"):
    name_in = discord.ui.TextInput(label="新しい曲名", max_length=80)

    def __init__(self, table, row_id, current):
        super().__init__()
        assert table in _RENAMABLE_TABLES
        self.table, self.row_id = table, row_id
        self.name_in.default = (current or "")[:80]

    async def on_submit(self, interaction):
        t = str(self.name_in.value).strip()
        if not t:
            await interaction.response.send_message("⚠️ 曲名が空です。", ephemeral=True)
            return
        await db.execute(f"UPDATE {self.table} SET title=? WHERE id=?", (t[:80], self.row_id))   # tableは内部定数のみ
        await db.commit()
        await interaction.response.send_message(f"✏️ 曲名を「**{t}**」に変更しました。", ephemeral=True)

def _oneline(s):
    """紹介文を1行化（改行は／に）"""
    return "／".join(ln.strip() for ln in (s or "").splitlines() if ln.strip())

def sub_text(s):
    """薄小文字（サブテキスト）行として先頭に改行付きで返す"""
    return f"\n-# {s}"

async def rec_all():
    async with db.execute(
        "SELECT r.id, r.query, r.title, r.comment, r.user_id, r.kind, COALESCE(u.name,'?') AS uname, "
        "(SELECT COUNT(*) FROM member_rec_tracks t WHERE t.rec_id=r.id) AS tn "
        "FROM member_recs r LEFT JOIN users u ON u.id=r.user_id ORDER BY r.id DESC LIMIT 500") as c:
        return await c.fetchall()

async def rec_tracks(rec_id):
    async with db.execute("SELECT id, query, title, comment, added_by FROM member_rec_tracks WHERE rec_id=? ORDER BY id LIMIT ?",
                          (rec_id, REC_ARTIST_TRACK_MAX)) as c:
        return await c.fetchall()

def rec_label(r):
    """セレクト表示名（アーティストは🎤＋曲数付き）"""
    if r["kind"] == "artist":
        return f"🎤 {(r['title'] or r['query'])[:70]}（{r['uname']}・{r['tn']}曲）"[:100]
    return f"{(r['title'] or r['query'])[:80]}（{r['uname']}）"[:100]

async def rec_count(uid):
    async with db.execute("SELECT COUNT(*) AS n FROM member_recs WHERE user_id=?", (str(uid),)) as c:
        return (await c.fetchone())["n"]

def rec_head(rows, page=0):
    """⭐一覧のヘッダー文（曲数とページ表示つき）"""
    base = "⭐ **みんなのおすすめ**（紹介文つき・選ぶと流れます。🎤アーティストは登録曲をまとめて再生）"
    if not rows:
        return base + "\nまだありません。最初の1件をどうぞ！"
    pages = max(1, -(-len(rows) // REC_PAGE))
    if pages > 1:
        base += f"\n-# ぜんぶで{len(rows)}曲・{page + 1}/{pages}ページ（新しい順）"
    return base

async def enqueue_lazy(interaction, tracks):
    """[(query, title), ...] をキューに積み、止まっていれば再生開始。成功メッセージ or None（VC接続失敗）"""
    prefer = interaction.user.voice.channel if getattr(interaction.user, "voice", None) and interaction.user.voice else None
    vc = await ensure_player_vc(prefer)
    if not vc:
        return None
    _player["vc"] = vc
    by = interaction.user.display_name
    ts0 = int(now_jst().timestamp())
    for tr in tracks:
        q, t = tr[0], tr[1]
        note_txt = tr[2] if len(tr) > 2 else None   # ⭐おすすめの紹介文（再生告知に小さく添える）
        _player["queue"].append({"type": "yt_lazy", "query": q, "title": t, "by": by, "note": note_txt})
        await db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES(?,?,?,?)",
                         (str(interaction.user.id), t, q if looks_like_url(q) else "", ts0))
    await db.commit()
    note = f"⏭ **{len(tracks)} 曲**をキューに追加"
    note += _bgm_mode_off()   # リクエストが来たらBGMモードは終了（終わったあと勝手にBGMへ戻らない）
    if not vc.is_playing():
        await _play_next()
        note += f"\n▶ 再生開始：{(_player.get('current') or '').lstrip('▶🎵 ')}"
    return note

def _bgm_mode_off():
    """BGMモードを解除する（リクエスト再生後に自動でBGMへ戻らないように）。解除したら案内文を返す"""
    if not _player.get("bgm"):
        return ""
    _player.update(bgm=False, auto=False)
    return "\n-# 🎵作業用BGMモードはオフにしました（リクエストが終わったら静かに待機。🎵でまた流せます）"

class PlCreateModal(discord.ui.Modal, title="📂 プレイリストを作る"):
    name_in = discord.ui.TextInput(label="プレイリスト名", max_length=40, placeholder="作業用しっとり曲 など")
    lines_in = discord.ui.TextInput(label="曲（1行に1つ・URLか検索語）", style=discord.TextStyle.paragraph, required=False,
                                    placeholder="https://…\nYOASOBI 夜に駆ける\n米津玄師 Lemon", max_length=1500)

    async def on_submit(self, interaction):
        name = str(self.name_in.value).strip() or "プレイリスト"
        lines = parse_track_lines(str(self.lines_in.value))
        await pl_create(interaction.user.id, name, lines)
        await interaction.response.send_message(
            f"📂 「**{name}**」を作りました（{len(lines)}曲）。パネル📂からみんな流せます。曲は「🎵 曲を追加」で増やせます。", ephemeral=True)

class PlAddModal(discord.ui.Modal, title="🎵 曲を追加"):
    lines_in = discord.ui.TextInput(label="曲（1行に1つ・URLか検索語）", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, pid, pname):
        super().__init__()
        self.pid, self.pname = pid, pname

    async def on_submit(self, interaction):
        room = PLAYLIST_MAX - len(await pl_tracks(self.pid))
        lines = parse_track_lines(str(self.lines_in.value), limit=max(0, room))
        if not lines:
            await interaction.response.send_message(f"⚠️ 追加できませんでした（1プレイリスト{PLAYLIST_MAX}曲まで）", ephemeral=True)
            return
        await pl_add_tracks(self.pid, lines)
        await interaction.response.send_message(f"🎵 「**{self.pname}**」に {len(lines)} 曲追加しました。", ephemeral=True)

class PlPlaySelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"{r['name']}（{r['oname']}・{r['n']}曲）"[:100], value=str(r["id"])) for r in rows]
        super().__init__(placeholder="📂 流すプレイリストを選ぶ", options=opts)

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        rows = await pl_tracks(int(self.values[0]))
        if not rows:
            await interaction.followup.send("このプレイリストはまだ空です。", ephemeral=True)
            return
        note = await enqueue_lazy(interaction, [(r["query"], (r["title"] or r["query"])[:80]) for r in rows])
        await interaction.followup.send(note or "❌ VCに接続できませんでした。どこかのVCに入ってから選んでね。", ephemeral=True)

class PlPickOwnSelect(discord.ui.Select):
    def __init__(self, rows, mode):
        self.mode = mode
        self.names = {str(r["id"]): r["name"] for r in rows}
        opts = [discord.SelectOption(label=f"{r['name']}（{r['n']}曲）"[:100], value=str(r["id"])) for r in rows]
        super().__init__(placeholder="自分のプレイリストを選ぶ", options=opts)

    async def callback(self, interaction):
        pid, name = int(self.values[0]), self.names[self.values[0]]
        if self.mode == "add":
            await interaction.response.send_modal(PlAddModal(pid, name))
            return
        rows = await pl_tracks(pid)
        if self.mode == "ren":
            if not rows:
                await interaction.response.edit_message(content=f"「**{name}**」にはまだ曲がありません。", view=None)
                return
            v = discord.ui.View(timeout=300)
            v.add_item(PlTrackRenameSelect(rows))
            await interaction.response.edit_message(content=f"✏️ 「**{name}**」のどの曲の名前を変える？", view=v)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(PlDelSelect(pid, name, rows))
        await interaction.response.edit_message(content=f"🗑 「**{name}**」から消すものを選んでね", view=v)

class PlTrackRenameSelect(discord.ui.Select):
    def __init__(self, rows):
        self.by_id = {str(r["id"]): r for r in rows}
        opts = [discord.SelectOption(label=(r["title"] or r["query"])[:100], value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="✏️ 名前を変える曲を選ぶ", options=opts)

    async def callback(self, interaction):
        r = self.by_id[self.values[0]]
        await interaction.response.send_modal(TrackRenameModal("member_playlist_tracks", r["id"], r["title"] or r["query"]))

class PlDelSelect(discord.ui.Select):
    def __init__(self, pid, name, rows):
        self.pid, self.pname = pid, name
        opts = [discord.SelectOption(label="🗑 プレイリストごと削除", value="all")] + \
               [discord.SelectOption(label=(r["title"] or r["query"])[:100], value=str(r["id"])) for r in rows[:24]]
        super().__init__(placeholder="消す曲（複数OK）か、まるごと削除", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        if "all" in self.values:
            await db.execute("DELETE FROM member_playlist_tracks WHERE pl_id=?", (self.pid,))
            await db.execute("DELETE FROM member_playlists WHERE id=?", (self.pid,))
            await db.commit()
            await interaction.response.edit_message(content=f"🗑 「**{self.pname}**」を削除しました。", view=None)
            return
        ids = [int(v) for v in self.values]
        await db.executemany("DELETE FROM member_playlist_tracks WHERE id=? AND pl_id=?", [(i, self.pid) for i in ids])
        await db.commit()
        await interaction.response.edit_message(content=f"🗑 「**{self.pname}**」から {len(ids)} 曲消しました。", view=None)

class PlHubView(discord.ui.View):
    def __init__(self, rows):
        super().__init__(timeout=600)
        if rows:
            self.add_item(PlPlaySelect(rows))

    async def _mine(self, interaction):
        return [r for r in await pl_all() if r["owner_id"] == str(interaction.user.id)]

    @discord.ui.button(label="➕ 新しく作る", style=discord.ButtonStyle.primary, row=1)
    async def b_create(self, interaction, button):
        await interaction.response.send_modal(PlCreateModal())

    @discord.ui.button(label="🎵 曲を追加", style=discord.ButtonStyle.secondary, row=1)
    async def b_add(self, interaction, button):
        mine = await self._mine(interaction)
        if not mine:
            await interaction.response.send_message("自分のプレイリストがまだありません。「➕ 新しく作る」からどうぞ。", ephemeral=True)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(PlPickOwnSelect(mine, "add"))
        await interaction.response.send_message("🎵 どのプレイリストに追加する？", view=v, ephemeral=True)

    @discord.ui.button(label="✏️ 曲名を変更", style=discord.ButtonStyle.secondary, row=1)
    async def b_rename(self, interaction, button):
        mine = await self._mine(interaction)
        if not mine:
            await interaction.response.send_message("自分のプレイリストがまだありません（直せるのは自分のものだけ）。", ephemeral=True)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(PlPickOwnSelect(mine, "ren"))
        await interaction.response.send_message("✏️ どのプレイリストの曲名を直す？", view=v, ephemeral=True)

    @discord.ui.button(label="🗑 削除", style=discord.ButtonStyle.secondary, row=1)
    async def b_del(self, interaction, button):
        mine = await self._mine(interaction)
        if not mine:
            await interaction.response.send_message("自分のプレイリストがまだありません（消せるのは自分のものだけ）。", ephemeral=True)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(PlPickOwnSelect(mine, "del"))
        await interaction.response.send_message("🗑 どのプレイリストを整理する？", view=v, ephemeral=True)

class RecSetModal(discord.ui.Modal, title="⭐ おすすめ曲を登録"):
    q_in = discord.ui.TextInput(label="曲のURL または 検索語", max_length=300)
    c_in = discord.ui.TextInput(label="紹介文（ひとこと）", style=discord.TextStyle.paragraph, max_length=200, required=False,
                                placeholder="作業がはかどる神曲です など")

    async def on_submit(self, interaction):
        uid = str(interaction.user.id)
        if await rec_count(uid) >= REC_MAX:
            await interaction.response.send_message(f"⚠️ おすすめは1人{REC_MAX}件まで。「🗑 自分のを消す」で空けてから登録してね。", ephemeral=True)
            return
        q = str(self.q_in.value).strip()
        cur = await db.execute("INSERT INTO member_recs(user_id,query,title,comment,ts,kind) VALUES(?,?,?,?,?,'song')",
                               (uid, q, q[:80], str(self.c_in.value).strip()[:200], int(now_jst().timestamp())))
        rid = cur.lastrowid
        await db.commit()
        if looks_like_url(q):
            asyncio.create_task(_bg_fill_titles("member_recs", [(rid, q)]))
        await interaction.response.send_message("⭐ おすすめ曲を登録しました！パネル⭐からみんな聴けます。", ephemeral=True)

class RecArtistModal(discord.ui.Modal, title="🎤 おすすめアーティストを登録"):
    a_in = discord.ui.TextInput(label="アーティスト名", max_length=80, placeholder="YOASOBI など")
    c_in = discord.ui.TextInput(label="紹介文（ひとこと）", style=discord.TextStyle.paragraph, max_length=200, required=False,
                                placeholder="全曲はずれなしです など")

    async def on_submit(self, interaction):
        uid = str(interaction.user.id)
        if await rec_count(uid) >= REC_MAX:
            await interaction.response.send_message(f"⚠️ おすすめは1人{REC_MAX}件まで。「🗑 自分のを消す」で空けてから登録してね。", ephemeral=True)
            return
        name = str(self.a_in.value).strip()
        await db.execute("INSERT INTO member_recs(user_id,query,title,comment,ts,kind) VALUES(?,?,?,?,?,'artist')",
                         (uid, name, name[:80], str(self.c_in.value).strip()[:200], int(now_jst().timestamp())))
        await db.commit()
        await interaction.response.send_message(
            f"🎤 おすすめアーティスト「**{name}**」を登録しました！\n"
            "つづけて「🎵 アーティストの曲を編集」から曲を入れてください（URL推奨・曲ごとに紹介文OK）。", ephemeral=True)

class RecSongAddModal(discord.ui.Modal, title="🎵 アーティストに曲を追加"):
    q_in = discord.ui.TextInput(label="曲のURL（おすすめ）または 検索語", max_length=300)
    c_in = discord.ui.TextInput(label="この曲の紹介文（ひとこと）", style=discord.TextStyle.paragraph, max_length=200, required=False,
                                placeholder="イントロから神 など")

    def __init__(self, rec_id, aname):
        super().__init__()
        self.rec_id, self.aname = rec_id, aname

    async def on_submit(self, interaction):
        if len(await rec_tracks(self.rec_id)) >= REC_ARTIST_TRACK_MAX:
            await interaction.response.send_message(f"⚠️ 1アーティスト{REC_ARTIST_TRACK_MAX}曲までです。", ephemeral=True)
            return
        q = str(self.q_in.value).strip()
        cur = await db.execute("INSERT INTO member_rec_tracks(rec_id,query,title,comment,ts,added_by) VALUES(?,?,?,?,?,?)",
                               (self.rec_id, q, q[:80], str(self.c_in.value).strip()[:200], int(now_jst().timestamp()),
                                str(interaction.user.id)))
        rid = cur.lastrowid
        await db.commit()
        if looks_like_url(q):
            asyncio.create_task(_bg_fill_titles("member_rec_tracks", [(rid, q)]))
        n = len(await rec_tracks(self.rec_id))
        await interaction.response.send_message(f"🎵 「**{self.aname}**」に追加しました（いま{n}曲）。続けて追加もできます。", ephemeral=True)

class RecTrackDelSelect(discord.ui.Select):
    def __init__(self, rec_id, aname, rows, owner_id):
        self.rec_id, self.aname, self.owner_id = rec_id, aname, owner_id
        opts = [discord.SelectOption(label=(r["title"] or r["query"])[:100], value=str(r["id"]),
                                     description=(r["comment"] or "")[:100]) for r in rows]
        super().__init__(placeholder="🗑 消す曲を選ぶ（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        ids = [int(v) for v in self.values]
        if uid == str(self.owner_id):   # アーティストの登録者は全曲消せる
            await db.executemany("DELETE FROM member_rec_tracks WHERE id=? AND rec_id=?", [(i, self.rec_id) for i in ids])
        else:                           # ほかの人は自分が追加した曲だけ
            await db.executemany("DELETE FROM member_rec_tracks WHERE id=? AND rec_id=? AND added_by=?",
                                 [(i, self.rec_id, uid) for i in ids])
        await db.commit()
        left = {r["id"] for r in await rec_tracks(self.rec_id)}
        done = [i for i in ids if i not in left]
        msg = f"🗑 「**{self.aname}**」から {len(done)} 曲消しました。" if done else "⚠️ 消せませんでした。"
        if len(done) < len(ids):
            msg += "\n-# 消せるのは、アーティストの登録者（全曲）か、自分が追加した曲だけです"
        await interaction.response.edit_message(content=msg, view=None)

class RecTrackRenameSelect(discord.ui.Select):
    def __init__(self, rows, owner_id):
        self.by_id = {str(r["id"]): r for r in rows}
        self.owner_id = str(owner_id)
        opts = [discord.SelectOption(label=(r["title"] or r["query"])[:100], value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="✏️ 名前を変える曲を選ぶ", options=opts)

    async def callback(self, interaction):
        r = self.by_id[self.values[0]]
        uid = str(interaction.user.id)
        if uid != self.owner_id and uid != (r["added_by"] or ""):
            await interaction.response.send_message("⚠️ 直せるのは、アーティストの登録者か、その曲を追加した人だけです。", ephemeral=True)
            return
        await interaction.response.send_modal(TrackRenameModal("member_rec_tracks", r["id"], r["title"] or r["query"]))

class ArtistManageView(discord.ui.View):
    def __init__(self, rec_id, aname, rows, owner_id):
        super().__init__(timeout=600)
        self.rec_id, self.aname, self.owner_id = rec_id, aname, owner_id
        if rows:
            self.add_item(RecTrackDelSelect(rec_id, aname, rows, owner_id))

    @discord.ui.button(label="➕ 曲を追加", style=discord.ButtonStyle.primary, row=1)
    async def b_add(self, interaction, button):
        await interaction.response.send_modal(RecSongAddModal(self.rec_id, self.aname))

    @discord.ui.button(label="✏️ 曲名を変更", style=discord.ButtonStyle.secondary, row=1)
    async def b_rename(self, interaction, button):
        rows = await rec_tracks(self.rec_id)
        if not rows:
            await interaction.response.send_message("まだ曲がありません。", ephemeral=True)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(RecTrackRenameSelect(rows, self.owner_id))
        await interaction.response.send_message(f"✏️ 「**{self.aname}**」のどの曲の名前を変える？", view=v, ephemeral=True)

    @discord.ui.button(label="🗑 アーティストごと削除", style=discord.ButtonStyle.danger, row=1)
    async def b_delall(self, interaction, button):
        if str(interaction.user.id) != str(self.owner_id):
            await interaction.response.send_message("⚠️ アーティストごと消せるのは登録した本人だけです。", ephemeral=True)
            return
        await db.execute("DELETE FROM member_rec_tracks WHERE rec_id=?", (self.rec_id,))
        await db.execute("DELETE FROM member_recs WHERE id=?", (self.rec_id,))
        await db.commit()
        await interaction.response.edit_message(content=f"🗑 「**{self.aname}**」を曲ごと削除しました。", view=None)

def _artist_track_note(rec, t):
    """🎤アーティストの曲1つぶんの紹介ノート（曲の紹介文＞アーティストの紹介文＞名前のみ）"""
    aname = (rec["title"] or rec["query"])[:40]
    base = f"🎤 {rec['uname']}のおすすめ（{aname}）"
    if t["comment"]:
        return f"{base}：{_oneline(t['comment'])}"
    return f"{base}：{_oneline(rec['comment'])}" if rec["comment"] else base

class RecTrackPlaySelect(discord.ui.Select):
    def __init__(self, rec, rows):
        self.rec = rec
        self.by_id = {str(t["id"]): t for t in rows}
        opts = [discord.SelectOption(label=(t["title"] or t["query"])[:100], value=str(t["id"]),
                                     description=(t["comment"] or "")[:100]) for t in rows]
        super().__init__(placeholder="🎵 聴きたい曲を選ぶ（選ぶたびキューへ・好きな順でOK）", options=opts)

    async def callback(self, interaction):
        await interaction.response.defer()
        t = self.by_id[self.values[0]]
        note = await enqueue_lazy(interaction, [(t["query"], (t["title"] or t["query"])[:80], _artist_track_note(self.rec, t))])
        aname = (self.rec["title"] or self.rec["query"])[:40]
        await interaction.edit_original_response(
            content=f"🎤 **{aname}**\n{note or '❌ VCに接続できませんでした。どこかのVCに入ってから選んでね。'}"
                    "\n-# 続けて選ぶと好きな順でキューに積めます", view=self.view)

class ArtistPlayView(discord.ui.View):
    def __init__(self, rec, rows):
        super().__init__(timeout=600)
        self.rec, self.rows = rec, rows
        self.add_item(RecTrackPlaySelect(rec, rows))

    @discord.ui.button(label="▶ ぜんぶ流す（登録順）", style=discord.ButtonStyle.primary, row=1)
    async def b_all(self, interaction, button):
        await interaction.response.defer()
        r = self.rec
        tracks = [(t["query"], (t["title"] or t["query"])[:80], _artist_track_note(r, t)) for t in self.rows]
        note = await enqueue_lazy(interaction, tracks)
        await interaction.edit_original_response(
            content=note or "❌ VCに接続できませんでした。どこかのVCに入ってから選んでね。", view=None)

class RecPlaySelect(discord.ui.Select):
    def __init__(self, rows):
        self.meta = {str(r["id"]): r for r in rows}
        opts = [discord.SelectOption(label=rec_label(r), value=str(r["id"]),
                                     description=(r["comment"] or "")[:100]) for r in rows]
        super().__init__(placeholder="⭐ みんなのおすすめから選ぶ（🎤はアーティスト）", options=opts)

    async def callback(self, interaction):
        await interaction.response.defer(ephemeral=True)
        r = self.meta[self.values[0]]
        mark = "🎤" if r["kind"] == "artist" else "⭐"
        extra = sub_text(f"{mark} {r['uname']}のおすすめ：{_oneline(r['comment'])}") if r["comment"] else ""
        if r["kind"] == "artist":   # アーティスト：曲を選んで聴くか、ぜんぶ流すかを選べる画面へ
            rows = await rec_tracks(r["id"])
            if not rows:
                await interaction.followup.send(f"🎤 「{r['title'] or r['query']}」にはまだ曲が登録されていません。"
                                                "（「🎵 アーティストの曲を追加・編集」からだれでも追加できます）", ephemeral=True)
                return
            aname = (r["title"] or r["query"])[:40]
            await interaction.followup.send(f"🎤 **{aname}**（{r['uname']}・{len(rows)}曲）{extra}"
                                            "\n曲を選ぶと1曲ずつ・好きな順でキューへ。▶でぜんぶ流せます。",
                                            view=ArtistPlayView(r, rows), ephemeral=True)
            return
        note_txt = f"⭐ {r['uname']}のおすすめ：{_oneline(r['comment'])}" if r["comment"] else None
        note = await enqueue_lazy(interaction, [(r["query"], (r["title"] or r["query"])[:80], note_txt)])
        await interaction.followup.send((note or "❌ VCに接続できませんでした。どこかのVCに入ってから選んでね。") + extra, ephemeral=True)

class SongManageView(discord.ui.View):
    """⭐自分のおすすめ曲の編集（曲名変更・削除）"""

    def __init__(self, rec):
        super().__init__(timeout=600)
        self.rec = rec

    @discord.ui.button(label="✏️ 曲名を変更", style=discord.ButtonStyle.primary)
    async def b_ren(self, interaction, button):
        await interaction.response.send_modal(TrackRenameModal("member_recs", self.rec["id"], self.rec["title"] or self.rec["query"]))

    @discord.ui.button(label="🗑 このおすすめを削除", style=discord.ButtonStyle.danger)
    async def b_del(self, interaction, button):
        await db.execute("DELETE FROM member_recs WHERE id=? AND user_id=?", (self.rec["id"], str(interaction.user.id)))
        await db.commit()
        await interaction.response.edit_message(content=f"🗑 「{self.rec['title'] or self.rec['query']}」を削除しました。", view=None)

class RecManagePickSelect(discord.ui.Select):
    """🛠 編集対象を選ぶ：自分のおすすめ曲＋全アーティスト"""

    def __init__(self, rows):
        self.by_id = {str(r["id"]): r for r in rows}
        opts = [discord.SelectOption(label=rec_label(r), value=str(r["id"])) for r in rows]
        super().__init__(placeholder="🛠 編集するものを選ぶ（⭐自分の曲・🎤アーティスト）", options=opts)

    async def callback(self, interaction):
        r = self.by_id[self.values[0]]
        if r["kind"] == "artist":
            name = r["title"] or r["query"]
            rows = await rec_tracks(r["id"])
            await interaction.response.edit_message(
                content=f"🎤 「**{name}**」（{len(rows)}曲）\n➕で曲を追加（URL推奨・曲ごとに紹介文OK・だれでも追加できます）、"
                        "✏️で曲名変更、セレクトで曲を削除（登録者は全曲・ほかの人は自分が足した曲）。",
                view=ArtistManageView(r["id"], name, rows, r["user_id"]))
            return
        await interaction.response.edit_message(content=f"⭐ 「**{r['title'] or r['query']}**」をどうする？", view=SongManageView(r))

class RecHubView(discord.ui.View):
    def __init__(self, rows, page=0):
        super().__init__(timeout=600)
        pages = max(1, -(-len(rows) // REC_PAGE))
        self.rows, self.page = rows, max(0, min(page, pages - 1))
        chunk = rows[self.page * REC_PAGE:(self.page + 1) * REC_PAGE]
        if chunk:
            self.add_item(RecPlaySelect(chunk))
        if pages > 1:   # 25曲を超えたらページ送り
            for lbl, d in (("◀ 前の25曲", -1), ("次の25曲 ▶", +1)):
                b = discord.ui.Button(label=lbl, style=discord.ButtonStyle.secondary, row=2,
                                      disabled=not (0 <= self.page + d < pages))
                b.callback = self._mk_flip(d)
                self.add_item(b)

    def _mk_flip(self, d):
        async def flip(interaction):
            p = self.page + d
            await interaction.response.edit_message(content=rec_head(self.rows, p), view=RecHubView(self.rows, p))
        return flip

    @discord.ui.button(label="⭐ おすすめ曲を登録", style=discord.ButtonStyle.primary, row=1)
    async def b_set(self, interaction, button):
        await interaction.response.send_modal(RecSetModal())

    @discord.ui.button(label="🎤 アーティストを登録", style=discord.ButtonStyle.primary, row=1)
    async def b_set_artist(self, interaction, button):
        await interaction.response.send_modal(RecArtistModal())

    @discord.ui.button(label="🛠 編集（曲の追加・曲名・削除）", style=discord.ButtonStyle.secondary, row=1)
    async def b_manage(self, interaction, button):
        uid = str(interaction.user.id)
        cands = [r for r in await rec_all() if r["kind"] == "artist" or r["user_id"] == uid][:25]
        if not cands:
            await interaction.response.send_message("編集できるものがまだありません。まずは⭐か🎤で登録してね。", ephemeral=True)
            return
        v = discord.ui.View(timeout=300)
        v.add_item(RecManagePickSelect(cands))
        await interaction.response.send_message(
            "🛠 編集するものを選んでね\n-# ⭐自分のおすすめ曲＝曲名変更・削除／🎤アーティスト＝曲の追加はだれでも・削除や曲名は登録者と追加した人",
            view=v, ephemeral=True)

# ------------------------------------------------------------
#  📜 歌詞（lrclib.net＝無料・キー不要のコミュニティ歌詞DB。本人だけに表示）
# ------------------------------------------------------------
_TITLE_NOISE = r"(?:official|music\s*video|lyric\s*video|lyrics?|audio|visualizer|mv|pv|full|short\s*ver\.?|ver\.?|映像|歌詞付き|歌詞|公式|ミュージックビデオ|リリックビデオ)"

def clean_track_title(t):
    """YouTube動画タイトル → 歌詞検索用のきれいな「アーティスト 曲名」っぽい文字列"""
    t = t or ""
    # 【MV】(Official Video) みたいなノイズ括弧は丸ごと落とす（曲名が入った括弧は残す）
    t = re.sub(r"[\(\[（【][^)\]）】]*[\)\]）】]",
               lambda m: " " if re.search(_TITLE_NOISE, m.group(0), re.I) else m.group(0), t)
    t = re.sub(_TITLE_NOISE, " ", t, flags=re.I)
    for ch in "「」『』[]【】()（）|｜/":
        t = t.replace(ch, " ")
    return re.sub(r"\s+", " ", t).strip()

def _lrclib_match_score(d, q):
    """lrclibの候補がいまの曲か判定。曲名が検索語（動画タイトル）に含まれなければ None＝別の曲。
    合えばスコア（アーティスト名も含まれていれば 2、曲名だけなら 1）"""
    qn = lyrics_norm(q)
    raw = d.get("trackName") or ""
    tn = lyrics_norm(re.sub(r"[\(\[（【][^)\]）】]*[\)\]）】]", " ", raw))   # 「(Remix)」などの補足は外して比べる
    if not tn or not qn:
        return None
    ok = tn in qn
    if not ok:   # 語順違い・区切り違い：曲名の各語（2文字以上）がぜんぶ入っていればOK
        toks = [w for w in clean_track_title(raw).casefold().split() if len(w) >= 2]
        ok = bool(toks) and all(t in qn for t in toks)
    if not ok:
        return None
    an = lyrics_norm(d.get("artistName") or "")
    artist_hit = bool(an) and an in qn
    if len(tn) < 2 and not artist_hit:
        return None   # 1文字の曲名は偶然含まれやすいので、アーティスト名も合うときだけ
    return 2 if artist_hit else 1

async def lyrics_blocked(title):
    """「🙅 違う曲の歌詞」と記録された曲か（lrclibの歌詞を使わない）"""
    k = lyrics_norm(title)
    if not k:
        return False
    async with db.execute("SELECT 1 FROM lyrics_block WHERE norm_key=?", (k,)) as c:
        return bool(await c.fetchone())

async def fetch_lyrics(title):
    """曲タイトル → {track, artist, text} または None（lrclibのsearch APIで、曲名が合う候補だけから選ぶ）"""
    import aiohttp
    import urllib.parse
    q = clean_track_title(title)
    if not q or await lyrics_blocked(title):
        return None
    url = "https://lrclib.net/api/search?q=" + urllib.parse.quote(q)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),
                                         headers={"User-Agent": "seikatsu-bot (private Discord circle bot)"}) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return None
                arr = await r.json()
    except Exception as e:
        print(f"歌詞取得失敗: {e!r}", flush=True)
        return None
    best, best_s = None, 0
    for d in arr or []:
        if d.get("instrumental"):
            continue
        if re.search(r"(?i)\(?\b(inst(rumental)?|off vocal|karaoke)\b|\(inst", d.get("trackName") or ""):
            continue   # インスト・カラオケ版はスキップ（歌詞DB上は歌詞付きで登録されていることがある）
        txt = (d.get("plainLyrics") or "").strip()
        s = _lrclib_match_score(d, q)
        if txt and s and s > best_s:   # 曲名が合わない候補（同じ歌手の別の曲など）は使わない
            best, best_s = {"track": d.get("trackName") or "？", "artist": d.get("artistName") or "？", "text": txt}, s
    return best

def lyrics_norm(s):
    """歌詞登録の照合キー（ノイズ除去→空白除去→小文字化）"""
    return re.sub(r"\s+", "", clean_track_title(s)).casefold()

async def custom_lyrics_find(title):
    """メンバー登録の歌詞から、いまの曲タイトルにいちばん合うものを探す（最初に見つかったものではなく、いちばん近いもの）。
    完全一致 ＞ 登録名がタイトルに含まれる（長いほど優先）＞ タイトルが登録名に含まれる（4文字以上）＞ 登録名の各語がすべて含まれる"""
    k = lyrics_norm(title)
    if not k:
        return None
    async with db.execute("SELECT c.norm_key, c.title, c.text, COALESCE(u.name,'?') AS uname "
                          "FROM custom_lyrics c LEFT JOIN users u ON u.id=c.user_id") as c:
        rows = await c.fetchall()
    best, best_s = None, 0
    for r in rows:
        rk = r["norm_key"] or ""
        s = 0
        if rk and rk == k:
            s = 1000
        elif rk and len(rk) >= 2 and rk in k:
            s = 100 + len(rk)
        elif rk and len(k) >= 4 and k in rk:
            s = 50 + len(k)
        else:   # 語順違い：登録名の各語がぜんぶ入っていれば、部分一致と同じ物差し（一致した長さ）で比べる
            toks = [w.casefold() for w in clean_track_title(r["title"]).split() if len(w) >= 2]
            if toks and all(t in k for t in toks):
                s = 100 + sum(len(t) for t in toks)
        if s > best_s:
            best, best_s = r, s
    return best

class LyricsAddModal(discord.ui.Modal, title="📝 歌詞を登録"):
    t_in = discord.ui.TextInput(label="曲名（アーティスト名も入れると確実）", max_length=120)
    x_in = discord.ui.TextInput(label="歌詞", style=discord.TextStyle.paragraph, max_length=4000,
                                placeholder="歌詞を貼り付け（4000字まで）。[01:23.45]歌詞 の形式なら🎤歌詞同期に対応")

    def __init__(self, default_title=""):
        super().__init__()
        if default_title:
            self.t_in.default = default_title[:120]

    async def on_submit(self, interaction):
        title = str(self.t_in.value).strip()
        text = str(self.x_in.value).strip()
        if not await save_custom_lyrics(title, text, interaction.user.id):
            await interaction.response.send_message("⚠️ 曲名が読めませんでした。", ephemeral=True)
            return
        note = await apply_lyrics_to_current(title, text)
        synced = "　🎤 タイムスタンプつきなので同期にも使われます。" if parse_lrc(text) else ""
        await interaction.response.send_message(f"📝 「**{title}**」の歌詞を登録しました！その曲の再生中に📜で出ます。{synced}{note}",
                                                ephemeral=True)

async def apply_lyrics_to_current(title, text):
    """いま流れている曲の歌詞を登録・修正したら、その場で同期表示を差し替える（古い間違った同期を止める）"""
    cur = current_track_title()
    if not cur:
        return ""
    hit = await custom_lyrics_find(cur)
    if not hit or hit["norm_key"] != lyrics_norm(title):
        return ""   # 登録したのは別の曲
    it = _player.get("cur_item")
    lines = parse_lrc(text)
    k = _player.get("karaoke")
    if lines:
        _player["karaoke"] = {"lines": lines, "offset": 0.0, "auto": False, "item": it}
        _player["karaoke"]["task"] = asyncio.create_task(karaoke_task())
        return "\n-# 🎤 いま流れている曲の同期を、この歌詞に切り替えました"
    if k and k.get("item") is it:
        _player["karaoke"] = None   # タイムスタンプなしの歌詞に直した→間違っていた同期は止める
        return "\n-# 🎤 いま流れていた同期表示（別の歌詞）は止めました"
    return ""

async def save_custom_lyrics(title, text, uid):
    """メンバー歌詞を保存（同じ曲名なら上書き＝修正もこの手順で）。曲名が読めなければ False"""
    key = lyrics_norm(title)
    if not key:
        return False
    uid, ts0 = str(uid), int(now_jst().timestamp())
    cur = await db.execute("UPDATE custom_lyrics SET title=?, text=?, user_id=?, ts=? WHERE norm_key=?",
                           (title[:120], text, uid, ts0, key))
    if cur.rowcount == 0:
        await db.execute("INSERT INTO custom_lyrics(norm_key,title,text,user_id,ts) VALUES(?,?,?,?,?)",
                         (key, title[:120], text, uid, ts0))
    await db.commit()
    return True

async def send_current_lyrics(interaction):
    """再生中の曲の歌詞をephemeralのembedで表示（呼び出し側でdefer済み）"""
    title = current_track_title()
    if not title:
        await interaction.followup.send("いま歌詞を出せる曲が流れていません。", ephemeral=True)
        return
    local = await custom_lyrics_find(title)   # メンバー登録の歌詞があれば最優先
    if local:
        head, text, footer = f"📜 {local['title']}", local["text"], f"📝 メンバー登録の歌詞（{local['uname']}）"
    else:
        ly = await fetch_lyrics(title)
        if not ly:
            await interaction.followup.send(
                f"📜 歌詞が見つかりませんでした（{clean_track_title(title)[:60]}）\n"
                "-# 歌詞DB（lrclib）に未登録の曲もあります。下のボタンやセレクトから自分で登録できます（登録後は📜で出ます）",
                view=LyricsHubView(await lyrics_candidate_titles(), clean_track_title(title)), ephemeral=True)
            return
        head, text, footer = f"📜 {ly['track']} ／ {ly['artist']}", ly["text"], ""
    text = text[:7800]   # embed説明文は4096字まで→長い曲は2枚に分割
    chunks = [text[i:i + 3900] for i in range(0, len(text), 3900)]
    embeds = [discord.Embed(description=c, color=discord.Color.gold()) for c in chunks]
    embeds[0].title = head[:256]
    if footer:
        embeds[-1].set_footer(text=footer)
    await interaction.followup.send(embeds=embeds, ephemeral=True,
                                    view=LyricsHubView((), clean_track_title(title)))

class LyricsMenuView(discord.ui.View):
    """📜を押したときの分岐：再生中の曲の歌詞を見る or 任意の曲に歌詞を追加・編集"""

    def __init__(self):
        super().__init__(timeout=600)

    @discord.ui.button(label="📜 再生中の曲の歌詞を表示", style=discord.ButtonStyle.primary)
    async def b_show(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await send_current_lyrics(interaction)

    @discord.ui.button(label="📝 歌詞を追加・編集", style=discord.ButtonStyle.secondary)
    async def b_edit(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        cur = clean_track_title(current_track_title() or "")
        await interaction.followup.send("📝 歌詞を登録・修正する曲を選んでね（リストにない曲は⌨で入力）",
                                        view=LyricsHubView(await lyrics_candidate_titles(), cur), ephemeral=True)

async def lyrics_candidate_titles():
    """歌詞登録の候補曲：いまの曲→キュー→最近の再生履歴（重複除去・25件）"""
    out, seen = [], set()

    def add(t):
        t = (t or "").strip()
        k = " ".join(sorted(clean_track_title(t).casefold().split()))   # 語順違いの同曲も1つに
        if t and k and k not in seen:
            seen.add(k)
            out.append(t)
    add(current_track_title())
    for it in _player["queue"]:
        if it.get("type") in ("yt", "yt_lazy"):
            add(it.get("title"))
    # まだ流していなくても、おすすめ・アーティスト登録曲・プレイリストの曲には歌詞を付けられるように
    for sql in ("SELECT title, query FROM member_recs WHERE kind='song' ORDER BY id DESC LIMIT 40",
                "SELECT title, query FROM member_rec_tracks ORDER BY id DESC LIMIT 40",
                "SELECT title, query FROM member_playlist_tracks ORDER BY id DESC LIMIT 40"):
        async with db.execute(sql) as c:
            for r in await c.fetchall():
                add(r["title"] or r["query"])
    async with db.execute("SELECT title FROM play_history ORDER BY id DESC LIMIT 60") as c:
        for r in await c.fetchall():
            add(r["title"])
    return out[:25]

class LyricsPickSelect(discord.ui.Select):
    def __init__(self, titles):
        self.titles = titles
        opts = [discord.SelectOption(label=t[:100], value=str(i)) for i, t in enumerate(titles)]
        super().__init__(placeholder="📝 歌詞を登録・修正する曲を選ぶ（キュー・最近の曲）", options=opts)

    async def callback(self, interaction):
        await interaction.response.send_modal(LyricsAddModal(clean_track_title(self.titles[int(self.values[0])])))

class LyricsHubView(discord.ui.View):
    """歌詞の登録・修正への入口：いまの曲ボタン＋曲セレクト＋自由入力"""

    def __init__(self, titles=(), default_title=""):
        super().__init__(timeout=600)
        self.default_title = default_title
        if default_title:
            b = discord.ui.Button(label="📝 この曲の歌詞を登録・修正", style=discord.ButtonStyle.primary, row=1)
            b.callback = self._open_default
            self.add_item(b)
        if titles:
            self.add_item(LyricsPickSelect(list(titles)))

    async def _open_default(self, interaction):
        await interaction.response.send_modal(LyricsAddModal(self.default_title))

    @discord.ui.button(label="⌨ 曲名を入力して登録", style=discord.ButtonStyle.secondary, row=1)
    async def b_free(self, interaction, button):
        await interaction.response.send_modal(LyricsAddModal())

def current_track_title():
    """いま流れている曲の素のタイトル（歌詞検索用）。音楽以外（環境音等）は None"""
    it = _player.get("cur_item") or {}
    if it.get("type") in ("yt", "yt_lazy"):
        return it.get("title") or None
    if it.get("type") == "file":
        return os.path.splitext(os.path.basename(it.get("path", "")))[0] or None
    return None

# ------------------------------------------------------------
#  🎤 カラオケ：LRC（タイムスタンプ付き歌詞）をVCサブタイトルに1行ずつ流す
#  実測：VCステータスAPIのバケットは約0.8秒/6回＝行替わり頻度なら余裕
# ------------------------------------------------------------
def parse_lrc(text):
    """LRC形式テキスト → [(秒, 行), ...]（時刻順・空行=間奏）。タイムスタンプが無ければ []"""
    out = []
    for ln in (text or "").splitlines():
        stamps = re.findall(r"\[(\d+):(\d{1,2}(?:\.\d+)?)\]", ln)
        body = re.sub(r"\[[^\]]*\]", "", ln).strip()
        for m, s in stamps:
            out.append((int(m) * 60 + float(s), body))
    out.sort(key=lambda x: x[0])
    return out

async def fetch_synced_lyrics(title, duration=None):
    """lrclibから同期歌詞を取得 → ([(秒,行),...], 長さ差秒orNone)。見つからなければ None。
    動画の長さに一番近い候補を選ぶ（MVのイントロ等によるズレ対策A）"""
    import aiohttp
    import urllib.parse
    q = clean_track_title(title)
    if not q or await lyrics_blocked(title):
        return None
    url = "https://lrclib.net/api/search?q=" + urllib.parse.quote(q)
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10),
                                         headers={"User-Agent": "seikatsu-bot (private Discord circle bot)"}) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return None
                arr = await r.json()
    except Exception as e:
        print(f"同期歌詞取得失敗: {e!r}", flush=True)
        return None
    cands = []
    for d in arr or []:
        if d.get("instrumental") or re.search(r"(?i)\(?\b(inst(rumental)?|off vocal|karaoke)\b|\(inst", d.get("trackName") or ""):
            continue
        sync = (d.get("syncedLyrics") or "").strip()
        s = _lrclib_match_score(d, q)
        if not sync or not s:
            continue   # 曲名が合わない候補は、長さが近くても使わない（同じ歌手の別の曲が選ばれる事故の対策）
        diff = abs((d.get("duration") or 0) - duration) if duration and d.get("duration") else None
        cands.append((-s, diff if diff is not None else 9999, sync, diff))
    if not cands:
        return None
    cands.sort(key=lambda x: (x[0], x[1]))   # アーティストも合う＞曲名だけ、その中で長さが近い順
    best, best_diff = cands[0][2], cands[0][3]
    lines = parse_lrc(best)
    return (lines, best_diff) if lines else None

def track_pos():
    """いまの曲の再生位置（秒）。送信済み音声フレーム数ベース＝一時停止中は進まない"""
    vc = _player.get("vc")
    try:
        p = getattr(vc, "_player", None)
        if p:
            return p.loops * 0.02
    except Exception:
        pass
    return None

def karaoke_board(lines, idx):
    """歌詞ウィンドウの本文：前の行（小）／▶いまの行（太字）／次の2行（小）"""
    def line(j):
        return (lines[j][1] or "♪〜") if 0 <= j < len(lines) else None
    parts = []
    if idx is None:   # まだ最初の行の前
        parts.append("**▶ ♪〜**")
        nxt = [line(0), line(1)]
    else:
        prev = line(idx - 1)
        if prev:
            parts.append(f"-# {prev}")
        parts.append(f"**▶ {line(idx)}**")
        nxt = [line(idx + 1), line(idx + 2)]
    parts += [f"-# {t}" for t in nxt if t]
    return "\n".join(parts)

async def karaoke_task():
    """1秒ごとに現在の歌詞行を計算して、VCチャットの歌詞ウィンドウ（1メッセージを編集）と
    VCサブタイトルの両方に出す（どちらも変化があったときだけAPIを叩く）"""
    base_item = _player.get("cur_item")
    title = (current_track_title() or "")[:60]
    was_auto = bool((_player.get("karaoke") or {}).get("auto"))
    msg, vc = None, _player.get("vc")
    if vc and vc.is_connected():
        try:   # 歌詞ウィンドウ＝VCチャットに1通だけ投稿して編集し続ける（新着は増えない）
            msg = await vc.channel.send(f"🎤 **{title}**\n-# 歌詞を同期中…", silent=True)
            k0 = _player.get("karaoke")
            if k0 is not None:
                k0["msg"] = msg
        except Exception:
            msg = None
    last_status, last_idx = None, -2
    try:
        while True:
            k = _player.get("karaoke")
            vc = _player.get("vc")
            if not k or not vc or not vc.is_connected() or _player.get("cur_item") is not base_item:
                break   # OFF・切断・曲が変わったら終了（1曲リピートはcur_itemが同じなので続く＝位置も頭に戻る）
            if k.get("task") not in (None, asyncio.current_task()):
                break   # 同じ曲で歌詞が差し替えられた（新しい同期タスクに交代）
            pos = track_pos()
            if pos is not None:
                t = pos + k.get("offset", 0.0)
                idx = None
                for n, (sec, _txt) in enumerate(k["lines"]):
                    if sec <= t:
                        idx = n
                    else:
                        break
                cur_line = k["lines"][idx][1] if idx is not None else ""
                show = f"🎤 {cur_line}"[:480] if cur_line else "🎤 ♪〜"
                if show != last_status:
                    try:
                        await vc.channel.edit(status=show)
                        _vc_status_cache["v"] = show   # キャッシュも更新（終了後の復帰が飛ばされないように）
                        last_status = show
                    except Exception as e:
                        print(f"🎤 歌詞同期表示エラー: {e!r}", flush=True)
                if msg and idx != last_idx:
                    try:
                        await msg.edit(content=f"🎤 **{title}**\n{karaoke_board(k['lines'], idx)}")
                        last_idx = idx
                    except Exception:
                        msg = None   # ウィンドウが消された等→以後はサブタイトルのみで続行
            await asyncio.sleep(1.0)
    finally:
        k = _player.get("karaoke")
        if k and k.get("task") is asyncio.current_task():
            _player["karaoke"] = None
        if msg:
            try:
                if was_auto:   # 自動起動ぶんは消す（連続再生でチャットが「おわり」行だらけにならないように）
                    await msg.delete()
                else:          # 手動起動ぶんは小さな1行にたたむ
                    await msg.edit(content=f"-# 🎤 歌詞同期おわり：{title}")
            except Exception:
                pass
        try:
            asyncio.create_task(update_work_vc_status())   # 通常のサブタイトルへ復帰
        except Exception:
            pass

async def karaoke_auto_enabled():
    """歌詞同期の自動起動（既定ON・🎤のコントロールから切替）"""
    return (await meta_get("karaoke_auto", "1")) != "0"

async def _maybe_auto_karaoke():
    """新しい曲が始まったとき、同期歌詞が見つかれば自動で歌詞同期を開始（無ければ静かに何もしない）"""
    try:
        if not await karaoke_auto_enabled():
            return
        it = _player.get("cur_item") or {}
        k = _player.get("karaoke")
        if k and k.get("item") is it:
            return   # この曲の同期はもう動いている（前の曲の残りtaskは曲変更を検知して自分で片付ける）
        title = current_track_title()
        vc = _player.get("vc")
        if not title or not vc or not vc.is_connected():
            return
        lines, diff = [], None
        local = await custom_lyrics_find(title)
        if local:
            lines = parse_lrc(local["text"])
            if not lines:
                return   # メンバー登録の歌詞（タイムスタンプ無し）がある曲は、lrclibの同期歌詞に戻らない
        if not lines:
            got = await fetch_synced_lyrics(title, it.get("duration"))
            if not got:
                return
            lines, diff = got
        if diff is not None and diff > 10:
            return   # 元音源と長さが大きく違う＝別物の可能性大。自動では出さない（手動🎤なら出せる）
        k = _player.get("karaoke")
        if _player.get("cur_item") is not it or (k and k.get("item") is it):
            return   # 歌詞を取りに行っている間に曲が変わった／すでにこの曲の同期が始まっていたらやめる
        _player["karaoke"] = {"lines": lines, "offset": 0.0, "auto": True, "item": it}
        _player["karaoke"]["task"] = asyncio.create_task(karaoke_task())
    except Exception as e:
        print(f"🎤 自動同期エラー: {e!r}", flush=True)

TAP_LEAD = 0.3   # ⏺打ち込みの反応遅れ補正（押した瞬間より少し手前に刻む）

def lrc_format(stamps):
    """[(秒, 行), ...] → LRCテキスト"""
    return "\n".join(f"[{int(s // 60):02d}:{s % 60:05.2f}]{ln}" for s, ln in stamps)

class LrcTapView(discord.ui.View):
    """⏺ LRC打ち込みモード：曲を聴きながら、行が歌われた瞬間に⏺を押してタイムスタンプを刻む"""

    def __init__(self, title, lines):
        super().__init__(timeout=1800)
        self.title = title            # 保存時の曲名
        self.lines = lines            # 全行（空行除去済み）
        self.stamps = []              # 刻んだ [(秒, 行), ...]
        self.idx = 0                  # 次に刻む行
        self.base_item = _player.get("cur_item")

    def board(self):
        prev_line = f"-# 前: {self.stamps[-1][1][:40]}\n" if self.stamps else ""
        cur = self.lines[self.idx] if self.idx < len(self.lines) else "（全行おわり！💾 で保存してね）"
        nxt = f"\n-# 次: {self.lines[self.idx + 1][:40]}" if self.idx + 1 < len(self.lines) else ""
        return (f"⏺ **LRC打ち込みモード**（{len(self.stamps)}/{len(self.lines)} 行）\n"
                f"{prev_line}▶ **{cur}**{nxt}\n"
                "-# この行が歌われた瞬間に ⏺ を押す（反応遅れ0.3秒は自動補正・押し間違いは↩）")

    async def _refresh(self, interaction, note=""):
        await interaction.response.edit_message(content=(note + "\n" if note else "") + self.board(), view=self)

    @discord.ui.button(label="⏺ いま！", style=discord.ButtonStyle.danger)
    async def b_tap(self, interaction, button):
        if _player.get("cur_item") is not self.base_item:
            await interaction.response.edit_message(content="⚠️ 曲が変わったので打ち込みを中止しました（刻んだ分は保存されていません）。", view=None)
            return
        pos = track_pos()
        if pos is None:
            await interaction.response.edit_message(content="⚠️ 再生位置が取れませんでした（再生中に使ってね）。", view=None)
            return
        if self.idx >= len(self.lines):
            await self._refresh(interaction, "もう全行刻み終わっています。💾 で保存！")
            return
        t = max(0.0, pos - TAP_LEAD)
        if self.stamps and t <= self.stamps[-1][0]:
            t = self.stamps[-1][0] + 0.1   # 連打しても時刻が逆転しないように
        self.stamps.append((t, self.lines[self.idx]))
        self.idx += 1
        await self._refresh(interaction)

    @discord.ui.button(label="↩ 1行もどる", style=discord.ButtonStyle.secondary)
    async def b_undo(self, interaction, button):
        if self.stamps:
            self.stamps.pop()
            self.idx = max(0, self.idx - 1)
        await self._refresh(interaction)

    @discord.ui.button(label="⏭ この行をとばす", style=discord.ButtonStyle.secondary)
    async def b_skip(self, interaction, button):
        if self.idx < len(self.lines):
            self.idx += 1   # 刻まずに次へ（この行はLRCに入らない）
        await self._refresh(interaction)

    @discord.ui.button(label="💾 保存", style=discord.ButtonStyle.success)
    async def b_save(self, interaction, button):
        if not self.stamps:
            await self._refresh(interaction, "⚠️ まだ1行も刻んでいません。")
            return
        lrc = lrc_format(self.stamps)
        await save_custom_lyrics(self.title, lrc, interaction.user.id)
        note = await apply_lyrics_to_current(self.title, lrc)
        await interaction.response.edit_message(
            content=f"💾 「**{self.title}**」のLRC（{len(self.stamps)}行）を保存しました！\n"
                    "この曲の再生中に 🎤 を押すと歌詞が同期表示されます（ズレは🎤の±2秒ボタンで微調整）。" + note, view=None)

    @discord.ui.button(label="❌ 中止", style=discord.ButtonStyle.secondary)
    async def b_cancel(self, interaction, button):
        await interaction.response.edit_message(content="❌ 打ち込みを中止しました（保存していません）。", view=None)

class KaraokeMakeView(discord.ui.View):
    """🎤で同期歌詞が無かったときの「⏺打ち込みでカラオケ化」入口"""

    def __init__(self, title):
        super().__init__(timeout=600)
        self.title = title   # clean済みタイトル

    @discord.ui.button(label="⏺ 打ち込みで同期歌詞を作る", style=discord.ButtonStyle.primary)
    async def b_start(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        local = await custom_lyrics_find(self.title)
        text = local["text"] if local else None
        if not text:
            ly = await fetch_lyrics(self.title)
            text = ly["text"] if ly else None
        if not text:
            await interaction.followup.send("⚠️ 全文歌詞がまだありません。先に歌詞を登録してから、もう一度どうぞ。",
                                            view=LyricsHubView((), self.title), ephemeral=True)
            return
        lines = [ln.strip() for ln in re.sub(r"\[[^\]]*\]", "", text).splitlines() if ln.strip()]
        if not lines:
            await interaction.followup.send("⚠️ 歌詞が読めませんでした。", ephemeral=True)
            return
        v = LrcTapView(self.title, lines)
        await interaction.followup.send(
            "🎧 **打ち込みのコツ**：曲を頭から流し直した直後に始めるときれいに刻めます（もう一度リクエストすればOK）。\n" + v.board(),
            view=v, ephemeral=True)

class KaraokeCtlView(discord.ui.View):
    def __init__(self, auto_on=True):
        super().__init__(timeout=900)
        b = discord.ui.Button(label=("🚫 自動同期をやめる" if auto_on else "🔁 自動同期をONに戻す"),
                              style=discord.ButtonStyle.secondary, row=1)
        b.callback = self._mk_auto_toggle(auto_on)
        self.add_item(b)

    def _mk_auto_toggle(self, cur_on):
        async def cb(interaction):
            await meta_set("karaoke_auto", "0" if cur_on else "1")
            if cur_on:
                _player["karaoke"] = None   # いまの曲の表示も止める
                await interaction.response.edit_message(
                    content="🚫 自動同期をOFFにしました（出したい曲だけ🎤で手動起動できます）", view=None)
            else:
                await interaction.response.edit_message(
                    content="🔁 自動同期をONに戻しました（同期歌詞がある曲は自動で表示されます）", view=None)
        return cb

    async def _nudge(self, interaction, d):
        k = _player.get("karaoke")
        if not k:
            await interaction.response.edit_message(content="🎤 歌詞同期は終了しています。", view=None)
            return
        k["offset"] = k.get("offset", 0.0) + d
        await interaction.response.edit_message(
            content=f"🎤 歌詞同期中（ずれ補正：歌詞を {k['offset']:+.0f} 秒）\n-# 歌詞が遅れて見えるなら「はやく」、先走るなら「おそく」")

    @discord.ui.button(label="⏪ 歌詞を2秒はやく", style=discord.ButtonStyle.secondary)
    async def b_fast(self, interaction, button):
        await self._nudge(interaction, +2.0)

    @discord.ui.button(label="⏩ 歌詞を2秒おそく", style=discord.ButtonStyle.secondary)
    async def b_slow(self, interaction, button):
        await self._nudge(interaction, -2.0)

    @discord.ui.button(label="⏹ 同期表示OFF", style=discord.ButtonStyle.danger)
    async def b_off(self, interaction, button):
        _player["karaoke"] = None   # タスクが次のtickで気づいて通常表示に戻す
        await interaction.response.edit_message(content="🎤 歌詞の同期表示をやめました。", view=None)

    @discord.ui.button(label="🙅 違う曲の歌詞", style=discord.ButtonStyle.secondary, row=1)
    async def b_wrong(self, interaction, button):
        """lrclibが別の曲の歌詞を返した曲を記録して、今後その曲ではlrclibの歌詞を使わない"""
        title = current_track_title()
        key = lyrics_norm(title or "")
        if not key:
            await interaction.response.edit_message(content="いま流れている曲がありません。", view=None)
            return
        await db.execute("INSERT OR REPLACE INTO lyrics_block(norm_key,user_id,ts) VALUES(?,?,?)",
                         (key, str(interaction.user.id), int(now_jst().timestamp())))
        await db.commit()
        _player["karaoke"] = None
        await interaction.response.edit_message(
            content=f"🙅 「{clean_track_title(title)[:60]}」は、今後lrclibの歌詞を出しません（同期も📜も）。\n"
                    "-# 正しい歌詞は 📜 → 📝 で登録できます。タイムスタンプつき（[00:12.34]行）なら同期にも使われます",
            view=LyricsHubView((), clean_track_title(title)))

# ------------------------------------------------------------
#  🎶 音楽パネル（#作業ログ🖥に常駐・よく使う操作をボタン化。返事は本人だけ＝チャンネルを汚さない）
# ------------------------------------------------------------
class PlayModal(discord.ui.Modal, title="🎶 曲をリクエスト"):
    kyoku = discord.ui.TextInput(label="URL または 検索語", placeholder="https://… か「lofi hiphop」など（Spotify・プレイリストURLもOK）",
                                 max_length=300)

    async def on_submit(self, interaction):
        await interaction.response.defer(ephemeral=True)
        await do_play(interaction, str(self.kyoku.value), eph=True)

class MusicPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="リクエスト", emoji="🎶", style=discord.ButtonStyle.primary, custom_id="sk_mu_play", row=0)
    async def b_play(self, interaction, button):
        await interaction.response.send_modal(PlayModal())

    @discord.ui.button(label="BGM", emoji="🎵", style=discord.ButtonStyle.secondary, custom_id="sk_mu_bgm", row=0)
    async def b_bgm(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(await start_bgm(), ephemeral=True)

    @discord.ui.button(label="環境音", emoji="🌿", style=discord.ButtonStyle.secondary, custom_id="sk_mu_amb", row=0)
    async def b_ambient(self, interaction, button):
        await send_ambient_picker(interaction)

    @discord.ui.button(label="よぶ", emoji="🎧", style=discord.ButtonStyle.secondary, custom_id="sk_mu_join", row=0)
    async def b_join(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        prefer = interaction.user.voice.channel if getattr(interaction.user, "voice", None) and interaction.user.voice else None
        vc = await ensure_player_vc(prefer)
        if not vc:
            await interaction.followup.send("❌ VCに接続できませんでした。どこかのVCに入ってから押してね。", ephemeral=True)
            return
        _player["vc"] = vc
        asyncio.create_task(warmup_player("パネル🎧"))
        await interaction.followup.send(f"🎧 {vc.channel.mention} で待機します（退出は ⏹）。", ephemeral=True)

    @discord.ui.button(label="一時停止/再開", emoji="⏯", style=discord.ButtonStyle.secondary, custom_id="sk_mu_pause", row=1)
    async def b_pause(self, interaction, button):
        vc = _player.get("vc")
        if vc and vc.is_playing():
            vc.pause()
            await interaction.response.send_message("⏸ 一時停止しました（もう一度押すと再開）。", ephemeral=True)
        elif vc and vc.is_paused():
            vc.resume()
            await interaction.response.send_message("▶ 再開しました。", ephemeral=True)
        else:
            await interaction.response.send_message("いま何も流れていません。", ephemeral=True)

    @discord.ui.button(label="スキップ", emoji="⏭", style=discord.ButtonStyle.secondary, custom_id="sk_mu_skip", row=1)
    async def b_skip(self, interaction, button):
        vc = _player.get("vc")
        if not vc or not vc.is_playing():
            await interaction.response.send_message("いま何も流れていません。", ephemeral=True)
            return
        await interaction.response.send_message("⏭ スキップ！" + (f"（次：{_player['queue'][0]['title']}）" if _player["queue"] else ""), ephemeral=True)
        vc.stop()   # afterコールバックが次の曲を再生する

    @discord.ui.button(label="キュー", emoji="📃", style=discord.ButtonStyle.secondary, custom_id="sk_mu_queue", row=1)
    async def b_queue(self, interaction, button):
        await interaction.response.send_message(queue_text() or "キューは空です。🎶 リクエストで追加してね。", ephemeral=True)

    @discord.ui.button(label="退出", emoji="⏹", style=discord.ButtonStyle.danger, custom_id="sk_mu_stop", row=1)
    async def b_stop(self, interaction, button):
        await stop_player()
        await interaction.response.send_message("⏹ ぜんぶ止めてVCから退出しました。", ephemeral=True)

    @discord.ui.button(label="みんなのプレイリスト", emoji="📂", style=discord.ButtonStyle.secondary, custom_id="sk_mu_lists", row=2)
    async def b_lists(self, interaction, button):
        rows = await pl_all()
        head = "📂 **みんなのプレイリスト**（選ぶとまとめてキューに入ります）" if rows else \
               "📂 **みんなのプレイリスト**\nまだありません。「➕ 新しく作る」が最初の1人！"
        await interaction.response.send_message(head, view=PlHubView(rows), ephemeral=True)

    @discord.ui.button(label="おすすめ曲", emoji="⭐", style=discord.ButtonStyle.secondary, custom_id="sk_mu_recs", row=2)
    async def b_recs(self, interaction, button):
        rows = await rec_all()
        await interaction.response.send_message(rec_head(rows), view=RecHubView(rows), ephemeral=True)

    @discord.ui.button(label="歌詞", emoji="📜", style=discord.ButtonStyle.secondary, custom_id="sk_mu_lyrics", row=2)
    async def b_lyrics(self, interaction, button):
        if current_track_title():   # 再生中：表示か追加・編集かを選ぶ
            await interaction.response.send_message("📜 歌詞メニュー", view=LyricsMenuView(), ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)   # 曲なし：登録・修正だけできる
        await interaction.followup.send("いま曲は流れていませんが、歌詞の登録・修正はできます。",
                                        view=LyricsHubView(await lyrics_candidate_titles()), ephemeral=True)

    @discord.ui.button(label="歌詞を同期", emoji="🎤", style=discord.ButtonStyle.secondary, custom_id="sk_mu_karaoke", row=2)
    async def b_karaoke(self, interaction, button):
        """いまの曲の歌詞をVCサブタイトルに1行ずつ流す（曲ごとにオン・曲が終わると自動オフ）"""
        if _player.get("karaoke"):   # すでにオン→補正・OFF・自動切替のコントロールを出す
            k = _player["karaoke"]
            await interaction.response.send_message(f"🎤 歌詞同期中（ずれ補正：歌詞を {k.get('offset', 0.0):+.0f} 秒）",
                                                    view=KaraokeCtlView(await karaoke_auto_enabled()), ephemeral=True)
            return
        it = _player.get("cur_item") or {}
        title = current_track_title()
        vc = _player.get("vc")
        if not title or not vc or not (vc.is_playing() or vc.is_paused()):
            await interaction.response.send_message("いま歌詞を同期できる曲が流れていません。", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        lines, diff, src = [], None, "lrclib"
        local = await custom_lyrics_find(title)   # メンバー歌詞がLRC形式なら最優先でカラオケ化
        if local:
            lines = parse_lrc(local["text"])
            if lines:
                src = f"メンバー登録（{local['uname']}）"
            else:   # 登録歌詞はあるがタイムスタンプが無い→lrclibの（別の曲かもしれない）同期歌詞には戻らない
                await interaction.followup.send(
                    f"🎤 この曲はメンバー登録の歌詞（{local['uname']}）を使っていますが、タイムスタンプが無いので同期できません。\n"
                    "-# 下の⏺で、曲を聴きながらタイムスタンプを刻めます（📜の全文表示はそのまま使えます）",
                    view=KaraokeMakeView(clean_track_title(title)), ephemeral=True)
                return
        if not lines:
            got = await fetch_synced_lyrics(title, it.get("duration"))
            if not got:
                await interaction.followup.send(
                    "🎤 タイムスタンプ付きの歌詞が見つかりませんでした（📜の全文表示なら出るかも）。\n"
                    "-# 下の⏺＝曲を聴きながらボタンを押してタイムスタンプを刻む打ち込みモード。全文歌詞（📝登録かlrclib）があれば使えます",
                    view=KaraokeMakeView(clean_track_title(title)), ephemeral=True)
                return
            lines, diff = got
        _player["karaoke"] = {"lines": lines, "offset": 0.0, "auto": False, "item": _player.get("cur_item")}
        _player["karaoke"]["task"] = asyncio.create_task(karaoke_task())
        warn = ""
        if diff is not None and diff > 3:
            warn = f"\n⚠️ 歌詞の元音源と動画の長さが {diff:.0f} 秒違います（MVのイントロ等）。ずれたら下のボタンで補正してね"
        elif diff is None and src == "lrclib":
            warn = "\n-# 動画の長さと照合できませんでした。ずれたら下のボタンで補正してね"
        await interaction.followup.send(f"🎤 歌詞の同期表示をはじめました（歌詞: {src}・曲が終わると自動で戻ります）{warn}",
                                        view=KaraokeCtlView(await karaoke_auto_enabled()), ephemeral=True)

VIEW_FACTORY["worklog"] = MusicPanelView

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
    try:
        await reconcile_work_sessions()   # 再起動中に入退室があっても作業セッションを立て直す
    except Exception as e:
        print(f"作業部屋の再同期エラー: {e!r}", flush=True)
    try:
        await backfill_meal_msg_ids()   # アルバム機能より前の写真記録にmsg_idを後付け（初回だけ）
    except Exception as e:
        print(f"ごはん写真の後付けエラー: {e!r}", flush=True)
    asyncio.create_task(warmup_player("起動"))   # 起動直後にPO Token/解読キャッシュを温めておく
    if not judge_loop.is_running():
        judge_loop.start()
    if not radio_loop.is_running():
        radio_loop.start()
    if not remind_loop.is_running():
        remind_loop.start()
    await load_role_msgs()
    await load_break()
    if not _ops_hello["done"]:   # 起動の知らせ（デプロイ・自動再起動が正しく終わったかの確認用）
        _ops_hello["done"] = True
        ver = await asyncio.to_thread(_ytdlp_installed_version)
        await bot_alert(f"🟢 起動しました（yt-dlp {ver or '不明'}・Cookie {'あり' if os.path.exists(YTDLP_COOKIES) else '**なし**'}）")
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
    """JSONの科目マスタをDBへ取り込む（起動時・冪等）。自由入力の科目は残す。
    courses_extra.json＝PDFの枠外・備考方式などで機械抽出できない科目の手動追加ファイル（同じ形式）"""
    if not os.path.exists(COURSES_JSON):
        print(f"科目マスタが見つかりません: {COURSES_JSON}", flush=True)
        return 0
    total = 0
    loaded_codes = set()
    for path in (COURSES_JSON, COURSES_EXTRA):
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
        for r in rows:
            loaded_codes.add(r["code"])
            await db.execute(
                "INSERT INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) VALUES(?,?,?,?,?,?,?,?,?,?,?,0) "
                "ON CONFLICT(code) DO UPDATE SET name=excluded.name,nname=excluded.nname,teacher=excluded.teacher,room=excluded.room,"
                "faculty=excluded.faculty,dept=excluded.dept,cls=excluded.cls,year=excluded.year,slots=excluded.slots,term=excluded.term",
                (r["code"], r["name"], norm_text(r["name"]), r.get("teacher") or "", r.get("room") or "", r.get("faculty") or "",
                 r.get("dept") or "", r.get("cls") or "", r.get("year"), ",".join(r.get("slots") or []), r.get("term") or ""))
        total += len(rows)
    await db.commit()
    print(f"科目マスタ {total} 件を読み込みました", flush=True)
    if len(loaded_codes) >= 100:   # マスタ読み込み失敗時に全消ししないためのガード
        try:
            await prune_removed_courses(loaded_codes)
        except Exception as e:
            print(f"旧コード掃除エラー: {e!r}", flush=True)
    try:
        await cleanup_label_customs()
    except Exception as e:
        print(f"ラベル丸写し科目の掃除エラー: {e!r}", flush=True)
    return total

async def prune_removed_courses(loaded_codes):
    """改番・分割でマスタJSONから消えたコードの科目行を削除（誰かが参照しているものは安全のため残す）"""
    async with db.execute("SELECT code, name FROM courses WHERE custom=0") as c:
        rows = await c.fetchall()
    removed = 0
    for r in rows:
        if r["code"] in loaded_codes:
            continue
        referenced = False
        for t in ("user_courses", "assignments", "attendance", "recurring_kadai"):
            async with db.execute(f"SELECT 1 FROM {t} WHERE code=? LIMIT 1", (r["code"],)) as c2:
                if await c2.fetchone():
                    referenced = True
                    break
        if referenced:
            continue
        await db.execute("DELETE FROM courses WHERE code=?", (r["code"],))
        print(f"マスタから消えた科目を削除: {r['code']}「{r['name']}」", flush=True)
        removed += 1
    await db.commit()
    return removed

async def cleanup_label_customs():
    """候補ラベル丸写しで作られた自由入力科目（例「English Discussion（（池辺）・金2） 農1年」）を
    本物の科目へ解決して削除。履修・課題・欠席・定例課題の参照も本物へ付け替える"""
    async with db.execute("SELECT * FROM courses WHERE custom=1") as c:
        customs = await c.fetchall()
    if not customs:
        return 0
    async with db.execute("SELECT * FROM courses WHERE custom=0") as c:
        labels = {norm_text(course_label(r)): r["code"] for r in await c.fetchall()}
    fixed = 0
    for cu in customs:
        real = labels.get(cu["nname"])
        if not real:
            continue
        await db.execute("UPDATE OR IGNORE user_courses SET code=? WHERE code=?", (real, cu["code"]))
        await db.execute("UPDATE OR IGNORE attendance SET code=? WHERE code=?", (real, cu["code"]))
        await db.execute("UPDATE assignments SET code=? WHERE code=?", (real, cu["code"]))
        await db.execute("UPDATE recurring_kadai SET code=? WHERE code=?", (real, cu["code"]))
        await db.execute("DELETE FROM user_courses WHERE code=?", (cu["code"],))   # 付け替えで重複した残り
        await db.execute("DELETE FROM attendance WHERE code=?", (cu["code"],))
        await db.execute("DELETE FROM courses WHERE code=?", (cu["code"],))
        print(f"ラベル丸写し科目を {cu['code']}「{cu['name']}」→ {real} に解決して削除", flush=True)
        fixed += 1
    await db.commit()
    return fixed

def course_label(c, with_code=False):
    """オートコンプリート／表示用の1行ラベル（100字以内）"""
    bits = []
    if c["teacher"]:
        bits.append(c["teacher"])
    if c["slots"]:
        bits.append(c["slots"])
    tag = ""
    if c["faculty"]:
        tag = c["faculty"] + (f"{c['year']}年" if c["year"] else "")   # 学科・クラス記号は出さない（他学科でも履修できる科目が紛らわしいため）
    s = c["name"] + (f"（{'・'.join(bits)}）" if bits else "") + (f" {tag}" if tag else "") + (f" [{c['code']}]" if with_code else "")
    return s[:100]

async def search_courses(q, limit=25):
    """スペース区切りの複数語で絞り込み検索。各語は 科目名・コード・教員・曜日時限・学科・クラス・学部 のどれかに
    部分一致すればOK（AND）。同名科目が大量にあるとき『academic 畠山』『writing 月1』のように絞れる"""
    terms = [t for t in re.split(r"\s+", unicodedata.normalize("NFKC", q or "").strip()) if t]
    if not terms:
        async with db.execute("SELECT * FROM courses ORDER BY custom DESC, faculty, year, name LIMIT ?", (limit,)) as c:
            return await c.fetchall()
    conds, args = [], []
    for t in terms:
        conds.append("(nname LIKE ? OR code LIKE ? OR IFNULL(teacher,'') LIKE ? OR IFNULL(slots,'') LIKE ? "
                     "OR IFNULL(dept,'') LIKE ? OR IFNULL(cls,'') LIKE ? OR IFNULL(faculty,'') LIKE ?)")
        args += [f"%{t.casefold()}%"] + [f"%{t}%"] * 6
    async with db.execute(
        "SELECT * FROM courses WHERE " + " AND ".join(conds) +
        " ORDER BY CASE WHEN nname LIKE ? THEN 0 ELSE 1 END, custom DESC, faculty, year, name, code LIMIT ?",
        args + [f"{norm_text(terms[0])}%", limit]) as c:
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
    """自由入力の科目を登録（同名があればそれを返す。検索候補のラベル丸写しなら元の科目に解決）"""
    nn = norm_text(name)
    async with db.execute("SELECT * FROM courses WHERE nname=? ORDER BY custom LIMIT 1", (nn,)) as c:
        r = await c.fetchone()
    if r:
        return r
    # 「English Discussion（（池辺）・金2） 農1年」のように候補ラベルごと貼られたケース
    # → 時限なしの自由入力科目を作らず、ラベルが一致する既存科目に解決する
    async with db.execute("SELECT * FROM courses WHERE custom=0") as c:
        for r in await c.fetchall():
            if norm_text(course_label(r)) == nn:
                return r
    code = "x%08x" % (zlib.crc32(nn.encode("utf-8")) & 0xffffffff)
    await db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) VALUES(?,?,?,'','','','','',NULL,'','',1)",
                     (code, name.strip()[:60], nn))
    await db.commit()
    return await get_course(code)

def parse_due(s, now=None):
    """'10/15' '10/15 23:59' '10月15日' '2026/10/15 17:00' → datetime(JST)。不正なら None。時刻省略は 23:59"""
    t = unicodedata.normalize("NFKC", s or "").strip()
    m = re.fullmatch(r"(?:(\d{4})[/年])?\s*(\d{1,2})[/月]\s*(\d{1,2})日?\s*(?:(\d{1,2})[:時]\s*(\d{2})?分?)?", t)
    if not m:
        return None
    now = now or now_jst()
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
    """対応済み（✅完了＋🚫提出しない）の人。リマインドや未完了一覧から外す判定に使う"""
    async with db.execute("SELECT user_id FROM assignment_done WHERE assignment_id=?", (aid,)) as c:
        return {r["user_id"] for r in await c.fetchall()}

async def skip_set(aid):
    """🚫「提出しない」を選んだ人（選択式課題で別のものを出す等）"""
    async with db.execute("SELECT user_id FROM assignment_done WHERE assignment_id=? AND skip=1", (aid,)) as c:
        return {r["user_id"] for r in await c.fetchall()}

async def kadai_mark(aid, uid, skip):
    """✅完了（skip=0）か🚫提出しない（skip=1）として記録（どちらか一方・上書き）"""
    await db.execute("INSERT INTO assignment_done(assignment_id,user_id,skip) VALUES(?,?,?) "
                     "ON CONFLICT(assignment_id,user_id) DO UPDATE SET skip=excluded.skip", (aid, str(uid), 1 if skip else 0))

class SkipButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_kadai_skip:(?P<id>\d+)"):
    """選択式の課題など「自分は出さない」ものを外す（もう一度押すと取り消し）"""

    def __init__(self, aid):
        super().__init__(discord.ui.Button(label="🚫 提出しない", style=discord.ButtonStyle.secondary, custom_id=f"sk_kadai_skip:{aid}"))
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
        if uid in await skip_set(self.aid):
            await db.execute("DELETE FROM assignment_done WHERE assignment_id=? AND user_id=?", (self.aid, uid))
            msg = f"⬜ 「{a['title']}」の「提出しない」を取り消しました（また通知されます）。"
        else:
            await kadai_mark(self.aid, uid, True)
            msg = f"🚫 「{a['title']}」は提出しないことにしました。リマインドも止まります（取り消しはもう一度押す）。"
        await db.commit()
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await maybe_celebrate(self.aid)
        except Exception as e:
            print(f"全員提出祝福エラー: {e!r}", flush=True)

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
        if uid in done and uid not in await skip_set(self.aid):
            await db.execute("DELETE FROM assignment_done WHERE assignment_id=? AND user_id=?", (self.aid, uid))
            msg = f"⬜ 「{a['title']}」を未完了に戻しました。"
        else:   # 未着手 or 🚫提出しない→やっぱり出した
            await kadai_mark(self.aid, uid, False)
            await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (uid, a["code"]))  # 押した人は履修者扱い
            msg = f"✅ 「{a['title']}」を完了にしました。おつかれさま！"
        await db.commit()
        await interaction.response.send_message(msg, ephemeral=True)
        try:
            await maybe_celebrate(self.aid)
        except Exception as e:
            print(f"全員提出祝福エラー: {e!r}", flush=True)
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
    view.add_item(SkipButton(a["id"]))
    msg = await ch.send(f"{header}\n{mention}", embed=kadai_embed(a, course, takers, done), view=view)
    await bump_panel("kadai")
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
        if stage == "d3":   # 毎週ある定例課題の3日前通知は騒がしいだけなので鳴らさない
            async with db.execute("SELECT 1 FROM recurring_spawned WHERE assignment_id=?", (a["id"],)) as c2:
                if await c2.fetchone():
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
        await spawn_recurring()   # 定例課題を先に生やしてからリマインド判定
    except Exception as e:
        print(f"teiki spawn error: {e!r}", flush=True)
    try:
        await remind_assignments()
    except Exception as e:
        print(f"remind error: {e!r}", flush=True)
    try:
        await remind_exams()   # 試験のグラデーション通知（30日前/1週間前/3日前/前日/当日）
    except Exception as e:
        print(f"exam remind error: {e!r}", flush=True)
    try:
        await fridge_remind()
    except Exception as e:
        print(f"fridge remind error: {e!r}", flush=True)
    try:
        await univ_event_remind()   # 🏫 大学の予定（3日前・開始日・履修登録の締切2日前）
        await shared_event_remind()  # 📣 みんなに見せる予定（3日前・当日に本人へ声かけ）
        await private_cleanup(day)   # 🙋 終わったじぶんの予定は消す
    except Exception as e:
        print(f"univ event remind error: {e!r}", flush=True)
    try:
        await memo_cleanup(day)   # 引き継がれなかったメモは朝に消える
    except Exception as e:
        print(f"memo cleanup error: {e!r}", flush=True)
    try:
        await goods_remind()   # 生活用品の交換期限アラート
    except Exception as e:
        print(f"goods remind error: {e!r}", flush=True)

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
@app_commands.describe(kamoku="科目名で検索（『academic 畠山』『writing 月1』のように教員名・曜日時限を混ぜて絞り込みOK）",
                       kamoku2="2つ目", kamoku3="3つ目", kamoku4="4つ目", kamoku5="5つ目")
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

def _row_get(c, k):
    return c[k] if k in c.keys() else None

def _wrap_jp(s, width, max_lines):
    """全角=1・半角=0.55 の見た目幅で折り返し（英語科目名が早々に切れないように）"""
    lines, cur, w = [], "", 0.0
    for ch in s:
        cw_ = 1.0 if ord(ch) > 0x2500 else 0.55
        if w + cw_ > width and cur:
            lines.append(cur)
            cur, w = "", 0.0
            if len(lines) == max_lines:
                return lines
        cur += ch
        w += cw_
    if cur and len(lines) < max_lines:
        lines.append(cur)
    return lines

def render_timetable(username, rows):
    """履修科目から時間割カードPNG（通信簿カードと同じブランド配色）。時限情報のある科目が無ければ None"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        import matplotlib.patheffects as _pe
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fp = font_manager.FontProperties(fname=f)
    fpb = font_manager.FontProperties(fname=_find_cjk_font_bold() or f)
    cells, used_days, max_p = {}, set(), 4
    for i, c in enumerate(rows):
        for slot in (c["slots"] or "").split(","):
            slot = slot.strip()
            if len(slot) >= 2 and slot[0] in DAY_CHARS and slot[1:].isdigit():
                di, p = DAY_CHARS.index(slot[0]), int(slot[1:])
                cells.setdefault((di, p), []).append((i, c))
                used_days.add(di)
                max_p = max(max_p, p)
    if not cells:
        return None
    day_idxs = [d for d in range(7) if d < 5 or d in used_days]   # 月〜金＋使っていれば土日
    n_d, n_p = len(day_idxs), max_p
    cw, chh = 1.5, 1.04
    left_w, head_h, title_h = 0.62, 0.36, 0.52
    fig_w = left_w + cw * n_d + 0.22
    fig_h = title_h + head_h + chh * n_p + 0.22
    fig = plt.figure(figsize=(fig_w, fig_h), dpi=150)
    fig.patch.set_facecolor(CARD_CREAM)
    terms = [t for t in (_row_get(c, "term") for c in rows) if t]
    term = max(set(terms), key=terms.count) if terms else ""
    t = fig.text(left_w / fig_w, 1 - 0.28 / fig_h, f"{_plain_name(username, 12)} の時間割", fontproperties=fpb,
                 fontsize=13.5, color=CARD_INK, va="center")
    t.set_path_effects([_pe.withStroke(linewidth=0.6, foreground=CARD_INK)])
    if term:
        fig.text(1 - 0.22 / fig_w, 1 - 0.28 / fig_h, str(term), fontproperties=fp, fontsize=10, color=CARD_ORANGE, va="center", ha="right")
    ax = fig.add_axes([left_w / fig_w, 0.14 / fig_h, cw * n_d / fig_w, chh * n_p / fig_h])
    ax.set_xlim(0, n_d)
    ax.set_ylim(0, n_p)
    ax.invert_yaxis()
    ax.axis("off")
    # 曜日ヘッダー
    for xi, di in enumerate(day_idxs):
        fx = (left_w + cw * (xi + 0.5)) / fig_w
        fy = 1 - (title_h + head_h * 0.5) / fig_h
        fig.text(fx, fy, DAY_CHARS[di], fontproperties=fpb, fontsize=11.5, color=CARD_INK, ha="center", va="center")
    # 時限ラベル（左端：時限番号＋開始時刻）
    for p in range(1, n_p + 1):
        fy = 1 - (title_h + head_h + chh * (p - 0.5)) / fig_h
        fig.text((left_w * 0.5) / fig_w, fy + 0.075 / fig_h, str(p), fontproperties=fpb, fontsize=11, color=CARD_INK, ha="center", va="center")
        st = PERIOD_START.get(p, "")
        if st:
            fig.text((left_w * 0.5) / fig_w, fy - 0.115 / fig_h, st, fontproperties=fp, fontsize=7.5, color=CARD_MUTED, ha="center", va="center")
    # 空グリッド
    for xi in range(n_d):
        for p in range(n_p):
            ax.add_patch(plt.Rectangle((xi, p), 1, 1, facecolor="#f2ecdd", edgecolor=CARD_CREAM, linewidth=2.0))
    # 授業セル（科目ごとに色チップ。同じコマに複数あれば分割）
    for (di, p), lst in sorted(cells.items()):
        xi = day_idxs.index(di)
        n = len(lst)
        for j, (ci, c) in enumerate(lst):
            col = CARD_SERIES[ci % len(CARD_SERIES)]
            y0, hh = p - 1 + j / n, 1 / n
            ax.add_patch(plt.Rectangle((xi, y0), 1, hh, facecolor="#fffdf6", edgecolor=CARD_CREAM, linewidth=2.0))
            ax.add_patch(plt.Rectangle((xi, y0), 0.05, hh, facecolor=col, edgecolor="none"))
            nm = _plain_name(c["name"], 24)
            lines = _wrap_jp(nm, 7.2, 3 if n == 1 else 2)
            ax.text(xi + 0.11, y0 + 0.10 / n, "\n".join(lines), fontproperties=fpb,
                    fontsize=8.5 if n == 1 else 7, color=CARD_INK, va="top", ha="left", linespacing=1.25)
            room = c["room"]
            if n == 1 and room:
                ax.text(xi + 0.11, y0 + hh - 0.10, _plain_name(str(room), 11), fontproperties=fp,
                        fontsize=7.5, color=CARD_MUTED, va="bottom", ha="left")
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf

def parse_slots_input(s):
    """「金3」「金3,金4」→ ['金3','金4']。読めなければ None"""
    t = unicodedata.normalize("NFKC", s or "").strip()
    out = []
    for p in re.split(r"[,、\s]+", t):
        if not p:
            continue
        if not re.fullmatch(r"[月火水木金土日][1-6]", p):
            return None
        if p not in out:
            out.append(p)
    return out or None

async def personal_slot_rows(uid):
    """空きコマの個人予定を時間割カード用の行に（同じ予定名はまとめて同色に）"""
    async with db.execute("SELECT slot, label, place FROM user_slots WHERE user_id=? ORDER BY slot", (str(uid),)) as c:
        rows = await c.fetchall()
    by = {}
    for r in rows:
        by.setdefault((r["label"], r["place"] or ""), []).append(r["slot"])
    return [{"name": lb, "slots": ",".join(sl), "room": pl or None, "term": None} for (lb, pl), sl in by.items()]

async def send_timetable(interaction):
    rows = list(await user_course_rows(interaction.user.id))
    personal = await personal_slot_rows(interaction.user.id)
    if not rows and not personal:
        await interaction.response.send_message("まだ履修科目がありません。「🎓 履修科目を登録」か `/jikanwari add` からどうぞ！", ephemeral=True)
        return
    buf = await asyncio.to_thread(render_timetable, interaction.user.display_name, rows + personal)
    unslotted = [r["name"] for r in rows
                 if not any(len(s.strip()) >= 2 and s.strip()[0] in DAY_CHARS and s.strip()[1:].isdigit()
                            for s in (r["slots"] or "").split(","))]
    if not buf:
        await interaction.response.send_message("時限情報のある科目が無いため、時間割を描けませんでした（自由入力の科目には時限がありません）。", ephemeral=True)
        return
    note = f"\n-# 時限が未登録のため載っていない科目：{', '.join(unslotted)}" if unslotted else ""
    await interaction.response.send_message("🗓 いまの履修科目から時間割を作りました。" + note,
                                            file=discord.File(buf, filename="jikanwari.png"), ephemeral=True)

@jikanwari.command(name="hyou", description="履修科目から時間割の画像を書き出す（自分にだけ表示）")
async def jikanwari_hyou(interaction):
    await send_timetable(interaction)

# ---- 👥 時間割コピー（同じ学科の先人の履修を丸ごともらって過不足を調整） ----
async def copy_courses(dst_uid, src_uid):
    """srcの履修をdstへマージコピー（既存はそのまま）。(追加数, コピー元の科目数) を返す"""
    rows = await user_course_rows(src_uid)
    added = 0
    for r in rows:
        cur = await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(dst_uid), r["code"]))
        added += 1 if cur.rowcount else 0
    await db.commit()
    return added, len(rows)

async def timetable_owners(exclude_uid):
    """履修登録している人の一覧（名前・科目数・主な学部学年タグ）。コピー元候補用"""
    async with db.execute("SELECT user_id, COUNT(*) AS n FROM user_courses GROUP BY user_id ORDER BY n DESC LIMIT 30") as c:
        rows = await c.fetchall()
    out = []
    for r in rows:
        if r["user_id"] == str(exclude_uid):
            continue
        usr = await get_user(r["user_id"])
        async with db.execute("SELECT c.faculty || IFNULL(c.year,'') AS fy, COUNT(*) AS m FROM user_courses u "
                              "JOIN courses c ON c.code=u.code WHERE u.user_id=? AND c.faculty!='' "
                              "GROUP BY fy ORDER BY m DESC LIMIT 1", (r["user_id"],)) as c2:
            f = await c2.fetchone()
        tag = f"{f['fy']}年・" if f and f["fy"] else ""
        out.append((r["user_id"], (usr["name"] if usr else "？"), r["n"], tag))
    return out[:25]

class CopyTrimSelect(discord.ui.Select):
    """コピー直後の「いらない科目を外す」プルダウン"""
    def __init__(self, rows):
        opts = [discord.SelectOption(label=course_label(r)[:100], value=r["code"]) for r in rows[:25]]
        super().__init__(placeholder="🗑 いらない科目を外す（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        removed = []
        for code in self.values:
            c = await get_course(code)
            await db.execute("DELETE FROM user_courses WHERE user_id=? AND code=?", (uid, code))
            removed.append(c["name"] if c else code)
        await db.commit()
        left = len(await user_course_rows(uid))
        await interaction.response.edit_message(
            content=f"🗑 {len(removed)}科目を外しました（{('、'.join(removed))[:150]}）。現在 {left} 科目。\n"
                    f"仕上がりは 🗓 時間割画像で確認、さらに外すなら `/jikanwari remove` で。", view=None)

async def do_copy_timetable(interaction, src_uid):
    me = str(interaction.user.id)
    if src_uid == me:
        await interaction.response.send_message("自分の時間割は…もう持ってます🙃", ephemeral=True)
        return
    src = await get_user(src_uid)
    src_name = (src["name"] if src else None) or "その人"
    added, total = await copy_courses(me, src_uid)
    if total == 0:
        await interaction.response.send_message(f"**{src_name}** さんはまだ履修科目を登録していません。", ephemeral=True)
        return
    mine = await user_course_rows(me)
    buf = await asyncio.to_thread(render_timetable, interaction.user.display_name, mine)
    view = discord.ui.View(timeout=600)
    view.add_item(CopyTrimSelect(mine))
    txt = (f"👥 **{src_name}** さんの時間割をコピーしました：追加 **{added}** 科目"
           + (f"（{total - added} 科目は登録済みでスキップ）" if total - added else "") + f"、現在 **{len(mine)}** 科目。\n"
           "選択科目の違いなどは下のプルダウンで外して調整（追加は 🎓、あとからは `/jikanwari remove`）。")
    kwargs = {"ephemeral": True, "view": view}
    if buf:
        kwargs["file"] = discord.File(buf, filename="jikanwari.png")
    if interaction.response.is_done():
        await interaction.followup.send(txt, **kwargs)
    else:
        await interaction.response.send_message(txt, **kwargs)

class CopySrcSelect(discord.ui.Select):
    def __init__(self, cands):
        opts = [discord.SelectOption(label=f"{name}（{tag}{n}科目）"[:100], value=uid) for uid, name, n, tag in cands]
        super().__init__(placeholder="👥 誰の時間割をコピーする？", options=opts)

    async def callback(self, interaction):
        await do_copy_timetable(interaction, self.values[0])

@jikanwari.command(name="copy", description="他のメンバーの時間割をコピーして、あとから過不足を調整（同じ学科の先人におすすめ）")
@app_commands.describe(hito="コピー元のメンバー")
async def jikanwari_copy(interaction, hito: discord.Member):
    await ensure_user(interaction.user)
    await do_copy_timetable(interaction, str(hito.id))

# ---- 🚨 欠席カウンター（落単ラインまであと何回かを数える。判定はしない） ----
KESSEKI_DEFAULT = 4   # 落単ラインの既定値（/kesseki set で科目ごとに変更可）

async def kesseki_row(uid, code):
    async with db.execute("SELECT * FROM attendance WHERE user_id=? AND code=?", (str(uid), code)) as c:
        return await c.fetchone()

async def kesseki_map(uid):
    async with db.execute("SELECT * FROM attendance WHERE user_id=?", (str(uid),)) as c:
        return {r["code"]: r for r in await c.fetchall()}

async def kesseki_bump(uid, code, delta=1):
    await db.execute("INSERT INTO attendance(user_id,code,absent,limit_n) VALUES(?,?,?,?) "
                     "ON CONFLICT(user_id,code) DO UPDATE SET absent=MAX(0, absent+?)",
                     (str(uid), code, max(0, delta), KESSEKI_DEFAULT, delta))
    await db.commit()
    return await kesseki_row(uid, code)

async def kesseki_set_limit(uid, code, n):
    await db.execute("INSERT INTO attendance(user_id,code,absent,limit_n) VALUES(?,?,0,?) "
                     "ON CONFLICT(user_id,code) DO UPDATE SET limit_n=?",
                     (str(uid), code, n, n))
    await db.commit()
    return await kesseki_row(uid, code)

def kesseki_text(name, r):
    left = r["limit_n"] - r["absent"]
    s = f"🚨 **{name}**　欠席 {r['absent']}/{r['limit_n']}　"
    if left <= 0:
        return s + "**落単ラインに到達…！先生に相談しよう🙏**"
    if left == 1:
        return s + "**次で落単ライン！あと1回まで！**"
    return s + f"欠席はあと **{left}回** まで！"

class KessekiSelect(discord.ui.Select):
    def __init__(self, rows, counts):
        opts = []
        for r in rows[:25]:
            k = counts.get(r["code"])
            extra = f"　欠席{k['absent']}/{k['limit_n']}" if k else ""
            opts.append(discord.SelectOption(label=(r["name"] + extra)[:100], value=r["code"]))
        super().__init__(placeholder="🚨 欠席した科目を選ぶ（+1）", options=opts)

    async def callback(self, interaction):
        c = await get_course(self.values[0])
        r = await kesseki_bump(interaction.user.id, self.values[0])
        rows = await user_course_rows(interaction.user.id)
        counts = await kesseki_map(interaction.user.id)
        view = discord.ui.View(timeout=600)
        if rows:
            view.add_item(KessekiSelect(rows, counts))
        await interaction.response.edit_message(
            content=kesseki_text(c["name"] if c else self.values[0], r) + "\n-# 間違えたら `/kesseki add kaisu:-1` で戻せます",
            view=view)

kesseki_grp = app_commands.Group(name="kesseki", description="欠席カウンター（落単ラインまであと何回かを数える）")

@kesseki_grp.command(name="add", description="欠席を記録（+1。間違えたら kaisu:-1 で戻す）")
@app_commands.describe(kamoku="科目（自分の履修から）", kaisu="増減 例 1／-1（省略で+1）")
@app_commands.autocomplete(kamoku=ac_my_courses)
async def kesseki_add(interaction, kamoku: str, kaisu: int = 1):
    await ensure_user(interaction.user)
    c = await get_course(kamoku)
    if not c:
        await interaction.response.send_message("科目は候補から選んでください（先に履修登録）。", ephemeral=True)
        return
    r = await kesseki_bump(interaction.user.id, c["code"], max(-10, min(10, kaisu)))
    await interaction.response.send_message(kesseki_text(c["name"], r) + hitokoto_suffix(), ephemeral=True)

@kesseki_grp.command(name="set", description="落単ライン（何回欠席でアウトか）を科目ごとに設定（既定4回）")
@app_commands.describe(kamoku="科目（自分の履修から）", kaisu="落単になる欠席回数 例 4")
@app_commands.autocomplete(kamoku=ac_my_courses)
async def kesseki_set(interaction, kamoku: str, kaisu: int):
    await ensure_user(interaction.user)
    c = await get_course(kamoku)
    if not c:
        await interaction.response.send_message("科目は候補から選んでください（先に履修登録）。", ephemeral=True)
        return
    r = await kesseki_set_limit(interaction.user.id, c["code"], max(1, min(30, kaisu)))
    left = max(0, r["limit_n"] - r["absent"])
    await interaction.response.send_message(
        f"🚨 **{c['name']}** の落単ラインを **{r['limit_n']}回** に設定（現在 欠席{r['absent']}・あと{left}回）", ephemeral=True)

@kesseki_grp.command(name="list", description="欠席カウンターの一覧（自分にだけ表示）")
async def kesseki_list(interaction):
    async with db.execute("SELECT a.*, c.name AS cname FROM attendance a JOIN courses c ON c.code=a.code "
                          "WHERE a.user_id=? ORDER BY (a.limit_n - a.absent), c.name", (str(interaction.user.id),)) as c:
        rows = await c.fetchall()
    if not rows:
        await interaction.response.send_message("まだ欠席の記録はありません。休んだら `/kesseki add` か #課題📚 の 🚨 ボタンで数えよう（記録ゼロがいちばんえらい）。", ephemeral=True)
        return
    lines = []
    for r in rows:
        left = r["limit_n"] - r["absent"]
        mark = "🟥" if left <= 0 else ("🟧" if left == 1 else "🟩")
        lines.append(f"{mark} **{r['cname']}**　{r['absent']}/{r['limit_n']}（あと{max(0, left)}回）")
    await interaction.response.send_message("🚨 **欠席カウンター**\n" + "\n".join(lines)[:1800], ephemeral=True)

bot.tree.add_command(kesseki_grp)

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
    await kadai_register(interaction, kamoku, kigen, naiyou, memo)

async def kadai_register(interaction, code, kigen, naiyou, memo):
    """課題を1件登録（パネルのフォームと /kadai add の共通処理）"""
    user = interaction.user
    await ensure_user(user)
    c = await get_course(code)
    if not c:
        await interaction.response.send_message("科目は候補から選んでください（先に履修登録）。", ephemeral=True)
        return
    due = parse_due(kigen)
    if not due:
        await interaction.response.send_message("⚠️ 期限の形式が読めませんでした。例: `10/15` `10/15 17:00` `10月15日`", ephemeral=True)
        return
    naiyou = (naiyou or "").strip()
    await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
    cur = await db.execute("INSERT INTO assignments(code,title,note,due_ts,created_by,created_at) VALUES(?,?,?,?,?,?)",
                           (c["code"], naiyou[:100], (memo or "").strip()[:300] or None, int(due.timestamp()), str(user.id), int(now_jst().timestamp())))
    aid = cur.lastrowid
    await db.commit()
    a = await assignment_row(aid)
    msg = await post_assignment(a, c, f"📌 **{user.display_name}** が課題を登録しました")
    if msg:
        await db.execute("UPDATE assignments SET msg_id=? WHERE id=?", (str(msg.id), aid))
        await db.commit()
    n = len(await takers_of(c["code"]))
    await interaction.response.send_message(f"✅ 登録しました：**{c['name']}**「{naiyou}」 期限 {fmt_due(due)}（履修者 {n} 人に通知。3日前・前日・当日朝にもリマインドします）", ephemeral=True)

@kadai.command(name="list", description="自分の履修科目の課題一覧（期限順・プルダウンで✅）")
async def kadai_list(interaction):
    emb = await kadai_list_embed(str(interaction.user.id))
    if not emb:
        await interaction.response.send_message("いま登録されている課題はありません 🎉", ephemeral=True)
        return
    view = await kadai_list_view(str(interaction.user.id))
    await interaction.response.send_message(embed=emb, view=view or discord.ui.View(), ephemeral=True)

async def kadai_list_embed(uid):
    """自分の履修科目の課題一覧embed（パネルと /kadai list の共通処理）。無ければ None"""
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND (a.created_by=? OR a.code IN (SELECT code FROM user_courses WHERE user_id=?)) ORDER BY a.due_ts",
        (uid, uid)) as c:
        rows = await c.fetchall()
    if not rows:
        return None
    lines = []
    for a in rows:
        done = uid in await done_set(a["id"])
        skipped = done and uid in await skip_set(a["id"])
        due = datetime.fromtimestamp(a["due_ts"], JST)
        left = days_left(due)
        if done and due < now_jst():
            continue   # 完了済みで期限（時刻まで）も過ぎた課題は見せない（自動クローズを待たず一覧から消す）
        if skipped:   # 🚫提出しないは薄く1行だけ（取り消せるように残す）
            lines.append(f"-# 🚫 {a['cname']}：{a['title']}（提出しない）")
            continue
        lines.append(f"{'✅' if done else '⬜'} **{a['cname']}**：{a['title']}　{fmt_due(due)}" + ("" if done else f"（{'今日！' if left == 0 else ('期限切れ！' if left < 0 else f'あと{left}日')}）"))
    if not lines:
        return None
    return discord.Embed(title="📚 課題一覧", description="\n".join(lines)[:4000], color=discord.Color.gold())

async def kadai_undone_rows(uid):
    """自分の未完了の課題（チェック用プルダウンの中身）"""
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND (a.created_by=? OR a.code IN (SELECT code FROM user_courses WHERE user_id=?)) "
        "AND a.id NOT IN (SELECT assignment_id FROM assignment_done WHERE user_id=?) ORDER BY a.due_ts",
        (uid, uid, uid)) as c:
        return await c.fetchall()

class KadaiDoneSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = []
        for r in rows[:25]:
            due = datetime.fromtimestamp(r["due_ts"], JST)
            opts.append(discord.SelectOption(label=f"{r['cname']}：{r['title']}"[:80] + f"（{due.month}/{due.day}）", value=str(r["id"])))
        super().__init__(placeholder="✅ 終わった課題にチェック（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        for v in self.values:
            await kadai_mark(int(v), uid, False)
        await db.commit()
        for v in self.values:
            try:
                await maybe_celebrate(int(v))
            except Exception as e:
                print(f"全員提出祝福エラー: {e!r}", flush=True)
        emb = await kadai_list_embed(uid)
        view = await kadai_list_view(uid)
        await interaction.response.edit_message(content=f"✅ {len(self.values)} 件を完了にしました{'。ぜんぶ片付いた🎉' if view is None else '！'}",
                                                embed=emb, view=view or discord.ui.View())

class KadaiSkipSelect(discord.ui.Select):
    """選択式の課題など、自分は出さないものを外す（リマインド・一覧から消える）"""

    def __init__(self, rows):
        opts = []
        for r in rows[:25]:
            due = datetime.fromtimestamp(r["due_ts"], JST)
            opts.append(discord.SelectOption(label=f"{r['cname']}：{r['title']}"[:80] + f"（{due.month}/{due.day}）", value=str(r["id"])))
        super().__init__(placeholder="🚫 提出しない課題を選ぶ（選択式の課題など・複数OK）", options=opts,
                         min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        for v in self.values:
            await kadai_mark(int(v), uid, True)
        await db.commit()
        for v in self.values:
            try:
                await maybe_celebrate(int(v))
            except Exception as e:
                print(f"全員提出祝福エラー: {e!r}", flush=True)
        emb = await kadai_list_embed(uid)
        view = await kadai_list_view(uid)
        await interaction.response.edit_message(
            content=f"🚫 {len(self.values)} 件を「提出しない」にしました（リマインドも止まります）。取り消しは下の↩から",
            embed=emb, view=view or discord.ui.View())

class KadaiUnskipSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"{r['cname']}：{r['title']}"[:100], value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="↩ 「提出しない」を取り消す", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        await db.executemany("DELETE FROM assignment_done WHERE assignment_id=? AND user_id=? AND skip=1",
                             [(int(v), uid) for v in self.values])
        await db.commit()
        emb = await kadai_list_embed(uid)
        view = await kadai_list_view(uid)
        await interaction.response.edit_message(content=f"↩ {len(self.values)} 件を未完了に戻しました。",
                                                embed=emb, view=view or discord.ui.View())

class KadaiDeferSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = []
        for r in rows[:25]:
            due = datetime.fromtimestamp(r["due_ts"], JST)
            opts.append(discord.SelectOption(label=f"{r['cname']}：{r['title']}"[:80] + f"（{due.month}/{due.day}）", value=str(r["id"])))
        super().__init__(placeholder="🕑 期限を変更する（提出延期など）", options=opts)

    async def callback(self, interaction):
        a = await assignment_row(int(self.values[0]))
        if not a:
            await interaction.response.send_message("課題が見つかりませんでした。", ephemeral=True)
            return
        await interaction.response.send_modal(KigenHenkoModal(a))

class KigenHenkoModal(discord.ui.Modal):
    def __init__(self, a):
        super().__init__(title=f"🕑 {a['title']}"[:45])
        self.aid = a["id"]
        self.kigen = discord.ui.TextInput(label="新しい期限（例 10/15 ／ 10/15 17:00）", max_length=20)
        self.add_item(self.kigen)

    async def on_submit(self, interaction):
        due = parse_due(self.kigen.value)
        if not due:
            await interaction.response.send_message("⚠️ 期限の形式が読めませんでした。例: `10/15` `10/15 17:00`", ephemeral=True)
            return
        a = await kadai_set_due(self.aid, due, interaction.user.display_name)
        await interaction.response.send_message(f"🕑 「{a['title']}」の期限を {fmt_due(due)} に変更しました。", ephemeral=True)

async def kadai_list_view(uid):
    """課題一覧に添える操作プルダウン（✅チェック＋🕑期限変更）。対象が無ければ None"""
    v = discord.ui.View(timeout=600)
    rows = await kadai_undone_rows(uid)
    if rows:
        v.add_item(KadaiDoneSelect(rows))
        v.add_item(KadaiSkipSelect(rows))
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND (a.created_by=? OR a.code IN (SELECT code FROM user_courses WHERE user_id=?)) ORDER BY a.due_ts",
        (uid, uid)) as c:
        allrows = await c.fetchall()
    if allrows:
        v.add_item(KadaiDeferSelect(allrows))
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND a.id IN (SELECT assignment_id FROM assignment_done WHERE user_id=? AND skip=1) ORDER BY a.due_ts",
        (uid,)) as c:
        skipped = await c.fetchall()
    if skipped:
        v.add_item(KadaiUnskipSelect(skipped))
    return v if v.children else None

@kadai.command(name="done", description="課題を完了にする（投稿の✅ボタンでもOK）")
@app_commands.describe(kadai_id="課題")
@app_commands.autocomplete(kadai_id=ac_open_assignments)
async def kadai_done(interaction, kadai_id: str):
    a = await assignment_row(int(kadai_id))
    if not a:
        await interaction.response.send_message("課題が見つかりません。", ephemeral=True)
        return
    await kadai_mark(a["id"], interaction.user.id, False)
    await db.commit()
    await interaction.response.send_message(f"✅ 「{a['title']}」を完了にしました。", ephemeral=True)
    try:
        await maybe_celebrate(a["id"])
    except Exception as e:
        print(f"全員提出祝福エラー: {e!r}", flush=True)

async def kadai_set_due(aid, due_dt, by_name):
    """課題の期限を変更（提出延期など）。リマインド済み記録を消して新しい期限で前日・当日に再通知"""
    await db.execute("UPDATE assignments SET due_ts=?, closed=0 WHERE id=?", (int(due_dt.timestamp()), aid))
    await db.execute("DELETE FROM assignment_reminded WHERE assignment_id=? AND stage IN ('d3','d1','d0')", (aid,))
    await db.commit()
    a = await assignment_row(aid)
    ch = await get_ch("kadai")
    if ch and a:
        await ch.send(f"🕑 **{a['cname']}**「{a['title']}」の期限を **{fmt_due(due_dt)}** に変更しました（by {by_name}）")
        await bump_panel("kadai")
    return a

async def build_kadai_forecast(now):
    """来週（明日〜7日後）締切の課題を日別にまとめたテキスト。無ければ None"""
    t1 = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    t2 = t1 + timedelta(days=7)
    async with db.execute(
        "SELECT a.*, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND a.due_ts >= ? AND a.due_ts < ? ORDER BY a.due_ts",
        (int(t1.timestamp()), int(t2.timestamp()))) as c:
        rows = await c.fetchall()
    if not rows:
        return None
    by_day = {}
    for a in rows:
        due = datetime.fromtimestamp(a["due_ts"], JST)
        by_day.setdefault(due.date().isoformat(), []).append(a)
    lines = [f"📚 **来週の課題予報**（{t1.month}/{t1.day}〜）"]
    for d in sorted(by_day):
        dd = date.fromisoformat(d)
        items = "／".join(f"**{a['cname']}**「{a['title']}」" for a in by_day[d])
        lines.append(f"・{dd.month}/{dd.day}({DAY_CHARS[dd.weekday()]})　{items}")
    lines.append("-# まだ登録されていない課題に気づいたら ➕ で登録してね")
    return "\n".join(lines)

async def kadai_forecast(now=None):
    """毎週日曜の通信簿の後に #課題📚 へ来週の見通しを流す"""
    now = now or now_jst()
    txt = await build_kadai_forecast(now)
    if not txt:
        return None
    ch = await get_ch("kadai")
    if ch:
        await ch.send(txt)
        await bump_panel("kadai")
    return txt

async def kadai_awards(d1, d2):
    """通信簿の課題系の賞：✅きっちり賞（期間内締切の課題消化率）＋📝課題係賞（手動登録数。定例の自動生成分は除く）"""
    t1 = int(datetime.fromisoformat(d1).replace(tzinfo=JST).timestamp())
    t2 = int((datetime.fromisoformat(d2).replace(tzinfo=JST) + timedelta(days=1)).timestamp())
    out = []
    async with db.execute("SELECT id, code FROM assignments WHERE due_ts >= ? AND due_ts < ?", (t1, t2)) as c:
        week_as = await c.fetchall()
    per = {}
    for a in week_as:
        done = await done_set(a["id"])
        skipped = await skip_set(a["id"])
        for uid in await takers_of(a["code"]):
            if uid in skipped:
                continue   # 🚫提出しないは分母からも外す（罰にも得にもしない）
            n, dn = per.get(uid, (0, 0))
            per[uid] = (n + 1, dn + (1 if uid in done else 0))
    cands = {uid: (n, dn) for uid, (n, dn) in per.items() if n >= 2}
    if cands:
        best = max(dn / n for n, dn in cands.values())
        if best > 0:
            names = []
            for uid, (n, dn) in cands.items():
                if dn / n == best:
                    gu = await get_user(uid)
                    names.append((gu["name"] if gu and gu["name"] else uid))
            out.append({"emoji": "✅", "title": "きっちり賞", "names": names, "value": f"課題消化 {round(best * 100)}%"})
    async with db.execute(
        "SELECT created_by, COUNT(*) AS n FROM assignments WHERE created_at >= ? AND created_at < ? "
        "AND created_by IS NOT NULL "
        "AND id NOT IN (SELECT assignment_id FROM recurring_spawned WHERE assignment_id IS NOT NULL) "
        "GROUP BY created_by", (t1, t2)) as c:
        regs = await c.fetchall()
    if regs:
        best = max(r["n"] for r in regs)
        names = []
        for r in regs:
            if r["n"] == best:
                gu = await get_user(r["created_by"])
                names.append((gu["name"] if gu and gu["name"] else r["created_by"]))
        out.append({"emoji": "📝", "title": "課題係賞", "names": names, "value": f"{best}件 登録"})
    return out

# ---- 📝 試験日カウントダウン ----
# チャンネルに鳴るのは 30日前→1週間前→3日前 だけ（しつこさ防止）。
# 前日は🌙就寝時の返事に、当日は☀️起床ダイジェストに、本人へそっと添える。
EXAM_STAGES = {30: "m30", 7: "w7", 3: "d3"}

def exam_days_left(exam_day, now=None):
    now = now or now_jst()
    return (date.fromisoformat(exam_day) - now.date()).days

def exam_date_str(exam_day):
    d = date.fromisoformat(exam_day)
    return f"{d.month}/{d.day}({DAY_CHARS[d.weekday()]})"

def exam_stage_text(stage, cname, title, exam_day):
    ds = exam_date_str(exam_day)
    return {
        "m30": f"🗓 **{cname}**「{title}」まで **あと1ヶ月**（{ds}）。そろそろ視界に入れておこう",
        "w7": f"📝 **{cname}**「{title}」まで **あと1週間**（{ds}）！",
        "d3": f"📝 **{cname}**「{title}」まで **あと3日**（{ds}）。追い込みどき",
    }[stage]

async def exam_rows_for(uid=None):
    """開催前の試験（uid指定で自分の履修科目のみ）。日付順"""
    q = "SELECT e.*, c.name AS cname FROM exams e JOIN courses c ON c.code=e.code WHERE e.closed=0"
    args = []
    if uid is not None:
        q += " AND e.code IN (SELECT code FROM user_courses WHERE user_id=?)"
        args.append(str(uid))
    q += " ORDER BY e.exam_day"
    async with db.execute(q, args) as c:
        return await c.fetchall()

async def remind_exams(now=None):
    """試験のグラデーション通知（毎朝）。過ぎた試験は自動クローズ"""
    now = now or now_jst()
    async with db.execute("SELECT e.*, c.name AS cname FROM exams e JOIN courses c ON c.code=e.code WHERE e.closed=0") as c:
        rows = await c.fetchall()
    sent = 0
    for e in rows:
        left = exam_days_left(e["exam_day"], now)
        if left < 0:
            await db.execute("UPDATE exams SET closed=1 WHERE id=?", (e["id"],))
            await db.commit()
            continue
        stage = EXAM_STAGES.get(left)
        if not stage:
            continue
        async with db.execute("SELECT 1 FROM exam_reminded WHERE exam_id=? AND stage=?", (e["id"], stage)) as c2:
            if await c2.fetchone():
                continue
        await db.execute("INSERT OR IGNORE INTO exam_reminded(exam_id,stage) VALUES(?,?)", (e["id"], stage))
        await db.commit()
        sent += 1
        takers = await takers_of(e["code"])
        ch = await get_ch("kadai")
        if ch and takers:
            mention = " ".join(f"<@{t}>" for t in takers)
            await ch.send(exam_stage_text(stage, e["cname"], e["title"], e["exam_day"]) + f"\n{mention}")
            await bump_panel("kadai")
    return sent

async def maybe_celebrate(aid):
    """課題の履修者全員が✅になった瞬間に一度だけ祝う（2人以上の科目のみ）"""
    a = await assignment_row(aid)
    if not a or a["closed"]:
        return False
    takers = await takers_of(a["code"])
    if len(takers) < 2:
        return False
    done = await done_set(aid)
    if not set(takers) <= done:
        return False
    submitted = done - await skip_set(aid)
    if len(submitted) < 2:
        return False   # 🚫提出しないを除いて実際に出した人が2人以上いるときだけ祝う
    async with db.execute("SELECT 1 FROM assignment_reminded WHERE assignment_id=? AND stage='all'", (aid,)) as c:
        if await c.fetchone():
            return False
    await db.execute("INSERT OR IGNORE INTO assignment_reminded(assignment_id,stage) VALUES(?, 'all')", (aid,))
    await db.commit()
    ch = await get_ch("kadai")
    if ch:
        await ch.send(f"🎉 **{a['cname']}**「{a['title']}」、対象の {len(submitted)} 人ぜんいん提出！ちょーえらい！{erai_emoji(ch.guild)}")
        await bump_panel("kadai")
    return True

@kadai.command(name="kigen", description="課題の期限を変更する（提出延期されたときなど）")
@app_commands.describe(kadai_id="課題", kigen="新しい期限 例: 10/15 ／ 10/15 17:00（時刻省略は23:59）")
@app_commands.autocomplete(kadai_id=ac_open_assignments)
async def kadai_kigen(interaction, kadai_id: str, kigen: str):
    a = await assignment_row(int(kadai_id))
    if not a:
        await interaction.response.send_message("課題が見つかりません。", ephemeral=True)
        return
    due = parse_due(kigen)
    if not due:
        await interaction.response.send_message("⚠️ 期限の形式が読めませんでした。例: `10/15` `10/15 17:00`", ephemeral=True)
        return
    await kadai_set_due(a["id"], due, interaction.user.display_name)
    await interaction.response.send_message(f"🕑 「{a['title']}」の期限を {fmt_due(due)} に変更しました。", ephemeral=True)

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
        await bump_panel("kadai")
    await interaction.response.send_message("取り下げました。", ephemeral=True)

bot.tree.add_command(kadai)

# ---- 🔁 毎週課題（定例課題：プリント・出席フォームなど。1回登録→授業日の2日前に自動で課題化） ----
TEIKI_SPAWN_AHEAD = 6   # 次の授業日ぶんを常に生成し、授業当日の朝には来週ぶんも生やす（＝課題が出た授業の直後に✅できる）。3日前リマインドは定例課題だけ鳴らさない

def course_day_chars(slots):
    """slots文字列（例「金1,金2」「月3,木2」）から授業のある曜日文字を週順・重複なしで"""
    seen = []
    for s in (slots or "").split(","):
        s = s.strip()
        if s and s[0] in DAY_CHARS and s[0] not in seen:
            seen.append(s[0])
    return seen

def parse_day_chars_input(s):
    """「日」「月,木」→ ['日'] ['月','木']（週順）。読めなければ None"""
    t = unicodedata.normalize("NFKC", s or "").strip()
    out = []
    for p in re.split(r"[,、\s・]+", t):
        if not p:
            continue
        if not all(ch in DAY_CHARS for ch in p):   # 「土日」のような連結表記もOK
            return None
        for ch in p:
            if ch not in out:
                out.append(ch)
    return sorted(out, key=DAY_CHARS.index) or None

def rule_day_chars(rule, course):
    """定例課題の生成曜日：ルールのdue_days指定 ＞ 授業の曜日"""
    dd = rule["due_days"] if "due_days" in rule.keys() else None
    if dd:
        return [c for c in DAY_CHARS if c in dd]
    return course_day_chars(course["slots"])

def course_first_period(slots, dc):
    """その曜日の最初の時限番号。無ければ None"""
    ps = [int(s.strip()[1:]) for s in (slots or "").split(",")
          if s.strip()[:1] == dc and s.strip()[1:].isdigit()]
    return min(ps) if ps else None

def teiki_due_dt(course, rule, d):
    """定例課題の期限日時：ルールの指定時刻 > その曜日の授業開始時刻 > 23:59"""
    hm = rule["due_time"]
    if not hm:
        p = course_first_period(course["slots"], DAY_CHARS[d.weekday()])
        hm = PERIOD_START.get(p) if p else None
    h, m = (hm.split(":") if hm else ("23", "59"))
    return datetime(d.year, d.month, d.day, int(h), int(m), tzinfo=JST)

async def spawn_recurring(now=None):
    """定例課題ルールから直近の授業日ぶんの課題を自動生成（毎朝）。生成済み・手動取り下げ済みの日は飛ばす"""
    now = now or now_jst()
    if break_on(now):
        return 0   # 🌸 長期休暇中は授業がないので毎週課題も生やさない（休み明けの朝から再開）
    async with db.execute("SELECT * FROM recurring_kadai WHERE active=1") as c:
        rules = await c.fetchall()
    made = 0
    for r in rules:
        course = await get_course(r["code"])
        if not course:
            continue
        for dc in rule_day_chars(r, course):
            off = (DAY_CHARS.index(dc) - now.weekday()) % 7
            if off > TEIKI_SPAWN_AHEAD:
                continue
            dates = [(now + timedelta(days=off)).date()]
            if off == 0:   # 授業当日は来週ぶんも生やす＝授業終わり（課題が出た直後）にすぐ✅できる
                dates.append(dates[0] + timedelta(days=7))
            for d in dates:
                dd = d.isoformat()
                async with db.execute("SELECT 1 FROM recurring_spawned WHERE rule_id=? AND due_day=?", (r["id"], dd)) as c2:
                    if await c2.fetchone():
                        continue
                due = teiki_due_dt(course, r, d)
                if due < now:   # 登録したその日の、もう始まっている授業には出さない
                    await db.execute("INSERT OR IGNORE INTO recurring_spawned(rule_id,due_day) VALUES(?,?)", (r["id"], dd))
                    await db.commit()
                    continue
                cur = await db.execute("INSERT INTO assignments(code,title,note,due_ts,created_by,created_at) VALUES(?,?,?,?,?,?)",
                                       (r["code"], r["title"], "🔁 毎週の定例課題（自動登録）", int(due.timestamp()), r["created_by"], int(now.timestamp())))
                await db.execute("INSERT OR IGNORE INTO recurring_spawned(rule_id,due_day,assignment_id) VALUES(?,?,?)", (r["id"], dd, cur.lastrowid))
                await db.commit()
                made += 1
    return made

async def teiki_rules_for(uid):
    """自分の履修科目に紐づく定例課題ルール"""
    async with db.execute(
        "SELECT r.*, c.name AS cname, c.slots FROM recurring_kadai r JOIN courses c ON c.code=r.code "
        "WHERE r.active=1 AND r.code IN (SELECT code FROM user_courses WHERE user_id=?) ORDER BY r.id", (uid,)) as c:
        return await c.fetchall()

async def teiki_update_rule(rule_id, hm, days, now=None):
    """毎週課題のルール変更（hm='23:59'/None=授業開始、days='日'や'月,木'/None=授業の曜日）。
    生成済みで未クローズの課題は、新しい曜日に合うものは時刻を再計算、合わないものは取り下げて
    新しい曜日の直近ぶんを即生成する。(ルール, 時刻反映件数, 取り下げ件数) を返す"""
    now = now or now_jst()
    await db.execute("UPDATE recurring_kadai SET due_time=?, due_days=? WHERE id=?", (hm, days, rule_id))
    await db.commit()
    async with db.execute("SELECT r.*, c.name AS cname, c.slots FROM recurring_kadai r JOIN courses c ON c.code=r.code WHERE r.id=?",
                          (rule_id,)) as c:
        rule = await c.fetchone()
    if not rule:
        return None, 0, 0
    course = await get_course(rule["code"])
    eff = rule_day_chars(rule, course)
    retimed, dropped = 0, 0
    async with db.execute(
        "SELECT rs.due_day, rs.assignment_id FROM recurring_spawned rs JOIN assignments a ON a.id=rs.assignment_id "
        "WHERE rs.rule_id=? AND a.closed=0", (rule_id,)) as c:
        rows = await c.fetchall()
    for r in rows:
        d = date.fromisoformat(r["due_day"])
        if DAY_CHARS[d.weekday()] in eff:
            due = teiki_due_dt(course, rule, d)
            await db.execute("UPDATE assignments SET due_ts=? WHERE id=?", (int(due.timestamp()), r["assignment_id"]))
            await db.execute("DELETE FROM assignment_reminded WHERE assignment_id=? AND stage IN ('d3','d1','d0')", (r["assignment_id"],))
            retimed += 1
        else:   # 曜日が変わって合わなくなった分は取り下げ（spawned記録は残る＝旧曜日で再生成しない）
            await db.execute("UPDATE assignments SET closed=1 WHERE id=?", (r["assignment_id"],))
            dropped += 1
    await db.commit()
    if rule["active"]:
        await spawn_recurring(now)   # 新しい曜日の直近ぶんをその場で生やす
    return rule, retimed, dropped

def teiki_label(r):
    days = "・".join(rule_day_chars(r, r)) or "？"   # rにslotsが同梱されているのでcourse代わりに使える
    return f"{r['cname']}「{r['title']}」（毎週{days}・締切 {r['due_time'] or '授業開始'}）"

# ---- #課題📚 パネル（履修登録と課題登録をボタンから） ----
class CourseSearchModal(discord.ui.Modal, title="🎓 履修科目を探す"):
    q = discord.ui.TextInput(label="科目名（教員名・曜日時限も混ぜてOK 例 writing 月1）", max_length=40)

    async def on_submit(self, interaction):
        query = self.q.value.strip()
        rows = await search_courses(query, 24)
        opts = [discord.SelectOption(label=course_label(r)[:100], value=r["code"]) for r in rows]
        if query and not any(norm_text(r["name"]) == norm_text(query) for r in rows):
            opts.append(discord.SelectOption(label=f"＋「{query[:40]}」をマスタに無い科目として登録", value=("new:" + query)[:100]))
        if not opts:
            await interaction.response.send_message("見つかりませんでした。別のキーワードでもう一度どうぞ。", ephemeral=True)
            return
        view = discord.ui.View(timeout=300)
        view.add_item(CourseSelect(opts[:25]))
        await interaction.response.send_message(f"🔍 「{query}」の検索結果です。登録する科目を選んでください：", view=view, ephemeral=True)

class CourseSelect(discord.ui.Select):
    def __init__(self, options):
        super().__init__(placeholder="登録する科目を選ぶ（複数OK）", options=options, min_values=1, max_values=len(options))

    async def callback(self, interaction):
        user = interaction.user
        await ensure_user(user)
        lines = []
        for v in self.values:
            c = await resolve_course_value(v)
            if not c:
                continue
            await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
            others = [t for t in await takers_of(c["code"]) if t != str(user.id)]
            lines.append(f"✅ {course_label(c)}" + (f"　👥 他 {len(others)} 人" if others else "　👥 最初の一人！"))
        await db.commit()
        total = len(await user_course_rows(user.id))
        await interaction.response.edit_message(content="\n".join(lines) + f"\n\n📚 登録科目 {total} 件。続けて探すならもう一度 🎓 を。", view=None)

class KadaiCourseSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=course_label(r)[:100], value=r["code"]) for r in rows[:25]]
        super().__init__(placeholder="どの科目の課題？", options=opts)

    async def callback(self, interaction):
        c = await get_course(self.values[0])
        if not c:
            await interaction.response.send_message("科目が見つかりませんでした。", ephemeral=True)
            return
        await interaction.response.send_modal(KadaiAddModal(c))

class KadaiAddModal(discord.ui.Modal):
    def __init__(self, course):
        super().__init__(title=f"📌 {course['name']}"[:45])
        self.code = course["code"]
        self.kigen = discord.ui.TextInput(label="期限（例 10/15 ／ 10/15 17:00）", max_length=20)
        self.naiyou = discord.ui.TextInput(label="内容（例 レポート提出）", max_length=100)
        self.memo = discord.ui.TextInput(label="補足（任意）", required=False, max_length=300, style=discord.TextStyle.paragraph)
        for i in (self.kigen, self.naiyou, self.memo):
            self.add_item(i)

    async def on_submit(self, interaction):
        await kadai_register(interaction, self.code, self.kigen.value, self.naiyou.value, self.memo.value or None)

class TeikiCourseSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=course_label(r)[:100], value=r["code"]) for r in rows[:25]]
        super().__init__(placeholder="➕ 毎週課題を追加する科目を選ぶ", options=opts)

    async def callback(self, interaction):
        c = await get_course(self.values[0])
        if not c:
            await interaction.response.send_message("科目が見つかりませんでした。", ephemeral=True)
            return
        if not course_day_chars(c["slots"]):
            await interaction.response.send_message("この科目は曜日時限の情報が無いので毎週課題にできません（通常の ➕ 課題登録を使ってください）。", ephemeral=True)
            return
        await interaction.response.send_modal(TeikiAddModal(c))

class TeikiAddModal(discord.ui.Modal):
    def __init__(self, course):
        super().__init__(title=f"🔁 {course['name']}"[:45])
        self.code = course["code"]
        self.naiyou = discord.ui.TextInput(label="毎週の課題の内容（例 プリント提出／出席フォーム）", max_length=100)
        self.jikoku = discord.ui.TextInput(label="締切時刻（例 23:59。空欄なら授業開始まで）", required=False, max_length=5)
        self.youbi = discord.ui.TextInput(label="締切の曜日（例 日 ／ 月,木。空欄なら授業の曜日）", required=False, max_length=10)
        for i in (self.naiyou, self.jikoku, self.youbi):
            self.add_item(i)

    async def on_submit(self, interaction):
        user = interaction.user
        await ensure_user(user)
        c = await get_course(self.code)
        hm = (self.jikoku.value or "").strip()
        if hm and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", hm):
            await interaction.response.send_message("⚠️ 時刻は `23:59` のような形式で入れてください（空欄なら授業開始まで）。", ephemeral=True)
            return
        days_list = None
        if (self.youbi.value or "").strip():
            days_list = parse_day_chars_input(self.youbi.value)
            if not days_list:
                await interaction.response.send_message("⚠️ 曜日は `日` や `月,木` の形式で入れてください（空欄なら授業の曜日）。", ephemeral=True)
                return
        await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
        await db.execute("INSERT INTO recurring_kadai(code,title,due_time,due_days,created_by,created_at) VALUES(?,?,?,?,?,?)",
                         (c["code"], self.naiyou.value.strip()[:100], hm or None,
                          ",".join(days_list) if days_list else None, str(user.id), int(now_jst().timestamp())))
        await db.commit()
        made = await spawn_recurring()   # 直近ぶんはその場で生成
        days = "・".join(days_list or course_day_chars(c["slots"]))
        ch = await get_ch("kadai")
        if ch:
            await ch.send(f"🔁 **{user.display_name}** が **{c['name']}** の毎週課題「{self.naiyou.value.strip()}」を登録しました"
                          f"（毎週{days}曜・締切 {hm or '授業開始'}。次の回のぶんから毎週自動で課題になります）")
            await bump_panel("kadai")
        await interaction.response.send_message(
            f"✅ 毎週課題を登録しました：**{c['name']}**「{self.naiyou.value.strip()}」（毎週{days}曜）"
            + (f"\n直近 {made} 件を課題化しました。" if made else "\n直近の授業日が来たら自動で課題になります。"), ephemeral=True)

class TeikiEditSelect(discord.ui.Select):
    def __init__(self, rules):
        opts = [discord.SelectOption(label=f"🕑 変更：{teiki_label(r)}"[:100], value=str(r["id"])) for r in rules[:25]]
        super().__init__(placeholder="🕑 毎週の締切（時刻・曜日）を変更する", options=opts)

    async def callback(self, interaction):
        async with db.execute("SELECT r.*, c.name AS cname FROM recurring_kadai r JOIN courses c ON c.code=r.code WHERE r.id=?",
                              (int(self.values[0]),)) as c:
            r = await c.fetchone()
        if not r:
            await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
            return
        await interaction.response.send_modal(TeikiEditModal(r))

class TeikiEditModal(discord.ui.Modal):
    def __init__(self, r):
        super().__init__(title=f"🕑 {r['cname']}「{r['title']}」"[:45])
        self.rid = r["id"]
        dd = r["due_days"] if "due_days" in r.keys() else None
        self.jikoku = discord.ui.TextInput(label="締切時刻（例 23:59。空欄なら授業開始まで）",
                                           required=False, max_length=5, default=r["due_time"] or "")
        self.youbi = discord.ui.TextInput(label="締切の曜日（例 日 ／ 月,木。空欄なら授業の曜日）",
                                          required=False, max_length=10, default=(dd or "").replace(",", "、"))
        for i in (self.jikoku, self.youbi):
            self.add_item(i)

    async def on_submit(self, interaction):
        hm = (self.jikoku.value or "").strip()
        if hm and not re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", hm):
            await interaction.response.send_message("⚠️ 時刻は `23:59` のような形式で入れてください（空欄なら授業開始まで）。", ephemeral=True)
            return
        days_list = None
        if (self.youbi.value or "").strip():
            days_list = parse_day_chars_input(self.youbi.value)
            if not days_list:
                await interaction.response.send_message("⚠️ 曜日は `日` や `月,木` の形式で入れてください（空欄なら授業の曜日）。", ephemeral=True)
                return
        rule, retimed, dropped = await teiki_update_rule(self.rid, hm or None, ",".join(days_list) if days_list else None)
        if not rule:
            await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
            return
        course = await get_course(rule["code"])
        label = f"毎週{'・'.join(rule_day_chars(rule, course))}曜・{hm or '授業開始'}"
        note = (f"・生成済み {retimed} 件に反映" if retimed else "") + (f"・旧曜日の {dropped} 件は取り下げ→新曜日で生成し直し" if dropped else "")
        ch = await get_ch("kadai")
        if ch:
            await ch.send(f"🕑 **{rule['cname']}** の毎週課題「{rule['title']}」の締切を **{label}** に変更しました"
                          f"（by {interaction.user.display_name}{note}）")
            await bump_panel("kadai")
        await interaction.response.send_message(f"✅ 締切を {label} に変更しました{note}。以後の自動生成も新しい設定です。", ephemeral=True)

class TeikiRemoveSelect(discord.ui.Select):
    def __init__(self, rules):
        opts = [discord.SelectOption(label=f"🗑 解除：{teiki_label(r)}"[:100], value=str(r["id"])) for r in rules[:25]]
        super().__init__(placeholder="🗑 毎週課題を解除する", options=opts)

    async def callback(self, interaction):
        async with db.execute("SELECT r.*, c.name AS cname, c.slots FROM recurring_kadai r JOIN courses c ON c.code=r.code WHERE r.id=?",
                              (int(self.values[0]),)) as c:
            r = await c.fetchone()
        if not r:
            await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
            return
        await db.execute("UPDATE recurring_kadai SET active=0 WHERE id=?", (r["id"],))
        await db.commit()
        ch = await get_ch("kadai")
        if ch:
            await ch.send(f"🔁🗑 **{r['cname']}** の毎週課題「{r['title']}」は **{interaction.user.display_name}** が解除しました。")
            await bump_panel("kadai")
        await interaction.response.edit_message(content=f"🗑 解除しました：{teiki_label(r)}", view=None)

class ExamCourseSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=course_label(r)[:100], value=r["code"]) for r in rows[:25]]
        super().__init__(placeholder="➕ 試験日を登録する科目を選ぶ", options=opts)

    async def callback(self, interaction):
        c = await get_course(self.values[0])
        if not c:
            await interaction.response.send_message("科目が見つかりませんでした。", ephemeral=True)
            return
        await interaction.response.send_modal(ExamAddModal(c))

class ExamAddModal(discord.ui.Modal):
    def __init__(self, course):
        super().__init__(title=f"📝 {course['name']}"[:45])
        self.code = course["code"]
        self.hi = discord.ui.TextInput(label="試験日（例 1/27）", max_length=20)
        self.namae = discord.ui.TextInput(label="名前（例 中間試験・小テスト。空欄なら「試験」）", required=False, max_length=30)
        for i in (self.hi, self.namae):
            self.add_item(i)

    async def on_submit(self, interaction):
        user = interaction.user
        await ensure_user(user)
        due = parse_due(self.hi.value)
        if not due:
            await interaction.response.send_message("⚠️ 日付が読めませんでした。例: `1/27` `2027/1/27`", ephemeral=True)
            return
        c = await get_course(self.code)
        title = (self.namae.value or "").strip() or "試験"
        exam_day = due.date().isoformat()
        await db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?,?)", (str(user.id), c["code"]))
        await db.execute("INSERT INTO exams(code,title,exam_day,created_by,created_at) VALUES(?,?,?,?,?)",
                         (c["code"], title, exam_day, str(user.id), int(now_jst().timestamp())))
        await db.commit()
        left = exam_days_left(exam_day)
        ch = await get_ch("kadai")
        if ch:
            await ch.send(f"📝 **{user.display_name}** が **{c['name']}** の試験日を登録しました：「{title}」{exam_date_str(exam_day)}（**あと{left}日**）\n"
                          f"-# 30日前・1週間前・3日前にここでお知らせ。前日の🌙と当日の☀️には本人にだけそっと出ます")
            await bump_panel("kadai")
        await interaction.response.send_message(f"✅ 登録しました：**{c['name']}**「{title}」{exam_date_str(exam_day)}（あと{left}日）", ephemeral=True)

class ExamRemoveSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"🗑 取り下げ：{r['cname']}「{r['title']}」{exam_date_str(r['exam_day'])}"[:100], value=str(r["id"]))
                for r in rows[:25]]
        super().__init__(placeholder="🗑 試験日を取り下げる（間違い・中止）", options=opts)

    async def callback(self, interaction):
        async with db.execute("SELECT e.*, c.name AS cname FROM exams e JOIN courses c ON c.code=e.code WHERE e.id=?",
                              (int(self.values[0]),)) as c:
            e = await c.fetchone()
        if not e:
            await interaction.response.send_message("見つかりませんでした。", ephemeral=True)
            return
        await db.execute("UPDATE exams SET closed=1 WHERE id=?", (e["id"],))
        await db.commit()
        ch = await get_ch("kadai")
        if ch:
            await ch.send(f"📝🗑 **{e['cname']}**「{e['title']}」（{exam_date_str(e['exam_day'])}）は **{interaction.user.display_name}** が取り下げました。")
            await bump_panel("kadai")
        await interaction.response.edit_message(content=f"🗑 取り下げました：{e['cname']}「{e['title']}」", view=None)

class FreeSlotModal(discord.ui.Modal, title="🧩 空きコマの予定"):
    koma = discord.ui.TextInput(label="コマ（例 金3 ／ 金3,金4）", max_length=20)
    yotei = discord.ui.TextInput(label="予定（例 バイト／部活／ジム）", max_length=30)
    basho = discord.ui.TextInput(label="場所（任意）", required=False, max_length=30)

    async def on_submit(self, interaction):
        await ensure_user(interaction.user)
        slots = parse_slots_input(self.koma.value)
        if not slots:
            await interaction.response.send_message("⚠️ コマは `金3` や `金3,金4` の形式で入れてください（1〜6限）。", ephemeral=True)
            return
        label = self.yotei.value.strip()[:30]
        place = self.basho.value.strip()[:30] or None
        for s in slots:
            await db.execute("INSERT INTO user_slots(user_id,slot,label,place) VALUES(?,?,?,?) "
                             "ON CONFLICT(user_id,slot) DO UPDATE SET label=excluded.label, place=excluded.place",
                             (str(interaction.user.id), s, label, place))
        await db.commit()
        await interaction.response.send_message(
            f"🧩 登録しました：**{label}**（{'・'.join(slots)}" + (f"／{place}" if place else "") + "）\n"
            "-# 時間割画像と☀️朝の時間割に表示されます（自分にだけ）。同じコマに入れ直すと上書きされます", ephemeral=True)

class FreeSlotAddButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="➕ 予定を追加", style=discord.ButtonStyle.primary)

    async def callback(self, interaction):
        await interaction.response.send_modal(FreeSlotModal())

class FreeSlotRemoveSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"🗑 {r['slot']} {r['label']}"[:100], value=r["slot"]) for r in rows[:25]]
        super().__init__(placeholder="🗑 予定を消す（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        for s in self.values:
            await db.execute("DELETE FROM user_slots WHERE user_id=? AND slot=?", (str(interaction.user.id), s))
        await db.commit()
        await interaction.response.edit_message(content=f"🗑 {len(self.values)} 件消しました。", view=None)

# ---- 🏫 大学の予定（長期休暇・履修登録期間など。誰でも登録→朝の予定・今後の予定・#課題📚 のお知らせに出る） ----
UNIV_KINDS = [("長期休暇", "🌸", "期間中は時間割と毎週課題がお休み・起床締切が少し遅くなる"),
              ("履修登録", "📋", "期間中は朝の予定に締切までの日数が出る"),
              ("行事", "🎪", "学園祭・休講日・オープンキャンパスなど"),
              ("その他", "📌", "そのほか全員に関係する予定")]
# 試験は科目ごとに日程が違うので、全体の「試験期間」は選ばせない（科目ごとの 📝 試験日 を使う）。過去の登録分の表示用に絵文字だけ残す
UNIV_EMOJI = {**{k: e for k, e, _ in UNIV_KINDS}, "試験期間": "📝"}

def md(iso):
    """'2027-02-10' → '2/10'。今年以外は年つき '2027/2/10'（何年のことか分かるように）"""
    y = int(iso[:4])
    base = f"{int(iso[5:7])}/{int(iso[8:])}"
    return base if y == now_jst().year else f"{y}/{base}"

def univ_span_text(r):
    return md(r["start_day"]) if r["start_day"] == r["end_day"] else f"{md(r['start_day'])}〜{md(r['end_day'])}"

def univ_status(r, today):
    """「あと3日で開始」「締切まであと2日」「今日まで！」などの状態文"""
    t = date.fromisoformat(today)
    s, e = date.fromisoformat(r["start_day"]), date.fromisoformat(r["end_day"])
    if t < s:
        n = (s - t).days
        return "明日から" if n == 1 else f"あと{n}日"
    if s == e:
        return "今日！"
    left = (e - t).days
    if left == 0:
        return "今日まで！"
    return f"{'締切' if r['kind'] == '履修登録' else '終わり'}まであと{left}日"

def univ_line(r, today):
    return f"{UNIV_EMOJI.get(r['kind'], '📌')} **{r['title']}**（{univ_span_text(r)}・{univ_status(r, today)}）"

async def univ_events_between(d1, d2):
    """[d1, d2] と期間が重なる予定（開始順）"""
    async with db.execute("SELECT e.*, COALESCE(u.name,'?') AS uname FROM univ_events e LEFT JOIN users u ON u.id=e.created_by "
                          "WHERE e.end_day >= ? AND e.start_day <= ? ORDER BY e.start_day, e.id", (d1, d2)) as c:
        return await c.fetchall()

async def add_univ_event(uid, kind, title, start, end):
    cur = await db.execute("INSERT INTO univ_events(title,kind,start_day,end_day,created_by,ts) VALUES(?,?,?,?,?,?)",
                           (title[:40], kind, start.isoformat(), end.isoformat(), str(uid), int(now_jst().timestamp())))
    await db.commit()
    if kind == "長期休暇":
        await load_break()
    return cur.lastrowid

async def univ_event_remind(now=None):
    """毎朝 #課題📚 へ：開始3日前・開始日・（履修登録は）締切2日前。各段階1回だけ・メンションなし"""
    now = now or now_jst()
    today = day_str(now)
    t = date.fromisoformat(today)
    rows = await univ_events_between(today, (t + timedelta(days=3)).isoformat())
    ch, key = await yotei_channel()
    if not ch:
        return 0
    n = 0
    for r in rows:
        s, e = date.fromisoformat(r["start_day"]), date.fromisoformat(r["end_day"])
        stage = None
        if (s - t).days == 3:
            stage, msg = "pre", f"🏫 **3日後から**：{univ_line(r, today)}"
        elif s == t:
            stage, msg = "start", f"🏫 **今日から**：{univ_line(r, today)}" if s != e else f"🏫 **今日**：{univ_line(r, today)}"
        elif r["kind"] == "履修登録" and (e - t).days == 2 and e > s:
            stage, msg = "end2", f"🏫 **締切まであと2日**：{univ_line(r, today)}　登録し忘れに注意！"
        if not stage:
            continue
        async with db.execute("SELECT 1 FROM univ_event_reminded WHERE event_id=? AND stage=?", (r["id"], stage)) as c:
            if await c.fetchone():
                continue
        await db.execute("INSERT OR IGNORE INTO univ_event_reminded(event_id,stage) VALUES(?,?)", (r["id"], stage))
        await db.commit()
        if r["kind"] == "長期休暇" and stage == "start":
            msg += "\n-# 🌸 期間中は時間割と毎週課題がお休み・起床締切が少し遅くなります"
        await ch.send(msg)
        n += 1
    if n:
        await bump_panel(key)
    return n

async def yotei_channel():
    """予定系のお知らせ先：#予定🗓（/setup前の古い環境なら #課題📚）→ (チャンネル, パネルkey)"""
    ch = await get_ch("yotei")
    if ch:
        return ch, "yotei"
    return await get_ch("kadai"), "kadai"

class UnivEventModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title=f"🏫 大学の予定：{kind}"[:45])
        self.kind = kind
        self.t_in = discord.ui.TextInput(label="名前", max_length=40, default=kind if kind != "その他" else None,
                                         placeholder="例：前期 履修登録／春休み／学園祭")
        self.d_in = discord.ui.TextInput(label="日付（1日だけ 11/20 ／期間 2/10-4/4）", max_length=30,
                                         placeholder="年はふつう自動。指定するなら 2027/2/10-4/4 や 27/2/10")
        for it in (self.t_in, self.d_in):
            self.add_item(it)

    async def on_submit(self, interaction):
        now = now_jst()
        span = parse_span(str(self.d_in.value), now)
        if not span:
            await interaction.response.send_message("⚠️ 日付が読めませんでした。例：`11/20` `2/10-4/4` `2027/2/10-4/4`", ephemeral=True)
            return
        s, e = span
        title = str(self.t_in.value).strip() or self.kind
        eid = await add_univ_event(interaction.user.id, self.kind, title, s, e)
        r = {"title": title, "kind": self.kind, "start_day": s.isoformat(), "end_day": e.isoformat()}
        extra = "\n-# 🌸 この期間は自動で長期休暇モードになります（時間割・毎週課題がお休み、起床締切が少し遅く）" if self.kind == "長期休暇" else ""
        head = f"✅ 登録しました：{UNIV_EMOJI.get(self.kind, '📌')} **{title}**{extra}"
        await interaction.response.send_message(f"{head}\n📅 **{fmt_full_span(s, e)}**\n-# 年が違っていたら下のボタンで直せます",
                                                view=YearShiftView("univ_events", eid, interaction.user.id, head), ephemeral=True)
        ch, key = await yotei_channel()
        if ch:
            await ch.send(f"🏫 **{interaction.user.display_name}** が大学の予定を登録しました：{univ_line(r, day_str(now))}"
                          "\n-# 間違いに気づいたら登録した人に教えてね（#予定🗓 の 🏫 大学の予定 から消せます）")
            await bump_panel(key)

class UnivKindSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=k, emoji=e, value=k, description=desc[:100]) for k, e, desc in UNIV_KINDS]
        super().__init__(placeholder="➕ 予定を登録する（種類を選ぶ）", options=opts)

    async def callback(self, interaction):
        await interaction.response.send_modal(UnivEventModal(self.values[0]))

class UnivDelSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"{r['title']}（{univ_span_text(r)}）"[:100], value=str(r["id"]),
                                     emoji=UNIV_EMOJI.get(r["kind"], "📌")) for r in rows[:25]]
        super().__init__(placeholder="🗑 予定を消す（自分が登録したもの）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        admin = bool(getattr(interaction.user, "guild_permissions", None) and interaction.user.guild_permissions.administrator)
        n = 0
        for v in self.values:
            q = "DELETE FROM univ_events WHERE id=?" + ("" if admin else " AND created_by=?")
            cur = await db.execute(q, (int(v),) if admin else (int(v), uid))
            n += cur.rowcount
        await db.commit()
        await load_break()
        await interaction.response.edit_message(content=f"🗑 予定を {n} 件消しました。", view=None)

async def univ_hub_message(user):
    today = day_str(now_jst())
    rows = await univ_events_between(today, (date.fromisoformat(today) + timedelta(days=180)).isoformat())
    lines = ["🏫 **大学の予定**（だれでも登録OK・朝の予定と 📆今後の予定 に出ます）"]
    lines += [f"・{univ_line(r, today)}（{r['uname']}）" for r in rows[:15]] \
        or ["-# まだ登録がありません。長期休暇や履修登録の期間を入れておくとみんなが助かります"]
    v = discord.ui.View(timeout=600)
    v.add_item(UnivKindSelect())
    calendar_buttons(v)
    admin = bool(getattr(user, "guild_permissions", None) and user.guild_permissions.administrator)
    mine = [r for r in rows if admin or r["created_by"] == str(user.id)]
    if mine:
        v.add_item(UnivDelSelect(mine))
    return "\n".join(lines), v

# ---- 🙋 じぶんの予定：🔒自分だけ（だれにも見えない）／📣みんなに見せる（#えらい🌟で宣言＋3日前と当日に声かけ） ----
#      どちらも終わった翌日に自動で消える
PRIVATE_KIND = "自分だけ"
SHARED_KIND = "みんなに宣言"
UNIV_EMOJI[PRIVATE_KIND] = "🔒"
UNIV_EMOJI[SHARED_KIND] = "📣"

async def private_events_between(uid, d1, d2):
    """じぶんの予定を、大学の予定と同じ形の辞書で返す（idは負数で区別・kindで見せる/自分だけを表す）"""
    async with db.execute("SELECT * FROM private_events WHERE user_id=? AND end_day >= ? AND start_day <= ? ORDER BY start_day, id",
                          (str(uid), d1, d2)) as c:
        rows = await c.fetchall()
    return [{"id": -r["id"], "pid": r["id"], "title": r["title"], "kind": SHARED_KIND if r["shared"] else PRIVATE_KIND,
             "shared": r["shared"], "start_day": r["start_day"], "end_day": r["end_day"], "created_by": str(uid), "uname": ""}
            for r in rows]

async def private_cleanup(today):
    """終わったじぶんの予定は消す（毎朝）"""
    cur = await db.execute("DELETE FROM private_events WHERE end_day < ?", (today,))
    await db.execute("DELETE FROM private_event_reminded WHERE event_id NOT IN (SELECT id FROM private_events)")
    await db.commit()
    return cur.rowcount

async def announce_shared_event(member, r, today):
    """📣みんなに見せる予定を #えらい🌟 で宣言"""
    ch = await get_ch("erai")
    if ch:
        await ch.send(f"📣 **{member.display_name}** の宣言：{univ_line(r, today)}\n-# みんなで見守ろう。3日前と当日の朝にBotが声をかけます",
                      silent=True)
        await bump_panel("erai")

async def shared_event_remind(now=None):
    """📣みんなに見せる予定：3日前と開始日の朝に #えらい🌟 で本人にメンション（各1回）"""
    now = now or now_jst()
    today = day_str(now)
    t = now.date()
    async with db.execute("SELECT * FROM private_events WHERE shared=1 AND start_day BETWEEN ? AND ?",
                          (today, (t + timedelta(days=3)).isoformat())) as c:
        rows = await c.fetchall()
    ch = await get_ch("erai")
    if not ch:
        return 0
    n = 0
    for r in rows:
        left = (date.fromisoformat(r["start_day"]) - t).days
        stage = {3: "pre", 0: "day"}.get(left)
        if not stage:
            continue
        async with db.execute("SELECT 1 FROM private_event_reminded WHERE event_id=? AND stage=?", (r["id"], stage)) as c:
            if await c.fetchone():
                continue
        await db.execute("INSERT OR IGNORE INTO private_event_reminded(event_id,stage) VALUES(?,?)", (r["id"], stage))
        await db.commit()
        ev = {"title": r["title"], "kind": SHARED_KIND, "start_day": r["start_day"], "end_day": r["end_day"]}
        head = "📣 **3日後**" if stage == "pre" else "📣 **今日は宣言の日！**"
        await ch.send(f"{head} <@{r['user_id']}>：{univ_line(ev, today)}" + ("　がんばれ！🔥" if stage == "day" else "　準備はどう？"))
        n += 1
    if n:
        await bump_panel("erai")
    return n

class PrivateEventModal(discord.ui.Modal):
    def __init__(self, shared=False):
        super().__init__(title="📣 みんなに見せる予定" if shared else "🔒 自分だけの予定")
        self.shared = shared
        self.t_in = discord.ui.TextInput(label="なにをする？" + ("（#えらい🌟 で宣言されます）" if shared else "（自分にしか見えません）"),
                                         max_length=40, placeholder="例：TOEIC本番／レポート仕上げ" if shared else "例：バイト面接／帰省／歯医者")
        self.d_in = discord.ui.TextInput(label="日付（1日だけ 11/3 ／期間 12/28-1/3）", max_length=30,
                                         placeholder="年はふつう自動。指定するなら 2027/1/10 や 27/1/10")
        for it in (self.t_in, self.d_in):
            self.add_item(it)

    async def on_submit(self, interaction):
        now = now_jst()
        span = parse_span(str(self.d_in.value), now, past_ok_days=0)
        if not span:
            await interaction.response.send_message("⚠️ 日付が読めませんでした。例：`11/3` `12/28-1/3` `2027/1/10`", ephemeral=True)
            return
        s, e = span
        title = str(self.t_in.value).strip()[:40]
        cur = await db.execute("INSERT INTO private_events(user_id,title,start_day,end_day,ts,shared) VALUES(?,?,?,?,?,?)",
                               (str(interaction.user.id), title, s.isoformat(), e.isoformat(), int(now.timestamp()),
                                1 if self.shared else 0))
        pid = cur.lastrowid
        await db.commit()
        r = {"title": title, "kind": SHARED_KIND if self.shared else PRIVATE_KIND, "start_day": s.isoformat(), "end_day": e.isoformat()}
        if self.shared:
            head = f"📣 宣言しました：**{title}**\n-# #えらい🌟 に流れました。3日前と当日の朝にメンションで声をかけます"
        else:
            head = f"🔒 登録しました：**{title}**\n-# 自分にしか見えません。朝の予定・カレンダーに出て、終わった翌日に自動で消えます"
        await interaction.response.send_message(f"{head}\n📅 **{fmt_full_span(s, e)}**",
                                                view=YearShiftView("private_events", pid, interaction.user.id, head), ephemeral=True)
        if self.shared:
            await announce_shared_event(interaction.user, r, day_str(now))

class PrivateToggleSelect(discord.ui.Select):
    """登録済みの予定の 🔒自分だけ⇔📣みんなに見せる を切り替える"""

    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"{r['title']}（{univ_span_text(r)}）"[:100], value=str(r["pid"]),
                                     emoji="📣" if r["shared"] else "🔒",
                                     description="いま：みんなに見せる → 自分だけにする" if r["shared"] else "いま：自分だけ → みんなに見せる")
                for r in rows[:25]]
        super().__init__(placeholder="👀 見せる／自分だけ を切り替える", options=opts)

    async def callback(self, interaction):
        pid = int(self.values[0])
        async with db.execute("SELECT * FROM private_events WHERE id=? AND user_id=?", (pid, str(interaction.user.id))) as c:
            r = await c.fetchone()
        if not r:
            await interaction.response.edit_message(content="⚠️ 見つかりませんでした。", view=None)
            return
        new = 0 if r["shared"] else 1
        await db.execute("UPDATE private_events SET shared=? WHERE id=?", (new, pid))
        await db.commit()
        ev = {"title": r["title"], "kind": SHARED_KIND if new else PRIVATE_KIND, "start_day": r["start_day"], "end_day": r["end_day"]}
        today = day_str(now_jst())
        if new:
            await interaction.response.edit_message(content=f"📣 みんなに見せる予定にしました：{univ_line(ev, today)}（#えらい🌟 で宣言）", view=None)
            await announce_shared_event(interaction.user, ev, today)
        else:
            await interaction.response.edit_message(content=f"🔒 自分だけの予定に戻しました：{univ_line(ev, today)}（今後の声かけはしません）",
                                                    view=None)

class PrivateDelSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=f"{r['title']}（{univ_span_text(r)}）"[:100], value=str(r["pid"]),
                                     emoji="📣" if r["shared"] else "🔒") for r in rows[:25]]
        super().__init__(placeholder="🗑 予定を消す", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        await db.executemany("DELETE FROM private_events WHERE id=? AND user_id=?",
                             [(int(v), str(interaction.user.id)) for v in self.values])
        await db.commit()
        await interaction.response.edit_message(content=f"🗑 {len(self.values)} 件消しました。", view=None)

class PrivateHubView(discord.ui.View):
    def __init__(self, rows):
        super().__init__(timeout=600)
        if rows:
            self.add_item(PrivateToggleSelect(rows))
            self.add_item(PrivateDelSelect(rows))

    @discord.ui.button(label="🔒 自分だけの予定を追加", style=discord.ButtonStyle.primary, row=2)
    async def b_add(self, interaction, button):
        await interaction.response.send_modal(PrivateEventModal(False))

    @discord.ui.button(label="📣 みんなに見せる予定を追加", style=discord.ButtonStyle.success, row=2)
    async def b_add_shared(self, interaction, button):
        await interaction.response.send_modal(PrivateEventModal(True))

async def send_private_hub(interaction):
    today = day_str(now_jst())
    rows = await private_events_between(interaction.user.id, today, "9999-12-31")
    lines = ["🙋 **じぶんの予定**",
             "-# 🔒自分だけ＝だれにも見えない／📣みんなに見せる＝#えらい🌟 で宣言して、3日前と当日の朝に声かけ（緊張感がほしい予定に）",
             "-# どちらも終わった翌日に自動で消えます"]
    lines += [f"・{univ_line(r, today)}" for r in rows[:20]] or ["-# まだありません"]
    await interaction.response.send_message("\n".join(lines), view=PrivateHubView(rows), ephemeral=True)

# ---- ⏱ バイト・部活など「時間つきの予定」：日付×時間で登録 → 月の合計時間（本人だけ・記録は消さない） ----
TIME_KINDS = [("アルバイト", "💼", "#b8860b"), ("部活・サークル", "🏸", "#3d8f83"), ("その他の活動", "🕒", "#8a8378")]   # ⏱は画像用フォントに無いので🕒
TIME_EMOJI = {**{k: e for k, e, _ in TIME_KINDS}, "習い事・塾": "📖"}   # 習い事・塾は選択肢から外した（記録済みの分の表示用に残す）
TIME_COLORS = {k: c for k, _, c in TIME_KINDS}

DASHES = "ー−‐‑–—―－〜～~"   # 日本語入力で「-」のつもりで出がちな記号（ー長音・−マイナス・全角など）

def _norm_dashes(t):
    for ch in DASHES:
        t = t.replace(ch, "-")
    return t

def parse_time_range(s):
    """'17-22' '17:30-22:00' '9時-12時半' '17ー22' '1700-2200' '午後5時-10時' '22-5'(日またぎ)
    → (開始'HH:MM', 終了'HH:MM', 長さ分)。読めなければ None"""
    t = _norm_dashes(unicodedata.normalize("NFKC", s or "").strip()).replace("時半", ":30").replace(" ", "")
    pm = "午後" in t
    t = t.replace("午前", "").replace("午後", "")
    t = re.sub(r"(\d{1,2})時(?:(\d{1,2})分?)?", lambda m: f"{m[1]}:{int(m[2] or 0):02d}", t)   # 9時30分→9:30・17時→17:00
    t = re.sub(r"(?<![\d:])(\d{1,2})(\d{2})(?![\d:])", r"\1:\2", t)   # 1700→17:00
    parts = [p for p in t.split("-") if p]
    if len(parts) != 2:
        return None
    mins = []
    for p in parts:
        m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", p.rstrip(":"))
        if not m:
            return None
        h, mi = int(m[1]), int(m[2] or 0)
        if h > 24 or mi > 59:
            return None
        mins.append(h * 60 + mi)
    if pm:   # 「午後5時-10時」：12時より前の数字は午後とみなす
        mins = [x + 720 if x < 720 else x for x in mins]
    st, en = mins
    if en <= st:
        en += 1440   # 22-5 は日またぎ
    dur = en - st
    if not 0 < dur <= 24 * 60:
        return None
    hm = lambda x: f"{(x // 60) % 24:02d}:{x % 60:02d}"
    return hm(st), hm(en), dur

def parse_days_multi(spec, now):
    """'11/3, 11/5' '11/3-11/7' '11/3、11/10-11/12' → 日付のリスト（最大31日）。過去120日までは今年の日付のまま（あとから記録できる）"""
    t = _norm_dashes(unicodedata.normalize("NFKC", spec or "").strip())
    t = re.sub(r"[\(（][月火水木金土日][\)）]", "", t)   # 11/3(火) の曜日は無視
    t = re.sub(r"\s*-\s*", "-", t)                       # 11/3 - 11/7 のスペースを詰める
    words = {"今日": 0, "きょう": 0, "明日": 1, "あした": 1, "あす": 1, "明後日": 2, "あさって": 2,
             "昨日": -1, "きのう": -1, "おととい": -2, "一昨日": -2}
    for w, off in sorted(words.items(), key=lambda x: -len(x[0])):
        dd = now.date() + timedelta(days=off)
        t = t.replace(w, f"{dd.year}/{dd.month}/{dd.day}")
    out = []
    for part in re.split(r"[,、，\s]+", t):
        if not part:
            continue
        span = parse_span(part, now, past_ok_days=120)
        if not span:
            return None
        s, e = span
        if (e - s).days > 31:
            return None
        out += [s + timedelta(days=i) for i in range((e - s).days + 1)]
    out = sorted(set(out))
    return out[:31] if out else None

def fmt_mins(m):
    """325 → '5h25m'／300 → '5h'"""
    h, mi = divmod(int(m), 60)
    return f"{h}h{mi:02d}m" if mi else f"{h}h"

async def time_events_between(uid, d1, d2):
    async with db.execute("SELECT * FROM time_events WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY day, start_hm",
                          (str(uid), d1, d2)) as c:
        return await c.fetchall()

async def month_time_totals(uid, y, m):
    """{種類: (合計分, 回数)}（登録順ではなく TIME_KINDS の順）"""
    d1, d2 = month_bounds(y, m)
    tot = {}
    for r in await time_events_between(uid, d1, d2):
        a, b = tot.get(r["kind"], (0, 0))
        tot[r["kind"]] = (a + r["minutes"], b + 1)
    order = [k for k, _, _ in TIME_KINDS]
    return {k: tot[k] for k in sorted(tot, key=lambda k: order.index(k) if k in order else len(order))}

def time_totals_text(tot):
    if not tot:
        return "まだ記録がありません"
    parts = [f"{TIME_EMOJI[k]}{k} **{fmt_mins(mn)}**（{n}回）" for k, (mn, n) in tot.items()]
    allm = sum(mn for mn, _ in tot.values())
    return "／".join(parts) + (f"　計 **{fmt_mins(allm)}**" if len(tot) > 1 else "")

def time_line(r, with_date=True):
    d = date.fromisoformat(r["day"])
    head = f"{md(r['day'])}({DAY_CHARS[d.weekday()]}) " if with_date else ""
    return f"{head}{TIME_EMOJI.get(r['kind'], '⏱')} {r['start_hm']}-{r['end_hm']}（{fmt_mins(r['minutes'])}）" + (f" {r['note']}" if r["note"] else "")

class TimeEventModal(discord.ui.Modal):
    def __init__(self, kind):
        super().__init__(title=f"{TIME_EMOJI[kind]} {kind}を記録"[:45])
        self.kind = kind
        self.d_in = discord.ui.TextInput(label="日付（複数OK）", max_length=120, placeholder="11/3, 11/5, 11/7 ／ 11/3-11/7 ／ 2027/1/10")
        self.t_in = discord.ui.TextInput(label="時間", max_length=20, placeholder="17-22 ／ 17:30-22:00 ／ 22-5（日またぎ）")
        self.n_in = discord.ui.TextInput(label="メモ（任意）", max_length=30, required=False, placeholder="バイト先・部活の内容など")
        for it in (self.d_in, self.t_in, self.n_in):
            self.add_item(it)

    async def on_submit(self, interaction):
        now = now_jst()
        days = parse_days_multi(str(self.d_in.value), now)
        tr = parse_time_range(str(self.t_in.value))
        if not days or not tr:
            await interaction.response.send_message(
                "⚠️ " + ("日付" if not days else "時間") + "が読めませんでした。例：日付 `11/3, 11/5` `11/3-11/7`／時間 `17-22` `17:30-22:00`",
                ephemeral=True)
            return
        st, en, dur = tr
        note = str(self.n_in.value).strip()[:30] or None
        ts0 = int(now.timestamp())
        await db.executemany("INSERT INTO time_events(user_id,kind,day,start_hm,end_hm,minutes,note,ts) VALUES(?,?,?,?,?,?,?,?)",
                             [(str(interaction.user.id), self.kind, d.isoformat(), st, en, dur, note, ts0) for d in days])
        await db.commit()
        ds = "・".join(f"{md(d.isoformat())}({DAY_CHARS[d.weekday()]})" for d in days[:10]) + ("…" if len(days) > 10 else "")
        tl = []
        for y, m in sorted({(d.year, d.month) for d in days})[:3]:
            mn, n = (await month_time_totals(interaction.user.id, y, m)).get(self.kind, (0, 0))
            tl.append(f"-# {m}月の{self.kind}：合計 {fmt_mins(mn)}（{n}回）")
        tots = "\n".join(tl)
        await interaction.response.send_message(
            f"{TIME_EMOJI[self.kind]} **{self.kind}** を {len(days)}日ぶん記録しました（{ds}　{st}-{en}・各{fmt_mins(dur)}）\n{tots}",
            ephemeral=True)

PRESET_MAX = 5   # 「いつもの」は1人5つまで（古いものから入れ替え）

def _hm_add(hm, minutes):
    h, m = map(int, hm.split(":"))
    t = (h * 60 + m + minutes) % 1440
    return f"{t // 60:02d}:{t % 60:02d}"

def dur_label(m):
    h, mi = divmod(int(m), 60)
    return (f"{h}時間" if h else "") + (f"{mi}分" if mi else "")

async def time_presets_of(uid):
    async with db.execute("SELECT * FROM time_presets WHERE user_id=? ORDER BY id DESC LIMIT ?", (str(uid), PRESET_MAX)) as c:
        return await c.fetchall()

def preset_label(p):
    end = _hm_add(p["start_hm"], p["minutes"])
    return f"{p['note'] or p['kind']} {p['start_hm']}-{end}"

async def record_time_events(uid, kind, days, start_hm, minutes, note):
    """記録して、記録した月ぶんの合計の文を返す"""
    end_hm = _hm_add(start_hm, minutes)
    ts0 = int(now_jst().timestamp())
    await db.executemany("INSERT INTO time_events(user_id,kind,day,start_hm,end_hm,minutes,note,ts) VALUES(?,?,?,?,?,?,?,?)",
                         [(str(uid), kind, d.isoformat(), start_hm, end_hm, minutes, note, ts0) for d in days])
    await db.commit()
    lines = []
    for y, m in sorted({(d.year, d.month) for d in days})[:3]:
        mn, n = (await month_time_totals(uid, y, m)).get(kind, (0, 0))
        lines.append(f"-# {m}月の{kind}：合計 {fmt_mins(mn)}（{n}回）")
    return end_hm, "\n".join(lines)

class _TimeDaySelect(discord.ui.Select):
    def __init__(self, view_):
        today = now_jst().date()
        base = today + timedelta(days=14 * view_.page)
        self.page_days = [(base + timedelta(days=i)).isoformat() for i in range(14)]
        opts = []
        for iso in self.page_days:
            d = date.fromisoformat(iso)
            opts.append(discord.SelectOption(label=f"{d.month}/{d.day}({DAY_CHARS[d.weekday()]})" + ("　今日" if d == today else ""),
                                             value=iso, default=iso in view_.days))
        super().__init__(placeholder="📅 日付をえらぶ（いくつでも）", options=opts, min_values=0, max_values=len(opts), row=0)

    async def callback(self, interaction):
        v = self.view
        v.days = (v.days - set(self.page_days)) | set(self.values)
        await v.refresh(interaction)

class _TimeHourSelect(discord.ui.Select):
    def __init__(self, view_):
        hours = list(range(5, 24)) + list(range(0, 5))   # 朝5時から並べる（深夜は最後）
        opts = [discord.SelectOption(label=f"{h}時" + ("（深夜）" if h < 5 else ""), value=str(h),
                                     default=view_.start_h == h) for h in hours]
        super().__init__(placeholder="🕐 はじまりの時（例 17時）", options=opts, row=1)

    async def callback(self, interaction):
        self.view.start_h = int(self.values[0])
        await self.view.refresh(interaction)

class _TimeMinSelect(discord.ui.Select):
    def __init__(self, view_):
        opts = [discord.SelectOption(label=f"{m:02d}分", value=str(m), default=view_.start_m == m) for m in (0, 15, 30, 45)]
        super().__init__(placeholder="はじまりの分（ふつうは 00分）", options=opts, row=2)

    async def callback(self, interaction):
        self.view.start_m = int(self.values[0])
        await self.view.refresh(interaction)

class _TimeDurSelect(discord.ui.Select):
    def __init__(self, view_):
        opts = [discord.SelectOption(label=dur_label(m), value=str(m), default=view_.minutes == m) for m in range(30, 721, 30)]
        super().__init__(placeholder="⏳ 長さ（例 5時間）", options=opts, row=3)

    async def callback(self, interaction):
        self.view.minutes = int(self.values[0])
        await self.view.refresh(interaction)

class _TimeNoteModal(discord.ui.Modal, title="📝 メモ"):
    n_in = discord.ui.TextInput(label="メモ（バイト先・部活の内容など・空欄で消す）", max_length=30, required=False)

    def __init__(self, view_):
        super().__init__()
        self.v = view_
        self.n_in.default = view_.note or None

    async def on_submit(self, interaction):
        self.v.note = str(self.n_in.value).strip()[:30] or None
        await self.v.refresh(interaction)

class TimeEntryView(discord.ui.View):
    """⏱ タップだけで記録：日付（複数）・はじまり（時・分）・長さを選んで ✅"""

    def __init__(self, uid, kind, start_hm=None, minutes=None, note=None):
        super().__init__(timeout=1800)
        self.uid, self.kind, self.note = str(uid), kind, note
        self.start_h = int(start_hm[:2]) if start_hm else None
        self.start_m = int(start_hm[3:]) if start_hm else 0
        self.minutes = minutes
        self.page = 0
        self.days = set()
        self._build()

    def _build(self):
        self.clear_items()
        self.add_item(_TimeDaySelect(self))
        self.add_item(_TimeHourSelect(self))
        self.add_item(_TimeMinSelect(self))
        self.add_item(_TimeDurSelect(self))
        for label, style, cb in (("◀ 前の2週", discord.ButtonStyle.secondary, self._prev),
                                 ("次の2週 ▶", discord.ButtonStyle.secondary, self._next),
                                 ("📝 メモ", discord.ButtonStyle.secondary, self._memo),
                                 ("💾 いつものに保存", discord.ButtonStyle.secondary, self._save_preset),
                                 ("✅ 記録する", discord.ButtonStyle.success, self._commit)):
            b = discord.ui.Button(label=label, style=style, row=4)
            b.callback = cb
            self.add_item(b)

    def start_hm(self):
        return None if self.start_h is None else f"{self.start_h:02d}:{self.start_m:02d}"

    def summary(self):
        ds = sorted(self.days)
        dtxt = "・".join(f"{md(x)}({DAY_CHARS[date.fromisoformat(x).weekday()]})" for x in ds[:12]) + ("…" if len(ds) > 12 else "")
        sh = self.start_hm()
        ttxt = (f"{sh}-{_hm_add(sh, self.minutes)}（{dur_label(self.minutes)}）" if sh and self.minutes
                else (f"{sh}〜（長さをえらんでね）" if sh else "（はじまりと長さをえらんでね）"))
        page = "" if self.page == 0 else f"\n-# いま表示中：{'過去' if self.page < 0 else '先'}の2週間"
        return (f"{TIME_EMOJI.get(self.kind, '⏱')} **{self.kind}を記録**" + (f"（{self.note}）" if self.note else "") + "\n"
                f"📅 {dtxt or '（日付をえらんでね・いくつでも）'}\n🕐 {ttxt}{page}\n"
                "-# ぜんぶ選んだら ✅ 記録する。よく使う時間は 💾 で「いつもの」にしておくと次から速い")

    async def refresh(self, interaction):
        self._build()
        await interaction.response.edit_message(content=self.summary(), view=self)

    async def _prev(self, interaction):
        self.page -= 1
        await self.refresh(interaction)

    async def _next(self, interaction):
        self.page += 1
        await self.refresh(interaction)

    async def _memo(self, interaction):
        await interaction.response.send_modal(_TimeNoteModal(self))

    async def _save_preset(self, interaction):
        sh = self.start_hm()
        if not sh or not self.minutes:
            await interaction.response.send_message("先に「はじまり」と「長さ」をえらんでね。", ephemeral=True)
            return
        await db.execute("INSERT INTO time_presets(user_id,kind,start_hm,minutes,note,ts) VALUES(?,?,?,?,?,?)",
                         (self.uid, self.kind, sh, self.minutes, self.note, int(now_jst().timestamp())))
        await db.execute("DELETE FROM time_presets WHERE user_id=? AND id NOT IN "
                         "(SELECT id FROM time_presets WHERE user_id=? ORDER BY id DESC LIMIT ?)", (self.uid, self.uid, PRESET_MAX))
        await db.commit()
        await interaction.response.send_message(
            f"💾 「{(self.note or self.kind)} {sh}-{_hm_add(sh, self.minutes)}」を「いつもの」に保存しました。"
            "次からは ⏱ の画面でえらぶだけで、日付をタップすれば記録できます。", ephemeral=True)

    async def _commit(self, interaction):
        sh = self.start_hm()
        if not self.days or not sh or not self.minutes:
            miss = "・".join(x for x, ok in (("日付", self.days), ("はじまりの時", sh), ("長さ", self.minutes)) if not ok)
            await interaction.response.send_message(f"⚠️ まだ選んでいないものがあります：{miss}", ephemeral=True)
            return
        days = [date.fromisoformat(x) for x in sorted(self.days)]
        end_hm, tots = await record_time_events(self.uid, self.kind, days, sh, self.minutes, self.note)
        ds = "・".join(f"{md(d.isoformat())}({DAY_CHARS[d.weekday()]})" for d in days[:10]) + ("…" if len(days) > 10 else "")
        await interaction.response.edit_message(
            content=f"✅ {TIME_EMOJI.get(self.kind, '⏱')} **{self.kind}** を {len(days)}日ぶん記録しました"
                    f"（{ds}　{sh}-{end_hm}・各{dur_label(self.minutes)}）\n{tots}", view=None)

async def open_time_entry(interaction, kind, start_hm=None, minutes=None, note=None):
    v = TimeEntryView(interaction.user.id, kind, start_hm, minutes, note)
    await interaction.response.send_message(v.summary(), view=v, ephemeral=True)

class TimeKindSelect(discord.ui.Select):
    """⏱ の画面の「記録する」：いつもの（保存したシフト）→ 種類 の順に並べる"""

    def __init__(self, presets=()):
        opts = [discord.SelectOption(label=f"⭐ {preset_label(p)}"[:100], value=f"p:{p['id']}", emoji=TIME_EMOJI.get(p["kind"], "⏱"),
                                     description=f"いつもの（{p['kind']}）・日付をタップするだけ") for p in presets]
        opts += [discord.SelectOption(label=k, emoji=e, value=f"k:{k}", description="日付・時間をタップでえらぶ") for k, e, _ in TIME_KINDS]
        super().__init__(placeholder="➕ 記録する（いつもの／種類をえらぶ）", options=opts[:25], row=0)

    async def callback(self, interaction):
        v = self.values[0]
        if v.startswith("p:"):
            async with db.execute("SELECT * FROM time_presets WHERE id=? AND user_id=?", (int(v[2:]), str(interaction.user.id))) as c:
                p = await c.fetchone()
            if not p:
                await interaction.response.send_message("その「いつもの」は見つかりませんでした。", ephemeral=True)
                return
            await open_time_entry(interaction, p["kind"], p["start_hm"], p["minutes"], p["note"])
        else:
            await open_time_entry(interaction, v[2:])

class TimeTextKindSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=k, emoji=e, value=k) for k, e, _ in TIME_KINDS]
        super().__init__(placeholder="⌨ 文字で入力する種類をえらぶ", options=opts)

    async def callback(self, interaction):
        await interaction.response.send_modal(TimeEventModal(self.values[0]))

class TimeDelSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=time_line(r)[:100], value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="🗑 記録を消す（この月のぶん）", options=opts, min_values=1, max_values=len(opts), row=1)

    async def callback(self, interaction):
        await db.executemany("DELETE FROM time_events WHERE id=? AND user_id=?", [(int(v), str(interaction.user.id)) for v in self.values])
        await db.commit()
        text, view = await time_hub_message(interaction.user.id, self.view.y, self.view.m)
        await interaction.response.edit_message(content=f"🗑 {len(self.values)}件消しました。\n" + text, view=view)

class PresetDelSelect(discord.ui.Select):
    def __init__(self, presets):
        opts = [discord.SelectOption(label=preset_label(p)[:100], value=str(p["id"]), emoji=TIME_EMOJI.get(p["kind"], "⏱"))
                for p in presets]
        super().__init__(placeholder="🗑 「いつもの」を消す", options=opts, min_values=1, max_values=len(opts), row=3)

    async def callback(self, interaction):
        await db.executemany("DELETE FROM time_presets WHERE id=? AND user_id=?", [(int(v), str(interaction.user.id)) for v in self.values])
        await db.commit()
        text, view = await time_hub_message(interaction.user.id, self.view.y, self.view.m)
        await interaction.response.edit_message(content=f"🗑 「いつもの」を {len(self.values)}件消しました。\n" + text, view=view)

class TimeHubView(discord.ui.View):
    def __init__(self, uid, y, m, rows, presets=()):
        super().__init__(timeout=900)
        self.uid, self.y, self.m = str(uid), y, m
        self.add_item(TimeKindSelect(presets))
        if rows:
            self.add_item(TimeDelSelect(rows))
        if presets:
            self.add_item(PresetDelSelect(presets))

    async def _go(self, interaction, delta):
        y, m = self.y, self.m + delta
        y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
        text, view = await time_hub_message(interaction.user.id, y, m)
        await interaction.response.edit_message(content=text, view=view)

    @discord.ui.button(label="◀ 前の月", style=discord.ButtonStyle.secondary, row=2)
    async def b_prev(self, interaction, button):
        await self._go(interaction, -1)

    @discord.ui.button(label="次の月 ▶", style=discord.ButtonStyle.secondary, row=2)
    async def b_next(self, interaction, button):
        await self._go(interaction, +1)

    @discord.ui.button(label="⌨ 文字で入力", style=discord.ButtonStyle.secondary, row=2)
    async def b_text(self, interaction, button):
        v = discord.ui.View(timeout=600)
        v.add_item(TimeTextKindSelect())
        await interaction.response.send_message(
            "⌨ 文字でまとめて入力（例：日付 `11/3, 11/5` `11/3-11/7`／時間 `17-22` `17:30-22:00` `22-5`）", view=v, ephemeral=True)

async def time_hub_message(uid, y, m):
    d1, d2 = month_bounds(y, m)
    rows = await time_events_between(uid, d1, d2)
    tot = await month_time_totals(uid, y, m)
    presets = await time_presets_of(uid)
    lines = [f"⏱ **{y}年{m}月のバイト・部活など**（自分にだけ表示・記録は消えません）",
             f"**合計**：{time_totals_text(tot)}"]
    if rows:
        lines += [f"・{time_line(r)}" for r in rows[:20]] + ([f"-# ほか {len(rows) - 20} 件"] if len(rows) > 20 else [])
    if not presets:
        lines.append("-# 💡 記録の画面で 💾 を押すと、よく使うシフトを「いつもの」に保存できます（次から日付をタップするだけ）")
    return "\n".join(lines), TimeHubView(uid, y, m, rows, presets)

async def send_time_hub(interaction):
    await ensure_user(interaction.user)
    now = now_jst()
    text, view = await time_hub_message(interaction.user.id, now.year, now.month)
    await interaction.response.send_message(text, view=view, ephemeral=True)

# ---- 🗓 予定カレンダー（大学の予定は帯、自分の課題・試験・実験は日付のマスに。本人にだけ表示） ----
UNIV_COLORS = {"長期休暇": "#e8913a", "履修登録": "#3d8f83", "行事": "#8a6fae", "その他": "#5b7fa6", "試験期間": "#c2527a",
               PRIVATE_KIND: "#9a9184", SHARED_KIND: "#c2527a"}
LEGEND_KINDS = ("長期休暇", "履修登録", "行事", "その他", PRIVATE_KIND, SHARED_KIND)

async def calendar_data(uid, y, m):
    """月表示の材料：(グリッド開始日, 終了日, 大学の予定・じぶんの予定, {日付: [(文字, 色 or None), ...]})
    マスの中身＝⏱バイト・部活など（色ラベル・いちばん上）→ 📝試験 → 🧪実験。課題は載せない（#課題📚 で見る）"""
    first = date(y, m, 1)
    last = date.fromisoformat(month_bounds(y, m)[1])
    gs = first - timedelta(days=first.weekday())
    ge = last + timedelta(days=6 - last.weekday())
    events = list(await univ_events_between(gs.isoformat(), ge.isoformat())) \
        + await private_events_between(uid, gs.isoformat(), ge.isoformat())
    items = {}
    uid = str(uid)
    short = lambda hm: str(int(hm[:2])) + ("" if hm[3:] == "00" else ":" + hm[3:])
    for r in await time_events_between(uid, gs.isoformat(), ge.isoformat()):   # ⏱ バイト・部活（例：💼17-22）
        items.setdefault(r["day"], []).append(
            (f"{TIME_EMOJI.get(r['kind'], '⏱')}{short(r['start_hm'])}-{short(r['end_hm'])}", TIME_COLORS.get(r["kind"], "#8a8378")))
    async with db.execute("SELECT e.title, e.exam_day, c.name AS cname FROM exams e JOIN courses c ON c.code=e.code "
                          "WHERE e.code IN (SELECT code FROM user_courses WHERE user_id=?) AND e.exam_day BETWEEN ? AND ?",
                          (uid, gs.isoformat(), ge.isoformat())) as c:
        for r in await c.fetchall():
            items.setdefault(r["exam_day"], []).append((f"📝{r['cname']}", None))
    for c2 in await user_course_rows(uid):
        for iso, txt in LAB_SCHEDULE.get(c2["code"], {}).items():
            if gs.isoformat() <= iso <= ge.isoformat():
                items.setdefault(iso, []).append((f"🧪{txt}", None))
    return gs, ge, events, items

def render_calendar_card(name, y, m, gs, ge, events, items, today, totals=None):
    """月カレンダー（PNG bytes）。totals=その月のバイト・部活などの合計（month_time_totals）。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    pad, cw, rh, bar_h, bar_gap = 40, 143, 168, 26, 4
    nrows = ((ge - gs).days + 1) // 7
    W = pad * 2 + cw * 7
    top = 150
    H = top + 40 + nrows * rh + 160
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    d.text((pad, 52), f"{y}年{m}月の予定", font=F(54, True), fill=CARD_INK)
    if totals:   # 見出しの下に、その月のバイト・部活などの合計
        tt = "　".join(f"{TIME_EMOJI.get(k, '⏱')}{k} {fmt_mins(mn)}（{n}回）" for k, (mn, n) in totals.items())
        _draw_mixed(d, pad + 4, 118, f"{name} さん　今月の合計：{tt}", F(20), E(20), CARD_MUTED)
    else:
        d.text((pad + 4, 118), f"{name} さんのカレンダー（大学の予定・じぶんの予定・バイト・部活・試験・実験）", font=F(20), fill=CARD_MUTED)
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, W - pad - round(logo.width * 90 / logo.height), 30, 90)
    for i, wd in enumerate("月火水木金土日"):
        col = "#5b7fa6" if i == 5 else ("#c2527a" if i == 6 else CARD_MUTED)
        d.text((pad + i * cw + cw // 2, top + 6), wd, font=F(22, True), fill=col, anchor="ma")
    gy = top + 40

    def fit(text, font, efont, maxw):
        t = text
        while t and _mixed_w(d, t, font, efont) > maxw:
            t = t[:-1]
        return t if t == text else (t[:-1] + "…" if len(t) > 1 else t)

    # マス
    for k in range((ge - gs).days + 1):
        day = gs + timedelta(days=k)
        x, yy = pad + (k % 7) * cw, gy + (k // 7) * rh
        inm = day.month == m
        d.rectangle([x, yy, x + cw, yy + rh], fill="#fffaf2" if inm else "#efe8da", outline=CARD_LINE, width=1)
        if day.isoformat() == today:
            d.rectangle([x + 2, yy + 2, x + cw - 2, yy + rh - 2], outline=CARD_ORANGE, width=3)
        col = CARD_MUTED if not inm else ("#5b7fa6" if day.weekday() == 5 else ("#c2527a" if day.weekday() == 6 else CARD_INK))
        d.text((x + 8, yy + 6), str(day.day), font=F(20, True), fill=col)
    # 大学の予定の帯（重なりは段を分ける・2段まで）
    lanes, lane_of = [], {}
    for ev in sorted(events, key=lambda r: (r["start_day"], r["end_day"])):
        for i, end in enumerate(lanes):
            if end < ev["start_day"]:
                lanes[i] = ev["end_day"]
                lane_of[ev["id"]] = i
                break
        else:
            lanes.append(ev["end_day"])
            lane_of[ev["id"]] = len(lanes) - 1
    used = {}
    for ev in events:
        ln = lane_of[ev["id"]]
        if ln >= 2:
            continue
        s = max(date.fromisoformat(ev["start_day"]), gs)
        e = min(date.fromisoformat(ev["end_day"]), ge)
        cur = s
        while cur <= e:
            row = (cur - gs).days // 7
            seg_end = min(e, gs + timedelta(days=row * 7 + 6))
            c1, c2 = (cur - gs).days % 7, (seg_end - gs).days % 7
            bx1, bx2 = pad + c1 * cw + 4, pad + (c2 + 1) * cw - 4
            by = gy + row * rh + 34 + ln * (bar_h + bar_gap)
            d.rounded_rectangle([bx1, by, bx2, by + bar_h], radius=8, fill=UNIV_COLORS.get(ev["kind"], "#5b7fa6"))
            label = ("▶ " if cur > date.fromisoformat(ev["start_day"]) else "") + ev["title"]
            d.text((bx1 + 8, by + 3), fit(label, F(16, True), None, bx2 - bx1 - 14), font=F(16, True), fill="#ffffff")
            for k2 in range((cur - gs).days, (seg_end - gs).days + 1):
                used[k2] = max(used.get(k2, 0), ln + 1)
            cur = seg_end + timedelta(days=1)
    # 自分の予定（⏱バイト・部活は色ラベル、📝試験・🧪実験は文字）
    for k in range((ge - gs).days + 1):
        day = gs + timedelta(days=k)
        lst = [it if isinstance(it, tuple) else (it, None) for it in (items.get(day.isoformat()) or [])]
        if not lst:
            continue
        x, yy = pad + (k % 7) * cw, gy + (k // 7) * rh
        iy = yy + 34 + used.get(k, 0) * (bar_h + bar_gap) + 2
        room = (yy + rh - 6 - iy) // 24
        show = lst if len(lst) <= room else lst[:max(0, room - 1)]
        for t, col in show:
            if col:
                d.rounded_rectangle([x + 4, iy, x + cw - 4, iy + 21], radius=6, fill=col)
                _draw_mixed(d, x + 9, iy + 1, fit(t, F(15, True), E(15), cw - 18), F(15, True), E(15), "#ffffff")
            else:
                _draw_mixed(d, x + 6, iy + 1, fit(t, F(15), E(15), cw - 12), F(15), E(15), CARD_INK)
            iy += 24
        if len(show) < len(lst):
            d.text((x + 6, iy), f"ほか{len(lst) - len(show)}件", font=F(14), fill=CARD_MUTED)
    # 凡例（1行目＝帯の色、2行目＝マスの中のラベル）
    ly = gy + nrows * rh + 16
    x = pad
    for kind in LEGEND_KINDS:
        d.rounded_rectangle([x, ly + 4, x + 34, ly + 24], radius=6, fill=UNIV_COLORS[kind])
        d.text((x + 42, ly), kind, font=F(20), fill=CARD_MUTED)
        x += 42 + int(d.textlength(kind, font=F(20))) + 26
    x = pad
    for kind, ic, col in TIME_KINDS:
        lb = f"{ic}{kind}"
        w = int(_mixed_w(d, lb, F(17, True), E(17))) + 16
        d.rounded_rectangle([x, ly + 36, x + w, ly + 60], radius=6, fill=col)
        _draw_mixed(d, x + 8, ly + 38, lb, F(17, True), E(17), "#ffffff")
        x += w + 14
    _draw_mixed(d, x + 10, ly + 38, "📝試験　🧪実験", F(20), E(20), CARD_MUTED)
    d.line([pad, H - 64, W - pad, H - 64], fill=CARD_LINE, width=2)
    d.text((pad, H - 50), "最低限生活リズムサークル", font=F(24, True), fill=CARD_INK)
    d.text((W - pad, H - 46), "予定は「大学の予定」ボタンから誰でも追加できます", font=F(18), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

# ---- 📊 タイムライン：種類ごとの行（スイムレーン）に、期間ものは帯・1日だけは◆で並べる（予定が増えても行は増えない） ----
TIMELINE_MONTHS = 4        # 最低この月数ぶんは表示（今月を含む）
TIMELINE_MAX_MONTHS = 7    # 先の予定があってもここまで
TIMELINE_LANES = [("長期休暇", "🌸", "長期休暇"), ("履修登録", "📋", "履修登録"), ("行事", "🎪", "行事"),
                  ("その他", "📌", "その他"), ("試験期間", "📝", "試験期間"),
                  (SHARED_KIND, "📣", "宣言した予定"), (PRIVATE_KIND, "🔒", "自分だけ")]

async def timeline_data(uid, today):
    """(表示はじめ, おわり, 予定ぜんぶ, {(年,月): バイト・部活などの合計分})"""
    t = date.fromisoformat(today)
    start = t.replace(day=1)
    y, m = start.year, start.month + TIMELINE_MONTHS - 1
    y, m = y + (m - 1) // 12, (m - 1) % 12 + 1
    end = date.fromisoformat(month_bounds(y, m)[1])
    y2, m2 = start.year, start.month + TIMELINE_MAX_MONTHS - 1
    y2, m2 = y2 + (m2 - 1) // 12, (m2 - 1) % 12 + 1
    cap = date.fromisoformat(month_bounds(y2, m2)[1])
    evs = sorted(list(await univ_events_between(today, cap.isoformat())) + await private_events_between(uid, today, cap.isoformat()),
                 key=lambda r: (r["start_day"], r["end_day"]))
    if evs:   # 先の予定まで届くように右端を延ばす（上限あり）
        far = max(date.fromisoformat(r["start_day"]) for r in evs)
        if far > end:
            end = min(cap, date.fromisoformat(month_bounds(far.year, far.month)[1]))
    evs = [r for r in evs if r["start_day"] <= end.isoformat()]
    mins = {}
    for r in await time_events_between(uid, start.isoformat(), end.isoformat()):
        k = (int(r["day"][:4]), int(r["day"][5:7]))
        mins[k] = mins.get(k, 0) + r["minutes"]
    return start, end, evs, mins

def _pack_lanes(items, key_start, key_end, gap_days=0):
    """重ならないように段に詰める → [[item, ...], ...]（期間が重なるものだけ下の段へ）"""
    lanes = []
    for it in sorted(items, key=lambda r: (key_start(r), key_end(r))):
        for ln in lanes:
            if key_end(ln[-1]) + timedelta(days=gap_days) < key_start(it):
                ln.append(it)
                break
        else:
            lanes.append([it])
    return lanes

def render_timeline_card(name, start, end, events, month_mins, today):
    """タイムライン（PNG bytes）。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    W, pad, label_w = 1080, 40, 150
    x0, x1 = pad + label_w, W - pad
    total = (end - start).days + 1
    X = lambda dd: x0 + (x1 - x0) * ((dd - start).days / total)
    dmeasure = ImageDraw.Draw(PImage.new("RGB", (8, 8)))
    BAR_H, BAR_GAP, PIN_ROW, TIER_H, LANE_PAD = 30, 8, 26, 24, 14
    sd = lambda r: date.fromisoformat(r["start_day"])
    ed = lambda r: date.fromisoformat(r["end_day"])
    # 種類ごとに：期間ものは段に詰めた帯、1日だけは◆（名前は重ならない段に）
    lanes = []
    for kind, ic, label in TIMELINE_LANES:
        evs = [r for r in events if r["kind"] == kind]
        if not evs:
            continue
        spans = [r for r in evs if r["end_day"] > r["start_day"]]
        pins = sorted([r for r in evs if r["end_day"] == r["start_day"]], key=sd)
        bar_rows = _pack_lanes(spans, sd, ed)
        tiers, placed = [], []
        for r in pins:
            px = X(max(sd(r), start) + timedelta(hours=12))
            txt = f"{int(r['start_day'][5:7])}/{int(r['start_day'][8:])} {r['title']}"   # 年は見出しで分かるので月/日だけ
            w = _mixed_w(dmeasure, txt, F(17, True), E(17)) + 10
            lx = min(max(px - 7, x0), x1 - w)
            for ti, used in enumerate(tiers):
                if all(lx >= b + 6 or lx + w <= a - 6 for a, b in used):
                    used.append((lx, lx + w))
                    placed.append((r, px, lx, ti, txt))
                    break
            else:
                tiers.append([(lx, lx + w)])
                placed.append((r, px, lx, len(tiers) - 1, txt))
        h = LANE_PAD * 2 + len(bar_rows) * (BAR_H + BAR_GAP) + (PIN_ROW + len(tiers) * TIER_H if pins else 0)
        lanes.append({"kind": kind, "ic": ic, "label": label, "bars": bar_rows, "pins": placed, "h": max(h, 64)})
    top = 160
    head_h = 62 if month_mins else 44
    gy = top + head_h
    gh = sum(l["h"] for l in lanes) if lanes else 90
    H = gy + gh + 112
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    d.text((pad, 46), "これからの予定", font=F(54, True), fill=CARD_INK)
    d.text((pad + 4, 114), f"{name} さんのタイムライン", font=F(20), fill=CARD_MUTED)
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, W - pad - round(logo.width * 90 / logo.height), 30, 90)
    # 月：交互の地色・区切り線・月名（年が変わるところに年）・その月のバイト・部活の合計
    cur, i = start, 0
    while cur <= end:
        nxt = (cur + timedelta(days=32)).replace(day=1)
        xa, xb = X(cur), X(min(nxt, end + timedelta(days=1)))
        if i % 2 == 0:
            d.rectangle([xa, gy, xb, gy + gh], fill="#fffaf2")
        d.line([xa, top + 6, xa, gy + gh], fill=CARD_LINE, width=2)
        d.text(((xa + xb) / 2, top + 6), f"{cur.month}月", font=F(24, True), fill=CARD_INK, anchor="ma")
        if cur.month == 1 and i > 0:
            d.text((xa + 4, top - 16), f"{cur.year}年", font=F(15, True), fill=CARD_ORANGE)
        mm = month_mins.get((cur.year, cur.month))
        if mm:
            d.text(((xa + xb) / 2, top + 36), f"活動 {fmt_mins(mm)}", font=F(15, True), fill=CARD_MUTED, anchor="ma")
        cur, i = nxt, i + 1
    d.line([x1, top + 6, x1, gy + gh], fill=CARD_LINE, width=2)
    # 行（種類ごと）
    yy = gy
    for ln in lanes:
        col = UNIV_COLORS.get(ln["kind"], "#5b7fa6")
        d.line([pad, yy, x1, yy], fill=CARD_LINE, width=1)
        d.rounded_rectangle([pad, yy + ln["h"] / 2 - 18, pad + 8, yy + ln["h"] / 2 + 18], radius=4, fill=col)
        _draw_mixed(d, pad + 18, int(yy + ln["h"] / 2 - 14), f"{ln['ic']} {ln['label']}", F(20, True), E(20), CARD_INK)
        by = yy + LANE_PAD
        for row in ln["bars"]:
            for r in row:
                s, e = max(sd(r), start), min(ed(r), end)
                bx1, bx2 = X(s), max(X(e + timedelta(days=1)), X(s) + 10)
                d.rounded_rectangle([bx1, by, bx2, by + BAR_H], radius=BAR_H // 2, fill=col)
                mdt = lambda iso: f"{int(iso[5:7])}/{int(iso[8:])}"
                txt = f"{r['title']}　{mdt(r['start_day'])}〜{mdt(r['end_day'])}"
                tf = F(16, True)
                if d.textlength(txt, font=tf) + 24 > bx2 - bx1:
                    txt = r["title"]
                if d.textlength(txt, font=tf) + 24 <= bx2 - bx1:
                    d.text((bx1 + 12, by + 5), txt, font=tf, fill="#ffffff")
                else:   # 帯が短くて入らない名前は帯の右に、右端からはみ出すなら帯の左に、色文字で
                    w = d.textlength(r["title"], font=tf)
                    lx = bx2 + 6 if bx2 + 6 + w <= x1 - 4 else max(x0 + 2, bx1 - 6 - w)
                    d.text((lx, by + 5), r["title"], font=tf, fill=col)
            by += BAR_H + BAR_GAP
        if ln["pins"]:
            py = by + 6
            d.line([x0, py + 8, x1, py + 8], fill=CARD_LINE, width=1)
            for r, px, lx, ti, txt in ln["pins"]:
                d.polygon([(px, py), (px + 8, py + 8), (px, py + 16), (px - 8, py + 8)], fill=col)
                ty = py + PIN_ROW + ti * TIER_H - 4
                if ti:
                    d.line([px, py + 16, px, ty + 2], fill=col, width=1)
                _draw_mixed(d, int(lx), int(ty), txt, F(17, True), E(17), CARD_INK)
        yy += ln["h"]
    if not lanes:
        d.text(((x0 + x1) / 2, gy + 30), "まだ予定がありません（#予定🗓 の 🏫 と 🙋 から登録できます）", font=F(20), fill=CARD_MUTED, anchor="ma")
    d.line([pad, gy + gh, x1, gy + gh], fill=CARD_LINE, width=1)
    td = date.fromisoformat(today)
    if start <= td <= end:   # 今日の線
        tx = X(td) + (x1 - x0) / total / 2
        d.line([tx, top + 30, tx, gy + gh], fill=CARD_ORANGE, width=3)
        d.rounded_rectangle([tx - 30, gy + gh + 6, tx + 30, gy + gh + 32], radius=13, fill=CARD_ORANGE)
        d.text((tx, gy + gh + 9), "今日", font=F(17, True), fill="#ffffff", anchor="ma")
    d.line([pad, H - 58, W - pad, H - 58], fill=CARD_LINE, width=2)
    d.text((pad, H - 46), "最低限生活リズムサークル", font=F(24, True), fill=CARD_INK)
    d.text((W - pad, H - 42), "◆＝1日だけの予定　月名の下＝その月のバイト・部活などの合計", font=F(17), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def send_timeline(interaction):
    await interaction.response.defer(ephemeral=True)
    today = day_str(now_jst())
    start, end, evs, mins = await timeline_data(interaction.user.id, today)
    buf = await asyncio.to_thread(render_timeline_card, interaction.user.display_name, start, end, evs, mins, today)
    if not buf:
        await interaction.followup.send("⚠️ タイムラインを作れませんでした（VMに日本語フォントが必要）。", ephemeral=True)
        return
    await interaction.followup.send(f"📊 **これからの予定**（{start.month}月〜{end.month}月・自分にだけ表示）",
                                    file=discord.File(buf, filename="timeline.png"), ephemeral=True)

def calendar_buttons(view, row=2):
    """📊タイムライン・🗓今月/来月のカレンダー・🔒自分だけの予定 のボタンを view に足す"""
    b = discord.ui.Button(label="📊 タイムライン", style=discord.ButtonStyle.primary, row=row)

    async def _tl(interaction):
        await send_timeline(interaction)
    b.callback = _tl
    view.add_item(b)
    for off, lb in ((0, "🗓 今月"), (1, "🗓 来月")):
        b = discord.ui.Button(label=lb, style=discord.ButtonStyle.secondary, row=row)

        async def _cb(interaction, off=off):
            await send_calendar(interaction, off)
        b.callback = _cb
        view.add_item(b)
    b = discord.ui.Button(label="🙋 じぶんの予定", style=discord.ButtonStyle.secondary, row=row)

    async def _pv(interaction):
        await send_private_hub(interaction)
    b.callback = _pv
    view.add_item(b)
    return view

async def send_calendar(interaction, offset=0):
    await interaction.response.defer(ephemeral=True)
    now = now_jst()
    y, m = now.year, now.month + offset
    if m > 12:
        y, m = y + 1, m - 12
    gs, ge, events, items = await calendar_data(interaction.user.id, y, m)
    totals = await month_time_totals(interaction.user.id, y, m)
    buf = await asyncio.to_thread(render_calendar_card, interaction.user.display_name, y, m, gs, ge, events, items, day_str(now),
                                  totals)
    if not buf:
        await interaction.followup.send("⚠️ カレンダーを作れませんでした（VMに日本語フォントが必要）。", ephemeral=True)
        return
    await interaction.followup.send(f"🗓 **{m}月の予定**（自分にだけ表示）", file=discord.File(buf, filename=f"calendar-{y}-{m:02d}.png"),
                                    ephemeral=True)

# ---- 🌱 新学期の準備：前の学期の科目をまとめて外して、そのまま新しい科目の登録へ ----
async def current_term():
    """いまの科目マスタの学期（マスタ科目でいちばん多いterm）。不明なら None"""
    async with db.execute("SELECT term, COUNT(*) AS n FROM courses WHERE custom=0 AND term IS NOT NULL AND term<>'' "
                          "GROUP BY term ORDER BY n DESC LIMIT 1") as c:
        r = await c.fetchone()
    return r["term"] if r else None

async def newterm_split(uid):
    """自分の履修を (前の学期のマスタ科目, 自由入力の科目, いまの学期の科目数, いまの学期名) に分ける"""
    term = await current_term()
    async with db.execute("SELECT c.code, c.name, c.term, c.custom FROM user_courses uc JOIN courses c ON c.code=uc.code "
                          "WHERE uc.user_id=? ORDER BY c.name", (str(uid),)) as c:
        rows = await c.fetchall()
    old = [r for r in rows if not r["custom"] and term and r["term"] and r["term"] != term]
    custom = [r for r in rows if r["custom"]]
    cur_n = len(rows) - len(old) - len(custom)
    return old, custom, cur_n, term

async def drop_courses(uid, codes):
    if not codes:
        return 0
    q = ",".join("?" * len(codes))
    cur = await db.execute(f"DELETE FROM user_courses WHERE user_id=? AND code IN ({q})", (str(uid), *codes))
    await db.execute(f"DELETE FROM attendance WHERE user_id=? AND code IN ({q})", (str(uid), *codes))
    await db.commit()
    return cur.rowcount

class NewTermCustomSelect(discord.ui.Select):
    def __init__(self, rows):
        opts = [discord.SelectOption(label=r["name"][:100], value=r["code"]) for r in rows[:25]]
        super().__init__(placeholder="✍ 自分で足した科目も外す（サークル・バイトなどは残してOK）", options=opts,
                         min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        n = await drop_courses(interaction.user.id, list(self.values))
        await interaction.response.send_message(f"🗑 自分で足した科目を {n} 件外しました。", ephemeral=True)

class NewTermView(discord.ui.View):
    def __init__(self, old, custom):
        super().__init__(timeout=900)
        self.old_codes = [r["code"] for r in old]
        b = discord.ui.Button(label=f"🗑 前の学期の科目（{len(old)}）をまとめて外す", style=discord.ButtonStyle.danger,
                              disabled=not old, row=1)
        b.callback = self._drop_old
        self.add_item(b)
        if custom:
            self.add_item(NewTermCustomSelect(custom))

    async def _drop_old(self, interaction):
        n = await drop_courses(interaction.user.id, self.old_codes)
        self.old_codes = []
        for it in self.children:
            if isinstance(it, discord.ui.Button) and it.label.startswith("🗑"):
                it.disabled = True
        await interaction.response.edit_message(
            content=f"🗑 前の学期の科目を **{n}件** 外しました（欠席カウンターも一緒にリセット）。\n下の「🎓 新しい科目を登録」からどうぞ！",
            view=self)

    @discord.ui.button(label="🧩 空きコマ予定もリセット", style=discord.ButtonStyle.secondary, row=2)
    async def b_slots(self, interaction, button):
        cur = await db.execute("DELETE FROM user_slots WHERE user_id=?", (str(interaction.user.id),))
        await db.commit()
        await interaction.response.send_message(f"🧩 空きコマ予定を {cur.rowcount} 件消しました。新しい予定は 🧩 から。", ephemeral=True)

    @discord.ui.button(label="🎓 新しい科目を登録", style=discord.ButtonStyle.primary, row=2)
    async def b_add(self, interaction, button):
        await interaction.response.send_modal(CourseSearchModal())

# ---- 🗒 科目メモ（履修者で共有：教室変更・持ち物・テスト範囲など。その授業の日の朝ダイジェストにも出る） ----
COURSE_NOTE_MAX = 10   # 1科目あたり（古いものから消える）

async def course_notes_for(code, limit=COURSE_NOTE_MAX):
    async with db.execute("SELECT n.id, n.text, n.user_id, n.ts, COALESCE(u.name,'?') AS uname FROM course_notes n "
                          "LEFT JOIN users u ON u.id=n.user_id WHERE n.code=? ORDER BY n.id DESC LIMIT ?", (code, limit)) as c:
        return await c.fetchall()

async def add_course_note(code, uid, text):
    await db.execute("INSERT INTO course_notes(code,text,user_id,ts) VALUES(?,?,?,?)",
                     (code, text[:200], str(uid), int(now_jst().timestamp())))
    await db.execute("DELETE FROM course_notes WHERE code=? AND id NOT IN "
                     "(SELECT id FROM course_notes WHERE code=? ORDER BY id DESC LIMIT ?)", (code, code, COURSE_NOTE_MAX))
    await db.commit()

def course_notes_text(cname, rows):
    if not rows:
        return f"🗒 **{cname}** のメモはまだありません。\n-# 教室変更・持ち物・テスト範囲など、履修者みんなに役立つことを書いておけます"
    return f"🗒 **{cname}** のメモ（新しい順・履修者みんなで共有）\n" + "\n".join(
        f"・{r['text']}（{r['uname']}・{datetime.fromtimestamp(r['ts'], JST).strftime('%m/%d')}）" for r in rows)

class CourseNoteModal(discord.ui.Modal, title="🗒 科目メモを書く"):
    t_in = discord.ui.TextInput(label="メモ（履修者みんなに見えます）", style=discord.TextStyle.paragraph, max_length=200,
                                placeholder="来週は2-428で実施／電卓持参／小テストは第3回まで など")

    def __init__(self, code, cname):
        super().__init__()
        self.code, self.cname = code, cname

    async def on_submit(self, interaction):
        await add_course_note(self.code, interaction.user.id, str(self.t_in.value).strip())
        await interaction.response.send_message(
            course_notes_text(self.cname, await course_notes_for(self.code)) + "\n-# ✅ 書きました。この授業がある日の☀️朝の予定にも出ます",
            ephemeral=True)

class CourseNoteDelSelect(discord.ui.Select):
    def __init__(self, code, cname, rows):
        self.code, self.cname = code, cname
        opts = [discord.SelectOption(label=r["text"][:100], value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="🗑 自分のメモを消す", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        await db.executemany("DELETE FROM course_notes WHERE id=? AND user_id=?", [(int(v), str(interaction.user.id)) for v in self.values])
        await db.commit()
        await interaction.response.edit_message(content=course_notes_text(self.cname, await course_notes_for(self.code)), view=None)

class CourseNoteView(discord.ui.View):
    def __init__(self, code, cname, rows, uid):
        super().__init__(timeout=600)
        self.code, self.cname = code, cname
        mine = [r for r in rows if r["user_id"] == str(uid)]
        if mine:
            self.add_item(CourseNoteDelSelect(code, cname, mine))

    @discord.ui.button(label="✏️ メモを書く", style=discord.ButtonStyle.primary, row=1)
    async def b_add(self, interaction, button):
        await interaction.response.send_modal(CourseNoteModal(self.code, self.cname))

class CourseNotePickSelect(discord.ui.Select):
    def __init__(self, rows):
        self.names = {r["code"]: r["name"] for r in rows}
        opts = [discord.SelectOption(label=r["name"][:100], value=r["code"], description=(r["slots"] or "")[:100]) for r in rows[:25]]
        super().__init__(placeholder="🗒 メモを見る・書く科目を選ぶ", options=opts)

    async def callback(self, interaction):
        code = self.values[0]
        rows = await course_notes_for(code)
        await interaction.response.edit_message(content=course_notes_text(self.names[code], rows),
                                                view=CourseNoteView(code, self.names[code], rows, interaction.user.id))

async def today_course_notes(codes, limit=3):
    """今日の授業の科目メモ（各科目の最新1件・全体でlimit件まで）"""
    out = []
    for code in codes:
        rows = await course_notes_for(code, 1)
        if rows:
            c = await get_course(code)
            out.append(f"**{c['name'] if c else code}**：{rows[0]['text']}")
        if len(out) >= limit:
            break
    return out

async def send_exam_hub(interaction):
    """📝 試験日カウントダウンの登録・一覧（#予定🗓 のパネルから）"""
    await ensure_user(interaction.user)
    rows = await user_course_rows(interaction.user.id)
    if not rows:
        await interaction.response.send_message("先に #課題📚 の 🎓 で履修科目を登録してください（試験日は科目ごとに登録します）。", ephemeral=True)
        return
    exs = await exam_rows_for(str(interaction.user.id))
    view = discord.ui.View(timeout=600)
    view.add_item(ExamCourseSelect(rows))
    if exs:
        view.add_item(ExamRemoveSelect(exs))
    lines = []
    for e in exs[:15]:
        left = exam_days_left(e["exam_day"])
        lines.append(f"・**{e['cname']}**「{e['title']}」{exam_date_str(e['exam_day'])}　" + ("**今日！🔥**" if left == 0 else f"**あと{left}日**"))
    txt = ("📝 **試験日カウントダウン**（30日前・1週間前・3日前に #課題📚 で履修者へ、前日の🌙・当日の☀️はあなたにだけお知らせ）\n"
           + ("\n".join(lines) if lines else "-# 登録されている試験はまだありません"))
    await interaction.response.send_message(txt, view=view, ephemeral=True)

async def send_today_plan(interaction):
    """📅 今日の予定（☀️の返事と同じ：天気＋時間割＋課題・試験・残タスク。うっかり消した用）"""
    await ensure_user(interaction.user)
    await interaction.response.defer(ephemeral=True, thinking=True)   # 天気の取得に少しかかる
    now = now_jst()
    parts = []
    try:
        w = await fetch_weather()
        if w:
            parts.append(w)
    except Exception:
        pass
    digest = await today_digest(interaction.user.id, now)
    parts.append(digest or "今日の授業・課題・試験はありません。のんびりいこう")
    await interaction.followup.send(f"📅 **{now.month}/{now.day}({DAY_CHARS[now.weekday()]}) の予定**\n" + "\n".join(parts),
                                    ephemeral=True)

async def send_tomorrow_plan(interaction):
    """🗓 明日の予定（明日の天気＋時間割＋課題・試験を前日の夜から確認）"""
    await ensure_user(interaction.user)
    await interaction.response.defer(ephemeral=True, thinking=True)
    asu = now_jst() + timedelta(days=1)
    parts = []
    try:
        w = await fetch_weather(1)
        if w:
            parts.append(w)
    except Exception:
        pass
    digest = await today_digest(interaction.user.id, asu, label="明日")
    parts.append(digest or "明日の授業・課題・試験はありません。のんびりいこう")
    await interaction.followup.send(f"🗓 **{asu.month}/{asu.day}({DAY_CHARS[asu.weekday()]}) の予定**\n" + "\n".join(parts),
                                    ephemeral=True)

async def send_upcoming_plan(interaction):
    """📆 今後の予定（7日の課題・30日の試験・大学の予定とじぶんの予定・実験）"""
    await ensure_user(interaction.user)
    await interaction.response.defer(ephemeral=True)
    text = await upcoming_digest(interaction.user.id, now_jst())
    await interaction.followup.send("📆 **今後の予定**\n" + (text or "7日以内の課題・試験・実験はありません。のんびりいこう"),
                                    view=calendar_buttons(discord.ui.View(timeout=600), row=0), ephemeral=True)

class YoteiPanelView(discord.ui.View):
    """🗓 #予定 のパネル：大学の予定・試験日・じぶんの予定・タイムライン・カレンダー"""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📅 今日の予定", style=discord.ButtonStyle.success, custom_id="sk_today", row=0)
    async def today_btn(self, interaction, button):
        await send_today_plan(interaction)

    @discord.ui.button(label="🌙 明日の予定", style=discord.ButtonStyle.success, custom_id="sk_tomorrow", row=0)
    async def tomorrow_btn(self, interaction, button):
        await send_tomorrow_plan(interaction)

    @discord.ui.button(label="📆 今後の予定", style=discord.ButtonStyle.success, custom_id="sk_upcoming", row=0)
    async def upcoming_btn(self, interaction, button):
        await send_upcoming_plan(interaction)

    @discord.ui.button(label="🏫 大学の予定", style=discord.ButtonStyle.primary, custom_id="sk_univ", row=1)
    async def univ_btn(self, interaction, button):
        await ensure_user(interaction.user)
        text, v = await univ_hub_message(interaction.user)
        await interaction.response.send_message(text, view=v, ephemeral=True)

    @discord.ui.button(label="🙋 じぶんの予定", style=discord.ButtonStyle.primary, custom_id="sk_mine", row=1)
    async def mine_btn(self, interaction, button):
        await ensure_user(interaction.user)
        await send_private_hub(interaction)

    @discord.ui.button(label="⏱ バイト・部活", style=discord.ButtonStyle.primary, custom_id="sk_time", row=1)
    async def time_btn(self, interaction, button):
        await send_time_hub(interaction)

    @discord.ui.button(label="📝 試験日", style=discord.ButtonStyle.secondary, custom_id="sk_exam", row=1)
    async def exam_btn(self, interaction, button):
        await send_exam_hub(interaction)

    @discord.ui.button(label="📊 タイムライン", style=discord.ButtonStyle.success, custom_id="sk_timeline", row=2)
    async def tl_btn(self, interaction, button):
        await ensure_user(interaction.user)
        await send_timeline(interaction)

    @discord.ui.button(label="🗓 今月のカレンダー", style=discord.ButtonStyle.secondary, custom_id="sk_cal0", row=2)
    async def cal0_btn(self, interaction, button):
        await ensure_user(interaction.user)
        await send_calendar(interaction, 0)

    @discord.ui.button(label="🗓 来月のカレンダー", style=discord.ButtonStyle.secondary, custom_id="sk_cal1", row=2)
    async def cal1_btn(self, interaction, button):
        await ensure_user(interaction.user)
        await send_calendar(interaction, 1)

VIEW_FACTORY["yotei"] = YoteiPanelView

class KadaiPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🌱 新学期の準備", style=discord.ButtonStyle.secondary, custom_id="sk_newterm", row=2)
    async def newterm_btn(self, interaction, button):
        await ensure_user(interaction.user)
        old, custom, cur_n, term = await newterm_split(interaction.user.id)
        if not old and not custom:
            await interaction.response.send_message(
                f"🌱 前の学期の科目はありません（いまの時間割：{term or '不明'}・登録 {cur_n} 科目）。\n"
                "-# 新学期の時間割がBotに入ると、ここから古い科目をまとめて外せるようになります", ephemeral=True)
            return
        lines = [f"🌱 **新学期の準備**（いまの時間割：{term}）"]
        if old:
            lines.append(f"前の学期の科目 **{len(old)}件**：" + "、".join(r["name"] for r in old[:12]) + ("…" if len(old) > 12 else ""))
        if custom:
            lines.append(f"自分で足した科目 {len(custom)}件（残すか選べます）")
        lines.append("-# 外すのは自分の登録だけ。みんなの課題や試験日はそのまま残ります")
        await interaction.response.send_message("\n".join(lines), view=NewTermView(old, custom), ephemeral=True)

    @discord.ui.button(label="🗒 科目メモ", style=discord.ButtonStyle.secondary, custom_id="sk_cnote", row=2)
    async def cnote_btn(self, interaction, button):
        await ensure_user(interaction.user)
        rows = await user_course_rows(interaction.user.id)
        if not rows:
            await interaction.response.send_message("先に 🎓 で履修科目を登録してください。", ephemeral=True)
            return
        v = discord.ui.View(timeout=600)
        v.add_item(CourseNotePickSelect(list(rows)))
        await interaction.response.send_message(
            "🗒 **科目メモ**：履修者みんなで共有するメモ（教室変更・持ち物・テスト範囲など）。授業がある日の朝の予定にも出ます",
            view=v, ephemeral=True)

    @discord.ui.button(label="🎓 履修科目を登録", style=discord.ButtonStyle.primary, custom_id="sk_jw_add", row=0)
    async def jw_add(self, interaction, button):
        await interaction.response.send_modal(CourseSearchModal())

    @discord.ui.button(label="➕ 課題を登録", style=discord.ButtonStyle.success, custom_id="sk_kd_add", row=0)
    async def kd_add(self, interaction, button):
        rows = await user_course_rows(interaction.user.id)
        if not rows:
            await interaction.response.send_message("先に「🎓 履修科目を登録」で科目を登録してください。", ephemeral=True)
            return
        view = discord.ui.View(timeout=300)
        view.add_item(KadaiCourseSelect(rows))
        await interaction.response.send_message("📌 どの科目の課題ですか？", view=view, ephemeral=True)

    @discord.ui.button(label="📖 履修一覧", style=discord.ButtonStyle.secondary, custom_id="sk_jw_list", row=1)
    async def jw_list(self, interaction, button):
        rows = await user_course_rows(interaction.user.id)
        if not rows:
            await interaction.response.send_message("まだ履修科目がありません。「🎓 履修科目を登録」からどうぞ！", ephemeral=True)
            return
        lines = []
        for r in rows:
            n = len(await takers_of(r["code"])) - 1
            lines.append(f"・{course_label(r)}" + (f"　👥{n}" if n > 0 else ""))
        await interaction.response.send_message(embed=discord.Embed(
            title=f"📚 {interaction.user.display_name} の履修科目（{len(rows)}）",
            description="\n".join(lines)[:4000], color=discord.Color.gold()), ephemeral=True)

    @discord.ui.button(label="📚 課題一覧", style=discord.ButtonStyle.secondary, custom_id="sk_kd_list", row=1)
    async def kd_list(self, interaction, button):
        emb = await kadai_list_embed(str(interaction.user.id))
        if not emb:
            await interaction.response.send_message("いま登録されている課題はありません 🎉", ephemeral=True)
            return
        view = await kadai_list_view(str(interaction.user.id))
        await interaction.response.send_message(embed=emb, view=view or discord.ui.View(), ephemeral=True)

    @discord.ui.button(label="🗓 時間割画像", style=discord.ButtonStyle.secondary, custom_id="sk_jw_img", row=1)
    async def jw_img(self, interaction, button):
        await ensure_user(interaction.user)
        await send_timetable(interaction)

    @discord.ui.button(label="🚨 欠席カウンター", style=discord.ButtonStyle.secondary, custom_id="sk_kesseki", row=2)
    async def kesseki_btn(self, interaction, button):
        await ensure_user(interaction.user)
        rows = await user_course_rows(interaction.user.id)
        if not rows:
            await interaction.response.send_message("先に 🎓 で履修科目を登録してください。", ephemeral=True)
            return
        counts = await kesseki_map(interaction.user.id)
        view = discord.ui.View(timeout=600)
        view.add_item(KessekiSelect(rows, counts))
        tracked = [f"・**{(await get_course(code))['name']}**　{r['absent']}/{r['limit_n']}" for code, r in list(counts.items())[:10] if await get_course(code)]
        await interaction.response.send_message(
            "🚨 **欠席カウンター**（下のプルダウンで選ぶと+1）\n" + ("\n".join(tracked) + "\n" if tracked else "")
            + "-# 落単ラインは既定4回。`/kesseki set` で科目ごとに変更、間違えたら `/kesseki add kaisu:-1`",
            view=view, ephemeral=True)

    @discord.ui.button(label="🧩 空きコマ予定", style=discord.ButtonStyle.secondary, custom_id="sk_jw_free", row=2)
    async def jw_free(self, interaction, button):
        await ensure_user(interaction.user)
        async with db.execute("SELECT slot, label, place FROM user_slots WHERE user_id=? ORDER BY slot", (str(interaction.user.id),)) as c:
            rows = await c.fetchall()
        view = discord.ui.View(timeout=600)
        view.add_item(FreeSlotAddButton())
        if rows:
            view.add_item(FreeSlotRemoveSelect(rows))
        cur = "\n".join(f"・{r['slot']}　**{r['label']}**" + (f"（{r['place']}）" if r["place"] else "") for r in rows[:20])
        await interaction.response.send_message(
            "🧩 **空きコマの予定**（バイト・部活など。時間割画像と☀️朝の時間割に出ます・自分にだけ）\n"
            + (cur if rows else "-# まだ登録がありません"), view=view, ephemeral=True)

    @discord.ui.button(label="🔁 毎週課題", style=discord.ButtonStyle.success, custom_id="sk_kd_teiki", row=0)
    async def kd_teiki(self, interaction, button):
        await ensure_user(interaction.user)
        rows = await user_course_rows(interaction.user.id)
        if not rows:
            await interaction.response.send_message("先に 🎓 で履修科目を登録してください。", ephemeral=True)
            return
        rules = await teiki_rules_for(str(interaction.user.id))
        view = discord.ui.View(timeout=600)
        view.add_item(TeikiCourseSelect(rows))
        if rules:
            view.add_item(TeikiEditSelect(rules))
            view.add_item(TeikiRemoveSelect(rules))
        txt = ("🔁 **毎週課題**（プリント・出席フォームなど毎週出る課題は、1回登録すれば毎週自動で課題になります。"
               "授業当日の朝には次の回のぶんも登場しているので、課題をもらった授業の直後に✅もできます）\n"
               + ("\n".join("・" + teiki_label(r) for r in rules[:15]) if rules else "-# まだ登録がありません")
               + "\n-# 「今週はなし」のときは、その週の課題だけを `/kadai delete` で取り下げればOK（ルールは残って来週また出ます）")
        await interaction.response.send_message(txt, view=view, ephemeral=True)

    @discord.ui.button(label="👥 時間割をコピー", style=discord.ButtonStyle.secondary, custom_id="sk_jw_copy", row=0)
    async def jw_copy(self, interaction, button):
        await ensure_user(interaction.user)
        cands = await timetable_owners(interaction.user.id)
        if not cands:
            await interaction.response.send_message("まだ履修登録している人がいません。🎓 から1人目になろう！", ephemeral=True)
            return
        view = discord.ui.View(timeout=300)
        view.add_item(CopySrcSelect(cands))
        await interaction.response.send_message("👥 誰の時間割をコピーしますか？（同じ学科・学年の人がおすすめ。コピー後にいらない科目を外せます）",
                                                view=view, ephemeral=True)

VIEW_FACTORY["kadai"] = KadaiPanelView


# ============================================================
#  朝の天気（Open-Meteo・キー不要）／起床時ダイジェスト／お休み申告／個人項目（カスタム最低限）
# ============================================================
DAY_CHARS = "月火水木金土日"
PERIOD_START = {1: "8:45", 2: "10:30", 3: "13:00", 4: "14:45", 5: "16:30", 6: "18:15"}
WMO = {0: "☀️快晴", 1: "🌤晴れ", 2: "⛅晴れ時々くもり", 3: "☁️くもり", 45: "🌫霧", 48: "🌫霧", 51: "🌦霧雨", 53: "🌦霧雨", 55: "🌧霧雨",
       56: "🌧みぞれ", 57: "🌧みぞれ", 61: "🌧雨", 63: "🌧雨", 65: "🌧強い雨", 66: "🌧みぞれ", 67: "🌧みぞれ", 71: "🌨雪", 73: "🌨雪", 75: "❄️大雪",
       77: "🌨雪", 80: "🌦にわか雨", 81: "🌧にわか雨", 82: "⛈激しい雨", 85: "🌨にわか雪", 86: "🌨にわか雪", 95: "⛈雷雨", 96: "⛈雷雨", 99: "⛈雷雨"}

_weather_cache = {"ts": 0.0, "daily": None}

async def weather_daily():
    """Open-Meteoの今日・明日の予報（daily配列）。30分キャッシュ。失敗は None"""
    import time as _t
    import aiohttp
    if _weather_cache["daily"] and _t.time() - _weather_cache["ts"] < 1800:
        return _weather_cache["daily"]
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={WEATHER_LAT}&longitude={WEATHER_LON}"
           "&daily=weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
           "&timezone=Asia%2FTokyo&forecast_days=2")
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as sess:
            async with sess.get(url) as r:
                if r.status != 200:
                    return None
                d = (await r.json())["daily"]
    except Exception as e:
        print(f"天気取得失敗: {e!r}", flush=True)
        return None
    _weather_cache.update(ts=_t.time(), daily=d)
    return d

RAIN_CODES = set(range(51, 68)) | set(range(80, 83)) | set(range(95, 100))   # WMO：霧雨・雨・にわか雨・雷雨

def rain_info(d, i):
    """(降水確率%またはNone, 雨っぽいか)"""
    try:
        p = (d.get("precipitation_probability_max") or [None, None])[i]
        code = int(d["weather_code"][i])
        return (int(p) if p is not None else None), (code in RAIN_CODES or (p is not None and p >= 50))
    except Exception:
        return None, False

async def fetch_weather(day_offset=0):
    """今日（day_offset=0）または明日（=1）の天気を1行で。失敗したら空文字"""
    d = await weather_daily()
    if not d:
        return ""
    try:
        i = day_offset
        code = int(d["weather_code"][i])
        p, _ = rain_info(d, i)
        return (f"{WMO.get(code, '🌡')}　最高 {d['temperature_2m_max'][i]:.0f}℃／最低 {d['temperature_2m_min'][i]:.0f}℃"
                + (f"　☔ {p}%" if p is not None else ""))
    except Exception as e:
        print(f"天気整形失敗: {e!r}", flush=True)
        return ""

async def laundry_rain_note(day_obj):
    """洗濯の日に雨予報なら一言（今日・明日の予報だけ見る）。それ以外は空文字"""
    off = (day_obj - now_jst().date()).days
    if off not in (0, 1):
        return ""
    d = await weather_daily()
    if not d:
        return ""
    p, rainy = rain_info(d, off)
    if not rainy:
        return ""
    return f"☔ 雨予報{f'（降水{p}%）' if p is not None else ''}なので、洗濯は部屋干しか別の日に回すのがおすすめ"

async def today_digest(uid, now, label="今日"):
    """「今日（明日）の授業」「未完了の課題」のダイジェスト。明日分は now に翌日・label='明日' を渡す"""
    dc = DAY_CHARS[now.weekday()]
    per = {}
    lines = []
    try:   # 🏫 大学の予定：開催中（長期休暇は下の一言で出す）と、3日以内に始まるもの
        tday = day_str(now)
        soon = (now.date() + timedelta(days=3)).isoformat()
        for r in await univ_events_between(tday, soon):
            if r["kind"] == "長期休暇" and r["start_day"] <= tday:
                continue
            lines.append(("🏫 いま：" if r["start_day"] <= tday else "🏫 もうすぐ：") + univ_line(r, tday))
        for r in await private_events_between(uid, tday, soon):   # 🙋 じぶんの予定（本人の返事にしか出ない）
            lines.append(("🙋 今日：" if r["start_day"] <= tday else "🙋 もうすぐ：") + univ_line(r, tday))
        for r in await time_events_between(uid, tday, tday):   # ⏱ バイト・部活の時間
            lines.append(f"⏱ {label}：{TIME_EMOJI.get(r['kind'], '⏱')}{r['kind']} {r['start_hm']}-{r['end_hm']}"
                         f"（{fmt_mins(r['minutes'])}）" + (f" {r['note']}" if r["note"] else ""))
    except Exception:
        pass
    if break_on(now):   # 🌸 長期休暇中は授業がないので時間割は出さない
        lines.append(f"🌸 {break_text(now)}中：時間割と毎週課題はお休みです")
    else:
        for c in await user_course_rows(uid):
            for slot in (c["slots"] or "").split(","):
                slot = slot.strip()
                if len(slot) >= 2 and slot[0] == dc and slot[1:].isdigit():
                    per.setdefault(int(slot[1:]), []).append(c)
        async with db.execute("SELECT slot, label, place FROM user_slots WHERE user_id=?", (str(uid),)) as c:
            for r in await c.fetchall():   # 空きコマの個人予定（バイト・部活など）も今日の時間割に混ぜる
                s = r["slot"]
                if s and s[0] == dc and s[1:].isdigit():
                    per.setdefault(int(s[1:]), []).append({"name": r["label"], "room": r["place"], "code": "_us_" + s})
    if per:
        lines.append(f"📅 **{label}の時間割**")
        seen = set()
        for p in range(1, max(5, max(per)) + 1):
            names = []
            for c in per.get(p, []):
                if c["code"] in seen:   # 実験など連続コマの2コマ目以降
                    names.append(f"〃 {c['name']}")
                else:
                    names.append(f"**{c['name']}**" + (f"（{c['room']}）" if c["room"] else ""))
                    seen.add(c["code"])
            lines.append(f"　{p}限 {PERIOD_START.get(p, '')}　" + ("／".join(names) if names else "─"))
        try:   # 今日の科目で欠席が残りわずかなら警告
            km = await kesseki_map(uid)
            warn, done = [], set()
            for p in sorted(per):
                for c in per[p]:
                    r = km.get(c["code"])
                    if r and c["code"] not in done and r["limit_n"] - r["absent"] <= 2:
                        warn.append(f"**{c['name']}** あと{max(0, r['limit_n'] - r['absent'])}回")
                        done.add(c["code"])
            if warn:
                lines.append("🚨 欠席はあと：" + "／".join(warn))
        except Exception:
            pass
    try:   # 🧪 実験カレンダー：今日の実施内容（A/Bクラスで日程が違う実験用）
        labs = []
        for c in await user_course_rows(uid):
            t = LAB_SCHEDULE.get(c["code"], {}).get(now.date().isoformat())
            if t:
                labs.append(f"**{c['name']}**：{t}")
        if labs:
            lines.append(f"🧪 {label}の実験：" + "／".join(labs))
    except Exception:
        pass
    try:   # 🗒 その日の授業の科目メモ（教室変更・持ち物など）
        codes = list(dict.fromkeys(c["code"] for p in sorted(per) for c in per[p] if not str(c["code"]).startswith("_us_")))
        notes = await today_course_notes(codes)
        if notes:
            lines.append("🗒 メモ：" + "／".join(notes))
    except Exception:
        pass
    try:   # 試験1週間前からは朝のダイジェストでそっとカウントダウン（本人にだけ見える）
        exs = [e for e in await exam_rows_for(uid) if 0 <= exam_days_left(e["exam_day"], now) <= 7]
        if exs:
            parts = []
            for e in exs[:4]:
                left = exam_days_left(e["exam_day"], now)
                parts.append(f"**{e['cname']}** " + (f"**{label}！🔥**" if left == 0 else f"あと{left}日") + f"（{exam_date_str(e['exam_day'])}）")
            lines.append("📝 試験：" + "／".join(parts))
    except Exception:
        pass
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
            lines.append(f"🧹 {label}やる家事：" + "／".join(f"{st['emoji']}{st['label']}（{kaji_interval_text(st['n'])}）" for st in due))
            if any(st["label"] == "洗濯" for st in due):
                try:
                    rn = await laundry_rain_note(now.date())
                    if rn:
                        lines.append("　" + rn)
                except Exception:
                    pass
    try:
        ms = await open_memos(uid, day_str(now))
        if ms:
            lines.append("📌 やること：" + "／".join(r["text"] + memo_deadline_label(r, day_str(now)) + ("　🌅" if r["carried"] else "")
                                                   for r in ms))
    except Exception:
        pass
    try:
        fr = await fridge_soon(uid, day_str(now))
        if fr:
            lines.append("🧊 期限が近い食品：" + "／".join(fr))
            for h in await fridge_tip_hints(uid, day_str(now)):
                lines.append("　💡 " + h)
    except Exception:
        pass
    try:
        gs = await goods_soon(uid, day_str(now))
        if gs:
            lines.append("📦 使用期限：" + "／".join(gs))
    except Exception:
        pass
    if label == "今日":   # TIPSとやることメモは「今日」専用（明日の分は存在しない）
        try:
            tp = await tip_of_day(day_str(now))
            if tp:
                lines.append(f"📚 今日のTIPS：**{tp['title']}** → <#{tp['thread_id']}>")
        except Exception:
            pass
    return "\n".join(lines)

async def upcoming_digest(uid, now, days=7):
    """今後n日の課題・試験・実験＋30日以内の大学の予定のまとめ（#予定🗓 の📆今後の予定ボタン用）"""
    lines = []
    try:
        tday = day_str(now)
        d30 = (now.date() + timedelta(days=30)).isoformat()
        ev = sorted(list(await univ_events_between(tday, d30)) + await private_events_between(uid, tday, d30),
                    key=lambda r: (r["start_day"], r["end_day"]))
        if ev:
            lines.append("🏫 **大学の予定・じぶんの予定（30日以内）**")
            lines += [f"　{univ_line(r, tday)}" for r in ev[:10]]
        tev = await time_events_between(uid, tday, (now.date() + timedelta(days=days)).isoformat())
        if tev:
            lines.append(f"⏱ **バイト・部活など（{days}日以内）**")
            lines += [f"　{time_line(r)}" for r in tev[:10]]
    except Exception:
        pass
    end = now + timedelta(days=days)
    async with db.execute(
        "SELECT a.title, a.due_ts, c.name AS cname FROM assignments a JOIN courses c ON c.code=a.code "
        "WHERE a.closed=0 AND a.code IN (SELECT code FROM user_courses WHERE user_id=?) "
        "AND a.id NOT IN (SELECT assignment_id FROM assignment_done WHERE user_id=?) "
        "AND a.due_ts<=? ORDER BY a.due_ts LIMIT 25", (str(uid), str(uid), int(end.timestamp()))) as c:
        rows = await c.fetchall()
    by_day = {}
    for r in rows:
        d = datetime.fromtimestamp(r["due_ts"], JST)
        by_day.setdefault(d.date(), []).append((d, r))
    if by_day:
        lines.append(f"📚 **{days}日以内の課題**")
        for dd in sorted(by_day):
            items = "／".join(f"{r['cname']}「{r['title']}」{d.strftime('%H:%M')}" for d, r in by_day[dd])
            mark = "⚠️期限切れ " if dd < now.date() else ""
            lines.append(f"　{mark}{dd.month}/{dd.day}({DAY_CHARS[dd.weekday()]})　{items}")
    try:
        exs = [e for e in await exam_rows_for(uid) if 0 <= exam_days_left(e["exam_day"], now) <= 30]
        if exs:
            lines.append("📝 **試験（30日以内）**")
            for e in exs[:8]:
                left = exam_days_left(e["exam_day"], now)
                lines.append(f"　{exam_date_str(e['exam_day'])}　**{e['cname']}**" + ("　今日！🔥" if left == 0 else f"　あと{left}日"))
    except Exception:
        pass
    try:
        labs = []
        for c2 in await user_course_rows(uid):
            for iso, t in LAB_SCHEDULE.get(c2["code"], {}).items():
                try:
                    dd = datetime.fromisoformat(iso).date()
                except Exception:
                    continue
                if now.date() <= dd <= end.date():
                    labs.append((dd, f"**{c2['name']}**：{t}"))
        if labs:
            lines.append(f"🧪 **実験（{days}日以内）**")
            for dd, t in sorted(labs):
                lines.append(f"　{dd.month}/{dd.day}({DAY_CHARS[dd.weekday()]})　{t}")
    except Exception:
        pass
    return "\n".join(lines)

def parse_day_spec(spec, now):
    """'' / 今日 / 明日 / 10/15 / 10/15-10/17 → ['YYYY-MM-DD', ...]。不正なら None"""
    t = unicodedata.normalize("NFKC", spec or "").strip()
    if not t or t in ("今日", "きょう"):
        return [day_str(now)]
    if t in ("明日", "あした"):
        return [day_str(now + timedelta(days=1))]
    if t in ("昨日", "きのう"):   # さかのぼりのお休み申告用（判定済みでもストリークは救済される）
        return [day_str(now - timedelta(days=1))]
    if t in ("おととい", "一昨日"):
        return [day_str(now - timedelta(days=2))]
    parts = [x for x in re.split(r"\s*[-〜~～]\s*", t) if x]
    if not parts or len(parts) > 2:
        return None
    ds = [parse_due(x, now) for x in parts]
    if any(d is None for d in ds):
        return None
    a, b = ds[0].date(), ds[-1].date()
    if b < a or (b - a).days > 31:
        return None
    return [(a + timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]

def parse_past_day(spec, now):
    """睡眠修正用の日付（過去向き）。''/今日/昨日/おととい/10/5/2026年10/5 → 'YYYY-MM-DD'。未来や不正は None"""
    t = unicodedata.normalize("NFKC", spec or "").strip()
    if not t or t in ("今日", "きょう"):
        return day_str(now)
    if t in ("昨日", "きのう"):
        return day_str(now - timedelta(days=1))
    if t in ("一昨日", "おととい"):
        return day_str(now - timedelta(days=2))
    m = re.fullmatch(r"(?:(\d{4})[/年])?\s*(\d{1,2})[/月]\s*(\d{1,2})日?", t)
    if not m:
        return None
    y = int(m.group(1)) if m.group(1) else now.year
    try:
        d = date(y, int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None
    if not m.group(1) and d > now.date():
        d = d.replace(year=y - 1)   # 年省略の未来日は昨年扱い（修正は過去向き）
    if d > now.date():
        return None
    return d.isoformat()

@bot.tree.command(name="suimin", description="睡眠時間を手動で記録・修正（寝落ち救済／誤入力の上書き。日付指定で過去も直せる）")
@app_commands.describe(jikan="睡眠時間(h) 例 3 や 6.5", memo="メモ 例 寝落ち（任意）",
                       uwagaki="True にするとその日の睡眠記録をこれ1本に置き換え（誤入力の修正用）",
                       hi="日付 例 昨日／10/5（省略=今日。過去の誤記録を直すとき用）")
async def suimin_command(interaction, jikan: float, memo: str = None, uwagaki: bool = False, hi: str = None):
    user = interaction.user
    await ensure_user(user)
    now = now_jst()
    day = parse_past_day(hi, now) if hi else day_str(now)
    if not day:
        await interaction.response.send_message("⚠️ 日付が読めませんでした（例 `昨日` `10/5`。未来の日は指定できません）", ephemeral=True)
        return
    if not (0 < jikan <= 24):
        await interaction.response.send_message("⚠️ 時間は 0〜24 の範囲で指定してください（例 3 や 6.5）", ephemeral=True)
        return
    if uwagaki:
        await db.execute("DELETE FROM events WHERE user_id=? AND day=? AND kind='sleep'", (str(user.id), day))
    today = day_str(now)
    ts = now if day == today else datetime.fromisoformat(day).replace(hour=12, tzinfo=JST)
    await add_event(user.id, "sleep", note=f"{jikan:.2f}", ts_dt=ts)
    rows = await events_on(user.id, day, "sleep")
    total = sum(float(x["note"] or 0) for x in rows)
    tag = f"（{memo.strip()}）" if memo and memo.strip() else ""
    verb = "で上書き" if uwagaki else "を追加"
    label = "今日" if day == today else day[5:].replace("-", "/")
    await interaction.response.send_message(f"😪 {label} の睡眠 {fmt_hours(jikan)} {verb}しました{tag}。{label}の合計 {fmt_hours(total)}。", ephemeral=True)
    await post_log("wake", f"😪 **{user.display_name}** {label} の睡眠 {'=' if uwagaki else '+'}{fmt_hours(jikan)}{tag}　→ {label} 計 {fmt_hours(total)}")

@bot.tree.command(name="oyasumi", description="お休み申告（その日は判定されず、連続達成も途切れない）")
@app_commands.describe(riyuu="理由 例: 帰省／体調不良（「なし」で取り消し）", hi="日付 例: 明日／昨日／10/15／10/15-10/17（省略=今日）")
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

def mycheck_replace_line(content, new_line):
    """メッセージ本文の「📝 今日のチェック」行だけを差し替える。
    ☀️の返事に載せた場合は授業・課題などの本文を保持したままカウントを更新するため"""
    if not content or content.startswith("📝"):
        return new_line
    lines = content.split("\n")
    for i, l in enumerate(lines):
        if l.startswith("📝 **今日のチェック**"):
            lines[i] = new_line
            return "\n".join(lines)
    return new_line

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
        content = mycheck_replace_line(interaction.message.content if interaction.message else "", await mycheck_text(uid, day))
        await interaction.response.edit_message(content=content, view=await MyCheckView.build(uid, day))

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

# ---- 🛠 最低限の設定パネル（/saitei のパネル版：項目を選んで1つずつ設定） ----
SAITEI_ITEMS = [
    ("kishou",   "☀️ 起床の締切",            "例 7:00（「なし」で解除）"),
    ("suimin",   "😴 最低睡眠時間(h)",        "例 6（0で解除）"),
    ("kyujitsu", "🏖 土日の起床後ろ倒し(h)",   "例 2（0で解除）"),
    ("yasumi",   "🗓 毎週の定休日（曜日）",    "例 土日（空欄で解除・その曜日は判定なし）"),
    ("nyuyoku",  "🛁 毎日の入浴",             "はい／いいえ"),
    ("hamigaki", "🪥 歯磨き（回/日）",         "例 2（0で解除）"),
    ("shokuji",  "🍚 食事（回/日）",           "例 2（0で解除）"),
    ("rajio",    "🏃 ラジオ体操",             "はい／いいえ"),
    ("benkyou",  "📖 勉強（みんなで暗記1日1問）", "はい／いいえ"),
    ("ryouri",   "🍳 料理（n日に1回）",        "例 1（0で解除）"),
    ("souji",    "🧹 掃除（n日に1回）",        "例 3（0で解除）"),
    ("sara",     "🍽️ 皿洗い（n日に1回）",      "例 1（0で解除）"),
    ("sentaku",  "🧺 洗濯（n日に1回）",        "例 3（0で解除）"),
    ("gomi",     "🗑 ごみ捨て（n日に1回）",    "例 3（0で解除）"),
]
_YES = ("はい", "する", "yes", "on", "1", "true")
_NO = ("いいえ", "しない", "no", "off", "0", "false", "なし")

async def apply_saitei_item(uid, key, raw, now=None):
    """設定パネルからの1項目更新（/saitei と同じ意味論・同じ初日猶予）。エラーメッセージ or None を返す"""
    uid = str(uid)
    today = day_str(now or now_jst())
    t = unicodedata.normalize("NFKC", raw or "").strip().casefold()
    if key == "kishou":
        if t in ("なし", "none", "-", "0", ""):
            await db.execute("UPDATE users SET wake_deadline=NULL WHERE id=?", (uid,))
        else:
            hh = parse_hhmm(t)
            if not hh:
                return "⚠️ 起床時刻の形式が読めませんでした（例 7:00）"
            await db.execute("UPDATE users SET wake_deadline=?, wake_set_day=? WHERE id=?", (hh, today, uid))
    elif key in ("suimin", "kyujitsu"):
        try:
            v = float(t or "0")
        except ValueError:
            return "⚠️ 数字で入れてください（例 6。0で解除）"
        if key == "suimin":
            await db.execute("UPDATE users SET sleep_min=?, sleep_set_day=? WHERE id=?", (v if v > 0 else None, today, uid))
        else:
            await db.execute("UPDATE users SET holiday_shift=?, wake_set_day=? WHERE id=?", (int(max(0.0, min(12.0, v)) * 60), today, uid))
    elif key == "yasumi":
        ds = []
        if t in ("", "なし", "0"):
            await db.execute("UPDATE users SET off_weekdays=NULL WHERE id=?", (uid,))
        else:
            ds = parse_day_chars_input(t)
            if not ds:
                return "⚠️ 曜日は `土日` や `土,日` の形式で入れてください（空欄で解除）"
            await db.execute("UPDATE users SET off_weekdays=? WHERE id=?", ("".join(ds), uid))
        # 今日の分はこの場で反映する。日次の実体化（materialize_weekly_off）は0時に実行済みなので、
        # 昼間に設定した定休日はそれだけだと今晩の判定に乗らず叱責されてしまう
        dc = DAY_CHARS[date.fromisoformat(today).weekday()]
        if dc in ds:
            await db.execute("INSERT OR IGNORE INTO off_days(day,user_id,reason) VALUES(?,?,'定休')", (today, uid))
        else:   # 逆に今日が定休でなくなったら、0時に実体化済みの「定休」だけ取り消す（/oyasumiの手動申告は残す）
            await db.execute("DELETE FROM off_days WHERE day=? AND user_id=? AND reason='定休'", (today, uid))
    elif key in ("nyuyoku", "rajio", "benkyou"):
        if t in _YES:
            v = 1
        elif t in _NO:
            v = 0
        else:
            return "⚠️ 「はい」か「いいえ」で入れてください"
        col, sd = {"nyuyoku": ("bath_daily", "bath_set_day"), "rajio": ("radio_daily", "radio_set_day"),
                   "benkyou": ("benkyou_min", "benkyou_set_day")}[key]
        await db.execute(f"UPDATE users SET {col}=?, {sd}=? WHERE id=?", (v, today, uid))
    elif key in ("hamigaki", "shokuji"):
        try:
            v = int(float(t or "0"))
        except ValueError:
            return "⚠️ 数字で入れてください（例 2。0で解除）"
        if key == "hamigaki":
            await db.execute("UPDATE users SET teeth_min=?, teeth_set_day=? WHERE id=?", (max(0, min(10, v)), today, uid))
        else:
            await db.execute("UPDATE users SET meals_min=?, meals_set_day=? WHERE id=?", (min(3, v) if v > 0 else None, today, uid))
    elif key in ("ryouri", "souji", "sara", "sentaku", "gomi"):
        try:
            v = int(float(t or "0"))
        except ValueError:
            return "⚠️ 数字で入れてください（例 3＝3日に1回。0で解除）"
        col = {"ryouri": "kaji_cook", "souji": "kaji_clean", "sara": "kaji_dish", "sentaku": "kaji_wash", "gomi": "kaji_trash"}[key]
        await db.execute(f"UPDATE users SET {col}=?, kaji_since=? WHERE id=?", (max(0, min(30, v)), today, uid))
    await db.commit()
    return None

class SaiteiModal(discord.ui.Modal):
    def __init__(self, key, label, hint):
        super().__init__(title=f"🛠 {label}"[:45])
        self.key = key
        self.label_ = label
        self.val = discord.ui.TextInput(label=hint[:45], required=False, max_length=10)
        self.add_item(self.val)

    async def on_submit(self, interaction):
        err = await apply_saitei_item(interaction.user.id, self.key, self.val.value)
        if err:
            await interaction.response.send_message(err, ephemeral=True)
            return
        u = await get_user(interaction.user.id)
        off = f"\n🗓 定休日：{'・'.join(u['off_weekdays']) if u['off_weekdays'] else 'なし'}"
        await interaction.response.send_message(
            f"✅ {self.label_} を更新しました。\n\n🛠 **あなたの最低限**\n{settings_text(u)}{off}"
            "\n\n🍀 今日設定・変更した項目は、**明日から**判定に入ります。", ephemeral=True)
        settei = await get_ch("settei")
        if settei:
            await settei.send(f"🛠 **{interaction.user.display_name}** が最低限を更新：" + " / ".join(settings_text(u).split("\n")))

class SaiteiSelect(discord.ui.Select):
    def __init__(self):
        opts = [discord.SelectOption(label=label[:100], value=key, description=hint[:100]) for key, label, hint in SAITEI_ITEMS]
        super().__init__(placeholder="🛠 設定する項目を選ぶ", options=opts)

    async def callback(self, interaction):
        key = self.values[0]
        _k, label, hint = next(x for x in SAITEI_ITEMS if x[0] == key)
        await interaction.response.send_modal(SaiteiModal(key, label, hint))

class SetteiView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 今日のチェック（自分の項目）", style=discord.ButtonStyle.primary, custom_id="sk_mycheck")
    async def mycheck(self, interaction, button):
        await ensure_user(interaction.user)
        await send_mycheck(interaction)

    @discord.ui.button(label="🛠 最低限を設定", style=discord.ButtonStyle.success, custom_id="sk_saitei")
    async def saitei_btn(self, interaction, button):
        await ensure_user(interaction.user)
        u = await get_user(interaction.user.id)
        off = f"\n🗓 定休日：{'・'.join(u['off_weekdays']) if u['off_weekdays'] else 'なし'}"
        view = discord.ui.View(timeout=600)
        view.add_item(SaiteiSelect())
        await interaction.response.send_message(
            f"🛠 **あなたの最低限**（下のプルダウンから項目を選んで変更）\n{settings_text(u)}{off}"
            "\n-# コマンド派は `/saitei` でまとめて設定もできます", view=view, ephemeral=True)

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
    await interaction.response.send_message(f"📝 「{nm}」を追加しました（{len(items) + 1}件）。☀️起床の返事にチェックリストが出ます。どのパネルの 📝 ボタンからも開けるよ。", ephemeral=True)

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

SETUP_TUTORIAL = os.getenv("SETUP_TUTORIAL", "0") == "1"   # 1にすると /setup が #はじめに📖 にBotの長いガイドを投稿・更新する

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
        "#設定🔧 で `/saitei` を実行。例：\n`/saitei kishou:8:00 suimin:6 nyuyoku:True ryouri:1 sentaku:3`\n"
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
        "`/kojin add namae:薬を飲む` のように追加すると、☀️起床の返事と各パネルの 📝 ボタンで毎日チェックできます。未チェックは判定で叱られます。"))
    e3 = discord.Embed(title="🔁 1日の流れ", color=gold, description=(
        f"**朝**　☀️ 起きた → {RADIO_TIME} ラジオ体操🏃\n"
        "**日中**　🍚 食べたら押す（#ごはん🍚 に写真を投げるだけでもOK）／🧹 家事をしたら押す／📚 課題に気づいたら `/kadai add`\n"
        "**夜**　🛁 お風呂・🪥 歯磨きをしたら押す → 🌙 おやすみ\n"
        f"**{JUDGE_HOUR}:00**　判定。未達は #叱責👹 で名指し、達成した人は ✨ で称えられ、連続日数 🔥 が伸びる（3・7・14・30日…でお祝い）\n"
        "**日曜の夜**　#つうしんぼ📮 に今週の通信簿（達成率ランキング・各賞・起床/睡眠グラフ）。月末は 🏆 月間MVP"))
    e4 = discord.Embed(title="🗺 チャンネル案内", color=gold, description=(
        "#はじめに📖　このガイド\n"
        "#暮らしのtips📚　レシピ・お得情報・家事ハックの知恵袋（フォーラム。🔖で自分のTIPS帳へ）\n"
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
        "**きろく**　各チャンネルのボタンが基本（☀️🌙😴／🍚／🧹／🛁🪥）。`/suimin` 寝落ちの後づけ／`/erai` 今日のがんばり\n"
        "**最低限**　`/saitei` 設定はぜんぶここ（起床・睡眠・入浴・歯磨き・食事・家事の種類別・休日ゆるめ。指定した項目だけ更新）\n"
        "**じぶん**　`/kiroku` 今日と今週／`/watashi` 推移グラフと気づき（CSVも）／`/nakama` 同じ起床時刻の仲間\n"
        "**お休み**　`/oyasumi riyuu:帰省 hi:10/15-10/17`（判定なし・連続達成も途切れない。`riyuu:なし` で取消）\n"
        "**科目**　`/jikanwari add` 登録／`list` 一覧／`remove` 外す\n"
        "**課題**　`/kadai add` 登録／`list` 一覧／`done` 完了／`delete` 取り下げ\n"
        "**自分の項目**　`/kojin add` 追加／`list` チェックリスト／`remove` 削除\n"
        "**TIPS**　`/tips sagasu` 検索／`mine` 保存したもの／`omakase` ランダム\n"
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
            await haj.send(f"👋 ようこそ {member.mention}！まずはこのチャンネルの説明を読んでみてね。")
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
            "**🪪 プロフィールカード** を押すと、自己紹介と称号を名刺風の1枚画像にして自分にだけ届けます（新歓・SNS用）。"
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

# ---- 🪪 プロフィールカード（自己紹介＋称号の名刺風1枚・新歓やSNS用） ----
PROFILE_SECTIONS = [("🎯", "今学期の目標", "f5", 2), ("🍚", "いつものごはん・ハマってること", "f3", 3),
                    ("😵", "苦手なこと・朝型／夜型", "f2", 3), ("🤝", "叱られ方・できること", "f4", 3)]

def render_profile_card(name, avatar, intro, info):
    """intro=自己紹介(dict f1..f5)、info={wake, streak, best, titles}、avatar=画像bytes(任意)。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont, ImageOps
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    W, H, pad = 1080, 1350, 60
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, W - pad - round(logo.width * 96 / logo.height), 34, 96)
    d.text((pad, 52), "PROFILE", font=F(28, True), fill=CARD_ORANGE)
    # アイコン（丸抜き）＋名前＋ひとこと紹介
    av = 210
    ay = 120
    if avatar:
        try:
            a = ImageOps.fit(PImage.open(io.BytesIO(avatar)).convert("RGB"), (av, av))
            mask = PImage.new("L", (av, av), 0)
            ImageDraw.Draw(mask).ellipse([0, 0, av - 1, av - 1], fill=255)
            img.paste(a, (pad, ay), mask)
        except Exception:
            avatar = None
    if not avatar:
        d.ellipse([pad, ay, pad + av, ay + av], fill=CARD_TRACK)
        d.text((pad + av // 2, ay + av // 2), (name or "?")[:1], font=F(96, True), fill=CARD_MUTED, anchor="mm")
    d.ellipse([pad - 4, ay - 4, pad + av + 4, ay + av + 4], outline=CARD_ORANGE, width=6)
    nx = pad + av + 40
    nf = F(64 if len(name) <= 8 else 48, True)
    _draw_mixed(d, nx, ay + 18, name[:14], nf, E(64), CARD_INK)
    yy = ay + 110
    for ln in _wrap_px(d, (intro.get("f1") or "").strip(), F(28), E(28), W - pad - nx)[:3]:
        _draw_mixed(d, nx, yy, ln, F(28), E(28), CARD_MUTED)
        yy += 40
    # 数字のピル
    y = ay + av + 40
    pills = [f"☀ 起床目標 {info['wake'] or '未設定'}", f"🔥 {info['streak']}日連続", f"🏆 自己ベスト {info['best']}日"]
    x = pad
    for p in pills:
        w = int(_mixed_w(d, p, F(28, True), E(28))) + 44
        d.rounded_rectangle([x, y, x + w, y + 60], radius=30, fill="#fffaf2", outline=CARD_LINE, width=2)
        _draw_mixed(d, x + 22, y + 13, p, F(28, True), E(28), CARD_INK)
        x += w + 16
    y += 80
    if info["titles"]:   # 称号ロールはオレンジのバッジで
        x = pad
        for t in info["titles"][:4]:
            w = int(_mixed_w(d, t, F(26, True), E(26))) + 36
            if x + w > W - pad:
                break
            d.rounded_rectangle([x, y, x + w, y + 50], radius=25, fill=CARD_ORANGE)
            _draw_mixed(d, x + 18, y + 10, t, F(26, True), E(26), "#ffffff")
            x += w + 12
        y += 74
    # 自己紹介の各項目（書いてあるものだけ・入りきる分だけ）
    y += 6
    for ic, lb, key, max_lines in PROFILE_SECTIONS:
        text = (intro.get(key) or "").strip()
        if not text:
            continue
        lines = []
        for para in text.splitlines():
            lines += _wrap_px(d, para, F(30), E(30), W - pad * 2 - 56)
        lines = lines[:max_lines]
        need = 64 + 44 * len(lines) + 26
        if y + need > H - 124:
            break
        d.rounded_rectangle([pad, y, W - pad, y + need], radius=22, fill="#fffaf2", outline=CARD_LINE, width=2)
        _draw_mixed(d, pad + 28, y + 20, f"{ic} {lb}", F(26, True), E(26), CARD_ORANGE)
        ly = y + 64
        for ln in lines:
            _draw_mixed(d, pad + 28, ly, ln, F(30), E(30), CARD_INK)
            ly += 44
        y += need + 18
    d.line([pad, H - 104, W - pad, H - 104], fill=CARD_LINE, width=2)
    bowl = _gohan_art("bowl")
    fx = pad
    if bowl:
        fx += _paste_art(img, bowl, pad, H - 92, 64) + 14
    d.text((fx, H - 84), "最低限生活リズムサークル", font=F(30, True), fill=CARD_INK)
    d.text((W - pad, H - 76), "いっしょにレッツなまりず", font=F(22), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def profile_info(member):
    u = await get_user(member.id)
    streak = await streak_of(member.id, day_str(now_jst()))
    title_names = {nm for _, nm, _ in ALL_TITLES}
    titles = [r.name for r in getattr(member, "roles", []) if r.name in title_names]
    return {"wake": u["wake_deadline"] if u and u["wake_deadline"] else None, "streak": streak,
            "best": max(streak, (u["best_streak"] if u and u["best_streak"] else 0)), "titles": titles}

async def send_profile_card(interaction):
    async with db.execute("SELECT * FROM intros WHERE user_id=?", (str(interaction.user.id),)) as c:
        row = await c.fetchone()
    if not row:
        await interaction.response.send_message("先に「📝 自己紹介を書く」で自己紹介を書いてね。カードはその内容から作ります。", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    avatar = None
    try:
        avatar = await interaction.user.display_avatar.replace(size=256, format="png").read()
    except Exception:
        pass
    buf = await asyncio.to_thread(render_profile_card, interaction.user.display_name, avatar, dict(row),
                                  await profile_info(interaction.user))
    if not buf:
        await interaction.followup.send("⚠️ カードを作れませんでした（VMに日本語フォントが必要）。", ephemeral=True)
        return
    await interaction.followup.send("🪪 プロフィールカードです（自分にだけ表示）。長押し・右クリックで保存して、新歓やSNSにもどうぞ！"
                                    "\n-# 内容を変えたいときは📝で自己紹介を更新すると、次のカードに反映されます",
                                    file=discord.File(buf, filename="profile.png"), ephemeral=True)

class IntroView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📝 自己紹介を書く／更新する", style=discord.ButtonStyle.primary, custom_id="sk_intro")
    async def intro(self, interaction, button):
        await open_intro_modal(interaction)

    @discord.ui.button(label="🪪 プロフィールカード", style=discord.ButtonStyle.secondary, custom_id="sk_intro_card")
    async def intro_card_btn(self, interaction, button):
        await send_profile_card(interaction)

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

    @discord.ui.button(label="📌 やること宣言", style=discord.ButtonStyle.secondary, custom_id="sk_memo_add", row=1)
    async def memo_add(self, interaction, button):
        await interaction.response.send_modal(MemoModal())

    @discord.ui.button(label="📌 やること一覧", style=discord.ButtonStyle.secondary, custom_id="sk_memo_list", row=1)
    async def memo_list(self, interaction, button):
        await ensure_user(interaction.user)
        await send_memo_list(interaction)

VIEW_FACTORY["erai"] = EraiView

@bot.tree.command(name="erai", description="今日がんばったことを報告する（最低限とは別・判定なし・褒め合い用）")
async def erai_command(interaction):
    await interaction.response.send_modal(EraiModal())

# ------------------------------------------------------------
#  やることメモ📌
#  「さっき何したかったんだっけ」対策の今日限りメモ。日時指定なし。
#  宣言が #えらい🌟 に流れて緊張を生み、✅でみんなに見える形で完了。
#  23時の判定後に引き継ぎ確認、放置すれば翌朝自動で消える（叱責はしない）。
# ------------------------------------------------------------
async def open_memos(uid, day):
    async with db.execute("SELECT * FROM memos WHERE user_id=? AND day=? AND done=0 ORDER BY id", (str(uid), day)) as c:
        return await c.fetchall()

def memo_until(r):
    """期限つきメモの期限（ISO）。無ければ None"""
    try:
        return r["until_day"] or None
    except (IndexError, KeyError):
        return None

def memo_deadline_label(r, today=None):
    """「（〜11/3・あと2日）」のような期限表示。期限なしは空"""
    u = memo_until(r)
    if not u:
        return ""
    today = today or day_str(now_jst())
    left = (date.fromisoformat(u) - date.fromisoformat(today)).days
    return f"（〜{md(u)}・" + ("今日まで！" if left <= 0 else f"あと{left}日") + "）"

def parse_memo_deadline(spec, now):
    """期限欄 → (開始日, 期限日) ISO。空欄は (今日, None)。'11/3'=今日〜11/3、'11/1-11/3'=11/1〜11/3。読めなければ None"""
    t = unicodedata.normalize("NFKC", spec or "").strip()
    today = now.date()
    if not t:
        return today.isoformat(), None
    days = parse_day_spec(t, now)
    if not days:
        return None
    s, e = date.fromisoformat(days[0]), date.fromisoformat(days[-1])
    if len(days) == 1:
        s = today
    s = max(s, today)
    if e < today:
        return None
    return s.isoformat(), (e.isoformat() if e > today or s > today else None)

async def memo_delete_public_by_id(msg_id):
    if not msg_id:
        return
    ch = await get_ch("erai")
    if not ch:
        return
    try:
        m = await ch.fetch_message(int(msg_id))
        await m.delete()
    except Exception:
        pass

def memo_done_text(name, text, guild):
    return f"✅ **{name}**：~~{text}~~　やりきった！{erai_emoji(guild)}"

async def memo_group_payload(rows, guild):
    """メモ群（同じ宣言メッセージのタスクたち）から公開メッセージの本文とViewを作る"""
    u = await get_user(rows[0]["user_id"])
    name = u["name"] if u else "？"
    open_rows = [r for r in rows if not r["done"]]
    if len(rows) == 1:
        r = rows[0]
        content = memo_done_text(name, r["text"], guild) if r["done"] else \
            f"📌 **{name}**：{r['text']}{memo_deadline_label(r)}" + ("　🌅持ち越し" if r["carried"] else "")
    else:
        tail = f"　ぜんぶやりきった！{erai_emoji(guild)}" if not open_rows else f"（✅ {len(rows) - len(open_rows)}/{len(rows)}）"
        lines = [(f"✅ ~~{r['text']}~~" if r["done"] else f"⬜ {r['text']}{memo_deadline_label(r)}" + ("　🌅" if r["carried"] else ""))
                 for r in rows]
        content = f"{'✅' if not open_rows else '📌'} **{name}** のやること{tail}\n" + "\n".join(lines)
    view = None
    if open_rows:
        view = discord.ui.View(timeout=None)
        for r in open_rows[:25]:
            view.add_item(MemoDoneButton(r["id"], text=(r["text"] if len(rows) > 1 else None)))
    return content, view

async def refresh_memo_message(msg_id):
    """宣言メッセージをDBの今の状態で描き直す。メモが1件も残っていなければメッセージごと消す"""
    if not msg_id:
        return
    ch = await get_ch("erai")
    if not ch:
        return
    async with db.execute("SELECT * FROM memos WHERE msg_id=? ORDER BY id", (str(msg_id),)) as c:
        rows = await c.fetchall()
    if not rows:
        await memo_delete_public_by_id(msg_id)
        return
    content, view = await memo_group_payload(rows, ch.guild)
    try:
        m = await ch.fetch_message(int(msg_id))
        await m.edit(content=content, view=view)
    except Exception:
        pass

class MemoDoneButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_memo_done:(?P<id>\d+)"):
    def __init__(self, memo_id, text=None):
        label = ("✅ " + text)[:80] if text else "✅ やりきった"
        super().__init__(discord.ui.Button(label=label, style=discord.ButtonStyle.success, custom_id=f"sk_memo_done:{memo_id}"))
        self.memo_id = int(memo_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"])

    async def callback(self, interaction):
        async with db.execute("SELECT * FROM memos WHERE id=?", (self.memo_id,)) as c:
            row = await c.fetchone()
        if not row or row["done"]:
            await interaction.response.send_message("このメモはもうありません。", ephemeral=True)
            return
        if row["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("宣言した本人だけが押せます。応援は絵文字リアクションでどうぞ！", ephemeral=True)
            return
        await db.execute("UPDATE memos SET done=1 WHERE id=?", (self.memo_id,))
        await db.commit()
        async with db.execute("SELECT * FROM memos WHERE msg_id=? ORDER BY id", (str(interaction.message.id),)) as c:
            rows = await c.fetchall()
        if rows:
            content, view = await memo_group_payload(rows, interaction.guild)
            await interaction.response.edit_message(content=content, view=view)
        else:
            await interaction.response.edit_message(content=memo_done_text(interaction.user.display_name, row["text"], interaction.guild), view=None)

def parse_memo_lines(raw):
    """フォームの複数行入力 → タスクのリスト（1行=1つ・行頭の箇条書き記号は除去・最大10件）"""
    out = []
    for line in (raw or "").splitlines():
        t = line.strip().lstrip("・-*•●○➀").strip()[:80]
        if t:
            out.append(t)
    return out[:10]

class MemoModal(discord.ui.Modal, title="📌 やることメモ"):
    what = discord.ui.TextInput(label="やること（1行に1つ・まとめて宣言OK）", style=discord.TextStyle.paragraph,
                                placeholder="例：\n参考文献を探す\n振込\nゴミ袋を買う", max_length=400)
    until = discord.ui.TextInput(label="期限（任意・空欄なら今日だけ）", required=False, max_length=20,
                                 placeholder="11/3＝今日〜11/3　／　11/1-11/3＝その期間")

    async def on_submit(self, interaction):
        user = interaction.user
        await ensure_user(user)
        now = now_jst()
        tasks = parse_memo_lines(self.what.value)
        if not tasks:
            await interaction.response.send_message("⚠️ やることが読み取れませんでした。1行に1つ書いてね。", ephemeral=True)
            return
        span = parse_memo_deadline(str(self.until.value), now)
        if not span:
            await interaction.response.send_message("⚠️ 期限が読めませんでした（過ぎた日付も×）。例：`11/3` `11/1-11/3`", ephemeral=True)
            return
        start_day, until_day = span
        ids = []
        for t in tasks:
            cur = await db.execute("INSERT INTO memos(user_id,text,day,ts,until_day) VALUES(?,?,?,?,?)",
                                   (str(user.id), t, start_day, int(now.timestamp()), until_day))
            ids.append(cur.lastrowid)
        await db.commit()
        ch = await get_ch("erai")
        if ch:
            q = ",".join("?" * len(ids))
            async with db.execute(f"SELECT * FROM memos WHERE id IN ({q}) ORDER BY id", ids) as c:
                rows = await c.fetchall()
            content, view = await memo_group_payload(rows, interaction.guild)
            msg = await ch.send(content, view=view)
            await db.execute(f"UPDATE memos SET msg_id=? WHERE id IN ({q})", [str(msg.id)] + ids)
            await db.commit()
        head = f"「{tasks[0]}」" if len(tasks) == 1 else f"{len(tasks)}件"
        if until_day:
            when = (f"{md(start_day)}から" if start_day > day_str(now) else "") + f"{md(until_day)}まで"
            tail = (f"{when}は毎日の「やること」に残ります。終わったら ✅ を。"
                    f"期限の日の23時にまだ残っていたら、引き継ぐか聞きます")
        else:
            tail = "終わったら ✅ を。23時に残っていたら引き継ぐか聞きます（放置すると翌朝ひっそり消えます）"
        await interaction.response.send_message(f"📌 {head}を宣言しました。{tail}" + hitokoto_suffix(), ephemeral=True)
        if ch:
            await bump_panel("erai")

class MemoListButton(discord.ui.Button):
    def __init__(self, row_):
        super().__init__(label=("✅ " + row_["text"])[:80], style=discord.ButtonStyle.secondary)
        self.memo_id = row_["id"]

    async def callback(self, interaction):
        async with db.execute("SELECT * FROM memos WHERE id=? AND user_id=?", (self.memo_id, str(interaction.user.id))) as c:
            row = await c.fetchone()
        note = ""
        if row and not row["done"]:
            await db.execute("UPDATE memos SET done=1 WHERE id=?", (row["id"],))
            await db.commit()
            await refresh_memo_message(row["msg_id"])
            note = f"✅ 「{row['text']}」やりきった！\n\n"
        rows = await open_memos(interaction.user.id, day_str(now_jst()))
        if rows:
            view = discord.ui.View(timeout=600)
            for r in rows[:25]:
                view.add_item(MemoListButton(r))
            await interaction.response.edit_message(content=note + memo_list_text(rows), view=view)
        else:
            await interaction.response.edit_message(content=note + "📌 全部やりきった！えらい！", view=None)

def memo_list_text(rows):
    return "📌 **今日のやること**（押したら完了）\n" + "\n".join(
        "・" + r["text"] + memo_deadline_label(r) + ("　🌅持ち越し" if r["carried"] else "") for r in rows)

async def send_memo_list(interaction):
    rows = await open_memos(interaction.user.id, day_str(now_jst()))
    if not rows:
        await interaction.response.send_message("📌 今日のやることは空っぽです。「📌 やること宣言」から気軽にどうぞ（期限なしなら翌朝には勝手に消えます・期限をつけるとその日まで残ります）。", ephemeral=True)
        return
    view = discord.ui.View(timeout=600)
    for r in rows[:25]:
        view.add_item(MemoListButton(r))
    await interaction.response.send_message(memo_list_text(rows), view=view, ephemeral=True)

class MemoCarryButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_memo_carry:(?P<uid>\d+)"):
    def __init__(self, uid):
        super().__init__(discord.ui.Button(label="🌅 明日へ引き継ぐ", style=discord.ButtonStyle.primary, custom_id=f"sk_memo_carry:{uid}"))
        self.uid = str(uid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["uid"])

    async def callback(self, interaction):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("本人だけが選べます。", ephemeral=True)
            return
        now = now_jst()
        target = day_str(now + timedelta(days=1)) if now.hour >= JUDGE_HOUR else day_str(now)
        cur = await db.execute("UPDATE memos SET day=?, carried=carried+1 WHERE user_id=? AND day<? AND done=0 "
                               "AND (until_day IS NULL OR until_day < ?)", (target, self.uid, target, target))
        await db.execute("DELETE FROM memo_prompts WHERE user_id=? AND msg_id=?", (self.uid, str(interaction.message.id)))
        await db.commit()
        await interaction.response.edit_message(content=f"🌅 {cur.rowcount}件を明日へ引き継ぎました。ぼちぼちいこう。", view=None)

class MemoDropButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_memo_drop:(?P<uid>\d+)"):
    def __init__(self, uid):
        super().__init__(discord.ui.Button(label="🗑 もういい", style=discord.ButtonStyle.secondary, custom_id=f"sk_memo_drop:{uid}"))
        self.uid = str(uid)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["uid"])

    async def callback(self, interaction):
        if str(interaction.user.id) != self.uid:
            await interaction.response.send_message("本人だけが選べます。", ephemeral=True)
            return
        today = day_str(now_jst())
        keep = "AND (until_day IS NULL OR until_day <= ?)"   # まだ期限前のメモは消さない
        async with db.execute(f"SELECT * FROM memos WHERE user_id=? AND day<=? AND done=0 {keep}", (self.uid, today, today)) as c:
            rows = await c.fetchall()
        await db.execute(f"DELETE FROM memos WHERE user_id=? AND day<=? AND done=0 {keep}", (self.uid, today, today))
        await db.execute("DELETE FROM memo_prompts WHERE user_id=? AND msg_id=?", (self.uid, str(interaction.message.id)))
        await db.commit()
        for mid in {r["msg_id"] for r in rows if r["msg_id"]}:
            await refresh_memo_message(mid)   # ✅済みが残っていれば表示更新、空なら宣言ごと削除
        await interaction.response.edit_message(content="🗑 消しました。やらない決断もえらい。また明日！", view=None)

async def memo_carry_prompts(day):
    """判定後、未完了メモがある人へ引き継ぎ確認（1人1日1回・手動判定でも二重投稿しない）"""
    ch = await get_ch("erai")
    if not ch:
        return
    async with db.execute("SELECT DISTINCT user_id FROM memos WHERE day=?", (day,)) as c:
        uids = [r["user_id"] for r in await c.fetchall()]
    posted = False
    for uid in uids:
        async with db.execute("SELECT 1 FROM memo_prompts WHERE day=? AND user_id=?", (day, uid)) as c:
            if await c.fetchone():
                continue
        rows = [r for r in await open_memos(uid, day) if not memo_until(r) or memo_until(r) <= day]   # 期限前のものは聞かない
        if not rows:
            continue
        view = discord.ui.View(timeout=None)
        view.add_item(MemoCarryButton(uid))
        view.add_item(MemoDropButton(uid))
        msg = await ch.send(f"📌 <@{uid}> 未完了のやること：" + "／".join(r["text"] for r in rows) +
                            "\n明日へ引き継ぐ？（何もしなければ朝にひっそり消えます）", view=view)
        await db.execute("INSERT OR IGNORE INTO memo_prompts(day,user_id,msg_id) VALUES(?,?,?)", (day, uid, str(msg.id)))
        await db.commit()
        posted = True
    if posted:
        await bump_panel("erai")

async def memo_cleanup(today):
    """引き継がれなかった昨日以前のメモを自動削除し、宣言・確認メッセージも畳む。古い夜勤記録も掃除。
    宣言メッセージの扱い：✅だけの宣言は歴史として残す／一部✅は✅だけ残す形に固定／全部未完了なら削除"""
    # 期限つきで期限がまだ来ていない未完了メモは、消さずに今日へ繰り越す（引き継ぎ回数は増やさない）
    await db.execute("UPDATE memos SET day=? WHERE day<? AND done=0 AND until_day IS NOT NULL AND until_day >= ?",
                     (today, today, today))
    await db.commit()
    async with db.execute("SELECT * FROM memos WHERE day<?", (today,)) as c:
        stale = await c.fetchall()
    by_msg = {}
    for r in stale:
        if r["msg_id"]:
            by_msg.setdefault(r["msg_id"], []).append(r)
    await db.execute("DELETE FROM memos WHERE day<?", (today,))
    await db.commit()
    ch = await get_ch("erai")
    for mid, group in by_msg.items():
        async with db.execute("SELECT 1 FROM memos WHERE msg_id=? LIMIT 1", (mid,)) as c:
            survivors = await c.fetchone()
        done_rows = [r for r in group if r["done"]]
        open_rows = [r for r in group if not r["done"]]
        if survivors:
            await refresh_memo_message(mid)   # 引き継がれた分が残っている→描き直し（流れた行は消える）
        elif done_rows and open_rows and ch:
            try:                              # 一部だけ✅→✅だけ残す形に固定
                m = await ch.fetch_message(int(mid))
                u = await get_user(group[0]["user_id"])
                nm = u["name"] if u else "？"
                await m.edit(content=f"✅ **{nm}** のやること\n" + "\n".join(f"✅ ~~{r['text']}~~" for r in done_rows), view=None)
            except Exception:
                pass
        elif not done_rows:
            await memo_delete_public_by_id(mid)   # 何も達成されず流れた→宣言ごと消す
        # 全部✅のメッセージはそのまま歴史として残す
    ch = await get_ch("erai")
    async with db.execute("SELECT * FROM memo_prompts WHERE day<?", (today,)) as c:
        prows = await c.fetchall()
    for p in prows:
        if ch and p["msg_id"]:
            try:
                m = await ch.fetch_message(int(p["msg_id"]))
                await m.edit(content="🌫 引き継がれなかったやることは、朝の風で消えました。", view=None)
            except Exception:
                pass
    await db.execute("DELETE FROM memo_prompts WHERE day<?", (today,))
    await db.execute("DELETE FROM night_shifts WHERE day<?", ((date.fromisoformat(today) - timedelta(days=60)).isoformat(),))
    await db.commit()

# ------------------------------------------------------------
#  作業部屋🖥
#  🔊作業部屋🖥（VC）に入ると計測開始・出ると終了。#作業ログ🖥 に
#  「はじめました（📌今日のやること付き）」→ 退室で「おつかれさま！1h23分」に育つ。
#  ✍ボタンで作業内容をひとこと宣言（任意）。10分未満はログを流さない。
#  ミュート等の切替では計測をリセットしない（NOKOで踏んだバグの再発防止）。
# ------------------------------------------------------------
def fmt_work(seconds):
    m = int((seconds or 0) // 60)
    return f"{m // 60}時間{m % 60}分" if m >= 60 else f"{m}分"

def split_session(start_ts, end_ts):
    """セッションをJSTの日付ごとに分割 → [(day, start, end), ...]（寝落ちで日をまたいでも統計が壊れない）"""
    out = []
    s = start_ts
    while s < end_ts:
        d0 = datetime.fromtimestamp(s, JST)
        nxt = int((d0.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp())
        e = min(end_ts, nxt)
        out.append((day_str(d0), s, e))
        s = e
    return out or [(day_str(datetime.fromtimestamp(start_ts, JST)), start_ts, end_ts)]

async def gakushu_vc_report(uid, seconds):
    """作業時間をみんなで暗記！！の作業時間（/api/vc）にも加算。失敗しても本体処理は続行"""
    import aiohttp
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as sess:
            async with sess.post(GAKUSHU_URL.rstrip("/") + "/api/vc",
                                 json={"secret": GAKUSHU_SECRET, "uid": str(uid), "seconds": int(seconds)}) as r:
                if r.status >= 300:
                    print(f"gakushu 作業時間加算エラー {r.status}", flush=True)
    except Exception as e:
        print(f"gakushu 作業時間加算失敗: {e!r}", flush=True)

class WorkNoteModal(discord.ui.Modal, title="✍ 何をやる？"):
    txt = discord.ui.TextInput(label="作業内容をひとこと", placeholder="例：レポート執筆／英検の単語／課題の実験レポ", max_length=80)

    def __init__(self, session_id):
        super().__init__()
        self.session_id = int(session_id)

    async def on_submit(self, interaction):
        note = self.txt.value.strip()[:80]
        await db.execute("UPDATE work_sessions SET note=? WHERE id=?", (note, self.session_id))
        await db.commit()
        try:
            await update_work_vc_status()   # 宣言をVCのサブタイトルにも反映
        except Exception:
            pass
        now = now_jst()
        memos_txt = "／".join(r["text"] for r in await open_memos(str(interaction.user.id), day_str(now)))
        content = (f"🖥 **{interaction.user.display_name}** さんが作業中：**{note}**"
                   + (f"\n📌 {memos_txt}" if memos_txt else ""))
        try:
            await interaction.response.edit_message(content=content)
        except Exception:
            await interaction.response.send_message("✍ 記録しました。", ephemeral=True)

class WorkNoteButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_work_note:(?P<id>\d+)"):
    def __init__(self, session_id):
        super().__init__(discord.ui.Button(label="✍ 何やるか宣言", style=discord.ButtonStyle.secondary, custom_id=f"sk_work_note:{session_id}"))
        self.session_id = int(session_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"])

    async def callback(self, interaction):
        async with db.execute("SELECT * FROM work_sessions WHERE id=?", (self.session_id,)) as c:
            row = await c.fetchone()
        if not row or row["end_ts"] is not None:
            await interaction.response.send_message("このセッションはもう終了しています。", ephemeral=True)
            return
        if row["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("作業中の本人だけが宣言できます。", ephemeral=True)
            return
        await interaction.response.send_modal(WorkNoteModal(self.session_id))

async def work_start(member):
    await ensure_user(member)
    uid = str(member.id)
    await work_close(uid, member.display_name)   # 万一開きっぱなしがあれば静かに閉じてから
    now = now_jst()
    cur = await db.execute("INSERT INTO work_sessions(user_id,start_ts,day) VALUES(?,?,?)", (uid, int(now.timestamp()), day_str(now)))
    sid = cur.lastrowid
    await db.commit()
    try:
        await update_work_vc_status(now)
    except Exception:
        pass
    ch = await get_ch("worklog")
    if not ch:
        return
    memos_txt = "／".join(r["text"] for r in await open_memos(uid, day_str(now)))
    view = discord.ui.View(timeout=None)
    view.add_item(WorkNoteButton(sid))
    try:
        msg = await ch.send(f"🖥 **{member.display_name}** さんが作業をはじめました" + (f"\n📌 {memos_txt}" if memos_txt else ""), view=view)
        await db.execute("UPDATE work_sessions SET msg_id=? WHERE id=?", (str(msg.id), sid))
        await db.commit()
        await bump_panel("worklog")   # 🎶音楽パネルを最下部に置き直す
    except Exception as e:
        print(f"作業ログ投稿失敗: {e!r}", flush=True)

async def work_close(uid, name, end_ts=None):
    """開いているセッションを閉じて日ごとに記録。10分未満はログ削除、以上なら「おつかれさま」へ書き換え"""
    uid = str(uid)
    async with db.execute("SELECT * FROM work_sessions WHERE user_id=? AND end_ts IS NULL ORDER BY id DESC LIMIT 1", (uid,)) as c:
        row = await c.fetchone()
    if not row:
        return
    end_ts = max(end_ts or int(now_jst().timestamp()), row["start_ts"])
    segs = split_session(row["start_ts"], end_ts)
    d0, _s0, e0 = segs[0]
    await db.execute("UPDATE work_sessions SET end_ts=?, day=? WHERE id=?", (e0, d0, row["id"]))
    for d, s, e in segs[1:]:
        await db.execute("INSERT INTO work_sessions(user_id,start_ts,end_ts,day,note) VALUES(?,?,?,?,?)", (uid, s, e, d, row["note"]))
    await db.commit()
    total = end_ts - row["start_ts"]
    ch = await get_ch("worklog")
    if ch and row["msg_id"]:
        try:
            m = await ch.fetch_message(int(row["msg_id"]))
            if total < WORK_MIN_LOG_SEC:
                await m.delete()   # ちょい入室はログに残さない（時間は記録済み）
            else:
                await m.edit(content=f"🖥 **{name}** 作業おつかれさま！ **{fmt_work(total)}**"
                             + (f"：{row['note']}" if row["note"] else "") + hitokoto_suffix(), view=None)
        except Exception:
            pass
    if total >= 60 and GAKUSHU_SECRET and WORK_TO_GAKUSHU:
        await gakushu_vc_report(uid, total)
    try:
        await update_work_vc_status()
    except Exception:
        pass

async def benkyokai_participants(day, now=None):
    """📖ミニ勉強会：その日の22〜23時枠と作業セッションの重なりを人ごとに分で集計（出入り自由・進行中は枠の終わりまで）"""
    now = now or now_jst()
    t0 = datetime.fromisoformat(day).replace(tzinfo=JST)
    ws, we = t0 + timedelta(hours=BENKYOKAI_START), t0 + timedelta(hours=BENKYOKAI_END)
    async with db.execute("SELECT user_id, start_ts, end_ts FROM work_sessions WHERE day=?", (day,)) as c:
        rows = await c.fetchall()
    per = {}
    for r in rows:
        s = datetime.fromtimestamp(r["start_ts"], JST)
        e = datetime.fromtimestamp(r["end_ts"], JST) if r["end_ts"] else min(now, we)
        ov = (min(e, we) - max(s, ws)).total_seconds()
        if ov > 0:
            per[r["user_id"]] = per.get(r["user_id"], 0) + ov
    out = []
    for uid, sec in sorted(per.items(), key=lambda x: -x[1]):
        mins = int(sec // 60)
        if mins >= 1:
            u = await get_user(uid)
            out.append((uid, (u["name"] if u and u["name"] else uid), mins))
    return out

async def benkyokai_streak(uid, day):
    """dayを含めて遡った勉強会の連続参加日数（events kind='benkyokai' で数える。途切れても叱られない、表示が消えるだけ）"""
    since = (date.fromisoformat(day) - timedelta(days=200)).isoformat()
    async with db.execute("SELECT DISTINCT day FROM events WHERE user_id=? AND kind='benkyokai' AND day BETWEEN ? AND ?",
                          (str(uid), since, day)) as c:
        days = {r["day"] for r in await c.fetchall()}
    n, d = 0, date.fromisoformat(day)
    while d.isoformat() in days:
        n += 1
        d -= timedelta(days=1)
    return n

async def benkyokai_recap(day, now):
    """23:00すぎ：参加記録＋🔥ストリーク計算＋#えらいへ自動報告＋#作業ログへおつかれ発表。参加者数を返す"""
    parts = await benkyokai_participants(day, now)
    if not parts:
        return 0
    erai_ch = await get_ch("erai")
    bits = []
    for uid, name, m in parts:
        await add_event(uid, "benkyokai", note=str(m), ts_dt=now)
        stk = await benkyokai_streak(uid, day)
        fire = f" 🔥{stk}日連続" if stk >= 2 else ""
        bits.append(f"**{name}** {m}分{fire}")
        text = f"ミニ勉強会に参加した（{m}分" + (f"・🔥{stk}日連続" if stk >= 2 else "") + "）"
        cur = await db.execute("INSERT INTO efforts(user_id,text,day,ts) VALUES(?,?,?,?)",
                               (str(uid), text, day, int(now.timestamp())))
        eid = cur.lastrowid
        await db.commit()
        if erai_ch:   # 参加しただけで🌟えらい報告→👏とがんばり屋賞に乗る（罰はなく報酬だけ）
            try:
                view = discord.ui.View(timeout=None)
                view.add_item(PraiseButton(eid, 0))
                msg = await erai_ch.send(_effort_content(name, text, [], []), view=view)
                await db.execute("UPDATE efforts SET msg_id=? WHERE id=?", (str(msg.id), eid))
                await db.commit()
            except Exception as e:
                print(f"勉強会えらい報告エラー: {e!r}", flush=True)
    if erai_ch:
        await bump_panel("erai")
    ch = await get_ch("worklog")
    if ch:
        await ch.send("📖 **今夜のミニ勉強会、おつかれさま！**　" + "／".join(bits) + "　ちょーえらい！")
    return len(parts)

# ---- 🔊作業部屋のVCステータス（チャンネル名の下のサブタイトル） ----
_vc_status_cache = {"v": None}

async def work_vc_status_text(now=None):
    """作業部屋VCに出すステータス文。勉強会中 ＞ 入室中の先頭の人の宣言（✍note、無ければやること📌）＞ 空"""
    now = now or now_jst()
    if BENKYOKAI_START >= 0 and BENKYOKAI_START <= now.hour < BENKYOKAI_END:
        return f"📖 ミニ勉強会ちゅう（〜{BENKYOKAI_END}:00）"
    vc = _player.get("vc")   # 再生中の曲（作業部屋で流しているときだけ）
    if _player.get("current") and vc and vc.is_connected():
        wid = await meta_get("ch_workvc")
        if wid and vc.channel.id == int(wid):
            return _player["current"][:80]
    day = day_str(now)
    async with db.execute("SELECT user_id, note FROM work_sessions WHERE end_ts IS NULL ORDER BY start_ts") as c:
        rows = await c.fetchall()
    for r in rows:
        t = r["note"]
        if not t:
            async with db.execute("SELECT text FROM memos WHERE user_id=? AND day=? AND done=0 ORDER BY id LIMIT 1",
                                  (r["user_id"], day)) as c2:
                m = await c2.fetchone()
            t = m["text"] if m else None
        if t:
            return ("📌 " + t)[:80]
    return ""

async def update_work_vc_status(now=None):
    """作業部屋VCのステータスを更新（変化があるときだけAPIを叩く）"""
    if _player.get("karaoke"):
        return None   # 🎤カラオケ中はサブタイトルを歌詞が占有（終了時にタスクがここを呼び直す）
    wid = await meta_get("ch_workvc")
    vc = bot.get_channel(int(wid)) if wid else None
    if not vc:
        return None
    status = await work_vc_status_text(now)
    if _vc_status_cache["v"] == status:
        return status
    try:
        await vc.edit(status=status or None)
        _vc_status_cache["v"] = status
    except Exception as e:
        print(f"VCステータス更新エラー: {e!r}", flush=True)
    return status

async def reconcile_work_sessions():
    """再起動時の整合：VCにいない人の開きっぱなしセッションを閉じ、いる人のセッションを開く"""
    wid = await meta_get("ch_workvc")
    vc = bot.get_channel(int(wid)) if wid else None
    present = {str(m.id): m for m in vc.members if not m.bot} if vc else {}
    async with db.execute("SELECT DISTINCT user_id FROM work_sessions WHERE end_ts IS NULL") as c:
        open_uids = [r["user_id"] for r in await c.fetchall()]
    for uid in open_uids:
        if uid not in present:
            u = await get_user(uid)
            await work_close(uid, u["name"] if u else "？")
    for uid, m in present.items():
        if uid not in open_uids:
            await work_start(m)

@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return
    b_id = before.channel.id if before.channel else None
    a_id = after.channel.id if after.channel else None
    if b_id == a_id:
        return   # ミュート・画面共有の切替は無視
    if a_id is not None:
        try:
            await auto_wake(member, "VC入室")   # ラジオ体操・作業部屋への入室も起きている証拠
        except Exception as e:
            print(f"自動起床エラー: {e!r}", flush=True)
    try:   # 🎵 プレイヤーVCが無人になったら、60秒の猶予つきで退出（一瞬の出入りでは切れない）
        vc = _player.get("vc")
        if vc and vc.is_connected() and not any(not m.bot for m in vc.channel.members):
            _schedule_leave_check()
    except Exception as e:
        print(f"プレイヤー退出エラー: {e!r}", flush=True)
    wid = await meta_get("ch_workvc")
    if not wid:
        return
    wid = int(wid)
    try:
        if a_id == wid and b_id != wid:
            await work_start(member)
            asyncio.create_task(warmup_player("作業部屋入室"))   # 入室＝そのうち/playしそうなので先に温める
            try:   # 🎧 誰かが入った瞬間にBotも入室してスタンバイ（最初の/playやBGMがすぐ出る）
                if not (_player.get("vc") and _player["vc"].is_connected()):
                    vc = await ensure_player_vc(after.channel)
                    if vc:
                        _player["vc"] = vc
            except Exception as e:
                print(f"🎧 自動入室エラー: {e!r}", flush=True)
        elif b_id == wid and a_id != wid:
            await work_close(member.id, member.display_name)
    except Exception as e:
        print(f"作業部屋エラー: {e!r}", flush=True)

async def work_award(d1, d2):
    """週次通信簿の🖥もくもく賞（同点は全員）"""
    async with db.execute("SELECT user_id, SUM(end_ts-start_ts) AS s FROM work_sessions "
                          "WHERE day BETWEEN ? AND ? AND end_ts IS NOT NULL GROUP BY user_id HAVING s>0 ORDER BY s DESC", (d1, d2)) as c:
        rows = await c.fetchall()
    if not rows:
        return None
    best = rows[0]["s"]
    names = []
    for r in rows:
        if r["s"] != best:
            break
        u = await get_user(r["user_id"])
        names.append(u["name"] if u else "？")
    return {"emoji": "🖥", "title": "もくもく賞", "names": names, "value": fmt_work(best)}


# ============================================================
#  暮らしのTIPS📚（フォーラム×Bot：投稿を知識ストック化・🔖保存・検索・今日のTIPS）
# ============================================================
TIPS_CH_NAME = "暮らしのtips📚"
TIPS_TAGS = [("🍳", "レシピ"), ("🛒", "お得・期間限定"), ("🧹", "家事ハック"), ("💤", "ねむり"), ("🏫", "キャンパス周辺"), ("📦", "その他")]
TIPS_TOPIC = ("暮らしの知恵をストックする場所。レシピ・お店の期間限定・家事ハックなど、後輩の生活を1つ楽にする投稿を。\n"
              "・タイトルは一言で（例「余りごはんで5分チャーハン」）\n"
              "・期間限定情報は本文に「期限: 10/15」と書くと、期限切れ後は検索から自動で消えます\n"
              "・役に立った投稿は 🔖保存（あなたのTIPS帳 /tips mine に入ります）")

def parse_tip_expiry(body, now):
    """本文の「期限: 10/15」を拾って day 文字列に。無ければ None"""
    m = re.search(r"期限[:：]\s*([0-9０-９/／年月日\s:：-]+)", body or "")
    if not m:
        return None
    d = parse_due(m.group(1).strip(), now)
    return day_str(d) if d else None

async def register_tip(thread, starter_content, applied_tags, owner_id):
    expires = parse_tip_expiry(starter_content, now_jst())
    tags = ",".join(t.name for t in (applied_tags or []))
    now = now_jst()
    await db.execute("INSERT OR IGNORE INTO tips(thread_id,author_id,title,tags,day,ts,expires) VALUES(?,?,?,?,?,?,?)",
                     (str(thread.id), str(owner_id or ""), (thread.name or "")[:100], tags, day_str(now), int(now.timestamp()), expires))
    await db.commit()
    async with db.execute("SELECT id FROM tips WHERE thread_id=?", (str(thread.id),)) as c:
        row = await c.fetchone()
    return row["id"] if row else None

@bot.event
async def on_thread_create(thread):
    tid = await meta_get("ch_tips")
    if not tid or thread.parent_id != int(tid):
        return
    starter = None
    for _ in range(4):  # 最初の投稿本文が届くまで少し待つ
        try:
            starter = await thread.fetch_message(thread.id)
            break
        except Exception:
            await asyncio.sleep(2)
    tip_id = await register_tip(thread, starter.content if starter else "", getattr(thread, "applied_tags", None), thread.owner_id)
    if not tip_id:
        return
    if thread.owner:
        await ensure_user(thread.owner)
    async with db.execute("SELECT tags, expires FROM tips WHERE id=?", (tip_id,)) as c:
        t = await c.fetchone()
    view = discord.ui.View(timeout=None)
    view.add_item(TipSaveButton(tip_id, 0))
    try:
        await thread.send("📚 TIPSとして登録しました" + (f"（期限 {t['expires']} まで。過ぎると検索から消えます）" if t["expires"] else "")
                          + "。役に立ったら 🔖 で自分のTIPS帳へ！", view=view)
    except Exception as e:
        print(f"TIPS登録メッセージ失敗: {e!r}", flush=True)

class TipSaveButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_tip:(?P<id>\d+)"):
    def __init__(self, tip_id, count=0):
        super().__init__(discord.ui.Button(label=f"🔖 保存（{count}）", style=discord.ButtonStyle.primary, custom_id=f"sk_tip:{tip_id}"))
        self.tip_id = int(tip_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"])

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        await ensure_user(interaction.user)
        async with db.execute("SELECT 1 FROM tip_saves WHERE tip_id=? AND user_id=?", (self.tip_id, uid)) as c:
            already = await c.fetchone()
        if already:
            await db.execute("DELETE FROM tip_saves WHERE tip_id=? AND user_id=?", (self.tip_id, uid))
            note = "TIPS帳から外しました。"
        else:
            await db.execute("INSERT OR IGNORE INTO tip_saves(tip_id,user_id,day) VALUES(?,?,?)", (self.tip_id, uid, day_str(now_jst())))
            note = "🔖 TIPS帳に入れました（`/tips mine` で一覧）"
        await db.commit()
        async with db.execute("SELECT COUNT(*) AS n FROM tip_saves WHERE tip_id=?", (self.tip_id,)) as c:
            n = (await c.fetchone())["n"]
        view = discord.ui.View(timeout=None)
        view.add_item(TipSaveButton(self.tip_id, n))
        try:
            await interaction.response.edit_message(view=view)
            await interaction.followup.send(note, ephemeral=True)
        except Exception:
            await interaction.response.send_message(note, ephemeral=True)

async def search_tips(keyword=None, limit=10, saved_by=None, today=None):
    """未期限切れのTIPSを 🔖数→新しい順 で返す。keywordはタイトル/タグの部分一致"""
    today = today or day_str(now_jst())
    q = ("SELECT t.*, (SELECT COUNT(*) FROM tip_saves s WHERE s.tip_id=t.id) AS saves FROM tips t "
         "WHERE (t.expires IS NULL OR t.expires >= ?)")
    args = [today]
    if keyword:
        q += " AND (t.title LIKE ? OR t.tags LIKE ?)"
        args += [f"%{keyword}%", f"%{keyword}%"]
    if saved_by:
        q += " AND t.id IN (SELECT tip_id FROM tip_saves WHERE user_id=?)"
        args.append(str(saved_by))
    q += " ORDER BY saves DESC, t.ts DESC LIMIT ?"
    args.append(limit)
    async with db.execute(q, args) as c:
        return await c.fetchall()

def tip_line(r):
    tag = f"（{r['tags']}）" if r["tags"] else ""
    exp = f"　⏳{r['expires'][5:].replace('-', '/')}まで" if r["expires"] else ""
    return f"🔖{r['saves']}　**{r['title']}**{tag}{exp} → <#{r['thread_id']}>"

async def tip_of_day(day):
    """その日の「今日のTIPS」1件（全員同じ・日替わり・🔖上位から選出）"""
    rows = await search_tips(limit=10, today=day)
    if not rows:
        return None
    return rows[zlib.crc32(day.encode()) % len(rows)]

# 未達項目の絵文字 → 処方TIPSの検索キーワード（順に試す）。家事系は「ラベル→家事ハック」の順
PRESCRIBE_MAP = {"☀️": ["ねむり"], "🌙": ["ねむり"], "🍚": ["レシピ"], "🍳": ["レシピ"],
                 "🛁": ["風呂", "家事ハック"], "🪥": ["歯磨き", "家事ハック"]}
KAJI_EMOJIS = {e for _col, _label, e, _subs in KAJI_CATS}

async def prescribe_tip(uid, day, misses):
    """未達項目に効きそうなTIPSを1件選んで「💊 処方TIPS」行を返す。見つからなければ空文字。
    選出は 日付＋ユーザー のハッシュで安定（同じ日に同じ人へは同じ処方、人と日で変わる）"""
    kws = []
    for m in misses:
        parts = m.split()
        if len(parts) < 2:
            continue
        em, label = parts[0], parts[1]
        cands = PRESCRIBE_MAP.get(em) or ([label, "家事ハック"] if em in KAJI_EMOJIS else [])
        for k in cands:
            if k not in kws:
                kws.append(k)
    for kw in kws:
        rows = await search_tips(keyword=kw, today=day)
        if rows:
            r = rows[zlib.crc32(f"{day}:{uid}".encode()) % len(rows)]
            return f"💊 処方TIPS：**{r['title']}** → <#{r['thread_id']}>"
    return ""

tips_grp = app_commands.Group(name="tips", description="暮らしのTIPSを探す・見返す")

@tips_grp.command(name="sagasu", description="TIPSを検索（タイトル・タグの部分一致。期限切れは出ません）")
@app_commands.describe(kensaku="キーワード 例 チャーハン／レシピ（空なら人気順）")
async def tips_sagasu(interaction, kensaku: str = None):
    rows = await search_tips(keyword=(kensaku or "").strip() or None)
    if not rows:
        await interaction.response.send_message("見つかりませんでした。#暮らしのtips📚 に最初の1件を投稿しよう！", ephemeral=True)
        return
    emb = discord.Embed(title=f"📚 TIPS検索{'：' + kensaku if kensaku else '（人気順）'}",
                        description="\n".join(tip_line(r) for r in rows)[:4000], color=discord.Color.gold())
    await interaction.response.send_message(embed=emb, ephemeral=True)

@tips_grp.command(name="mine", description="自分が🔖保存したTIPS帳")
async def tips_mine(interaction):
    rows = await search_tips(saved_by=interaction.user.id, limit=25)
    if not rows:
        await interaction.response.send_message("まだTIPS帳は空です。#暮らしのtips📚 で役に立った投稿の 🔖 を押すとここに貯まります。", ephemeral=True)
        return
    emb = discord.Embed(title=f"🔖 {interaction.user.display_name} のTIPS帳（{len(rows)}）",
                        description="\n".join(tip_line(r) for r in rows)[:4000], color=discord.Color.gold())
    await interaction.response.send_message(embed=emb, ephemeral=True)

@tips_grp.command(name="omakase", description="ランダムに1件")
async def tips_omakase(interaction):
    rows = await search_tips(limit=50)
    if not rows:
        await interaction.response.send_message("まだTIPSがありません。#暮らしのtips📚 に最初の1件をどうぞ！", ephemeral=True)
        return
    import random as _r
    await interaction.response.send_message(embed=discord.Embed(title="🎲 おまかせTIPS", description=tip_line(_r.choice(rows)), color=discord.Color.gold()), ephemeral=True)

bot.tree.add_command(tips_grp)

# ------------------------------------------------------------
#  冷蔵庫の期限リマインド🧊
#  在庫管理はしない。「期限が近い食品だけの使い捨てメモ」。
#  ☀️の朝ダイジェストと、REMIND_HOUR に #ごはん🍚 へのアラートで知らせる。
#  消し忘れても期限から3日で自動削除＝リストが腐らない。
# ------------------------------------------------------------
FRIDGE_PRESETS = {  # kigen 省略時の目安日数（消費期限ベースで安全側）
    "牛乳": 4, "卵": 14, "豆腐": 3, "納豆": 7, "もやし": 2, "食パン": 4,
    "ヨーグルト": 7, "ひき肉": 2, "鶏肉": 2, "豚肉": 3, "牛肉": 3, "魚": 2, "刺身": 1,
    "ハム": 5, "ベーコン": 7, "ウインナー": 7, "ちくわ": 4, "かまぼこ": 5, "油揚げ": 4,
    "カット野菜": 2, "レタス": 5, "キャベツ": 7, "きゅうり": 4, "トマト": 5,
    "ほうれん草": 3, "小松菜": 3, "きのこ": 4, "バナナ": 4, "生クリーム": 3, "チーズ": 14,
}

def fridge_preset_days(name):
    n = unicodedata.normalize("NFKC", name or "").strip()
    if n in FRIDGE_PRESETS:
        return FRIDGE_PRESETS[n]
    for k, d in FRIDGE_PRESETS.items():
        if k in n:   # 「低脂肪牛乳」→牛乳 の部分一致
            return d
    return None

def fridge_due_fmt(due_day):
    _y, m, d = due_day.split("-")
    return f"{int(m)}/{int(d)}"

def fridge_label(due_day, today):
    d = (date.fromisoformat(due_day) - date.fromisoformat(today)).days
    if d < 0:
        return f"⚠️{-d}日過ぎ"
    if d == 0:
        return "今日まで！"
    if d == 1:
        return "明日まで"
    return f"あと{d}日"

async def fridge_items_of(uid):
    async with db.execute("SELECT * FROM fridge_items WHERE user_id=? ORDER BY due_day, id", (str(uid),)) as c:
        return await c.fetchall()

async def fridge_soon(uid, today):
    """今日・明日が期限（＋期限切れでまだ自動削除されていない）ものの表示リスト"""
    out = []
    for r in await fridge_items_of(uid):
        if (date.fromisoformat(r["due_day"]) - date.fromisoformat(today)).days <= 1:
            out.append(f"**{r['name']}**（{fridge_label(r['due_day'], today)}）")
    return out

async def fridge_tip_hints(uid, today, limit=2):
    """期限が近い食品の名前で #暮らしのtips📚 を探して「使いみち」のヒントを返す（無ければ空）"""
    out, used = [], set()
    for r in await fridge_items_of(uid):
        if (date.fromisoformat(r["due_day"]) - date.fromisoformat(today)).days > 1:
            continue
        name = r["name"]
        words = [name] + [p for p in FRIDGE_PRESETS if p in name and p != name]   # 低脂肪牛乳→牛乳 でも探す
        for w in words:
            hits = [t for t in await search_tips(keyword=w, limit=3, today=today) if t["id"] not in used]
            if hits:
                used.add(hits[0]["id"])
                out.append(f"**{name}** を使うなら →「{hits[0]['title']}」<#{hits[0]['thread_id']}>")
                break
        if len(out) >= limit:
            break
    return out

async def fridge_cleanup(today):
    """期限から3日過ぎたものは自動削除（消し忘れてもリストが腐らない）"""
    limit = (date.fromisoformat(today) - timedelta(days=3)).isoformat()
    await db.execute("DELETE FROM fridge_items WHERE due_day < ?", (limit,))
    await db.commit()

async def fridge_remind():
    """REMIND_HOUR に #ごはん🍚 へ、期限が今日・明日のものをまとめてアラート"""
    today = day_str(now_jst())
    await fridge_cleanup(today)
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    async with db.execute("SELECT * FROM fridge_items WHERE due_day<=? ORDER BY user_id, due_day, id", (tomorrow,)) as c:
        rows = await c.fetchall()
    if not rows:
        return
    lines = [f"・<@{r['user_id']}> **{r['name']}**（{fridge_label(r['due_day'], today)}）" for r in rows]
    await post_log("meal", "🧊 **冷蔵庫アラート**：期限が近いよ！使い切りレシピは `/tips sagasu` も参考に\n" + "\n".join(lines))

async def fridge_register(interaction, namae, kigen):
    """食品を1件登録（パネルのフォームと /reizouko add の共通処理）"""
    await ensure_user(interaction.user)
    now = now_jst()
    nm = unicodedata.normalize("NFKC", namae or "").strip()[:30]
    if not nm:
        await interaction.response.send_message("⚠️ 食品名を入れてください（例 牛乳）", ephemeral=True)
        return
    kigen = (kigen or "").strip()
    if kigen:
        d = parse_due(kigen)
        if not d:
            await interaction.response.send_message("⚠️ 期限の形式が読めませんでした。例: `9/7` `10月15日`", ephemeral=True)
            return
        due = day_str(d)
    else:
        days = fridge_preset_days(nm)
        if days is None:
            await interaction.response.send_message(f"「{nm}」の目安日数を知らないので、期限も入れてください（例 `9/7`）。", ephemeral=True)
            return
        due = day_str(now + timedelta(days=days))
    await db.execute("INSERT INTO fridge_items(user_id,name,due_day,created_day) VALUES(?,?,?,?)",
                     (str(interaction.user.id), nm, due, day_str(now)))
    await db.commit()
    await interaction.response.send_message(
        f"🧊 **{nm}** を登録しました（期限 {fridge_due_fmt(due)}・{fridge_label(due, day_str(now))}）。"
        f"前日と当日の朝にお知らせします。期限から3日過ぎると自動で消えます。" + hitokoto_suffix(),
        ephemeral=True)

class FridgeAddModal(discord.ui.Modal, title="🧊 冷蔵庫に登録"):
    nm = discord.ui.TextInput(label="食品名（例 牛乳・もやし・鶏肉）", max_length=30)
    kg = discord.ui.TextInput(label="期限（例 9/7。定番食品は空欄で目安から自動）", required=False, max_length=20)

    async def on_submit(self, interaction):
        await fridge_register(interaction, self.nm.value, self.kg.value)

class FridgeEatSelect(discord.ui.Select):
    def __init__(self, rows, today):
        opts = [discord.SelectOption(label=f"{r['name']}（期限 {fridge_due_fmt(r['due_day'])}・{fridge_label(r['due_day'], today)}）"[:100],
                                     value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="🍽 食べた・使い切ったものを選ぶ（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        uid = str(interaction.user.id)
        names = []
        for v in self.values:
            async with db.execute("SELECT name FROM fridge_items WHERE id=? AND user_id=?", (int(v), uid)) as c:
                r = await c.fetchone()
            if r:
                names.append(r["name"])
                await db.execute("DELETE FROM fridge_items WHERE id=?", (int(v),))
        await db.commit()
        if not names:
            await interaction.response.edit_message(content="対象が見つかりませんでした。", view=None)
            return
        await interaction.response.edit_message(
            content="🍽 " + "、".join(f"**{n}**" for n in names) + " を使い切りました。フードロスゼロ、えらい！" + hitokoto_suffix(), view=None)

async def fridge_show(interaction):
    """一覧＋「食べた」プルダウン（パネルの🧊ボタンと /reizouko list の共通処理）"""
    today = day_str(now_jst())
    rows = await fridge_items_of(interaction.user.id)
    if not rows:
        await interaction.response.send_message("いま登録されている食品はありません。「🧊 冷蔵庫に登録」ボタンか `/reizouko add` でどうぞ。期限の前日と当日の朝にお知らせします。", ephemeral=True)
        return
    lines = [f"・**{r['name']}**　期限 {fridge_due_fmt(r['due_day'])}（{fridge_label(r['due_day'], today)}）" for r in rows]
    view = discord.ui.View(timeout=300)
    view.add_item(FridgeEatSelect(rows, today))
    await interaction.response.send_message("🧊 **冷蔵庫メモ**\n" + "\n".join(lines) +
                                            "\n\n食べたものは下のプルダウンで消せます（期限3日過ぎは自動で消えます）",
                                            view=view, ephemeral=True)

reizouko_grp = app_commands.Group(name="reizouko", description="冷蔵庫の期限リマインド（期限が近い食品だけの使い捨てメモ）")

@reizouko_grp.command(name="add", description="食品を登録。期限の前日と当日の朝にお知らせ（定番食品は kigen 省略OK）")
@app_commands.describe(namae="食品名 例 牛乳（定番食品は候補に目安日数つきで出ます）", kigen="期限 例 9/7（省略すると定番食品は目安から自動設定）")
async def reizouko_add(interaction, namae: str, kigen: str = None):
    await fridge_register(interaction, namae, kigen)

@reizouko_add.autocomplete("namae")
async def reizouko_namae_ac(interaction, current):
    cur = unicodedata.normalize("NFKC", current or "").strip()
    return [app_commands.Choice(name=f"{k}（目安{d}日）", value=k)
            for k, d in FRIDGE_PRESETS.items() if not cur or cur in k][:25]

@reizouko_grp.command(name="list", description="登録中の食品と期限を見る（自分にだけ表示）")
async def reizouko_list(interaction):
    await fridge_show(interaction)

@reizouko_grp.command(name="tabeta", description="食べた・使い切った食品をメモから消す")
@app_commands.describe(namae="消す食品（候補から選ぶのが確実）")
async def reizouko_tabeta(interaction, namae: str):
    uid = str(interaction.user.id)
    row = None
    if namae.isdigit():   # オートコンプリートで選ぶと id が入る
        async with db.execute("SELECT * FROM fridge_items WHERE id=? AND user_id=?", (int(namae), uid)) as c:
            row = await c.fetchone()
    if not row:           # 手入力なら名前一致（同名は期限が近いものから）
        nm = unicodedata.normalize("NFKC", namae or "").strip()
        async with db.execute("SELECT * FROM fridge_items WHERE user_id=? AND name=? ORDER BY due_day, id LIMIT 1", (uid, nm)) as c:
            row = await c.fetchone()
    if not row:
        await interaction.response.send_message("見つかりませんでした。`/reizouko list` で確認してね。", ephemeral=True)
        return
    await db.execute("DELETE FROM fridge_items WHERE id=?", (row["id"],))
    await db.commit()
    await interaction.response.send_message(f"🍽 **{row['name']}** を使い切りました。フードロスゼロ、えらい！" + hitokoto_suffix(), ephemeral=True)

@reizouko_tabeta.autocomplete("namae")
async def reizouko_tabeta_ac(interaction, current):
    cur = unicodedata.normalize("NFKC", current or "").strip()
    return [app_commands.Choice(name=f"{r['name']}（期限 {fridge_due_fmt(r['due_day'])}）", value=str(r["id"]))
            for r in await fridge_items_of(interaction.user.id) if not cur or cur in r["name"]][:25]

bot.tree.add_command(reizouko_grp)

# ------------------------------------------------------------
#  生活用品の使用期限📦
#  マンスリーコンタクト・歯ブラシ・スポンジなど「開封からN日で交換」するものの
#  リマインド。冷蔵庫🧊と違って勝手には消えず、🔄交換でサイクルが回る。判定はしない。
# ------------------------------------------------------------
GOODS_PRESETS = {  # 開封・使用開始からの交換目安（日）。あくまで目安、nichisuu で自由に変更可
    "マンスリーコンタクト": 30, "2weekコンタクト": 14, "コンタクト洗浄液": 90, "目薬": 30,
    "歯ブラシ": 30, "カミソリの刃": 14, "キッチンスポンジ": 30, "ふきん": 30,
    "浄水カートリッジ": 60, "マスカラ": 90, "リップ": 180, "化粧水": 180, "日焼け止め": 365,
}

def goods_preset_days(name):
    n = unicodedata.normalize("NFKC", name or "").strip()
    if n in GOODS_PRESETS:
        return GOODS_PRESETS[n]
    for k, d in GOODS_PRESETS.items():
        if k in n:   # 「左目のマンスリーコンタクト」→マンスリーコンタクト の部分一致
            return d
    return None

async def goods_items_of(uid):
    async with db.execute("SELECT * FROM goods_items WHERE user_id=? ORDER BY due_day, id", (str(uid),)) as c:
        return await c.fetchall()

async def goods_soon(uid, today):
    """今日・明日が期限＋期限切れのものの表示リスト（自動では消えない。🔄交換でリセット）"""
    out = []
    for r in await goods_items_of(uid):
        if (date.fromisoformat(r["due_day"]) - date.fromisoformat(today)).days <= 1:
            out.append(f"**{r['name']}**（{fridge_label(r['due_day'], today)}）")
    return out

async def goods_reset(item_id, today=None):
    """交換・開封し直し → 今日から次のサイクルへ。新しい期限dayを返す"""
    today = today or day_str(now_jst())
    async with db.execute("SELECT * FROM goods_items WHERE id=?", (item_id,)) as c:
        r = await c.fetchone()
    if not r:
        return None
    due = (date.fromisoformat(today) + timedelta(days=r["cycle_days"])).isoformat()
    await db.execute("UPDATE goods_items SET due_day=?, opened_day=? WHERE id=?", (due, today, item_id))
    await db.commit()
    return due

async def goods_register(interaction, namae, nichisuu):
    """品を「今日開封」として登録（パネルのフォームと /kigen add の共通処理）"""
    await ensure_user(interaction.user)
    today = day_str(now_jst())
    nm = unicodedata.normalize("NFKC", namae or "").strip()[:30]
    if not nm:
        await interaction.response.send_message("⚠️ 品名を入れてください（例 マンスリーコンタクト）", ephemeral=True)
        return
    days = nichisuu if nichisuu else goods_preset_days(nm)
    if not days:
        await interaction.response.send_message(f"「{nm}」の目安日数を知らないので、何日ごとに交換するか指定してください（例 `30`）。", ephemeral=True)
        return
    days = max(1, min(730, int(days)))
    due = (date.fromisoformat(today) + timedelta(days=days)).isoformat()
    await db.execute("INSERT INTO goods_items(user_id,name,cycle_days,due_day,opened_day) VALUES(?,?,?,?,?)",
                     (str(interaction.user.id), nm, days, due, today))
    await db.commit()
    await interaction.response.send_message(
        f"📦 **{nm}** を今日開封として登録しました（{days}日ごと・次の交換 {fridge_due_fmt(due)}）。"
        f"期限の前日と当日の朝にお知らせします。交換したら 🔄 で次のサイクルへ。" + hitokoto_suffix(), ephemeral=True)

class GoodsAddModal(discord.ui.Modal, title="📦 使用期限メモに登録"):
    nm = discord.ui.TextInput(label="品名（例 マンスリーコンタクト・歯ブラシ）", max_length=30)
    dd = discord.ui.TextInput(label="何日ごと？（例 30。定番品は空欄で目安から自動）", required=False, max_length=4)

    async def on_submit(self, interaction):
        raw = self.dd.value.strip()
        if raw and not raw.isdigit():
            await interaction.response.send_message("⚠️ 日数は数字で入れてください（例 30）", ephemeral=True)
            return
        await goods_register(interaction, self.nm.value, int(raw) if raw else None)

class GoodsResetSelect(discord.ui.Select):
    def __init__(self, rows, today):
        opts = [discord.SelectOption(label=f"{r['name']}（{fridge_label(r['due_day'], today)}・{r['cycle_days']}日ごと）"[:100],
                                     value=str(r["id"])) for r in rows[:25]]
        super().__init__(placeholder="🔄 交換・開封し直したものを選ぶ（複数OK）", options=opts, min_values=1, max_values=len(opts))

    async def callback(self, interaction):
        notes = []
        for v in self.values:
            async with db.execute("SELECT * FROM goods_items WHERE id=? AND user_id=?", (int(v), str(interaction.user.id))) as c:
                r = await c.fetchone()
            if r:
                due = await goods_reset(r["id"])
                notes.append(f"**{r['name']}**（次は {fridge_due_fmt(due)}）")
        content = ("🔄 " + "、".join(notes) + "　リセットしました。えらい！" + hitokoto_suffix()) if notes else "対象が見つかりませんでした。"
        await interaction.response.edit_message(content=content, view=None)

async def goods_show(interaction):
    today = day_str(now_jst())
    rows = await goods_items_of(interaction.user.id)
    if not rows:
        await interaction.response.send_message("📦 登録されている品はありません。「📦 期限メモに登録」か `/kigen add namae:マンスリーコンタクト` でどうぞ。開封した日に登録すると、交換時期の朝にお知らせします。", ephemeral=True)
        return
    lines = [f"・**{r['name']}**　次の交換 {fridge_due_fmt(r['due_day'])}（{fridge_label(r['due_day'], today)}・{r['cycle_days']}日ごと）" for r in rows]
    view = discord.ui.View(timeout=300)
    view.add_item(GoodsResetSelect(rows, today))
    await interaction.response.send_message("📦 **使用期限メモ**\n" + "\n".join(lines) +
                                            "\n\n交換したら下のプルダウンでリセット。やめる品は `/kigen remove` で。",
                                            view=view, ephemeral=True)

class GoodsResetButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_goods_reset:(?P<id>\d+)"):
    def __init__(self, item_id, label=None):
        super().__init__(discord.ui.Button(label=(label or "🔄 交換した")[:80], style=discord.ButtonStyle.primary,
                                           custom_id=f"sk_goods_reset:{item_id}"))
        self.item_id = int(item_id)

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["id"])

    async def callback(self, interaction):
        async with db.execute("SELECT * FROM goods_items WHERE id=?", (self.item_id,)) as c:
            r = await c.fetchone()
        if not r:
            await interaction.response.send_message("この品はもう登録されていません。", ephemeral=True)
            return
        if r["user_id"] != str(interaction.user.id):
            await interaction.response.send_message("本人だけが押せます。", ephemeral=True)
            return
        due = await goods_reset(r["id"])
        await interaction.response.send_message(f"🔄 **{r['name']}** を交換しました。次は {fridge_due_fmt(due)} です。えらい！" + hitokoto_suffix(), ephemeral=True)

async def goods_remind():
    """REMIND_HOUR に #おふろ🛁 へ、交換期限が今日・明日のものをまとめてアラート（🔄ボタンで即リセット）。
    期限を過ぎたものは毎朝は騒がず、☀️の返事に⚠️で出続ける"""
    today = day_str(now_jst())
    tomorrow = (date.fromisoformat(today) + timedelta(days=1)).isoformat()
    async with db.execute("SELECT * FROM goods_items WHERE due_day IN (?,?) ORDER BY user_id, due_day, id", (today, tomorrow)) as c:
        rows = await c.fetchall()
    if not rows:
        return
    ch = await get_ch("goods")
    if not ch:
        return
    lines = [f"・<@{r['user_id']}> **{r['name']}**（{fridge_label(r['due_day'], today)}・{r['cycle_days']}日ごと）" for r in rows]
    view = discord.ui.View(timeout=None)
    for r in rows[:25]:
        view.add_item(GoodsResetButton(r["id"], label=f"🔄 {r['name']}"))
    await ch.send("📦 **使用期限アラート**：そろそろ交換の時期です\n" + "\n".join(lines), view=view)
    await bump_panel("goods")

kigen_grp = app_commands.Group(name="kigen", description="生活用品の使用期限リマインド（コンタクト・歯ブラシ等の交換サイクル）")

@kigen_grp.command(name="add", description="開封・使用開始した品を登録（定番品は nichisuu 省略で目安から自動）")
@app_commands.describe(namae="品名 例 マンスリーコンタクト（定番品は候補に目安つきで出ます）", nichisuu="何日ごとに交換するか 例 30（省略で定番品は目安から）")
async def kigen_add(interaction, namae: str, nichisuu: int = None):
    await goods_register(interaction, namae, nichisuu)

@kigen_add.autocomplete("namae")
async def kigen_namae_ac(interaction, current):
    cur = unicodedata.normalize("NFKC", current or "").strip()
    return [app_commands.Choice(name=f"{k}（目安{d}日）", value=k)
            for k, d in GOODS_PRESETS.items() if not cur or cur in k][:25]

@kigen_grp.command(name="list", description="登録中の品と次の交換時期を見る（自分にだけ表示）")
async def kigen_list(interaction):
    await goods_show(interaction)

async def ac_my_goods(interaction, current):
    cur = unicodedata.normalize("NFKC", current or "").strip()
    return [app_commands.Choice(name=f"{r['name']}（次の交換 {fridge_due_fmt(r['due_day'])}）", value=str(r["id"]))
            for r in await goods_items_of(interaction.user.id) if not cur or cur in r["name"]][:25]

@kigen_grp.command(name="koukan", description="交換・開封し直した品のサイクルをリセット")
@app_commands.describe(namae="交換した品（候補から選ぶ）")
@app_commands.autocomplete(namae=ac_my_goods)
async def kigen_koukan(interaction, namae: str):
    r = None
    if namae.isdigit():
        async with db.execute("SELECT * FROM goods_items WHERE id=? AND user_id=?", (int(namae), str(interaction.user.id))) as c:
            r = await c.fetchone()
    if not r:
        await interaction.response.send_message("見つかりませんでした。`/kigen list` で確認してね。", ephemeral=True)
        return
    due = await goods_reset(r["id"])
    await interaction.response.send_message(f"🔄 **{r['name']}** を交換しました。次は {fridge_due_fmt(due)} です。" + hitokoto_suffix(), ephemeral=True)

@kigen_grp.command(name="remove", description="品をリマインドから外す")
@app_commands.describe(namae="外す品（候補から選ぶ）")
@app_commands.autocomplete(namae=ac_my_goods)
async def kigen_remove(interaction, namae: str):
    r = None
    if namae.isdigit():
        async with db.execute("SELECT * FROM goods_items WHERE id=? AND user_id=?", (int(namae), str(interaction.user.id))) as c:
            r = await c.fetchone()
    if not r:
        await interaction.response.send_message("見つかりませんでした。`/kigen list` で確認してね。", ephemeral=True)
        return
    await db.execute("DELETE FROM goods_items WHERE id=?", (r["id"],))
    await db.commit()
    await interaction.response.send_message(f"🗑 **{r['name']}** をリマインドから外しました。", ephemeral=True)

bot.tree.add_command(kigen_grp)

class GoodsPanelView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📦 期限メモに登録", style=discord.ButtonStyle.primary, custom_id="sk_goods_add", row=0)
    async def goods_add(self, interaction, button):
        await interaction.response.send_modal(GoodsAddModal())

    @discord.ui.button(label="🔄 見る・交換した", style=discord.ButtonStyle.secondary, custom_id="sk_goods_list", row=0)
    async def goods_list(self, interaction, button):
        await ensure_user(interaction.user)
        await goods_show(interaction)

VIEW_FACTORY["goods"] = GoodsPanelView

async def ensure_tips_forum(guild, cat):
    """#暮らしのtips📚（フォーラム）を用意。コミュニティ未設定などで作れない場合は案内を出す"""
    tch = None
    tid0 = await meta_get("ch_tips")
    if tid0:
        tch = guild.get_channel(int(tid0))
    if tch is None:
        tch = discord.utils.get(guild.channels, name=TIPS_CH_NAME)
    if tch is None:
        tags = [discord.ForumTag(name=n, emoji=discord.PartialEmoji(name=e)) for e, n in TIPS_TAGS]
        try:
            try:
                tch = await guild.create_forum(TIPS_CH_NAME, category=cat, topic=TIPS_TOPIC, available_tags=tags, reason="暮らしのTIPS（/setup）")
            except TypeError:
                tch = await guild.create_forum(TIPS_CH_NAME, category=cat, topic=TIPS_TOPIC, reason="暮らしのTIPS（/setup)")
                await tch.edit(available_tags=tags)
        except Exception as e:
            SETUP_ERRORS.append(f"#{TIPS_CH_NAME}（フォーラム）を作れませんでした（{e}）。サーバー設定→有効化→「コミュニティ」をONにしてから、もう一度 /setup を実行してください")
            return None
    await meta_set("ch_tips", tch.id)
    return tch


# ============================================================
#  /watashi（自分の記録：推移グラフ・前期間との比較・気づき・CSV。本人にだけ表示）
# ============================================================
WATASHI_KIKAN = ["今週", "先週", "今月", "先月", "全部"]

def resolve_period(kikan, now):
    """(d1, d2, prev_d1, prev_d2)。全部のとき prev は None"""
    today = day_str(now)
    if kikan == "先週":
        d1, d2 = week_range(now - timedelta(days=7))
        p1, p2 = week_range(now - timedelta(days=14))
    elif kikan == "今月":
        first = now.replace(day=1)
        d1, d2 = day_str(first), today
        pl = first - timedelta(days=1)
        p1, p2 = day_str(pl.replace(day=1)), day_str(pl)
    elif kikan == "先月":
        first = now.replace(day=1)
        pl = first - timedelta(days=1)
        d1, d2 = day_str(pl.replace(day=1)), day_str(pl)
        ppl = pl.replace(day=1) - timedelta(days=1)
        p1, p2 = day_str(ppl.replace(day=1)), day_str(ppl)
    elif kikan == "全部":
        return "2000-01-01", today, None, None
    else:  # 今週
        d1, d2 = week_range(now)
        p1, p2 = week_range(now - timedelta(days=7))
    return d1, d2, p1, p2

async def personal_stats(uid, d1, d2):
    """期間内の per_day（day/wake分/sleep時間/ach/nizone）と集計を返す"""
    uid = str(uid)
    async with db.execute("SELECT kind, sub, ts, day, note FROM events WHERE user_id=? AND day BETWEEN ? AND ? ORDER BY ts",
                          (uid, d1, d2)) as c:
        evs = await c.fetchall()
    async with db.execute("SELECT day, achieved FROM daily_results WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        ach = {r["day"]: r["achieved"] for r in await c.fetchall()}
    wake, sleep, nizone = {}, {}, {}
    counts = {"meals": 0, "baths": set(), "teeth": 0, "radio": 0, "nizone": 0}
    chores = {}
    for e in evs:
        d = e["day"]
        if e["kind"] == "wake" and d not in wake:
            t = datetime.fromtimestamp(e["ts"], JST)
            wake[d] = t.hour * 60 + t.minute
        elif e["kind"] == "sleep" and e["note"]:
            sleep[d] = sleep.get(d, 0.0) + float(e["note"])
        elif e["kind"] == "nizone":
            nizone[d] = nizone.get(d, 0) + 1
            counts["nizone"] += 1
        elif e["kind"] == "meal":
            counts["meals"] += 1
        elif e["kind"] == "bath":
            counts["baths"].add(d)
        elif e["kind"] == "teeth":
            counts["teeth"] += 1
        elif e["kind"] == "radio":
            counts["radio"] += 1
        elif e["kind"] == "chore":
            chores[e["sub"]] = chores.get(e["sub"], 0) + 1
    days_all = sorted(set(wake) | set(sleep) | set(ach) | set(nizone))
    per_day = [{"day": d, "wake": wake.get(d), "sleep": sleep.get(d), "ach": ach.get(d), "nizone": nizone.get(d, 0)} for d in days_all]
    wakes = [v for v in wake.values()]
    sleeps = [v for v in sleep.values()]
    judged = len(ach)
    achieved = sum(1 for v in ach.values() if v)
    async with db.execute("SELECT COUNT(*) AS n FROM efforts WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        n_efforts = (await c.fetchone())["n"]
    return {
        "per_day": per_day,
        "wake_avg": (sum(wakes) / len(wakes)) if wakes else None,
        "sleep_avg": (sum(sleeps) / len(sleeps)) if sleeps else None,
        "judged": judged, "achieved": achieved,
        "rate": (achieved * 100 / judged) if judged else None,
        "chores": chores, "meals": counts["meals"], "baths": len(counts["baths"]),
        "teeth": counts["teeth"], "radio": counts["radio"], "nizone": counts["nizone"],
        "efforts": n_efforts,
    }

def fmt_min(m):
    return f"{int(m) // 60}:{int(m) % 60:02d}"

def personal_insights(cur, prev):
    """ルールベースの気づき（最大3つ）。AI不使用"""
    import statistics
    tips = []
    per_day = cur["per_day"]
    judged_days = [d for d in per_day if d["ach"] is not None]
    # 弱点の曜日
    if len(judged_days) >= 7 and cur["rate"] is not None:
        by_wd = {}
        for d in judged_days:
            by_wd.setdefault(date.fromisoformat(d["day"]).weekday(), []).append(d["ach"])
        worst = None
        for wd, xs in by_wd.items():
            if len(xs) >= 2:
                r = sum(xs) / len(xs) * 100
                if worst is None or r < worst[1]:
                    worst = (wd, r)
        if worst and cur["rate"] - worst[1] >= 25:
            tips.append(f"{DAY_CHARS[worst[0]]}曜日に崩れがち（達成率 {round(worst[1])}%）。{DAY_CHARS[worst[0]]}曜の予定を見直すと伸びそう")
    # 起床のブレ
    wakes = [d["wake"] for d in per_day if d["wake"] is not None]
    if len(wakes) >= 5:
        sd = statistics.pstdev(wakes)
        if sd >= 90:
            tips.append(f"起床時刻のブレが大きめ（±{int(sd)}分）。毎日だいたい同じ時刻に起きると体が楽になります")
    # 短い睡眠の翌日は二度寝
    short_next, ok_next = [], []
    for a, b in zip(per_day, per_day[1:]):
        if a["sleep"] is not None and (date.fromisoformat(b["day"]) - date.fromisoformat(a["day"])).days == 1:
            (short_next if a["sleep"] < 6 else ok_next).append(1 if b["nizone"] else 0)
    if len(short_next) >= 3 and len(ok_next) >= 3:
        if sum(short_next) / len(short_next) >= sum(ok_next) / len(ok_next) + 0.3:
            tips.append("睡眠が6時間を切った翌日は二度寝しがちです。早く寝るのが一番の二度寝対策かも")
    # 前期間からの改善（ポジティブ）
    if prev:
        if prev.get("wake_avg") and cur.get("wake_avg") and prev["wake_avg"] - cur["wake_avg"] >= 15:
            tips.append(f"前の期間より平均 {int(prev['wake_avg'] - cur['wake_avg'])} 分の早起きに。えらい！")
        if prev.get("rate") is not None and cur.get("rate") is not None and cur["rate"] - prev["rate"] >= 10:
            tips.append(f"達成率が {round(cur['rate'] - prev['rate'])}pt 改善しています。えらい！")
    return tips[:3]

def render_personal_chart(per_day, title):
    """起床・睡眠・達成の推移（PNG bytes）。matplotlib が無ければ None"""
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
    except Exception:
        return None
    if not per_day:
        return None
    fp = None
    f = _find_cjk_font()
    if f:
        fp = font_manager.FontProperties(fname=f)
    days = [d["day"] for d in per_day]
    x = list(range(len(days)))
    step = max(1, len(days) // 8)
    ticks = x[::step]
    labels = [f"{int(days[i][5:7])}/{int(days[i][8:10])}" for i in ticks]
    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(8, 7.4), dpi=120, height_ratios=[3, 3, 1])
    ax1.plot(x, [d["wake"] / 60 if d["wake"] is not None else float("nan") for d in per_day], marker="o", color="#d9701a")
    ax1.set_title(f"起床時刻（{title}）", fontproperties=fp); ax1.set_ylabel("時", fontproperties=fp)
    ax2.plot(x, [d["sleep"] if d["sleep"] is not None else float("nan") for d in per_day], marker="s", color="#2f6f73")
    ax2.set_title("睡眠時間", fontproperties=fp); ax2.set_ylabel("時間", fontproperties=fp)
    for d, xi in zip(per_day, x):
        if d["ach"] is not None:
            ax3.bar(xi, 1, color=("#4caf50" if d["ach"] else "#e53935"), width=0.8)
    ax3.set_title("判定（緑=達成／赤=未達）", fontproperties=fp)
    ax3.set_yticks([])
    for ax in (ax1, ax2, ax3):
        ax.set_xticks(ticks)
        ax.set_xticklabels(labels, fontproperties=fp)
        ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    return buf

# ---- 📖 月間 生活のしおり（個人リザルトカード・SNSに貼れる縦長1枚） ----
def month_bounds(y, m):
    first = date(y, m, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first.isoformat(), last.isoformat()

async def shiori_stats(uid, y, m):
    """しおり用の月間集計（/watashiの集計＋ストリーク・拍手・作業時間・日ごとの達成）"""
    uid = str(uid)
    d1, d2 = month_bounds(y, m)
    st = await personal_stats(uid, d1, d2)
    async with db.execute("SELECT day, achieved FROM daily_results WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        ach = {r["day"]: r["achieved"] for r in await c.fetchall()}
    async with db.execute("SELECT day FROM off_days WHERE user_id=? AND day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        off = {r["day"] for r in await c.fetchall()}
    best = run = 0
    dd = date.fromisoformat(d1)
    while dd.isoformat() <= d2:
        k = dd.isoformat()
        if k in off:
            pass                     # お休みはストリークを切らない
        elif ach.get(k):
            run += 1
            best = max(best, run)
        elif k in ach:
            run = 0
        dd += timedelta(days=1)
    async with db.execute("SELECT COUNT(*) AS n FROM meal_praise mp JOIN events e ON e.id=mp.event_id "
                          "WHERE e.user_id=? AND mp.day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        claps = (await c.fetchone())["n"]
    async with db.execute("SELECT COUNT(*) AS n FROM effort_praise ep JOIN efforts f ON f.id=ep.effort_id "
                          "WHERE f.user_id=? AND ep.day BETWEEN ? AND ?", (uid, d1, d2)) as c:
        claps += (await c.fetchone())["n"]
    async with db.execute("SELECT COALESCE(SUM(end_ts-start_ts),0) AS s FROM work_sessions "
                          "WHERE user_id=? AND day BETWEEN ? AND ? AND end_ts IS NOT NULL", (uid, d1, d2)) as c:
        work_sec = (await c.fetchone())["s"] or 0
    return {**st, "y": y, "m": m, "ach_map": ach, "off": off, "best_run": best, "claps": claps, "work_sec": work_sec}

def render_shiori_card(name, st):
    """月間しおりカード（PNG bytes）。PIL/日本語フォントが無ければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    W, H, pad = 1080, 1350, 60
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    y, m = st["y"], st["m"]
    # 見出し：手描き数字（あれば）＋「月の 生活のしおり」、右上にサークルロゴ
    x = pad
    digits = [_gohan_art(f"d{ch}") for ch in str(m)]
    if all(digits):
        for dg in digits:
            x += _paste_art(img, dg, x, 54, 104) + 6
        x += 8
    else:
        d.text((x, 62), f"{m}", font=F(96, True), fill=CARD_ORANGE)
        x += d.textlength(f"{m}", font=F(96, True)) + 8
    d.text((x, 92), "月の 生活のしおり", font=F(62, True), fill=CARD_INK)
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, W - pad - round(logo.width * 120 / logo.height), 36, 120)
    spark = _gohan_art("sparkle")
    if spark:
        _paste_art(img, spark, int(x + d.textlength("月の 生活のしおり", font=F(62, True))) + 10, 60, 48)
    d.text((pad, 196), f"{name} さん", font=F(40, True), fill=CARD_INK)
    d.text((W - pad, 206), f"{y}年{m}月", font=F(28), fill=CARD_MUTED, anchor="ra")
    # 数字タイル 3×2
    def hm(sec):
        return f"{int(sec // 3600)}h{int(sec % 3600 // 60):02d}m" if sec else "—"
    tiles = [
        ("☀", "平均起床", fmt_min(st["wake_avg"]) if st["wake_avg"] is not None else "—", ""),
        ("🌙", "平均睡眠", fmt_hours(st["sleep_avg"]) if st["sleep_avg"] is not None else "—", ""),
        ("◎", "達成率", f"{round(st['rate'])}%" if st["rate"] is not None else "—",
         f"{st['achieved']}/{st['judged']}日" if st["judged"] else ""),
        ("🔥", "最長連続", f"{st['best_run']}日", "お休みは切れない"),
        ("🖥", "作業部屋", hm(st["work_sec"]), ""),
        ("👏", "もらった拍手", f"{st['claps']}", "ごはん・えらい"),
    ]
    gap, ty, th = 24, 280, 170
    tw = (W - pad * 2 - gap * 2) // 3
    for i, (ic, lb, val, sub) in enumerate(tiles):
        tx = pad + (i % 3) * (tw + gap)
        yy = ty + (i // 3) * (th + gap)
        d.rounded_rectangle([tx, yy, tx + tw, yy + th], radius=22, fill="#fffaf2", outline=CARD_LINE, width=2)
        _draw_mixed(d, tx + 22, yy + 18, f"{ic} {lb}", F(26), E(26), CARD_MUTED)
        vf = F(56 if len(val) <= 6 else 44, True)
        d.text((tx + 22, yy + 62), val, font=vf, fill=CARD_ORANGE if i in (2, 3) else CARD_INK)
        if sub:
            d.text((tx + 22, yy + th - 32), sub, font=F(16), fill=CARD_MUTED)
    # 達成カレンダー
    cy = ty + th * 2 + gap + 46
    _draw_mixed(d, pad, cy, "📅 達成カレンダー", F(30, True), E(30), CARD_INK)
    legend = [("#d9701a", "達成"), ("#e8dfcf", "お休み"), ("#b9b2a6", "未達")]
    lx = W - pad
    for col, t in reversed(legend):
        lx -= d.textlength(t, font=F(20)) + 4
        d.text((lx, cy + 8), t, font=F(20), fill=CARD_MUTED)
        lx -= 22
        d.ellipse([lx, cy + 10, lx + 16, cy + 26], fill=col)
        lx -= 18
    gy = cy + 56
    cw = (W - pad * 2) // 7
    for i, wd in enumerate("月火水木金土日"):
        d.text((pad + i * cw + cw // 2, gy), wd, font=F(22, True), fill=CARD_MUTED, anchor="ma")
    first = date(y, m, 1)
    d1, d2 = month_bounds(y, m)
    ndays = date.fromisoformat(d2).day
    rh = 76
    for day in range(1, ndays + 1):
        k = date(y, m, day).isoformat()
        pos = first.weekday() + day - 1
        cx = pad + (pos % 7) * cw + cw // 2
        cyy = gy + 40 + (pos // 7) * rh + rh // 2
        r = 28
        if k in st["off"]:
            d.ellipse([cx - r, cyy - r, cx + r, cyy + r], fill="#e8dfcf")
            d.text((cx, cyy), str(day), font=F(24), fill=CARD_MUTED, anchor="mm")
        elif st["ach_map"].get(k):
            d.ellipse([cx - r, cyy - r, cx + r, cyy + r], fill=CARD_ORANGE)
            d.text((cx, cyy), str(day), font=F(24, True), fill="#ffffff", anchor="mm")
        elif k in st["ach_map"]:
            d.ellipse([cx - 7, cyy + 16, cx + 7, cyy + 30], fill="#b9b2a6")
            d.text((cx, cyy - 4), str(day), font=F(24), fill=CARD_MUTED, anchor="mm")
        else:
            d.text((cx, cyy), str(day), font=F(24), fill="#c9c1b3", anchor="mm")
    # フッター
    d.line([pad, H - 104, W - pad, H - 104], fill=CARD_LINE, width=2)
    bowl = _gohan_art("bowl")
    fx = pad
    if bowl:
        fx += _paste_art(img, bowl, pad, H - 92, 64) + 14
    d.text((fx, H - 84), "最低限生活リズムサークル", font=F(30, True), fill=CARD_INK)
    d.text((W - pad, H - 76), "来月もレッツなまりず", font=F(22), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

@bot.tree.command(name="shiori", description="月間「生活のしおり」カードをもらう（SNSに貼れる1枚・自分にだけ表示）")
@app_commands.describe(tsuki="何月のしおり？（省略=今月。月初3日までは先月）")
async def shiori_command(interaction, tsuki: app_commands.Range[int, 1, 12] = None):
    await interaction.response.defer(ephemeral=True)
    now = now_jst()
    if tsuki is None:
        ref = now - timedelta(days=3) if now.day <= 3 else now
        y, m = ref.year, ref.month
    else:
        m = tsuki
        y = now.year if m <= now.month else now.year - 1
    st = await shiori_stats(interaction.user.id, y, m)
    if not st["per_day"]:
        await interaction.followup.send(f"{y}年{m}月の記録がまだありません。", ephemeral=True)
        return
    buf = await asyncio.to_thread(render_shiori_card, interaction.user.display_name, st)
    if not buf:
        await interaction.followup.send("⚠️ カードを作れませんでした（VMに日本語フォントが必要）。", ephemeral=True)
        return
    await interaction.followup.send(
        f"📖 {m}月の生活のしおりです。長押し（右クリック）で保存してSNSにもどうぞ！\n-# #最低限生活リズムサークル",
        file=discord.File(buf, filename=f"shiori-{y}-{m:02d}.png"), ephemeral=True)

# ---- 🎉 サークル記念カード（発足から半年・1周年…にみんなの合計を1枚に） ----
CIRCLE_FOUNDED = os.getenv("CIRCLE_FOUNDED", "2026-09-01")

def anniversary_label(d, founded=None):
    """記念日なら '半年' / '1周年' / 'n周年'。それ以外は None"""
    f = date.fromisoformat(founded or CIRCLE_FOUNDED)
    if d.day != f.day or d <= f:
        return None
    months = (d.year - f.year) * 12 + (d.month - f.month)
    if months == 6:
        return "半年"
    if months > 0 and months % 12 == 0:
        return f"{months // 12}周年"
    return None

async def circle_totals(until_day):
    """発足日〜until_dayのサークル全体の合計"""
    d1 = CIRCLE_FOUNDED

    async def one(sql, args=()):
        async with db.execute(sql, args) as c:
            r = await c.fetchone()
        return (r[0] if r else 0) or 0
    rng = (d1, until_day)
    return {
        "members": await one("SELECT COUNT(DISTINCT user_id) FROM events WHERE day BETWEEN ? AND ?", rng),
        "wakes": await one("SELECT COUNT(*) FROM events WHERE kind='wake' AND day BETWEEN ? AND ?", rng),
        "meals": await one("SELECT COUNT(*) FROM events WHERE kind='meal' AND day BETWEEN ? AND ?", rng),
        "photos": await one("SELECT COUNT(*) FROM events WHERE kind='meal' AND msg_id IS NOT NULL AND day BETWEEN ? AND ?", rng),
        "chores": await one("SELECT COUNT(*) FROM events WHERE kind='chore' AND day BETWEEN ? AND ?", rng),
        "radio": await one("SELECT COUNT(*) FROM events WHERE kind='radio' AND day BETWEEN ? AND ?", rng),
        "work_h": int((await one("SELECT SUM(end_ts-start_ts) FROM work_sessions WHERE end_ts IS NOT NULL AND day BETWEEN ? AND ?", rng)) // 3600),
        "efforts": await one("SELECT COUNT(*) FROM efforts WHERE day BETWEEN ? AND ?", rng),
        "claps": await one("SELECT COUNT(*) FROM meal_praise WHERE day BETWEEN ? AND ?", rng)
                 + await one("SELECT COUNT(*) FROM effort_praise WHERE day BETWEEN ? AND ?", rng),
        "kadai": await one("SELECT COUNT(*) FROM assignment_done WHERE skip=0"),
        "best": await one("SELECT MAX(best_streak) FROM users"),
    }

def render_anniversary_card(label, totals, founded, today):
    """記念カード（1080×1350 PNG）。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont
    except Exception:
        return None
    f = _find_cjk_font()
    if not f:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    W, H, pad = 1080, 1350, 60
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, (W - round(logo.width * 150 / logo.height)) // 2, 44, 150)
    head = f"発足から {label}！" if label == "半年" else f"{label}！"
    tw = _mixed_w(d, "🎉 " + head, F(72, True), E(72))
    _draw_mixed(d, (W - tw) // 2, 214, "🎉 " + head, F(72, True), E(72), CARD_ORANGE)
    fd, td = date.fromisoformat(founded), date.fromisoformat(today)
    sub = f"{fd.year}.{fd.month}.{fd.day} → {td.year}.{td.month}.{td.day}　みんなで積み上げた記録"
    d.text((W // 2, 322), sub, font=F(28), fill=CARD_MUTED, anchor="ma")
    tiles = [("☀", "起きた", f"{totals['wakes']:,}回"), ("🍚", "ごはん報告", f"{totals['meals']:,}回"),
             ("📸", "ごはん写真", f"{totals['photos']:,}枚"), ("🧹", "家事", f"{totals['chores']:,}回"),
             ("🖥", "作業部屋", f"{totals['work_h']:,}時間"), ("🏃", "ラジオ体操", f"{totals['radio']:,}回"),
             ("📚", "課題提出", f"{totals['kadai']:,}件"), ("👏", "拍手", f"{totals['claps']:,}回")]
    gap, ty, th = 24, 392, 160
    tw2 = (W - pad * 2 - gap) // 2
    for i, (ic, lb, val) in enumerate(tiles):
        tx = pad + (i % 2) * (tw2 + gap)
        yy = ty + (i // 2) * (th + gap)
        d.rounded_rectangle([tx, yy, tx + tw2, yy + th], radius=22, fill="#fffaf2", outline=CARD_LINE, width=2)
        _draw_mixed(d, tx + 26, yy + 22, f"{ic} {lb}", F(28), E(28), CARD_MUTED)
        d.text((tx + 26, yy + 68), val, font=F(56, True), fill=CARD_INK)
    by = ty + 4 * (th + gap) + 10
    d.text((W // 2, by), f"参加した人 {totals['members']} 人 ・ いちばん長い連続達成 {totals['best']} 日",
           font=F(30, True), fill=CARD_INK, anchor="ma")
    d.line([pad, H - 104, W - pad, H - 104], fill=CARD_LINE, width=2)
    bowl = _gohan_art("bowl")
    fx = pad
    if bowl:
        fx += _paste_art(img, bowl, pad, H - 92, 64) + 14
    d.text((fx, H - 84), "最低限生活リズムサークル", font=F(30, True), fill=CARD_INK)
    d.text((W - pad, H - 76), "これからもレッツなまりず", font=F(22), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def build_anniversary(today_iso, label):
    totals = await circle_totals(today_iso)
    return await asyncio.to_thread(render_anniversary_card, label, totals, CIRCLE_FOUNDED, today_iso), totals

async def maybe_post_anniversary(now):
    """記念日の9時に #つうしんぼ へ記念カード（1日1回）"""
    label = anniversary_label(now.date())
    day = day_str(now)
    if not label or now.hour != 9 or await meta_get("anniv_posted") == day:
        return None
    await meta_set("anniv_posted", day)
    buf, totals = await build_anniversary(day, label)
    ch = await get_ch("tsushinbo")
    if not ch:
        return None
    text = (f"🎉 **最低限生活リズムサークル、{'発足から' if label == '半年' else ''}{label}！**\n"
            f"みんなで起きた回数 **{totals['wakes']:,}回**、ごはん報告 **{totals['meals']:,}回**。毎日ちょっとずつの積み重ねです。"
            "これからもレッツなまりず🌅\n-# 画像はSNSにもどうぞ #最低限生活リズムサークル")
    if buf:
        await ch.send(text, file=discord.File(buf, filename=f"anniversary-{day}.png"))
    else:
        await ch.send(text)
    return True

# ---- 🎧 週間ヘビロテカード（作業部屋でよく流れた曲） ----
async def heavy_rotation(d1, d2, top=5):
    """期間内のリクエスト集計 → (曲[(表示名, 回数)], 人[(名前, 回数)], 総数)。
    同じ人が同じ日に同じ曲を何度入れても1回（プレイリスト一括追加の水増し防止）"""
    t1 = int(datetime.fromisoformat(d1).replace(tzinfo=JST).timestamp())
    t2 = int((datetime.fromisoformat(d2).replace(tzinfo=JST) + timedelta(days=1)).timestamp())
    async with db.execute("SELECT p.user_id, p.title, p.ts, COALESCE(u.name,'?') AS uname FROM play_history p "
                          "LEFT JOIN users u ON u.id=p.user_id WHERE p.ts >= ? AND p.ts < ? AND p.title IS NOT NULL",
                          (t1, t2)) as c:
        rows = await c.fetchall()
    seen, songs, people, disp = set(), {}, {}, {}
    for r in rows:
        k = re.sub(r"\s+", "", clean_track_title(r["title"])).casefold()
        if not k:
            continue
        day = datetime.fromtimestamp(r["ts"], JST).date().isoformat()
        sig = (r["user_id"], k, day)
        if sig in seen:
            continue
        seen.add(sig)
        songs[k] = songs.get(k, 0) + 1
        disp.setdefault(k, clean_track_title(r["title"]) or r["title"])
        people[r["uname"]] = people.get(r["uname"], 0) + 1
    rank = sorted(songs.items(), key=lambda x: -x[1])[:top]
    return ([(disp[k], n) for k, n in rank], sorted(people.items(), key=lambda x: -x[1])[:3], len(seen))

def render_heavy_rotation_card(songs, people, d1, d2):
    """ヘビロテ1枚（PNG bytes）。描けなければ None"""
    try:
        from PIL import Image as PImage, ImageDraw, ImageFont
    except Exception:
        return None
    f = _find_cjk_font()
    if not f or not songs:
        return None
    fb = _find_cjk_font_bold() or f
    ef = _find_emoji_font()
    W, H, pad = 1080, 1080, 60
    img = PImage.new("RGB", (W, H), CARD_CREAM)
    d = ImageDraw.Draw(img)
    F = lambda s, bold=False: ImageFont.truetype(fb if bold else f, s)
    E = lambda s: ImageFont.truetype(ef, s) if ef else None
    _draw_mixed(d, pad, 64, "🎧 今週のヘビロテ", F(64, True), E(64), CARD_INK)
    d.text((pad + 4, 158), f"作業部屋でよく流れた曲　{d1[5:].replace('-', '/')}〜{d2[5:].replace('-', '/')}",
           font=F(26), fill=CARD_MUTED)
    logo = _gohan_art("logo")
    if logo:
        _paste_art(img, logo, W - pad - round(logo.width * 110 / logo.height), 40, 110)
    top_n = songs[0][1]
    y = 236
    rowh = 128
    for i, (title, n) in enumerate(songs):
        col = (CARD_ORANGE, CARD_AMBER, "#e8c08a")[i] if i < 3 else CARD_LINE
        d.ellipse([pad, y + 14, pad + 72, y + 86], fill=col)
        d.text((pad + 36, y + 50), str(i + 1), font=F(40, True), fill="#ffffff" if i < 3 else CARD_MUTED, anchor="mm")
        tf = F(36, True)
        t = title
        while d.textlength(t, font=tf) > W - pad * 2 - 120 and len(t) > 2:
            t = t[:-2] + "…"
        d.text((pad + 100, y + 10), t, font=tf, fill=CARD_INK)
        bw = W - pad * 2 - 100 - 110
        d.rounded_rectangle([pad + 100, y + 70, pad + 100 + bw, y + 90], radius=10, fill=CARD_TRACK)
        d.rounded_rectangle([pad + 100, y + 70, pad + 100 + max(20, int(bw * n / top_n)), y + 90], radius=10, fill=col)
        d.text((W - pad, y + 80), f"{n}回", font=F(28, True), fill=CARD_MUTED, anchor="rm")
        y += rowh
    if people:
        _draw_mixed(d, pad, H - 190, "🙌 よく流してくれた人：" + "・".join(p for p, _ in people), F(30), E(30), CARD_INK)
    d.line([pad, H - 120, W - pad, H - 120], fill=CARD_LINE, width=2)
    d.text((pad, H - 96), "最低限生活リズムサークル", font=F(30, True), fill=CARD_INK)
    d.text((W - pad, H - 88), "#作業部屋BGM", font=F(24), fill=CARD_MUTED, anchor="ra")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf

async def post_heavy_rotation(now=None):
    """日曜の通信簿のあと、#作業ログ🖥 に今週のヘビロテを投稿（リクエストが少ない週は何もしない）"""
    now = now or now_jst()
    d1, d2 = week_range(now)
    songs, people, total = await heavy_rotation(d1, d2)
    if total < 3 or not songs:
        return None
    ch = await get_ch("worklog")
    if not ch:
        return None
    buf = await asyncio.to_thread(render_heavy_rotation_card, songs, people, d1, d2)
    if buf:
        await ch.send("🎧 **今週のヘビロテ**（作業部屋でよく流れた曲）",
                      file=discord.File(buf, filename=f"heavy-rotation-{d2}.png"), silent=True)
    else:
        await ch.send("🎧 **今週のヘビロテ**\n" + "\n".join(f"{i}. {t}（{n}回）" for i, (t, n) in enumerate(songs, 1)), silent=True)
    await bump_panel("worklog")
    return True

def build_personal_csv(evs, results):
    import csv
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["day", "kind", "sub", "time", "note"])
    for e in evs:
        w.writerow([e["day"], e["kind"], e["sub"] or "", datetime.fromtimestamp(e["ts"], JST).strftime("%H:%M"), e["note"] or ""])
    w.writerow([])
    w.writerow(["day", "achieved", "misses"])
    for r in results:
        w.writerow([r["day"], r["achieved"], (r["misses"] or "").replace("\n", " / ")])
    return buf.getvalue().encode("utf-8-sig")

@bot.tree.command(name="watashi", description="自分の記録をくわしく（推移グラフ・前の期間との比較・気づき。自分にだけ表示）")
@app_commands.describe(kikan="期間（既定=今週）", csv="True にすると全記録をCSVでもらえる")
@app_commands.choices(kikan=[app_commands.Choice(name=k, value=k) for k in WATASHI_KIKAN])
async def watashi_command(interaction, kikan: str = "今週", csv: bool = False):
    await interaction.response.defer(ephemeral=True)
    user = interaction.user
    await ensure_user(user)
    now = now_jst()
    d1, d2, p1, p2 = resolve_period(kikan, now)
    cur = await personal_stats(user.id, d1, d2)
    prev = await personal_stats(user.id, p1, p2) if p1 else None
    u = await get_user(user.id)
    if not cur["per_day"]:
        await interaction.followup.send(f"{kikan}の記録はまだありません。", ephemeral=True)
        return
    def delta(a, b, unit, invert=False):
        if a is None or b is None:
            return ""
        d = a - b
        if abs(d) < 1:
            return "（変わらず）"
        arrow = "▼" if (d < 0) != invert else "▲"
        return f"（前の期間 {arrow}{abs(round(d))}{unit}）"
    st_now = await streak_of(user.id, day_str(now))
    emb = discord.Embed(title=f"📈 {user.display_name} の記録（{kikan}：{d1[5:].replace('-', '/')}〜{d2[5:].replace('-', '/')}）",
                        color=discord.Color.gold())
    matome = [
        "☀️ 平均起床：" + (fmt_min(cur["wake_avg"]) + delta(cur["wake_avg"], prev and prev["wake_avg"], "分", invert=True) if cur["wake_avg"] is not None else "—"),
        "🌙 平均睡眠：" + (fmt_hours(cur["sleep_avg"]) + delta(cur["sleep_avg"] and cur["sleep_avg"] * 60, prev and prev["sleep_avg"] and prev["sleep_avg"] * 60, "分") if cur["sleep_avg"] is not None else "—"),
        "🎯 達成率：" + (f"{round(cur['rate'])}%（{cur['achieved']}/{cur['judged']}日）" + delta(cur["rate"], prev and prev["rate"], "pt") if cur["rate"] is not None else "—（判定なし）"),
        f"🔥 連続達成：{st_now} 日（自己ベスト {max(st_now, (u['best_streak'] or 0))} 日）",
    ]
    emb.add_field(name="📊 まとめ", value="\n".join(matome), inline=False)
    ch_txt = "／".join(f"{CHORE_LABEL.get(k, k)}×{n}" for k, n in sorted(cur["chores"].items(), key=lambda x: -x[1])) or "なし"
    naiyaku = [
        f"🧹 家事：{sum(cur['chores'].values())} 回（{ch_txt}）",
        f"🍚 食事報告：{cur['meals']} 回　🛁 入浴：{cur['baths']} 日　🪥 歯磨き：{cur['teeth']} 回",
        f"🏃 ラジオ体操：{cur['radio']} 回　😴 二度寝：{cur['nizone']} 回　🌟 えらい報告：{cur['efforts']} 件",
    ]
    emb.add_field(name="🧾 内訳", value="\n".join(naiyaku), inline=False)
    ins = personal_insights(cur, prev)
    if ins:
        emb.add_field(name="💡 気づき", value="\n".join("・" + t for t in ins), inline=False)
    emb.set_footer(text="この表示はあなたにしか見えません。比べる相手は、過去の自分だけ。")
    files = []
    try:
        buf = await asyncio.to_thread(render_personal_chart, cur["per_day"], kikan)
        if buf:
            files.append(discord.File(buf, filename="watashi.png"))
            emb.set_image(url="attachment://watashi.png")
    except Exception as e:
        print(f"個人グラフ生成エラー: {e!r}", flush=True)
    if csv:
        async with db.execute("SELECT kind, sub, ts, day, note FROM events WHERE user_id=? ORDER BY ts", (str(user.id),)) as c:
            all_evs = await c.fetchall()
        async with db.execute("SELECT day, achieved, misses FROM daily_results WHERE user_id=? ORDER BY day", (str(user.id),)) as c:
            all_res = await c.fetchall()
        data = build_personal_csv(all_evs, all_res)
        files.append(discord.File(io.BytesIO(data), filename=f"seikatsu-{day_str(now)}.csv"))
    await interaction.followup.send(embed=emb, files=files, ephemeral=True)

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
    await find_or_rename("workvc", WORK_VC_NAME, WORK_VC_NAME, guild.voice_channels,
                         lambda n: guild.create_voice_channel(n, category=cat))
    haj = await get_ch("hajimeni")
    if haj:
        await make_readonly(haj, guild, "ガイド用チャンネルは読み取り専用")
        if SETUP_TUTORIAL:   # 既定オフ：#はじめに📖 は管理者が手で書いた最低限の説明を使う（Botの長い説明で上書きしない）
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
    tips_ch = await ensure_tips_forum(guild, cat)
    try:
        botlog = await ensure_botlog_channel(guild, cat)
    except Exception as e:
        botlog = None
        SETUP_ERRORS.append(f"#{BOTLOG_CH_NAME} を作れませんでした（{e!r}）")
    await meta_set("guild_id", guild.id)
    for key in VIEW_FACTORY:
        await refresh_panel(key)   # 最下部にあるパネルは編集だけ＝変更のないチャンネルに新着を出さない
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
                "・**ryouri / souji / sara / sentaku / gomi** 家事を種類ごとに「N日に1回」（例 `ryouri:1 sentaku:3`・0で解除・毎日判定）\n"
                "・**rajio** 毎朝のラジオ体操に参加する（True/False）\n\n"
                f"🏃 ラジオ体操は毎朝 **{RADIO_TIME}** に 🔊ラジオ体操🏃 で自動再生。#起床🌅 の 🏃 ボタンで呼び出し（メンション）のON/OFF。\n"
                "📚 **課題**：`/jikanwari add` で履修科目を登録（科目名で検索・最大5つずつ）→ 気づいた人が `/kadai add` で課題を登録すると、"
                f"同じ科目の履修者だけに #課題📚 で通知。3日前・前日・当日 {REMIND_HOUR}:00 に未完了の人へリマインド。投稿の ✅ で完了。\n\n"
                "🔥 **連続達成**：判定で未達ゼロの日が続くと連続日数が伸びる（3・7・14・30日…で祝福）。`/kiroku` で確認。\n"
                "📮 **通信簿**：毎週日曜の判定後に #つうしんぼ📮 へ達成率ランキング・各賞・起床/睡眠グラフ。月末は月間表彰（MVP）。\n"
                "🛌 **お休み申告**：`/oyasumi riyuu:帰省 hi:10/15-10/17` → その日は判定されず、連続達成も途切れない。\n"
                "📝 **自分だけの項目**：`/kojin add namae:薬を飲む` → ☀️起床の返事に出るチェックリストか、各パネルの 📝 ボタンで毎日チェック。未チェックは判定対象。\n"
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
        + (f"\n🔒 管理用：{botlog.mention}（エラー通知・自動メンテ・週1バックアップ。管理者だけに見えます）" if botlog else "")
        + ("\n⚠️ " + "\n⚠️ ".join(dict.fromkeys(SETUP_ERRORS)) if SETUP_ERRORS else ""), ephemeral=True)

async def nakama_of(uid, wake_deadline):
    if not wake_deadline:
        return []
    async with db.execute("SELECT name FROM users WHERE wake_deadline=? AND id<>? ORDER BY name", (wake_deadline, str(uid))) as c:
        return [r["name"] for r in await c.fetchall()]

@bot.tree.command(name="saitei", description="自分の「最低限」を設定する（指定した項目だけ更新）")
@app_commands.describe(kishou="起床の締切 例 7:00（「なし」で解除）", suimin="最低睡眠時間(h) 例 6（0で解除）",
                       nyuyoku="毎日入浴する", shokuji="1日の最低食事回数 1〜3（0で解除）",
                       rajio="毎朝のラジオ体操に参加する", kyujitsu="土日は起床締切を何時間遅らせるか 例 2（0で解除）",
                       hamigaki="1日の最低歯磨き回数 例 2（0で解除）",
                       benkyou="毎日みんなで暗記！！で勉強する（1問でも解けばOK。Falseで解除）",
                       ryouri="料理：何日に1回 例 1=毎日（0で解除）", souji="掃除：何日に1回 例 7", sara="皿洗い：何日に1回 例 1",
                       sentaku="洗濯：何日に1回 例 3（5工程のどれかでOK）", gomi="ごみ捨て：何日に1回 例 3")
async def saitei_command(interaction, kishou: str = None, suimin: float = None, nyuyoku: bool = None,
                         shokuji: int = None, rajio: bool = None, kyujitsu: float = None, hamigaki: int = None,
                         benkyou: bool = None,
                         ryouri: int = None, souji: int = None, sara: int = None, sentaku: int = None, gomi: int = None):
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
    if benkyou is not None:
        await db.execute("UPDATE users SET benkyou_min=?, benkyou_set_day=? WHERE id=?", (1 if benkyou else 0, today, str(user.id)))
    if shokuji is not None:
        await db.execute("UPDATE users SET meals_min=?, meals_set_day=? WHERE id=?", (min(3, shokuji) if shokuji > 0 else None, today, str(user.id)))
    kaji_changed = False
    for col, v in (("kaji_cook", ryouri), ("kaji_clean", souji), ("kaji_dish", sara), ("kaji_wash", sentaku), ("kaji_trash", gomi)):
        if v is not None:
            await db.execute(f"UPDATE users SET {col}=? WHERE id=?", (max(0, min(30, v)), str(user.id)))
            kaji_changed = True
    if kaji_changed:
        await db.execute("UPDATE users SET kaji_since=? WHERE id=?", (today, str(user.id)))
    await db.commit()
    u = await get_user(user.id)
    mates = await nakama_of(user.id, u["wake_deadline"])
    mate_txt = ""
    if u["wake_deadline"]:
        mate_txt = f"\n\n👥 同じ {u['wake_deadline']} 起床の仲間：" + ("、".join(mates) if mates else "まだいない（最初の一人！）")
    changed_any = any(v is not None for v in (kishou, suimin, nyuyoku, shokuji, rajio, kyujitsu, hamigaki, benkyou, ryouri, souji, sara, sentaku, gomi))
    grace = "\n\n🍀 今日設定・変更した項目は、**明日から**判定に入ります（初日から叱られない仕様）。" if changed_any else ""
    await interaction.response.send_message(f"🛠 **{user.display_name} の最低限**\n{settings_text(u)}{mate_txt}{grace}", ephemeral=True)
    if changed_any:
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
        "🌙 睡眠：" + (fmt_hours(sum(float(x["note"] or 0) for x in s)) if s else "未記録"),
        "🍚 食事：" + ("、".join(f"{m['sub']}" + (f"({m['note']})" if m["note"] else "") for m in meals) if meals else "未報告")
        + (lambda n: f"　🍳 朝ごはん{n}日連続" if n >= 2 else "")(await asagohan_streak(user.id, day)),
        "🧹 家事：" + ("、".join(CHORE_LABEL[c["sub"]] for c in chores) if chores else "なし"),
        "🛁 入浴：" + ((f"済（{len(bath)}回）" if len(bath) >= 2 else "済") if bath else "未報告"),
        "🪥 歯磨き：" + (f"{len(await events_on(user.id, day, 'teeth'))} 回"),
        "😴 二度寝：" + (lambda k: f"{k} 回（本日 {k + 1} 度寝）" if k else "なし（えらい）")(len(await events_on(user.id, day, "nizone"))),
        "🏃 ラジオ体操：" + ("参加" if radio else "—"),
        "🌟 今日のえらい：" + str(len(await db.execute_fetchall("SELECT 1 FROM efforts WHERE user_id=? AND day=?", (str(user.id), day)))) + " 件",
    ]
    sts = await kaji_status(await get_user(user.id), day)
    if sts:
        today.append("🧹 家事の期限：" + "／".join(f"{st['emoji']}{st['label']}" + ("＝今日やる！" if st["due"] else f" あと{st['n'] - st['gap']}日") for st in sts))
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
    async with db.execute("SELECT COUNT(*) AS n FROM night_shifts WHERE user_id=? AND day BETWEEN ? AND ?", (str(user.id), d1, d2)) as c:
        yakin_n = (await c.fetchone())["n"]
    if yakin_n:
        week.append(f"🏭夜勤 {yakin_n} 回")
    async with db.execute("SELECT COALESCE(SUM(end_ts-start_ts),0) AS s FROM work_sessions "
                          "WHERE user_id=? AND day BETWEEN ? AND ? AND end_ts IS NOT NULL", (str(user.id), d1, d2)) as c:
        work_s = (await c.fetchone())["s"]
    if work_s:
        week.append(f"🖥作業 {fmt_work(work_s)}")
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
        cmd = getattr(interaction.command, "qualified_name", "?")
        await bot_alert(f"⚠️ コマンド `/{cmd}` でエラー（{interaction.user.display_name}）\n-# {_short_tb(error)}",
                        key=f"cmd:{cmd}:{type(error).__name__}", cooldown_h=1)
    try:
        if interaction.response.is_done():
            await interaction.followup.send(msg, ephemeral=True)
        else:
            await interaction.response.send_message(msg, ephemeral=True)
    except Exception:
        pass

def _short_tb(error):
    """例外を「どのファイルの何行目で何が」の短い1行に（#bot管理🔒用）"""
    import traceback
    err = getattr(error, "original", error)
    frames = traceback.extract_tb(err.__traceback__) if err.__traceback__ else []
    where = next((f"bot.py:{f.lineno} {f.name}" for f in reversed(frames) if f.filename.endswith("bot.py")), "")
    return f"{type(err).__name__}: {str(err)[:200]}" + (f"（{where}）" if where else "")

async def _view_on_error(self, interaction, error, item):
    """ボタン・セレクト操作で起きたエラーも #bot管理🔒 に（discord.pyの既定はログに出すだけ）"""
    print(f"view error: {error!r}", flush=True)
    label = getattr(item, "custom_id", None) or getattr(item, "label", None) or type(item).__name__
    await bot_alert(f"⚠️ ボタン/選択 `{label}` でエラー（{interaction.user.display_name}）\n-# {_short_tb(error)}",
                    key=f"view:{type(item).__name__}:{type(error).__name__}", cooldown_h=1)
    try:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ うまく処理できませんでした。少し待ってからもう一度試してね。", ephemeral=True)
    except Exception:
        pass

discord.ui.View.on_error = _view_on_error
discord.ui.Modal.on_error = lambda self, interaction, error: _view_on_error(self, interaction, error, self)

# --- 起動 ---
if __name__ == "__main__":
    if TOKEN:
        bot.run(TOKEN)
    else:
        print("❌ 環境変数 DISCORD_BOT_TOKEN が設定されていません。.env を確認してください。", flush=True)
