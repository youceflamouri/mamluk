# رفع الموقع على استضافة حقيقية (رابط عام يعمل من أي مكان)

اخترت **PythonAnywhere** لأنه الأنسب لهذا المشروع تحديدًا: مجاني بالكامل، لا
يحتاج بطاقة بنكية، **لا يمحو قاعدة بيانات SQLite** بين كل إعادة تشغيل (بعكس
Render أو Railway المجانيَين اللذين يمسحان الملفات كل مرة)، ورابطك سيكون
دائمًا:

```
https://اسم_المستخدم.pythonanywhere.com
```

## الخطوات

### 1. أنشئ حسابًا مجانيًا
اذهب إلى https://www.pythonanywhere.com واضغط "Start running Python" ثم اختر
الخطة المجانية **Beginner**. اختر اسم مستخدم بسيطًا (سيصبح جزءًا من رابط
موقعك).

### 2. ارفع ملفات المشروع
من لوحة التحكم، اذهب إلى تبويب **Files**. ارفع ملف `mamluk_flask.zip` كاملًا
(زر Upload a file). بعدها افتح تبويب **Consoles** وشغّل **Bash console
جديدة**، ثم نفّذ:
```bash
unzip mamluk_flask.zip -d mamluk_flask
cd mamluk_flask
pip3.10 install --user -r requirements.txt
```
(إذا ظهرت لك نسخة بايثون مختلفة عن 3.10 في الواجهة، استخدم نفس الرقم الظاهر
عندك بدل 3.10)

### 3. أنشئ تطبيق ويب جديد
اذهب إلى تبويب **Web** ← **Add a new web app** ← اختر نطاقك المجاني ← عند
السؤال عن الإطار (Framework) اختر **Manual configuration** (وليس Flask
الجاهز) ← اختر نفس نسخة بايثون التي استخدمتها في الخطوة السابقة.

### 4. عدّل ملف WSGI
في نفس تبويب **Web**، ستجد رابط ملف باسم شبيه بـ
`/var/www/اسم_المستخدم_pythonanywhere_com_wsgi.py`. افتحه واحذف كل محتواه،
ثم ضع بدلاً منه:
```python
import sys
import os

path = "/home/اسم_المستخدم/mamluk_flask"
if path not in sys.path:
    sys.path.insert(0, path)

# بيانات حسّاسة — عدّلها هنا بدل ترك القيم الافتراضية في الكود
os.environ["MAMLUK_SECRET_KEY"] = "ضع-هنا-نص-عشوائي-طويل-وسري"
os.environ["MAMLUK_ADMIN_EMAIL"] = "youcef437@gmail.com"
os.environ["MAMLUK_ADMIN_PASSWORD"] = "ylamouri2007"
os.environ["MAMLUK_ENV"] = "production"

from app import app as application
```
غيّر `اسم_المستخدم` في المسار إلى اسم المستخدم الحقيقي الخاص بك (مرتين).
لتوليد نص عشوائي سري بسهولة، نفّذ هذا في الـ Bash console وانسخ الناتج:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 5. اربط مجلد static
في نفس تبويب **Web**، تحت قسم **Static files**، أضف:
- URL: `/static/`
- Directory: `/home/اسم_المستخدم/mamluk_flask/static`

### 6. أعد التشغيل
اضغط الزر الأخضر الكبير **Reload** أعلى تبويب Web. بعد ثوانٍ، افتح رابطك:
```
https://اسم_المستخدم.pythonanywhere.com
```
ولوحة الإدارة على:
```
https://اسم_المستخدم.pythonanywhere.com/admin/login
```

---

## بديل: Render.com (أسرع في الإعداد، لكن بدون تخزين دائم مجانًا)

إذا أردت لاحقًا نشرًا عبر Git بدل الرفع اليدوي:
1. ارفع المشروع إلى مستودع GitHub.
2. من Render.com أنشئ **New Web Service** واربطه بالمستودع.
3. Build Command: `pip install -r requirements.txt`
4. Start Command: `gunicorn app:app`
5. أضف متغيرات البيئة نفسها (`MAMLUK_SECRET_KEY`, `MAMLUK_ADMIN_EMAIL`,
   `MAMLUK_ADMIN_PASSWORD`, `MAMLUK_ENV=production`) من تبويب Environment.

⚠️ **تنبيه مهم**: الخطة المجانية في Render **تمسح ملف `mamluk.db`** (الطلبات،
أكواد الخصم، الرسائل) في كل مرة يُعاد فيها نشر أو إعادة تشغيل الخدمة. للاستخدام
الفعلي (متجر حقيقي)، إمّا أضف قرصًا دائمًا مدفوعًا (Persistent Disk) من Render،
أو استخدم PythonAnywhere كما في الأعلى، أو اطلب مني لاحقًا تحويل قاعدة البيانات
من SQLite إلى PostgreSQL (خدمة قاعدة بيانات مستقلة لا تُمسح أبدًا).

---

## ملاحظات أمان قبل النشر الفعلي
- غيّر `MAMLUK_SECRET_KEY` عن القيمة العشوائية المؤقتة إلى نص سري ثابت (كما في
  الخطوة 4)، وإلا سيُسجَّل خروج كل المستخدمين والأدمن عند كل إعادة تشغيل.
- تأكد أن `MAMLUK_ENV=production` مضبوط حتى تعمل كوكيز الجلسة بوضع `Secure`
  (HTTPS فقط) — وهذا مضمون تلقائيًا على PythonAnywhere وRender لأن كليهما
  يوفران HTTPS افتراضيًا.
- غيّر كلمة مرور الإدارة من لوحة التحكم بعد أول دخول فعلي على الموقع المنشور.
