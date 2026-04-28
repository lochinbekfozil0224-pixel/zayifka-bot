# 🤖 Zayavka qabul + Nakrutka bloklash boti

## 📁 Loyiha tarkibi

```
.
├── main.py            # Asosiy bot kodi
├── requirements.txt   # Kutubxonalar
├── Procfile           # Railway start buyruq
├── railway.toml       # Railway konfiguratsiya
├── runtime.txt        # Python versiyasi
├── .gitignore         # Git ga qo'shilmaydigan fayllar
└── README.md          # Shu fayl
```

## 🚀 Railway'ga deploy qilish

### 1-qadam: Fayllarni GitHub'ga yuklash

1. GitHub'da yangi repo yarating (private bo'lsin)
2. Hamma fayllarni yuklang
3. Railway'da loyihangizga kiring → **+ Create** → **GitHub Repo** → repongizni tanlang

### 2-qadam: Environment variable'larni qo'shish ⚠️ MUHIM

Railway dashboard'da → loyihangiz → **Variables** bo'limi → quyidagilarni qo'shing:

| Nom | Qiymat | Izoh |
|-----|--------|------|
| `BOT_TOKEN` | `8201...YQU` (sizning tokeningiz) | @BotFather'dan olingan token |
| `ADMIN_ID` | `8135915671` | Sizning Telegram ID raqamingiz |
| `DB_PATH` | `/data/bot.db` | DB fayli yo'li (volume uchun) |

> ⚠️ **Ogohlantirish:** Bot tokeningiz oldingi chatda ochiq turibdi. @BotFather → `/revoke` bilan **yangi token** oling va yuqorida shuni qo'ying!

### 3-qadam: Volume ulash 💾 ENG MUHIM

**SQLite ma'lumotlar bazasi (foydalanuvchilar, balanslar, vazifalar) Railway'da har redeploy paytida yo'qoladi**, agar volume ulamasangiz!

1. Service sozlamalari → **Volumes** tab
2. **+ New Volume**
3. Mount path: `/data`
4. Save

Endi `DB_PATH=/data/bot.db` env var bilan birga DB doimiy saqlanadi.

### 4-qadam: Deploy

Hamma narsa to'g'ri qo'yilgan bo'lsa, Railway o'zi build qiladi va botni ishga tushiradi.

**Loglarda** quyidagini ko'rsangiz — tayyor:
```
Bot ishga tushdi
```

Telegramda `/start` bosing.

---

## 🔧 Lokalda ishlatish

```bash
pip install -r requirements.txt
python main.py
```

`bot.db` fayli avtomatik yaratiladi.

---

## ⚙️ Bot funksiyalari

**Foydalanuvchi uchun:**
- 📥 **Zayavka qabul qilish** — kanalga kelgan so'rovlarni avto qabul qiladi (ilk 20k bepul, keyin 1k=1000 so'm)
- 🛡 **Nakrutka bloklash** — kanalga qo'shilayotgan fake/bot akkauntlarni avto chiqaradi
- 👤 **Profil** — balans, statistika, hisobni to'ldirish

**Admin uchun (⚙️ Admin panel):**
- 📊 Statistika
- 👥 Foydalanuvchilar ro'yxati
- ⚙️ Limit/narxlarni o'zgartirish
- 💳 Karta ma'lumotlari
- 📝 Botning matnlarini tahrirlash
- 📢 Majburiy obuna kanallari
- 📨 Foydalanuvchilarga xabar yuborish (broadcast)
- 📡 Bot admin bo'lgan kanallarga xabar yuborish
- 💰 To'lovlarni tasdiqlash/rad etish

---

## ❗ Muhim eslatmalar

1. **Bot kanalga admin** bo'lib qo'shilishi shart (`Add new members`, `Ban users`, `Manage join requests` huquqlari).
2. **Eski a'zolarni tekshirish** Bot API orqali iloji yo'q — bot faqat **yangi qo'shilayotgan** a'zolarni real-time skanerlaydi.
3. **`FAKE_THRESHOLD`** (kodda `35`) — bundan past ball olgan akkaunt nakrutka deb hisoblanadi. Agar:
   - Juda ko'p **odam ham chiqarilayotgan** bo'lsa → 25-30 ga tushiring
   - **Nakrutka o'tib ketayotgan** bo'lsa → 45 ga ko'taring

---

## 🐛 Xatolar

Loglarda xato chiqsa, **Railway dashboard → Deployments → View Logs** orqali ko'ring.

Agar bot javob bermasa:
1. `BOT_TOKEN` to'g'rimi?
2. Volume ulanganmi?
3. Boshqa joyda **shu token** bilan bot ishlamayaptimi? (Bir vaqtning o'zida bitta joyda ishlasa kerak — Telegram konflikt qiladi.)
