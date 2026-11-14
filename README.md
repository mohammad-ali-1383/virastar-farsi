# ویراستار متن فارسی

سرویس اصلاح و ویرایش خودکار متن فارسی با هوش مصنوعی. این سرویس املا، دستور زبان، نشانه‌گذاری، فاصله‌ها و نویسه‌آرایی متن را اصلاح می‌کند و می‌تواند آن را با حفظ لحن نویسنده یا به لحن رسمی و اداری بازنویسی کند. امکان دریافت خروجی Word با تنظیمات دلخواه کاربر نیز وجود دارد.

## قابلیت‌ها

- **ویرایش هوشمند متن فارسی:** اصلاح غلط املایی، دستور زبان، نشانه‌گذاری، نیم‌فاصله و فاصله‌گذاری.
- **دو شیوهٔ ویرایش، انتخاب‌شده پیش از ارسال درخواست:**
  - `preserve` («ویرایش با حفظ لحن»): فقط خطاها اصلاح می‌شود و لحن و سطح رسمی/محاوره‌ای نویسنده تغییر نمی‌کند.
  - `formal` («تبدیل به لحن رسمی و اداری»): متن با حفظ معنا به زبانی دقیق، مؤدبانه و اداری بازنویسی می‌شود.
- **خروجی Word (.docx) اختیاری:** ساخت فایل با انتخاب فونت، اندازهٔ قلم، فاصلهٔ خطوط، تراز متن، حاشیهٔ صفحه، شمارهٔ صفحه (و جای آن) و کادر دور صفحه.
- **نمایش مدل پاسخ‌دهنده:** پاسخ موفق شامل `model_used` و `provider_name` است و رابط کاربری مدل تولیدکنندهٔ نتیجه را نشان می‌دهد.
- **جایگزینی خودکار provider:** چند provider سازگار با OpenAI به‌صورت مرتب امتحان می‌شوند و در صورت خرابی یکی، درخواست به provider بعدی می‌رود.

## پشتهٔ فناوری

| بخش | فناوری |
|---|---|
| بک‌اند | Python 3.12+، FastAPI، Pydantic v2، `python-docx` |
| فرانت‌اند | React 19، TypeScript، Vite |
| تست | pytest (بک‌اند)، Vitest + Testing Library (فرانت‌اند) |
| استقرار | Docker و Docker Compose؛ Nginx در نسخهٔ تولید |

## ساختار پروژه

```text
.
├── backend/
│   ├── app/
│   │   ├── config.py              # تنظیمات و کشف provider slotها
│   │   ├── errors.py              # کدها و پاسخ‌های خطا
│   │   ├── schemas.py             # مدل‌های درخواست/پاسخ
│   │   ├── main.py                # اپ FastAPI، سلامت و ثبت روت‌ها
│   │   ├── routers/               # correct.py و export_docx.py
│   │   └── services/              # اصلاح، پرامپت‌ها، provider و ساخت docx
│   ├── tests/                     # تست‌های pytest (بدون فراخوانی واقعی provider)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── requirements-dev.txt
├── frontend/
│   ├── src/                       # App، دیالوگ‌ها، کلاینت API و تست‌ها
│   ├── .env.example
│   ├── Dockerfile
│   └── nginx.conf                 # سرویس تولید و پروکسی /api به بک‌اند
├── context/                       # فقط دو پرامپت زیر قابل commit هستند
│   ├── correction-prompt.md
│   └── correction-prompt-formal.md
├── docker-compose.yml             # محیط توسعه
├── docker-compose.prod.yml        # نسخهٔ تولید
├── .env.example                   # الگوی تنظیمات بک‌اند (بدون راز واقعی)
└── README.md
```

## پیش‌نیازها

- Python 3.12 یا بالاتر
- Node.js 20.19+ یا 22.12+ (نسخهٔ 24 پیشنهاد می‌شود)
- Docker و Docker Compose (اختیاری، برای اجرای کانتینری)

## راه‌اندازی محلی

ابتدا فایل تنظیمات را از الگو بسازید:

```bash
cp .env.example .env
```

سپس حداقل یک provider را در `.env` تنظیم کنید (بخش «متغیرهای محیطی» را ببینید). بدون یک slot کامل، درخواست اصلاح با خطای `503 PROVIDER_UNAVAILABLE` پاسخ می‌دهد.

### روش الف — Docker Compose (پیشنهادی)

```bash
docker compose up --build
```

- فرانت‌اند: <http://localhost:5173>
- بک‌اند: <http://localhost:8000>
- مستندات API: <http://localhost:8000/docs>
- بررسی سلامت: <http://localhost:8000/health>

### روش ب — اجرای محلی

بک‌اند:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate        # در ویندوز (Git Bash): source .venv/Scripts/activate
pip install -r requirements-dev.txt
uvicorn app.main:app --reload --port 8000
```

فرانت‌اند (در ترمینال دیگر):

```bash
cd frontend
npm install
cp .env.example .env
npm run dev
```

### محل فایل `.env`

فایل `.env` در ریشهٔ مخزن، هم برای Docker Compose و هم برای اجرای محلی بک‌اند استفاده می‌شود. مسیر فایل‌ها نسبت به پکیج بک‌اند حل می‌شود، بنابراین `cd backend && uvicorn ...` نیز همان فایل ریشه را می‌خواند. فایل `backend/.env` در صورت وجود خوانده می‌شود و بر فایل ریشه اولویت دارد. متغیرهای واقعی محیط همیشه بر محتوای `.env` اولویت دارند.

فرانت‌اند در اجرای محلی، `VITE_API_BASE_URL` را از `frontend/.env` می‌خواند. در Docker Compose این مقدار از سمت Compose تزریق می‌شود و در نسخهٔ تولید، Nginx درخواست‌های `/api/` را به بک‌اند پروکسی می‌کند.

## API

### `POST /api/v1/correct`

درخواست:

```json
{
  "text": "متن فارسی",
  "style": "preserve"
}
```

`style` اختیاری است و پیش‌فرض آن `preserve` است. فقط دو مقدار `preserve` و `formal` پذیرفته می‌شود؛ هر مقدار دیگری `400 INVALID_INPUT` برمی‌گرداند.

پاسخ موفق:

```json
{
  "corrected_text": "متن اصلاح‌شده",
  "model_used": "model-id",
  "provider_name": "slot-name"
}
```

`model_used` و `provider_name` نشان می‌دهند کدام slot واقعاً پاسخ را تولید کرده است (در صورت fallback، slot بعدی). متن باید پس از حذف فاصله‌های ابتدایی و انتهایی خالی نباشد و طول آن از `MAX_TEXT_LENGTH` بیشتر نباشد.

```bash
curl -X POST http://localhost:8000/api/v1/correct \
  -H "Content-Type: application/json" \
  -d '{"text": "سلام این یک متن ازمایشی است", "style": "preserve"}'
```

### `POST /api/v1/export-docx`

متن اصلاح‌شده و تنظیمات ظاهری را می‌گیرد و فایل `.docx` را با نام `virastar-export.docx` برمی‌گرداند. هر خط متن به یک پاراگراف راست‌به‌چپ تبدیل می‌شود.

```json
{
  "text": "متن اصلاح‌شده",
  "options": {
    "font": "Vazirmatn",
    "font_size": 14,
    "line_spacing": 1.5,
    "alignment": "justify",
    "margins": "normal",
    "page_numbers": true,
    "page_number_position": "center",
    "page_border": false
  }
}
```

مقادیر مجاز:

| گزینه | مقادیر | پیش‌فرض |
|---|---|---|
| `font` | `Vazirmatn` \| `Sahel` \| `Tahoma` | `Vazirmatn` |
| `font_size` | `12` \| `14` \| `16` \| `18` | `14` |
| `line_spacing` | `1.0` \| `1.15` \| `1.5` \| `2.0` | `1.5` |
| `alignment` | `right` \| `justify` \| `center` | `justify` |
| `margins` | `normal` \| `narrow` \| `wide` | `normal` |
| `page_numbers` | `true` \| `false` | `true` |
| `page_number_position` | `center` \| `right` | `center` |
| `page_border` | `true` \| `false` | `false` |

اگر `options` ارسال نشود، همان مقادیر پیش‌فرض بالا استفاده می‌شود. `page_number_position` فقط زمانی معنا دارد که `page_numbers` برابر `true` باشد.

### خطاها

همهٔ پاسخ‌های خطا شکل `{"error": "CODE", "message": "..."}` دارند:

- `400 INVALID_INPUT` برای ورودی نامعتبر (از جمله `style` یا گزینه‌های export نامعتبر)
- `400 TEXT_TOO_LONG` برای متن بلندتر از حد مجاز
- `404 NOT_FOUND` و `405 METHOD_NOT_ALLOWED` برای مسیر یا روش ناشناخته
- `500 INTERNAL_ERROR` برای خطای غیرمنتظره
- `503 PROVIDER_UNAVAILABLE` وقتی هیچ provider قابل استفاده‌ای پاسخ نمی‌دهد (محدودیت نرخ، احراز هویت، زمان‌اوت، خطای اتصال یا پاسخ خالی)

### زنجیرهٔ provider و fallback

Slotها بر اساس شمارهٔ `N` به ترتیب صعودی امتحان می‌شوند. خطاهای مربوط به provider باعث رفتن به slot بعدی می‌شوند؛ کل زنجیره حداکثر ۶۰ ثانیه فرصت دارد و slotی که کمتر از ۵ ثانیه زمان باقی‌مانده داشته باشد شروع نمی‌شود. slotهایی که خطای احراز هویت/پرداخت/دسترسی (`401/402/403`) یا محدودیت نرخ (`429`) می‌دهند، مدتی در حافظهٔ همان فرآیند کنار گذاشته می‌شوند (به‌ترتیب ۳۰۰ و ۳۰ ثانیه). هیچ نتیجهٔ ساختگی برگردانده نمی‌شود و متن خام هرگز به‌عنوان اصلاح‌شده تحویل داده نمی‌شود.

`backend/app/services/prompt.py` بر اساس سبک درخواست، یکی از دو پرامپت ثابت را انتخاب می‌کند که با دو فایل `context/correction-prompt.md` و `context/correction-prompt-formal.md` هم‌خوان نگه داشته می‌شوند.

## متغیرهای محیطی

| متغیر | حوزه | پیش‌فرض | کاربرد |
|---|---|---|---|
| `APP_ENV` | بک‌اند | `development` | برچسب محیط اجرا |
| `MAX_TEXT_LENGTH` | بک‌اند | `1000` | حداکثر طول متن پذیرفته‌شده |
| `CORS_ORIGINS` | بک‌اند | مبدأهای توسعه | آرایهٔ JSON مبدأهای مجاز مرورگر |
| `API_KEY_N` | بک‌اند | خالی | کلید محرمانهٔ slot شمارهٔ `N` |
| `BASE_URL_N` | بک‌اند | خالی | آدرس پایهٔ provider؛ مسیر `/chat/completions` به آن اضافه می‌شود |
| `MODEL_N` | بک‌اند | خالی | شناسهٔ مدل ارسالی به slot |
| `TIMEOUT_SECONDS_N` | بک‌اند | `20` | زمان‌اوت هر درخواست برای slot |
| `EXTRA_BODY_N` | بک‌اند | خالی | شیء JSON اختیاری برای افزودن به بدنهٔ درخواست |
| `NAME_N` | بک‌اند | `slot-N` | نام slot در لاگ‌ها و در `provider_name` |
| `VITE_API_BASE_URL` | فرانت‌اند | `http://localhost:8000` | آدرس بک‌اند برای مرورگر |

هر slot فقط وقتی معتبر است که `API_KEY_N`، `BASE_URL_N` و `MODEL_N` هر سه پر باشند. شماره‌ها می‌توانند پیوسته نباشند. کلیدها فقط در محیط بک‌اند نگهداری و فقط برای همان provider استفاده می‌شوند؛ هرگز در پاسخ، کد فرانت‌اند یا لاگ ظاهر نمی‌شوند.

نمونهٔ تنظیم دو slot (بدون کلید واقعی):

```bash
API_KEY_1=your-key-here
BASE_URL_1=https://api.z.ai/api/paas/v4
MODEL_1=<model-id>
NAME_1=zai
EXTRA_BODY_1={"thinking":{"type":"disabled"}}

API_KEY_2=your-key-here
BASE_URL_2=https://api.gapgpt.app/v1
MODEL_2=<model-id>
NAME_2=gapgpt
```

## جریان استفاده برای کاربر

1. متن فارسی را در کادر وارد کنید. شمارندهٔ صفحه حداکثر ۱۰۰۰ کاراکتر را نشان می‌دهد.
2. روی «اصلاح متن» بزنید.
3. در دیالوگ «شیوهٔ ویرایش متن»، «ویرایش با حفظ لحن» یا «تبدیل به لحن رسمی و اداری» را انتخاب کنید. تا انتخاب شما، هیچ درخواستی ارسال نمی‌شود.
4. متن اصلاح‌شده در پنل نتیجه نمایش داده می‌شود و مدل پاسخ‌دهنده زیر آن نشان داده می‌شود.
5. در صورت نیاز، «کپی» کنید یا با «دریافت فایل ورد» تنظیمات ظاهری Word را انتخاب و فایل را دانلود کنید.

## نکات و محدودیت‌ها

- فونت‌ها در فایل Word تعبیه (embed) نمی‌شوند؛ فونت‌های وزیرمتن و ساحل باید روی رایانهٔ کاربر نصب باشند تا در Word درست نمایش داده شوند.
- حد مجاز متن به‌صورت پیش‌فرض ۱۰۰۰ کاراکتر است و هم در فرانت‌اند و هم در بک‌اند اعمال می‌شود.
- محدودیت نرخ یا سهمیهٔ providerها می‌تواند به پاسخ `503 PROVIDER_UNAVAILABLE` منجر شود؛ در این حالت هیچ اصلاحی در دسترس نیست.
- متن‌ها در سرور ذخیره نمی‌شوند و دیتابیسی وجود ندارد. لاگ‌های محلی (در صورت وجود) در `.gitignore` قرار دارند و نباید commit شوند.
- کیفیت خروجی به provider و مدل انتخاب‌شده وابسته است.

## توسعه و تست

```bash
# بک‌اند
cd backend
python -m venv .venv
source .venv/bin/activate        # ویندوز (Git Bash): source .venv/Scripts/activate
pip install -r requirements-dev.txt
pytest

# فرانت‌اند
cd frontend
npm install
npm run test
npm run typecheck
npm run build
```

همهٔ تست‌های خودکار provider را mock می‌کنند؛ برای اجرای آن‌ها هیچ کلید API لازم نیست و هیچ درخواستی به شبکهٔ بیرونی ارسال نمی‌شود.

## وضعیت پروژه

این مخزن خصوصی است و در حال توسعه است. نسخهٔ فعلی شامل اصلاح متن با دو شیوهٔ ویرایش، خروجی Word قابل تنظیم، نمایش مدل پاسخ‌دهنده و زنجیرهٔ fallback بین providerهای سازگار با OpenAI است.
