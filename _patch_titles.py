import io
p = "bot.py"
s = io.open(p, encoding="utf-8").read()
def rep(old, new, count=1):
    global s
    n = s.count(old)
    assert n == count, f"anchor count={n} (want {count}): {old[:80]!r}"
    s = s.replace(old, new)

# ---- schema: ごはん写真の👏 ----
rep("CREATE TABLE IF NOT EXISTS effort_praise(", "CREATE TABLE IF NOT EXISTS meal_praise(event_id INTEGER NOT NULL, user_id TEXT NOT NULL, day TEXT, PRIMARY KEY(event_id, user_id));\nCREATE TABLE IF NOT EXISTS effort_praise(")

# ---- 称号ロール定義＋操作（_reaction_role の前に挿入） ----
rep("async def _reaction_role(payload, add):", r'''# ---- 称号ロール（判定結果からBotが自動で付け外し） ----
TITLE_STREAK = [(30, "🔥リズムの化身", 0xff4d00), (7, "🔥リズムの達人", 0xff7a1a), (3, "🔥リズム見習い", 0xffa64d)]
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

async def _reaction_role(payload, add):''')

# ---- 判定後に段位を更新 ----
rep('''        await gakushu_report(u["id"], u["name"] or "", day, achieved, streak, len(misses))
''', '''        await gakushu_report(u["id"], u["name"] or "", day, achieved, streak, len(misses))
        try:
            await apply_streak_title(guild, u["id"], streak)
        except Exception as e:
            print(f"段位エラー: {e!r}", flush=True)
''')

# ---- ごはん写真に「👏 うまそう！」ボタン ----
rep('''    view = discord.ui.View(timeout=None)
    for s2, e2 in MEALS:
        view.add_item(MealFixButton(eid, message.author.id, s2, e2, current=sub))
    await message.reply(f"📸 {MEAL_EMOJI[sub]} **{sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）",
                        mention_author=False, view=view)
    await bump_panel("meal")
''', '''    view = discord.ui.View(timeout=None)
    for s2, e2 in MEALS:
        view.add_item(MealFixButton(eid, message.author.id, s2, e2, current=sub))
    view.add_item(MealPraiseButton(eid, 0))
    await message.reply(f"📸 {MEAL_EMOJI[sub]} **{sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）",
                        mention_author=False, view=view)
    await bump_panel("meal")

class MealPraiseButton(discord.ui.DynamicItem[discord.ui.Button], template=r"sk_mp:(?P<eid>\\d+)"):
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
''')

# MealFix の編集時も👏ボタンを保つ
rep('''        view = discord.ui.View(timeout=None)
        for s2, e2 in MEALS:
            view.add_item(MealFixButton(self.eid, self.uid, s2, e2, current=self.sub))
        await interaction.response.edit_message(
            content=f"📸 {MEAL_EMOJI[self.sub]} **{self.sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）", view=view)
''', '''        view = discord.ui.View(timeout=None)
        for s2, e2 in MEALS:
            view.add_item(MealFixButton(self.eid, self.uid, s2, e2, current=self.sub))
        async with db.execute("SELECT COUNT(*) AS n FROM meal_praise WHERE event_id=?", (self.eid,)) as c:
            n = (await c.fetchone())["n"]
        view.add_item(MealPraiseButton(self.eid, n))
        await interaction.response.edit_message(
            content=f"📸 {MEAL_EMOJI[self.sub]} **{self.sub}ごはん** として記録しました（違ったら下のボタンで選び直せます・本人のみ）", view=view)
''')

# ---- 通信簿：飯テロ賞＋称号更新＋目標宣言スレッド ----
rep('''        award("👏 ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),''',
    '''        award("👏 ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),''')
rep('''    kaikin_need = 3 if kind == "week" else 15
    kaikin = [x for x in judged if x["judged"] >= kaikin_need and x["ach"] == x["judged"]]''',
    '''    kaikin_need = 3 if kind == "week" else 15
    kaikin = [x for x in judged if x["judged"] >= kaikin_need and x["ach"] == x["judged"]]
    mt_names, mt_best = await meshitero_winners(d1, d2)''')
rep('''        award("👏 ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),
        award("🐷 寝坊賞", "late", fmt=lambda v: f"{v}回"),''',
    '''        award("👏 ほめ上手賞", "praises", fmt=lambda v: f"{v}回"),
        ("🍜 飯テロ賞：" + "、".join(f"**{n}**" for n in mt_names) + f"（👏{mt_best}）") if mt_names else None,
        award("🐷 寝坊賞", "late", fmt=lambda v: f"{v}回"),''')
rep('''    if file:
        await ch.send(embed=emb, file=file)
    else:
        await ch.send(embed=emb)
    return f"{'通信簿' if kind == 'week' else '月間表彰'}を投稿しました（{len(stats)}人）"''',
    '''    if file:
        await ch.send(embed=emb, file=file)
    else:
        await ch.send(embed=emb)
    if guild is not None:
        try:
            if kind == "week":
                nebou = [x for x in stats if x["late"]]
                best_late = max((x["late"] for x in nebou), default=0)
                await set_exclusive_title(guild, TITLE_KAIKIN[0], [x["uid"] for x in kaikin])
                await set_exclusive_title(guild, TITLE_NEBOU[0], [x["uid"] for x in nebou if x["late"] == best_late] if best_late else [])
            else:
                await set_exclusive_title(guild, TITLE_MVP[0], [judged[0]["uid"]] if judged else [])
                await post_goal_thread(guild, ch, now)
        except Exception as e:
            print(f"称号/宣言スレ エラー: {e!r}", flush=True)
    return f"{'通信簿' if kind == 'week' else '月間表彰'}を投稿しました（{len(stats)}人）"

async def post_goal_thread(guild, ch, now):
    """月間表彰の直後に、翌月の目標宣言スレッドを作る（月に1回だけ）"""
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=1)
    key = f"goal_thread_{nxt.year}-{nxt.month:02d}"
    if await meta_get(key):
        return
    prev = await meta_get("goal_thread_last")
    extra = f"\\n🔙 先月の宣言の答え合わせもどうぞ → <#{prev}>" if prev else ""
    msg = await ch.send(f"🎯 **{nxt.month}月の目標宣言、募集！**\\nスレッドに一言どうぞ。来月末の表彰で答え合わせします。{extra}")
    try:
        th = await msg.create_thread(name=f"🎯 {nxt.month}月の目標宣言")
        await meta_set(key, th.id)
        await meta_set("goal_thread_last", th.id)
    except Exception as e:
        print(f"宣言スレッド作成失敗（Botに「公開スレッドの作成」権限が必要）: {e!r}", flush=True)
        await meta_set(key, msg.id)''')

# ---- setup：称号ロールも作成、DynamicItem登録 ----
rep("        created = await ensure_roles(guild)", "        created = await ensure_roles(guild)\n        created += await ensure_title_roles(guild)")
rep("        self.add_dynamic_items(DoneButton, MealFixButton, PraiseButton)", "        self.add_dynamic_items(DoneButton, MealFixButton, PraiseButton, MealPraiseButton)")

io.open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched bot.py (titles/meshitero/goal)")

t = io.open("test_bot.py", encoding="utf-8").read()
t = t.replace("    # meta の upsert\n", '''    # 称号の段位と飯テロ賞
    check("段位: 2日はなし", B.streak_title_for(2), None)
    check("段位: 3日で見習い", B.streak_title_for(3), "🔥リズム見習い")
    check("段位: 10日で達人", B.streak_title_for(10), "🔥リズムの達人")
    check("段位: 40日で化身", B.streak_title_for(40), "🔥リズムの化身")
    mid1 = await B.add_event(11, "meal", "昼", note="写真", ts_dt=datetime(2026, 8, 5, 12, tzinfo=JST))
    mid2 = await B.add_event(12, "meal", "夜", note="写真", ts_dt=datetime(2026, 8, 5, 19, tzinfo=JST))
    for uid in ("7", "9"):
        await B.db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,'2026-08-05')", (mid1, uid))
    await B.db.execute("INSERT OR IGNORE INTO meal_praise(event_id,user_id,day) VALUES(?,?,'2026-08-05')", (mid2, "7"))
    await B.db.commit()
    names, best = await B.meshitero_winners(d1, d2)
    check("飯テロ賞: 👏2の写真が勝ち", (names, best), (["はやおき"], 2))

    # meta の upsert
''')
io.open("test_bot.py", "w", encoding="utf-8", newline="\n").write(t)
print("patched test_bot.py")
