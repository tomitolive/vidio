# Video Scraper & Upload

GitHub Actions workflow لاستخراج الفيديو من موقع TV10 ورفعه إلى DoodStream.

## المميزات

- استخراج رابط الفيديو من صفحة TV10
- **دعم VidSrc API** - استخراج الفيديوهات باستخدام معرفات TMDB
- دعم قائمة السيرفرات (serversList) مع اختيار السيرفر المفضل
- رفع الفيديو تلقائيًا إلى DoodStream باستخدام API
- حفظ النتيجة (filecode) في ملف JSON
- **نظام التتبع** - تجنب معالجة نفس الفيلم أكثر من مرة
- **دعم صفحات التصنيفات (Category Pages)** - استخراج ورفع جميع الفيديوهات من صفحة تصنيف

## الإعداد

### 1. تشغيل الـ Workflow

#### لاستخراج فيلم من VidSrc (باستخدام TMDB ID):
1. اذهب إلى تبويب "Actions" في المستودع
2. اختر workflow "Video Scraper & Upload"
3. اضغط على "Run workflow"
4. أدخل:
   - **source**: `vidsrc`
   - **tmdb_id**: معرف TMDB للفيلم (مثال: `533535`)

سيقوم النظام بـ:
- إنشاء رابط VidSrc embed من TMDB ID
- حفظ الرابط في ملف JSON
- حفظ TMDB ID في ملف التتبع لتجنب التكرار

#### لصفحة فيديو واحدة من TV10:
1. اذهب إلى تبويب "Actions" في المستودع
2. اختر workflow "Video Scraper & Upload"
3. اضغط على "Run workflow"
4. أدخل:
   - **source**: `tv10` (الافتراضي)
   - **page_url**: رابط صفحة TV10 (مثال: `https://tv10.egydead.live/...`)
   - **api_key**: مفتاح API الخاص بك من DoodStream
   - **server_preference** (اختياري): اسم السيرفر المفضل (مثال: `StreamHG`, `Mixdrop`, `Voe`)

#### لصفحة تصنيف (Category Page):
1. اذهب إلى تبويب "Actions" في المستودع
2. اختر workflow "Video Scraper & Upload"
3. اضغط على "Run workflow"
4. أدخل:
   - **source**: `tv10`
   - **page_url**: رابط صفحة التصنيف (مثال: `https://tv10.egydead.live/category/english-movies/`)
   - **api_key**: مفتاح API الخاص بك من DoodStream
   - **server_preference** (اختياري): اسم السيرفر المفضل

سيقوم الـ workflow تلقائيًا بالكشف عن نوع الصفحة ومعالجتها:
- إذا كان الرابط يحتوي على `/category/` سيتم معالجة جميع الفيديوهات في الصفحة
- إذا كان رابط فيديو عادي سيتم معالجة الفيديو الواحد

## السيرفرات المدعومة

- StreamHG
- Mixdrop
- Voe
- Streamix
- Byse
- DoodStream

## النتيجة

### لصفحة فيديو واحدة:
بعد اكتمال الـ workflow:
- سيتم حفظ النتيجة في ملف `upload_result.json` كـ artifact
- الملف يحتوي على:
  - `filecode`: كود الفيديو المرفوع
  - `original_url`: الرابط الأصلي
  - `iframe_url`: رابط الـ iframe
  - `video_url`: رابط الفيديو

### لصفحة تصنيف:
بعد اكتمال الـ workflow:
- سيتم حفظ النتيجة في ملف `upload_result.json` كـ artifact
- الملف يحتوي على:
  - `category_url`: رابط التصنيف
  - `total_videos`: إجمالي عدد الفيديوهات
  - `successful_uploads`: عدد الرفعات الناجحة
  - `failed_uploads`: عدد الرفعات الفاشلة
  - `results`: قائمة بجميع النتائج لكل فيديو

## التشغيل المحلي

يمكنك تشغيل السكريبت محليًا:

### لاستخدام VidSrc:
```bash
pip install -r requirements.txt
export SOURCE="vidsrc"
export TMDB_ID="533535"
python scrape_upload.py
```

### لاستخدام TV10:
```bash
pip install -r requirements.txt
export SOURCE="tv10"
export PAGE_URL="https://tv10.egydead.live/..."
export EARNVIDS_API_KEY="your_doodstream_api_key"
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
- **نظام التتبع**: يتم حفظ معرفات TMDB المعالجة في `processed_movies.json` لتجنب التكرار
- **VidSrc**: يستخدم TMDB IDs لاستخراج الأفلام مع الترجمة العربية
