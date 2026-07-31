# CS 1.6 Telegram Bot

## 1. O'rnatish

```bash
python3 -m venv venv
source venv/bin/activate       # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

## 2. Sozlash (`config.py`)

- **BOT_TOKEN** — @BotFather dan olasiz (`/newbot`).
- **OWNER_ID** — sizning shaxsiy Telegram ID raqamingiz. @userinfobot ga
  `/start` yozib bilib olasiz. Bu odam hech qachon ban/kick/mute
  qilinmaydi — u har doim eng yuqori huquqqa ega.
- **SERVER_IP / SERVER_PORT** — allaqachon `84.54.82.234:27047` qilib
  qo'yilgan, kerak bo'lsa o'zgartiring.

## 3. Adminlarni sozlash (`admins.json`)

```json
{
  "admins": {
    "123456789": 5,
    "987654321": 3
  }
}
```

- Key — admin foydalanuvchining Telegram `user_id` raqami (matn ko'rinishida).
- Value — daraja (level). Raqam qancha katta bo'lsa, admin shuncha "kuchli".
- Level 5 admin — level 3 adminni ban/kick/mute qila oladi, lekin aksincha yo'q.
- Bir xil levelga ega ikkita admin bir-birini ban qila olmaydi.
- Bot egasi (`OWNER_ID`) bu faylga umuman yozilmaydi — u avtomatik eng
  kuchli hisoblanadi va uni hech kim (hatto boshqa yuqori levelli admin
  ham) ban/kick/mute qila olmaydi.

> Foydalanuvchi `user_id` sini bilish uchun @userinfobot yoki
> @getidsbot dan foydalaning.

## 4. Ishga tushirish

```bash
python3 bot.py
```

Serverda doimiy ishlashi uchun `systemd` yoki `screen`/`tmux` yoki
`pm2` orqali fon rejimida qoldiring. Masalan `systemd` bilan:

```ini
# /etc/systemd/system/csbot.service
[Unit]
Description=CS 1.6 Telegram Bot
After=network.target

[Service]
WorkingDirectory=/home/user/cs_bot
ExecStart=/home/user/cs_bot/venv/bin/python3 bot.py
Restart=always
User=user

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now csbot
```

## 5. Buyruqlar

### Hammaga ochiq
- `/info` — serverdagi kartani, IP'ni, o'yinchilar sonini va
  killarni (frag) chiroyli formatda ko'rsatadi.

### Faqat guruh adminlari uchun (guruhda ishlaydi)
- `.ban @user 1d spam qildi` yoki reply qilib `.ban 1d spam qildi`
- `.kick @user sabab` yoki reply qilib `.kick sabab`
- `.mute @user 1h spam` yoki reply qilib `.mute 1h spam`
- `.pin` — reply qilingan xabarni pin qiladi
- `.del` — reply qilingan xabarni o'chiradi (va buyruqning o'zini ham)

**Vaqt formati:** `10m` (daqiqa), `2h` (soat), `1d` (kun), `1w`
(hafta), yoki `doim` / `permanent` — cheksiz muddat uchun.

## 6. Muhim eslatmalar

- Bot guruhda **admin** bo'lishi shart (ban/kick/mute/pin/delete
  huquqlari bilan), aks holda amallar ishlamaydi — Telegram xato
  qaytaradi.
- `.ban`/`.kick`/`.mute` — @username orqali ishlashi uchun o'sha
  foydalanuvchi avval botga yozgan yoki guruhda ko'rinadigan bo'lishi
  kerak (Telegram cheklovi). Eng ishonchli usul — **reply qilib**
  buyruq yozish.
- A2S protokoli (server so'rovi) GoldSrc serverlarda "deaths"
  (o'lganlar soni) ni standart tarzda bermaydi — faqat "kill/frag"
  ko'rsatiladi. Agar sizga aniq deaths statistikasi kerak bo'lsa, buni
  serverning o'zida (masalan statsme yoki boshqa plagin orqali) alohida
  saqlab, botga API/log fayl orqali ulash kerak bo'ladi — aytsangiz shu
  qismini ham qo'shib beraman.
