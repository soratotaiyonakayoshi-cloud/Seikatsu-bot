"""bot.py の判定ロジック・DB・パネル制約のテスト。 実行: .venv/Scripts/python test_bot.py"""
import os, sys, asyncio
from datetime import datetime, date
os.environ["DB_PATH"] = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_seikatsu.db")
if os.path.exists(os.environ["DB_PATH"]):
    os.remove(os.environ["DB_PATH"])   # 前回中断の残骸があると集計テストが狂う
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
check("空きコマ: 単発パース", B.parse_slots_input("金3"), ["金3"])
check("空きコマ: 複数＋全角＋重複", B.parse_slots_input("金３、金4 金4"), ["金3", "金4"])
check("空きコマ: 不正は None", B.parse_slots_input("金7"), None)
check("定例: 曜日抽出 金1,金2→金", B.course_day_chars("金1,金2"), ["金"])
check("定例: 曜日抽出 月3,木2→月木", B.course_day_chars("月3,木2"), ["月", "木"])
check("定例: 曜日抽出 空→なし", B.course_day_chars(""), [])
check("定例: 最初の時限 金1,金2→1", B.course_first_period("金1,金2", "金"), 1)
check("定例: 最初の時限 無い曜日→None", B.course_first_period("金1,金2", "月"), None)
check("定例曜日: 単発", B.parse_day_chars_input("日"), ["日"])
check("定例曜日: 複数・週順に整列", B.parse_day_chars_input("木、月"), ["月", "木"])
check("定例曜日: 不正は None", B.parse_day_chars_input("丑"), None)
check("定例曜日: 連結表記 土日", B.parse_day_chars_input("土日"), ["土", "日"])
check("設定パネル: 🛠ボタンあり", any(getattr(i, "custom_id", "") == "sk_saitei" for i in B.SetteiView().children), True)
check("設定パネル: 項目セレクトは14項目", len(B.SaiteiSelect().options), 14)

# ---- 永続ビュー（custom_id 必須制約）とコマンド登録 ----
for V in (B.WakeView, B.MealView, B.ChoreView, B.BathView, B.KadaiPanelView, B.GoodsPanelView, B.MusicPanelView, B.YoteiPanelView):
    B.bot.add_view(V())   # custom_id が欠けていればここで例外
check("永続ビュー8種 登録OK", True, True)
check("予定パネル: 6ボタン", {i.custom_id for i in B.YoteiPanelView().children},
      {"sk_univ", "sk_mine", "sk_exam", "sk_timeline", "sk_cal0", "sk_cal1"})
check("予定パネル: VIEW_FACTORYとチャンネル定義", (B.VIEW_FACTORY.get("yotei") is B.YoteiPanelView, B.CH["yotei"][0]), (True, "予定🗓"))
check("音楽パネルは12ボタン", {i.custom_id for i in B.MusicPanelView().children},
      {"sk_mu_play", "sk_mu_bgm", "sk_mu_amb", "sk_mu_join", "sk_mu_pause", "sk_mu_skip", "sk_mu_queue", "sk_mu_stop",
       "sk_mu_lists", "sk_mu_recs", "sk_mu_lyrics", "sk_mu_karaoke"})
check("LRC: 基本パース", B.parse_lrc("[00:12.50]こんにちは\n[01:02]さようなら"), [(12.5, "こんにちは"), (62.0, "さようなら")])
check("LRC: 1行複数タイムスタンプ＋時刻順", B.parse_lrc("[01:00][00:10]サビ\n[00:05]Aメロ"), [(5.0, "Aメロ"), (10.0, "サビ"), (60.0, "サビ")])
check("LRC: 空行は間奏として残る", B.parse_lrc("[00:01]歌\n[00:30]\n[00:40]歌2")[1], (30.0, ""))
check("LRC: タイムスタンプなしは空", B.parse_lrc("ただの歌詞\n2行目"), [])
check("LRC: 打ち込みの書き出し形式", B.lrc_format([(5.25, "あ"), (65.5, "い")]), "[00:05.25]あ\n[01:05.50]い")
check("LRC: 書き出し→パースの往復一致", B.parse_lrc(B.lrc_format([(12.3, "歌"), (72.0, "詞")])), [(12.3, "歌"), (72.0, "詞")])
_kl = [(1.0, "1行目"), (5.0, "2行目"), (9.0, ""), (13.0, "4行目")]
check("歌詞窓: 開始前は次の2行", B.karaoke_board(_kl, None), "**▶ ♪〜**\n-# 1行目\n-# 2行目")
check("歌詞窓: 現在行が太字・前後に小さい行", B.karaoke_board(_kl, 1), "-# 1行目\n**▶ 2行目**\n-# ♪〜\n-# 4行目")
check("歌詞窓: 最終行", B.karaoke_board(_kl, 3), "-# ♪〜\n**▶ 4行目**")
check("歌詞: タイトル掃除（MV括弧・公式語）", B.clean_track_title("YOASOBI「アイドル」Official Music Video"), "YOASOBI アイドル")
check("歌詞: 【MV】は落とし曲名括弧は残す", B.clean_track_title("【MV】米津玄師 - Lemon（弾き語り）"), "米津玄師 - Lemon 弾き語り")
check("歌詞: 全角ノイズ括弧", B.clean_track_title("Ado 唱【公式】"), "Ado 唱")
check("歌詞: 照合キー正規化", B.lyrics_norm("YOASOBI「アイドル」Official MV"), "yoasobiアイドル")
check("曲リスト入力: 箇条書き剥がしと空行除去", B.parse_track_lines("・夜に駆ける\n\n- https://youtu.be/x\n米津玄師 Lemon"),
      ["夜に駆ける", "https://youtu.be/x", "米津玄師 Lemon"])
check("おすすめ: 1人20曲まで", B.REC_MAX, 20)
check("おすすめ: 26曲で2ページ表記", "2ページ" in B.rec_head([{}] * 26, 0), True)
check("おすすめ: 25曲以下はページ表記なし", "ページ" in B.rec_head([{}] * 25, 0), False)
check("音楽パネルがVIEW_FACTORYに", B.VIEW_FACTORY.get("worklog") is B.MusicPanelView, True)
check("ごはんパネルに🧊 2ボタン", {"sk_fridge_add", "sk_fridge_list"} <= {i.custom_id for i in B.MealView().children}, True)
check("🧊は2段目・ごはんは1段目", sorted({i.row for i in B.MealView().children if i.custom_id.startswith("sk_fridge")}) == [1]
      and sorted({i.row for i in B.MealView().children if i.custom_id.startswith("sk_meal")}) == [0], True)
check("起床パネルに📅今日の予定", any(getattr(i, "custom_id", "") == "sk_today" for i in B.WakeView().children), True)
check("起床パネルに🗓明日・📆今後", {"sk_tomorrow", "sk_upcoming"} <= {getattr(i, "custom_id", "") for i in B.WakeView().children}, True)
check("起床パネルの行割り: 明日は1段目・今後は2段目",
      ([i.row for i in B.WakeView().children if getattr(i, "custom_id", "") == "sk_tomorrow"],
       [i.row for i in B.WakeView().children if getattr(i, "custom_id", "") == "sk_upcoming"]), ([0], [1]))
check("課題パネルは11ボタン（予定系は#予定へ）", {i.custom_id for i in B.KadaiPanelView().children},
      {"sk_jw_add", "sk_kd_add", "sk_jw_list", "sk_kd_list", "sk_jw_img", "sk_jw_copy", "sk_kesseki", "sk_kd_teiki", "sk_jw_free", "sk_cnote", "sk_newterm"})
check("課題パネルがVIEW_FACTORYに", B.VIEW_FACTORY.get("kadai") is B.KadaiPanelView, True)
check("コマンド一覧", sorted(c.name for c in B.bot.tree.get_commands()), ["BGMに追加", "album", "ambient", "bgm", "erai", "hantei", "help", "jikanwari", "jikoshokai", "join", "kadai", "kanri", "kesseki", "kigen", "kiroku", "kojin", "kyuka", "loop", "nakama", "oyasumi", "pause", "play", "queue", "rajio", "reizouko", "resume", "saitei", "setup", "shiori", "shuffle", "skip", "stop", "suimin", "tips", "tsushinbo", "volume", "watashi", "レシピカードにする"])
check("音楽: URL判定", (B.looks_like_url("https://youtu.be/x"), B.looks_like_url("lofi hiphop")), (True, False))
check("音楽: 音源なしなら空リスト", isinstance(B.bgm_files(), list), True)
check("環境音: ファイル一覧はリスト", isinstance(B.ambient_files(), list), True)
_asrc, _ab, _ao = B.ambient_ffmpeg_args(["/a/rain.mp3"])
check("環境音: 1音源はループ指定のみ", (_asrc, _ab, _ao), ("/a/rain.mp3", "-stream_loop -1", ""))
_asrc3, _ab3, _ao3 = B.ambient_ffmpeg_args(["/a/r.mp3", "/a/f.mp3", "/a/b.mp3"])
check("環境音: 3音源の追加入力はbefore側", _asrc3 == "/a/b.mp3" and "-stream_loop -1 -i /a/r.mp3" in _ab3 and "-i /a/f.mp3" in _ab3 and _ab3.endswith("-stream_loop -1"), True)
check("環境音: optionsに-iを含まずamixミックス", "-i " not in _ao3 and "[0:a][1:a][2:a]amix=inputs=3" in _ao3 and "-map [mix]" in _ao3, True)
check("音楽: VCチャット告知関数あり", callable(getattr(B, "_announce_now_playing", None)), True)
check("Spotify: プレイリストURL判定", B.spotify_url_parts("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc"), ("playlist", "37i9dQZF1DXcBWIGoYBM5M"))
check("Spotify: intl付きトラックURL", B.spotify_url_parts("https://open.spotify.com/intl-ja/track/4cOdK2wGLETKBW3PvgPWqT"), ("track", "4cOdK2wGLETKBW3PvgPWqT"))
check("Spotify: アルバムURL", B.spotify_url_parts("https://open.spotify.com/album/1ATL5GLyefJaxhQzSPVrLX")[0], "album")
check("Spotify: 他のURLはNone", (B.spotify_url_parts("https://youtu.be/x"), B.spotify_url_parts("lofi hiphop")), (None, None))
check("Spotify: 検索語への変換", B._sp_entry({"name": "夜に駆ける", "artists": [{"name": "YOASOBI"}]}), ("YOASOBI 夜に駆ける", "夜に駆ける／YOASOBI"))
check("Spotify: 埋め込みJSONの深掘り", B._find_key_deep({"props": [{"x": 1}, {"trackList": [{"title": "a"}]}]}, "trackList"), [{"title": "a"}])
check("時間表記: 分秒と時分秒", (B.fmt_dur_sec(2219), B.fmt_dur_sec(3723)), ("36:59", "1:02:03"))
check("運用: Cookie切れの判定", B.classify_yt_error("ERROR: Sign in to confirm you're not a bot")[0], "yt_cookie")
check("運用: yt-dlp古いの判定", B.classify_yt_error("ERROR: [youtube] x: No video formats found")[0], "yt_update")
check("運用: 無関係なエラーは通知しない", B.classify_yt_error("HTTP Error 404: Not Found"), None)
try:
    raise ValueError("テスト")
except ValueError as _e:
    check("運用: 例外の短い要約に行番号", "ValueError: テスト" in B._short_tb(_e), True)
check("BGM追加: パス区切りを除去", "/" not in B._safe_bgm_name("../../etc/passwd.mp3") and ".." not in B._safe_bgm_name("..\\..\\x.mp3") and B._safe_bgm_name("うた.mp3") == "うた.mp3", True)
check("BGM: removeサブコマンドあり", sorted(c.name for c in B.bgm_grp.commands), ["list", "remove", "start", "stop"])

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
    check("B 未達4件（週合計の家事判定は廃止）", len(m), 4)
    check("B 寝坊メッセージ", m[0], "☀️ 寝坊 07:00 まで → 09:42")
    check("B 睡眠不足", m[1], "🌙 睡眠不足 4.5h（最低 6.0h）")
    check("B 入浴", m[2], "🛁 入浴 未報告")
    check("B 食事(昼2回は1種類)", m[3], "🍚 食事 1/2 回")

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
    _card_orig = B.render_tsushinbo_card
    B.render_tsushinbo_card = lambda *a, **k: None   # 通信簿テストは従来テキスト経路で検証（カードは単体テストで）
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
    check("日付: 昨日（さかのぼりお休み用）", B.parse_day_spec("昨日", nw), ["2026-08-04"])
    check("日付: おととい", B.parse_day_spec("おととい", nw), ["2026-08-03"])
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
    check("ダイジェストに水曜3限", "3限 13:00　**テスト科目**（L0011）" in dg, True)
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
    check("寮・下宿 に改名済み", any(n == "🛏寮・下宿" for _, items in B.ROLE_GROUPS.values() for _, n, _ in items), True)
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

    # 歯磨き・ごみ捨て
    check("家事ボタンに ごみ捨て", any(k == "trash" for k, _, _, _ in B.CHORES), True)
    check("家事カテゴリに ごみ捨て", any(c == "kaji_trash" for c, _, _, _ in B.KAJI_CATS), True)
    await B.db.execute("UPDATE users SET teeth_min=2 WHERE id='8'"); await B.db.commit()
    await B.add_event(8, "teeth", ts_dt=datetime(2026, 8, 4, 8, tzinfo=JST))
    m8 = await B.build_misses(await B.get_user(8), "2026-08-04", d1, d2, False)
    check("歯磨き 1/2 で未達", "🪥 歯磨き 1/2 回" in m8, True)
    await B.add_event(8, "teeth", ts_dt=datetime(2026, 8, 4, 22, tzinfo=JST))
    m8 = await B.build_misses(await B.get_user(8), "2026-08-04", d1, d2, False)
    check("歯磨き 2/2 で達成", any("歯磨き" in x for x in m8), False)
    check("設定表示に歯磨き", "🪥 歯磨き 1日2回" in B.settings_text(await B.get_user(8)), True)
    await B.db.execute("UPDATE users SET kaji_trash=3, kaji_since='2026-08-01' WHERE id='8'"); await B.db.commit()
    stt = {x["label"]: x for x in await B.kaji_status(await B.get_user(8), "2026-08-04")}
    check("ごみ捨て 3日に1回 未記録→設定日から3日で今日やる", stt["ごみ捨て"]["due"], True)

    # 入浴は1日2回以上でも記録でき、判定は1回以上でOKのまま
    await B.add_event(9, "bath", ts_dt=datetime(2026, 8, 5, 9, tzinfo=JST))
    await B.add_event(9, "bath", ts_dt=datetime(2026, 8, 5, 22, tzinfo=JST))
    await B.ensure_user(M(9, "I"))
    await B.db.execute("UPDATE users SET bath_daily=1 WHERE id='9'"); await B.db.commit()
    m9 = await B.build_misses(await B.get_user(9), "2026-08-05", d1, d2, False)
    check("入浴2回でも未達にならない", any("入浴" in x for x in m9), False)
    check("入浴2回が記録されている", len(await B.events_on(9, "2026-08-05", "bath")), 2)

    # add_event はIDを返す（写真の食事種類の選び直しに使う）
    eid_test = await B.add_event(9, "meal", "昼", note="写真", ts_dt=datetime(2026, 8, 5, 12, tzinfo=JST))
    check("add_event がIDを返す", isinstance(eid_test, int) and eid_test > 0, True)
    await B.db.execute("UPDATE events SET sub='夜' WHERE id=?", (eid_test,)); await B.db.commit()
    async with B.db.execute("SELECT sub FROM events WHERE id=?", (eid_test,)) as c:
        check("食事種類を選び直せる", (await c.fetchone())["sub"], "夜")

    # 今日のえらい（褒め合い）
    cur = await B.db.execute("INSERT INTO efforts(user_id,text,day,ts) VALUES('11','早起きして図書館','2026-08-05',0)")
    eid2 = cur.lastrowid
    await B.db.execute("INSERT INTO effort_praise(effort_id,user_id,day) VALUES(?, '12', '2026-08-05')", (eid2,))
    await B.db.execute("INSERT INTO effort_praise(effort_id,user_id,day) VALUES(?, '7', '2026-08-05')", (eid2,))
    await B.db.commit()
    check("褒めた人の一覧", sorted(await B._praisers(eid2)), ["12", "7"])
    body = B._effort_content("はやおき", "早起きして図書館", ["12", "7"], ["ねぼう", "G"])
    check("投稿本文に件数と名前", "👏 × 2" in body and "ねぼう" in body, True)

    # 称号の段位と飯テロ賞
    check("段位: 2日はなし", B.streak_title_for(2), None)
    check("段位: 3日でかけだし", B.streak_title_for(3), "かけだし文化人")
    check("段位: 10日でそこそこ", B.streak_title_for(10), "そこそこ文化人")
    check("段位: 40日で文句なし", B.streak_title_for(40), "文句なしの文化人")
    mid1 = await B.add_event(11, "meal", "昼", note="写真", ts_dt=datetime(2026, 8, 5, 12, tzinfo=JST))
    mid2 = await B.add_event(12, "meal", "夜", note="写真", ts_dt=datetime(2026, 8, 5, 19, tzinfo=JST))
    for uid in ("7", "9"):
        await B.db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,'2026-08-05')", (mid1, uid))
    await B.db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,'2026-08-05')", (mid2, "7"))
    await B.db.commit()
    names, best = await B.meshitero_winners(d1, d2)
    check("飯テロ賞: 👏2の写真が勝ち", (names, best), (["はやおき"], 2))

    # 宣言の答え合わせ（◯△✕）
    await B.db.execute("INSERT INTO goal_reviews(month,user_id,result) VALUES('2026-09','11','o')")
    await B.db.execute("INSERT INTO goal_reviews(month,user_id,result) VALUES('2026-09','12','x')")
    await B.db.commit()
    ty = await B.goal_review_tally("2026-09")
    check("答え合わせ集計", "◯ 1人（はやおき）" in ty and "✕ 1人（ねぼう）" in ty, True)
    await B.db.execute("INSERT INTO goal_reviews(month,user_id,result) VALUES('2026-09','12','o') "
                       "ON CONFLICT(month,user_id) DO UPDATE SET result=excluded.result")
    await B.db.commit()
    check("押し直しで切替", "◯ 2人" in await B.goal_review_tally("2026-09"), True)

    # 設定初日は判定されない
    await B.ensure_user(M(20, "しんじん"))
    await B.db.execute("UPDATE users SET wake_deadline='07:00', wake_set_day='2026-08-05', sleep_min=6, sleep_set_day='2026-08-05', bath_daily=1, bath_set_day='2026-08-05' WHERE id='20'")
    await B.db.commit()
    u20 = await B.get_user(20)
    check("初日は未達ゼロ", await B.build_misses(u20, "2026-08-05", d1, d2, False), [])
    check("初日は全項目スキップ扱い", await B.all_items_skipped(u20, "2026-08-05"), True)
    m20 = await B.build_misses(u20, "2026-08-06", d1, d2, False)
    check("翌日からは判定される", len(m20) >= 2, True)
    check("翌日はスキップ扱いでない", await B.all_items_skipped(u20, "2026-08-06"), False)
    # 既存ユーザー（set_day NULL）は従来どおり判定される
    check("set_day NULL は判定対象", await B.all_items_skipped(await B.get_user(2), "2026-08-05"), False)
    # 今日追加した個人項目は判定されない
    await B.db.execute("INSERT INTO custom_items(user_id,name,created_at) VALUES('20','散歩',?)", (int(datetime(2026, 8, 5, 15, tzinfo=JST).timestamp()),))
    await B.db.commit()
    check("今日追加の個人項目は未達に出ない", any("散歩" in x for x in await B.build_misses(await B.get_user(20), "2026-08-05", d1, d2, False)), False)
    check("翌日は個人項目も判定", any("散歩" in x for x in await B.build_misses(await B.get_user(20), "2026-08-06", d1, d2, False)), True)

    # 二度寝（記録と週次の二度寝賞）
    for hh in (9, 11, 14):
        await B.add_event(12, "nizone", ts_dt=datetime(2026, 8, 5, hh, tzinfo=JST))
    await B.db.commit()
    check("二度寝3回=四度寝", len(await B.events_on(12, "2026-08-05", "nizone")) + 1, 4)
    sent3 = []
    class FakeCh3:
        async def send(self, content=None, **kw):
            sent3.append((content, kw.get("embed")))
    B.get_ch = lambda key: _fake_async(FakeCh3() if key in ("tsushinbo", "kora") else None)
    B.now_jst = lambda: datetime(2026, 8, 9, 23, 5, tzinfo=JST)
    try:
        await B.weekly_summary(None, manual=True)
        aw3 = next((f.value for f in sent3[0][1].fields if "各賞" in f.name), "")
        check("二度寝賞がねぼうに", "😴 二度寝賞：**ねぼう**（3回）" in aw3, True)
    finally:
        B.get_ch = orig_get_ch
        B.now_jst = orig_now

    # 睡眠は合計で判定（寝落ち3h＋夜4h＝7h）
    await B.ensure_user(M(21, "ねおち"))
    await B.db.execute("UPDATE users SET sleep_min=6 WHERE id='21'"); await B.db.commit()
    await B.add_event(21, "sleep", note="3.00", ts_dt=datetime(2026, 8, 5, 21, tzinfo=JST))
    m21 = await B.build_misses(await B.get_user(21), "2026-08-05", d1, d2, False)
    check("3hだけなら睡眠不足", any("睡眠不足" in x for x in m21), True)
    await B.add_event(21, "sleep", note="4.00", ts_dt=datetime(2026, 8, 5, 22, tzinfo=JST))
    m21 = await B.build_misses(await B.get_user(21), "2026-08-05", d1, d2, False)
    check("3h+4h=7hで達成", any("睡眠" in x for x in m21), False)

    # 自動バックアップ（7世代）
    import os as _os
    bdir = B.BACKUP_DIR
    dest = await B.backup_db(keep=3)
    check("バックアップ作成", _os.path.exists(dest), True)
    for dd in range(1, 6):
        open(_os.path.join(bdir, f"seikatsu-2020-01-0{dd}.db"), "w").close()
    await B.backup_db(keep=3)
    rest = sorted(f for f in _os.listdir(bdir) if f.startswith("seikatsu-"))
    check("世代は3つに剪定", len(rest), 3)
    for f in rest:
        _os.remove(_os.path.join(bdir, f))
    _os.rmdir(bdir)
    # 週合計が設定されていても判定されない（廃止）
    check("chores_weekは対象外", B.has_any_setting({**dict(ub), "wake_deadline": None, "sleep_min": None, "bath_daily": 0, "meals_min": None, "radio_daily": 0, "teeth_min": 0, "kaji_cook": 0, "kaji_clean": 0, "kaji_dish": 0, "kaji_wash": 0, "kaji_trash": 0, "chores_week": 3}), False)

    # 暮らしのTIPS
    check("期限パース: 期限: 10/15", B.parse_tip_expiry("激うま。期限: 10/15 まで", datetime(2026, 9, 3, tzinfo=JST)), "2026-10-15")
    check("期限パース: なし", B.parse_tip_expiry("ただのレシピ", None), None)
    now_i = int(datetime(2026, 9, 1, tzinfo=JST).timestamp())
    await B.db.execute("INSERT INTO tips(thread_id,author_id,title,tags,day,ts,expires) VALUES('901','11','5分チャーハン','レシピ','2026-09-01',?,NULL)", (now_i,))
    await B.db.execute("INSERT INTO tips(thread_id,author_id,title,tags,day,ts,expires) VALUES('902','12','限定プリン','お得・期間限定','2026-09-01',?, '2026-09-02')", (now_i,))
    await B.db.execute("INSERT INTO tip_saves(tip_id,user_id,day) VALUES(1,'7','2026-09-01')")
    await B.db.commit()
    rows = await B.search_tips(today="2026-09-03")
    check("期限切れは検索から消える", [r["title"] for r in rows], ["5分チャーハン"])
    rows2 = await B.search_tips(today="2026-09-02")
    check("期限内なら出る", sorted(r["title"] for r in rows2), ["5分チャーハン", "限定プリン"])
    rows3 = await B.search_tips(keyword="チャーハン", today="2026-09-03")
    check("キーワード検索", len(rows3), 1)
    rows4 = await B.search_tips(saved_by="7", today="2026-09-03")
    check("TIPS帳（保存済みのみ）", [r["title"] for r in rows4], ["5分チャーハン"])
    tp = await B.tip_of_day("2026-09-03")
    check("今日のTIPSが選ばれる", tp["title"], "5分チャーハン")
    check("tip_line 表示", "🔖1" in B.tip_line(rows[0]) and "<#901>" in B.tip_line(rows[0]), True)

    # /watashi（期間・集計・気づき・CSV・グラフ）
    nw2 = datetime(2026, 9, 3, 12, 0, tzinfo=JST)  # 木曜
    check("期間: 今週", B.resolve_period("今週", nw2)[:2], ("2026-08-31", "2026-09-06"))
    check("期間: 先月", B.resolve_period("先月", nw2)[:2], ("2026-08-01", "2026-08-31"))
    check("期間: 全部はprevなし", B.resolve_period("全部", nw2)[2], None)
    ps = await B.personal_stats(11, "2026-08-03", "2026-08-09")
    check("personal_stats 平均起床", B.fmt_min(ps["wake_avg"]), "6:30")
    check("personal_stats 睡眠平均", round(ps["sleep_avg"], 1), 7.5)
    check("personal_stats 達成率100", ps["rate"], 100.0)
    ins = B.personal_insights({"per_day": [], "rate": 50.0, "wake_avg": 480.0}, {"rate": 30.0, "wake_avg": 500.0})
    check("気づき: 改善が出る", any("改善" in x for x in ins) and any("早起き" in x for x in ins), True)
    csvb = B.build_personal_csv([], [])
    check("CSVヘッダ", csvb.decode("utf-8-sig").startswith("day,kind,sub,time,note"), True)
    chart = B.render_personal_chart([{"day": "2026-09-01", "wake": 400, "sleep": 6.5, "ach": 1, "nizone": 0},
                                     {"day": "2026-09-02", "wake": 460, "sleep": 5.0, "ach": 0, "nizone": 1}], "テスト")
    check("個人グラフ生成", chart is not None and len(chart.getvalue()) > 10000, True)

    # 📖 勉強（みんなで暗記！！連携）
    b1 = M(61, "勉強する人"); await B.ensure_user(b1)
    b2 = M(62, "サボる人"); await B.ensure_user(b2)
    await B.db.execute("UPDATE users SET benkyou_min=1 WHERE id IN ('61','62')"); await B.db.commit()
    check("勉強: 連携未設定なら判定しない（冤罪防止）", await B.build_misses(await B.get_user(62), day, d1, d2, False), [])
    B.GAKUSHU_SECRET = "test"
    B._study_cache.update(day=day, ids={"61"}, ts=int(B.now_jst().timestamp()))
    check("勉強: 解いた人は未達なし", await B.build_misses(await B.get_user(61), day, d1, d2, False), [])
    check("勉強: 解いてない人は未達", await B.build_misses(await B.get_user(62), day, d1, d2, False),
          ["📖 勉強 未達（みんなで暗記！！で今日1問も解いてない）"])
    B._study_cache.update(day=day, ids=None, ts=int(B.now_jst().timestamp()))
    check("勉強: API不通の日は判定しない", await B.build_misses(await B.get_user(62), day, d1, d2, False), [])
    B.GAKUSHU_SECRET = ""
    check("勉強: has_any_setting に含む", B.has_any_setting(await B.get_user(62)), True)
    check("勉強: 設定表示に📖", "📖 勉強 毎日" in B.settings_text(await B.get_user(62)), True)

    # 夜勤🏭（起床・睡眠・ラジオ体操だけ免除）
    nw_eve = datetime(2026, 8, 5, 21, 0, tzinfo=JST)
    nw_am = datetime(2026, 8, 6, 8, 0, tzinfo=JST)
    check("夜勤: 夜に押すと翌日が対象", B.yakin_target_day(nw_eve), "2026-08-06")
    check("夜勤: 朝に押すと当日が対象（夜勤明け）", B.yakin_target_day(nw_am), "2026-08-06")
    y1 = M(41, "夜勤者"); await B.ensure_user(y1)
    await B.db.execute("UPDATE users SET wake_deadline='07:00', sleep_min=6, radio_daily=1, meals_min=1 WHERE id='41'")
    await B.db.execute("INSERT OR IGNORE INTO night_shifts(day,user_id) VALUES(?, '41')", (day,))
    await B.db.commit()
    m41 = await B.build_misses(await B.get_user(41), day, d1, d2, False)
    check("夜勤: 起床・睡眠・ラジオ体操は判定されない", m41, ["🍚 食事 0/1 回"])
    check("夜勤: is_night_shift", await B.is_night_shift(41, day), True)
    check("夜勤: 別の日は通常", await B.is_night_shift(41, "2026-08-09"), False)

    # やることメモ📌（今日限り・引き継ぎ・自動消滅）
    await B.db.execute("INSERT INTO memos(user_id,text,day,ts) VALUES('51','振込',?,1)", (day,))
    await B.db.execute("INSERT INTO memos(user_id,text,day,ts) VALUES('51','ゴミ袋を買う',?,1)", (day,))
    await B.db.execute("INSERT INTO memos(user_id,text,day,ts,carried) VALUES('51','古いメモ','2026-08-01',1,0)")
    await B.db.commit()
    ms = await B.open_memos("51", day)
    check("メモ: 今日の未完了は2件", [r["text"] for r in ms], ["振込", "ゴミ袋を買う"])
    check("メモ: 一覧テキスト", "振込" in B.memo_list_text(ms) and "押したら完了" in B.memo_list_text(ms), True)
    nxt = "2026-08-06"
    await B.db.execute("UPDATE memos SET day=?, carried=carried+1 WHERE user_id='51' AND day<?", (nxt, nxt))
    await B.db.commit()
    ms2 = await B.open_memos("51", nxt)
    check("メモ: 引き継ぎで翌日へ（持ち越し印つき）", len(ms2) == 3 and all(r["carried"] == 1 for r in ms2), True)
    check("メモ: 持ち越しは一覧に🌅", "🌅持ち越し" in B.memo_list_text(ms2), True)
    await B.db.execute("UPDATE memos SET day=? WHERE user_id='51' AND text='古いメモ'", ("2026-08-01",))
    await B.db.execute("INSERT OR IGNORE INTO memo_prompts(day,user_id,msg_id) VALUES('2026-08-01','51',NULL)")
    await B.db.commit()
    await B.memo_cleanup(nxt)
    check("メモ: 昨日以前は自動消滅・今日は残る", [r["text"] for r in await B.open_memos("51", nxt)], ["振込", "ゴミ袋を買う"])
    async with B.db.execute("SELECT COUNT(*) AS n FROM memo_prompts") as c:
        check("メモ: 古い引き継ぎ確認も掃除", (await c.fetchone())["n"], 0)
    check("メモ: 複数行パース（箇条書き記号・空行を整理）", B.parse_memo_lines("・参考文献を探す\n\n- 振込\nゴミ袋を買う  "),
          ["参考文献を探す", "振込", "ゴミ袋を買う"])
    check("メモ: 最大10件", len(B.parse_memo_lines("\n".join(f"t{i}" for i in range(15)))), 10)
    for txt in ("A", "B", "C"):
        await B.db.execute("INSERT INTO memos(user_id,text,day,ts,msg_id) VALUES('71',?,?,1,'777')", (txt, day))
    await B.db.execute("UPDATE memos SET done=1 WHERE user_id='71' AND text='B'"); await B.db.commit()
    check("メモ: 完了済みは一覧から消える", [r["text"] for r in await B.open_memos("71", day)], ["A", "C"])
    async with B.db.execute("SELECT * FROM memos WHERE msg_id='777' ORDER BY id") as c:
        grows = await c.fetchall()
    gc_content, gc_view = await B.memo_group_payload(grows, None)
    check("メモ: グループ本文に✅と⬜と進捗", "✅ ~~B~~" in gc_content and "⬜ A" in gc_content and "（✅ 1/3）" in gc_content, True)
    check("メモ: 未完了ぶんだけ✅ボタン", len(gc_view.children), 2)
    await B.db.execute("UPDATE memos SET done=1 WHERE user_id='71'"); await B.db.commit()
    async with B.db.execute("SELECT * FROM memos WHERE msg_id='777' ORDER BY id") as c:
        grows = await c.fetchall()
    gc_content2, gc_view2 = await B.memo_group_payload(grows, None)
    check("メモ: 全完了で祝い文＋ボタンなし", "ぜんぶやりきった" in gc_content2 and gc_view2 is None, True)
    check("えらいパネルに📌 2ボタン", {"sk_memo_add", "sk_memo_list"} <= {i.custom_id for i in B.EraiView().children}, True)
    check("起床パネルに🏭", any(i.custom_id == "sk_yakin" for i in B.WakeView().children), True)

    # 作業部屋🖥
    check("作業: fmt 45分", B.fmt_work(45 * 60), "45分")
    check("作業: fmt 1時間23分", B.fmt_work(83 * 60), "1時間23分")
    st_ = int(datetime(2026, 8, 5, 23, 30, tzinfo=JST).timestamp())
    en_ = int(datetime(2026, 8, 6, 1, 0, tzinfo=JST).timestamp())
    check("作業: 日またぎで分割", [(d, e - s) for d, s, e in B.split_session(st_, en_)],
          [("2026-08-05", 1800), ("2026-08-06", 3600)])
    check("作業: 同日は1区間", len(B.split_session(st_, st_ + 600)), 1)
    await B.db.execute("INSERT INTO work_sessions(user_id,start_ts,end_ts,day) VALUES('81',0,7200,?)", (day,))
    await B.db.execute("INSERT INTO work_sessions(user_id,start_ts,end_ts,day) VALUES('82',0,3600,?)", (day,))
    await B.db.commit()
    wa = await B.work_award(d1, d2)
    check("作業: もくもく賞は最長の人（2時間）", wa is not None and wa["title"] == "もくもく賞" and wa["value"] == "2時間0分", True)
    check("作業: チャンネル定義", B.CH["worklog"][0], "作業ログ🖥")

    # 通信簿カード🎨（デザイン済みテンプレートに数字を流し込む1枚画像）
    fake_rank = [{"name": "はやおき", "ach": 7, "judged": 7, "streak": 12},
                 {"name": "ねぼう🐷", "ach": 3, "judged": 7, "streak": 0}]
    fake_awards = [{"emoji": "👑", "title": "皆勤賞", "names": ["はやおき"], "value": "全日達成"},
                   {"emoji": "🐷", "title": "寝坊賞", "names": ["ねぼう🐷"], "value": "3回"},
                   {"emoji": "🖥", "title": "もくもく賞", "names": ["はやおき"], "value": "2時間0分"}]
    fake_series = [{"name": "はやおき", "wake": {"2026-08-03": 6.5, "2026-08-05": 6.9}, "sleep": {"2026-08-03": 7.2, "2026-08-04": 6.0}}]
    cardb = _card_orig("2026-08-03", "2026-08-09", fake_rank, fake_awards, fake_series)
    check("通信簿カード: PNG生成", cardb is not None and len(cardb.getvalue()) > 20000, True)
    check("通信簿カード: 空データでも生成", _card_orig("2026-08-03", "2026-08-09", [], [], []) is not None, True)
    check("通信簿カード: 名前の絵文字除去", B._plain_name("ねぼう🐷"), "ねぼう")
    check("通信簿カード: 絵文字フォント同梱", B._find_emoji_font() is not None and "NotoEmoji" in B._find_emoji_font(), True)

    # 時間割カード🗓
    tt_rows = [
        {"name": "微分積分学および演習Ⅱ", "slots": "月1,木2", "room": "L0026", "term": "2026後期"},
        {"name": "有機化学Ⅱ", "slots": "月2", "room": "142", "term": "2026後期"},
        {"name": "Integrated English", "slots": "水3", "room": "L1131", "term": "2026後期"},
        {"name": "生物化学", "slots": "水3", "room": "141", "term": "2026後期"},   # 同じコマの重複
        {"name": "自由入力の科目", "slots": "", "room": None, "term": None},        # 時限なし→載らない
        {"name": "土曜集中講義", "slots": "土5", "room": "講堂", "term": "2026後期"},
    ]
    ttb = B.render_timetable("しぐま", tt_rows)
    check("時間割カード: PNG生成", ttb is not None and len(ttb.getvalue()) > 10000, True)
    check("時間割カード: 時限なしのみは None", B.render_timetable("しぐま", [{"name": "x", "slots": "", "room": None, "term": None}]), None)

    # ---- 🔁 毎週課題（定例課題の自動生成） ----
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) "
                       "VALUES('tst001','テスト英語ED','てすとえいごed','ボールドウィン','L1','工','化物','','1','金2','2026後期',0)")
    await B.db.execute("INSERT INTO recurring_kadai(code,title,due_time,created_by,created_at) VALUES('tst001','プリント提出',NULL,'1',0)")
    await B.db.commit()
    wed = datetime(2026, 8, 5, 8, 0, tzinfo=JST)       # 水曜 → 金曜まで2日
    check("定例: 水曜朝に金曜ぶんを生成", await B.spawn_recurring(wed), 1)
    arow = await (await B.db.execute("SELECT * FROM assignments WHERE code='tst001'")).fetchone()
    adue = datetime.fromtimestamp(arow["due_ts"], JST)
    check("定例: 期限は金曜の授業開始(金2=10:30)", (adue.month, adue.day, adue.hour, adue.minute), (8, 7, 10, 30))
    check("定例: 同日再実行は増えない", await B.spawn_recurring(wed), 0)
    await B.db.execute("UPDATE assignments SET closed=1 WHERE id=?", (arow["id"],))   # 「今週はなし」＝取り下げ
    await B.db.commit()
    check("定例: 取り下げ後も再生成しない", await B.spawn_recurring(datetime(2026, 8, 6, 8, 0, tzinfo=JST)), 0)
    check("定例: 授業当日の朝に来週ぶんが生える", await B.spawn_recurring(datetime(2026, 8, 7, 8, 0, tzinfo=JST)), 1)   # 金曜朝→8/14(金)ぶん
    check("定例: 翌日の再実行は増えない", await B.spawn_recurring(datetime(2026, 8, 8, 8, 0, tzinfo=JST)), 0)
    # 締切時刻を指定したルール＋授業がもう始まっている日はスキップ
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,year,slots,term,custom) "
                       "VALUES('tst002','テスト分子生物学','てすとぶんしせいぶつがく','','L2','農','','','2','月1','2026後期',0)")
    await B.db.execute("INSERT INTO recurring_kadai(code,title,due_time,created_by,created_at) VALUES('tst002','出席フォーム','23:59','1',0)")
    await B.db.commit()
    check("定例: 指定時刻23:59なら当日+来週の2件", await B.spawn_recurring(datetime(2026, 8, 10, 12, 0, tzinfo=JST)), 2)   # 月曜昼→8/10と8/17
    brow = await (await B.db.execute("SELECT * FROM assignments WHERE code='tst002' ORDER BY due_ts")).fetchone()
    bdue = datetime.fromtimestamp(brow["due_ts"], JST)
    check("定例: 指定時刻が反映", (bdue.month, bdue.day, bdue.hour, bdue.minute), (8, 10, 23, 59))
    await B.db.execute("UPDATE recurring_kadai SET due_time=NULL WHERE code='tst002'")
    await B.db.execute("DELETE FROM recurring_spawned WHERE rule_id=(SELECT id FROM recurring_kadai WHERE code='tst002')")
    await B.db.execute("DELETE FROM assignments WHERE code='tst002'")
    await B.db.commit()
    check("定例: 授業開始(月1 8:45)過ぎの当日はスキップ・来週ぶんだけ生成", await B.spawn_recurring(datetime(2026, 8, 10, 12, 0, tzinfo=JST)), 1)
    check("定例: 当日スキップは課題なしで記録される", (await (await B.db.execute(
        "SELECT COUNT(*) AS n FROM recurring_spawned WHERE due_day='2026-08-10' AND assignment_id IS NULL")).fetchone())["n"], 1)
    # 定例課題は3日前リマインドを鳴らさない（前日・当日だけ）。通常課題は従来どおり鳴る
    teiki_aid = (await (await B.db.execute("SELECT assignment_id FROM recurring_spawned WHERE due_day='2026-08-14'")).fetchone())["assignment_id"]
    await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst001','単発レポート',?,'1',0)",
                       (int(datetime(2026, 8, 14, 23, 59, tzinfo=JST).timestamp()),))
    norm_aid = (await (await B.db.execute("SELECT id FROM assignments WHERE title='単発レポート'")).fetchone())["id"]
    _now_orig = B.now_jst
    B.now_jst = lambda: datetime(2026, 8, 11, 8, 0, tzinfo=JST)   # 期限の3日前
    try:
        await B.remind_assignments()
    finally:
        B.now_jst = _now_orig
    check("定例: 3日前リマインドは鳴らない", await (await B.db.execute(
        "SELECT 1 FROM assignment_reminded WHERE assignment_id=? AND stage='d3'", (teiki_aid,))).fetchone(), None)
    check("通常課題: 3日前リマインドは鳴る", (await (await B.db.execute(
        "SELECT 1 FROM assignment_reminded WHERE assignment_id=? AND stage='d3'", (norm_aid,))).fetchone()) is not None, True)
    await B.db.execute("UPDATE recurring_kadai SET active=0")
    await B.db.commit()
    check("定例: ルール解除(active=0)で生成停止", await B.spawn_recurring(datetime(2026, 8, 19, 8, 0, tzinfo=JST)), 0)
    check("定例: 解除済ルールは一覧に出ない", len(await B.teiki_rules_for("1")), 0)

    # 課題一覧のチェック用プルダウン（✅を一覧から入れられる）
    und = await B.kadai_undone_rows("1")
    n0 = len(und)
    check("課題チェック: 未完了一覧に候補がある", n0 >= 2, True)
    check("課題チェック: プルダウン項目数が一致", len(B.KadaiDoneSelect(und).options), min(n0, 25))
    await B.db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?, '1')", (und[0]["id"],))
    await B.db.commit()
    check("課題チェック: 完了済みは候補から消える", len(await B.kadai_undone_rows("1")), n0 - 1)
    for r in await B.kadai_undone_rows("1"):
        await B.db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?, '1')", (r["id"],))
    await B.db.commit()
    v1 = await B.kadai_list_view("1")
    check("課題チェック: 未完了ゼロでも🕑期限変更だけは出る", v1 is not None and len(v1.children) == 1, True)

    # ① 期限変更：due更新＋リマインド済み記録のリセット（再通知される）
    newdue = datetime(2026, 8, 20, 17, 0, tzinfo=JST)
    await B.kadai_set_due(norm_aid, newdue, "テスト")
    ak = await B.assignment_row(norm_aid)
    check("期限変更: due_tsが更新される", ak["due_ts"], int(newdue.timestamp()))
    check("期限変更: d3/d1/d0のリマインド記録が消える", (await (await B.db.execute(
        "SELECT COUNT(*) AS n FROM assignment_reminded WHERE assignment_id=? AND stage IN ('d3','d1','d0')", (norm_aid,))).fetchone())["n"], 0)

    # ② 来週の課題予報
    fct = await B.build_kadai_forecast(datetime(2026, 8, 9, 21, 0, tzinfo=JST))   # 日曜夜
    check("課題予報: 来週締切の定例課題が載る", fct is not None and "プリント提出" in fct and "8/14(金)" in fct, True)
    check("課題予報: 該当なしの週は None", await B.build_kadai_forecast(datetime(2026, 12, 1, 21, 0, tzinfo=JST)), None)

    # ③ 全員提出の祝福（2人以上・一度だけ）
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,slots,term,custom) "
                       "VALUES('tst003','テスト祝福','てすとしゅくふく','','','','','','月1','2026後期',0)")
    g95, g96 = M(95, "ゴリラ"), M(96, "ペリカン")
    await B.ensure_user(g95); await B.ensure_user(g96)
    for u in ("95", "96"):
        await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES(?, 'tst003')", (u,))
    rnow = B.now_jst()
    cur = await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst003','祝テスト',?, '95', ?)",
                             (int(rnow.timestamp()) + 86400, int(rnow.timestamp())))
    cid = cur.lastrowid
    await B.db.execute("INSERT INTO assignment_done(assignment_id,user_id) VALUES(?, '95')", (cid,))
    await B.db.commit()
    check("祝福: まだ全員ではない", await B.maybe_celebrate(cid), False)
    await B.db.execute("INSERT INTO assignment_done(assignment_id,user_id) VALUES(?, '96')", (cid,))
    await B.db.commit()
    check("祝福: 全員✅で祝う", await B.maybe_celebrate(cid), True)
    check("祝福: 二度目は鳴らない", await B.maybe_celebrate(cid), False)

    # 🌸 長期休暇モード
    check("休暇: 日付の読み取り", (B.parse_md("2/1", date(2026, 10, 1)), B.parse_md("2027-03-31", date(2026, 10, 1)), B.parse_md("あ", date(2026, 1, 1))),
          (date(2026, 2, 1), date(2027, 3, 31), None))
    await B.set_break(date(2027, 2, 1), date(2027, 3, 31), 120, "春休み")
    check("休暇: 期間内の判定", (B.break_on("2027-02-10"), B.break_on(date(2027, 4, 1)), B.break_on(datetime(2027, 3, 31, 9, 0, tzinfo=JST))), (True, False, True))
    check("休暇: 表示名", B.break_text(), "春休み（2/1〜3/31）")
    _u = {"wake_deadline": "07:00", "holiday_shift": 60}
    check("休暇: 平日も締切が+2時間", B.effective_deadline(_u, date(2027, 2, 10)), "09:00")
    check("休暇: 土日設定のほうが大きければそちら", B.effective_deadline({"wake_deadline": "07:00", "holiday_shift": 180}, date(2027, 2, 10)), "10:00")
    check("休暇: 期間外はいつもどおり", B.effective_deadline(_u, date(2027, 4, 7)), "07:00")
    check("休暇: 毎週課題を生やさない", await B.spawn_recurring(datetime(2027, 2, 10, 8, 0, tzinfo=JST)), 0)
    _dg = await B.today_digest("1", datetime(2027, 2, 10, 8, 0, tzinfo=JST))
    check("休暇: 朝の予定に時間割を出さず休暇の一言", ("🌸" in _dg, "時間割**" in _dg), (True, False))
    await B.set_break(None, None, 0, "")
    check("休暇: 解除", B.break_on("2027-02-10"), False)

    # 🌱 新学期の準備（いまの学期＝マスタでいちばん多いterm。違う学期の科目だけ外す）
    _term = await B.current_term()
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,slots,term,custom) "
                       "VALUES('old001','むかしの科目','むかしのかもく','','','','','','月1','1999前期',0)")
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('95','old001')")
    await B.db.execute("INSERT OR IGNORE INTO attendance(user_id,code,absent,limit_n) VALUES('95','old001',2,4)")
    await B.db.commit()
    _old, _cus, _curn, _t = await B.newterm_split("95")
    check("新学期: いまの学期が取れる", _t == _term and _t is not None, True)
    check("新学期: 前の学期の科目だけ拾う", [r["code"] for r in _old], ["old001"])
    check("新学期: まとめて外す（欠席カウンターも）", await B.drop_courses("95", ["old001"]), 1)
    check("新学期: 外れた", (await (await B.db.execute("SELECT COUNT(*) AS n FROM attendance WHERE user_id='95' AND code='old001'")).fetchone())["n"], 0)

    # ☔ 天気×洗濯（キャッシュに予報を入れて判定）
    import time as _time
    B._weather_cache.update(ts=_time.time(), daily={"weather_code": [61, 1], "precipitation_probability_max": [80, 10],
                                                    "temperature_2m_max": [15, 20], "temperature_2m_min": [8, 10]})
    check("天気: 雨コード＋80%は雨", B.rain_info(B._weather_cache["daily"], 0), (80, True))
    check("天気: 晴れ10%は雨じゃない", B.rain_info(B._weather_cache["daily"], 1), (10, False))
    _today = B.now_jst().date()
    check("洗濯: 今日が雨なら一言", "部屋干し" in await B.laundry_rain_note(_today), True)
    check("洗濯: 明日が晴れなら何も言わない", await B.laundry_rain_note(_today + B.timedelta(days=1)), "")
    check("天気: 1行表示に降水確率", "☔ 80%" in await B.fetch_weather(0), True)
    B._weather_cache.update(ts=0.0, daily=None)

    # 💡 冷蔵庫→TIPS（プリセット名で言い換えて探す）
    _tday = B.day_str(B.now_jst())
    await B.db.execute("INSERT INTO tips(thread_id,author_id,title,tags,day,ts) VALUES('t9001','95','牛乳で作るミルク粥','🍳レシピ',?,0)", (_tday,))
    await B.db.execute("INSERT INTO fridge_items(user_id,name,due_day,created_day) VALUES('98','低脂肪牛乳',?,?)", (_tday, _tday))
    await B.db.commit()
    check("冷蔵庫ヒント: 低脂肪牛乳→牛乳のTIPS", await B.fridge_tip_hints("98", _tday), ["**低脂肪牛乳** を使うなら →「牛乳で作るミルク粥」<#t9001>"])

    # 🎓 大学の予定
    _now = B.now_jst()
    _t0 = _now.date()
    eid = await B.add_univ_event("95", "履修登録", "前期 履修登録", _t0 - B.timedelta(days=1), _t0 + B.timedelta(days=2))
    _r = {"title": "前期 履修登録", "kind": "履修登録", "start_day": (_t0 - B.timedelta(days=1)).isoformat(), "end_day": (_t0 + B.timedelta(days=2)).isoformat()}
    check("大学の予定: 締切までの日数", B.univ_status(_r, B.day_str(_now)), "締切まであと2日")
    check("大学の予定: 開始前・当日まで・1日だけ",
          (B.univ_status({"kind": "行事", "start_day": "2027-01-10", "end_day": "2027-01-10"}, "2027-01-07"),
           B.univ_status({"kind": "その他", "start_day": "2027-01-01", "end_day": "2027-01-05"}, "2027-01-05"),
           B.univ_status({"kind": "行事", "start_day": "2027-01-10", "end_day": "2027-01-10"}, "2027-01-10")),
          ("あと3日", "今日まで！", "今日！"))
    _dg2 = await B.today_digest("95", _now)
    check("大学の予定: 開催中は朝の予定に出る", "🏫 いま" in _dg2 and "前期 履修登録" in _dg2, True)
    check("大学の予定: 試験期間は選べない（科目ごとの📝試験日を使う）", "試験期間" in [o.value for o in B.UnivKindSelect().options], False)
    check("大学の予定: ボタンは🏫で#予定のパネルに", any(getattr(i, "label", "") == "🏫 大学の予定" for i in B.YoteiPanelView().children), True)
    _up = await B.upcoming_digest("95", _now)
    check("大学の予定: 今後の予定にも出る", "大学の予定" in _up and "前期 履修登録" in _up, True)
    await B.add_univ_event("96", "長期休暇", "冬休み", date(2027, 12, 25), date(2028, 1, 6))
    check("大学の予定: 長期休暇は自動で休暇モード", (B.break_on("2027-12-30"), B.break_text(date(2027, 12, 30))), (True, "冬休み（12/25〜1/6）"))
    check("大学の予定: 休暇の締切ずらし", B.effective_deadline({"wake_deadline": "07:00", "holiday_shift": 0}, date(2027, 12, 30)), "09:00")
    await B.db.execute("DELETE FROM univ_events WHERE kind='長期休暇'"); await B.db.commit(); await B.load_break()
    check("大学の予定: 消すと休暇モードも外れる", B.break_on("2027-12-30"), False)

    # 🗓 予定カレンダー
    await B.add_univ_event("95", "行事", "学園祭", date(2026, 11, 20), date(2026, 11, 22))
    _gs, _ge, _evs, _its = await B.calendar_data("95", 2026, 11)
    check("カレンダー: グリッドは月曜はじまり・日曜おわり", (_gs.weekday(), _ge.weekday(), _gs <= date(2026, 11, 1)), (0, 6, True))
    check("カレンダー: 大学の予定が入る", any(r["title"] == "学園祭" for r in _evs), True)
    check("カレンダー: 画像PNG生成", B.render_calendar_card("テスト", 2026, 11, _gs, _ge, _evs, _its, "2026-11-21") is not None, True)
    check("カレンダー: ボタンは📊・🗓今月・🗓来月・🔒の4つ", [i.label for i in B.calendar_buttons(B.discord.ui.View(), row=0).children],
          ["📊 タイムライン", "🗓 今月", "🗓 来月", "🙋 じぶんの予定"])

    # 🔒 自分だけの予定（本人にだけ・終わったら消える）
    _td = B.now_jst().date()
    await B.db.execute("INSERT INTO private_events(user_id,title,start_day,end_day,ts) VALUES('95','歯医者',?,?,0)",
                       (_td.isoformat(), _td.isoformat()))
    await B.db.execute("INSERT INTO private_events(user_id,title,start_day,end_day,ts) VALUES('95','むかしの予定','2020-01-01','2020-01-02',0)")
    await B.db.commit()
    check("自分だけ: 本人の朝の予定に出る", "🙋 今日：🔒" in await B.today_digest("95", B.now_jst()), True)
    check("自分だけ: ほかの人には出ない", "歯医者" in await B.today_digest("96", B.now_jst()), False)
    check("自分だけ: カレンダーにも入る", any(r["kind"] == B.PRIVATE_KIND for r in (await B.calendar_data("95", _td.year, _td.month))[2]), True)
    check("自分だけ: 終わったものは消える", await B.private_cleanup(B.day_str(B.now_jst())), 1)

    # 📣 みんなに見せる予定（kindで区別・3日前と当日に声かけ・各1回）
    _d3 = (_td + B.timedelta(days=3)).isoformat()
    await B.db.execute("INSERT INTO private_events(user_id,title,start_day,end_day,ts,shared) VALUES('95','TOEIC本番',?,?,0,1)", (_d3, _d3))
    await B.db.commit()
    _pe = [r for r in await B.private_events_between("95", _td.isoformat(), _d3) if r["title"] == "TOEIC本番"]
    check("宣言: 見せる予定はkindが宣言・📣", (_pe[0]["kind"], B.UNIV_EMOJI[_pe[0]["kind"]]), (B.SHARED_KIND, "📣"))
    check("宣言: 自分だけ⇔見せるの切替プルダウン", any(isinstance(i, B.PrivateToggleSelect) for i in B.PrivateHubView(_pe).children), True)
    check("宣言: ほかの人の予定には出ない", "TOEIC" in await B.today_digest("96", B.now_jst()), False)

    # 📊 タイムライン（今月から最低4か月・課題は載せない）
    await B.db.execute("INSERT INTO private_events(user_id,title,start_day,end_day,ts) VALUES('95','帰省','2026-11-05','2026-11-07',0)")
    await B.db.commit()
    _s, _e, _tl, _more = await B.timeline_data("95", "2026-10-25")   # 日付を固定（実行日に左右されないように）
    check("タイムライン: 今月はじまり・4か月以上", (_s, (_e.year - _s.year) * 12 + _e.month - _s.month >= 3), (date(2026, 10, 1), True))
    check("タイムライン: 大学の予定と自分だけの予定が並ぶ", {r["kind"] for r in _tl} >= {"行事", B.PRIVATE_KIND}, True)
    check("タイムライン: 画像PNG生成", B.render_timeline_card("テスト", _s, _e, _tl, _more, "2026-10-25") is not None, True)

    # 🐾 いやしアルバム（見出しは文字・横の茶碗なし）
    from PIL import Image as _PI
    _bio = __import__("io").BytesIO()
    _PI.new("RGB", (400, 300), (200, 180, 160)).save(_bio, format="PNG")
    _png = _bio.getvalue()
    _al = B.render_meal_album([(_png, "ぺんぎん　🐾5"), (_png, "たぬき")], "10月のいやしアルバム", "", 10, True, None, None)
    check("いやしアルバム: コラージュPNG生成", _al is not None, True)

    # 📌 やること宣言の期限（空欄＝今日だけ／11/3＝今日〜／11/1-11/3＝期間）
    _n = datetime(2026, 10, 26, 12, 0, tzinfo=JST)
    check("メモ期限: 空欄は今日だけ", B.parse_memo_deadline("", _n), ("2026-10-26", None))
    check("メモ期限: 日付1つは今日〜その日", B.parse_memo_deadline("11/3", _n), ("2026-10-26", "2026-11-03"))
    check("メモ期限: 期間は開始日から", B.parse_memo_deadline("11/1-11/3", _n), ("2026-11-01", "2026-11-03"))
    check("メモ期限: 今日を書いたら期限なし扱い", B.parse_memo_deadline("10/26", _n), ("2026-10-26", None))
    check("メモ期限: 表示", B.memo_deadline_label({"until_day": "2026-11-03"}, "2026-11-01"), "（〜11/3・あと2日）")
    cur = await B.db.execute("INSERT INTO memos(user_id,text,day,ts,until_day) VALUES('95','レポート','2026-10-25',0,'2026-10-28')")
    _mid = cur.lastrowid
    await B.db.execute("INSERT INTO memos(user_id,text,day,ts) VALUES('95','振込','2026-10-25',0)")
    await B.db.commit()
    await B.memo_cleanup("2026-10-26")
    _left = {r["text"]: r["day"] for r in await (await B.db.execute("SELECT text, day FROM memos WHERE user_id='95'")).fetchall()}
    check("メモ期限: 期限内は今日へ繰り越し・期限なしは消える", (_left.get("レポート"), "振込" in _left), ("2026-10-26", False))
    check("メモ期限: 期限前は夜の引き継ぎ確認の対象外",
          [r["text"] for r in await B.open_memos("95", "2026-10-26") if not B.memo_until(r) or B.memo_until(r) <= "2026-10-26"], [])
    await B.db.execute("DELETE FROM memos WHERE id=?", (_mid,)); await B.db.commit()

    # 🎉 記念カード（発足から半年・n周年）
    check("記念日: 半年", B.anniversary_label(date(2027, 3, 1), "2026-09-01"), "半年")
    check("記念日: 1周年・2周年", (B.anniversary_label(date(2027, 9, 1), "2026-09-01"), B.anniversary_label(date(2028, 9, 1), "2026-09-01")), ("1周年", "2周年"))
    check("記念日: それ以外は None", (B.anniversary_label(date(2027, 3, 2), "2026-09-01"), B.anniversary_label(date(2027, 6, 1), "2026-09-01"),
                                      B.anniversary_label(date(2026, 9, 1), "2026-09-01")), (None, None, None))
    _tot = await B.circle_totals("2099-12-31")
    check("記念: 合計が全部数値で数えられる", all(isinstance(v, int) and v >= 0 for v in _tot.values()) and len(_tot) == 11, True)
    check("記念: カードPNG生成", B.render_anniversary_card("半年", _tot, "2026-09-01", "2027-03-01") is not None, True)

    # 🪪 プロフィールカード
    check("プロフィール: 自己紹介パネルに🪪ボタン", {i.custom_id for i in B.IntroView().children}, {"sk_intro", "sk_intro_card"})
    check("プロフィール: カードPNG生成（アイコンなし）",
          B.render_profile_card("テスト", None, {"f1": "工学部 1年", "f5": "早起き"}, {"wake": None, "streak": 0, "best": 0, "titles": []}) is not None, True)

    # 🍳 朝ごはんストリーク（今日まだなら昨日から・お休みは飛ばす）
    def _ts(dd, h):
        return int(datetime(2026, 9, dd, h, 0, tzinfo=JST).timestamp())
    for dd in (1, 2, 3, 5):   # 4日はお休み
        await B.db.execute("INSERT INTO events(user_id,kind,sub,ts,day,note) VALUES('97','meal','朝',?,?, '')",
                           (_ts(dd, 8), f"2026-09-{dd:02d}"))
    await B.db.execute("INSERT INTO off_days(day,user_id,reason) VALUES('2026-09-04','97','帰省')")
    await B.db.execute("INSERT INTO events(user_id,kind,sub,ts,day,note) VALUES('97','meal','昼',?, '2026-09-06', '')", (_ts(6, 12),))
    await B.db.commit()
    check("朝ごはん: お休みを挟んで4日連続", await B.asagohan_streak("97", "2026-09-05"), 4)
    check("朝ごはん: 今日まだなら昨日までで数える", await B.asagohan_streak("97", "2026-09-06"), 4)
    check("朝ごはん: 2日空いたら途切れる", await B.asagohan_streak("97", "2026-09-08"), 0)
    check("朝ごはん: 返事の一言は2日目から", (B.asagohan_note(1), "7日連続" in B.asagohan_note(7)), ("", True))

    # 🗒 科目メモ（共有・最新が先・上限で古いものから消える）
    await B.add_course_note("tst003", "95", "来週は2-428")
    await B.add_course_note("tst003", "96", "電卓持参")
    _cn = await B.course_notes_for("tst003")
    check("科目メモ: 新しい順・書いた人つき", [(r["text"], r["uname"]) for r in _cn], [("電卓持参", "ペリカン"), ("来週は2-428", "ゴリラ")])
    check("科目メモ: 朝の予定用は最新1件", await B.today_course_notes(["tst003"]), ["**テスト祝福**：電卓持参"])
    for i in range(B.COURSE_NOTE_MAX + 3):
        await B.add_course_note("tst003", "95", f"メモ{i}")
    check("科目メモ: 上限を超えたら古いものから消える", len(await B.course_notes_for("tst003", 100)), B.COURSE_NOTE_MAX)

    # 🎧 ヘビロテ（同じ人・同じ日・同じ曲は1回）
    hts = int(datetime(2026, 9, 2, 20, 0, tzinfo=JST).timestamp())
    for uid_, t_ in (("95", "YOASOBI「アイドル」Official MV"), ("95", "YOASOBI アイドル"), ("96", "YOASOBI アイドル"), ("96", "米津玄師 Lemon")):
        await B.db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES(?,?, '', ?)", (uid_, t_, hts))
    await B.db.commit()
    _songs, _people, _tot = await B.heavy_rotation("2026-08-31", "2026-09-06")
    check("ヘビロテ: 1位はアイドル（同日同人の重複は1回）", (_songs[0][1], _tot), (2, 3))
    check("ヘビロテ: カードPNG生成", B.render_heavy_rotation_card(_songs, _people, "2026-08-31", "2026-09-06") is not None, True)

    # 📖 月間しおり
    _sh = await B.shiori_stats("97", 2026, 9)
    check("しおり: 月の範囲", B.month_bounds(2026, 2), ("2026-02-01", "2026-02-28"))
    check("しおり: 集計にお休みが入る", "2026-09-04" in _sh["off"], True)
    check("しおり: カードPNG生成", B.render_shiori_card("テスト", _sh) is not None, True)

    # 🚫 提出しない（選択式の課題など）
    cur = await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst003','選択課題A',?, '95', ?)",
                             (int(rnow.timestamp()) + 86400, int(rnow.timestamp())))
    sid = cur.lastrowid
    await B.db.commit()
    check("提出しない: 最初は未完了一覧にある", any(r["id"] == sid for r in await B.kadai_undone_rows("95")), True)
    await B.kadai_mark(sid, "95", True)
    await B.db.commit()
    check("提出しない: 未完了一覧から消える", any(r["id"] == sid for r in await B.kadai_undone_rows("95")), False)
    check("提出しない: skip_setに入る・done_setにも入る（リマインド対象外）", ("95" in await B.skip_set(sid), "95" in await B.done_set(sid)), (True, True))
    _le = await B.kadai_list_embed("95")
    check("提出しない: 一覧には🚫で薄く残る", "🚫" in _le.description and "選択課題A" in _le.description, True)
    _lv = await B.kadai_list_view("95")
    check("提出しない: 取り消しセレクトが出る", any(isinstance(i, B.KadaiUnskipSelect) for i in _lv.children), True)
    await B.kadai_mark(sid, "96", False)
    await B.db.commit()
    check("提出しない: 出したのが1人だけなら全員提出祝福は鳴らない", await B.maybe_celebrate(sid), False)
    await B.kadai_mark(sid, "95", False)   # やっぱり出した→skipが外れて完了に
    await B.db.commit()
    check("提出しない→完了に上書き", ("95" in await B.skip_set(sid), "95" in await B.done_set(sid)), (False, True))

    # ④ 通信簿の課題系の賞
    for i, dd in ((1, 11), (2, 12)):
        await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst003',?,?,'96',?)",
                           (f"週課題{i}", int(datetime(2026, 8, dd, 23, 59, tzinfo=JST).timestamp()),
                            int(datetime(2026, 8, 10, 9, 0, tzinfo=JST).timestamp())))
    await B.db.commit()
    for title in ("週課題1", "週課題2"):
        aidw = (await (await B.db.execute("SELECT id FROM assignments WHERE title=?", (title,))).fetchone())["id"]
        await B.db.execute("INSERT INTO assignment_done(assignment_id,user_id) VALUES(?, '95')", (aidw,))
    aidw1 = (await (await B.db.execute("SELECT id FROM assignments WHERE title='週課題1'")).fetchone())["id"]
    await B.db.execute("INSERT INTO assignment_done(assignment_id,user_id) VALUES(?, '96')", (aidw1,))
    await B.db.commit()
    aw = await B.kadai_awards("2026-08-10", "2026-08-16")
    kic = next((a for a in aw if a["title"] == "きっちり賞"), None)
    kak = next((a for a in aw if a["title"] == "課題係賞"), None)
    check("きっちり賞: 全消化のゴリラだけ", kic is not None and kic["names"] == ["ゴリラ"] and "100%" in kic["value"], True)
    check("課題係賞: 2件登録のペリカン", kak is not None and kak["names"] == ["ペリカン"] and kak["value"] == "2件 登録", True)

    # ラベル丸写し事故（「English Discussion（（池辺）・金2） 農1年」事件）の解決
    tst = await B.get_course("tst001")
    label = B.course_label(tst)
    r = await B.ensure_custom_course(label)
    check("ラベル丸写し: ensure_custom_courseが本物に解決", r["code"], "tst001")
    await B.db.execute("INSERT INTO courses(code,name,nname,teacher,room,faculty,dept,cls,slots,term,custom) "
                       "VALUES('xdeadbeef',?,?,'','','','','','','',1)", (label, B.norm_text(label)))
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('42','xdeadbeef')")
    await B.db.commit()
    check("ラベル丸写し: 起動時掃除が1件解決", await B.cleanup_label_customs(), 1)
    check("ラベル丸写し: 科目行は削除される", await B.get_course("xdeadbeef"), None)
    check("ラベル丸写し: 履修は本物へ付け替え", "tst001" in [r2["code"] for r2 in await B.user_course_rows(42)], True)

    # 課題一覧: 完了済み＆期限切れは消える・未完了の期限切れは「期限切れ！」表示
    rn = B.now_jst()
    mk = lambda title, dd: B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst001',?,?,'91',0)",
                                        (title, int((rn + B.timedelta(days=dd)).timestamp())))
    await mk("過去×完了", -2); await mk("過去×未完了", -1); await mk("未来×完了", 2)
    await B.db.commit()
    for title in ("過去×完了", "未来×完了"):
        aid9 = (await (await B.db.execute("SELECT id FROM assignments WHERE title=?", (title,))).fetchone())["id"]
        await B.db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?, '91')", (aid9,))
    await B.db.commit()
    await mk("今朝×完了", 0)   # 期限=今この瞬間より少し前（同日内）
    await B.db.execute("UPDATE assignments SET due_ts=? WHERE title='今朝×完了'", (int(rn.timestamp()) - 3600,))
    await B.db.commit()
    aidm = (await (await B.db.execute("SELECT id FROM assignments WHERE title='今朝×完了'")).fetchone())["id"]
    await B.db.execute("INSERT OR IGNORE INTO assignment_done(assignment_id,user_id) VALUES(?, '91')", (aidm,))
    await B.db.commit()
    emb9 = await B.kadai_list_embed("91")
    check("課題一覧: 完了×期限切れは消える", "過去×完了" in emb9.description, False)
    check("課題一覧: 完了×今日の午前期限も時刻で消える", "今朝×完了" in emb9.description, False)
    check("課題一覧: 未完了×期限切れは残って警告", "過去×未完了" in emb9.description and "期限切れ！" in emb9.description, True)
    check("課題一覧: 完了×期限前は✅で残る", "✅ **テスト英語ED**：未来×完了" in emb9.description, True)

    # /suimin の日付指定（過去の誤記録の修正）
    pnow = datetime(2026, 10, 6, 9, 0, tzinfo=JST)
    check("過去日付: 省略・今日", B.parse_past_day("", pnow), "2026-10-06")
    check("過去日付: 昨日", B.parse_past_day("昨日", pnow), "2026-10-05")
    check("過去日付: おととい", B.parse_past_day("おととい", pnow), "2026-10-04")
    check("過去日付: 10/5", B.parse_past_day("10/5", pnow), "2026-10-05")
    check("過去日付: 年省略の未来日は昨年", B.parse_past_day("12/25", pnow), "2025-12-25")
    check("過去日付: 年指定の未来日は無効", B.parse_past_day("2027/1/1", pnow), None)
    check("過去日付: 不正は None", B.parse_past_day("あさって", pnow), None)
    # 過去日の睡眠を上書き：誤記録23.7h→7hに修正できる
    s9 = M(90, "寝すぎ太郎")
    await B.ensure_user(s9)
    bad_day = "2026-08-04"
    await B.add_event(90, "sleep", note="23.70", ts_dt=t(7, 0, 4))
    await B.db.execute("DELETE FROM events WHERE user_id='90' AND day=? AND kind='sleep'", (bad_day,))
    await B.add_event(90, "sleep", note="7.00", ts_dt=datetime(2026, 8, 4, 12, 0, tzinfo=JST))
    rows9 = await B.events_on(90, bad_day, "sleep")
    check("過去日の睡眠上書き: 1本に置き換わる", (len(rows9), float(rows9[0]["note"])), (1, 7.0))

    # 📝チェックリストの導線（各パネルのショートカット＋☀️返事の行差し替え）
    check("📝ショートカットが4パネル全部に", all(any(str(getattr(i, "custom_id", "")).startswith("sk_mycheck_") for i in V().children)
                                             for V in (B.WakeView, B.MealView, B.ChoreView, B.BathView)), True)
    check("行差し替え: 単独リストは丸ごと更新", B.mycheck_replace_line("📝 **今日のチェック**（x）　✅ 0/2", "NEW"), "NEW")
    check("行差し替え: ☀️返事は本文を保持", B.mycheck_replace_line("✅ 6:30 起床\n📅 今日の授業：数学\n📝 **今日のチェック**（x）　✅ 0/2\n-# 💡 豆", "NEW"),
          "✅ 6:30 起床\n📅 今日の授業：数学\nNEW\n-# 💡 豆")
    check("行差し替え: 行が無ければリストだけに", B.mycheck_replace_line("ただの文", "NEW"), "NEW")

    # 家事カウントは「押したボタンごと・本人のみ」
    for _ in range(2):
        await B.add_event(31, "chore", "clean", ts_dt=t(10))
    await B.add_event(31, "chore", "wash_run", ts_dt=t(11)); await B.add_event(31, "chore", "hang", ts_dt=t(12))
    await B.add_event(32, "chore", "clean", ts_dt=t(10))  # 別人の掃除は混ぜない
    check("家事: 掃除は本人の2回だけ", await B.count_events_between(31, "chore", d1, d2, sub="clean"), 2)
    check("家事: 洗濯工程は工程ごと", await B.count_events_between(31, "chore", d1, d2, sub="wash_run"), 1)
    check("家事: sub省略は従来通り全種合計", await B.count_events_between(31, "chore", d1, d2), 4)

    # 起床締切の操作猶予（＋10分）
    check("猶予: 6:30→6:40", B.grace_deadline("06:30"), "06:40")
    check("猶予: 23:55は23:59まで", B.grace_deadline("23:55"), "23:59")
    g1 = M(21, "ぎりぎりセーフ"); await B.ensure_user(g1)
    await B.db.execute("UPDATE users SET wake_deadline='06:30' WHERE id='21'"); await B.db.commit()
    await B.add_event(21, "wake", ts_dt=t(6, 40))
    check("猶予内(6:40)の報告は寝坊にならない", await B.build_misses(await B.get_user(21), day, d1, d2, False), [])
    g2 = M(22, "ぎりぎりアウト"); await B.ensure_user(g2)
    await B.db.execute("UPDATE users SET wake_deadline='06:30' WHERE id='22'"); await B.db.commit()
    await B.add_event(22, "wake", ts_dt=t(6, 41))
    check("猶予超え(6:41)は寝坊・表示は元の締切", await B.build_misses(await B.get_user(22), day, d1, d2, False), ["☀️ 寝坊 06:30 まで → 06:41"])

    # 課題一覧embed（パネルとコマンドの共通処理）
    check("課題一覧embed（履修者には出る）", (await B.kadai_list_embed("1")) is not None, True)
    check("課題一覧embed（無関係な人はNone）", await B.kadai_list_embed("999999"), None)

    # 冷蔵庫の期限リマインド🧊
    check("冷蔵庫: プリセット 牛乳=4日", B.fridge_preset_days("牛乳"), 4)
    check("冷蔵庫: 部分一致 低脂肪牛乳", B.fridge_preset_days("低脂肪牛乳"), 4)
    check("冷蔵庫: 未知はNone", B.fridge_preset_days("キャビア"), None)
    check("冷蔵庫: ラベル 今日", B.fridge_label("2026-09-03", "2026-09-03"), "今日まで！")
    check("冷蔵庫: ラベル 明日", B.fridge_label("2026-09-04", "2026-09-03"), "明日まで")
    check("冷蔵庫: ラベル あと3日", B.fridge_label("2026-09-06", "2026-09-03"), "あと3日")
    check("冷蔵庫: ラベル 期限切れ", B.fridge_label("2026-09-01", "2026-09-03"), "⚠️2日過ぎ")
    check("冷蔵庫: 期限表示", B.fridge_due_fmt("2026-09-07"), "9/7")
    for nm, due in (("牛乳", "2026-09-03"), ("もやし", "2026-09-04"), ("卵", "2026-09-10"), ("古い豆腐", "2026-08-25")):
        await B.db.execute("INSERT INTO fridge_items(user_id,name,due_day,created_day) VALUES('9',?,?,'2026-09-01')", (nm, due))
    await B.db.commit()
    soon = await B.fridge_soon("9", "2026-09-03")
    check("冷蔵庫: 今日・明日＋期限切れの3件（卵は出ない）", len(soon), 3)
    check("冷蔵庫: 表示に名前とラベル", any("牛乳" in s and "今日まで！" in s for s in soon), True)
    await B.fridge_cleanup("2026-09-03")
    check("冷蔵庫: 期限3日過ぎは自動削除", sorted(r["name"] for r in await B.fridge_items_of("9")), ["もやし", "卵", "牛乳"])

    # ⏰起床リマインド（オプトイン）
    wed_ = datetime(2026, 8, 5, 6, 0, tzinfo=JST)
    sat_ = datetime(2026, 8, 8, 6, 0, tzinfo=JST)
    check("⏰: 締切の10分前", B.wake_remind_target({"wake_deadline": "07:00", "holiday_shift": 0}, wed_), "06:50")
    check("⏰: 土日の後ろ倒しに追従", B.wake_remind_target({"wake_deadline": "07:00", "holiday_shift": 120}, sat_), "08:50")
    check("⏰: 締切なしはNone", B.wake_remind_target({"wake_deadline": None, "holiday_shift": 0}, wed_), None)
    check("起床パネルに⏰ボタン", any(i.custom_id == "sk_wake_remind" for i in B.WakeView().children), True)

    # ☀️押し忘れ救済（自動起床）
    _orig_now_aw = B.now_jst
    B.now_jst = lambda: datetime(2026, 8, 5, 7, 30, tzinfo=JST)
    try:
        u101 = M(101, "ねすごし"); await B.ensure_user(u101)
        await B.db.execute("UPDATE users SET wake_deadline='07:00' WHERE id='101'"); await B.db.commit()
        await B.add_event(101, "bed", ts_dt=datetime(2026, 8, 4, 23, 30, tzinfo=JST))
        await B.auto_wake(u101, "発言")
        w101 = await B.events_on(101, "2026-08-05", "wake")
        s101 = await B.events_on(101, "2026-08-05", "sleep")
        check("自動起床: 活動で☀️記録", len(w101), 1)
        check("自動起床: 睡眠も🌙から自動計算(8h)", len(s101) == 1 and abs(float(s101[0]["note"]) - 8.0) < 0.01, True)
        await B.auto_wake(u101, "発言")
        check("自動起床: 二重記録しない", len(await B.events_on(101, "2026-08-05", "wake")), 1)
        u102 = M(102, "夜更かし中"); await B.ensure_user(u102)
        await B.db.execute("UPDATE users SET wake_deadline='07:00' WHERE id='102'"); await B.db.commit()
        await B.add_event(102, "bed", ts_dt=datetime(2026, 8, 5, 6, 0, tzinfo=JST))   # 1.5時間前に就寝
        await B.auto_wake(u102, "発言")
        check("自動起床: 就寝直後の活動では記録しない", len(await B.events_on(102, "2026-08-05", "wake")), 0)
        u103 = M(103, "🌙なし"); await B.ensure_user(u103)
        await B.db.execute("UPDATE users SET wake_deadline='07:00' WHERE id='103'"); await B.db.commit()
        await B.auto_wake(u103, "発言")
        check("自動起床: 🌙が無ければ何もしない", len(await B.events_on(103, "2026-08-05", "wake")), 0)
    finally:
        B.now_jst = _orig_now_aw

    # 科目検索の複数語絞り込み（同名28件のAcademic Writing問題）＋事前リマインド
    rs = await B.search_courses("academic 畠山")
    check("科目検索: 教員名で絞れる（月1畠山が見つかる）", sorted(r["code"] for r in rs), ["EL1012g", "el0059"])
    rs2 = await B.search_courses("writing 月1")
    check("科目検索: 曜日時限で絞れる", len(rs2) == 10 and all("月1" in r["slots"] for r in rs2), True)
    rs3 = await B.search_courses("discussion 金1")
    check("手動追加: 農の金1 English Discussion（備考欄方式）が引ける",
          sorted(r["code"] for r in rs3), ["EL1003a", "EL1003b", "EL1003c", "EL1003d", "EL1003e"])
    rs4 = await B.search_courses("discussion 金2")
    check("手動追加: 金2の5クラスも", len(rs4), 5)

    # 実験・演習の連続コマ展開（結合セル対策の再ビルド確認）
    check("実験: 月3〜5限に展開", (await B.get_course("bo1005b"))["slots"], "月3,月4,月5")
    check("実験: 工学基礎実験も金1〜2", (await B.get_course("c0009"))["slots"], "金1,金2")

    # 🚨 欠席カウンター
    r = await B.kesseki_bump("121", "bo1005b")
    check("欠席: +1・既定ライン4", (r["absent"], r["limit_n"]), (1, 4))
    r = await B.kesseki_bump("121", "bo1005b", 2)
    check("欠席: 累積3・あと1回文言", r["absent"] == 3 and "次で落単ライン" in B.kesseki_text("生物学実験", r), True)
    r = await B.kesseki_bump("121", "bo1005b", -5)
    check("欠席: マイナスは0で止まる", r["absent"], 0)
    r = await B.kesseki_set_limit("121", "bo1005b", 2)
    check("欠席: ライン変更（回数は維持）", (r["limit_n"], r["absent"]), (2, 0))
    r = await B.kesseki_bump("121", "bo1005b", 2)
    check("欠席: 到達文言", "到達" in B.kesseki_text("x", r), True)

    # 📅 今日の時間割（1〜5限フル表示＋欠席警告）
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('122','bo1005b')")
    await B.db.commit()
    dg = await B.today_digest(122, datetime(2026, 8, 3, 8, 0, tzinfo=JST))   # 月曜
    check("時間割: 空きコマは─で5限まで", "1限 8:45　─" in dg and "5限 16:30" in dg, True)
    check("時間割: 実験は3限に教室付き・4限以降は〃", "3限 13:00　**生物学実験（Aクラス）**（1講─15）" in dg and "〃 生物学実験（Aクラス）" in dg, True)
    await B.kesseki_bump("122", "bo1005b", 3)   # 既定4→あと1回
    dg2 = await B.today_digest(122, datetime(2026, 8, 3, 8, 0, tzinfo=JST))
    check("時間割: 欠席残りわずか警告", "🚨 欠席はあと：**生物学実験（Aクラス）** あと1回" in dg2, True)

    # 👥 時間割コピー
    cp_src = M(112, "先人"); await B.ensure_user(cp_src)
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('112','el0046'),('112','el0047')")
    await B.db.commit()
    check("コピー: 2科目もらえる", await B.copy_courses("111", "112"), (2, 2))
    check("コピー: 再コピーは重複スキップ", await B.copy_courses("111", "112"), (0, 2))
    check("コピー: マージ先に入っている", sorted(r["code"] for r in await B.user_course_rows("111")), ["el0046", "el0047"])
    owners = await B.timetable_owners("999")
    own112 = next((o for o in owners if o[0] == "112"), None)
    check("コピー: 候補一覧に名前・科目数・学部タグ", own112 is not None and own112[1] == "先人" and own112[2] == 2 and own112[3] == "工1年・", True)
    check("コピー: 自分は候補から除外", any(o[0] == "112" for o in await B.timetable_owners("112")), False)
    check("事前リマインド: 朝の項目（もう取り返せない）は出さない",
          B.actionable_misses(["☀️ 寝坊 06:30 まで → 09:00", "🌙 睡眠不足 4.5h（最低 6.0h）", "🏃 ラジオ体操 未参加",
                               "🛁 入浴 未報告", "🍚 食事 1/2 回"]),
          ["🛁 入浴 未報告", "🍚 食事 1/2 回"])

    # 生活用品の使用期限📦
    check("期限メモ: プリセット マンスリー=30日", B.goods_preset_days("マンスリーコンタクト"), 30)
    check("期限メモ: 部分一致", B.goods_preset_days("左のマンスリーコンタクト"), 30)
    check("期限メモ: 未知はNone", B.goods_preset_days("謎の水"), None)
    await B.db.execute("INSERT INTO goods_items(user_id,name,cycle_days,due_day,opened_day) VALUES('91','コンタクト',30,?,'2026-07-06')", (day,))
    await B.db.execute("INSERT INTO goods_items(user_id,name,cycle_days,due_day,opened_day) VALUES('91','歯ブラシ',30,'2026-08-06','2026-07-07')")
    await B.db.execute("INSERT INTO goods_items(user_id,name,cycle_days,due_day,opened_day) VALUES('91','日焼け止め',365,'2026-12-01','2026-08-01')")
    await B.db.execute("INSERT INTO goods_items(user_id,name,cycle_days,due_day,opened_day) VALUES('91','古いスポンジ',30,'2026-08-01','2026-07-02')")
    await B.db.commit()
    gs = await B.goods_soon("91", day)
    check("期限メモ: 今日・明日・超過の3件（先の日焼け止めは出ない）", len(gs), 3)
    check("期限メモ: 超過は⚠️表示のまま消えない", any("⚠️4日過ぎ" in s for s in gs), True)
    async with B.db.execute("SELECT id FROM goods_items WHERE user_id='91' AND name='コンタクト'") as c:
        gid = (await c.fetchone())["id"]
    check("期限メモ: 交換で次サイクルへ", await B.goods_reset(gid, day), "2026-09-04")
    async with B.db.execute("SELECT opened_day FROM goods_items WHERE id=?", (gid,)) as c:
        check("期限メモ: 開封日も更新", (await c.fetchone())["opened_day"], day)
    check("使用期限パネルに📦🔄 2ボタン", {i.custom_id for i in B.GoodsPanelView().children}, {"sk_goods_add", "sk_goods_list"})
    check("使用期限パネルがVIEW_FACTORYに", B.VIEW_FACTORY.get("goods") is B.GoodsPanelView, True)
    check("使用期限チャンネル定義", B.CH["goods"][0], "使用期限📦")
    check("どうぶつチャンネル定義", B.CH["animal"][0], "どうぶつ🐄")
    check("おふろパネルに📦は無い", any(str(getattr(i, "custom_id", "")).startswith("sk_goods") for i in B.BathView().children), False)

    # 叱責に処方TIPS💊（テストDBのTIPS: 5分チャーハン=レシピタグ、限定プリン=期限切れ）
    rx = await B.prescribe_tip("42", "2026-09-03", ["🍚 食事 1/2 回"])
    check("処方: 食事未達→レシピTIPS", rx.startswith("💊") and "5分チャーハン" in rx and "<#901>" in rx, True)
    rx = await B.prescribe_tip("42", "2026-09-03", ["🍳 料理 今日やってない（毎日）"])
    check("処方: 料理未達→レシピTIPS", "5分チャーハン" in rx, True)
    rx = await B.prescribe_tip("42", "2026-09-03", ["☀️ 寝坊 07:00 まで → 09:42", "🍚 食事 0/2 回"])
    check("処方: ねむりが無ければ次の候補へ", "5分チャーハン" in rx, True)
    check("処方: 該当キーワード無し→空", await B.prescribe_tip("42", "2026-09-03", ["🏃 ラジオ体操 未参加"]), "")
    check("処方: 洗濯は家事ハック無しなら空", await B.prescribe_tip("42", "2026-09-03", ["🧺 洗濯 3日やってない（3日に1回）"]), "")
    check("処方: 同じ日・同じ人なら同じ処方", await B.prescribe_tip("42", "2026-09-03", ["🍚 食事 1/2 回"]),
          await B.prescribe_tip("42", "2026-09-03", ["🍚 食事 1/2 回"]))

    # ローディング画面のひとこと💡
    check("ひとこと: 50件以上読み込めた", len(B.HITOKOTO) >= 50, True)
    check("ひとこと: 全部が妥当な長さの文字列", all(isinstance(s, str) and 8 <= len(s) <= 140 for s in B.HITOKOTO), True)
    check("ひとこと: 重複なし", len(set(B.HITOKOTO)), len(B.HITOKOTO))
    draws = [B.hitokoto_suffix() for _ in range(40)]
    check("ひとこと: サブテキスト形式", all(d.startswith("\n-# 💡 ") for d in draws), True)
    check("ひとこと: 直近の繰り返しを避ける", len(set(draws)) >= min(30, len(B.HITOKOTO) // 2), True)

    # 📝 試験日カウントダウン
    exnow = datetime(2026, 8, 5, 8, 0, tzinfo=JST)
    await B.db.execute("INSERT INTO exams(code,title,exam_day,created_by,created_at) VALUES('tst001','期末試験','2026-09-04','42',0)")
    await B.db.commit()
    check("試験: 残り日数", B.exam_days_left("2026-09-04", exnow), 30)
    check("試験: 30日前に通知（記録）", await B.remind_exams(exnow), 1)
    check("試験: 同日の再実行は鳴らない", await B.remind_exams(exnow), 0)
    check("試験: 29〜8日前は静か", await B.remind_exams(datetime(2026, 8, 15, 8, 0, tzinfo=JST)), 0)
    check("試験: 1週間前に通知", await B.remind_exams(datetime(2026, 8, 28, 8, 0, tzinfo=JST)), 1)
    check("試験: 3日前に通知", await B.remind_exams(datetime(2026, 9, 1, 8, 0, tzinfo=JST)), 1)
    check("試験: 前日・当日はチャンネルに鳴らない", (await B.remind_exams(datetime(2026, 9, 3, 8, 0, tzinfo=JST)))
          + (await B.remind_exams(datetime(2026, 9, 4, 8, 0, tzinfo=JST))), 0)
    check("試験: ステージはm30/w7/d3のみ", sorted(B.EXAM_STAGES.values()), ["d3", "m30", "w7"])
    # ☀️ダイジェストに7日前からカウントダウン（user42はtst001履修）
    dig = await B.today_digest("42", datetime(2026, 9, 1, 7, 0, tzinfo=JST))
    check("試験: ☀️ダイジェストに「あと3日」", dig is not None and "試験" in dig and "あと3日" in dig, True)
    dig0 = await B.today_digest("42", datetime(2026, 9, 4, 7, 0, tzinfo=JST))
    check("試験: 当日の☀️は「今日！」", dig0 is not None and "今日！🔥" in dig0, True)
    check("試験: 過ぎたら自動クローズ", await B.remind_exams(datetime(2026, 9, 5, 8, 0, tzinfo=JST)) or
          (await (await B.db.execute("SELECT closed FROM exams WHERE exam_day='2026-09-04'")).fetchone())["closed"], 1)
    check("試験: クローズ後は一覧に出ない", len(await B.exam_rows_for("42")), 0)

    # 🕑 毎週課題のルール締切変更（生成済み分にも反映）
    cur = await B.db.execute("INSERT INTO recurring_kadai(code,title,due_time,created_by,created_at) VALUES('tst003','週プリ',NULL,'95',0)")
    rid3 = cur.lastrowid
    cur = await B.db.execute("INSERT INTO assignments(code,title,due_ts,created_by,created_at) VALUES('tst003','週プリ',?,'95',0)",
                             (int(datetime(2026, 8, 24, 8, 45, tzinfo=JST).timestamp()),))   # 月1=8:45既定
    aid3 = cur.lastrowid
    await B.db.execute("INSERT INTO recurring_spawned(rule_id,due_day,assignment_id) VALUES(?, '2026-08-24', ?)", (rid3, aid3))
    await B.db.execute("INSERT OR IGNORE INTO assignment_reminded(assignment_id,stage) VALUES(?, 'd1')", (aid3,))
    await B.db.commit()
    tnow3 = datetime(2026, 8, 22, 9, 0, tzinfo=JST)   # 土曜（月1の8/24分は生成済み→spawnは増えない）
    rule3, n3, _d3 = await B.teiki_update_rule(rid3, "21:00", None, tnow3)
    a3 = await B.assignment_row(aid3)
    check("定例時刻変更: 生成済み1件に反映", (n3, datetime.fromtimestamp(a3["due_ts"], JST).strftime("%H:%M")), (1, "21:00"))
    check("定例時刻変更: リマインド記録リセット", (await (await B.db.execute(
        "SELECT COUNT(*) AS n FROM assignment_reminded WHERE assignment_id=? AND stage IN ('d3','d1','d0')", (aid3,))).fetchone())["n"], 0)
    check("定例時刻変更: ルールに保存", rule3["due_time"], "21:00")
    rule3b, _r3, _d3b = await B.teiki_update_rule(rid3, None, None, tnow3)
    a3b = await B.assignment_row(aid3)
    check("定例時刻変更: 空欄で授業開始(月1=8:45)に戻る", datetime.fromtimestamp(a3b["due_ts"], JST).strftime("%H:%M"), "08:45")
    # 曜日変更：月→日。旧曜日の生成済みは取り下げ、新曜日の直近ぶん(8/23 日曜・授業なし→23:59)を即生成
    rule3c, rt3, dp3 = await B.teiki_update_rule(rid3, None, "日", tnow3)
    check("定例曜日変更: 旧曜日ぶんは取り下げ", (rt3, dp3), (0, 1))
    check("定例曜日変更: closedになる", (await B.assignment_row(aid3))["closed"], 1)
    new3 = await (await B.db.execute("SELECT * FROM assignments WHERE code='tst003' AND closed=0 ORDER BY id DESC LIMIT 1")).fetchone()
    nd3 = datetime.fromtimestamp(new3["due_ts"], JST)
    check("定例曜日変更: 新曜日で即生成（日曜・授業なし→23:59）", (nd3.month, nd3.day, nd3.weekday(), nd3.strftime("%H:%M")), (8, 23, 6, "23:59"))
    await B.db.execute("UPDATE recurring_kadai SET active=0 WHERE id=?", (rid3,))
    await B.db.execute("UPDATE assignments SET closed=1 WHERE code='tst003'")
    await B.db.commit()

    # 🧩 空きコマの個人予定
    await B.db.execute("INSERT INTO user_slots(user_id,slot,label,place) VALUES('42','火2','バイト','駅前')")
    await B.db.execute("INSERT INTO user_slots(user_id,slot,label,place) VALUES('42','火3','バイト','駅前')")
    await B.db.execute("INSERT INTO user_slots(user_id,slot,label,place) VALUES('42','水1','ジム',NULL)")
    await B.db.commit()
    pr42 = await B.personal_slot_rows("42")
    check("空きコマ: 同じ予定はまとめて1行（同色）", sorted((r["name"], r["slots"]) for r in pr42),
          [("ジム", "水1"), ("バイト", "火2,火3")])
    dig_f = await B.today_digest("42", datetime(2026, 9, 1, 7, 0, tzinfo=JST))   # 火曜
    check("空きコマ: ☀️の時間割に場所つきで出る", dig_f is not None and "バイト" in dig_f and "駅前" in dig_f, True)

    # 📖 ごはんアルバム
    check("ごはんパネルに📖アルバムボタン", any(i.custom_id == "sk_gohan_album" for i in B.MealView().children), True)
    e1 = await B.add_event(97, "meal", "夜", note="カレー", ts_dt=t(19, 0, 3))
    e2 = await B.add_event(97, "meal", "朝", note="トースト", ts_dt=t(8, 0, 4))
    e3 = await B.add_event(98, "meal", "昼", note="ラーメン", ts_dt=t(12, 0, 4))
    for eid_, mid in ((e1, "111"), (e2, "222"), (e3, "333")):
        await B.db.execute("UPDATE events SET msg_id=? WHERE id=?", (mid, eid_))
    for u in ("50", "51"):
        await B.db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,'2026-08-04')", (e3, u))
    await B.db.commit()
    rows_m = await B.meal_photo_rows("2026-08-03", "2026-08-05")
    check("アルバム: 👏の多い順→新しい順", [r["id"] for r in rows_m[:3]], [e3, e2, e1])
    check("アルバム: 本人指定は新しい順", [r["id"] for r in await B.meal_photo_rows("2026-08-03", "2026-08-05", uid=97)], [e2, e1])
    check("アルバム: 写真なし(msg_idなし)は入らない", all(r["msg_id"] for r in rows_m), True)
    from PIL import Image as _PI
    import io as _io
    def _dummy(color):
        b = _io.BytesIO()
        _PI.new("RGB", (500, 400), color).save(b, format="PNG")
        return b.getvalue()
    alb = B.render_meal_album([(_dummy((200, 80, 60)), "テスト　👏2"), (_dummy((80, 160, 90)), "8/4　朝ごはん"), (_dummy((90, 90, 200)), "x")],
                              "8月のベスト飯アルバム", "テスト")
    check("アルバム: コラージュPNG生成", alb is not None and len(alb.getvalue()) > 5000, True)
    check("アルバム: 壊れた画像でも落ちない", B.render_meal_album([(b"notimage", "x")], "t") is not None, True)
    check("アルバム: 手描き素材が全部ある", all(B._gohan_art(n) is not None for n in
          ["logo", "title", "sparkle", "bowl", "crown", "hearts", "tape_red", "tape_teal", "tape_yellow",
           "head_zairyo", "head_tsukurikata", "head_hitokoto"] + [f"d{i}" for i in range(10)]), True)
    check("アルバム: crown付きでも生成できる", B.render_meal_album([(_dummy((90, 90, 90)), "x")], "t", month=10, crown=True) is not None, True)
    from PIL import Image as _PI2
    a_plain = _PI2.open(B.render_meal_album([(_dummy((100, 100, 100)), "x")], "テスト"))
    a_hand = _PI2.open(B.render_meal_album([(_dummy((100, 100, 100)), "x")], "テスト", month=10))
    check("アルバム: 手描きヘッダー高さ132（茶碗＋右上ロゴ入り）", (a_plain.height, a_hand.height), (540, 540))   # 132 + (360+34+14)

    # 📖 ミニ勉強会（22〜23時に作業部屋にいた時間の重なり）
    bk_day = "2026-08-20"
    bt = lambda h, m=0: int(datetime(2026, 8, 20, h, m, tzinfo=JST).timestamp())
    await B.ensure_user(M(61, "どんちゃん")); await B.ensure_user(M(62, "ｴﾗ")); await B.ensure_user(M(63, "無関係"))
    rows_bk = [("61", bt(21, 30), bt(22, 20)),   # 前から入って20分重なる
               ("61", bt(22, 40), bt(22, 50)),   # 出入り自由＝再入室10分
               ("62", bt(22, 50), None),          # 入りっぱなし→枠の終わりまで10分
               ("63", bt(20, 0), bt(21, 0))]      # 枠外→不参加
    for uid, s, e in rows_bk:
        await B.db.execute("INSERT INTO work_sessions(user_id,start_ts,end_ts,day) VALUES(?,?,?,?)", (uid, s, e, bk_day))
    await B.db.commit()
    bk = await B.benkyokai_participants(bk_day, datetime(2026, 8, 20, 23, 0, tzinfo=JST))
    check("勉強会: 重なり分の合計と順位", [(u, m) for u, _, m in bk], [("61", 30), ("62", 10)])
    check("勉強会: 枠外の人は入らない", all(u != "63" for u, _, _ in bk), True)
    check("勉強会: 時間帯の既定は22-23", (B.BENKYOKAI_START, B.BENKYOKAI_END), (22, 23))

    # 勉強会ストリークと自動えらい報告
    for dd2 in ("2026-08-18", "2026-08-19"):   # 前々日・前日も参加していたことにする
        await B.db.execute("INSERT INTO events(user_id,kind,ts,day,note) VALUES('61','benkyokai',0,?, '10')", (dd2,))
    await B.db.commit()
    n_rec = await B.benkyokai_recap(bk_day, datetime(2026, 8, 20, 23, 0, tzinfo=JST))
    check("勉強会: リキャップは参加者2人", n_rec, 2)
    check("勉強会: ストリーク3日（61）", await B.benkyokai_streak("61", bk_day), 3)
    check("勉強会: 初参加はストリーク1（62）", await B.benkyokai_streak("62", bk_day), 1)
    ef61 = await (await B.db.execute("SELECT text FROM efforts WHERE user_id='61' AND day=?", (bk_day,))).fetchone()
    check("勉強会: 自動えらい報告（🔥つき）", ef61["text"], "ミニ勉強会に参加した（30分・🔥3日連続）")
    ef62 = await (await B.db.execute("SELECT text FROM efforts WHERE user_id='62' AND day=?", (bk_day,))).fetchone()
    check("勉強会: 初参加の報告は🔥なし", ef62["text"], "ミニ勉強会に参加した（10分）")

    # 🔊 作業部屋のVCステータス（サブタイトル）
    check("VCステータス: 勉強会中が最優先", await B.work_vc_status_text(datetime(2026, 8, 21, 22, 30, tzinfo=JST)),
          "📖 ミニ勉強会ちゅう（〜23:00）")
    await B.ensure_user(M(64, "どん"))
    cur = await B.db.execute("INSERT INTO work_sessions(user_id,start_ts,end_ts,day,note) VALUES('64',?,NULL,'2026-08-21','英検の単語')",
                             (int(datetime(2026, 8, 21, 12, 0, tzinfo=JST).timestamp()),))
    sid64 = cur.lastrowid
    await B.db.commit()
    check("VCステータス: ✍宣言を映す", await B.work_vc_status_text(datetime(2026, 8, 21, 13, 0, tzinfo=JST)), "📌 英検の単語")
    await B.db.execute("UPDATE work_sessions SET note=NULL WHERE id=?", (sid64,))
    await B.db.execute("INSERT INTO memos(user_id,text,day,ts) VALUES('64','レポート執筆','2026-08-21',0)")
    await B.db.commit()
    check("VCステータス: 宣言が無ければやること📌", await B.work_vc_status_text(datetime(2026, 8, 21, 13, 0, tzinfo=JST)), "📌 レポート執筆")
    await B.db.execute("UPDATE work_sessions SET end_ts=start_ts+600 WHERE id=?", (sid64,))
    await B.db.execute("UPDATE work_sessions SET end_ts=start_ts+600 WHERE end_ts IS NULL")   # 他テストの開きっぱなしも閉じる
    await B.db.commit()
    check("VCステータス: 誰もいなければ空", await B.work_vc_status_text(datetime(2026, 8, 21, 13, 0, tzinfo=JST)), "")

    # 📜 レシピカード
    check("レシピ: パネルにボタン", any(getattr(i, "custom_id", "") == "sk_recipe" for i in B.MealView().children), True)
    check("レシピ: 行パース（記号・番号を剥がす）", B.parse_recipe_lines("・卵 1個\n2. めんつゆ\n\n●ネギ"), ["卵 1個", "めんつゆ", "ネギ"])
    check("レシピ: 上限で切る", len(B.parse_recipe_lines("\n".join(f"材料{i}" for i in range(30)))), 14)
    check("レシピ: 材料行を(名前,分量)に分割", B.parse_ingredient_lines("冷凍うどん 1玉\n鶏もも肉：250g\nラー油\n・オリーブ オイル 大さじ1"),
          [("冷凍うどん", "1玉"), ("鶏もも肉", "250g"), ("ラー油", ""), ("オリーブ オイル", "大さじ1")])
    check("レシピ: 分量タプルでも描ける", B.render_recipe_card("t", "x", [("卵", "1個"), ("塩", "")], ["まぜる"]) is not None, True)
    rbuf = B.render_recipe_card("テスト", "超ながい料理名のテストでカードがはみ出さないか確認するやつ",
                                [f"材料{i} 100g" for i in range(8)],
                                ["とても長い手順の説明文をここに書いて折り返しが正しく効いているかどうかを確かめる" * 2, "まぜる", "たべる🌙"],
                                "コツも長めに書いて折り返しを確認する。深夜に食べるとうまい")
    check("レシピ: カードPNG生成", rbuf is not None and len(rbuf.getvalue()) > 20000, True)
    check("レシピ: 手順10個超でも落ちない", B.render_recipe_card("t", "x", ["a"], [f"手順{i}" for i in range(12)]) is not None, True)
    from PIL import Image as _PI3
    _db = _io.BytesIO()
    _PI3.new("RGB", (400, 300), (100, 120, 80)).save(_db, "PNG")
    _plain_h = _PI3.open(B.render_recipe_card("t", "画像テスト", ["a"], ["b"])).height
    _img_h = _PI3.open(B.render_recipe_card("t", "画像テスト", ["a"], ["b"], "", _db.getvalue(), {0: _db.getvalue()})).height
    check("レシピ: メイン＋工程画像で背が伸びる", _img_h > _plain_h + 300, True)
    check("レシピ: 壊れた画像bytesでも落ちない", B.render_recipe_card("t", "x", ["a"], ["b"], "", b"broken", {0: b"broken"}) is not None, True)
    # 画像回収フロー（下書きの状態機械）
    B.RECIPE_DRAFTS["77"] = {"title": "t", "ing": ["a"], "steps": ["b", "c"], "memo": "", "main": None, "imgs": {}, "await": None}
    check("レシピ回収: 待ち状態でなければ回収しない", B.consume_recipe_photo(77, b"img"), False)
    B.RECIPE_DRAFTS["77"]["await"] = "main"
    check("レシピ回収: メイン画像に取り込み", (B.consume_recipe_photo(77, b"img"), B.RECIPE_DRAFTS["77"]["main"]), (True, b"img"))
    check("レシピ回収: 取り込み後は待ち解除", B.RECIPE_DRAFTS["77"]["await"], None)
    B.RECIPE_DRAFTS["77"]["await"] = 1
    B.consume_recipe_photo(77, b"img2")
    check("レシピ回収: 工程2に取り込み", B.RECIPE_DRAFTS["77"]["imgs"], {1: b"img2"})
    del B.RECIPE_DRAFTS["77"]

    # 🛠 設定パネル（apply_saitei_item＝/saiteiと同じ意味論）
    s130 = M(130, "せっちゃん")
    await B.ensure_user(s130)
    check("saitei項目: 起床設定", await B.apply_saitei_item(130, "kishou", "7時30分"), None)
    u130 = await B.get_user(130)
    check("saitei項目: 値とset_day", (u130["wake_deadline"], u130["wake_set_day"] is not None), ("07:30", True))
    check("saitei項目: 不正な時刻はエラー文", (await B.apply_saitei_item(130, "kishou", "abc")) is not None, True)
    check("saitei項目: なしで解除", await B.apply_saitei_item(130, "kishou", "なし"), None)
    check("saitei項目: 解除反映", (await B.get_user(130))["wake_deadline"], None)
    await B.apply_saitei_item(130, "suimin", "6.5")
    check("saitei項目: 睡眠6.5h", (await B.get_user(130))["sleep_min"], 6.5)
    await B.apply_saitei_item(130, "suimin", "0")
    check("saitei項目: 0で解除", (await B.get_user(130))["sleep_min"], None)
    await B.apply_saitei_item(130, "nyuyoku", "はい")
    check("saitei項目: 入浴はい", (await B.get_user(130))["bath_daily"], 1)
    check("saitei項目: はい/いいえ以外はエラー", (await B.apply_saitei_item(130, "nyuyoku", "たまに")) is not None, True)
    await B.apply_saitei_item(130, "souji", "3")
    u130 = await B.get_user(130)
    check("saitei項目: 掃除3日に1回＋kaji_since", (u130["kaji_clean"], u130["kaji_since"] is not None), (3, True))
    # 🗓 毎週の定休日
    check("定休日: 土日を設定", await B.apply_saitei_item(130, "yasumi", "土日"), None)
    check("定休日: 保存形式", (await B.get_user(130))["off_weekdays"], "土日")
    check("定休日: 土曜に実体化される", await B.materialize_weekly_off("2026-08-08"), 1)   # 土
    off130 = await (await B.db.execute("SELECT reason FROM off_days WHERE day='2026-08-08' AND user_id='130'")).fetchone()
    check("定休日: off_daysに定休で記録", off130["reason"], "定休")
    check("定休日: 二重実体化しない", await B.materialize_weekly_off("2026-08-08"), 0)
    check("定休日: 平日は対象外", await B.materialize_weekly_off("2026-08-05"), 0)   # 水
    check("定休日: 空欄で解除", await B.apply_saitei_item(130, "yasumi", ""), None)
    check("定休日: 解除反映", (await B.get_user(130))["off_weekdays"], None)
    # 設定したその日の分も即実体化される（0時の日次実体化は済んでいるため。土曜の昼に土日を設定→その夜に叱責された事故の対策）
    nw_sat = datetime(2026, 8, 15, 14, 0, tzinfo=B.JST)   # 土曜の昼
    check("定休日: 当日設定で即実体化", await B.apply_saitei_item(130, "yasumi", "土日", now=nw_sat), None)
    off_sat = await (await B.db.execute("SELECT reason FROM off_days WHERE day='2026-08-15' AND user_id='130'")).fetchone()
    check("定休日: 当日のoff_daysに定休", off_sat["reason"], "定休")
    check("定休日: 当日解除で定休も取消", await B.apply_saitei_item(130, "yasumi", "", now=nw_sat), None)
    off_sat = await (await B.db.execute("SELECT reason FROM off_days WHERE day='2026-08-15' AND user_id='130'")).fetchone()
    check("定休日: 取消反映", off_sat, None)
    # 定休でない日に設定しても今日のoff_daysは増えない
    nw_wed = datetime(2026, 8, 12, 14, 0, tzinfo=B.JST)   # 水曜
    await B.apply_saitei_item(130, "yasumi", "土日", now=nw_wed)
    off_wed = await (await B.db.execute("SELECT reason FROM off_days WHERE day='2026-08-12' AND user_id='130'")).fetchone()
    check("定休日: 平日設定では当日実体化なし", off_wed, None)
    await B.apply_saitei_item(130, "yasumi", "", now=nw_wed)   # 後片付け

    # 📂 プレイリスト＆⭐おすすめ曲（URLを混ぜると裏のタイトル解決タスクが走るので検索語のみでテスト）
    pid = await B.pl_create(130, "テスト用", ["YOASOBI 夜に駆ける", "米津玄師 Lemon"])
    check("プレイリスト: 作成と曲数", len(await B.pl_tracks(pid)), 2)
    pl_row = next((r for r in await B.pl_all() if r["id"] == pid), None)
    check("プレイリスト: 一覧（名前・曲数・作者名）", (pl_row["name"], pl_row["n"], pl_row["oname"]), ("テスト用", 2, "せっちゃん"))
    await B.pl_add_tracks(pid, ["あいみょん マリーゴールド"])
    check("プレイリスト: 曲追加", len(await B.pl_tracks(pid)), 3)
    await B.db.execute("INSERT INTO member_recs(user_id,query,title,comment,ts) VALUES('130','q1','おすすめ曲','神曲です',0)")
    await B.db.commit()
    rec = next((r for r in await B.rec_all() if r["title"] == "おすすめ曲"), None)
    check("おすすめ: 一覧（紹介文・名前つき）", (rec["comment"], rec["uname"]), ("神曲です", "せっちゃん"))
    check("おすすめ: 既定はsong", rec["kind"], "song")
    check("おすすめ: 曲の表示名", B.rec_label(rec), "おすすめ曲（せっちゃん）")
    await B.db.execute("INSERT INTO member_recs(user_id,query,title,comment,ts,kind) VALUES('130','YOASOBI','YOASOBI','全部いい',0,'artist')")
    await B.db.commit()
    art = next((r for r in await B.rec_all() if r["kind"] == "artist"), None)
    check("おすすめ: アーティスト登録と🎤表示（0曲）", B.rec_label(art), "🎤 YOASOBI（せっちゃん・0曲）")
    check("おすすめ: 件数カウント", await B.rec_count(130), 2)
    await B.db.execute("INSERT INTO member_rec_tracks(rec_id,query,title,comment,ts) VALUES(?,?,?,?,0)",
                       (art["id"], "q2", "夜に駆ける", "初見はこれ")); await B.db.commit()
    check("おすすめ: アーティストの曲（紹介文つき）", [(t["title"], t["comment"]) for t in await B.rec_tracks(art["id"])],
          [("夜に駆ける", "初見はこれ")])
    art = next((r for r in await B.rec_all() if r["kind"] == "artist"), None)
    check("おすすめ: 表示に曲数反映", B.rec_label(art), "🎤 YOASOBI（せっちゃん・1曲）")
    # 📝 メンバー登録の歌詞（キーの双方向部分一致で曲タイトルと照合）
    await B.db.execute("INSERT INTO custom_lyrics(norm_key,title,text,user_id,ts) VALUES(?,?,?,?,0)",
                       (B.lyrics_norm("アイドル YOASOBI"), "アイドル YOASOBI", "ららら歌詞", "130")); await B.db.commit()
    _cl = await B.custom_lyrics_find("YOASOBI「アイドル」Official Music Video")
    check("歌詞登録: 語順が違ってもヒット", _cl and _cl["text"], "ららら歌詞")
    _cl2 = await B.custom_lyrics_find("アイドル YOASOBI MV")
    check("歌詞登録: 同語順でもヒット", _cl2 and _cl2["text"], "ららら歌詞")
    check("歌詞登録: 無関係な曲はヒットしない", await B.custom_lyrics_find("米津玄師 Lemon"), None)

    # 📜 歌詞登録の候補曲（いまの曲→キュー→履歴・重複はキーで除去）
    B._player["cur_item"] = {"type": "yt", "title": "アイドル YOASOBI MV"}
    B._player["queue"] = [{"type": "yt_lazy", "title": "米津玄師 Lemon"}, {"type": "file", "path": "x.mp3"}]
    await B.db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES('130','YOASOBI「アイドル」Official MV','',0)")
    await B.db.execute("INSERT INTO play_history(user_id,title,url,ts) VALUES('130','ヨルシカ 晴る','',0)")
    await B.db.commit()
    _cand = await B.lyrics_candidate_titles()
    check("歌詞候補: いまの曲が先頭", _cand[0], "アイドル YOASOBI MV")
    check("歌詞候補: キューの曲入り・同曲の履歴は重複除去", ("米津玄師 Lemon" in _cand, "YOASOBI「アイドル」Official MV" not in _cand), (True, True))
    check("歌詞候補: 履歴の曲入り", "ヨルシカ 晴る" in _cand, True)
    B._player["cur_item"] = None
    B._player["queue"] = []

    check("歌詞同期: 既定で自動ON", await B.karaoke_auto_enabled(), True)
    await B.meta_set("karaoke_auto", "0")
    check("歌詞同期: metaでOFFに切替", await B.karaoke_auto_enabled(), False)
    await B.meta_set("karaoke_auto", "1")

    B._player["bgm"] = True
    _msg = B._bgm_mode_off()
    check("BGM: リクエストでモードオフ＋案内", (B._player["bgm"], "オフ" in _msg), (False, True))
    check("BGM: すでにオフなら案内なし", B._bgm_mode_off(), "")

    # meta の upsert
    await B.meta_set("last_judge_day", "2026-08-05"); await B.meta_set("last_judge_day", "2026-08-06")
    check("meta 上書き", await B.meta_get("last_judge_day"), "2026-08-06")

    # 科目ラベルから学科・クラス記号を除く
    lab = B.course_label({"name": "生涯スポーツ実技", "teacher": "若月", "slots": "金2", "faculty": "工", "year": 1, "cls": "L1", "code": "x"})
    check("ラベル: 学科記号(L1)は出ない", "L1" not in lab and lab.endswith("工1年"), True)
    # 体育はマスタJSONで教員ごとに分割済み
    import json as _json
    _mm = _json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "courses_2026_kouki.json"), encoding="utf-8"))
    _pe = [c for c in _mm if c["name"] in ("生涯スポーツ実技", "スポーツ健康科学実技")]
    check("体育: 教員ごとに分割済み（連名なし）", len(_pe) >= 10 and all("・" not in (c["teacher"] or "") for c in _pe), True)
    # マスタから消えたコードの掃除（参照がある科目は残す）
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,slots,term,custom) "
                       "VALUES('zz999','旧科目','きゅうかもく','','','','','','月1','2026後期',0)")
    await B.db.execute("INSERT OR IGNORE INTO courses(code,name,nname,teacher,room,faculty,dept,cls,slots,term,custom) "
                       "VALUES('zz998','旧科目2','きゅうかもく2','','','','','','月2','2026後期',0)")
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('42','zz998')")
    await B.db.commit()
    await B.load_courses_master()
    check("旧コード掃除: 参照なしは消える", await B.get_course("zz999"), None)
    check("旧コード掃除: 履修者がいる科目は残る", (await B.get_course("zz998")) is not None, True)
    check("旧コード掃除: テスト用参照つき科目も残る", (await B.get_course("tst001")) is not None, True)
    # 実験のA/Bクラス分割（extraで既存コード上書き＋B用新コード）
    check("実験AB: 既存コードはAクラスに改名", (await B.get_course("bo1005b"))["name"], "生物学実験（Aクラス）")
    check("実験AB: Bクラスの新コード", (await B.get_course("bo1005bB"))["name"], "生物学実験（Bクラス）")
    check("実験AB: 化学もA/B", ((await B.get_course("ch1004a"))["name"], (await B.get_course("ch1004aB"))["name"]),
          ("化学実験（Aクラス）", "化学実験（Bクラス）"))
    check("実験AB: コマは月3-5のまま", (await B.get_course("bo1005bB"))["slots"], "月3,月4,月5")
    # 🧪 実験カレンダー（日付別の実施内容）
    check("実験cal: データ読込（4科目）", sorted(B.LAB_SCHEDULE.keys()), ["bo1005b", "bo1005bB", "ch1004a", "ch1004aB"])
    await B.ensure_user(M(123, "ビーさん"))
    await B.db.execute("INSERT OR IGNORE INTO user_courses(user_id,code) VALUES('123','bo1005bB')")
    await B.db.commit()
    dgl = await B.today_digest(123, datetime(2026, 10, 19, 8, 0, tzinfo=JST))   # 月曜・Bクラスは「動物」
    check("実験cal: Bクラスの当日内容が出る", dgl is not None and "🧪 今日の実験" in dgl and "動物（2-428・宮本）" in dgl, True)
    dgl2 = await B.today_digest(123, datetime(2026, 10, 12, 8, 0, tzinfo=JST))   # 予備日→実験行なし
    check("実験cal: 予備日は出ない", dgl2 is None or "🧪" not in dgl2, True)
    await B.db.close()

try:
    asyncio.run(main())
except BaseException:
    import traceback
    traceback.print_exc()
    print("\n=== FAILED (exception) ===")
    os._exit(1)   # db未closeでもaiosqliteのスレッドを残さず即終了
print("\n=== " + ("ALL PASS" if ok else "FAILED") + " ===")
sys.exit(0 if ok else 1)
