# Video Scraper & Upload

GitHub Actions workflow لاستخراج الفيديو من موقع TV10 ورفعه إلى earnvidsapi.com.

## المميزات

- استخراج رابط الفيديو من صفحة TV10
- دعم قائمة السيرفرات (serversList) مع اختيار السيرفر المفضل
- رفع الفيديو تلقائيًا إلى earnvidsapi.com باستخدام API
- حفظ النتيجة (filecode) في ملف JSON

## الإعداد

### 1. إضافة GitHub Secret

اذهب إلى إعدادات المستودع في GitHub:
- Settings → Secrets and variables → Actions → New repository secret
- أضف السكرت التالي:
  - Name: `EARNVIDS_API_KEY`
  - Value: مفتاح API الخاص بك من earnvidsapi.com

### 2. تشغيل الـ Workflow

1. اذهب إلى تبويب "Actions" في المستودع
2. اختر workflow "Video Scraper & Upload"
3. اضغط على "Run workflow"
4. أدخل:
   - **page_url**: رابط صفحة TV10 (مثال: `https://tv10.egydead.live/...`)
   - **server_preference** (اختياري): اسم السيرفر المفضل (مثال: `EarnVids`, `Streamix`, `Voe`)

## السيرفرات المدعومة

- EarnVids
- Streamix
- Voe
- Mixdrop
- StreamHG
- Byse

## النتيجة

بعد اكتمال الـ workflow:
- سيتم حفظ النتيجة في ملف `upload_result.json` كـ artifact
- الملف يحتوي على:
  - `filecode`: كود الفيديو المرفوع
  - `original_url`: الرابط الأصلي
  - `iframe_url`: رابط الـ iframe
  - `video_url`: رابط الفيديو

## التشغيل المحلي

يمكنك تشغيل السكريبت محليًا:

```bash
pip install -r requirements.txt
export PAGE_URL="https://tv10.egydead.live/..."
export EARNVIDS_API_KEY="your_api_key"
python scrape_upload.py
```

## هيكل المشروع

```
.
├── scrape_upload.py          # السكريبت الرئيسي
├── requirements.txt          # المكتبات المطلوبة
├── .github/
│   └── workflows/
│       └── video-scraper.yml # GitHub Actions workflow
└── README.md                # هذا الملف
```

## ملاحظات

- السكريبت يستخدم BeautifulSoup لاستخراج قائمة السيرفرات
- إذا لم يتم العثور على السيرفر المفضل، سيتم استخدام أول سيرفر متاح
- الـ class `ServersList` جاهز لإضافة منطق فك تشفير الروابط إذا لزم الأمر
