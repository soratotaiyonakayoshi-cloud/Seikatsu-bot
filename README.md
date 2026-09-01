# 最低限生活リズムサークル Bot

起床・睡眠・家事・食事・入浴を **ボタン1タップ** で記録し、毎晩の判定で
「自分で決めた最低限」を守れなかった人を `#こら` に名指しで晒す Discord Bot。
データは VM 上の SQLite に保存（外部サービス・課金なし）。

## できること（MVP）

| チャンネル | パネル / 動作 |
|---|---|
| `#起床` | ☀️起きた／🌙おやすみ。睡眠時間は自動計算（おやすみを押し忘れたら起床時に聞かれる） |
| `#ごはん` | 🍚朝 🍱昼 🍽️夜 🍩間食 → 何を食べたか入力。**写真を投げるだけでも時間帯から自動記録** |
| `#家事` | 🍳料理 🧹掃除 🍽️皿洗い ＋ 洗濯5工程（洗濯機を回す／取り込む／乾燥機を回す／取り込む／干す） |
| `#おふろ` | 🛁お風呂入った |
| `#こら` | 毎晩 `JUDGE_HOUR`（既定23時）に判定。未達の人を `@メンション` ＋ `:こら:` 絵文字で晒す |
| `#設定` | `/saitei` の使い方。設定更新も流れる |

パネルは報告があるたびにチャンネルの一番下に置き直されるので、スクロールせずに押せます。

### コマンド
- `/setup` 【管理者】カテゴリ＋6チャンネル＋パネルを作成（何度実行してもOK）
- `/saitei kishou:7:00 suimin:6 nyuyoku:True shokuji:2 kaji:3` 自分の最低限を設定（指定した項目だけ更新・`なし`/`0`で解除）。同じ起床時刻の仲間も表示
- `/nakama` 起床時刻ごとの仲間一覧
- `/kiroku` 自分の今日・今週の記録
- `/hantei` 【管理者】今すぐ判定（テスト用）

### 判定ルール
- ☀️ 起床：締切までに起床報告が無い／締切を過ぎていたら「寝坊」
- 🌙 睡眠：起床時に算出した睡眠時間が最低値未満（未記録も未達）
- 🛁 入浴：当日の報告が無い
- 🍚 食事：朝・昼・夜のうち報告した種類数が最低回数未満（間食は数えない）
- 🧹 家事：**日曜のみ**、その週（月〜日）の回数が最低回数未満

## セットアップ

### 1) Discord Bot を作る（Developer Portal）
1. https://discord.com/developers/applications → New Application
2. **Bot** → Reset Token でトークン取得
3. **Bot → Privileged Gateway Intents** で **MESSAGE CONTENT INTENT を ON**（#ごはん の写真検知に必要）
4. **OAuth2 → URL Generator**：scopes = `bot` + `applications.commands`、
   permissions = Manage Channels / View Channels / Send Messages / Embed Links / Attach Files / Read Message History / Add Reactions
   （permissions 整数 `117840`）→ 生成URLでサーバーに招待

### 2) サーバー側の準備
- 叱り用のカスタム絵文字を **`こら`** という名前で登録（無ければ 👹 で代用されます）
- 招待後、管理者が `/setup` を実行 → チャンネルとパネルが自動で揃います

### 3) VM に配置（Oracle Cloud・他Botと同居OK）
```bash
git clone <このリポジトリ> seikatsu-bot
cd seikatsu-bot
bash setup.sh
cp .env.example .env && nano .env        # DISCORD_BOT_TOKEN を入力
venv/bin/python bot.py                   # 動作確認 → 「ログイン成功」で Ctrl+C
sudo cp seikatsu-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now seikatsu-bot
journalctl -u seikatsu-bot -f            # ログ
```

### 更新
```bash
cd ~/seikatsu-bot && git pull && sudo systemctl restart seikatsu-bot
```

### バックアップ
記録は `seikatsu.db` 1ファイル。`scp` でコピーするだけ。

## 今後の予定
- 課題リマインド（時間割→科目ロール→登録者に通知）
- 朝6:30 ラジオ体操（mezamashi-bot-v2 を音源差し替えで流用）
- 週次サマリ・連続達成・「みんなで暗記！！」ランキング連携
