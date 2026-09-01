"""bot.py の判定ロジック・DB・パネル制約のテスト。 実行: .venv/Scripts/python test_bot.py"""
import os, sys, asyncio
from datetime import datetime
os.environ["DB_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_seikatsu.db")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bot as B

ok = True
def check(label, got, want):
    global ok
    if got != want: ok = False
    print(f"{'OK ' if got == want else 'NG '} {label}: got={got!r} want={want!r}")

JST = B.JST
# ---- 純粋関数 ----
check("parse 7:00", B.parse_hhmm("7:00"), "07:00")
check("parse 07:30", B.parse_hhmm("07:30"), "07:30")
check("parse 7時30分", B.parse_hhmm("7時30分"), "07:30")
check("parse 2330", B.parse_hhmm("2330"), "23:30")
check("parse 24:00 は無効", B.parse_hhmm("24:00"), None)
check("parse abc は無効", B.parse_hhmm("abc"), None)
wake = datetime(2026, 8, 5, 7, 10, tzinfo=JST)
check("就寝23:30→前日", B.bed_dt_from_hhmm("23:30", wake), datetime(2026, 8, 4, 23, 30, tzinfo=JST))
check("就寝01:00→当日", B.bed_dt_from_hhmm("01:00", wake), datetime(2026, 8, 5, 1, 0, tzinfo=JST))
check("週範囲(水曜→月〜日)", B.week_range(datetime(2026, 8, 5, tzinfo=JST)), ("2026-08-03", "2026-08-09"))
check("食事推定 8:00", B.infer_meal_sub(datetime(2026,8,5,8,0,tzinfo=JST)), "朝")
check("食事推定 12:30", B.infer_meal_sub(datetime(2026,8,5,12,30,tzinfo=JST)), "昼")
check("食事推定 16:00", B.infer_meal_sub(datetime(2026,8,5,16,0,tzinfo=JST)), "間食")
check("食事推定 19:00", B.infer_meal_sub(datetime(2026,8,5,19,0,tzinfo=JST)), "夜")

# ---- 永続ビュー（custom_id 必須制約）とコマンド登録 ----
for V in (B.WakeView, B.MealView, B.ChoreView, B.BathView):
    B.bot.add_view(V())   # custom_id が欠けていればここで例外
check("永続ビュー4種 登録OK", True, True)
check("コマンド一覧", sorted(c.name for c in B.bot.tree.get_commands()), ["hantei", "jikanwari", "kadai", "kiroku", "nakama", "rajio", "saitei", "setup"])

class M:  # メンバー擬似
    def __init__(s, i, n): s.id, s.display_name = i, n

async def main():
    await B.db_init()
    day = "2026-08-05"                      # 水曜
    sun = "2026-08-09"                      # 日曜
    d1, d2 = "2026-08-03", "2026-08-09"
    t = lambda h, m=0, dd=5: datetime(2026, 8, dd, h, m, tzinfo=JST)

    # A: 7:00起床/睡眠6h/入浴毎日/食事2回/家事週3 → 全部守る
    a = M(1, "A")
    await B.ensure_user(a)
    await B.db.execute("UPDATE users SET wake_deadline='07:00', sleep_min=6, bath_daily=1, meals_min=2, chores_week=3 WHERE id='1'")
    await B.add_event(1, "bed", ts_dt=t(23, 30, 4))
    await B.add_event(1, "wake", ts_dt=t(6, 50))
    await B.add_event(1, "sleep", note="7.33", ts_dt=t(6, 50))
    await B.add_event(1, "bath", ts_dt=t(21))
    await B.add_event(1, "meal", "朝", ts_dt=t(7)); await B.add_event(1, "meal", "夜", ts_dt=t(19)); await B.add_event(1, "meal", "間食", ts_dt=t(15))
    for dd in (3, 4, 5): await B.add_event(1, "chore", "cook", ts_dt=t(12, 0, dd))
    ua = await B.get_user(1)
    check("A 平日：未達なし", await B.build_misses(ua, day, d1, d2, False), [])
    check("A 日曜：家事3/3で未達なし", await B.build_misses(ua, day, d1, d2, True), [])

    # B: 寝坊＋睡眠不足＋入浴なし＋食事1回、家事は週1のみ
    b = M(2, "B")
    await B.ensure_user(b)
    await B.db.execute("UPDATE users SET wake_deadline='07:00', sleep_min=6, bath_daily=1, meals_min=2, chores_week=3 WHERE id='2'")
    await B.add_event(2, "wake", ts_dt=t(9, 42))
    await B.add_event(2, "sleep", note="4.50", ts_dt=t(9, 42))
    await B.add_event(2, "meal", "昼", ts_dt=t(12)); await B.add_event(2, "meal", "昼", ts_dt=t(13))  # 同じ種類2回は1回扱い
    await B.add_event(2, "chore", "clean", ts_dt=t(10, 0, 4))
    ub = await B.get_user(2)
    m = await B.build_misses(ub, day, d1, d2, False)
    check("B 平日：未達4件（家事は日曜まで判定しない）", len(m), 4)
    check("B 寝坊メッセージ", m[0], "☀️ 寝坊 07:00 まで → 09:42")
    check("B 睡眠不足", m[1], "🌙 睡眠不足 4.5h（最低 6.0h）")
    check("B 入浴", m[2], "🛁 入浴 未報告")
    check("B 食事(昼2回は1種類)", m[3], "🍚 食事 1/2 回")
    m_sun = await B.build_misses(ub, day, d1, d2, True)
    check("B 日曜：家事 1/3 が加わる", m_sun[-1], "🧹 家事 今週 1/3 回")

    # C: 何も報告しない人（設定あり）
    c = M(3, "C")
    await B.ensure_user(c)
    await B.db.execute("UPDATE users SET wake_deadline='08:00', sleep_min=6 WHERE id='3'")
    uc = await B.get_user(3)
    check("C 未報告2件", await B.build_misses(uc, day, d1, d2, False), ["☀️ 起床 未報告（08:00 まで）", "🌙 睡眠時間 未報告"])

    # D: 設定なし → 判定対象外
    d = M(4, "D"); await B.ensure_user(d)
    check("D 設定なしは対象外", B.has_any_setting(await B.get_user(4)), False)

    # 就寝の自動検出：20時間以内の直近
    await B.add_event(5, "bed", ts_dt=t(0, 30))
    check("直近の就寝を検出", await B.last_bed_within(5, t(7, 0)), t(0, 30))
    check("30時間前の就寝は無視", await B.last_bed_within(5, t(7, 0, 7)), None)

    # 同時刻の仲間
    check("Aの7:00仲間はB", await B.nakama_of(1, "07:00"), ["B"])
    check("Cの8:00仲間はいない", await B.nakama_of(3, "08:00"), [])

    # ラジオ体操
    e = M(6, "E"); await B.ensure_user(e)
    await B.db.execute("UPDATE users SET radio_daily=1 WHERE id='6'")
    ue = await B.get_user(6)
    check("E ラジオ体操のみ設定→判定対象", B.has_any_setting(ue), True)
    check("E 未参加", await B.build_misses(ue, day, d1, d2, False), ["🏃 ラジオ体操 未参加"])
    await B.add_event(6, "radio", ts_dt=t(6, 35))
    check("E 参加済み→未達なし", await B.build_misses(ue, day, d1, d2, False), [])
    check("設定表示にラジオ体操", "🏃 ラジオ体操 毎日" in B.settings_text(ue), True)

    # 科目マスタ・検索・履修・課題
    async with B.db.execute("SELECT COUNT(*) AS n FROM courses") as c:
        ncourses = (await c.fetchone())["n"]
    check("科目マスタ読込 500件以上", ncourses > 500, True)
    hits = await B.search_courses("微分積分")
    check("検索: 微分積分 がヒット", any("微分積分" in r["name"] for r in hits), True)
    hits2 = await B.search_courses("english discussion")
    check("検索: 大文字小文字・空白を無視", len(hits2) > 5, True)
    cc = await B.ensure_custom_course("ゼミ（山田研）")
    check("自由入力科目を作成", cc["custom"], 1)
    check("同名の自由入力は再利用", (await B.ensure_custom_course("ゼミ (山田研)"))["code"], cc["code"])
    du = B.parse_due("10/15")
    check("期限 10/15 → 23:59", (du.month, du.day, du.hour, du.minute), (10, 15, 23, 59))
    du2 = B.parse_due("10月15日 17:00")
    check("期限 10月15日 17:00", (du2.month, du2.day, du2.hour, du2.minute), (10, 15, 17, 0))
    check("期限 不正", B.parse_due("あした"), None)
    code = hits[0]["code"]
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('1',?)", (code,))
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('2',?)", (code,))
    await B.db.commit()
    check("同じ科目の履修者", sorted(await B.takers_of(code)), ["1", "2"])
    from datetime import timedelta as _td
    due3 = B.now_jst() + _td(days=3)
    cur = await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES(?,?,?,?,?)", (code, "レポート", int(due3.timestamp()), "1", 0))
    aid = cur.lastrowid; await B.db.commit()
    check("残り日数 3", B.days_left(due3), 3)
    await B.db.execute("INSERT INTO assignment_done(assignment_id,user_id) VALUES(?, '2')", (aid,)); await B.db.commit()
    check("完了集合", await B.done_set(aid), {"2"})

    # meta の upsert
    await B.meta_set("last_judge_day", "2026-08-05"); await B.meta_set("last_judge_day", "2026-08-06")
    check("meta 上書き", await B.meta_get("last_judge_day"), "2026-08-06")
    await B.db.close()

asyncio.run(main())
print("\n=== " + ("ALL PASS" if ok else "FAILED") + " ===")
sys.exit(0 if ok else 1)
