import asyncio, json, os, re, sqlite3, time
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
import aiohttp

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN", "").strip()
PHONE_1=os.getenv("PHONE_1", "09369952103").strip()
PHONE_2=os.getenv("PHONE_2", "09904358418").strip()
SHOP_URL=os.getenv("SHOP_URL", "https://liyanashop.ir").strip()
TG_URL=os.getenv("TELEGRAM_URL", "https://t.me/liyanashop_ir").strip()
RUBIKA_URL=os.getenv("RUBIKA_URL", "https://rubika.ir/liyanashop_gnv").strip()
GROQ_API_KEY=os.getenv("GROQ_API_KEY", "").strip()
GROQ_MODEL=os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile").strip()
OWNER_TELEGRAM_ID=os.getenv("OWNER_TELEGRAM_ID", "").strip()
OWNER_PASSWORD=os.getenv("OWNER_PASSWORD", "").strip()
AI_WEEKLY_LIMIT=3
DB_PATH=Path(__file__).with_name("bot_data.sqlite3")
AI_USERS=set()                 # users currently chatting with AI
OWNER_PASSWORD_PENDING=set()   # owner IDs currently entering password
OWNER_AI_USERS=set()           # owner IDs authenticated for AI testing
DATA=json.loads(Path(__file__).with_name("products.json").read_text(encoding="utf-8"))
PAGE_SIZE=8
bot=Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher()

HOME_TEXT=("<b>✦ LIYANA SHOP ✦</b>\n"
"<i>فروشگاه آرایشی و بهداشتی لیانا</i>\n\n"
"سلام خوش اومدی 🌷\nاز منوی زیر می‌تونی محصولات رو ببینی، محصول مناسب رو پیدا کنی یا از فروشگاه درباره قیمت و موجودی بپرسی.")

def btn(text, data=None, url=None):
    if url: return InlineKeyboardButton(text=text,url=url)
    return InlineKeyboardButton(text=text,callback_data=data)

def nav_row(): return [btn("↩️ بازگشت", "back"),btn("🏠 خانه", "home")]
def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [btn("🛍 مشاهده محصولات", "catalog:0"),btn("🔎 موجودی و قیمت", "stocksearch")],
      [btn("💡 راهنمای انتخاب محصول", "questions"),btn("🏬 اطلاعات فروشگاه", "shop")],
      [btn("🤖 گفت‌وگو با هوش مصنوعی", "ai:open")],
      [btn("📣 کانال تلگرام",url=TG_URL),btn("🛒 سایت فروشگاه",url=SHOP_URL)]
    ])
def cats(): return sorted(set(p['category'] for p in DATA if p.get('published',True)))
def categories_kb():
    rows=[]; c=cats()
    for i in range(0,len(c),2): rows.append([btn("✨ "+c[i],f"cat:{i}:0")]+([btn("✨ "+c[i+1],f"cat:{i+1}:0")] if i+1<len(c) else []))
    rows.append([btn("📚 همه محصولات", "catalog:0")]); rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)
def product_keyboard(p):
    rows=[[btn("💰 استعلام قیمت",f"ask:price:{p['id']}"),btn("📦 استعلام موجودی",f"ask:stock:{p['id']}")],
          [btn("🔗 صفحه محصول",url=p['product_url'])],nav_row()]
    return InlineKeyboardMarkup(inline_keyboard=rows)
def money_toman(raw):
    try:
        n=int(str(raw).replace(',','').strip())
        return f"{n:,}".replace(',', '٬') + ' تومان'
    except (TypeError, ValueError):
        return None

def customer_stock_status(raw):
    status=str(raw or '').strip()
    norm=normalize_fa(status)
    if 'ناموجود' in norm or 'تمام شده' in norm:
        return 'فعلاً ناموجود اعلام شده؛ برای اطمینان از وضعیت امروز با فروشگاه هماهنگ کن.'
    if 'موجود' in norm:
        return 'موجود به نظر می‌رسد؛ برای اطمینان از موجودی امروز با فروشگاه هماهنگ کن.'
    return 'برای اطلاع از موجودی امروز با فروشگاه هماهنگ کن.'

def variant_lines(p):
    out=[]
    for v in p.get('variants',[]):
        attr=(v.get('attribute') or v.get('name') or '').strip()
        price=money_toman(v.get('price_raw'))
        stock=customer_stock_status(v.get('stock_file') or p.get('stock_file'))
        bits=[]
        if attr: bits.append(esc(attr))
        if price: bits.append('<b>'+esc(price)+'</b>')
        bits.append('وضعیت موجودی: '+esc(stock))
        out.append('• '+' — '.join(bits))
    return out

def product_text(p):
    desc=p.get('description','').strip()
    if not desc or 'باید با توضیحات سازنده تأیید شود' in desc or 'باید از عکس و مشخصات سازنده بررسی شود' in desc: desc=describe_product(p)
    variants=p.get('variants',[])
    opt=''
    if variants:
      labels=[]
      for v in variants[:8]:
        a=v.get('attribute') or v.get('name','')
        if a and a not in labels: labels.append(a)
      if labels: opt='\n\n<b>تنوع‌ها:</b> '+ '، '.join(labels)
    prices=variant_lines(p)
    price_text=('\n\n<b>قیمت و تنوع:</b>\n'+'\n'.join(prices)) if prices else ''
    return f"<b>✨ {esc(p['name'])}</b>\n\n{esc(desc[:700])}{opt}{price_text}\n\n<i>قیمت و موجودی لحظه‌ای را از فروشگاه استعلام بگیر.</i>"
def esc(s):
    import html
    return html.escape(str(s or ''))
def paginated(items,page,prefix):
    start=page*PAGE_SIZE; chunk=items[start:start+PAGE_SIZE]; rows=[]
    for p in chunk: rows.append([btn("🧴 "+p['name'][:48],f"product:{p['id']}")])
    nav=[]
    if ':' in prefix:
      base, extra = prefix.split(':',1)
      if page>0: nav.append(btn("⬅️ قبلی",f"{base}:{page-1}:{extra}"))
      if start+PAGE_SIZE<len(items): nav.append(btn("بعدی ➡️",f"{base}:{page+1}:{extra}"))
    else:
      if page>0: nav.append(btn("⬅️ قبلی",f"{prefix}:{page-1}"))
      if start+PAGE_SIZE<len(items): nav.append(btn("بعدی ➡️",f"{prefix}:{page+1}"))
    if nav: rows.append(nav)
    rows.append([btn("🗂 دسته‌بندی‌ها", "categories")]); rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)
def search_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [btn("☀️ ضدآفتاب", "q:sunscreen"),btn("💧 شوینده و مراقبت پوست", "q:skincare")],
      [btn("💄 آرایش صورت", "q:face"),btn("💋 محصولات لب", "q:lips")],
      [btn("👁 چشم و ابرو", "q:eyes"),btn("🧴 عطر و بدن", "q:body")],nav_row()])
def matching_question(key):
    mp={'sunscreen':['ضدآفتاب'],'skincare':['مراقبت پوست','پاک‌کننده و شوینده','ضدآفتاب'], 'face':['آرایش صورت'],'lips':['آرایش لب'],'eyes':['آرایش چشم و ابرو'],'body':['عطر و خوشبوکننده','بهداشت شخصی']}
    cats_=mp.get(key,[])
    return [p for p in DATA if p['category'] in cats_ and p.get('published',True)]
def find(pid): return next((p for p in DATA if str(p['id'])==str(pid)),None)

@dp.message(CommandStart())
async def start(m:Message):
    AI_USERS.discard(m.from_user.id)
    OWNER_PASSWORD_PENDING.discard(m.from_user.id)
    await m.answer(HOME_TEXT,reply_markup=home_kb())
@dp.message(Command("home"))
async def home(m:Message):
    AI_USERS.discard(m.from_user.id)
    OWNER_PASSWORD_PENDING.discard(m.from_user.id)
    await m.answer(HOME_TEXT,reply_markup=home_kb())
# --- AI chat, weekly quota and owner test mode ---
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS ai_usage (user_id INTEGER NOT NULL, week_key TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, week_key))")

def current_week_key():
    # ISO week: resets every Monday, based on UTC to avoid server timezone surprises.
    return time.strftime("%G-W%V", time.gmtime())

def quota_used(user_id:int)->int:
    with sqlite3.connect(DB_PATH) as con:
        row=con.execute("SELECT used FROM ai_usage WHERE user_id=? AND week_key=?",(user_id,current_week_key())).fetchone()
        return int(row[0]) if row else 0

def quota_increment(user_id:int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT INTO ai_usage(user_id,week_key,used) VALUES(?,?,1) ON CONFLICT(user_id,week_key) DO UPDATE SET used=used+1",(user_id,current_week_key()))

def is_owner_id(user_id:int)->bool:
    return bool(OWNER_TELEGRAM_ID and str(user_id)==OWNER_TELEGRAM_ID)

def ai_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("👑 من صاحب ربات هستم", "ai:owner")],
        [btn("↩️ خروج از هوش مصنوعی", "ai:exit"), btn("🏠 خانه", "home")]
    ])

def product_context(query:str, limit=8):
    q=query.casefold()
    # Find product records by name/category and useful term overlap, without sending the entire catalogue.
    terms=[t for t in re.findall(r"[\w؀-ۿ]+", q) if len(t)>2]
    scored=[]
    for p in DATA:
        if not p.get('published', True):
            continue
        name=str(p.get('name',''))
        cat=str(p.get('category',''))
        hay=(name+' '+cat+' '+str(p.get('description',''))).casefold()
        score=sum(1 for t in terms if t in hay)
        if q and q in name.casefold(): score += 5
        if score: scored.append((score,p))
    scored.sort(key=lambda x:x[0],reverse=True)
    out=[]
    for _,p in scored[:limit]:
        variants=[]
        for v in p.get('variants',[])[:5]:
            price=str(v.get('price_raw') or '').strip()
            attr=str(v.get('attribute') or v.get('name') or '').strip()
            stock=str(v.get('stock_file') or p.get('stock_file') or 'نامشخص')
            variants.append({'variant':attr,'price_raw':price,'stock_file':stock})
        out.append({'name':p.get('name',''),'category':p.get('category',''),'description':p.get('description',''),'variants':variants,'product_url':p.get('product_url',''),'image':p.get('image','')})
    return out

# --- دامنه گفت‌وگو و پایگاه دانش آرایشی آفلاین ---
DOMAIN_TERMS=(
 'آرایش','آرایشی','بهداشتی','زیبایی','میکاپ','میکاپ','پوست','چرب','خشک','مختلط','حساس',
 'ضدآفتاب','ضد آفتاب','spf','کرم پودر','کرمپودر','فوندیشن','پرایمر','کانسیلر','پنکیک',
 'پودر فیکس','رژ','لب','لیپ','ریمل','خط چشم','سایه','ابرو','رژگونه','هایلایتر','کانتور','تینت','تینت لب','تینت گونه','بی بی کرم','سی سی کرم','کوشن','فیکساتور','اسپری فیکس','پودر بیک','رژ مایع','خط لب','بالم لب','لیپ گلاس','لیپ گلیتر','مداد چشم','مداد ابرو','ژل ابرو','صابون ابرو','فرمژه','مژه مصنوعی','چسب مژه','لاک ژل','لاک ناخن','پاک کننده لاک','پد لاک','تینت','تینت لب','تینت گونه','بی بی کرم','سی سی کرم','کوشن','فیکساتور','اسپری فیکس','پودر بیک','رژ مایع','خط لب','بالم لب','لیپ گلاس','لیپ گلیتر','مداد چشم','مداد ابرو','ژل ابرو','صابون ابرو','فرمژه','مژه مصنوعی','چسب مژه','لاک ژل','لاک ناخن','پاک کننده لاک','پد لاک',
 'شوینده','فیس واش','پاک کننده','پاک‌کننده','میسلار','تونر','سرم','آبرسان','مرطوب کننده',
 'مرطوب‌کننده','ماسک صورت','جوش','آکنه','جای جوش','لک صورت','منافذ','روتین پوستی','روتین پوست',
 'ترکیبات','مواد تشکیل دهنده','ترکیب محصول','حجم محصول','رنگ محصول','تناژ','رنگ پوست',
 'لاک','ناخن','عطر','ادکلن','بادی اسپلش','خوشبوکننده','مو','مژه','مراقبت پوست','مراقبت مو',
 'براش','پد آرایشی','اسفنج آرایشی','اسفنج','پاک کردن آرایش','پاک‌کننده آرایش','ماندگاری آرایش',
 'ضدآب','ضد آب','مات','براق','پوشش سبک','پوشش زیاد','پوسته پوسته','قرمزی پوست','حساسیت پوستی',
 'محصول','محصولات','قیمت','موجودی','موجوده','موجود هست','توی فروشگاه','داخل فروشگاه','کاتالوگ',
 'سلام','درود','صبح بخیر','عصر بخیر','وقت بخیر','خوبی','چطوری','حالت چطوره','چه خبر','چه عالی','عالیه','آفرین','خوبه','خوب شد','کاره نداری','کاری نداری','چه خبرها','ممنونم','قربونت','خواهش می کنم','خواهش میکنم','خوشگلم','عزیزم','معرفی فروشگاه','درباره فروشگاه','آدرس فروشگاه','شماره فروشگاه','ساعت کاری','ارسال دارید','خرید حضوری','خرید اینترنتی','سفارش چطور','چطور سفارش','ارسال کالا','ارسال محصول',
 'ممنون','مرسی','تشکر','دمت گرم','خداحافظ','فعلا','فعلاً','بای','شب بخیر','روز بخیر'
)
SMALL_TALK={
 'greet':('سلام','درود','سلام عزیزم','سلام وقت بخیر','سلام خوبی'),
 'how':('خوبی','چطوری','حالت چطوره','چه خبر'),
 'thanks':('ممنون','مرسی','تشکر','دمت گرم','سپاس','ممنونم','قربونت','خواهش می کنم'),
 'bye':('خداحافظ','فعلا','فعلاً','بای','شب بخیر','روز بخیر'),
 'positive':('چه عالی','عالیه','آفرین','خوبه','خوب شد','چه خوب','عالی بود','دمت گرم'),
 'casual':('کاره نداری','کاری نداری','چه خبرها','چیکار میکنی','چیکار می کنی')
}

def normalize_fa(s:str)->str:
    s=str(s or '').casefold()
    table=str.maketrans({'ي':'ی','ك':'ک','ۀ':'ه','ة':'ه','أ':'ا','إ':'ا','ؤ':'و','ئ':'ی','‌':' ','ـ':' ', 'ۀ':'ه'})
    s=s.translate(table)
    s=re.sub(r'[^\w\s؀-ۿ]', ' ', s)
    return re.sub(r'\s+',' ',s).strip()

def is_small_talk(q:str):
    q=normalize_fa(q)
    words=q.split()
    if len(words)>9: return None
    # Exact / near-exact social messages; avoid classifying a long product question as small talk.
    for key, phrases in SMALL_TALK.items():
        for phrase in phrases:
            ph=normalize_fa(phrase)
            if q == ph or (len(words)<=4 and (q.startswith(ph+' ') or q.endswith(' '+ph))):
                return key
    return None

def product_name_matches(query:str):
    q=normalize_fa(query)
    if len(q)<3: return []
    # Search names only for existence checks; avoid generic descriptions creating false positives.
    exact=[]
    for p in DATA:
        if not p.get('published',True): continue
        name=normalize_fa(p.get('name',''))
        if q in name or name in q:
            exact.append(p)
    return exact

def is_beauty_question(q:str)->bool:
    qn=normalize_fa(q)
    if is_small_talk(qn): return True
    if product_name_matches(qn): return True
    # Store/customer-service questions are valid in the Liyana assistant.
    service_terms=('فروشگاه','لیانا','آدرس','شماره تماس','تماس','تلفن','شعبه','گناوه','پارکینگ ملت','جام جم',
                   'ساعت کاری','باز هستید','بازین','بازید','تعطیل','ارسال','پست','سفارش','خرید حضوری','خرید اینترنتی',
                   'پرداخت','مرجوع','تعویض','سایت','کانال','تلگرام','قیمت','موجودی','دارید','دارین','میخوام','می خواهم','میخوام بخرم')
    if any(normalize_fa(x) in qn for x in service_terms): return True
    # Strong topical terms; include everyday phrasing and common cosmetic names.
    strong=[x for x in DOMAIN_TERMS if len(normalize_fa(x))>=3 and normalize_fa(x) in qn]
    if strong: return True
    # Purchase-intent phrasing such as "یه تینت خوب میخوام" or "چی برای لب بخرم".
    intent_terms=('میخوام','می خواهم','بخرم','پیشنهاد بده','چی بخرم','چی خوبه','چه خوبه','کدوم بهتره','مناسب من','برای من چی')
    beauty_nouns=('رنگ','لب','صورت','چشم','پوست','مو','ناخن','آرایش','مراقبت','کرم','رژ','تینت','عطر','ضدآفتاب','ریمل','ابرو','تینت','رژ','لب','پوست','مو','مژه','ناخن','پودر فیکس','اسپری فیکس','بالم','تونر','سرم','ماسک')
    return any(x in qn for x in intent_terms) and any(x in qn for x in beauty_nouns)

def describe_product(p):
    """Description grounded in the catalog name/category; avoids invented ingredients or claims."""
    name=p.get('name','').strip(); cat=p.get('category','محصولات آرایشی و بهداشتی')
    lower=normalize_fa(name)
    if any(x in lower for x in ('کرم پودر','کرمپودر','فوندیشن')):
        base='محصول آرایش صورت برای ایجاد پوشش و یکدست‌تر نشان دادن ظاهر پوست. برای انتخاب دقیق، نوع پوست، تناژ رنگ و میزان پوشش مهم است؛ رنگ‌بندی دقیق مشخص نیست.'
    elif 'پرایمر' in lower: base='محصول زیرساز آرایش که معمولاً پیش از کرم‌پودر استفاده می‌شود. نتیجه و سازگاری به فرمول محصول و نوع پوست بستگی دارد؛ فرمول دقیق مشخص نیست.'
    elif 'ضدآفتاب' in lower or 'spf' in lower: base='محصول مرتبط با محافظت در برابر آفتاب. SPF، طیف محافظت، روش مصرف و سازگاری با نوع پوست را از برچسب سازنده بررسی کن.'
    elif any(x in lower for x in ('فیس واش','شوینده','پاک کننده','پاک‌کننده')): base='محصول پاک‌سازی پوست. برای انتخاب بهتر باید نوع پوست و اطلاعات روی بسته‌بندی بررسی شود؛ ترکیبات دقیق مشخص نیست.'
    elif 'سرم' in lower: base='محصول مراقبت پوستی از نوع سرم. کاربرد دقیق، ترکیبات و نوع پوست مناسب از نام کالا به‌تنهایی قابل تأیید نیست؛ مشخصات سازنده را بررسی کن.'
    elif 'ماسک' in lower: base='محصول ماسک در دسته مراقبت یا زیبایی. نوع ماسک، مدت استفاده و مناسب‌بودن برای نوع پوست باید از توضیحات سازنده تأیید شود.'
    elif any(x in lower for x in ('رژگونه','رژ گونه','پنکیک','هایلایتر','کانتور','کانسیلر')): base='محصول آرایش صورت. برای انتخاب رنگ مناسب، تناژ پوست و رنگ‌های موجود اهمیت دارند؛ رنگ‌بندی دقیق در اطلاعات فعلی تأیید نشده است.'
    elif any(x in lower for x in ('رژ لب','رژلب','لیپ گلاس','لیپ‌گلاس','تینت لب','بالم لب','مربا لب','خط لب')): base='محصول آرایش یا مراقبت لب. جلوه نهایی، رنگ و ترکیبات دقیق باید از عکس و مشخصات سازنده بررسی شود.'
    elif any(x in lower for x in ('ریمل','خط چشم','مداد چشم','سایه','ابرو')): base='محصول آرایش چشم یا ابرو. رنگ، کاربرد و توضیحات روی بسته‌بندی را بررسی کن؛ ضدآب بودن یا مناسب‌بودن برای چشم حساس مشخص نیست.'
    elif 'ناخن' in lower or 'لاک' in lower: base='محصول مرتبط با آرایش یا مراقبت ناخن. رنگ، حجم و شیوه استفاده باید از مشخصات بسته‌بندی تأیید شود.'
    elif any(x in lower for x in ('عطر','ادکلن','بادی اسپلش','خوشبو')): base='محصول خوشبوکننده. رایحه، ماندگاری و حجم دقیق از نام کالا قابل تأیید نیست؛ توضیحات یا تست محصول را بررسی کن.'
    elif cat=='ابزار آرایشی': base='ابزار مرتبط با آرایش یا آماده‌سازی روتین زیبایی. تعداد اقلام و کاربرد دقیق را در بخش تنوع‌ها و عکس محصول بررسی کن.'
    elif cat=='مراقبت پوست': base='محصول در دسته مراقبت پوست. کاربرد دقیق، ترکیبات و نوع پوست مناسب باید با توضیحات سازنده تأیید شود.'
    else: base=f'این محصول در دسته «{cat}» قرار می‌گیرد. برای جزئیات دقیق مثل ترکیبات، حجم، رنگ یا روش مصرف، مشخصات روی بسته‌بندی یا صفحه محصول را بررسی کن.'
    variants=p.get('variants') or []; attrs=[]
    for v in variants[:5]:
        a=(v.get('attribute') or v.get('name') or '').strip()
        if a and a not in attrs: attrs.append(a)
    if attrs: base += ' تنوع‌ها: '+ '، '.join(attrs)+'.'
    return base

def search_products(query, limit=8):
    q=normalize_fa(query).replace('کرم پودر','کرمپودر')
    if not q: return []
    stop={'برای','من','چی','چیه','کدوم','کدام','خوب','مناسب','دارم','میخوام','میخوام','محصول','محصولات','لطفا','لطفاً','یه','یک','از','به','با','را','رو','و','یا','این','اون','هست','هستش','توی','داخل','فروشگاه','لیانا','یه','ی','میخوام','میخواهم','می','خوام','بخرم','پیشنهاد','بده','خوب','خوبه','بهترین','دارین','دارید','میشه','می شه','لطفا','لطفاً'}
    terms=[t for t in q.split() if len(t)>1 and t not in stop]
    aliases={
      'کرمپودر':['کرم پودر','کرمپودر','فوندیشن','بی بی کرم','کوشن'], 'فوندیشن':['کرم پودر','کرمپودر','فوندیشن'],
      'رژلب':['رژ لب','رژلب','لیپ گلاس','بالم لب','تینت لب'], 'لب':['رژ لب','رژلب','لیپ گلاس','بالم لب','تینت لب','خط لب'],
      'ابرو':['مداد ابرو','ژل ابرو','ابرو'], 'چشم':['ریمل','خط چشم','سایه','مداد چشم'],
      'جوش':['ضد جوش','آکنه','جوش','سرم درمانی فوری آکنه'], 'آبرسان':['آبرسان','مرطوب کننده','مرطوب‌کننده','کرم دست'],
      'خشک':['آبرسان','مرطوب کننده','مرطوب‌کننده','بالم لب'], 'چرب':['فیس واش','شوینده','ضدآفتاب'],
      'ضدآفتاب':['ضدآفتاب','ضد آفتاب','spf'], 'ناخن':['ناخن','لاک'], 'پاکسازی':['فیس واش','شوینده','پاک کننده','میسلار'],
      'رنگ':['رژ لب','رژگونه','لاک','سایه','مداد ابرو'], 'عطر':['عطر','ادکلن','بادی اسپلش']
    }
    scored=[]
    for item in DATA:
        if not item.get('published',True): continue
        name=normalize_fa(item.get('name','')); cat=normalize_fa(item.get('category',''))
        # Do not score generic descriptions: they can make unrelated products look relevant.
        hay=name+' '+cat
        score=0
        for t in terms:
            if t in name: score += 6 if t in name.split() else 3
            elif t in cat: score += 2
        if q in name: score += 12
        # semantic aliases should match the product name, not boilerplate descriptions
        for t in terms:
            for key, vals in aliases.items():
                if t==key and any(normalize_fa(v) in name for v in vals): score+=3
        # Small typo tolerance only when the query is a single meaningful word.
        if not score and len(terms)==1 and len(terms[0])>=4:
            import difflib
            token=terms[0]
            name_tokens=name.split()
            if any(difflib.SequenceMatcher(None,token,nt).ratio()>=0.82 for nt in name_tokens): score=2
        if score: scored.append((score,item))
    scored.sort(key=lambda z:(z[0],normalize_fa(z[1].get('name',''))),reverse=True)
    # Keep only meaningful matches; do not return random products just because a word is generic.
    return [item for score,item in scored[:limit] if score>=2]

def knowledge_answer(q:str):
    """Semantic-ish offline retrieval: normalize colloquial Persian, expand synonyms,
    score phrase/token overlap and tolerate small typos without using external APIs."""
    try:
        kb=json.loads((Path(__file__).with_name('beauty_knowledge.json')).read_text(encoding='utf-8'))
    except Exception:
        return None
    qn=normalize_fa(q)
    # Common Persian chat spelling, colloquial forms and cosmetic synonyms.
    replacements={
      'پوستم چربه':'پوست چرب','صورتم چربه':'پوست چرب','پوستم خشکه':'پوست خشک','صورتم خشکه':'پوست خشک',
      'پوستم حساسه':'پوست حساس','پوستم مختلطه':'پوست مختلط','پیشونیم':'پیشانی','گونه هام':'گونه ها',
      'میخوام':'می خواهم','میخام':'می خواهم','می خوام':'می خواهم','میخوام بخرم':'خرید محصول',
      'تینت':'تینت لب','رژ':'رژ لب','رژلب':'رژ لب','کرم پودر':'کرمپودر','فوندیشن':'کرمپودر',
      'فیس واش':'شوینده صورت','واش صورت':'شوینده صورت','میسلار':'میسلار واتر','پاک کننده آرایش':'پاک کردن آرایش',
      'آبرسان':'مرطوب کننده','مرطوب کننده':'مرطوب کننده','ضد آفتاب':'ضدآفتاب','دور چشم':'کرم دور چشم',
      'خط چشم':'آرایش چشم','مژه مصنوعی':'مژه','موهام':'مو','لبام':'لب','صورتم':'صورت',
      'چه جوری':'چطور','چجوری':'چطور','چطوری':'چطور','چیه':'چیست','یعنی چی':'چیست'
    }
    expanded=qn
    for a,b in sorted(replacements.items(),key=lambda x:len(x[0]),reverse=True):
        expanded=expanded.replace(normalize_fa(a),normalize_fa(b))
    stop={'برای','من','چی','چیه','چیست','کدوم','کدام','خوب','مناسب','دارم','میخوام','میخواهم','می','خوام','بخرم','محصول','محصولات','لطفا','یه','یک','از','به','با','را','رو','و','یا','این','اون','هست','هستش','توی','داخل','فروشگاه','لیانا','چه','چطور','چگونه','میشه','می شه','کنم','کنه','بده','میشه','میخوام','می خواهم','یه','واسه','واسه‌','رو','را'}
    tokens=[t for t in expanded.split() if len(t)>1 and t not in stop]
    qset=set(tokens)
    best=None; best_score=0
    try:
        from difflib import SequenceMatcher
    except Exception:
        SequenceMatcher=None
    for item in kb:
        keys=[normalize_fa(x) for x in item.get('keywords',[])]
        title=normalize_fa(item.get('title',''))
        candidates=keys+[title]
        score=0
        for key in candidates:
            if not key: continue
            # Exact phrase match is strongest.
            if key in expanded:
                score=max(score, 14+min(8,len(key.split())*2))
            kt={t for t in key.split() if len(t)>1 and t not in stop}
            if kt and qset:
                overlap=len(kt & qset)
                coverage=overlap/max(1,len(kt))
                query_coverage=overlap/max(1,len(qset))
                score=max(score, overlap*4 + coverage*5 + query_coverage*2)
                # typo tolerance on meaningful tokens only
                if SequenceMatcher and not overlap:
                    fuzzy=sum(1 for qt in qset if any(len(ktok)>=4 and SequenceMatcher(None,qt,ktok).ratio()>=0.84 for ktok in kt))
                    if fuzzy: score=max(score, fuzzy*3+2)
        if score>best_score:
            best=item; best_score=score
    # Higher threshold avoids answering a different topic just because one common word matched.
    if best and best_score>=9:
        return best.get('answer')
    return None

def product_status_answer(query:str):
    q=normalize_fa(query)
    wants_stock=any(x in q for x in ('موجود','موجوده','موجود هست','موجود دارید','دارید','دارین','هست یا نه','وجود داره','پیدا میشه','پیدا می شود'))
    wants_price=any(x in q for x in ('قیمت','چنده','چند تومنه','چقدر'))
    asks_existence=any(x in q for x in ('دارید','دارین','وجود داره','هست یا نه','توی کاتالوگ','در کاتالوگ','توی فروشگاه','داخل فروشگاه','پیدا میشه','پیدا می شود','موجود'))
    if not (wants_stock or wants_price or asks_existence):
        return None
    candidates=product_name_matches(q)
    if not candidates:
        # Strip question wording and search the remaining product phrase.
        cleaned=re.sub(r'\b(آیا|این|اون|محصول|کالا|توی|داخل|فروشگاه|لیانا|موجود|موجوده|هست|هستش|دارید|دارین|قیمت|چنده|چقدر|رو|را|میخوام|میخوام|وجود|داره|پیدا|میشه|می شود|است)\b',' ',q)
        cleaned=re.sub(r'\s+',' ',cleaned).strip()
        candidates=product_name_matches(cleaned)
        if not candidates:
            results=search_products(cleaned,5)
            if results and len(normalize_fa(cleaned).split())>=1:
                # A long, specific product query should not return unrelated siblings.
                top_score=0
                top=results[0]
                name=normalize_fa(top.get('name',''))
                terms=[t for t in normalize_fa(cleaned).split() if len(t)>1]
                overlap=sum(1 for t in terms if t in name)
                if len(terms)>=3 and overlap>=max(3, len(terms)-1):
                    candidates=results[:1]
                else:
                    candidates=results[:3]
    if not candidates: return None
    lines=[]
    for p in candidates[:5]:
        if wants_stock:
            status=customer_stock_status(p.get('stock_file'))
            lines.append('• '+p.get('name','')+' — '+status)
        elif wants_price:
            prices=[]
            for v in p.get('variants',[]):
                raw=v.get('price_raw')
                try: prices.append(f"{int(str(raw).replace(',','')):,}".replace(',','٬')+' تومان')
                except Exception: pass
            lines.append('• '+p.get('name','')+' — '+(', '.join(prices) if prices else 'برای قیمت این محصول با فروشگاه هماهنگ کن'))
        else: lines.append('• '+p.get('name','')+' — این محصول رو در لیانا پیدا کردم')
    prefix='🔎 این محصولات رو در لیانا پیدا کردم:\n' if not wants_stock and not wants_price else ('📦 وضعیت موجودی:\n' if wants_stock else '💰 قیمت محصولات:\n')
    suffix='\n\nتوجه: قیمت و موجودی ممکن است تغییر کرده باشد؛ برای اطمینان نهایی از فروشگاه استعلام بگیر.'
    return prefix+'\n'.join(lines)+suffix

def offline_answer(user_text:str)->str:
    import random
    q=normalize_fa(user_text)
    talk=is_small_talk(q)
    if talk=='bye': return random.choice(('فعلاً، ممنون که به لیانا سر زدی 🌷 هر وقت سؤال محصول داشتی برگرد.','خداحافظ 💕 روزت قشنگ! هر وقت درباره محصولات سؤال داشتی من اینجام.','به امید دیدار 🌸'))
    if talk=='how': return random.choice(('ممنون که پرسیدی 🌷 آماده‌ام برای پیدا کردن محصول کمکت کنم؛ تو چطوری؟','مرسی از لطفت 💗 بگو دنبال چه محصولی هستی؟','خوبم و آماده‌ام کمک کنم 🌸'))
    if talk=='greet' and len(q.split())<=6: return random.choice(('سلام عزیزم 🌷 خوش اومدی به لیانا شاپ! چه محصولی رو برات پیدا کنم؟','سلام سلام 💕 درباره محصولات آرایشی یا مراقبت پوست چی می‌خوای بدونی؟','درود 🌸 می‌تونم هم سؤال‌های آرایشی رو جواب بدم هم محصولات لیانا رو پیدا کنم.'))
    if talk=='thanks': return random.choice(('خواهش می‌کنم 💗 سؤال دیگه‌ای درباره محصولات داشتی بپرس.','قربونت 🌷 خوشحالم کمک کردم.','خواهش می‌کنم! 🌸'))
    if talk=='positive': return random.choice(('خوشحالم که به کارت اومد 🌷 هر سؤال دیگه‌ای درباره محصولات یا آرایش داشتی بپرس.','قربونت 💗 برای انتخاب محصول یا پیدا کردن قیمت و موجودی هم کمکت می‌کنم.'))
    if talk=='casual': return random.choice(('من اینجام که درباره محصولات و خرید از لیانا کمکت کنم 😊 تو بگو دنبال چه چیزی هستی؟','فعلاً کارم راهنمایی درباره آرایش و محصولات لیاناست 🌷 چیزی می‌خوای برات پیدا کنم؟'))
    if any(x in q for x in ('معرفی فروشگاه','معرفی کن','فروشگاه رو معرفی','درباره فروشگاه','آدرس فروشگاه','کجاست فروشگاه','کجا هستید','شماره فروشگاه','شماره تماس','شعبه های فروشگاه','شعبه های لیانا')):
        return ('🌸 فروشگاه آرایشی و بهداشتی لیانا در بندر گناوه دو شعبه دارد:\n\n📍 شعبه اول: پارکینگ ملت، راسته اصلی، بعد از داروخانه شبانه‌روزی دکتر معرفت، آخرین مغازه سمت راست.\n📍 شعبه دوم: پارکینگ ملت، پاساژ جام جم، پلاک ۱۰.\n\n🛍 سایت فروشگاه: '+SHOP_URL+'\n☎️ برای قیمت و موجودی روز می‌تونی با فروشگاه تماس بگیری: '+PHONE_1+' یا '+PHONE_2)
    if any(x in q for x in ('ساعت کاری','چه ساعتی باز','کی باز','باز هستید','تا چه ساعتی')):
        return '🌷 ساعت کاری دقیق در اطلاعات فعلی من ثبت نشده؛ برای اینکه اشتباه راهنمایی‌ات نکنم، لطفاً با فروشگاه تماس بگیر: '+PHONE_1+' یا '+PHONE_2
    if talk=='positive': return random.choice(('خوشحالم که به کارت اومد 🌷 هر سؤال دیگه‌ای درباره محصولات یا آرایش داشتی بپرس.','قربونت 💗 برای انتخاب محصول یا پیدا کردن قیمت و موجودی هم کمکت می‌کنم.'))
    if talk=='casual': return random.choice(('من اینجام که درباره محصولات و خرید از لیانا کمکت کنم 😊 تو بگو دنبال چه چیزی هستی؟','فعلاً کارم راهنمایی درباره آرایش و محصولات لیاناست 🌷 چیزی می‌خوای برات پیدا کنم؟'))
    if any(x in q for x in ('معرفی فروشگاه','معرفی کن','فروشگاه رو معرفی','درباره فروشگاه','آدرس فروشگاه','کجاست فروشگاه','کجا هستید','شماره فروشگاه','شماره تماس','شعبه های فروشگاه','شعبه های لیانا')):
        return ('🌸 فروشگاه آرایشی و بهداشتی لیانا در بندر گناوه دو شعبه دارد:\n\n📍 شعبه اول: پارکینگ ملت، راسته اصلی، بعد از داروخانه شبانه‌روزی دکتر معرفت، آخرین مغازه سمت راست.\n📍 شعبه دوم: پارکینگ ملت، پاساژ جام جم، پلاک ۱۰.\n\n🛍 سایت فروشگاه: '+SHOP_URL+'\n☎️ برای قیمت و موجودی روز می‌تونی با فروشگاه تماس بگیری: '+PHONE_1+' یا '+PHONE_2)
    if any(x in q for x in ('ساعت کاری','چه ساعتی باز','کی باز','باز هستید','تا چه ساعتی')):
        return '🌷 ساعت کاری دقیق در اطلاعات فعلی من ثبت نشده؛ برای اینکه اشتباه راهنمایی‌ات نکنم، لطفاً با فروشگاه تماس بگیر: '+PHONE_1+' یا '+PHONE_2
    # Answer stock/price/existence questions against product names and catalog records first.
    status=product_status_answer(q)
    if status: return status
    # A question about cosmetics should be answered from the curated FAQ, not guessed from a product title.
    answer=knowledge_answer(q)
    if answer:
        # For shopping/recommendation questions, pair the general advice with actual catalog matches.
        recommendation_words=('پیشنهاد','چی بخرم','چه محصول','کرم پودر','کرمپودر','فوندیشن','ضدآفتاب','ضد آفتاب','رژ لب','رژلب','ریمل','پرایمر','کانسیلر','آبرسان','شوینده')
        if any(x in q for x in recommendation_words) and not any(x in q for x in ('خشک شده','آب بزنم','تاریخ انقضا','چند وقت یکبار')):
            results=search_products(q,4)
            if results:
                answer += '\n\n🔎 این محصولات مرتبط را هم در لیانا پیدا کردم:\n' + '\n'.join('• '+p['name'] for p in results[:4]) + '\n\nمناسب‌بودن دقیق این کالاها برای نوع پوست یا هدف تو فقط با مشخصات سازنده قابل تأیید است.'
        return answer
    # Product search: report matches only when query is actually in the beauty domain.
    if is_beauty_question(q):
        results=search_products(q,6)
        if results:
            lines=[]
            for p in results:
                prices=[]
                for v in p.get('variants',[])[:2]:
                    try:
                        prices.append(f"{int(str(v.get('price_raw')).replace(',','')):,}".replace(',','٬')+' تومان')
                    except Exception: pass
                extra=(' — قیمت: '+', '.join(prices)) if prices else ''
                lines.append('• '+p['name']+extra+'\n  '+customer_stock_status(p.get('stock_file'))) 
            return '🔎 این محصولات لیانا به سؤال تو نزدیک‌ترند:\n'+'\n'.join(lines)+'\n\nقیمت و موجودی ممکن است تغییر کرده باشد؛ برای اطمینان از وضعیت امروز با فروشگاه هماهنگ کن: '+SHOP_URL
        # General advice fallback, without pretending to know a product exists.
        return '🌷 حتماً کمکت می‌کنم. برای اینکه پیشنهاد دقیق‌تری بدم، بگو دنبال چه محصولی هستی و اگر مربوط به پوستته، نوع پوستت چیه (چرب، خشک، مختلط یا حساس) و حدود بودجه‌ات چقدره؟ اگر اسم محصول خاصی مدنظرت هست هم بنویس تا داخل محصولات لیانا جست‌وجو کنم. نمی‌خوام اسم یا موجودی محصولی رو حدس بزنم.'
    return '🌸 من دستیار لیانا شاپ هستم و می‌تونم درباره آرایش، مراقبت پوست و مو، انتخاب محصولات، قیمت و موجودی، آدرس فروشگاه و خرید راهنمایی‌ات کنم. مثلاً بنویس «یه تینت لب خوب می‌خوام»، «برای پوست چرب چی مناسبه؟» یا «فروشگاه کجاست؟». سؤال‌های کاملاً نامرتبط رو پاسخ نمی‌دم.'

async def call_groq(user_text:str):
    """Use verified local product records first, then optional AI with relevant product context."""
    q=normalize_fa(user_text)
    # Price/stock/existence questions must be grounded in the store's current catalog data.
    if any(x in q for x in ('قیمت','چنده','چقدر','موجود','دارید','دارین','وجود داره','هست یا نه','پیدا میشه')):
        local=product_status_answer(user_text)
        if local:
            return local
    if not GROQ_API_KEY:
        return offline_answer(user_text)
    products=product_context(user_text, limit=10)
    kb_answer=knowledge_answer(user_text) or ''
    system_prompt=(
      'تو مشاور فروش خوش‌برخورد و آگاه فروشگاه «لیانا شاپ» هستی و فقط به فارسی طبیعی پاسخ می‌دهی. '
      'به مشتری هرگز درباره فایل، JSON، دیتابیس، کد، منبع داخلی یا روش فنی دریافت اطلاعات حرف نزن. '
      'از اطلاعات محصولی که در زمینه گفتگو آمده استفاده کن؛ نام، رنگ، ترکیبات، حجم، SPF، ماندگاری، ادعای درمانی یا سازگاری را اگر منبعی تأیید نکرده، نساز. '
      'اگر رنگ‌ها/ویژگی‌ها در داده محصول ثبت نشده‌اند، شفاف بگو اطلاعات دقیق آن مورد را نداریم و لینک محصول/فروشگاه را پیشنهاد بده. '
      'برای پیشنهاد محصول، ابتدا نیاز و نوع پوست/تناژ/بودجه مشتری را در نظر بگیر؛ اگر کافی نیست یک سؤال کوتاه بپرس. '
      'قیمت و موجودی ممکن است تغییر کند؛ فقط اعداد و وضعیت موجود در داده مرتبط را گزارش کن و برای تأیید نهایی ارجاع به فروشگاه بده. '
      'از ادعاهای پزشکی قطعی پرهیز کن، در حساسیت شدید یا مشکلات پوستی ماندگار مراجعه به متخصص را پیشنهاد بده. '
      'کوتاه، کاربردی، گرم و حرفه‌ای بنویس؛ پاسخ را به شکل فروشنده واقعی ارائه کن و درباره محدودیت فنی سیستم توضیح نده.'
    )
    context='اطلاعات مرتبط محصولات فروشگاه (فقط برای راهنمایی دقیق؛ این داده‌ها را به شکل فایل یا منبع فنی به مشتری معرفی نکن):\n'+json.dumps(products,ensure_ascii=False)
    if kb_answer:
        context+='\nدانش عمومی مرتبط: '+kb_answer
    payload={'model':GROQ_MODEL,'messages':[{'role':'system','content':system_prompt},{'role':'user','content':context+'\n\nپرسش مشتری: '+user_text}],'temperature':0.35,'max_tokens':700}
    try:
        timeout=aiohttp.ClientTimeout(total=18)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post('https://api.groq.com/openai/v1/chat/completions',headers={'Authorization':'Bearer '+GROQ_API_KEY,'Content-Type':'application/json'},json=payload) as response:
                if response.status==200:
                    body=await response.json()
                    answer=body['choices'][0]['message']['content'].strip()
                    if answer: return answer
    except Exception:
        pass
    return offline_answer(user_text)

@dp.callback_query(F.data=='ai:open')
async def cb_ai_open(c:CallbackQuery):
    uid=c.from_user.id
    AI_USERS.add(uid); OWNER_PASSWORD_PENDING.discard(uid)
    await c.message.answer('🤖 <b>دستیار محصولات لیانا شاپ</b>\n\nبه پرسش‌های رایج آرایش و مراقبت پوست و مو پاسخ می‌دهم، سؤال‌های محاوره‌ای را می‌فهمم و در محصولات لیانا جست‌وجو می‌کنم؛ اگر اطلاعات دقیق رنگ یا ترکیبات محصول مشخص نباشد، حدس نمی‌زنم و راه بررسی را پیشنهاد می‌دهم.\n\nمی‌توانی درباره مراقبت پوست، اصول آرایش، ایمنی محصولات، نام محصول، قیمت یا موجودی محصولات بپرسی؛ سؤال‌های نامرتبط پاسخ داده نمی‌شوند.\n\nپیامت را بفرست؛ برای خروج دکمه زیر را بزن.',reply_markup=ai_kb())
    await c.answer()

@dp.callback_query(F.data=='ai:owner')
async def cb_ai_owner(c:CallbackQuery):
    uid=c.from_user.id
    if not is_owner_id(uid):
        await c.answer('این گزینه فقط برای صاحب ربات فعال است.',show_alert=True); return
    OWNER_PASSWORD_PENDING.add(uid); AI_USERS.discard(uid)
    await c.message.answer('🔐 رمز مدیریت را بفرست. برای امنیت، پیام رمز پس از دریافت حذف می‌شود.')
    await c.answer()

@dp.callback_query(F.data=='ai:exit')
async def cb_ai_exit(c:CallbackQuery):
    uid=c.from_user.id; AI_USERS.discard(uid); OWNER_PASSWORD_PENDING.discard(uid)
    await c.message.answer('از حالت هوش مصنوعی خارج شدی 🌷',reply_markup=home_kb())
    await c.answer()

@dp.message(F.text)
async def text_search(m:Message):
    uid=m.from_user.id
    q=(m.text or '').strip()
    if uid in OWNER_PASSWORD_PENDING:
        try: await m.delete()
        except Exception: pass
        OWNER_PASSWORD_PENDING.discard(uid)
        if is_owner_id(uid) and OWNER_PASSWORD and q==OWNER_PASSWORD:
            OWNER_AI_USERS.add(uid); AI_USERS.add(uid)
            await m.answer('✅ حالت آزمایشی صاحب ربات فعال شد. پاسخ‌گویی محلی فعال است و به API نیاز ندارد.',reply_markup=ai_kb())
        else:
            OWNER_AI_USERS.discard(uid)
            await m.answer('❌ رمز یا دسترسی صاحب ربات معتبر نیست.',reply_markup=home_kb())
        return
    if uid in AI_USERS and not q.startswith('/'):
        if len(q)<2: return
        owner_mode=(uid in OWNER_AI_USERS and is_owner_id(uid))
        if not is_beauty_question(q):
            await m.answer('🌸 من فقط درباره محصولات آرایشی و بهداشتی فروشگاه لیانا شاپ کمک می‌کنم. لطفاً درباره محصول، نوع پوست، رنگ، قیمت یا موجودی سؤال بپرس.\n\n',reply_markup=ai_kb())
            return
        answer=await call_groq(q)
        await m.answer(esc(answer),reply_markup=ai_kb())
        return
    if q.startswith('/') or len(q)<2: return
    found=[p for p in DATA if normalize_fa(q) in normalize_fa(p['name']) and p.get('published',True)][:40]
    if not found:
      await m.answer("محصولی با این نام پیدا نکردم. می‌تونی از دسته‌بندی‌ها انتخاب کنی یا نام دیگری بنویسی.",reply_markup=categories_kb()); return
    await m.answer(f"🔎 <b>{len(found)}</b> نتیجه برای «{esc(q)}» (حداکثر ۴۰ نتیجه):",reply_markup=paginated(found,0,'searchpage:'+quote(q,safe='')))
@dp.callback_query(F.data=='home')
async def cb_home(c:CallbackQuery):
    AI_USERS.discard(c.from_user.id)
    OWNER_PASSWORD_PENDING.discard(c.from_user.id)
    await c.message.edit_text(HOME_TEXT,reply_markup=home_kb()); await c.answer()
@dp.callback_query(F.data=='back')
async def cb_back(c:CallbackQuery):
    AI_USERS.discard(c.from_user.id)
    OWNER_PASSWORD_PENDING.discard(c.from_user.id)
    await c.message.edit_text("از منوی زیر ادامه بده 🌷",reply_markup=home_kb()); await c.answer()
@dp.callback_query(F.data=='categories')
async def cb_cats(c:CallbackQuery): await c.message.edit_text("<b>🗂 دسته‌بندی محصولات</b>\nکدوم دسته رو می‌خوای ببینی؟",reply_markup=categories_kb()); await c.answer()
@dp.callback_query(F.data.startswith('catalog:'))
async def cb_catalog(c:CallbackQuery):
    page=int(c.data.split(':')[1]); items=[p for p in DATA if p.get('published',True)]
    await c.message.edit_text(f"<b>🛍 محصولات لیانا</b>\nصفحه {page+1} از {(len(items)+PAGE_SIZE-1)//PAGE_SIZE}\nبرای مشاهده جزئیات، محصول رو انتخاب کن.",reply_markup=paginated(items,page,'catalog')); await c.answer()
@dp.callback_query(F.data.startswith('cat:'))
async def cb_cat(c:CallbackQuery):
    _,code,page=c.data.split(':',2); page=int(page)
    try: cat=cats()[int(code)]
    except (ValueError,IndexError): cat=None
    if cat is None: await c.answer('دسته‌بندی پیدا نشد',show_alert=True); return
    items=[p for p in DATA if p['category']==cat and p.get('published',True)]
    await c.message.edit_text(f"<b>✨ {esc(cat)}</b>\n{len(items)} محصول در این دسته",reply_markup=paginated(items,page,'catpage:'+code)); await c.answer()
@dp.callback_query(F.data.startswith('catpage:'))
async def cb_catpage(c:CallbackQuery):
    _,page,code=c.data.split(':',2); page=int(page)
    try: cat=cats()[int(code)]
    except (ValueError,IndexError): cat=None
    if cat is None: await c.answer('دسته‌بندی پیدا نشد',show_alert=True); return
    items=[p for p in DATA if p['category']==cat and p.get('published',True)]
    await c.message.edit_text(f"<b>✨ {esc(cat)}</b>\n{len(items)} محصول در این دسته",reply_markup=paginated(items,page,'catpage:'+code)); await c.answer()
@dp.callback_query(F.data.startswith('searchpage:'))
async def cb_searchpage(c:CallbackQuery):
    _,page,query=c.data.split(':',2); page=int(page); query=__import__('urllib.parse').parse.unquote(query)
    items=[p for p in DATA if normalize_fa(query) in normalize_fa(p['name']) and p.get('published',True)][:40]
    await c.message.edit_text(f"🔎 نتایج جست‌وجو برای «{esc(query)}»",reply_markup=paginated(items,page,'searchpage:'+quote(query,safe=''))); await c.answer()
@dp.callback_query(F.data.startswith('qpage:'))
async def cb_qpage(c:CallbackQuery):
    _,page,key=c.data.split(':',2); page=int(page); items=matching_question(key)
    await c.message.edit_text("<b>💡 پیشنهادهای دسته‌بندی‌شده</b>\nاین فهرست از دسته‌بندی نام محصولات ساخته شده؛ برای سازگاری با نوع پوست، مشخصات سازنده را بررسی کن.",reply_markup=paginated(items,page,'qpage:'+key)); await c.answer()
@dp.callback_query(F.data.startswith('product:'))
async def cb_product(c:CallbackQuery):
    p=find(c.data.split(':',1)[1])
    if not p: await c.answer('محصول پیدا نشد',show_alert=True); return
    try:
      if p.get('image'): await c.message.answer_photo(p['image'],caption=product_text(p),reply_markup=product_keyboard(p))
      else: await c.message.answer(product_text(p),reply_markup=product_keyboard(p))
    except Exception: await c.message.answer(product_text(p),reply_markup=product_keyboard(p))
    await c.answer()
@dp.callback_query(F.data=='questions')
async def cb_questions(c:CallbackQuery):
    await c.message.edit_text("<b>💡 راهنمای انتخاب محصول</b>\nیک موضوع رو انتخاب کن. این پیشنهادها بر اساس دسته‌بندی نام محصولات هستند؛ برای تعیین دقیق نوع پوست و سازگاری، مشخصات سازنده باید بررسی شود.",reply_markup=search_kb()); await c.answer()
@dp.callback_query(F.data.startswith('q:'))
async def cb_q(c:CallbackQuery):
    key=c.data.split(':')[1]; items=matching_question(key)
    titles={'sunscreen':'ضدآفتاب‌ها','skincare':'مراقبت پوست و شوینده‌ها','face':'آرایش صورت','lips':'محصولات لب','eyes':'چشم و ابرو','body':'عطر و بهداشت بدن'}
    if not items:
      await c.message.edit_text('فعلاً محصولی در این دسته‌بندی پیدا نشد.',reply_markup=search_kb())
    else:
      await c.message.edit_text(f"<b>💡 {titles.get(key,'پیشنهاد محصولات')}</b>\nاین فهرست بر اساس نام محصول مرتب شده، نه رتبه‌بندی تخصصی. محصول رو انتخاب کن تا اطلاعات و راه استعلام رو ببینی.",reply_markup=paginated(items,0,'qpage:'+key))
    await c.answer()
@dp.callback_query(F.data=='stocksearch')
async def cb_stock(c:CallbackQuery):
    await c.message.answer("🔎 <b>جست‌وجوی محصول</b>\nنام محصول رو به صورت یک پیام بفرست تا بین محصولات جست‌وجو کنم. برای برگشت از دکمه خانه استفاده کن.",reply_markup=InlineKeyboardMarkup(inline_keyboard=[nav_row()])); await c.answer()
@dp.callback_query(F.data=='shop')
async def cb_shop(c:CallbackQuery):
    text=("<b>🏬 فروشگاه لیانا</b>\n\n"
    "📍 <b>شعبه ۱:</b> بندر گناوه، پارکینگ ملت، راسته اصلی، بعد از داروخانه شبانه‌روزی دکتر معرفت، آخرین مغازه سمت راست.\n\n"
    "📍 <b>شعبه ۲:</b> بندر گناوه، پارکینگ ملت، پاساژ جام جم، پلاک ۱۰.\n\n"
    f"☎️ <b>استعلام قیمت و موجودی:</b>\n<code>{esc(PHONE_1)}</code>\n<code>{esc(PHONE_2)}</code>\n\n"
    "برای تماس، شماره را کپی کن یا از دکمه‌های زیر استفاده کن.")
    kb=InlineKeyboardMarkup(inline_keyboard=[[btn('📞 شماره شعبه ۱','phone:1'),btn('📞 شماره شعبه ۲','phone:2')],[btn('📣 تلگرام',url=TG_URL),btn('🛒 سایت',url=SHOP_URL)],nav_row()])
    await c.message.edit_text(text,reply_markup=kb); await c.answer()
@dp.callback_query(F.data.startswith('phone:'))
async def cb_phone(c:CallbackQuery):
    number=PHONE_1 if c.data.endswith(':1') else PHONE_2
    await c.answer()
    await c.message.answer('📞 شماره تماس فروشگاه:\n<code>'+esc(number)+'</code>\nشماره را لمس و کپی کن.')

@dp.callback_query(F.data.startswith('ask:'))
async def cb_ask(c:CallbackQuery):
    _,kind,pid=c.data.split(':',2); p=find(pid)
    if not p: await c.answer('محصول پیدا نشد',show_alert=True); return
    lines=variant_lines(p)
    if kind=='price':
        if lines:
            text=(f"<b>💰 قیمت محصول</b>\n\nمحصول: <b>{esc(p['name'])}</b>\n\n"+'\n'.join(lines)+'\n\n<i>قیمت‌ها ممکن است تغییر کرده باشند؛ پیش از خرید قیمت نهایی را از فروشگاه بپرس.</i>')
        else:
            text=(f"<b>💰 استعلام قیمت</b>\n\nمحصول: <b>{esc(p['name'])}</b>\nقیمت این محصول مشخص نیست؛ لطفاً از فروشگاه استعلام بگیر.")
    else:
        status=customer_stock_status(p.get('stock_file'))
        text=(f"<b>📦 وضعیت موجودی</b>\n\nمحصول: <b>{esc(p['name'])}</b>\nوضعیت: <b>{esc(status)}</b>")
        if lines: text += '\n\n'+'\n'.join(lines)
        text += '\n\n<i>ممکن است این وضعیت با موجودی لحظه‌ای فروشگاه فرق داشته باشد.</i>'
    kb=InlineKeyboardMarkup(inline_keyboard=[[btn('📞 شماره شعبه ۱','phone:1'),btn('📞 شماره شعبه ۲','phone:2')],[btn('🔗 صفحه محصول',url=p['product_url'])],nav_row()])
    await c.message.answer(text,reply_markup=kb); await c.answer()

async def main():
    init_db()
    if not TOKEN: raise SystemExit('BOT_TOKEN را در فایل .env وارد کن؛ از BotFather دریافت می‌شود.')
    await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())
