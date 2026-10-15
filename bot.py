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
    """Show only catalog facts; avoid generic or unverified generated product bios."""
    variants=p.get('variants',[])
    lines=[f"<b>✨ {esc(p.get('name','محصول'))}</b>"]
    if p.get('category'):
        lines.append('<b>دسته‌بندی:</b> '+esc(p.get('category')))
    labels=[]
    for v in variants[:12]:
        attr=(v.get('attribute') or v.get('name') or '').strip()
        if attr and attr not in labels: labels.append(attr)
    if labels:
        lines.append('\n<b>تنوع‌های ثبت‌شده:</b> '+ '، '.join(esc(x) for x in labels))
    prices=variant_lines(p)
    if prices:
        lines.append('\n<b>قیمت و وضعیت:</b>\n'+'\n'.join(prices))
    lines.append('\nبرای رنگ، ترکیبات یا ویژگی‌هایی که اینجا مشخص نشده‌اند، تصویر و توضیحات بسته‌بندی محصول را بررسی کن.')
    return '\n'.join(lines)

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
    history_clear(m.from_user.id)
    await m.answer(HOME_TEXT,reply_markup=home_kb())
@dp.message(Command("home"))
async def home(m:Message):
    AI_USERS.discard(m.from_user.id)
    OWNER_PASSWORD_PENDING.discard(m.from_user.id)
    history_clear(m.from_user.id)
    await m.answer(HOME_TEXT,reply_markup=home_kb())
# --- AI chat, weekly quota and owner test mode ---
def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute("CREATE TABLE IF NOT EXISTS ai_usage (user_id INTEGER NOT NULL, week_key TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0, PRIMARY KEY(user_id, week_key))")
        # Short conversation memory lets follow-up answers such as «گندمیه»
        # be interpreted against the question the assistant asked previously.
        con.execute("CREATE TABLE IF NOT EXISTS ai_history (user_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at INTEGER NOT NULL)")

def history_get(user_id:int, limit:int=12):
    with sqlite3.connect(DB_PATH) as con:
        rows=con.execute("SELECT role,content FROM ai_history WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?",(user_id,limit)).fetchall()
    return [{'role':r,'content':c} for r,c in reversed(rows)]

def history_add(user_id:int, role:str, content:str):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("INSERT INTO ai_history(user_id,role,content,created_at) VALUES(?,?,?,?)",(user_id,role,str(content)[:3000],int(time.time())))
        # Keep the latest 12 messages per customer; old turns are not needed.
        con.execute("DELETE FROM ai_history WHERE user_id=? AND rowid NOT IN (SELECT rowid FROM ai_history WHERE user_id=? ORDER BY created_at DESC, rowid DESC LIMIT 12)",(user_id,user_id))

def history_clear(user_id:int):
    with sqlite3.connect(DB_PATH) as con:
        con.execute("DELETE FROM ai_history WHERE user_id=?",(user_id,))

def contextual_query(user_text:str, history:list):
    """Join short follow-up answers to the active product/beauty question."""
    q=normalize_fa(user_text)
    if not history:
        return user_text
    last_assistant=next((x['content'] for x in reversed(history) if x.get('role')=='assistant'),'')
    previous_user=next((x['content'] for x in reversed(history) if x.get('role')=='user'),'')
    if len(q.split())<=6 and previous_user:
        assistant_q=normalize_fa(last_assistant)
        followup_markers=('رنگ پوست','تناژ','نوع پوست','پوستت چرب','پوستت خش','بودجه','برای تیرگی','برای لک','چه نتیجه','کدام محصول','کدوم محصول')
        if any(normalize_fa(x) in assistant_q for x in followup_markers):
            return f"{previous_user}؛ پاسخ تکمیلی مشتری: {user_text}؛ سؤال قبلی دستیار: {last_assistant}"
    return user_text

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
    """Provide AI with best-ranked catalogue matches, their descriptions and variants."""
    matches=search_products(query,limit)
    # If user asks for a category/skin-based recommendation, retrieve product families.
    if not matches:
        q=normalize_fa(query)
        for term in ('تینت','کانسیلر','پنکیک','رژ لب','کرم پودر','آبرسان','نمک بدن','ریمل','رژگونه'):
            if term in q:
                matches=search_products(term,limit); break
    out=[]
    for p in matches[:limit]:
        variants=[]
        for v in p.get('variants',[])[:8]:
            variants.append({'variant':str(v.get('attribute') or v.get('name') or ''),'price_raw':str(v.get('price_raw') or ''),'stock_file':str(v.get('stock_file') or p.get('stock_file') or 'نامشخص')})
        out.append({'name':p.get('name',''),'category':p.get('category',''),'description':p.get('description') or describe_product(p),'variants':variants,'product_url':p.get('product_url','')})
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
 'positive':('چه عالی','عالیه','آفرین','خوبه','خوب شد','چه خوب','عالی بود','دمت گرم','اوکی','اوکیه','باشه','قبوله','درسته'),
 'casual':('کاره نداری','کاری نداری','چه خبرها','چیکار میکنی','چیکار می کنی','چه خبر','چخبر','چخبره')
}

def normalize_fa(s:str)->str:
    s=str(s or '').casefold()
    table=str.maketrans({'ي':'ی','ك':'ک','ۀ':'ه','ة':'ه','أ':'ا','إ':'ا','ؤ':'و','ئ':'ی','‌':' ','ـ':' '})
    s=s.translate(table)
    s=re.sub(r"[^\w\s؀-ۿ]", " ", s)
    s=re.sub(r'\s+',' ',s).strip()
    # Frequent Persian chat spellings/typos, without changing product names materially.
    fixes={
      'چخبر':'چه خبر','چخبره':'چه خبره','چطوری':'چطوری','چخبرها':'چه خبرها',
      'میخام':'می خواهم','میخوام':'می خواهم','میخاستم':'می خواستم','میخاست':'می خواست',
      'میشه':'می شود','میشه؟':'می شود','مناسبه':'مناسب است','پوستم چربه':'پوست من چرب است',
      'پوستم خشکه':'پوست من خشک است','پوستم حساسه':'پوست من حساس است','پوستم مختلطه':'پوست من مختلط است',
      'کانسیلرر':'کانسیلر','تینتت':'تینت','رژلب':'رژ لب','کرمپودر':'کرم پودر',
      'ضدافتاب':'ضد آفتاب','ضدآفتاب':'ضد آفتاب','عسلیی':'عسلی','پوستم جوش میزنه':'پوست من جوش می زند',
      'جوش میزنم':'جوش می زنم','دوستت دارم':'دوستت دارم','دارین':'دارید','موجوده':'موجود است',
      'کدوم':'کدام','واسه':'برای','رو':'را'
    }
    for a,b in sorted(fixes.items(),key=lambda x:len(x[0]),reverse=True):
        s=re.sub(r'(?<!\w)'+re.escape(a)+r'(?!\w)',b,s)
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
    # Short answers can be meaningful when the assistant just asked about skin tone/type.
    if len(qn.split())<=4 and any(x in qn for x in ('گندمی','سبزه','روشن','متوسط','تیره','گرم','سرد','خنثی','چرب','خشک','مختلط','حساس','زیر چشم','لک','بودجه')): return True
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
    """Useful product-specific copy based on product name/category; avoids unsupported manufacturer claims."""
    name=str(p.get('name','')).strip(); cat=str(p.get('category','محصولات آرایشی و بهداشتی'))
    n=normalize_fa(name); c=normalize_fa(cat)
    # Brand/product-name cues add meaningful details while clearly avoiding unverified ingredient claims.
    if 'نمک بدن' in n:
        scent='هلو و توت‌فرنگی' if ('هلو' in n and 'توت فرنگی' in n) else ('هلو' if 'هلو' in n else ('آلوئه‌ورا' if 'الوئه' in n else ''))
        base=f'نمک بدن برای روتین مراقبت بدن و لایه‌برداری ملایمِ سطح پوست استفاده می‌شود؛ {"رایحه/مدل نام‌گذاری‌شده «"+scent+"» دارد، اما عطر و ترکیبات دقیق را باید از بسته‌بندی تأیید کرد. " if scent else "مدل مخصوص مراقبت بدن است؛ ترکیبات و میزان لایه‌برداری را از بسته‌بندی بررسی کن. "}روی پوست سالم و مرطوب، با فشار کم و طبق دستور محصول استفاده کن؛ روی زخم، پوست تحریک‌شده یا بلافاصله بعد از اصلاح به کار نبر. اگر پوستت حساس است اول بخش کوچکی را امتحان کن.'
    elif 'تینت' in n:
        base='تینت معمولاً برای ایجاد رنگ سبک و طبیعی روی لب و گاهی گونه به کار می‌رود؛ با لایه‌گذاری می‌توان شدت رنگ را بیشتر کرد. لب را پیش از استفاده نرم و تمیز کن و مقدار کمی بزن. اگر محصول برای لب و گونه هر دو معرفی نشده، فقط در محل درج‌شده روی بسته‌بندی استفاده کن. رنگ دقیق این مدل را از تنوع‌ها و عکس محصول بررسی کن.'
    elif 'کانسیلر' in n:
        base='کانسیلر برای پوشاندن موضعی تیرگی زیر چشم، قرمزی یا لک در آرایش استفاده می‌شود. برای زیر چشم معمولاً رنگی نزدیک به پوست یا کمی روشن‌تر انتخاب می‌شود؛ برای پوشاندن لک، هماهنگی با رنگ پوست طبیعی‌تر است. اگر خطوط زیر چشم داری، مقدار کم بزن و با ضربه‌های آرام محو کن. برای پیشنهاد کد رنگ دقیق باید تناژ پوست و رنگ‌های موجود مشخص باشد.'
    elif 'پنکیک' in n:
        spf=' نام محصول به SPF50 اشاره می‌کند؛ برای محافظت واقعی، دستور مصرف و ادعای طیف محافظت را روی بسته‌بندی بررسی کن و پنکیک را جایگزین کامل ضدآفتاب مستقل فرض نکن.' if 'spf50' in n or 'spf 50' in n else ''
        base='پنکیک برای مرتب‌کردن آرایش پایه، یکدست‌تر نشان‌دادن پوست و کمک به کنترل برق ظاهری استفاده می‌شود. با پد تمیز، لایه نازک بزن و در صورت نیاز روی نواحی موردنظر بیشتر کار کن. رنگ باید با پوست صورت و گردن هماهنگ باشد؛ برای انتخاب کد دقیق، رنگ‌های موجود و تناژ پوستت را مقایسه کن.'+spf
    elif 'کرم پودر' in n or 'کرمپودر' in n or 'فوندیشن' in n:
        base='کرم‌پودر برای یکدست‌تر نشان‌دادن ظاهر پوست و ساختن پایه آرایش است. برای پوست چرب معمولاً بافت سبک و فینیش مات‌تر، و برای پوست خشک معمولاً فرمول راحت‌تر و آبرسان‌تر انتخاب می‌شود؛ اما فرمول همین مدل باید از توضیحات سازنده تأیید شود. رنگ را با تناژ گردن در نور طبیعی بسنج و ابتدا لایه نازک بزن.'
        if 'کانسیلر دار' in n: base+=' این مدل طبق نام محصول همراه کانسیلر معرفی شده؛ محتویات دقیق پک را با عکس و مشخصات کالا بررسی کن.'
    elif any(x in n for x in ('رژ لب','رژلب','رژ مایع','رژ جامد','لیپ گلاس','بالم لب','مربا لب','خط لب','سرم رنگی لب')):
        base='این محصول برای آرایش لب است. برای نتیجه تمیز، لب را نرم نگه دار و از مرکز لب به سمت گوشه‌ها بزن؛ اگر پوشش پررنگ‌تر می‌خواهی، لایه دوم را پس از ثابت‌شدن لایه اول اضافه کن. جلوه مات/براق، ماندگاری و رنگ دقیق را از مشخصات و تصویر همین مدل بررسی کن.'
    elif 'رژگونه' in n:
        base='رژگونه برای اضافه‌کردن رنگ و شادابی به گونه‌هاست. مقدار کمی روی برجستگی گونه بزن و به سمت شقیقه محو کن؛ برای پوست روشن معمولاً رنگ‌های ملایم و برای پوست متوسط تا تیره رنگ‌های عمیق‌تر هم می‌توانند زیبا باشند، ولی تناژ نهایی به رنگ دقیق محصول بستگی دارد.'
    elif 'سرم' in n and ('آکنه' in n or 'جوش' in n):
        base='این محصول با عنوان مرتبط با جوش/آکنه عرضه می‌شود، اما نام به‌تنهایی ترکیبات یا اثربخشی درمانی را ثابت نمی‌کند. پیش از استفاده، مواد مؤثره و دستور سازنده را بررسی کن؛ محصول جدید را ابتدا روی بخش کوچکی از پوست امتحان کن و در صورت جوش‌های دردناک یا ماندگار از متخصص پوست کمک بگیر.'
    elif 'فوم شستشو' in n or 'فیس واش' in n or 'شوینده' in n:
        cue=' مدل هلو' if 'هلو' in n else ''
        base=f'شوینده برای پاک‌کردن آلودگی، چربی و باقی‌مانده‌های روزانه از پوست استفاده می‌شود.{cue} پوست را با آب ولرم خیس کن، طبق دستور مقدار مناسب را به‌آرامی ماساژ بده و کامل آبکشی کن. اگر بعد از شست‌وشو کشیدگی یا سوزش داشتی، دفعات مصرف را کم کن یا محصول ملایم‌تری انتخاب کن؛ ترکیبات دقیق را روی بسته‌بندی ببین.'
    elif 'هیالورونیک' in n:
        base='محصول آبرسان حاوی هیالورونیک اسید طبق نام‌گذاری محصول است؛ این ماده معمولاً برای جذب و نگهداشت رطوبت در روتین استفاده می‌شود. روی پوست کمی نم‌دار و طبق دستور بسته‌بندی بزن و در صورت نیاز با مرطوب‌کننده مناسب کامل کن. میزان غلظت، ترکیبات همراه و سازگاری دقیق این مدل از نام آن مشخص نمی‌شود.'
    elif 'برنج' in n and 'بیواکوا' in n:
        base='محصول مراقبت پوستی با نام/مدل برنج از بیواکواست. برای معرفی دقیق‌تر، کاربرد درج‌شده روی بسته‌بندی (مثل کرم، ماسک یا پک چندمرحله‌ای) را ملاک قرار بده؛ فقط از نام محصول نمی‌توان روشن‌کنندگی، ترکیبات یا نتیجه قطعی را تضمین کرد. روی پوست تمیز و طبق دستور سازنده استفاده کن و ابتدا تست حساسیت انجام بده.'
    elif 'بیواکوا' in n and 'لوسیون' in n:
        base='لوسیون برای مراقبت و نرم‌کردن پوست بدن یا ناحیه درج‌شده روی بسته‌بندی استفاده می‌شود. مقدار مناسبی روی پوست تمیز بزن و تا جذب‌شدن پخش کن. مدل هلو از نام محصول قابل تشخیص است، اما رایحه، ترکیبات و نوع پوست مناسب را از برچسب سازنده بررسی کن.'
    elif 'ماسک ورقه ای دست' in n:
        base='ماسک ورقه‌ای دست برای مراقبت تکمیلی از پوست دست و کمک به احساس نرمی استفاده می‌شود. دست‌ها را تمیز کن، ماسک را طبق زمان درج‌شده روی بسته‌بندی قرار بده و دستور آبکشی یا ماساژ باقی‌مانده را رعایت کن؛ مدت استفاده را بیشتر از توصیه سازنده نکن.'
    elif 'ماسک ورقه ای پا' in n:
        base='ماسک ورقه‌ای پا برای مراقبت تکمیلی از پوست پا استفاده می‌شود. پیش از مصرف دستور بسته‌بندی را بخوان، مدت استفاده را رعایت کن و روی پوست ترک‌خورده یا آسیب‌دیده بدون تأیید سازنده استفاده نکن؛ اگر محصول لایه‌بردار است، پوست را نکن و اجازه بده روند طبق دستور پیش برود.'
    elif 'کرم دست' in n:
        base='کرم دست برای کمک به نرم‌ماندن پوست دست و کاهش احساس خشکی در طول روز است. بعد از شستن دست‌ها و هر زمان احساس خشکی داشتی، مقدار کمی روی دست و اطراف ناخن‌ها ماساژ بده. برای پوست حساس، ترکیبات عطری و هشدارهای روی بسته‌بندی را بررسی کن.'
    elif 'عطر' in n or 'ادکلن' in n or 'بادی اسپلش' in n:
        base='محصول خوشبوکننده برای استفاده شخصی است. رایحه، شدت و ماندگاری به فرمول محصول و نوع پوست وابسته است و از نام به‌تنهایی نمی‌شود آن‌ها را قطعی گفت. اگر پوست حساسی داری، ابتدا روی لباس یا بخش کوچکی از پوست طبق دستور سازنده امتحان کن.'
    elif 'ضد آفتاب' in n or 'ضدآفتاب' in n:
        base='محصول مرتبط با محافظت در برابر آفتاب است. مقدار و دفعات تمدید، طیف محافظت و سازگاری با پوست را طبق برچسب بررسی کن؛ برای محافظت روزانه، به مقدار کافی و تمدید منظم نیاز است. فقط از روی نام محصول نمی‌توان مقاومت در برابر آب یا مناسب‌بودن برای پوست حساس را تأیید کرد.'
    elif 'ابزار آرایشی' in c or any(x in n for x in ('براش','پد کانسیلر','اسفنج','فرمژه')):
        base='ابزار آرایشی برای کمک به زدن، پخش‌کردن یا محوکردن محصول است. ابزار را متناسب با کاربرد استفاده کن و به‌طور منظم تمیز و کاملاً خشک نگه دار تا باقی‌مانده مواد و آلودگی جمع نشود. تعداد و شکل دقیق اقلام را از عکس محصول بررسی کن.'
    elif 'آبرسان' in n or 'مرطوب کننده' in n or 'مرطوب‌کننده' in n:
        base='محصول مرطوب‌کننده/آبرسان برای کمک به حفظ رطوبت و کاهش احساس خشکی در روتین پوستی است. روی پوست تمیز و طبق دستور سازنده استفاده کن. برای پوست چرب، بافت سبک و برای پوست خشک، بافت غنی‌تر ممکن است راحت‌تر باشد؛ سازگاری دقیق این مدل را از ترکیبات و توضیحاتش بررسی کن.'
    elif 'ماسک' in n:
        base='محصول ماسک برای استفاده تکمیلی در روتین مراقبت یا زیبایی است. نوع ماسک، محل استفاده، مدت ماندن روی پوست و نیاز به آبکشی را از دستور همین محصول پیروی کن؛ استفاده بیشتر از زمان توصیه‌شده لزوماً نتیجه بهتری نمی‌دهد.'
    elif any(x in n for x in ('ناخن گیر','ناخن‌گیر')):
        base='ناخن‌گیر برای کوتاه‌کردن ناخن‌هاست؛ با فشار کنترل‌شده و روی ناخن خشک استفاده کن و ابزار را پس از استفاده تمیز نگه دار. برای کودک، فقط با نظارت بزرگسال استفاده شود.'
    elif 'ناخن' in n or 'لاک ناخن' in n or n.startswith('لاک ') or 'لاک ' in n:
        base='محصول مرتبط با آرایش یا مراقبت ناخن است. برای نتیجه مرتب، سطح ناخن را تمیز و خشک کن و لایه‌ها را نازک بزن. زمان خشک‌شدن، نیاز به بیس/تاپ‌کوت و روش پاک‌کردن به نوع محصول بستگی دارد؛ دستور بسته‌بندی را بررسی کن.'
    elif any(x in n for x in ('ریمل','خط چشم','مداد چشم','سرمه چشم','سایه','ابرو','جداکننده مژه')):
        base='محصول آرایش چشم یا ابرو است. با حرکت‌های کوتاه و کنترل‌شده استفاده کن و از به‌اشتراک‌گذاشتن محصول چشم خودداری کن. ضدآب‌بودن، مناسب‌بودن برای چشم حساس و رنگ دقیق را فقط در صورت درج روی مشخصات سازنده قطعی بدان.'
    elif 'دستمال مرطوب' in n:
        base='دستمال مرطوب برای پاک‌کردن آلودگی یا تمیزکردن ناحیه درج‌شده روی بسته‌بندی کاربرد دارد. از تماس با چشم و نواحی حساس خودداری کن مگر اینکه سازنده صریحاً مناسب دانسته باشد؛ پس از بازکردن، بسته را محکم ببند تا خشک نشود.'
    elif 'صابون' in n and any(x in n for x in ('aha','هیدروکسی','تریاک','کلاژن','طلا')):
        base='این صابون برای پاک‌سازی پوست عرضه شده است. از نام یا ادعاهای روی عنوان نمی‌توان اثر روشن‌کنندگی، ضدجوش یا درمانی را تضمین کرد؛ ترکیبات و دستور مصرف را بخوان و ابتدا روی بخش کوچکی از پوست امتحان کن. اگر سوزش یا خشکی ایجاد شد، مصرف را قطع کن.'
    elif 'ست مسافرتی' in n or 'پک مراقبت' in n:
        base='این محصول به شکل ست/پک عرضه شده و برای داشتن چند قلم مراقبتی یا کاربردی در یک مجموعه مناسب است. تعداد و نوع اقلام داخل پک را از عکس و توضیحات همین کالا بررسی کن؛ محتویات را صرفاً از نام پک حدس نزن.'
    elif 'مراقبت پوست' in c:
        base='این محصول برای روتین مراقبت پوست عرضه شده است. کاربرد اصلی و ترتیب مصرف را از نام و دستور بسته‌بندی بررسی کن؛ ترکیبات و نتیجه دقیق را نباید صرفاً از عنوان محصول حدس زد. برای پوست حساس، ابتدا تست روی بخش کوچکی از پوست انجام بده.'
    elif 'بهداشت شخصی' in c:
        base='این کالا در دسته بهداشت شخصی قرار دارد. محل مصرف، دفعات استفاده و هشدارها را مطابق بسته‌بندی رعایت کن؛ روی پوست آسیب‌دیده یا تحریک‌شده استفاده نکن مگر اینکه دستور سازنده اجازه داده باشد.'
    else:
        base=f'این محصول از دسته «{cat}» است. برای اینکه انتخابت دقیق باشد، به کاربرد درج‌شده روی بسته‌بندی و عکس همین مدل توجه کن؛ رنگ، حجم، ترکیبات و ادعاهای تخصصی را بدون مشخصات سازنده قطعی فرض نکن.'
    variants=p.get('variants') or []; attrs=[]
    for v in variants[:8]:
        a=str(v.get('attribute') or v.get('name') or '').strip()
        if a and a not in attrs: attrs.append(a)
    if attrs: base+=' تنوع/بسته‌بندی ثبت‌شده برای این کالا: '+', '.join(attrs)+'.'
    return base

def search_products(query, limit=8):
    """Rank products by exact phrase, product terms, shade/variant, synonyms and typo similarity."""
    from difflib import SequenceMatcher
    q=normalize_fa(query)
    if not q: return []
    stop={'برای','من','چی','چیه','کدام','خوب','مناسب','دارم','می خواهم','می','خواهم','محصول','محصولات','لطفا','یه','یک','از','به','با','را','و','یا','این','اون','است','هست','توی','داخل','فروشگاه','لیانا','بخرم','پیشنهاد','بده','بهترین','دارید','می شود','کنم','کنه','رو','چه','خبر'}
    aliases={
      'کرم پودر':['کرم پودر','کرمپودر','فوندیشن','foundation','بی بی کرم','bb کرم','سی سی کرم','cc کرم','کوشن'],
      'فوندیشن':['کرم پودر','فوندیشن','کوشن'], 'رژ لب':['رژ لب','رژلب','لیپ گلاس','لیپگلاس','بالم لب','تینت لب','رژ مایع','رژ جامد'],
      'لب':['رژ لب','لیپ گلاس','بالم لب','تینت لب','خط لب','رژ مایع'], 'ابرو':['مداد ابرو','ژل ابرو','صابون ابرو'],
      'چشم':['ریمل','خط چشم','سایه','مداد چشم'], 'جوش':['جوش','آکنه','ضد جوش','سرم آکنه'],
      'آبرسان':['آبرسان','مرطوب کننده','هیالورونیک','کرم آبرسان'], 'خشک':['آبرسان','مرطوب کننده','کرم دست'],
      'چرب':['شوینده','فیس واش','ضد آفتاب'], 'ضد آفتاب':['ضد آفتاب','پنکیک spf','spf','ضدافتاب'],
      'ناخن':['ناخن','لاک'], 'پاکسازی':['فیس واش','شوینده','پاک کننده','میسلار','تونر'],
      'رنگ':['رژ لب','رژگونه','لاک','سایه'], 'عطر':['عطر','ادکلن','بادی اسپلش','بادی‌اسپلش','body splash'],
      'خوشبوکننده':['عطر','ادکلن','بادی اسپلش','بادی‌اسپلش','بادی اسپری'], 'بادی اسپلش':['بادی اسپلش','بادی‌اسپلش','بادیاسپلش','body splash','بادی اسپری'],
      'اسپلش':['بادی اسپلش','بادی‌اسپلش'], 'اسپری بدن':['بادی اسپلش','اسپری بدن','بادی اسپری'],
      'تینت':['تینت','تینت لب','تینت گونه'], 'نمک بدن':['نمک بدن','اسکراب بدن'], 'کانسیلر':['کانسیلر','concealer'],
      'پنکیک':['پنکیک','پودر فشرده'], 'مرطوب کننده':['مرطوب کننده','کرم آبرسان','لوسیون'],
      'پاک کننده':['پاک کننده','پاک‌کننده','میسلار','شوینده','فیس واش'], 'شوینده':['شوینده','فیس واش','ژل شستشو','فوم شستشو'],
      'عطر بدن':['بادی اسپلش','عطر','ادکلن'], 'رژگونه':['رژگونه','رژ گونه','بلشر','blush'],
      'هایلایتر':['هایلایتر','highlighter'], 'پرایمر':['پرایمر','primer'], 'فیکساتور':['فیکساتور','اسپری فیکس','پودر فیکس'],
      'مو':['مراقبت مو','ماسک مو','شامپو','سرم مو']
    }
    # Normalize common joined/separated cosmetic words before tokenization.
    q=q.replace('بادیاسپلش','بادی اسپلش').replace('بادی‌اسپلش','بادی اسپلش').replace('لیپگلاس','لیپ گلاس').replace('رژلب','رژ لب').replace('کرمپودر','کرم پودر').replace('ضدافتاب','ضد آفتاب')
    # Expand everyday terms into catalogue vocabulary; this helps requests like «یه خوشبوکننده می‌خوام».
    expanded=set(q.split())
    for key, vals in aliases.items():
        if normalize_fa(key) in q or any(normalize_fa(v) in q for v in vals):
            expanded.update(normalize_fa(v) for v in vals)
    terms=[t for t in expanded if len(t)>1 and t not in stop]
    # Category-oriented requests should surface the matching product family.
    scored=[]
    for item in DATA:
        if not item.get('published',True): continue
        name=normalize_fa(item.get('name','')); cat=normalize_fa(item.get('category',''))
        variants=' '.join(normalize_fa((v.get('attribute') or v.get('name') or '')) for v in item.get('variants',[]))
        hay=name+' '+cat+' '+variants
        score=0
        if q and q in name: score+=18
        # Exact product-family intent should outrank incidental token matches.
        family_aliases={
          'بادی اسپلش':('بادی اسپلش','بادی‌اسپلش','بادیاسپلش','body splash'),
          'تینت':('تینت',), 'کانسیلر':('کانسیلر',), 'پنکیک':('پنکیک',), 'نمک بدن':('نمک بدن',),
          'رژگونه':('رژگونه','رژ گونه'), 'رژ لب':('رژ لب','رژلب','رژ مایع','رژ جامد'),
          'ریمل':('ریمل',), 'عطر':('عطر','ادکلن','بادی اسپلش'), 'اسپری بدن':('بادی اسپلش','اسپری بدن','بادی اسپری')
        }
        for fam, markers in family_aliases.items():
            if normalize_fa(fam) in q and any(normalize_fa(m) in name for m in markers): score+=14
        if 'کانسیلر' in q and 'کانسیلر' in name and not any(x in name for x in ('پد کانسیلر','مثلثی','ابزار')): score+=10
        for t in terms:
            if t in name.split(): score+=8
            elif t in name: score+=5
            elif t in variants: score+=7
            elif t in cat: score+=2
            else:
                # One-character chat typos and misspellings; compare only meaningful tokens.
                best=max([SequenceMatcher(None,t,nt).ratio() for nt in name.split() if len(nt)>=3] or [0])
                if len(t)>=4 and best>=0.82: score+=3
        for t in terms:
            for key, vals in aliases.items():
                if t==key or t in key.split():
                    if any(normalize_fa(v) in name for v in vals): score+=4
        # Shade intent: if the catalogue has the shade as a variant/name, prioritize it.
        shade_terms={'عسلی','نود','صورتی','قرمز','قهوه ای','قهوه‌ای','رز','هلویی','گلبهی','موکا','کاراملی','بژ','روشن','متوسط','تیره'}
        for shade in shade_terms:
            if shade in q and shade in hay: score+=12
        if score: scored.append((score,item))
    scored.sort(key=lambda z:(z[0],normalize_fa(z[1].get('name',''))),reverse=True)
    # For a specific shade search, include products in the matching family if no exact shade variant is listed.
    results=[item for score,item in scored if score>=3]
    family_terms=[('بادی اسپلش',('بادی اسپلش','بادی‌اسپلش','بادیاسپلش','body splash')),('اسپری بدن',('بادی اسپلش','اسپری بدن','بادی اسپری')),('تینت',('تینت',)),('کانسیلر',('کانسیلر',)),('پنکیک',('پنکیک',)),('نمک بدن',('نمک بدن',)),('رژگونه',('رژگونه','رژ گونه')),('رژ لب',('رژ لب','رژلب','رژ مایع','رژ جامد')),('ریمل',('ریمل',)),('کرم پودر',('کرم پودر','کرمپودر','فوندیشن'))]
    for family, markers in family_terms:
        if family in q:
            narrowed=[item for item in results if any(normalize_fa(m) in normalize_fa(item.get('name','')) for m in markers)]
            if narrowed: results=narrowed
            if family=='کانسیلر':
                standalone=[item for item in results if 'کانسیلر' in normalize_fa(item.get('name','')) and not any(x in normalize_fa(item.get('name','')) for x in ('پد کانسیلر','مثلثی','ابزار'))]
                if standalone: results=standalone
            break
    results=results[:limit]
    if not results and terms:
        # Last-resort family search, not a random catalogue dump.
        family=[t for t in terms if t in aliases]
        if family:
            for item in DATA:
                if not item.get('published',True): continue
                name=normalize_fa(item.get('name',''))
                if any(normalize_fa(v) in name for f in family for v in aliases[f]): results.append(item)
                if len(results)>=limit: break
    return results


def related_products(p, limit=3):
    """Return related items from the same use/category without duplicating the original."""
    pid=str(p.get('id'))
    cat=normalize_fa(p.get('category',''))
    name=normalize_fa(p.get('name',''))
    family=[]
    for word in ('تینت','رژ لب','رژلب','لیپ گلاس','کانسیلر','پنکیک','کرم پودر','آبرسان','نمک بدن','ماسک','بیواکوا'):
        if word in name: family.append(word)
    ranked=[]
    for other in DATA:
        if not other.get('published',True) or str(other.get('id'))==pid: continue
        oname=normalize_fa(other.get('name','')); ocat=normalize_fa(other.get('category',''))
        score=0
        if cat and ocat==cat: score+=2
        for f in family:
            if f in oname: score+=7
        if score: ranked.append((score,other))
    ranked.sort(key=lambda x:(-x[0],normalize_fa(x[1].get('name',''))))
    return [x[1] for x in ranked[:limit]]

def conversational_keyboard(user_text:str, history:list, answer:str):
    """Add tappable product cards and store-information actions to AI replies."""
    q=normalize_fa(contextual_query(user_text,history or []))
    a=normalize_fa(answer)
    rows=[]; products=[]; seen=set()
    # Prefer product names explicitly mentioned in the answer, then fill from ranked search.
    for p in DATA:
        if not p.get('published',True): continue
        name=normalize_fa(p.get('name',''))
        if len(name)>=5 and name in a and str(p.get('id')) not in seen:
            products.append(p); seen.add(str(p.get('id')))
            if len(products)>=5: break
    product_intent=any(x in q for x in ('تینت','کانسیلر','پنکیک','رژ لب','رژلب','کرم پودر','کرمپودر','رژگونه','ریمل','نمک بدن','آبرسان','مرطوب کننده','محصول','پیشنهاد','بهترین','دارید','قیمت','موجودی'))
    if product_intent and len(products)<5:
        for p in search_products(q,8):
            pid=str(p.get('id'))
            if pid not in seen:
                products.append(p); seen.add(pid)
            if len(products)>=5: break
    for p in products[:5]:
        rows.append([btn('🧴 '+str(p.get('name','محصول'))[:48],f"product:{p['id']}")])
    store_intent=any(x in q for x in ('فروشگاه','لیانا','آدرس','شماره تماس','شماره فروشگاه','ساعت کاری','شعبه','ارسال','سفارش','اطلاعات فروشگاه','کجاست','کجا هستید'))
    if store_intent:
        rows.append([btn('🏬 اطلاعات کامل فروشگاه','shop'),btn('🛒 سایت فروشگاه',url=SHOP_URL)])
    if rows:
        rows.append([btn('↩️ ادامه گفت‌وگو','ai:continue'),btn('🏠 خانه','home')])
        return InlineKeyboardMarkup(inline_keyboard=rows)
    return ai_kb()

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

def recommendation_answer(user_text:str):
    """Understand shopping intents and make the next best move before generic FAQ retrieval."""
    import random
    q=normalize_fa(user_text)
    # Social/affection phrases should get natural, varied replies.
    if any(x in q for x in ('دوستت دارم','عاشقتم','عزیزمی','عشقم')):
        return random.choice(('چه مهربونی تو 💗 منم خوشحالم که اینجام تا انتخاب‌هات راحت‌تر بشه! حالا بگو امروز دنبال چه خوشگلی‌ای هستی؟','قربون محبتت 🌷 بیا با هم یه محصول خوشگل برای سلیقه‌ات پیدا کنیم!','این همه انرژی خوب؟ 😄💞 بگو ببینم چی لازم داری تا راهنماییت کنم.'))
    if any(x in q for x in ('بهترین تینت','تینت خوب','تینت عسلی','تینت لب','تینت میخوام','تینت می خواهم')):
        items=search_products('تینت '+('عسلی' if 'عسلی' in q else ''),6)
        if items:
            intro='تینت برای رنگ‌دهی سبک و طبیعی به لب کاربرد دارد و با لایه‌گذاری می‌توان شدت رنگ را بیشتر کرد. 🌷 '
            if 'عسلی' in q: intro+='برای طیف عسلی، رنگ واقعی باید از عکس/کد رنگ محصول تأیید شود؛ در فهرست محصولات، این گزینه‌های تینت را پیدا کردم: '
            else: intro+='اگر جلوه طبیعی و راحت برای روزمره می‌خواهی، این مدل‌های تینت لیانا گزینه‌های خوبی برای بررسی‌اند: '
            lines='\n'.join('• '+p.get('name','') for p in items[:5])
            return intro+'\n'+lines+'\n\nاگر بگی رنگ پوستت روشنه، متوسطه یا تیره و رنگ گرم دوست داری یا سرد، دقیق‌تر محدودش می‌کنم.'
        return 'تینت معمولاً رنگی سبک و طبیعی به لب می‌دهد و با چند لایه پررنگ‌تر می‌شود. برای اینکه بهترین گزینه موجود را پیدا کنم، بگو رنگ پوستت روشن، متوسط یا تیره است و رنگ‌های گرم مثل هلویی/عسلی دوست داری یا صورتی/رز؟'
    if any(x in q for x in ('کدام کانسیلر','کدوم کانسیلر','کانسیلر برای پوستم','کانسیلر مناسب پوست','چه کانسیلری','کانسیلر خوب')):
        items=search_products('کانسیلر',6)
        tone=('گندمی' if 'گندمی' in q else ('سبزه' if 'سبزه' in q else ('روشن' if 'روشن' in q else ('متوسط' if 'متوسط' in q else ('تیره' if 'تیره' in q else '')))))
        if any(x in q for x in ('پوست چرب','پوستم چرب','پوست خشک','پوستم خشک','پوست مختلط','پوست حساس')):
            skin='چرب' if 'چرب' in q else ('خشک' if 'خشک' in q else ('مختلط' if 'مختلط' in q else 'حساس'))
            base=f'برای پوست {skin}، مهم است کانسیلر روی پوستت راحت بنشیند و بافتش با نیازت هماهنگ باشد؛ اما نوع پوست به‌تنهایی برای تعیین کد رنگ کافی نیست. '
        elif tone:
            base=f'مرسی که گفتی 💗 برای پوست {tone}، کد کانسیلر را باید با رنگ زیر چشم و تناژت هماهنگ کرد؛ فقط از روی «{tone}» نمی‌شود کد دقیق را قطعی انتخاب کرد. حالا بگو پوستت چربه، خشکه، مختلطه یا حساس؟ '
        else:
            base='حتماً 💗 برای اینکه کانسیلر هم از نظر بافت و هم رنگ بهت بیاد، اول بگو پوستت چربه، خشکه، مختلطه یا حساس؟ و رنگ پوستت روشنه، متوسطه یا تیره؟ '
        if items:
            base+='این کانسیلرها را در لیانا پیدا کردم: '+'، '.join(p.get('name','') for p in items[:4])+'.'
        base+=' برای پیشنهاد کد رنگ دقیق، تناژت (گرم/طلایی، سرد/صورتی یا خنثی) و اینکه برای تیرگی زیر چشم می‌خواهی یا پوشاندن لک را هم بگو.'
        return base
    if any(x in q for x in ('بهترین محصول','بهترین چیز','بهترین کالا','بهترین از فروشگاه','بهترین محصول فروشگاه','چی از فروشگاه خوبه')):
        return 'با کمال میل 🌸 منظورت بهترین محصول برای کدوم نیازه؟ تینت و رژ لب، آرایش صورت، مراقبت پوست، مو یا بدن؟ اگر بگی دنبال چه نتیجه‌ای هستی و حدود بودجه‌ات چقدره، از محصولات لیانا چند گزینه مرتبط معرفی می‌کنم.'
    # Skin concern: advice plus a clarifying question and related catalog products.
    if any(x in q for x in ('صورتم جوش میزنه','پوستم جوش میزنه','جوش میزنم','جوش می زند','جوش دارم','آکنه دارم')):
        items=search_products('جوش آکنه شوینده',5)
        base='اگر صورتت جوش می‌زند، معمولاً شروع ساده بهتر است: شوینده ملایم، مرطوب‌کننده سبک و ضدآفتاب غیرکومدون‌زا؛ اسکراب خشن و دستکاری جوش‌ها را کمتر کن. محصولات جدید را یکی‌یکی اضافه کن. اگر جوش‌ها دردناک، شدید یا ماندگارند، متخصص پوست مناسب‌تر است. '
        if items: base+='از محصولات لیانا این گزینه‌ها به موضوع نزدیک‌اند: '+'، '.join(p.get('name','') for p in items[:4])+'. '
        base+='پوستت بیشتر چربه یا خشکه/مختلط؟ جوش‌ها بیشتر زیرپوستی‌اند یا قرمز و ملتهب؟ با این دو مورد دقیق‌تر راهنمایی‌ات می‌کنم.'
        return base
    # Product family requests: explain what it is, then list matching store items and useful complements.
    families=[('نمک بدن','نمک بدن'),('پنکیک','پنکیک'),('رژ لب','رژ لب'),('رژلب','رژ لب'),('تینت','تینت'),('کانسیلر','کانسیلر'),('کرم پودر','کرم پودر'),('کرمپودر','کرم پودر'),('آبرسان','آبرسان'),('مرطوب کننده','مرطوب کننده'),('ریمل','ریمل'),('رژگونه','رژگونه')]
    chosen=next(((word,key) for word,key in families if word in q),None)
    if chosen and (any(x in q for x in ('بهترین','پیشنهاد','معرفی','چی خوبه','می خواهم','میخوام','دارید','پیدا کن','پیدا کنه','خوبه','مناسب')) or any(x in q for x in ('گندمی','روشن','تیره','سبزه','تناژ','رنگ پوستم','بهم میاد','به من میاد'))):
        word,key=chosen
        items=search_products(key,6)
        if items:
            if key=='نمک بدن': intro='نمک بدن برای روتین مراقبت بدن و لایه‌برداری سطحی استفاده می‌شود؛ پوست حساس یا تحریک‌شده را با آن نساب و دستور بسته‌بندی را رعایت کن. گزینه‌های لیانا: '
            elif key=='تینت': intro='تینت برای رنگ‌دهی سبک و طبیعی به لب کاربرد دارد؛ شدت رنگ را می‌توان معمولاً با لایه‌گذاری بیشتر کرد. گزینه‌های لیانا: '
            elif key=='کانسیلر': intro='کانسیلر برای پوشش موضعی تیرگی زیر چشم یا لک‌هاست؛ انتخاب بافت و رنگ به نوع پوست و تناژ بستگی دارد. گزینه‌های لیانا: '
            elif key=='پنکیک': intro='پنکیک برای مرتب‌کردن آرایش پایه و یکدست‌تر نشان‌دادن پوست کاربرد دارد؛ کد رنگ باید با پوست و گردن هماهنگ باشد. گزینه‌های لیانا: '
            elif key=='آبرسان' or key=='مرطوب کننده': intro='آبرسان/مرطوب‌کننده به حفظ رطوبت پوست کمک می‌کند؛ بافت و ترکیبات را متناسب با پوستت انتخاب کن. گزینه‌های لیانا: '
            elif key=='رژ لب': intro='رژلب مناسب می‌تواند رنگ صورت را شاداب‌تر نشان دهد؛ برای پوست گندمی، طیف رز گرم، هلویی، آجری و مرجانی اغلب انتخاب‌های خوبی برای امتحان‌اند. رنگ نهایی به تناژ پوست و مدل بستگی دارد. گزینه‌های لیانا: '
            else: intro=f'{key} برای تکمیل آرایش/روتین زیبایی استفاده می‌شود؛ نتیجه نهایی به مدل و ویژگی‌های درج‌شده روی محصول بستگی دارد. گزینه‌های لیانا: '
            out='\n'.join('• '+p.get('name','') for p in items[:5])
            related=[]
            for p in items[:2]:
                related.extend(related_products(p,2))
            seen=set(x.get('id') for x in items)
            rel=[]
            for p in related:
                if p.get('id') not in seen and p.get('id') not in {x.get('id') for x in rel}: rel.append(p)
            suffix=''
            if rel: suffix+='\n\n✨ برای ست‌کردن یا کامل‌کردن انتخابت، این‌ها هم مرتبط‌اند: '+'، '.join(p.get('name','') for p in rel[:3])+'.'
            if key in ('کانسیلر','کرم پودر','پنکیک','تینت','رژ لب','رژگونه'):
                tone=('گندمی' if 'گندمی' in q else ('روشن' if 'روشن' in q else ('تیره' if 'تیره' in q or 'سبزه' in q else '')))
                if key=='رژ لب' and tone:
                    suffix+=f'\n\nبرای پوست {tone}، طیف‌های رز/هلویی یا رنگ‌های عمیق‌تر می‌توانند انتخاب‌های خوبی باشند؛ تناژ گرم یا سرد و سلیقه‌ات تعیین‌کننده است.'
                elif key=='پنکیک' and tone:
                    suffix+=f'\n\nبرای پوست {tone}، رنگ پنکیک را در نور طبیعی کنار خط فک با گردن مقایسه کن؛ کد دقیق را بدون دیدن رنگ‌بندی نمی‌شود قطعی گفت.'
                elif key=='تینت' and tone:
                    suffix+=f'\n\nبرای پوست {tone}، رنگ‌های ملایم رز/هلویی یا طیف‌های عمیق‌تر می‌توانند جذاب باشند؛ رنگ واقعی مدل را از تصویر بررسی کن.'
                else:
                    suffix+='\n\nاگر رنگ پوستت را (روشن/متوسط/تیره) و تناژت را (گرم/سرد/خنثی) بگویی، پیشنهاد را دقیق‌تر می‌کنم.'
            return intro+'\n'+out+suffix
    return None

def offline_answer(user_text:str, history=None)->str:
    import random
    q=normalize_fa(user_text)
    talk=is_small_talk(q)
    if talk=='bye': return random.choice(('فعلاً، ممنون که به لیانا سر زدی 🌷 هر وقت سؤال محصول داشتی برگرد.','خداحافظ 💕 روزت قشنگ! هر وقت درباره محصولات سؤال داشتی من اینجام.','به امید دیدار 🌸'))
    if talk=='how': return random.choice(('ممنون که پرسیدی 🌷 آماده‌ام برای پیدا کردن محصول کمکت کنم؛ تو چطوری؟','مرسی از لطفت 💗 بگو دنبال چه محصولی هستی؟','خوبم و آماده‌ام کمک کنم 🌸'))
    if talk=='greet' and len(q.split())<=6: return random.choice(('سلام عزیزم 🌷 خوش اومدی به لیانا شاپ! چه محصولی رو برات پیدا کنم؟','سلام سلام 💕 درباره محصولات آرایشی یا مراقبت پوست چی می‌خوای بدونی؟','درود 🌸 می‌تونم هم سؤال‌های آرایشی رو جواب بدم هم محصولات لیانا رو پیدا کنم.'))
    if talk=='thanks': return random.choice(('خواهش می‌کنم 💗 سؤال دیگه‌ای درباره محصولات داشتی بپرس.','قربونت 🌷 خوشحالم کمک کردم.','خواهش می‌کنم! 🌸'))
    if talk=='positive': return random.choice(('خوشحالم که به کارت اومد 🌷 هر سؤال دیگه‌ای درباره محصولات یا آرایش داشتی بپرس.','قربونت 💗 برای انتخاب محصول یا پیدا کردن قیمت و موجودی هم کمکت می‌کنم.'))
    if talk=='casual': return random.choice(('من اینجام که درباره محصولات و خرید از لیانا کمکت کنم 😊 تو بگو دنبال چه چیزی هستی؟','فعلاً کارم راهنمایی درباره آرایش و محصولات لیاناست 🌷 چیزی می‌خوای برات پیدا کنم؟'))
    if any(x in q for x in ('چی پیشنهاد میدی','چه پیشنهاد میدی','چی پیشنهاد می دهی','چه چیزی پیشنهاد','پیشنهادت چیه','من چی بخرم','چی خوبه برام')):
        return 'با کمال میل 🌷 دوست داری از بین محصولات لیانا چی پیشنهاد بدم؟ مثلاً آرایش لب، آرایش صورت، عطر و بادی‌اسپلش یا مراقبت پوست؟ اگر برای پوستته، نوع پوستت رو هم بگو تا انتخاب دقیق‌تری داشته باشیم.'
    if any(x in q for x in ('دوستت دارم','عاشقتم')):
        return random.choice(('چه مهربون گفتی 💗 منم خوشحالم که می‌تونم کمکت کنم!','قربون محبتت 🌷 بگو امروز دنبال چه چیزی هستی؟','چه انرژی قشنگی 💕 هر وقت خواستی برای انتخاب محصول کمکت می‌کنم.'))
    if any(x in q for x in ('معرفی فروشگاه','اطلاعات فروشگاه','اطلاعات لیانا','معرفی کن','فروشگاه رو معرفی','درباره فروشگاه','آدرس فروشگاه','کجاست فروشگاه','کجا هستید','شماره فروشگاه','شماره تماس','شعبه های فروشگاه','شعبه های لیانا')):
        return ('🌸 فروشگاه آرایشی و بهداشتی لیانا در بندر گناوه دو شعبه دارد:\n\n📍 شعبه اول: پارکینگ ملت، راسته اصلی، بعد از داروخانه شبانه‌روزی دکتر معرفت، آخرین مغازه سمت راست.\n📍 شعبه دوم: پارکینگ ملت، پاساژ جام جم، پلاک ۱۰.\n\n🛍 سایت فروشگاه: '+SHOP_URL+'\n☎️ برای قیمت و موجودی روز می‌تونی با فروشگاه تماس بگیری: '+PHONE_1+' یا '+PHONE_2)
    if any(x in q for x in ('ساعت کاری','چه ساعتی باز','کی باز','باز هستید','تا چه ساعتی')):
        return '🌷 ساعت کاری دقیق در اطلاعات فعلی من ثبت نشده؛ برای اینکه اشتباه راهنمایی‌ات نکنم، لطفاً با فروشگاه تماس بگیر: '+PHONE_1+' یا '+PHONE_2
    if talk=='positive': return random.choice(('خوشحالم که به کارت اومد 🌷 هر سؤال دیگه‌ای درباره محصولات یا آرایش داشتی بپرس.','قربونت 💗 برای انتخاب محصول یا پیدا کردن قیمت و موجودی هم کمکت می‌کنم.'))
    if talk=='casual': return random.choice(('من اینجام که درباره محصولات و خرید از لیانا کمکت کنم 😊 تو بگو دنبال چه چیزی هستی؟','فعلاً کارم راهنمایی درباره آرایش و محصولات لیاناست 🌷 چیزی می‌خوای برات پیدا کنم؟'))
    if any(x in q for x in ('معرفی فروشگاه','معرفی کن','فروشگاه رو معرفی','درباره فروشگاه','آدرس فروشگاه','کجاست فروشگاه','کجا هستید','شماره فروشگاه','شماره تماس','شعبه های فروشگاه','شعبه های لیانا')):
        return ('🌸 فروشگاه آرایشی و بهداشتی لیانا در بندر گناوه دو شعبه دارد:\n\n📍 شعبه اول: پارکینگ ملت، راسته اصلی، بعد از داروخانه شبانه‌روزی دکتر معرفت، آخرین مغازه سمت راست.\n📍 شعبه دوم: پارکینگ ملت، پاساژ جام جم، پلاک ۱۰.\n\n🛍 سایت فروشگاه: '+SHOP_URL+'\n☎️ برای قیمت و موجودی روز می‌تونی با فروشگاه تماس بگیری: '+PHONE_1+' یا '+PHONE_2)
    if any(x in q for x in ('ساعت کاری','چه ساعتی باز','کی باز','باز هستید','تا چه ساعتی')):
        return '🌷 ساعت کاری دقیق در اطلاعات فعلی من ثبت نشده؛ برای اینکه اشتباه راهنمایی‌ات نکنم، لطفاً با فروشگاه تماس بگیر: '+PHONE_1+' یا '+PHONE_2
    smart=recommendation_answer(user_text)
    if smart: return smart
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

async def call_groq(user_text:str, history=None):
    """Use verified local product records first, then optional AI with relevant product context."""
    history=history or []
    interpreted_text=contextual_query(user_text,history)
    q=normalize_fa(interpreted_text)
    # Price/stock/existence questions must be grounded in the store's current catalog data.
    if any(x in q for x in ('قیمت','چنده','چقدر','موجود','دارید','دارین','وجود داره','هست یا نه','پیدا میشه')):
        local=product_status_answer(interpreted_text)
        if local:
            return local
    if not GROQ_API_KEY:
        return offline_answer(interpreted_text,history=history)
    products=product_context(interpreted_text, limit=10)
    kb_answer=knowledge_answer(interpreted_text) or ''
    system_prompt=(
      'تو مشاور فروش خوش‌برخورد و آگاه فروشگاه «لیانا شاپ» هستی و فقط به فارسی طبیعی پاسخ می‌دهی. '
      'به مشتری هرگز درباره فایل، JSON، دیتابیس، کد، منبع داخلی یا روش فنی دریافت اطلاعات حرف نزن. '
      'از اطلاعات محصولی که در زمینه گفتگو آمده استفاده کن؛ نام، رنگ، ترکیبات، حجم، SPF، ماندگاری، ادعای درمانی یا سازگاری را اگر منبعی تأیید نکرده، نساز. '
      'اگر رنگ‌ها/ویژگی‌ها در داده محصول ثبت نشده‌اند، شفاف بگو اطلاعات دقیق آن مورد را نداریم و لینک محصول/فروشگاه را پیشنهاد بده. '
      'برای پیشنهاد محصول، ابتدا نیاز و نوع پوست/تناژ/بودجه مشتری را در نظر بگیر؛ اگر کافی نیست یک سؤال کوتاه بپرس. '
      'قیمت و موجودی ممکن است تغییر کند؛ فقط اعداد و وضعیت موجود در داده مرتبط را گزارش کن و برای تأیید نهایی ارجاع به فروشگاه بده. '
      'از ادعاهای پزشکی قطعی پرهیز کن، در حساسیت شدید یا مشکلات پوستی ماندگار مراجعه به متخصص را پیشنهاد بده. '
      'کوتاه، کاربردی، گرم و حرفه‌ای بنویس؛ هر بار جمله‌بندی را متنوع کن و پاسخ کلیشه‌ای تکراری نده. پیام‌های اجتماعی مثل دوستت دارم، اوکی، چه خبر و تشکر را طبیعی و متناسب پاسخ بده. اگر کاربر بهترین تینت/محصول را خواست، از محصولات مرتبط موجود چند گزینه نام ببر و ویژگی عمومی همان دسته را توضیح بده. برای رنگ کانسیلر/پنکیک/رژلب، اگر نوع پوست، رنگ و تناژ کافی نیست یک سؤال کوتاه بپرس. برای درخواست محصول مرتبط، مکمل‌های مناسب را از فهرست محصولات پیدا کن. پاسخ را به شکل فروشنده واقعی ارائه کن و درباره روش فنی دریافت اطلاعات توضیح نده.'
    )
    context='اطلاعات مرتبط محصولات فروشگاه (فقط برای راهنمایی دقیق؛ این داده‌ها را به شکل فایل یا منبع فنی به مشتری معرفی نکن):\n'+json.dumps(products,ensure_ascii=False)
    if kb_answer:
        context+='\nدانش عمومی مرتبط: '+kb_answer
    messages=[{'role':'system','content':system_prompt}]
    # Conversation turns are included so the model can understand «همون»، «این رنگ»،
    # and short answers to its own clarifying questions.
    messages.extend(history[-10:])
    messages.append({'role':'user','content':context+'\n\nپیام فعلی مشتری: '+user_text+'\nبرداشت از پیام با توجه به مکالمه: '+interpreted_text})
    payload={'model':GROQ_MODEL,'messages':messages,'temperature':0.55,'max_tokens':700}
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
    return offline_answer(interpreted_text,history=history)

@dp.callback_query(F.data=='ai:open')
async def cb_ai_open(c:CallbackQuery):
    uid=c.from_user.id
    AI_USERS.add(uid); OWNER_PASSWORD_PENDING.discard(uid); history_clear(uid)
    await c.message.answer('🤖 <b>دستیار محصولات لیانا شاپ</b>\n\nبه پرسش‌های رایج آرایش و مراقبت پوست و مو پاسخ می‌دهم، سؤال‌های محاوره‌ای را می‌فهمم و در محصولات لیانا جست‌وجو می‌کنم؛ اگر اطلاعات دقیق رنگ یا ترکیبات محصول مشخص نباشد، حدس نمی‌زنم و راه بررسی را پیشنهاد می‌دهم.\n\nمی‌توانی درباره مراقبت پوست، اصول آرایش، ایمنی محصولات، نام محصول، قیمت یا موجودی محصولات بپرسی؛ سؤال‌های نامرتبط پاسخ داده نمی‌شوند.\n\nپیامت را بفرست؛ برای خروج دکمه زیر را بزن.',reply_markup=ai_kb())
    await c.answer()

@dp.callback_query(F.data=='ai:continue')
async def cb_ai_continue(c:CallbackQuery):
    AI_USERS.add(c.from_user.id)
    await c.answer('ادامه بده؛ پیام‌های قبلی را در نظر می‌گیرم.')

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
    history_clear(uid)
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
        history=history_get(uid)
        interpreted=contextual_query(q,history)
        # Let greetings, casual conversation, store questions and recommendation requests reach intent handling.
        answer=await call_groq(q,history=history)
        history_add(uid,'user',q)
        history_add(uid,'assistant',answer)
        # Use the pre-turn history when deriving product buttons so a short answer
        # such as «گندمی» stays linked to the original tint/concealer question.
        await m.answer(esc(answer),reply_markup=conversational_keyboard(q,history,answer))
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
