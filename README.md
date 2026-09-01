# 最低限生活リズムサークル Bot

起床・睡眠・家事・食事・入浴を **ボタン1タップ** で記録し、毎晩の判定で
「自分で決めた最低限」を守れなかった人を `#叱責👹` に名指しで晒す Discord Bot。
データは VM 上の SQLite に保存（外部サービス・課金なし）。

## できること（MVP）

| チャンネル | パネル / 動作 |
|---|---|
| `#起床🌅` | ☀️起きた／🌙おやすみ。睡眠時間は自動計算（おやすみを押し忘れたら起床時に聞かれる） |
| `#ごはん🍚🍚` | 🍚朝 🍱昼 🍽️夜 🍩間食 → 何を食べたか入力。**写真を投げるだけでも時間帯から自動記録** |
| `#家事🧹` | 🍳料理 🧹掃除 🍽️皿洗い ＋ 洗濯5工程（洗濯機を回す／取り込む／乾燥機を回す／取り込む／干す） |
| `#おふろ🛁` | 🛁お風呂入った |
| `#叱責👹` | 毎晩 `JUDGE_HOUR`（既定23時）に判定。未達の人を `@メンション` ＋ `:こら:` 絵文字で晒す |
| `🔊ラジオ体操🏃` | 毎朝 `RADIO_TIME`（既定6:30）に1分前予告→Botが入室して音源を再生。再生中にいた人を「参加」として記録。`#起床🌅` の🏃ボタンで呼び出しメンションON/OFF |
| `#課題📚` | `/kadai add` で登録した課題を、**同じ科目の履修者だけ**にメンション通知。3日前・前日・当日 `REMIND_HOUR`（既定8時）に未完了の人へリマインド。投稿の ✅ で完了 |
| `#つうしんぼ📮` | 毎週日曜の判定後に、その週の**達成率ランキング**と各賞（👑皆勤・🌅早起き・🛌ぐっすり・🧹家事・🍚ごはん・🛁きれい好き・🏃ラジオ体操・🐷寝坊・👹こら）を投稿 |
| `#設定🔧` | `/saitei` の使い方。設定更新も流れる。📝 今日のチェック（個人項目） |
| `#自己紹介🙋` | 📝 ボタン（または `/jikoshokai`）→ 5項目のフォーム → 自己紹介カードを投稿（再投稿で上書き。起床目標・連続達成・履修科目数・ロールを自動で添付）。リアクションで 学部（🏭工学部/🌱農学部）・学年（1〜4年+）・生活形態（🏠一人暮らし/👪実家/🛏寮）のロールを付け外し（同じ枠は1つだけ）。ロールは `/setup` が自動作成＝Botに**ロールの管理**権限が必要 |
| `#はじめに📖` | 参加者向けチュートリアル（読み取り専用・`/setup` で自動投稿＆更新）。`/help` でいつでも呼び出せる |

パネルは報告があるたびにチャンネルの一番下に置き直されるので、スクロールせずに押せます。

### コマンド
- `/help` 使い方ガイド（自分にだけ表示）
- `/jikoshokai` 自己紹介を書く／更新する
- `/setup` 【管理者】カテゴリ＋チャンネル一式＋パネルを作成（何度実行してもOK。旧名のチャンネルがあれば絵文字つきの名前に改名して引き継ぐ）
- `/saitei kishou:7:00 suimin:6 nyuyoku:True shokuji:2 kaji:3` 自分の最低限を設定（指定した項目だけ更新・`なし`/`0`で解除）。同じ起床時刻の仲間も表示
- `/nakama` 起床時刻ごとの仲間一覧
- `/kiroku` 自分の今日・今週の記録
- `/hantei` 【管理者】今すぐ判定（テスト用）
- `/tsushinbo` 【管理者】今週の通信簿を今すぐ投稿（`tsuki:True` で月間表彰）
- `/oyasumi riyuu:帰省 hi:10/15-10/17` お休み申告（その日は判定されず連続達成も途切れない。`riyuu:なし` で取り消し）
- `/kojin add namae:薬を飲む`／`remove`／`list` 自分だけの最低限項目。`#設定🔧` の 📝 ボタンで毎日チェック（未チェックは判定対象）
- `/saitei kyujitsu:2` 土日は起床締切を2時間遅らせる
- `/rajio` 【管理者】今すぐラジオ体操を流す（音が出るかのテスト用）
- `/jikanwari add kamoku:<科目名で検索>`（最大5つ同時）／`list`／`remove` 履修科目の登録。マスタに無い科目は入力した名前で新規登録できる
- `/kadai add kamoku:<自分の履修科目> kigen:10/15 naiyou:レポート提出`／`list`／`done`／`delete` 課題の登録・確認・完了・取り下げ

### 課題リマインドの仕組み（ロールは使わない）
- 科目マスタ `data/courses_2026_kouki.json` を **時間割PDF（工学部・農学部 2026後期）から自動生成**（`data/build_courses.py`、609科目）。内部キーは時間割コード、ユーザーは科目名で検索するだけ
- 「誰が何を履修しているか」を Bot が持ち、課題は同じ科目の履修者を個別メンション。Discordのロール上限（250）に縛られない
- 学期が変わったら新しいPDFを `data/src/` に置いて `PYTHONUTF8=1 .venv/Scripts/python data/build_courses.py` を再実行（要 `pip install pdfplumber`）

### 判定ルール
- ☀️ 起床：締切までに起床報告が無い／締切を過ぎていたら「寝坊」
- 🌙 睡眠：起床時に算出した睡眠時間が最低値未満（未記録も未達）
- 🛁 入浴：当日の報告が無い
- 🍚 食事：朝・昼・夜のうち報告した種類数が最低回数未満（間食は数えない）
- 🧹 家事：**日曜のみ**、その週（月〜日）の回数が最低回数未満
- 🏃 ラジオ体操：`rajio:True` の人は、その朝の再生中にVCにいなかったら未達

## セットアップ

### 1) Discord Bot を作る（Developer Portal）
1. https://discord.com/developers/applications → New Application
2. **Bot** → Reset Token でトークン取得
3. **Bot → Privileged Gateway Intents** で **MESSAGE CONTENT INTENT を ON**（#ごはん🍚 の写真検知に必要）
4. **OAuth2 → URL Generator**：scopes = `bot` + `applications.commands`、
   permissions = Manage Channels / View Channels / Send Messages / Embed Links / Attach Files / Read Message History / Add Reactions
   （permissions 整数 `117840`）→ 生成URLでサーバーに招待

### 2) サーバー側の準備
- 新規参加者を自動で歓迎したい場合は Developer Portal で **SERVER MEMBERS INTENT** を ON にし、`.env` に `MEMBERS_INTENT=1`（OFFのまま1にすると起動できません）
- カスタム絵文字 **`こら`**（叱責用・無ければ👹）と **`えらい`**（達成用・無ければ✨）を登録
- Botのロールに **ロールの管理** を付与し、ロール一覧で Bot のロールを 学部/学年/生活形態 ロールより**上**に置く（`/setup` がロールを作成・リアクションで付け外し）
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

### 音源（ラジオ体操）の置き方
権利の都合でリポジトリには含めません。手元のmp3を VM に直接コピーします（PCのPowerShellで）:
```powershell
scp -i "$HOME\.ssh\oracle.key" "D:\Downloads\videoplayback.mp3" ubuntu@158.179.180.17:/home/ubuntu/seikatsu-bot/radio.mp3
```
ファイル名を変える場合は `.env` の `RADIO_MP3` を合わせる。音声再生には `ffmpeg` と `libopus0` が必要（`setup.sh` で導入。既存VMは `sudo apt-get install -y ffmpeg libopus0 libffi-dev` と `venv/bin/pip install -r requirements.txt`）。

### 更新
```bash
cd ~/seikatsu-bot && git pull && sudo systemctl restart seikatsu-bot
```

### バックアップ
記録は `seikatsu.db` 1ファイル。`scp` でコピーするだけ。

### 連続達成（ストリーク）
判定で未達ゼロの日が続くと 🔥連続日数が伸びる（記録の無い日で途切れる。`/oyasumi` したお休みの日は飛ばして継続）。3・7・14・30・50・100日でお祝い投稿。`/kiroku` に現在と自己ベストを表示。

### 起床時のダイジェスト・朝の天気
☀️を押した返事に、その日の授業（履修登録した科目の曜日時限・教室）と未完了の課題を添える。ラジオ体操の予告には Open-Meteo の天気（`WEATHER_LAT/LON`、既定=府中）。

### 週間グラフ・月間表彰
通信簿に起床時刻・睡眠時間の折れ線グラフ（matplotlib。日本語ラベルには `fonts-noto-cjk` が必要＝`setup.sh` で導入。既存VMは `sudo apt-get install -y fonts-noto-cjk` と `venv/bin/pip install -r requirements.txt`）。月末の判定後に 🏆月間MVP と月間各賞。

### みんなで暗記！！（gakushu-rpg）連携
`.env` に `GAKUSHU_SECRET`（Cloudflare側 `VC_SECRET` と同じ値）を入れると、毎晩の判定結果を `/api/seikatsu` へ送信。達成した日は 🎫メダル10枚＋連続ボーナス（7日+30…）、HUDの🌅生活ランキング／プロフィールに反映。gakushu-rpg 側は `npm run seikatsu:remote` → `npm run deploy` が必要。

## 今後の予定
- 使ってみて出てきた要望に応じて調整
