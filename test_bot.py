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
check("コマンド一覧", sorted(c.name for c in B.bot.tree.get_commands()), ["hantei", "help", "jikanwari", "jikoshokai", "kadai", "kaji", "kiroku", "kojin", "nakama", "oyasumi", "rajio", "saitei", "setup", "tsushinbo"])

class M:  # メンバー擬似
    def __init__(s, i, n): s.id, s.display_name = i, n

async def _fake_async(v):
    return v

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

    # ストリーク（連続達成）
    for dd, ok_ in (("2026-08-01", 1), ("2026-08-02", 1), ("2026-08-03", 0), ("2026-08-04", 1), ("2026-08-05", 1)):
        await B.db.execute("INSERT INTO daily_results(day,user_id,achieved,misses) VALUES(?,?,?,?)", (dd, "7", ok_, "" if ok_ else "☀️ 寝坊"))
    await B.db.commit()
    check("連続達成 8/5時点=2", await B.streak_of(7, "2026-08-05"), 2)
    check("連続達成 8/3で途切れ=0", await B.streak_of(7, "2026-08-03"), 0)
    check("連続達成 8/2時点=2", await B.streak_of(7, "2026-08-02"), 2)
    check("記録の無い日はカウントしない", await B.streak_of(7, "2026-08-07"), 0)
    check("マイルストーンに7", 7 in B.MILESTONES, True)

    # 週次通信簿（偽チャンネルに投稿させて内容を確認）
    sent = []
    class FakeCh:
        async def send(self, content=None, **kw):
            sent.append((content, kw.get("embed")))
    orig_get_ch = B.get_ch
    async def fake_get_ch(key):
        return FakeCh() if key in ("tsushinbo", "kora") else None
    B.get_ch = fake_get_ch
    orig_now = B.now_jst
    B.now_jst = lambda: datetime(2026, 8, 9, 23, 5, tzinfo=JST)   # 日曜
    try:
        for uid, nm in ((11, "はやおき"), (12, "ねぼう")):
            await B.ensure_user(M(uid, nm))
        for dd in ("2026-08-03", "2026-08-04", "2026-08-05"):
            await B.db.execute("INSERT OR REPLACE INTO daily_results(day,user_id,achieved,misses) VALUES(?,?,1,'')", (dd, "11"))
            await B.db.execute("INSERT OR REPLACE INTO daily_results(day,user_id,achieved,misses) VALUES(?,?,0,'☀️ 寝坊 07:00 まで → 09:30')", (dd, "12"))
            await B.add_event(11, "wake", ts_dt=datetime.fromisoformat(dd).replace(hour=6, minute=30, tzinfo=JST))
            await B.add_event(12, "wake", ts_dt=datetime.fromisoformat(dd).replace(hour=9, minute=30, tzinfo=JST))
            await B.add_event(11, "sleep", note="7.5", ts_dt=datetime.fromisoformat(dd).replace(hour=6, minute=30, tzinfo=JST))
            await B.add_event(12, "chore", "cook", ts_dt=datetime.fromisoformat(dd).replace(hour=12, tzinfo=JST))
        await B.db.commit()
        res = await B.weekly_summary(None, manual=True)
        check("通信簿 投稿された", len(sent), 1)
        emb = sent[0][1]
        check("通信簿 embed あり", emb is not None, True)
        fields = {f.name: f.value for f in emb.fields}
        rank = fields.get("🏆 最低限 達成率ランキング", "")
        check("達成率1位は はやおき", rank.startswith("🥇 **はやおき**"), True)
        check("ねぼう は 0/3", "**ねぼう**　達成 0/3日（0%）" in rank, True)
        aw = fields.get("🎖 今週の各賞", "")
        check("皆勤賞 はやおき", "👑 皆勤賞：**はやおき**" in aw, True)
        check("早起き賞 6:30", "🌅 早起き賞：**はやおき**（平均 6:30）" in aw, True)
        check("寝坊賞 ねぼう 3回", "🐷 寝坊賞：**ねぼう**（3回）" in aw, True)
        kaji_line = next((l for l in aw.split("\n") if l.startswith("🧹 家事賞")), "")
        check("家事賞 同点は全員（ねぼう含む・3回）", "**ねぼう**" in kaji_line and kaji_line.endswith("（3回）"), True)
        check("ぐっすり賞 7.5h", "🛌 ぐっすり賞：**はやおき**（平均 7.5h）" in aw, True)
    finally:
        B.get_ch = orig_get_ch
        B.now_jst = orig_now

    # お休み申告でストリークが途切れない
    await B.db.execute("INSERT INTO off_days(day,user_id,reason) VALUES('2026-08-03','7','帰省')"); await B.db.commit()
    check("お休みの日を飛ばして連続=4", await B.streak_of(7, "2026-08-05"), 4)
    check("お休み当日から見ても継続", await B.streak_of(7, "2026-08-03"), 2)
    # 休日設定
    u7 = await B.get_user(7) or (await B.ensure_user(M(7, "G")) or await B.get_user(7))
    await B.db.execute("UPDATE users SET wake_deadline='07:00', holiday_shift=120 WHERE id='7'"); await B.db.commit()
    u7 = await B.get_user(7)
    check("平日の締切 07:00", B.effective_deadline(u7, datetime(2026, 8, 5, tzinfo=JST)), "07:00")
    check("土曜の締切 09:00", B.effective_deadline(u7, datetime(2026, 8, 8, tzinfo=JST)), "09:00")
    # 日付指定
    nw = datetime(2026, 8, 5, 12, 0, tzinfo=JST)
    check("日付: 省略=今日", B.parse_day_spec("", nw), ["2026-08-05"])
    check("日付: 明日", B.parse_day_spec("明日", nw), ["2026-08-06"])
    check("日付: 範囲", B.parse_day_spec("10/15-10/17", nw), ["2026-10-15", "2026-10-16", "2026-10-17"])
    check("日付: 不正", B.parse_day_spec("あさって", nw), None)
    # 個人項目が未チェックなら判定で未達
    await B.db.execute("INSERT INTO custom_items(user_id,name,created_at) VALUES('7','薬を飲む',0)"); await B.db.commit()
    m7 = await B.build_misses(u7, "2026-08-05", d1, d2, False)
    check("個人項目 未チェックが未達に", "📝 薬を飲む 未チェック" in m7, True)
    async with B.db.execute("SELECT id FROM custom_items WHERE user_id='7'") as c:
        iid = (await c.fetchone())["id"]
    await B.db.execute("INSERT INTO custom_checks(day,user_id,item_id) VALUES('2026-08-05','7',?)", (iid,)); await B.db.commit()
    m7 = await B.build_misses(u7, "2026-08-05", d1, d2, False)
    check("チェック済みなら未達にならない", any("薬を飲む" in x for x in m7), False)
    # 起床ダイジェスト（水曜3限の科目）
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) VALUES('t_test1','テスト科目','てすと','','L0011','工','','',1,'水3','',1)")
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('7','t_test1')"); await B.db.commit()
    dg = await B.today_digest(7, datetime(2026, 8, 5, 7, 0, tzinfo=JST))
    check("ダイジェストに水曜3限", "3限(13:00) テスト科目 L0011" in dg, True)
    check("月曜は出ない", "テスト科目" in await B.today_digest(7, datetime(2026, 8, 3, 7, 0, tzinfo=JST)), False)
    # 月間表彰（偽チャンネル）
    sent2 = []
    class FakeCh2:
        async def send(self, content=None, **kw):
            sent2.append((content, kw.get("embed"), kw.get("file")))
    B.get_ch = lambda key: _fake_async(FakeCh2() if key in ("tsushinbo", "kora") else None)
    B.now_jst = lambda: datetime(2026, 8, 31, 23, 5, tzinfo=JST)
    try:
        res2 = await B.monthly_summary(None, manual=True)
        check("月間表彰 投稿", len(sent2), 1)
        check("月間MVP 表示", (sent2[0][1].description or "").startswith("👑 **月間MVP："), True)
        B.now_jst = lambda: datetime(2026, 8, 9, 23, 5, tzinfo=JST)
        sent2.clear()
        await B.weekly_summary(None, manual=True)
        check("週次にグラフ添付（matplotlibあれば）", sent2[0][2] is not None or B.render_week_chart("2026-08-03", []) is None, True)
    finally:
        B.get_ch = orig_get_ch
        B.now_jst = orig_now

    # チュートリアル
    embs = B.tutorial_embeds()
    check("チュートリアル 6枚", len(embs), 6)
    check("各embedがDiscord上限内", all(len(e) <= 6000 and all(len(f.value) <= 1024 for f in e.fields) for e in embs), True)
    check("判定時刻が埋め込まれる", f"{B.JUDGE_HOUR}:00" in (embs[0].description or ""), True)

    # ロール名の照合（絵文字の異体字セレクタや空白を無視）とリアクション絵文字の一意性
    class R:
        def __init__(s, n): s.name = n
    class G:
        roles = [R("🛏\ufe0f寮"), R("1年"), R("🏭 工学部")]
    check("find_role: VS16違いでも一致", B.find_role(G(), "🛏寮").name, "🛏\ufe0f寮")
    check("find_role: 空白違いでも一致", B.find_role(G(), "🏭工学部").name, "🏭 工学部")
    check("find_role: 無ければNone", B.find_role(G(), "2年"), None)
    emojis = [B._norm_role(e) for items in B.ROLE_GROUPS.values() for e, _, _ in items[1]]
    check("リアクション絵文字が重複しない", len(emojis), len(set(emojis)))
    check("ロールは9個", sum(len(items[1]) for items in B.ROLE_GROUPS.values()), 9)
    # 自己紹介カード
    class Av:
        url = "https://example.invalid/a.png"
    class Mem:
        id, display_name, display_avatar, roles = 7, "G", Av(), [R("🏭工学部"), R("その他")]
    await B.db.execute("INSERT OR REPLACE INTO intros(user_id,f1,f2,f3,f4,f5,updated_at) VALUES('7','ひさ／工学部 2年','夜型','うどん','ガツンと','1限に行く',0)"); await B.db.commit()
    async with B.db.execute("SELECT * FROM intros WHERE user_id='7'") as c:
        irow = await c.fetchone()
    card = await B.intro_card(Mem(), irow)
    check("カード 5項目", len(card.fields), 5)
    check("カード footer に起床目標とロール", "起床目標 07:00" in card.footer.text and "🏭工学部" in card.footer.text and "その他" not in card.footer.text, True)
    check("テンプレ embed 上限内", len(B.intro_template_embed()) <= 6000, True)

    # 家事の種類ごとの頻度
    await B.ensure_user(M(8, "H"))
    await B.db.execute("UPDATE users SET kaji_cook=1, kaji_wash=3, kaji_since='2026-08-01' WHERE id='8'")
    for dd in ("2026-08-01", "2026-08-02"):
        await B.add_event(8, "chore", "cook", ts_dt=datetime.fromisoformat(dd).replace(hour=12, tzinfo=JST))
    await B.add_event(8, "chore", "hang", ts_dt=datetime(2026, 8, 1, 15, tzinfo=JST))
    await B.db.commit()
    u8 = await B.get_user(8)
    st = {x["label"]: x for x in await B.kaji_status(u8, "2026-08-02")}
    check("料理 毎日: 今日やった→OK", st["料理"]["due"], False)
    check("洗濯 3日に1回: 8/1にやって8/2→まだ", st["洗濯"]["due"], False)
    st4 = {x["label"]: x for x in await B.kaji_status(u8, "2026-08-04")}
    check("洗濯 3日に1回: 8/4で3日空き→今日やる", (st4["洗濯"]["gap"], st4["洗濯"]["due"]), (3, True))
    check("料理 毎日: 8/4やってない→未達", st4["料理"]["due"], True)
    m8 = await B.build_misses(u8, "2026-08-04", d1, d2, False)
    check("判定文: 洗濯3日やってない", "🧺 洗濯 3日やってない（3日に1回）" in m8, True)
    check("判定文: 料理 今日やってない", "🍳 料理 今日やってない（毎日）" in m8, True)
    check("未記録は設定日から数える(掃除7日)", (await B.db.execute("UPDATE users SET kaji_clean=7 WHERE id='8'")) is not None and
          {x["label"]: x for x in await B.kaji_status(await B.get_user(8), "2026-08-07")}["掃除"]["due"], False)
    check("設定日から7日で掃除が未達", {x["label"]: x for x in await B.kaji_status(await B.get_user(8), "2026-08-08")}["掃除"]["due"], True)
    check("種類設定だけでも判定対象", B.has_any_setting(await B.get_user(8)), True)
    check("設定表示", "🧺 洗濯 3日に1回" in B.settings_text(await B.get_user(8)), True)

    check("#ロール🏷 チャンネル定義", B.CH["roles"][0], "ロール🏷")
    check("自己紹介チャンネルの説明にロールが無い", "ロール" in B.CH["jikoshokai"][1], False)

    # meta の upsert
    await B.meta_set("last_judge_day", "2026-08-05"); await B.meta_set("last_judge_day", "2026-08-06")
    check("meta 上書き", await B.meta_get("last_judge_day"), "2026-08-06")
    await B.db.close()

asyncio.run(main())
print("\n=== " + ("ALL PASS" if ok else "FAILED") + " ===")
sys.exit(0 if ok else 1)
