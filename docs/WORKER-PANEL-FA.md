# پنل ورکر — پنل کامل روی Cloudflare رایگان

این تنها شکلی از این محصول است که **بدون سرور، بدون Container و بدون پلن
پرداختی** کار می‌کند. خودِ Worker هم پنل است، هم پروکسی:

* پنل گرافیکی روی همان آدرس Worker (ورود مدیر، ساخت کاربر، لینک، تنظیمات)
* تنظیمات در **KV** و کاربران در **D1**
* پروکسی **VLESS over WebSocket + TLS** که داخل خود Worker اجرا می‌شود

## چرا این یکی جواب می‌دهد و آن دو تای دیگر نه

| شکل استقرار | چه چیزی لازم دارد | مشکلش |
|---|---|---|
| `cloudflare-worker/` | یک origin پشتش | فقط پروکسی است؛ هر مسیرش `503` می‌دهد |
| `wrangler.jsonc` ریشه | **پلن Workers Paid** | پنل پایتونی در Container است؛ روی Free در `/containers/me` رد می‌شود |
| **همین پنل ورکر** | هیچ‌چیز | پنل = خود Worker، پروکسی = جاوااسکریپت |

Worker فقط جاوااسکریپت اجرا می‌کند، پس این استقرار همان چیزی را سرو می‌کند که
جاوااسکریپت می‌تواند: VLESS روی WebSocket و TLS. چیزی که پورت خام TCP یا UDP
خودش را می‌خواهد (Reality، TUIC، MTProto، وب‌پروکسی HTTP/SOCKS5) اینجا وجود
ندارد — و همین معامله است.

## استقرار

```bash
npx wrangler deploy --config worker-panel/wrangler.jsonc
```

**حتماً `--config` را بده.** wrangler فایل کانفیگ را در پوشه‌های بالاتر هم
می‌گردد و اگر `--config` ندهی، `wrangler.jsonc` ریشهٔ مخزن را پیدا می‌کند (همان
استقرار Container) و با خطای `@cloudflare/containers` می‌خورد.

اگر تازه می‌سازی، اول دو بایندینگ:

```bash
npx wrangler kv namespace create nexus-panel-kv   # id را در wrangler.jsonc بگذار
npx wrangler d1 create nexus-panel                # id را در wrangler.jsonc بگذار
npx wrangler d1 execute <database> --remote --file=worker-panel/schema.sql
```

اگر این مرحله را جا بیندازی هم مشکلی نیست: خود Worker در اولین درخواست جدول را
می‌سازد (`CREATE TABLE IF NOT EXISTS`).

> **نکتهٔ D1:** سقف رایگان هر حساب ۱۰ دیتابیس است. اگر پر بود، یک دیتابیس خالی
> را استفاده کن؛ جدول این پروژه با پیشوند `nexus_` ساخته می‌شود و با هیچ جدول
> دیگری قاطی نمی‌شود.

## راه‌اندازی

1. آدرس Worker را باز کن. رمز پیش‌فرض **`admin`** است و پنل تا عوضش نکنی هشدار
   می‌دهد. **همان اول عوضش کن** — آدرس این پنل عمومی است.
2. یک **دامنهٔ خودت** به Worker وصل کن
   (`Workers & Pages → Worker → Settings → Domains & Routes`).
   آدرس `*.workers.dev` در ایران فیلتر است؛ Worker روی آن فقط با VPN باز می‌شود.
3. یک کاربر بساز و «لینک اتصال» را در v2rayNG / Hiddify / Streisand اضافه کن.
4. در **میزبان‌های اضافه** چند **IP تمیز کلودفلر** بگذار (هر خط یکی). هر کدام یک
   لینک اضافه در همان سابلینک می‌شود؛ آدرسِ وصل‌شدن آن IP است و Host/SNI همان
   دامنهٔ Worker می‌ماند. این باعث می‌شود با فیلتر شدن یک آدرس، سابلینک از کار
   نیفتد.

## مصرف واقعی است

`used_bytes` اندازه‌گیری می‌شود، نه حدس. رله بایت‌ها را همان‌جا جمع می‌کند و
دسته‌دسته (و یک‌بار هم موقع بستن) در D1 می‌نویسد. پاسخ سابلینک هم
`subscription-userinfo` و `profile-update-interval: 12` می‌فرستد، پس صفحهٔ
«اشتراک» خود کلاینت هم مصرف و بروزرسانی خودکار را نشان می‌دهد.

## چرا گاهی اتصال خروجی برقرار نمی‌شود

`connect()` همان API کلودفلر برای TCP خروجی است و بعضی مقصدها را از اول رد
می‌کند:

* **شبکهٔ خود کلودفلر** (`1.1.1.1`، `cloudflare.com`) رد می‌شود.
* بعضی آدرس‌ها پاسخ می‌دهند
  `proxy request failed ... It looks like you might be trying to connect to a
  HTTP-based service`.

بقیهٔ مقصدها عادی وصل می‌شوند (تست‌شده: `github.com:80` و `github.com:443`
هر دو وصل می‌شوند و HTTP از داخل رله رد می‌شود). رله دلیل شکست را در close
reason همان WebSocket می‌گوید، پس اگر کلاینت «وصل شد» ولی چیزی باز نشد، دلیلش
سمت سرور پیدا می‌شود.

برای تست از خود پنل:

```bash
curl -b cookies.txt -X POST -H 'content-type: application/json' \
  -d '{"address":"github.com","port":443}' \
  https://<worker>/api/diag
```

## فایل‌ها

```
worker-panel/wrangler.jsonc     بایندینگ‌ها: NEXUS_KV و NEXUS_DB
worker-panel/schema.sql         تنها جدول (nexus_users)
worker-panel/src/index.js       مسیرها: / و /api/* و /sub/<uuid> و /ws*
worker-panel/src/panel.js       خود پنل (یک رشته، بدون build)
worker-panel/src/store.js       تنظیمات KV + کاربران D1
worker-panel/src/auth.js        رمز PBKDF2 و کوکی امضاشدهٔ HMAC
worker-panel/src/links.js       ساخت لینک vless و بدنهٔ سابلینک
worker-panel/src/vless-core.js  پارس هدر VLESS و لیست مقصدهای ممنوع
worker-panel/src/vless.js       رله: WebSocket ↔ cloudflare:sockets
```

تست بدون شبکه:

```bash
node tests/worker_panel_smoke.mjs
```
