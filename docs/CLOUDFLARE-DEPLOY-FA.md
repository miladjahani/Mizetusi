# استقرار کامل NEXUS روی کلودفلر (Worker + Container)

این راهنما کل پنل را **روی خود کلودفلر** بالا می‌آورد: هیچ سرور دیگری، هیچ پروژه‌ی Railway، هیچ VPS. سه چیز با هم کار می‌کنند:

- **Worker** — تنها آدرس عمومی شماست. کلودفلر آن را روی شبکهٔ anycast خودش سرو می‌کند، پس IP‌ای که کاربر می‌بیند IP تمیز کلودفلر است، نه IP سرور.
- **Container** — همان اپلیکیشن خودمان (تصویر Docker این مخزن: Xray، sing-box، mihomo، mtg، mtproto-proxy و پنل FastAPI) که کلودفلر روی شبکه‌اش اجرا می‌کند. Worker با یک بایندینگ (`NEXUS_CONTAINER`) به آن می‌رسد.
- **Postgres** — تنها چیزی که باید بیرون از کلودفلر بماند. بدون آن هم پنل بالا می‌آید، ولی داده‌ها با خواب‌رفتن Container از بین می‌روند (بخش ۳).

> **یک واقعیت که باید اول بدانید:** Worker فقط JavaScript/WASM اجرا می‌کند؛ نه Python، نه پروسه، نه Xray. پس «کل برنامه داخل خود Worker» از نظر فنی وجود ندارد. آنچه وجود دارد **Container** است: ماشین لینوکس واقعیِ کلودفلر که از `Dockerfile` همین مخزن ساخته می‌شود و Worker جلوی آن می‌ایستد. این راهنما همان است، و از نظر نتیجه «همه‌چیز روی کلودفلر» می‌شود.

زمان کل: با راه صفر (وصل‌کردن مخزن) حدود ۵ دقیقه کار دستی، به‌علاوهٔ اولین build که چند دقیقه طول می‌کشد.

---

## ۰. راه پیشنهادی: مخزن را وصل کن، خودش دپلوی می‌شود (بدون هیچ سکرتی)

اگر این مخزن را به کلودفلر وصل کنی، از آن لحظه **هر push روی شاخهٔ `main` خودش Worker و Container را می‌سازد و منتشر می‌کند** — بدون توکن، بدون سکرت، و بدون اینکه روی کامپیوتر خودت Docker داشته باشی. کلودفلر توکن لازم برای build را خودش می‌سازد و ایمیج Container را هم در محیط build خودش می‌سازد.

1. Cloudflare Dashboard → **Workers & Pages** → **Create application** → کنار **Import a repository** روی **Get started** بزن.
2. حساب GitHub را وصل کن و مخزن `miladjahani/Mizetusi` را انتخاب کن.
3. نام Worker **باید** `nexus-panel` باشد — دقیقاً همان نامی که در `wrangler.jsonc` نوشته شده. (کلودفلر صریح گفته: اگر نام Worker در داشبورد با نام داخل فایل تنظیمات یکی نباشد، build شکست می‌خورد.)
4. تنظیمات build را **دقیقاً** این‌طور بگذار:

   | تنظیم | مقدار |
   |---|---|
   | Production branch | `main` |
   | Root directory | `/` (ریشهٔ مخزن) |
   | **Build command** | **کاملاً خالی** — اگر کلودفلر خودش چیزی مثل `bun run dev` یا `npm run dev` گذاشته، پاکش کن |
   | Deploy command | `npx wrangler deploy` (پیش‌فرض) |

   > **چرا Build command باید خالی باشد:** `bun run dev` یا `npm run dev` معنی‌اش `wrangler dev` است — یک سرور محلی که **هیچ‌وقت تمام نمی‌شود**. بعد از آن کلودفلر منتظر می‌ماند تا build تمام شود، پس هیچ‌وقت به مرحلهٔ deploy نمی‌رسد؛ حتی وقتی تصویر Docker با موفقیت ساخته شده است. نشانه‌اش هم این خط در انتهای build است: `Ready on http://localhost:8787`.
   >
   > **ولی از این به بعد یک اشتباه در این خانه deploy را نمی‌خورد:** اسکریپت `dev` در پروژه به `cloudflare-containers/dev.mjs` وصل است و در محیط build (که `WORKERS_CI` ست شده) خودش را رد می‌کند و با موفقیت خارج می‌شود، پس مرحلهٔ deploy همان‌جا اجرا می‌شود. پاک‌کردن Build command هنوز درست‌تر و سریع‌تر است (چون دیگر یک build بی‌دلیل هم راه نمی‌افتد).

5. **Save and Deploy**.

همین. اولین build چند دقیقه طول می‌کشد: کلودفلر ایمیج را **در محیط build خودش** می‌سازد (نه روی کامپیوتر تو) و بعد به رجیستری خودش push می‌کند. روی استقرار موفق، لاگ build را با این خط‌ها می‌بینی:

```
Installing project dependencies: npm clean-install
⎔ Container image(s) ready
```

بعد از آن، از **Deployments → View build history** وضعیت و لاگ build را می‌بینی. اگر build با شکست تمام شد یا تمام نشد، جدول بخش **۱۲** را ببین.

### چرا «بدون هیچ سکرت» واقعاً بدون سکرت است

| چیز | چطور حل می‌شود |
|---|---|
| توکن API برای deploy | کلودفلر خودش برای هر Workers Builds یکی می‌سازد؛ لازم نیست چیزی بسازی یا جایی بچسبانی |
| `JWT_SECRET` | خود اپ در اولین بوت یک راز ۴۸ بایتی می‌سازد و در دیتابیس ذخیره می‌کند (`bootstrap()` در `app/main.py`)، پس نشست‌ها بدون هیچ متغیری امضا می‌شوند |
| `ADMIN_PASSWORD` | اگر ست نشده باشد رمز ورود `admin` است — **بلافاصله بعد از اولین ورود از داخل پنل عوضش کن** |
| `PUBLIC_BASE_URL` | لازم نیست؛ بعد از ساختن دامنهٔ اختصاصی، همان دامنه را در تب «کلودفلر» پنل ذخیره کن |
| `DATABASE_URL` | **لازم نیست، ولی ارزش دارد**: دیسک Container موقتی است، پس بدون Postgres کاربران و حجم مصرفی با خواب‌رفتن یا هر deploy پاک می‌شود. هر وقت خواستی، بعداً در Settings → Variables & Secrets اضافه‌اش کن؛ به pipeline ربطی ندارد |

بعد از این مرحله فقط دو کار دستی می‌ماند که در بخش‌های **۶** و **۷** آمده‌اند: وصل‌کردن **دامنهٔ اختصاصی** و ذخیرهٔ همان دامنه در تب «کلودفلر» پنل. اگر ترجیح می‌دهی از کامپیوتر خودت (با Docker) دپلوی کنی، بخش **۵** همان راه دوم است.

### چند نکتهٔ همین راه

- هر push به `main` یک ایمیج جدید می‌سازد و نمونه‌های Container را به‌روز می‌کند (چند دقیقه). push به شاخه‌های دیگر فقط Worker را آپلود می‌کند و ایمیج را دست نمی‌زند.
- سهمیهٔ تصاویر هر حساب ۵۰ گیگابایت است؛ `npx wrangler containers images list` و `npx wrangler containers delete` برای تمیزکاری.
- این مسیر هیچ‌کدام از فایل‌های `.env` یا سکرت روی مخزن را لازم ندارد و چیزی از آن‌ها را روی گیت‌هاب نمی‌گذارد.

---

## ۱. چه چیزی کار می‌کند و چه چیزی نه

یک Container فقط از طریق Worker و روی HTTP/HTTPS دیده می‌شود: **پورت TCP خام و UDP ندارد**. دقیقاً مثل Render بدون TCP Proxy. پس:

| قابلیت | وضعیت | چرا |
|---|---|---|
| خود پنل (صفحه‌ها، API، ورود) | ✅ | از طریق Worker پروکسی می‌شود |
| لینک سابسکریپشن و صفحهٔ وضعیت کاربر | ✅ | همان مسیر |
| VLESS · VMess · Trojan روی WebSocket (`/ws/…`، `/cdn/…`) | ✅ | همه روی ۴۴۳ و WebSocket |
| Shadowsocks (چهار cipher) و نود WARP | ✅ | همان |
| پروکسی WEB تلگرام (`tg://webproxy`) | ✅ | حاملش همین ۴۴۳ است و پورت خام نمی‌خواهد |
| پروکسی `web.telegram.org` (`/tg/…`) | ✅ | مسیر HTTP روی همان دامنه |
| **Reality** | ❌ | پورت TCP خام لازم دارد |
| **AnyTLS · TUIC · MTProto (`tg://proxy`)** | ❌ | پورت TCP/UDP خام لازم دارند |
| **پروکسی HTTP/SOCKS5 تلگرام** | ❌ | پورت ۸۴۴۸/۸۴۴۹ عمومی وجود ندارد |

پنل این را خودش می‌فهمد: چون `CLOUDFLARE_DEPLOYMENT_ID` را کلودفلر تزریق می‌کند، پلتفرم به‌صورت **«Cloudflare (Worker + Container)»** گزارش می‌شود و ترنسپورت‌های پورت‌دار منتشر نمی‌شوند (به‌جای اینکه لینک مرده بدهند). اگر Reality یا MTProto برایتان مهم است، مسیر درست VPS است: [docs/VPS-DEPLOY-FA.md](VPS-DEPLOY-FA.md).

---

## ۲. پیش‌نیازها

**برای راه صفر (وصل‌کردن مخزن) فقط دو چیز لازم است:**

| مورد | چرا |
|---|---|
| حساب کلودفلر + پلن **Workers Paid** (۵ دلار در ماه) | Containerها فقط روی پلن پولی فعال‌اند |
| یک **دامنه در همان حساب کلودفلر** | بدون custom domain، Worker روی `*.workers.dev` می‌ماند و آن پسوند در ایران فیلتر است |

**فقط برای راه دوم، یعنی دپلوی از کامپیوتر خودت** (و برای داشتن داده‌ی ماندگار):

| مورد | چرا |
|---|---|
| **Docker** روی همان کامپیوتر | `wrangler deploy` تصویر را همان‌جا build و به رجیستری کلودفلر push می‌کند |
| **Node.js 18+** و `npm` | برای wrangler |
| یک دیتابیس **Postgres** (بخش ۳) | تنها ذخیره‌سازی‌ای که زنده می‌ماند |
| حدود ۱ گیگابایت آپلود | اولین build تصویر |

```bash
docker info            # باید جواب بدهد، نه خطای daemon
node -v && npm -v
```

---

## ۳. اول دیتابیس، بعد deploy

دیسک یک Container **موقتی** است: با خواب‌رفتن نمونه و با هر deploy پاک می‌شود. یعنی اگر پنل روی SQLite داخلی بماند، هر بار که Container می‌خوابد کاربران، حجم مصرفی، توکن سابسکریپشن‌ها و تنظیمات از بین می‌رود. برای همین یک Postgres بیرونی لازم است — هر سرور Postgres کار می‌کند و `DATABASE_URL` تنها چیزی است که پنل می‌خواهد.

یک Postgres مدیریت‌شده بردارید (مثلاً Neon یا Supabase؛ هر دو پلن رایگان دارند و در ایران هم از طریق کلاینت وصل می‌شوند) و رشتهٔ اتصال را بردارید؛ شکلش این است:

```
postgresql://user:password@host/dbname?sslmode=require
```

برای اطمینان از این‌که رشتهٔ اتصال واقعاً به اپ رسیده، `npx wrangler secret list` باید `DATABASE_URL` را نشان بدهد. در پنل هم تب **کلودفلر** (جعبهٔ وضعیت اجرا، بالای «منابع لبه و لوکیشن‌ها») پلتفرم را **«Cloudflare (Worker + Container)»** نشان می‌دهد و مسیر دیتابیس محلی را می‌نویسد.

---

## ۴. گرفتن کد

```bash
git clone https://github.com/miladjahani/Mizetusi.git
cd Mizetusi
```

---

## ۵. راه دوم: استقرار از کامپیوتر خودت با یک دستور

> اگر بخش **۰** را رفته‌ای (مخزن را به کلودفلر وصل کرده‌ای) این بخش را رد کن؛ آن راه خودش دپلوی می‌کند و این یکی فقط برای دپلوی دستی از سیستم خودت یا عیب‌یابی است.

```bash
npm install                # وابستگی‌های Worker (wrangler و @cloudflare/containers)
npx wrangler login         # یک‌بار، در مرورگر
sh cloudflare-containers/deploy.sh --db 'postgresql://user:pass@host/db?sslmode=require'
```

اسکریپت این کارها را می‌کند:

1. وجود Docker و Node را چک می‌کند؛
2. `npm install` و لاگین را انجام می‌دهد؛
3. **secret**ها را می‌سازد: `ADMIN_PASSWORD` تصادفی و `JWT_SECRET` (و `DATABASE_URL` را همان‌جا که دادید). رمزی که قبلاً ست شده باشد هرگز بازنویسی نمی‌شود؛ پس اجرای دوباره، پسورد زندهٔ شما را عوض نمی‌کند؛
4. `wrangler deploy` را اجرا می‌کند: تصویر Docker ساخته و push می‌شود، Container بالا می‌آید و Worker منتشر می‌شود.

خروجی، پسورد ادمین را **یک‌بار** نشان می‌دهد. یادداشتش کنید.

اگر `--domain panel.example.com` هم بدهید، `PUBLIC_BASE_URL` روی همان دامنه ست می‌شود تا پنل از همان اولین بوت نود اصلی و همهٔ لینک‌ها را روی آدرس درست بسازد:

```bash
sh cloudflare-containers/deploy.sh --domain panel.example.com --db 'postgresql://…'
```

**اولین deploy چند دقیقه طول می‌کشد و بعد از آن هم کلودفلر چند دقیقه وقت می‌خواهد تا نمونه‌ها آماده شوند.** در این فاصله Worker جواب می‌دهد اما درخواست‌های داخل Container ممکن است خطا بدهند (`/health` وضعیت را نشان می‌دهد).

---

## ۶. دامنهٔ اختصاصی (مهم‌ترین قدم برای ایران)

1. Workers & Pages → Worker شما (`nexus-panel`) → Settings → **Domains & Routes**.
2. **Add custom domain** → مثلاً `panel.example.com`.
3. کلودفلر خودش گواهی TLS را صادر می‌کند.

بدون این قدم، Worker روی `nexus-panel.<sub>.workers.dev` جواب می‌دهد و آن پسوند کل در ایران فیلتر است — یعنی یک آدرس دوم که فقط با VPN باز می‌شود.

---

## ۷. تنظیم پنل

1. `https://panel.example.com` را باز کنید و با `ADMIN_PASSWORD` وارد شوید.
2. تب **کلودفلر** → **Cloudflare Worker** → همان دامنهٔ اختصاصی را ذخیره کنید و **«ذخیره و Sync»**.
3. **«پینگ همه نودها»** را بزنید تا نودها و لینک‌های سابسکریپشن روی همین دامنه ساخته شوند.
4. یک کاربر بسازید و لینکش را در کلاینت تست کنید.

از این لحظه هر لینکی که پنل به کاربر می‌دهد (سابسکریپشن، صفحهٔ وضعیت، دانلود) روی `panel.example.com` ساخته می‌شود.

---

## ۸. بررسی سلامت و لاگ‌ها

```bash
curl -s https://panel.example.com/health | jq .
```

```json
{
  "ok": true,
  "worker": "nexus-edge-6",
  "mode": "container",
  "container": true,
  "origin": null,
  "paths": [ "…" ],
  "panel": true
}
```

با `?probe=1` وورکر یک درخواست واقعی هم به Container می‌زند و همان را گزارش می‌کند (دکمهٔ «تست ورکر» در پنل همین را صدا می‌زند).

```bash
npx wrangler containers list     # وضعیت نمونه‌های Container
npx wrangler tail               # لاگ زندهٔ Worker
```

لاگ خود اپلیکیشن (uvicorn) در Workers & Pages → Containers → **Logs** است.

---

## ۹. متغیرها و تنظیمات

| نام | کجا | توضیح |
|---|---|---|
| `ADMIN_PASSWORD` | secret | رمز ورود پنل؛ بدونش پنل روی `admin` بالا می‌آید |
| `JWT_SECRET` | secret | امضای نشست؛ عوض‌کردنش همهٔ دستگاه‌ها را خارج می‌کند |
| `DATABASE_URL` | secret | Postgres — تنها ذخیره‌سازی ماندگار |
| `PUBLIC_BASE_URL` | secret | اختیاری: دامنهٔ اختصاصی Worker |
| `ALLOWED_HOSTS` | `wrangler.jsonc` → `vars` | اختیاری: لیست Host مجاز برای مسیرهای WebSocket |
| `instance_type` | `wrangler.jsonc` → `containers` | `basic` (۱ گیگ رم / ۴ گیگ دیسک) ارزان‌تر، `standard-1` (۴ گیگ / ۸ گیگ) پیش‌فرض، `standard-2` برای استفادهٔ عمومی |
| `max_instances` | `wrangler.jsonc` → `containers` | **۱** — پنل یک دیتابیس دارد؛ دو نمونه یعنی دو پنل جدا |

ست‌کردن دستی secret:

```bash
npx wrangler secret put ADMIN_PASSWORD
npx wrangler secret list
```

---

## ۱۰. به‌روزرسانی نسخه

```bash
git pull
npm run deploy
```

تصویر دوباره ساخته می‌شود و Container با نسخهٔ جدید بالا می‌آید. چون دیتابیس بیرونی است، کاربران و حجم مصرفی دست‌نخورده می‌مانند.

---

## ۱۱. هزینه و محدودیت‌ها

- **پلن Workers Paid** لازم است (۵ دلار در ماه) و مصرف Container جداگانه بر اساس vCPU-ثانیه، حافظه-ثانیه و دیسک حساب می‌شود.
- **`sleepAfter = '1h'`** در `cloudflare-containers/worker.js`: یک ساعت بی‌کاری، Container خاموش می‌شود. هر تونل زنده همیشه فعالیت را تازه می‌کند، پس وسط دانلود چیزی قطع نمی‌شود؛ ولی یک نصب بی‌استفاده پول اضافه نمی‌دهد.
- **تصویر باید در دیسک آن instance type جا شود** («Image size: Same as instance disk space»). دیسک `basic` چهار گیگ است و برای این تصویر کافی است.
- **پورت ورودی خام و UDP وجود ندارد** — همان جدول بخش ۱.
- **حساب‌های فیلترشده**: Cloudflare شبکهٔ خودش را برای حساب‌های Enterprise و سرویس‌های خاص محدود می‌کند (بند ۲.۸ شرایط استفاده دربارهٔ استفادهٔ CDN برای سرویس پروکسی/VPN). تصمیم و ریسکش با شماست؛ اگر می‌خواهید سرویس کاملاً زیر کنترل خودتان باشد، راهنمای VPS همان کار را بدون این محدودیت انجام می‌دهد.

---

## ۱۲. عیب‌یابی

| نشانه | دلیل | راه‌حل |
|---|---|---|
| `/health` می‌گوید `503 NEXUS_ORIGIN is not configured and no NEXUS_CONTAINER is bound` | بایندینگ Container در deploy نبوده | مطمئن شوید `wrangler.jsonc` ریشه را deploy می‌کنید (همان‌جایی که `containers` و `durable_objects` تعریف شده‌اند) |
| `502 origin unreachable` | Container بالا نیامده یا هنوز آماده نیست | چند دقیقه صبر کنید، بعد `npx wrangler containers list` و لاگ Container |
| صفحهٔ پنل باز می‌شود ولی دوباره لاگین می‌خواهد | کوکی روی دامنهٔ دیگری ست شده | فقط از دامنهٔ اختصاصی وارد شوید، نه `workers.dev` |
| بعد از یک ساعت بی‌کاری، کاربر‌ها/تنظیمات رفته‌اند | روی SQLite داخلی بودید | `DATABASE_URL` را ست کنید (بخش ۳) |
| `docker info` خطا می‌دهد | Docker اجرا نیست | Docker Desktop / colima را بالا بیاورید؛ بدونش تصویر build نمی‌شود |
| خطای حجم تصویر | instance type کوچک است | `instance_type` را در `wrangler.jsonc` یک درجه بالا ببرید |
| خطای architecture در build | روی مک Apple Silicon ایمیج باید amd64 باشد | در Docker Desktop گزینهٔ Rosetta/`linux/amd64` را فعال کنید |
| build در کلودفلر با خطای نام شکست می‌خورد | نام Worker در داشبورد با `name` داخل `wrangler.jsonc` یکی نیست | Worker را دقیقاً `nexus-panel` نام‌گذاری کن (بخش ۰) |
| build تمام نمی‌شود و آخرین خطش `Ready on http://localhost:8787` است | Build command روی `bun run dev` / `npm run dev` مانده — یک dev server که هیچ‌وقت خارج نمی‌شود | Settings → Builds → Build command را خالی کن، Deploy command را `npx wrangler deploy` بگذار، ذخیره و Retry |
| build روی نصب وابستگی‌ها شکست می‌خورد | نسخهٔ Node محیط build قدیمی است | در Settings → Builds نسخهٔ Node را ۲۰ یا بالاتر بگذار؛ `package-lock.json` در مخزن هست تا نصب قطعی باشد |
| preview روی شاخه‌های غیر `main` کار نمی‌کند | Workerای که Durable Object/Container دارد Version URL نمی‌گیرد | Preview command را `npx wrangler versions upload` بگذار، یا فقط روی `main` کار کن |
| سهمیهٔ تصاویر پر شده | هر build یک ایمیج جدید می‌سازد (سقف هر حساب ۵۰ گیگ) | `npx wrangler containers images list` و بعد `npx wrangler containers delete` |
| دامنه فقط با VPN باز می‌شود | custom domain وصل نشده | بخش ۶ |
| `npx wrangler deploy` می‌گوید لاگین نیستید | توکن منقضی شده | `npx wrangler login` |

---

## ۱۳. دو نکته‌ای که دامنه را زنده نگه می‌دارد

1. **`ADMIN_PASSWORD` را ست نگه دارید** (اسکریپت خودش این کار را می‌کند).
2. **پروکسی WEB تلگرام را روشن نگذارید** مگر لازمش داشته باشید: حاملش دامنهٔ خود پنل است، و دامنه‌ای که ترافیک پروکسی تلگرام سرو می‌کند همان دامنه‌ای است که یک شبکه می‌تواند بشناسد و کل دامنه را ببندد.

---

## پیوست: شکل دوم استقرار روی کلودفلر

اگر اپلیکیشن را جای دیگری (Railway، VPS، Render) اجرا می‌کنید و فقط می‌خواهید Worker جلوی آن باشد، لازم نیست Container بسازید: همان `cloudflare-worker/worker.js` را در داشبورد paste کنید و `NEXUS_ORIGIN` را به آدرس اپ بدهید. توضیح کامل: [../cloudflare-worker/README.md](../cloudflare-worker/README.md).

هر دو شکل یک فایل پروکسی و یک تست دارند: `tests/worker_smoke.mjs` هم مسیر «اپ جای دیگری است» و هم مسیر «اپ در Container است» را روی همان کد اجرا می‌کند.
