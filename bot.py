import asyncio, csv, io, json, os, re, sqlite3, time, hashlib
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramServerError
import aiohttp

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN", "").strip()
PHONE_1=os.getenv("PHONE_1", "09369952103").strip()
PHONE_2=os.getenv("PHONE_2", "09904358418").strip()
SHOP_URL=os.getenv("SHOP_URL", "https://liyanashop.ir").strip()
TG_URL=os.getenv("TELEGRAM_URL", "https://t.me/liyanashop_ir").strip()
RUBIKA_URL=os.getenv("RUBIKA_URL", "https://rubika.ir/liyanashop_gnv").strip()
GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", "").strip()
GEMINI_MODEL=os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
PRODUCTS_CSV_URL=os.getenv("PRODUCTS_CSV_URL", "").strip()
CSV_REFRESH_SECONDS=max(60, int(os.getenv("CSV_REFRESH_SECONDS", "600")))
OWNER_TELEGRAM_ID=os.getenv("OWNER_TELEGRAM_ID", "").strip()
OWNER_PASSWORD=os.getenv("OWNER_PASSWORD", "").strip()
AI_WEEKLY_LIMIT=3
DB_PATH=Path(__file__).with_name("bot_data.sqlite3")
AI_USERS=set()                 # users currently chatting with AI
OWNER_PASSWORD_PENDING=set()   # owner IDs currently entering password
OWNER_AI_USERS=set()           # owner IDs authenticated for AI testing
OWNER_STATES={}                # owner-only multi-step management workflow
OWNER_OVERRIDES_PATH=Path(__file__).with_name('owner_overrides.json')
OWNER_LEARNING_PATH=Path(__file__).with_name('owner_learning.json')
OWNER_CATALOG_PATH=Path(__file__).with_name('owner_catalog.json')
DATA=json.loads(Path(__file__).with_name("products.json").read_text(encoding="utf-8"))

# محصولات فایل CSV ووکامرس به کاتالوگ اصلی اضافه/به‌روزرسانی می‌شوند.
# منبع CSV می‌تواند فایل research_queue.csv کنار bot.py یا آدرس فید در PRODUCTS_CSV_URL باشد.
def _csv_value(row, *names):
    for name in names:
        val = row.get(name)
        if val is not None and str(val).strip(): return str(val).strip()
    return ""

def _truthy(value):
    return str(value or "").strip().lower() in {"1", "yes", "true", "publish", "published", "بله", "موجود"}

def _guess_category(name, raw_category=""):
    text=normalize_fa((name or "")+" "+(raw_category or ""))
    rules=[
      ("ضدآفتاب", ["ضد افتاب","ضدآفتاب","spf"]),
      ("مراقبت پوست", ["کرم","سرم","مرطوب کننده","آبرسان","ماسک صورت","تونر","اسکراب","زانو","آرنج","دور چشم","پاکسازی"]),
      ("پاک‌کننده و شوینده", ["شوینده","فوم شستشو","ژل شستشو","میسلار","پاک کننده","صابون"]),
      ("آرایش لب", ["رژ لب","رژلب","بالم لب","برق لب","تینت لب","خط لب","لیپ گلاس","lip gloss"]),
      ("آرایش چشم و ابرو", ["ریمل","خط چشم","سایه چشم","مداد چشم","ابرو","مژه"]),
      ("آرایش صورت", ["کرم پودر","پنکک","کانسیلر","رژگونه","هایلایتر","پرایمر","کانتور","پودر فیکس"]),
      ("عطر و خوشبوکننده", ["عطر","ادکلن","بادی اسپلش","اسپری خوشبو","پرفیوم"]),
      ("بهداشت شخصی", ["دست","ناخن","شامپو","مام رول","دئودورانت","بهداشت","مسواک"]),
    ]
    for cat, words in rules:
        if any(normalize_fa(w) in text for w in words): return cat
    # از دسته‌بندی فروشگاه استفاده کن، اما نام‌های کلی عمده را به مشتری نشان نده.
    raw=(raw_category.split(",")[0].split(">")[-1].strip() if raw_category else "")
    if raw and normalize_fa(raw) not in {"عمده","آرایشی عمده","محصولات","همه محصولات"}: return raw
    return "سایر محصولات"

def _parse_woo_csv(raw_text):
    reader=csv.DictReader(io.StringIO(raw_text.lstrip("\ufeff")))
    rows=list(reader)
    if not reader.fieldnames or not any("نام" in str(x) for x in reader.fieldnames):
        raise ValueError("ستون نام در CSV پیدا نشد؛ لینک باید خروجی CSV محصولات ووکامرس باشد.")
    base=[]; by_id={}; variations={}
    for row in rows:
        pid=_csv_value(row,"شناسه")
        name=_csv_value(row,"نام")
        typ=_csv_value(row,"نوع").lower()
        parent=_csv_value(row,"مادر")
        if typ=="variation" or (parent and not name):
            if parent:
                variations.setdefault(parent,[]).append(row)
            continue
        if not name or not pid: continue
        if not _truthy(_csv_value(row,"منتشر شده")): continue
        rawcat=_csv_value(row,"دسته‌ها","دسته ها")
        stockraw=_csv_value(row,"در انبار؟")
        stock="موجود" if _truthy(stockraw) else ("ناموجود" if stockraw else "نامشخص")
        regular=_csv_value(row,"قیمت عادی")
        sale=_csv_value(row,"قیمت فروش ویژه")
        price=sale or regular
        img=_csv_value(row,"تصاویر").split(",")[0].strip()
        if img.startswith("http://"): img="https://"+img[7:]
        desc=_csv_value(row,"توضیحات","توضیح کوتاه")
        url=SHOP_URL.rstrip("/")+"/?s="+quote(name)+"&post_type=product"
        variants=[]
        attr_name=_csv_value(row,"نام 1 صفت","نام 2 صفت","نام 3 صفت")
        attr_val=_csv_value(row,"مقدار(های) 1 صفت","مقدار(های) 2 صفت","مقدار(های) 3 صفت")
        if attr_val or price:
            variants.append({"id":pid,"name":name+(" - "+attr_val if attr_val else ""),"price_raw":price,"stock_file":stock,"attribute":attr_val or attr_name or ""})
        prod={"id":pid,"sku":_csv_value(row,"شناسه محصول"),"name":name,"category":_guess_category(name,rawcat),"description":desc,"image":img,"published":True,"stock_file":stock,"variants":variants,"product_url":url,"csv_imported":True}
        base.append(prod); by_id[pid]=prod
    for parent, vrows in variations.items():
        if parent not in by_id: continue
        prod=by_id[parent]
        for row in vrows:
            vid=_csv_value(row,"شناسه")
            price=_csv_value(row,"قیمت فروش ویژه") or _csv_value(row,"قیمت عادی")
            attr=[]
            for i in range(1,11):
                val=_csv_value(row,f"مقدار(های) {i} صفت")
                if val: attr.append(val)
            stock="موجود" if _truthy(_csv_value(row,"در انبار؟")) else "ناموجود"
            prod.setdefault("variants",[]).append({"id":vid,"name":prod["name"]+(" - "+"، ".join(attr) if attr else ""),"price_raw":price,"stock_file":stock,"attribute":"، ".join(attr)})
    return base

def _merge_csv_products(csv_products):
    # داده‌های معرفی‌شده در products.json حفظ می‌شوند؛ اطلاعات تازه CSV (قیمت/موجودی/تصویر/تنوع) به‌روز می‌شود.
    existing={str(p.get("id")):p for p in DATA if isinstance(p,dict) and p.get("id") is not None}
    merged=list(DATA)
    for fresh in csv_products:
        pid=str(fresh["id"]); old=existing.get(pid)
        if old:
            old["name"]=fresh.get("name") or old.get("name")
            old["category"]=fresh.get("category") or old.get("category")
            old["image"]=fresh.get("image") or old.get("image")
            old["stock_file"]=fresh.get("stock_file") or old.get("stock_file")
            old["product_url"]=fresh.get("product_url") or old.get("product_url")
            if fresh.get("variants"): old["variants"]=fresh["variants"]
            if fresh.get("description") and not old.get("description"): old["description"]=fresh["description"]
        else:
            merged.append(fresh)
    DATA[:] = merged
    apply_owner_catalog()
    apply_owner_overrides()
    return len(csv_products)

def load_local_csv():
    path=Path(__file__).with_name("research_queue.csv")
    if not path.exists(): return 0
    try:
        return _merge_csv_products(_parse_woo_csv(path.read_text(encoding="utf-8-sig")))
    except Exception as e:
        print(f"[CSV] خطا در خواندن فایل محلی: {e}"); return 0

async def refresh_csv_feed():
    if not PRODUCTS_CSV_URL: return
    try:
        timeout=aiohttp.ClientTimeout(total=25)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(PRODUCTS_CSV_URL) as resp:
                resp.raise_for_status()
                raw=await resp.text(encoding=None)
        count=_merge_csv_products(_parse_woo_csv(raw))
        print(f"[CSV] فید به‌روزرسانی شد؛ {count} محصول از CSV خوانده شد. کل کاتالوگ: {len(DATA)}")
    except Exception as e:
        print(f"[CSV] فید در دسترس نیست؛ کاتالوگ فعلی حفظ شد: {e}")

async def csv_refresh_loop():
    # اگر فایل CSV روی هاست جایگزین شود، در چرخه بعدی دوباره از دیسک خوانده می‌شود.
    # فید URL هم در صورت تنظیم بودن، در همان چرخه بررسی می‌شود.
    while True:
        await asyncio.sleep(CSV_REFRESH_SECONDS)
        local_count=load_local_csv()
        if local_count:
            print(f"[CSV] فایل محلی دوباره خوانده شد؛ {local_count} محصول/ردیف، کل کاتالوگ: {len(DATA)}")
        await refresh_csv_feed()
PAGE_SIZE=8
SEARCH_PAGE_QUERIES={}  # شناسه کوتاه برای نگهداری عبارت جست‌وجو بین صفحات
AI_PAGE_PRODUCTS={}  # فهرست پیشنهادهای هوش مصنوعی برای صفحه‌بندی
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
def cats(): return sorted(set(str(p.get('category') or 'سایر محصولات') for p in DATA if p.get('published',True)))
def categories_kb():
    rows=[]; c=cats()
    for i in range(0,len(c),2): rows.append([btn("✨ "+c[i],f"cat:{i}:0")]+([btn("✨ "+c[i+1],f"cat:{i+1}:0")] if i+1<len(c) else []))
    rows.append([btn("📚 همه محصولات", "catalog:0")]); rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)
def product_keyboard(p):
    rows=[[btn("💰 استعلام قیمت",f"ask:price:{p['id']}"),btn("📦 استعلام موجودی",f"ask:stock:{p['id']}")]]
    url=str(p.get('product_url') or '').strip()
    if url.startswith(('https://','http://')):
        rows.append([btn("🔗 صفحه محصول",url=url)])
    rows.append(nav_row())
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
        bits=[]
        if attr: bits.append(esc(attr))
        if price: bits.append('<b>'+esc(price)+'</b>')
        out.append('• '+' — '.join(bits))
    return out

def product_text(p):
    """Render a concise product card without generated descriptions or stock disclaimers."""
    name=str(p.get('name') or 'محصول').strip()
    category=str(p.get('category') or 'سایر محصولات').strip()
    lines=[f"<b>✨ {esc(name)}</b>", f"<b>🗂 دسته‌بندی:</b> {esc(category)}"]

    variants=p.get('variants') or []
    labels=[]
    for v in variants[:12]:
        attr=str(v.get('attribute') or v.get('name') or '').strip()
        if attr and attr not in labels:
            labels.append(attr)
    if labels:
        lines.extend(['', '<b>📦 تنوع / بسته‌بندی ثبت‌شده:</b> '+ '، '.join(esc(x) for x in labels)])

    prices=variant_lines(p)
    if prices:
        lines.extend(['', '<b>💰 قیمت ثبت‌شده:</b>', '\n'.join(prices)])
    bio=str(p.get('description') or '').strip()
    if bio:
        lines.extend(['', '<b>📝 درباره محصول:</b>', esc(bio)])
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
def find(pid): return next((p for p in DATA if str(p.get('id',''))==str(pid)),None)

@dp.message(CommandStart())
async def start(m:Message):
    AI_USERS.discard(m.from_user.id)
    OWNER_PASSWORD_PENDING.discard(m.from_user.id)
    history_clear(m.from_user.id)
    await m.answer(HOME_TEXT,reply_markup=home_kb())
@dp.message(Command('owner'))
async def owner_command(m:Message):
    uid=m.from_user.id
    if owner_is_authenticated(uid):
        OWNER_STATES.pop(uid,None)
        await m.answer('👑 پنل مدیریت لیانا',reply_markup=owner_panel_kb())
    else:
        await m.answer('برای ورود، از بخش هوش مصنوعی گزینه «من صاحب ربات هستم» را بزن.')

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

def owner_json_read(path, default):
    try:
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value, type(default)) else default
    except Exception:
        return default

def owner_json_write(path, value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    tmp.replace(path)

def owner_catalog():
    data=owner_json_read(OWNER_CATALOG_PATH, {"added": [], "deleted_ids": []})
    if not isinstance(data, dict): data={"added": [], "deleted_ids": []}
    data.setdefault("added", []); data.setdefault("deleted_ids", [])
    return data

def apply_owner_catalog():
    """Apply owner-added products and deletions after every CSV import."""
    cfg=owner_catalog()
    deleted={str(x) for x in cfg.get("deleted_ids", [])}
    # Hide deleted items from every catalog-driven feature, including AI/search.
    DATA[:] = [p for p in DATA if str(p.get("id", "")) not in deleted]
    existing={str(p.get("id")) for p in DATA}
    for p in cfg.get("added", []):
        if isinstance(p, dict) and str(p.get("id")) not in deleted and str(p.get("id")) not in existing:
            DATA.append(p); existing.add(str(p.get("id")))

def persist_owner_product(product):
    cfg=owner_catalog()
    cfg["deleted_ids"]=[str(x) for x in cfg.get("deleted_ids", []) if str(x)!=str(product.get("id"))]
    cfg["added"]=[p for p in cfg.get("added", []) if str(p.get("id"))!=str(product.get("id"))]
    cfg["added"].append(product)
    owner_json_write(OWNER_CATALOG_PATH,cfg)
    apply_owner_catalog()
    apply_owner_overrides()

def apply_owner_overrides():
    """Apply persistent owner edits after product/feed imports."""
    overrides=owner_json_read(OWNER_OVERRIDES_PATH,{})
    if not isinstance(overrides,dict): return
    for item in DATA:
        ov=overrides.get(str(item.get('id')))
        if not isinstance(ov,dict): continue
        for field in ('description','stock_file','image','name','category','product_url'):
            if field in ov: item[field]=ov[field]
        if 'price_raw' in ov:
            variants=item.get('variants') or []
            if variants:
                for variant in variants: variant['price_raw']=str(ov['price_raw'])
            else:
                item['variants']=[{'id':str(item.get('id','')),'name':str(item.get('name','')),'attribute':'','price_raw':str(ov['price_raw']),'stock_file':str(item.get('stock_file') or 'نامشخص')}]

def owner_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn('💰 تغییر قیمت محصول','owner:edit:price'),btn('📦 تغییر موجودی','owner:edit:stock')],
        [btn('📝 ویرایش بیوی محصول','owner:edit:description')],
        [btn('🛠 ویرایش همه اطلاعات محصول','owner:edit:all')],
        [btn('➕ افزودن محصول جدید','owner:add'),btn('➖ حذف محصول','owner:delete')],
        [btn('🧠 آموزش پاسخ به سؤال‌ها','owner:teach:faq')],
        [btn('🛍 آموزش پیشنهاد یک محصول','owner:teach:product')],
        [btn('🤖 آزمایش هوش مصنوعی','owner:testai')],
        [btn('🏠 بازگشت به خانه','home')]
    ])

def owner_is_authenticated(uid:int)->bool:
    return is_owner_id(uid) and uid in OWNER_AI_USERS

def owner_learning():
    data=owner_json_read(OWNER_LEARNING_PATH,{'faqs':[],'rules':[]})
    if not isinstance(data,dict): data={'faqs':[],'rules':[]}
    data.setdefault('faqs',[]); data.setdefault('rules',[])
    return data

def learned_owner_answer(query:str):
    """Return owner-taught FAQ or product recommendation before generic AI."""
    q=normalize_fa(query)
    learned=owner_learning()
    for rule in learned.get('rules',[]):
        keys=[normalize_fa(x) for x in rule.get('keywords',[]) if str(x).strip()]
        if keys and any(k and k in q for k in keys):
            prod=find(rule.get('product_id'))
            if prod:
                return str(rule.get('intro') or 'حتماً 🌷 این محصول از فروشگاه لیانا با توضیحت مرتبطه:')+'\n\n🧴 '+str(prod.get('name',''))+'\n'+str(prod.get('description') or describe_product(prod))
    from difflib import SequenceMatcher
    best=None; best_score=0
    q_tokens=set(q.split())
    for item in learned.get('faqs',[]):
        keys=[normalize_fa(x) for x in item.get('keywords',[]) if str(x).strip()]
        score=0
        for key in keys:
            if key and key in q: score=max(score,12+len(key.split()))
            else:
                kt=set(key.split())
                overlap=len(kt & q_tokens)
                if overlap: score=max(score,overlap*4 + overlap/max(1,len(kt))*4)
                elif key:
                    if any(SequenceMatcher(None,t,key).ratio()>=0.86 for t in q_tokens if len(t)>=4): score=max(score,5)
        if score>best_score: best=item; best_score=score
    if best and best_score>=7: return str(best.get('answer') or '') or None
    return None

def ai_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("👑 من صاحب ربات هستم", "ai:owner")],
        [btn("↩️ خروج از هوش مصنوعی", "ai:exit"), btn("🏠 خانه", "home")]
    ])


def fuzzy_catalog_products(query:str, limit=8):
    """Find catalog products from short keywords or long colloquial messages, tolerating typos."""
    from difflib import SequenceMatcher
    q=normalize_fa(query)
    if not q:
        return []
    # Common casual phrases and request words should not dominate product matching.
    stop={
        'شما','ما','من','منو','برای','واسه','یه','یک','این','اون','آن','رو','را','به','از','با','و','یا',
        'دارید','دارین','دارین؟','موجود','موجوده','هست','هستش','هستید','میخواهم','میخوام','می','خوام',
        'می شود','میشه','میشه؟','پیدا','کن','کنید','میشه','لطفا','لطفاً','خواهشا','خواهشاً','سلام',
        'محصول','محصولات','کالا','چیز','چیزی','جنس','جنسش','مدل','مدلش','پیشنهاد','معرفی','بده','بدید',
        'بگید','بگو','کدوم','کدام','چه','چی','چیه','چطوره','خوبه','خوب','مناسب','دارم','میخوام','می خواهم',
        'قیمت','چنده','چقدر','توی','داخل','فروشگاه','لیانا','شاپ','برام','برامون','هم','همین','اونایی',
        'میشه','می‌خوام','میکنی','میکنید','می‌کنید','بخرم','بگیرم','خرید','کردن','کردید','دنبال','هستم'
    }
    # Normalize joined/spoken variants.
    replacements={
        'رژلب':'رژ لب','کرمپودر':'کرم پودر','ضدافتاب':'ضد آفتاب','ضدآفتاب':'ضد آفتاب',
        'لیپگلاس':'لیپ گلاس','بادیاسپلش':'بادی اسپلش','فیسواش':'فیس واش','خطچشم':'خط چشم',
        'رژگونه':'رژ گونه','مدادابرو':'مداد ابرو','اسپری بدن':'بادی اسپلش'
    }
    for old,new in replacements.items():
        q=q.replace(old,new)
    q_tokens=[t for t in q.split() if len(t)>1 and t not in stop and not t.isdigit()]
    if not q_tokens:
        return []
    # Synonyms cover everyday descriptions rather than only official product terms.
    synonyms={
        'لب':['رژ لب','تینت','لیپ گلاس','برق لب','بالم لب','خط لب'],
        'رنگ لب':['رژ لب','تینت','لیپ گلاس'],
        'براق':['لیپ گلاس','برق لب'],
        'خوشبو':['عطر','ادکلن','بادی اسپلش'],
        'بو':['عطر','ادکلن','بادی اسپلش'],
        'بدن':['بادی اسپلش','لوسیون','نمک بدن'],
        'لک':['کانسیلر','کرم پودر','سرم'],
        'تیرگی':['کانسیلر'],
        'زیر چشم':['کانسیلر'],
        'چشم':['ریمل','خط چشم','سایه چشم','مداد چشم'],
        'مژه':['ریمل','مژه مصنوعی','فرمژه'],
        'ابرو':['مداد ابرو','ژل ابرو','صابون ابرو'],
        'پوست':['آبرسان','مرطوب کننده','شوینده','ضد آفتاب','کرم پودر'],
        'خشکی':['آبرسان','مرطوب کننده','کرم دست'],
        'جوش':['ضد جوش','شوینده','سرم'],
        'ناخن':['لاک','ناخن مصنوعی','ناخن گیر'],
        'مو':['شامپو','ماسک مو','سرم مو']
    }
    expanded=list(q_tokens)
    for token in q_tokens:
        for key, vals in synonyms.items():
            if token==key or (len(token)>=3 and (token in key or key in token)):
                expanded.extend(normalize_fa(v) for v in vals)
    query_terms=list(dict.fromkeys(expanded))
    scored=[]
    for item in DATA:
        if not item.get('published',True):
            continue
        name=normalize_fa(item.get('name',''))
        category=normalize_fa(item.get('category',''))
        attrs=' '.join(normalize_fa(v.get('attribute') or v.get('name') or '') for v in item.get('variants',[]))
        desc=normalize_fa(item.get('description',''))
        hay=f'{name} {category} {attrs} {desc}'
        name_tokens=name.split()
        score=0; matched=0
        for token in q_tokens:
            if token in name:
                score+=8 if token in name_tokens else 6; matched+=1
            elif token in category:
                score+=4; matched+=1
            elif token in attrs:
                score+=3; matched+=1
            elif token in desc:
                score+=1; matched+=1
            else:
                # Typo-tolerant token match against product name and category terms.
                candidates=[x for x in (name_tokens+category.split()) if len(x)>=3 and len(token)>=3]
                best=max((SequenceMatcher(None,token,x).ratio() for x in candidates),default=0)
                if best>=0.76:
                    score+=4; matched+=1
        # Exact brand/product keyword in a long message should be enough to surface it.
        if matched:
            score += int(8*matched/max(1,len(q_tokens)))
            if len(q_tokens)>1 and matched/len(q_tokens)>=0.5:
                score+=3
            scored.append((score,matched,name,item))
    scored.sort(key=lambda x:(-x[0],-x[1],x[2]))
    # Avoid unrelated results from a very weak overlap.
    if not scored or scored[0][0]<4:
        return []
    return [x[3] for x in scored[:max(1,min(int(limit),200))]]

def product_context(query:str, limit=8):
    """Provide AI with best-ranked catalogue matches, their descriptions and variants."""
    matches=intent_products(query, limit)
    if not matches:
        matches=fuzzy_catalog_products(query,limit)
    if not matches:
        matches=search_products(query,limit)
    # Merge fuzzy matches to catch keywords embedded in long sentences or misspelled words.
    fuzzy=fuzzy_catalog_products(query,limit)
    seen_ids={str(p.get('id')) for p in matches}
    for p in fuzzy:
        if str(p.get('id')) not in seen_ids:
            matches.append(p); seen_ids.add(str(p.get('id')))
        if len(matches)>=limit:
            break
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
    table=str.maketrans({'ي':'ی','ى':'ی','ك':'ک','ۀ':'ه','ة':'ه','أ':'ا','إ':'ا','ٱ':'ا','ؤ':'و','ئ':'ی','‌':' ','ـ':' ','۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9'})
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

def is_cosmetics_only_question(q:str, history=None)->bool:
    """Allow only beauty/cosmetics topics in the public AI chat, plus brief social replies."""
    qn=normalize_fa(q)
    if not qn:
        return False
    # Brief greetings and courtesy messages are allowed so the chat feels natural.
    if is_small_talk(qn):
        return True
    # Follow-up answers (e.g. skin type, tone, budget) are allowed only in an active beauty discussion.
    if history:
        last_assistant=normalize_fa(next((x.get('content','') for x in reversed(history) if x.get('role')=='assistant'),''))
        previous_user=normalize_fa(next((x.get('content','') for x in reversed(history) if x.get('role')=='user'),''))
        followup_markers=('رنگ پوست','تناژ','نوع پوست','پوستت چرب','پوستت خش','بودجه','برای تیرگی','برای لک','کدام محصول','کدوم محصول','چه محصول')
        beauty_markers=('آرایش','آرایشی','بهداشتی','زیبایی','میکاپ','پوست','مو','ناخن','لب','چشم','ابرو','مژه','رژ','ریمل','کرم','ضدآفتاب','عطر','ادکلن','بادی اسپلش','تینت','پرایمر','کانسیلر','پنکیک','شوینده','آبرسان','مرطوب کننده','تونر','سرم','ماسک','پرایمر','لاک','محصول')
        if len(qn.split())<=6 and any(x in last_assistant for x in followup_markers) and any(x in (previous_user+' '+last_assistant) for x in beauty_markers):
            return True
    # Exact product names in the current catalogue count as cosmetics/store-product questions.
    if product_name_matches(qn):
        return True
    # Beauty-specific vocabulary. Deliberately exclude generic store, address, delivery and unrelated terms.
    beauty_terms=(
      'آرایش','آرایشی','بهداشتی','زیبایی','میکاپ','پوست','مو','ناخن','لب','چشم','ابرو','مژه',
      'رژ','ریمل','خط چشم','سایه','رژگونه','هایلایتر','کانتور','تینت','بی بی کرم','سی سی کرم','کوشن',
      'کرم پودر','کرمپودر','فوندیشن','پرایمر','کانسیلر','پنکیک','پودر فیکس','فیکساتور','اسپری فیکس',
      'بالم لب','لیپ گلاس','برق لب','مداد ابرو','ژل ابرو','صابون ابرو','فرمژه','مژه مصنوعی','چسب مژه',
      'لاک','ناخن','پاک کننده لاک','پد لاک','شوینده','فیس واش','میسلار','تونر','سرم','آبرسان',
      'مرطوب کننده','ماسک صورت','جوش','آکنه','جای جوش','لک صورت','منافذ','روتین پوست','ضدآفتاب',
      'ضد آفتاب','spf','ترکیبات محصول','رنگ پوست','تناژ','پوست چرب','پوست خشک','پوست مختلط','پوست حساس',
      'عطر','ادکلن','بادی اسپلش','خوشبوکننده','براش','پد آرایشی','اسفنج آرایشی','پاک کردن آرایش',
      'محصول آرایشی','محصول بهداشتی','محصول مراقبت','موجودی ریمل','قیمت ریمل','قیمت رژ','قیمت کرم'
    )
    if any(normalize_fa(term) in qn for term in beauty_terms):
        return True
    # Allow purchase/price/stock questions only when they clearly name a cosmetic product.
    if any(x in qn for x in ('قیمت','چنده','چقدر','موجود','دارید','دارین','میخوام','می خواهم','می خوام','پیدا کن','پیشنهاد بده','چی بخرم')):
        return any(x in qn for x in ('ریمل','رژ','کرم','تینت','عطر','ادکلن','بادی اسپلش','ضدآفتاب','لاک','شوینده','آبرسان','کانسیلر','پرایمر','پنکیک','لیپ گلاس','محصول آرایشی','محصول بهداشتی')) or bool(search_products(qn, limit=1))
    return False


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
    beauty_nouns=('رنگ','لب','صورت','چشم','پوست','مو','ناخن','آرایش','مراقبت','کرم','رژ','تینت','عطر','ضدآفتاب','ریمل','ابرو','تینت','رژ','لب','پوست','مو','مژه','ناخن','پودر فیکس','اسپری فیکس','بالم','تونر','سرم','ماسک','لیپ گلاس','لیپگلاس','برق لب','بادی اسپلش','بادیاسپلش','بادی اسپری','اسپری بدن','گلاس لب')
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
    """Robust Persian product search: exact names, normalized spelling, tokens and typo tolerance."""
    from difflib import SequenceMatcher
    q = normalize_fa(query)
    if not q or len(q) < 2:
        return []

    # Normalize common joined terms consistently on both sides.
    def search_norm(value):
        value = normalize_fa(value)
        replacements = (
            ('بادیاسپلش', 'بادی اسپلش'), ('لیپگلاس', 'لیپ گلاس'),
            ('رژلب', 'رژ لب'), ('کرمپودر', 'کرم پودر'),
            ('ضدافتاب', 'ضد آفتاب'), ('ضدآفتاب', 'ضد آفتاب'),
            ('فیسواش', 'فیس واش'), ('خطچشم', 'خط چشم'),
            ('رژگونه', 'رژ گونه'), ('مدادابرو', 'مداد ابرو'),
        )
        for old, new_value in replacements:
            value = value.replace(old, new_value)
        return re.sub(r'\s+', ' ', value).strip()

    q = search_norm(q)
    stop = {'برای','من','چی','چیه','کدام','خوب','مناسب','دارم','می خواهم','می','خواهم',
            'محصول','محصولات','لطفا','یه','یک','از','به','با','را','و','یا','این','اون',
            'است','هست','توی','داخل','فروشگاه','لیانا','بخرم','پیشنهاد','بده','بهترین',
            'دارید','می شود','کنم','کنه','رو','چه','خبر','میخوام','میخوام'}
    aliases = {
        'کرم پودر':['فوندیشن','foundation','بی بی کرم','bb کرم','سی سی کرم','cc کرم','کوشن'],
        'رژ لب':['لیپ گلاس','لیپ گلاس','برق لب','بالم لب','تینت لب','رژ مایع','رژ جامد'],
        'لیپ گلاس':['برق لب','lip gloss','گلاس لب'],
        'بادی اسپلش':['body splash','اسپری بدن','بادی اسپری'],
        'لب':['رژ لب','لیپ گلاس','بالم لب','تینت لب','خط لب','رژ مایع'],
        'ابرو':['مداد ابرو','ژل ابرو','صابون ابرو'],
        'چشم':['ریمل','خط چشم','سایه','مداد چشم'],
        'جوش':['آکنه','ضد جوش','سرم آکنه'],
        'آبرسان':['مرطوب کننده','هیالورونیک','کرم آبرسان'],
        'ضد آفتاب':['پنکیک spf','spf'], 'ناخن':['لاک'],
        'پاکسازی':['فیس واش','شوینده','پاک کننده','میسلار','تونر'],
        'عطر':['ادکلن','بادی اسپلش','body splash'],
        'خوشبوکننده':['عطر','ادکلن','بادی اسپلش','بادی اسپری'],
        'تینت':['تینت لب','تینت گونه'], 'نمک بدن':['اسکراب بدن'],
        'کانسیلر':['concealer'], 'پنکیک':['پودر فشرده'],
        'مرطوب کننده':['کرم آبرسان','لوسیون'],
        'شوینده':['فیس واش','ژل شستشو','فوم شستشو'],
        'رژگونه':['بلشر','blush'], 'هایلایتر':['highlighter'],
        'پرایمر':['primer'], 'فیکساتور':['اسپری فیکس','پودر فیکس'],
        'مو':['مراقبت مو','ماسک مو','شامپو','سرم مو']
    }
    # Keep the query tokens ordered and stable (a set made ranking nondeterministic).
    q_tokens = [t for t in q.split() if len(t) > 1 and t not in stop]
    # Fast path for exact names. Keep every duplicate-name record visible instead of
    # silently hiding a second catalog item with the same display name.
    exact_matches = [item for item in DATA
                     if item.get('published', True) and search_norm(item.get('name', '')) == q]
    if exact_matches:
        return exact_matches[:max(1, min(int(limit), 200))]
    expanded = list(q_tokens)
    for key, vals in aliases.items():
        if search_norm(key) in q or any(search_norm(v) in q for v in vals):
            expanded.extend(search_norm(v) for v in [key, *vals])
    terms = list(dict.fromkeys(t for t in expanded if len(t) > 1 and t not in stop))

    scored = []
    for item in DATA:
        if not item.get('published', True):
            continue
        name = search_norm(item.get('name', ''))
        category = search_norm(item.get('category', ''))
        variants = ' '.join(search_norm(v.get('attribute') or v.get('name') or '')
                            for v in (item.get('variants') or []))
        description = search_norm(item.get('description', ''))
        hay = f'{name} {category} {variants} {description}'
        score = 0
        if q == name:
            score += 1000
        elif q in name:
            score += 100
        elif name and name in q:
            score += 70

        # Token coverage makes a product discoverable even when the customer omits
        # package counts, brand words, or writes words in a different order.
        matched = 0
        for token in q_tokens:
            if token in name.split():
                score += 22; matched += 1
            elif token in name:
                score += 15; matched += 1
            elif token in variants:
                score += 12; matched += 1
            elif token in category:
                score += 5; matched += 1
            elif token in description:
                score += 2; matched += 1
            else:
                best = max((SequenceMatcher(None, token, nt).ratio()
                            for nt in name.split() if len(nt) >= 3), default=0)
                if len(token) >= 4 and best >= 0.80:
                    score += 5; matched += 1
        if q_tokens and matched == len(q_tokens):
            score += 12
        elif q_tokens and matched:
            score += int(10 * matched / len(q_tokens))

        # Expand category/family requests, but avoid blindly returning unrelated products.
        for key, vals in aliases.items():
            if search_norm(key) in q or any(search_norm(v) in q for v in vals):
                if any(search_norm(term) in name for term in [key, *vals]):
                    score += 18

        if score > 0:
            scored.append((score, name, item))

    # Stable tie-break: exact/product-name matches first, then alphabetical.
    scored.sort(key=lambda x: (-x[0], x[1]))
    results = [item for _, _, item in scored]
    return results[:max(1, min(int(limit), 200))]



# Recognized shopping intents. Each family has phrases customers may use; matches are
# filtered back to actual catalog names so a request for lip gloss does not show tools.
PRODUCT_INTENTS = [
    ('ضد آفتاب', ('ضد آفتاب','ضدافتاب','ضدآفتاب','ضد افتاب','sunscreen')),
    ('بادی اسپلش', ('بادی اسپلش','بادیاسپلش','بادی اسپری','اسپری بدن','خوشبوکننده بدن')),
    ('لیپ گلاس', ('لیپ گلاس','لیپگلاس','برق لب','گلاس لب','lip gloss')),
    ('رژ لب', ('رژ لب','رژلب','رژ مایع','رژ جامد','رژ مات','رژ براق')),
    ('تینت', ('تینت','تینت لب','تینت گونه')),
    ('خط لب', ('خط لب','مداد لب')),
    ('بالم لب', ('بالم لب','مرطوب کننده لب')),
    ('رژگونه', ('رژگونه','رژ گونه','بلشر')),
    ('ریمل', ('ریمل','mascara')),
    ('خط چشم', ('خط چشم','خطچشم','eyeliner')),
    ('مداد ابرو', ('مداد ابرو','مدادابرو')),
    ('ابرو', ('ژل ابرو','صابون ابرو','لیفت ابرو')),
    ('کانسیلر', ('کانسیلر','concealer')),
    ('کرم پودر', ('کرم پودر','کرمپودر','فوندیشن','foundation','بی بی کرم','سی سی کرم','کوشن')),
    ('پنکیک', ('پنکیک','پودر فشرده')),
    ('پرایمر', ('پرایمر','primer')),
    ('پودر فیکس', ('پودر فیکس','فیکساتور','اسپری فیکس')),
    ('آبرسان', ('آبرسان','هیالورونیک','مرطوب کننده','مرطوب‌کننده')),
    ('شوینده', ('شوینده','فیس واش','فیسواش','ژل شستشو','فوم شستشو','پاک کننده صورت')),
    ('ضد جوش', ('ضد جوش','آکنه','جوش صورت','سرم جوش')),
    ('نمک بدن', ('نمک بدن','اسکراب بدن')),
    ('شامپو', ('شامپو','مراقبت مو','ماسک مو','سرم مو')),
    ('عطر', ('عطر','ادکلن','eau de parfum')),
    ('لاک ناخن', ('لاک ناخن','لاک','ناخن مصنوعی','ناخن بچه گانه')),
    ('براش', ('براش','قلم مو آرایشی','ست براش')),
]

def intent_families(query):
    q=normalize_fa(query)
    found=[]
    for family, markers in PRODUCT_INTENTS:
        normalized_markers=[normalize_fa(x) for x in markers]
        if any(marker and marker in q for marker in normalized_markers):
            found.append((family, markers))
    # Preserve phrase order but don't return the same family twice.
    out=[]; seen=set()
    for family,markers in found:
        if family not in seen:
            seen.add(family); out.append((family,markers))
    return out

def intent_products(query, limit=8):
    """Extract multiple product needs from a full message and return real catalog items."""
    q=normalize_fa(query)
    if not q:
        return []
    out=[]; seen=set()
    # A full product name inside a conversational sentence has highest priority.
    for p in product_name_matches(q):
        pid=str(p.get('id',''))
        if pid not in seen:
            out.append(p); seen.add(pid)
        if len(out)>=limit:
            return out[:limit]
    families=intent_families(q)
    if not families:
        return out
    per_family=max(2, min(20, limit//max(1,len(families))))
    for family, markers in families:
        candidates=search_products(family,20)
        strict=[]
        normalized_markers=[normalize_fa(x) for x in markers]
        for p in candidates:
            if not p.get('published',True):
                continue
            name=normalize_fa(p.get('name',''))
            category=normalize_fa(p.get('category',''))
            # A category-level family may use a synonym rather than the literal family name.
            if any(m and m in name for m in normalized_markers):
                strict.append(p)
            elif family in ('آبرسان','شوینده','شامپو','لاک ناخن','ابرو','ضد جوش') and any(m and m in category for m in normalized_markers):
                strict.append(p)
        if not strict:
            # Keep a conservative fallback from search ranking rather than returning no help.
            strict=[p for p in candidates if p.get('published',True)]
        added=0
        for p in strict:
            pid=str(p.get('id',''))
            if pid in seen:
                continue
            out.append(p); seen.add(pid); added+=1
            if len(out)>=limit or added>=per_family:
                break
        if len(out)>=limit:
            break
    return out[:max(1,min(int(limit),200))]

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
    """Attach real product buttons and a paginated list of all relevant AI suggestions."""
    q=normalize_fa(contextual_query(user_text,history or []))
    a=normalize_fa(answer)
    rows=[]; products=[]; seen=set()

    # Prefer products explicitly named in the answer.
    for p in DATA:
        if not p.get('published',True):
            continue
        name=normalize_fa(p.get('name',''))
        if len(name)>=4 and name in a and str(p.get('id')) not in seen:
            products.append(p); seen.add(str(p.get('id')))
            if len(products)>=5:
                break

    # Include relevant catalog items, including newly imported CSV products.
    all_relevant=intent_products(q, 200)
    relevant_ids={str(p.get('id')) for p in all_relevant}
    for p in fuzzy_catalog_products(q, 200):
        if str(p.get('id')) not in relevant_ids:
            all_relevant.append(p); relevant_ids.add(str(p.get('id')))
        if len(all_relevant)>=200:
            break
    for p in all_relevant:
        pid=str(p.get('id'))
        if pid not in seen and len(products)<5:
            products.append(p); seen.add(pid)

    for p in products[:5]:
        row=[btn('🧴 '+str(p.get('name','محصول'))[:42],f"product:{p['id']}")]
        url=str(p.get('product_url') or '').strip()
        if url.startswith(('https://','http://')):
            row.append(btn('🔗 صفحه محصول',url=url))
        rows.append(row)

    # Save the full matching list and provide paging for long recommendation lists.
    merged=[]; merged_seen=set()
    for p in products + all_relevant:
        pid=str(p.get('id'))
        if pid not in merged_seen:
            merged.append(p); merged_seen.add(pid)
    if len(merged)>5:
        page_key=hashlib.md5((str(user_text)+str(time.time_ns())).encode('utf-8')).hexdigest()[:8]
        AI_PAGE_PRODUCTS[page_key]=[str(p.get('id')) for p in merged]
        while len(AI_PAGE_PRODUCTS)>100:
            AI_PAGE_PRODUCTS.pop(next(iter(AI_PAGE_PRODUCTS)))
        rows.append([btn('📄 دیدن همه پیشنهادها ('+str(len(merged))+' محصول)','aipage:0:'+page_key)])

    store_intent=any(x in q for x in ('فروشگاه','لیانا','آدرس','شماره تماس','شماره فروشگاه','ساعت کاری','شعبه','ارسال','سفارش','اطلاعات فروشگاه','کجاست','کجا هستید'))
    if store_intent:
        rows.append([btn('🏬 اطلاعات کامل فروشگاه','shop'),btn('🛒 سایت فروشگاه',url=SHOP_URL)])
    rows.append([btn('↩️ ادامه گفت‌وگو','ai:continue'),btn('🏠 خانه','home')])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def knowledge_answer(q:str):
    """Semantic-ish offline retrieval plus owner-taught answers, without external APIs."""
    learned=learned_owner_answer(q)
    if learned:
        return learned
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
        fuzzy=fuzzy_catalog_products(q,5)
        if fuzzy and any(x in q for x in ('دارید','دارین','موجود','قیمت','چنده','چقدر','وجود داره','پیدا میشه')):
            candidates=fuzzy[:3]
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
    families=[('بادی اسپلش','بادی اسپلش'),('بادیاسپلش','بادی اسپلش'),('بادی اسپری','بادی اسپلش'),('اسپری بدن','بادی اسپلش'),('خوشبوکننده بدن','بادی اسپلش'),('بوی خوب','بادی اسپلش'),('رایحه','بادی اسپلش'),('نمک بدن','نمک بدن'),('پنکیک','پنکیک'),('رژ لب','رژ لب'),('رژلب','رژ لب'),('تینت','تینت'),('کانسیلر','کانسیلر'),('کرم پودر','کرم پودر'),('کرمپودر','کرم پودر'),('آبرسان','آبرسان'),('مرطوب کننده','مرطوب کننده'),('ریمل','ریمل'),('رژگونه','رژگونه')]
    chosen=next(((word,key) for word,key in families if word in q),None)
    if chosen and (any(x in q for x in ('بهترین','پیشنهاد','معرفی','چی خوبه','می خواهم','میخوام','دارید','پیدا کن','پیدا کنه','خوبه','مناسب')) or any(x in q for x in ('گندمی','روشن','تیره','سبزه','تناژ','رنگ پوستم','بهم میاد','به من میاد'))):
        word,key=chosen
        items=search_products(key,6)
        if items:
            if key=='نمک بدن': intro='نمک بدن برای روتین مراقبت بدن و لایه‌برداری سطحی استفاده می‌شود؛ پوست حساس یا تحریک‌شده را با آن نساب و دستور بسته‌بندی را رعایت کن. گزینه‌های لیانا: '
            elif key=='تینت': intro='تینت برای رنگ‌دهی سبک و طبیعی به لب کاربرد دارد؛ شدت رنگ را می‌توان معمولاً با لایه‌گذاری بیشتر کرد. گزینه‌های لیانا: '
            elif key=='کانسیلر': intro='کانسیلر برای پوشش موضعی تیرگی زیر چشم یا لک‌هاست؛ انتخاب بافت و رنگ به نوع پوست و تناژ بستگی دارد. گزینه‌های لیانا: '
            elif key=='پنکیک': intro='پنکیک برای مرتب‌کردن آرایش پایه و یکدست‌تر نشان‌دادن پوست کاربرد دارد؛ کد رنگ باید با پوست و گردن هماهنگ باشد. گزینه‌های لیانا: '
            elif key=='بادی اسپلش': intro='بادی‌اسپلش برای خوشبوکردن بدن استفاده می‌شود؛ رایحه‌ی خوشایند کاملاً سلیقه‌ای است و ماندگاری هر مدل را بدون اطلاعات سازنده نمی‌شود قطعی گفت. 🌸 چند گزینه از محصولات لیانا برای بررسی: '
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
    # Direct product-intent search must run before generic FAQ replies.
    # This catches requests such as «لیپ گلاس میخوام» and «بادی اسپلش می‌خوام».
    product_families = {
      'لیپ گلاس': ('لیپ گلاس','لیپگلاس','برق لب','گلاس لب'),
      'بادی اسپلش': ('بادی اسپلش','بادی‌اسپلش','بادیاسپلش','بادی اسپری','اسپری بدن'),
      'تینت': ('تینت',), 'کانسیلر': ('کانسیلر',), 'پنکیک': ('پنکیک',),
      'رژ لب': ('رژ لب','رژلب','رژ مایع','رژ جامد'), 'رژگونه': ('رژگونه','رژ گونه'),
      'ریمل': ('ریمل',), 'عطر': ('عطر','ادکلن'), 'نمک بدن': ('نمک بدن',),
      'آبرسان': ('آبرسان','مرطوب کننده','هیالورونیک'), 'کرم پودر': ('کرم پودر','کرمپودر','فوندیشن')
    }
    product_request = any(x in q for x in ('می خواهم','میخوام','می خواستم','میخاستم','دنبال','پیدا کن','پیشنهاد بده','معرفی کن','دارید','دارین','می خوام'))
    requested_family = None
    for fam, markers in product_families.items():
        if any(normalize_fa(marker) in q for marker in markers):
            requested_family = fam; break
    if requested_family and (product_request or len(q.split()) <= 5):
        results = search_products(requested_family, 6)
        # Keep only true matches from the requested family, so e.g. lip gloss doesn't return lipstick tools.
        markers = product_families[requested_family]
        strict = [p for p in results if any(normalize_fa(m) in normalize_fa(p.get('name','')) for m in markers)]
        if strict: results = strict
        if results:
            lines=[]
            for p in results[:6]:
                prices=[]
                for v in p.get('variants',[])[:2]:
                    price=money_toman(v.get('price_raw'))
                    if price: prices.append(price)
                price_text=(' — '+ '، '.join(prices)) if prices else ''
                lines.append('• '+p.get('name','محصول')+price_text)
            return 'حتماً عزیزم 🌷 این گزینه‌ها رو از محصولات لیانا برات پیدا کردم:\n\n'+'\n'.join(lines)+'\n\nروی دکمه‌ی محصولی که دوست داری بزن تا جزئیاتش رو ببینی. اگر رنگ یا مدل خاصی مدنظرت هست بگو تا دقیق‌تر بگردم.'
    # Generic availability/recommendation phrasing: use fuzzy matching over the full message.
    shopping_markers=('دارید','دارین','موجود','پیشنهاد','معرفی','دنبال','میخوام','می خواهم',
                      'می خوام','پیدا کن','چی خوبه','چی مناسبه','برای من','به درد','میگردم','می گردم')
    if any(normalize_fa(x) in q for x in shopping_markers):
        found=fuzzy_catalog_products(user_text,6)
        if found:
            lines=[]
            for item in found[:6]:
                price=money_toman((item.get('variants') or [{}])[0].get('price_raw'))
                lines.append('🧴 '+item.get('name','محصول')+(' — '+price if price else ''))
            return 'حتماً 🌷 از روی توضیحت این محصولات مرتبط رو در لیانا پیدا کردم:\n\n'+'\n'.join(lines)+'\n\nبرای دیدن جزئیات هر محصول روی دکمه‌اش بزن. اگر منظورت کاربرد خاصی بوده، بگو تا پیشنهادها رو دقیق‌تر کنم.'
    smart=recommendation_answer(user_text)
    if smart: return smart
    # Long natural-language shopping messages: retrieve actual catalog matches even if
    # no exact intent phrase was recognized (including misspellings and colloquial wording).
    shopping_markers=('دارید','دارین','موجود','پیشنهاد','معرفی','دنبال','میخوام','می خواهم','می خوام',
                      'پیدا کن','چی خوبه','چی مناسبه','برای من','به درد','میگردم','می گردم','می‌گردم')
    if any(normalize_fa(x) in q for x in shopping_markers):
        fuzzy=fuzzy_catalog_products(q,6)
        if fuzzy:
            lines=[]
            for item in fuzzy[:6]:
                price=money_toman((item.get('variants') or [{}])[0].get('price_raw'))
                lines.append('• '+esc(item.get('name','محصول'))+(' — '+esc(price) if price else ''))
            return '🔎 از روی کلمات کلیدی پیامت این گزینه‌های مرتبط رو در محصولات لیانا پیدا کردم 🌷\n\n'+'\n'.join(lines)+'\n\nبرای دیدن جزئیات، روی دکمه محصول بزن. اگر کاربرد یا مدل خاصی مدنظرت هست بگو تا دقیق‌تر بگردم.'
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
    # Last-chance keyword extraction: if the message contains a recognizable product
    # family but was phrased unusually, search the catalogue instead of returning a hollow reply.
    keyword_families = {
      'بادی اسپلش': ('بادی اسپلش','بادیاسپلش','بادی اسپری','اسپری بدن','خوشبوکننده بدن','رایحه','بوی خوب'),
      'عطر': ('عطر','ادکلن','عطر بدن'),
      'رژ لب': ('رژ لب','رژلب','رژ مایع','رژ جامد'),
      'لیپ گلاس': ('لیپ گلاس','لیپگلاس','برق لب'),
      'تینت': ('تینت',), 'ریمل': ('ریمل',), 'کانسیلر': ('کانسیلر',),
      'رژگونه': ('رژگونه','رژ گونه'), 'ضد آفتاب': ('ضد آفتاب','ضدافتاب'),
      'شوینده': ('شوینده','فیس واش','ژل شستشو'), 'آبرسان': ('آبرسان','مرطوب کننده')
    }
    for family, keywords in keyword_families.items():
        if any(normalize_fa(k) in q for k in keywords):
            matches=search_products(family,5)
            if matches:
                lines=[]
                for item in matches[:5]:
                    price=money_toman((item.get('variants') or [{}])[0].get('price_raw'))
                    lines.append('🧴 <b>'+esc(item.get('name','محصول'))+'</b>'+(' — '+esc(price) if price else ''))
                return '🔎 از روی کلمات کلیدی پیامت، این محصولات مرتبط رو در لیانا پیدا کردم 🌷\n\n'+'\n'.join(lines)+'\n\nبرای دیدن جزئیات و صفحه مستقیم هر محصول از دکمه‌های زیر استفاده کن. اگر کاربرد یا بودجه‌ی خاصی مدنظرت هست بگو تا پیشنهادها رو دقیق‌تر کنم.'
    return '🌸 من دستیار لیانا شاپ هستم و می‌تونم درباره آرایش، مراقبت پوست و مو، انتخاب محصولات، قیمت و موجودی، آدرس فروشگاه و خرید راهنمایی‌ات کنم. مثلاً بنویس «یه تینت لب خوب می‌خوام»، «برای پوست چرب چی مناسبه؟» یا «یه بادی‌اسپلش خوشبو معرفی کن». اگر منظورت محصول خاصیه، اسم یا کاربردش رو بگو تا داخل محصولات لیانا جست‌وجو کنم.'

def catalog_request_reply(query:str, limit:int=5):
    """Return only real catalog items that match a clearly requested product family."""
    q = normalize_fa(query)
    families = intent_families(q)
    if not families:
        return None

    matches = intent_products(q, 200)
    strict = []
    seen = set()
    # If the customer names an extra brand/model word that exists in the catalog,
    # use it as a constraint rather than listing every item in the same category.
    ignored = {
        'عمده','لطفا','لطفاً','میخوام','می خواهم','میخواهم','می شود','میشه','دارید',
        'دارین','موجود','هست','هستش','برای','واسه','یه','یک','خوب','بهتر','بهترین',
        'محصول','محصولات','پیشنهاد','بده','بدید','معرفی','قیمت','چنده','چقدر','لطفا'
    }
    family_words = set()
    for _, markers in families:
        for marker in markers:
            family_words.update(normalize_fa(marker).split())
    query_tokens = [t for t in q.split() if len(t) > 2 and t not in ignored and t not in family_words]
    catalog_words = set()
    for item in DATA:
        catalog_words.update(normalize_fa(item.get('name', '')).split())
    specific_terms = [t for t in query_tokens if t in catalog_words]
    for family, markers in families:
        normalized_markers = [normalize_fa(x) for x in markers if normalize_fa(x)]
        for product in matches:
            if not product.get('published', True):
                continue
            name = normalize_fa(product.get('name', ''))
            # Only show a product if its actual name supports the requested product type.
            if not any(marker in name for marker in normalized_markers):
                continue
            if specific_terms and not all(term in name for term in specific_terms):
                continue
            pid = str(product.get('id', ''))
            if pid not in seen:
                strict.append(product)
                seen.add(pid)
    if not strict:
        requested = '، '.join(family for family, _ in families)
        return (
            '🌷 ممنون که گفتی دنبال چی هستی! فعلاً محصولی با تطابق کافی برای '
            f'«{requested}» در فهرست محصولات لیانا پیدا نکردم. 💗\n\n'
            'نمی‌خوام محصول نامرتبطی بهت پیشنهاد بدم؛ اگر مدل، برند یا اسم دیگری مدنظرت هست '
            'برام بنویس تا دوباره دقیق‌تر جست‌وجو کنم.'
        )

    shown = strict[:limit]
    out = ['🔎 این محصولاتِ مرتبط رو در کاتالوگ لیانا پیدا کردم 🌷', '']
    for product in shown:
        out.append('🧴 ' + str(product.get('name', 'محصول')))
        variants = product.get('variants') or []
        prices = []
        for variant in variants[:4]:
            price = money_toman(variant.get('price_raw'))
            attr = str(variant.get('attribute') or '').strip()
            if price:
                prices.append((attr + ' — ' if attr else '') + price)
        if prices:
            out.extend(prices)
        description = str(product.get('description') or '').strip()
        if description:
            out.append(description[:280])
        out.append('')
    out.append('💗 اگر منظورت برند، رنگ یا بسته‌بندی مشخصیه، بگو تا بین همین محصولات دقیق‌تر برات پیدا کنم.')
    return '\n'.join(out).strip()


async def call_gemini(user_text:str, history=None):
    """Answer AI chat with Gemini, with a deterministic real-catalog search for product-family requests."""
    history=history or []
    interpreted_text=contextual_query(user_text,history)
    normalized = normalize_fa(interpreted_text)
    identity_phrases = ('کی هستی', 'تو کی هستی', 'خودت کی هستی', 'معرفی خودت', 'اسمت چیه', 'تو چی هستی')
    if any(normalize_fa(phrase) in normalized for phrase in identity_phrases):
        return '🤖 من دستیار هوش مصنوعی لیانا شاپ هستم؛ اینجام تا درباره محصولات آرایشی و بهداشتی، مراقبت پوست و مو و پیدا کردن محصولات مناسب راهنمایی‌ات کنم. 🌷'
    qn = normalize_fa(interpreted_text)
    # Store facts already configured by the shop owner should be answered from
    # the local, known information rather than guessed by the language model.
    store_fact_terms = (
        'آدرس فروشگاه','شماره تماس','شماره فروشگاه','ساعت کاری','شعبه','پارکینگ ملت',
        'پاساژ جام جم','ارسال دارید','ارسال کالا','روش ارسال','چطور سفارش','نحوه سفارش',
        'اطلاعات فروشگاه','درباره فروشگاه','معرفی فروشگاه','خرید حضوری','خرید اینترنتی'
    )
    if any(normalize_fa(term) in qn for term in store_fact_terms):
        return offline_answer(interpreted_text, history=history)

    # Questions about a brand/company or a product's background should use web
    # research instead of being mistaken for a request to list catalog products.
    research_terms = (
        'شرکت','برند','کمپانی','سازنده','تولید کننده','تولیدکننده','کشور سازنده',
        'ساخت کجاست','ساخت کدام کشور','اصالت','اصل یا فیک','تقلبی','ترکیبات',
        'مواد تشکیل دهنده','نظرات','بررسی','ریویو','ترند','جدیدترین','معروف ترین',
        'اطلاعات درباره','درباره برند','کیفیت برند','تاریخچه','وب سایت رسمی'
    )
    wants_web_research = any(normalize_fa(term) in qn for term in research_terms) or any(
        term in qn for term in ('لیانا شاپ','لیاناشاپ','liana shop','liyanashop')
    )
    if not wants_web_research:
        catalog_answer = catalog_request_reply(interpreted_text)
        if catalog_answer is not None:
            return catalog_answer
    beauty_brand_terms = ('ویولت','شیگلم','xixi','ایکس ایکس آی','ارت اسکین','آرت اسکین','جولیوم','کماس','کالیستا','مای','فلورمار','گلدن رز','نوت','لورال','مک','میبلین','اسنس','رولوشن','روماند','گابرینی','بیول','لافارر','سینره','درماتیپیک')
    brand_context = wants_web_research and any(normalize_fa(term) in qn for term in beauty_brand_terms)
    if not is_cosmetics_only_question(interpreted_text, history=history) and not brand_context:
        return '🌷 من در این بخش فقط درباره آرایش، محصولات آرایشی و بهداشتی و مراقبت پوست و مو راهنمایی می‌کنم. لطفاً سؤالت رو در همین زمینه بپرس 💄🧴'
    if not GEMINI_API_KEY:
        return '🌷 دستیار هوشمند فعلاً در دسترس نیست. لطفاً کمی بعد دوباره امتحان کن.'
    system_prompt=(
      'تو دستیار هوشمند و مشاور خوش‌برخورد لیانا شاپ در زمینه آرایش، محصولات آرایشی و بهداشتی و مراقبت پوست و مو هستی. '
      'همیشه فارسی روان، خوش‌خوان، کامل و کاربردی بنویس؛ لحن گرم و صمیمی داشته باش و گاهی متناسب با فضا از «عزیزم»، «جانم» یا «حتماً گلم» استفاده کن، اما در هر پیام زیاده‌روی نکن. '
      'از ۲ تا ۵ ایموجی مرتبط و زیبا در جای مناسب استفاده کن و متن را با تیتر یا فهرست کوتاه مرتب کن؛ ایموجی‌ها جای اطلاعات واقعی را نگیرند. '
      'فقط به موضوعات آرایش، زیبایی، مراقبت پوست و مو، برندهای آرایشی، محصولات آرایشی و اطلاعات فروشگاه لیانا شاپ پاسخ محتوایی بده. '
      'قانون بسیار مهم: هرگز نام محصول، برند، قیمت، موجودی، آدرس، شماره تماس، ترکیبات، رنگ، حجم، SPF یا ویژگی‌ای را که منبع معتبر تأیید نکرده جعل نکن. '
      'کاتالوگ واقعی لیانا مرجع قطعی محصول، قیمت و موجودی است؛ محصولی را به لیانا نسبت نده مگر واقعاً در کاتالوگ پیدا شده باشد. اگر تطابق پیدا نشد، صادقانه بگو و محصول نامرتبط را به‌جای آن پیشنهاد نده. '
      'برای اطلاعات درباره شرکت‌ها و برندها، کشور سازنده، ترکیبات، مشخصات و موضوعات روز آرایشی از نتایج جست‌وجوی وب استفاده کن؛ فقط نتیجه‌ای را بگو که با شواهد وب پشتیبانی می‌شود. اگر منبع معتبر پیدا نشد، شفاف بگو نتوانستی تأیید کنی. '
      'نتایج وب درباره موجود بودن در لیانا، قیمت روز یا موجودی فروشگاه مدرک محسوب نمی‌شوند؛ این موارد را فقط از کاتالوگ/اطلاعات قطعی فروشگاه بگو و در غیر این صورت کاربر را برای استعلام راهنمایی کن. '
      'وقتی چند محصول واقعی در کاتالوگ موجودند، درخواست کاربر مثل نوع پوست/مو، کاربرد، رنگ دلخواه، بودجه و ترجیحاتش را لحاظ کن؛ اگر برای انتخاب دقیق اطلاعات مهمی کم است یک سؤال کوتاه بپرس. '
      'اگر پاسخ بر جست‌وجوی وب متکی است، منابع را با عنوان و لینک قابل کلیک در انتهای پاسخ بیاور و تفاوت اطلاعات تأییدشده با توصیه عمومی را روشن کن. '
      'از ادعاهای پزشکی قطعی پرهیز کن و برای مشکلات پوستی شدید یا ماندگار مراجعه به متخصص را پیشنهاد بده. '
      'به سلام و احوالپرسی و تشکر طبیعی و صمیمی جواب بده. درباره موضوعات خارج از حوزه گفته‌شده پاسخ محتوایی نده و محترمانه موضوع مجاز را یادآوری کن.'
    )
    contents=[]
    for item in history[-10:]:
        role=item.get('role','user')
        if role == 'system':
            continue
        gemini_role='model' if role in ('assistant','model') else 'user'
        content=str(item.get('content','')).strip()
        if content:
            contents.append({'role':gemini_role,'parts':[{'text':content}]})
    contents.append({'role':'user','parts':[{'text':user_text}]})
    payload={
        'systemInstruction': {'parts':[{'text':system_prompt}]},
        'contents': contents,
        'generationConfig': {'temperature':0.45,'maxOutputTokens':1000}
    }
    # Gemini's Google Search grounding supplies fresh web evidence and source URLs.
    # Enable it for beauty/brand questions so answers can be checked by the customer.
    if wants_web_research or any(normalize_fa(term) in qn for term in (
        'محصول چیه','چی هست','چه کاربردی','برای چی خوبه','ترکیب','مواد موثر',
        'فرق بین','تفاوت','جدیدترین','ترند','معرفی برند','برندهای آرایشی'
    )):
        payload['tools'] = [{'google_search': {}}]
    url=f'https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent'
    # Retry transient network/API failures with exponential backoff. A longer timeout
    # gives Gemini room to finish a normal answer without hanging forever.
    timeout=aiohttp.ClientTimeout(total=60, connect=15, sock_read=50)
    retryable_statuses={408, 429, 500, 502, 503, 504}
    last_error='unknown error'
    for attempt in range(4):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    url,
                    headers={'x-goog-api-key':GEMINI_API_KEY,'Content-Type':'application/json'},
                    json=payload
                ) as response:
                    body=await response.json(content_type=None)
                    if response.status==200:
                        candidates=body.get('candidates') or []
                        if candidates:
                            parts=(candidates[0].get('content') or {}).get('parts') or []
                            answer=''.join(str(part.get('text','')) for part in parts if part.get('text')).strip()
                            if answer:
                                # Add real citations returned by Gemini grounding metadata.
                                metadata=(candidates[0].get('groundingMetadata') or candidates[0].get('grounding_metadata') or {})
                                chunks=metadata.get('groundingChunks') or metadata.get('grounding_chunks') or []
                                sources=[]
                                seen_urls=set()
                                for chunk in chunks:
                                    web_item=chunk.get('web') or {}
                                    uri=str(web_item.get('uri') or '').strip()
                                    title=str(web_item.get('title') or uri).strip()
                                    if uri.startswith(('https://','http://')) and uri not in seen_urls:
                                        seen_urls.add(uri)
                                        sources.append((title,uri))
                                    if len(sources)>=4:
                                        break
                                if sources:
                                    answer += '\n\n🔎 منابع برای بررسی بیشتر:\n' + '\n'.join(
                                        f'• {title[:120]} — {uri}' for title,uri in sources
                                    )
                                return answer
                        last_error='Gemini returned no text candidate'
                    else:
                        last_error=f'HTTP {response.status}: {str(body)[:500]}'
                        print(f'Gemini API error (attempt {attempt+1}/4): {last_error}')
                        if response.status not in retryable_statuses:
                            break
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            last_error=f'{type(exc).__name__}: {exc}'
            print(f'Gemini connection failed (attempt {attempt+1}/4): {last_error}')
        except Exception as exc:
            last_error=f'{type(exc).__name__}: {exc}'
            print(f'Unexpected Gemini error (attempt {attempt+1}/4): {last_error}')
            break
        if attempt < 3:
            await asyncio.sleep(min(2 ** attempt, 8))
    print(f'Gemini failed after retries: {last_error}')
    return '🌷 ارتباط با دستیار هوشمند موقتاً برقرار نشد. کمی بعد دوباره پیام بده؛ من اینجام که کمکت کنم.'

@dp.callback_query(F.data=='ai:open')
async def cb_ai_open(c:CallbackQuery):
    uid=c.from_user.id
    AI_USERS.add(uid); OWNER_PASSWORD_PENDING.discard(uid); history_clear(uid)
    await c.message.answer('🤖 <b>دستیار آرایشی و بهداشتی لیانا شاپ</b>\n\nاینجا فقط به سؤال‌های مربوط به آرایش، محصولات آرایشی و بهداشتی، مراقبت پوست و مو و قیمت یا موجودی همین محصولات پاسخ می‌دهم. 🌷\n\nسؤال‌های نامرتبط با این موضوع پاسخ داده نمی‌شوند. اسم محصول یا نیازت رو بنویس تا راهنمایی‌ات کنم و اگر لازم بود داخل محصولات لیانا جست‌وجو کنم.\n\nپیامت را بفرست؛ برای خروج دکمه زیر را بزن.',reply_markup=ai_kb())
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

@dp.callback_query(F.data=='owner:panel')
async def cb_owner_panel(c:CallbackQuery):
    if not owner_is_authenticated(c.from_user.id):
        await c.answer('ابتدا از گزینه «من صاحب ربات هستم» وارد شو.',show_alert=True); return
    OWNER_STATES.pop(c.from_user.id,None)
    await c.message.answer('👑 <b>پنل مدیریت لیانا</b>\nچه چیزی را می‌خواهی تغییر بدهی؟',reply_markup=owner_panel_kb())
    await c.answer()

@dp.callback_query(F.data=='owner:add')
async def cb_owner_add(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    OWNER_STATES[uid]={"step":"add_name","product":{}}
    await c.message.answer("➕ <b>افزودن محصول جدید</b>\n۱ از ۷: اسم کامل محصول را بفرست.")
    await c.answer()

@dp.callback_query(F.data=='owner:delete')
async def cb_owner_delete(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    OWNER_STATES[uid]={"step":"delete_search"}
    await c.message.answer("➖ اسم محصولی را که می‌خواهی حذف کنی بفرست. محصول از کاتالوگ، دسته‌بندی‌ها، جست‌وجو و پیشنهادهای هوش مصنوعی کنار گذاشته می‌شود.")
    await c.answer()

@dp.callback_query(F.data.startswith('owner:deletepick:'))
async def cb_owner_deletepick(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    pid=c.data.split(':',2)[2]
    product=find(pid)
    if not product:
        await c.answer('محصول پیدا نشد یا قبلاً حذف شده.',show_alert=True); return
    OWNER_STATES[uid]={"step":"delete_confirm","product_id":str(pid)}
    kb=InlineKeyboardMarkup(inline_keyboard=[
        [btn('🗑 بله، حذف شود',f'owner:deleteconfirm:{pid}')],
        [btn('لغو','owner:panel')]
    ])
    await c.message.answer('حذف این محصول تأیید می‌شود؟\n\n'+product_text(product),reply_markup=kb)
    await c.answer()

@dp.callback_query(F.data.startswith('owner:deleteconfirm:'))
async def cb_owner_deleteconfirm(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    pid=c.data.split(':',2)[2]
    product=find(pid)
    if not product:
        await c.answer('این محصول قبلاً حذف شده است.',show_alert=True); return
    cfg=owner_catalog()
    cfg["deleted_ids"]=list(dict.fromkeys([str(x) for x in cfg.get("deleted_ids",[])] + [str(pid)]))
    cfg["added"]=[p for p in cfg.get("added",[]) if str(p.get("id"))!=str(pid)]
    owner_json_write(OWNER_CATALOG_PATH,cfg)
    DATA[:]=[p for p in DATA if str(p.get("id"))!=str(pid)]
    # Remove recommendation rules that point to a deleted product.
    learned=owner_learning()
    learned["rules"]=[r for r in learned.get("rules",[]) if str(r.get("product_id"))!=str(pid)]
    owner_json_write(OWNER_LEARNING_PATH,learned)
    overrides=owner_json_read(OWNER_OVERRIDES_PATH,{})
    if isinstance(overrides,dict):
        overrides.pop(str(pid),None); owner_json_write(OWNER_OVERRIDES_PATH,overrides)
    OWNER_STATES.pop(uid,None)
    await c.message.answer('✅ محصول «'+esc(product.get('name',''))+'» حذف شد و دیگر در جست‌وجو و پیشنهادهای هوش مصنوعی نمایش داده نمی‌شود.',reply_markup=owner_panel_kb())
    await c.answer()

@dp.callback_query(F.data.startswith('owner:edit:'))
async def cb_owner_edit(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    field=c.data.rsplit(':',1)[-1]
    labels={'price':'قیمت','stock':'موجودی','description':'بیوی محصول'}
    OWNER_STATES[uid]={'step':'product_search','field':field}
    await c.message.answer(f'🔎 اسم محصولی که می‌خواهی {labels.get(field,"اطلاعاتش")} را تغییر بدهی بفرست. می‌توانی بخشی از نام را هم بنویسی.')
    await c.answer()

@dp.callback_query(F.data.startswith('owner:editfield:'))
async def cb_owner_editfield(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    parts=c.data.split(':',3)
    if len(parts)!=4:
        await c.answer('درخواست نامعتبر است.',show_alert=True); return
    _,_,pid,field=parts
    product=find(pid)
    if not product:
        await c.answer('محصول پیدا نشد.',show_alert=True); return
    OWNER_STATES[uid]={'step':'value','field':field,'product_id':str(pid)}
    prompts={
      'name':'نام جدید محصول را بفرست.',
      'category':'دسته‌بندی جدید را بفرست.',
      'price':'قیمت جدید را به تومان و با عدد بفرست.',
      'stock':'وضعیت موجودی جدید را بفرست.',
      'description':'توضیحات کامل جدید محصول را بفرست.',
      'image':'لینک مستقیم عکس (https://...) را بفرست؛ برای پاک‌کردن عکس «ندارد» بنویس.',
      'product_url':'لینک صفحه محصول را بفرست؛ برای پاک‌کردن لینک «ندارد» بنویس.'
    }
    await c.message.answer('🛠 ویرایش «'+esc(product.get('name',''))+'»\n'+prompts.get(field,'مقدار جدید را بفرست.'))
    await c.answer()

@dp.callback_query(F.data.startswith('owner:pick:'))
async def cb_owner_pick(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    parts=c.data.split(':',3)
    if len(parts)!=4:
        await c.answer('درخواست نامعتبر است.',show_alert=True); return
    _,_,field,pid=parts
    product=find(pid)
    if not product:
        await c.answer('محصول پیدا نشد.',show_alert=True); return
    if field=='all':
        kb=InlineKeyboardMarkup(inline_keyboard=[
          [btn('✏️ نام محصول','owner:editfield:'+str(pid)+':name'),btn('🗂 دسته‌بندی','owner:editfield:'+str(pid)+':category')],
          [btn('💰 قیمت','owner:editfield:'+str(pid)+':price'),btn('📦 موجودی','owner:editfield:'+str(pid)+':stock')],
          [btn('📝 توضیحات','owner:editfield:'+str(pid)+':description')],
          [btn('🖼 لینک عکس','owner:editfield:'+str(pid)+':image'),btn('🔗 لینک محصول','owner:editfield:'+str(pid)+':product_url')],
          [btn('↩️ پنل مدیریت','owner:panel')]
        ])
        await c.message.answer('🛠 کدام اطلاعات «'+esc(product.get('name',''))+'» را می‌خواهی ویرایش کنی؟',reply_markup=kb)
        await c.answer()
        return
    OWNER_STATES[uid]={'step':'value','field':field,'product_id':str(pid)}
    prompts={
      'price':f'💰 قیمت جدید برای «{product.get("name","")}» را فقط به عدد تومان بفرست. این قیمت روی همه تنوع‌های این محصول اعمال می‌شود.',
      'stock':f'📦 وضعیت جدید موجودی «{product.get("name","")}» را بفرست؛ مثلاً «موجود»، «ناموجود» یا «برای استعلام تماس بگیرید».',
      'description':f'📝 بیوی جدید محصول «{product.get("name","")}» را کامل بفرست. همین متن جایگزین بیوی قبلی می‌شود.'}
    await c.message.answer(prompts.get(field,'مقدار جدید را بفرست.'))
    await c.answer()

@dp.callback_query(F.data=='owner:teach:faq')
async def cb_owner_teach_faq(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    OWNER_STATES[uid]={'step':'faq_keywords'}
    await c.message.answer('🧠 <b>آموزش پاسخ جدید</b>\nکلمه‌ها یا جمله‌هایی را که مشتری ممکن است بگوید بفرست؛ چند مورد را با علامت | جدا کن.\nمثال: ارسال دارید؟ | هزینه ارسال چقدره | ارسال به شهرستان')
    await c.answer()

@dp.callback_query(F.data=='owner:teach:product')
async def cb_owner_teach_product(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    OWNER_STATES[uid]={'step':'rule_keywords'}
    await c.message.answer('🛍 <b>آموزش پیشنهاد محصول</b>\nعبارت‌ها یا کلمه‌های کلیدی مشتری را با | جدا کن.\nمثال: هدیه دخترونه | چیز فانتزی | ناخن کاپی بارا')
    await c.answer()

@dp.callback_query(F.data=='owner:testai')
async def cb_owner_testai(c:CallbackQuery):
    if not owner_is_authenticated(c.from_user.id):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    AI_USERS.add(c.from_user.id)
    await c.message.answer('🤖 حالت آزمایش فعال شد. مثل مشتری سؤال بپرس؛ برای برگشت به پنل، /owner را بفرست.')
    await c.answer()

@dp.callback_query(F.data.startswith('owner:rulepick:'))
async def cb_owner_rulepick(c:CallbackQuery):
    uid=c.from_user.id
    if not owner_is_authenticated(uid):
        await c.answer('دسترسی مدیریت ندارید.',show_alert=True); return
    parts=c.data.split(':',3)
    if len(parts)!=4:
        await c.answer('درخواست نامعتبر است.',show_alert=True); return
    keyhash=parts[2]; pid=parts[3]
    state=OWNER_STATES.get(uid,{})
    keys=state.get('keywords',[])
    product=find(pid)
    if not product or not keys:
        await c.answer('اطلاعات آموزش پیدا نشد؛ دوباره شروع کن.',show_alert=True); return
    learned=owner_learning()
    learned['rules'].append({'keywords':keys,'product_id':str(pid),'intro':'حتماً 🌷 با توجه به چیزی که گفتی، این محصول رو از لیانا پیشنهاد می‌کنم:'})
    owner_json_write(OWNER_LEARNING_PATH,learned)
    OWNER_STATES.pop(uid,None)
    await c.message.answer('✅ قانون پیشنهاد محصول ذخیره شد. هر وقت مشتری یکی از این عبارت‌ها را بنویسد، این محصول در پاسخ پیشنهاد می‌شود.\n\nکلمات کلیدی: '+', '.join(keys)+'\nمحصول: '+str(product.get('name','')),reply_markup=owner_panel_kb())
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
            OWNER_AI_USERS.add(uid); AI_USERS.discard(uid); OWNER_STATES.pop(uid,None)
            await m.answer('✅ ورود مدیریت موفق بود. از این پنل می‌توانی قیمت، موجودی، بیوی محصولات و آموزش پاسخ‌های هوش مصنوعی را تغییر بدهی.',reply_markup=owner_panel_kb())
        else:
            OWNER_AI_USERS.discard(uid)
            await m.answer('❌ رمز یا دسترسی صاحب ربات معتبر نیست.',reply_markup=home_kb())
        return
    if uid in OWNER_STATES and owner_is_authenticated(uid) and not q.startswith('/'):
        state=OWNER_STATES[uid]
        step=state.get('step')
        if step=='delete_search':
            matches=search_products(q,limit=12)
            if not matches:
                await m.answer('محصولی پیدا نکردم؛ اسم یا بخشی از نامش را دوباره بفرست.')
                return
            rows=[[btn('🧴 '+str(item.get('name','محصول'))[:45],f"owner:deletepick:{item.get('id')}")] for item in matches[:12]]
            rows.append([btn('↩️ پنل مدیریت','owner:panel')])
            await m.answer('کدام محصول را حذف کنم؟ برای جلوگیری از حذف اشتباه، در مرحله بعد تأیید می‌گیریم.',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        if step and step.startswith('add_'):
            prod=state.setdefault('product',{})
            if step=='add_name':
                if len(q)<2 or len(q)>180:
                    await m.answer('اسم محصول را بین ۲ تا ۱۸۰ نویسه بفرست.'); return
                if any(normalize_fa(p.get('name',''))==normalize_fa(q) for p in DATA):
                    await m.answer('محصولی با این نام از قبل وجود دارد. نام دقیق‌تر یا محصول دیگری بفرست.'); return
                prod['name']=q; state['step']='add_category'
                await m.answer('۲ از ۷: دسته‌بندی محصول را بفرست؛ مثلاً آرایش چشم و ابرو یا مراقبت پوست.')
                return
            if step=='add_category':
                prod['category']=q[:100]; state['step']='add_price'
                await m.answer('۳ از ۷: قیمت را به تومان و فقط با عدد بفرست. اگر قیمت هنوز مشخص نیست، بنویس «نامشخص».')
                return
            if step=='add_price':
                digits=re.sub(r'[^0-9۰-۹٠-٩]','',q).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789'))
                if not digits and normalize_fa(q) not in ('نامشخص','بعدا','بعدا مشخص می شود'):
                    await m.answer('قیمت را عددی به تومان بفرست یا بنویس «نامشخص».'); return
                prod['price_raw']=str(int(digits)) if digits else ''
                state['step']='add_stock'
                await m.answer('۴ از ۷: وضعیت موجودی را بفرست؛ مثلاً موجود، ناموجود یا برای استعلام.')
                return
            if step=='add_stock':
                prod['stock_file']=q[:120]; state['step']='add_package'
                await m.answer('۵ از ۷: نوع بسته‌بندی/تعداد را بفرست؛ مثلاً ۱۲ عددی، ۲۴ عددی یا «ندارد».')
                return
            if step=='add_package':
                prod['package']='' if normalize_fa(q) in ('ندارد','خیر','نه') else q[:180]
                state['step']='add_description'
                await m.answer('۶ از ۷: توضیحات محصول را بفرست. اگر توضیح نداری بنویس «خودکار» تا معرفی عمومی و محتاطانه ساخته شود.')
                return
            if step=='add_description':
                if normalize_fa(q) in ('خودکار','ندارم','ندارد'):
                    prod['description']=''
                else: prod['description']=q[:3000]
                state['step']='add_url'
                await m.answer('۷ از ۸: لینک صفحه محصول را بفرست؛ اگر فعلاً لینک نداری بنویس «ندارد».')
                return
            if step=='add_url':
                url='' if normalize_fa(q) in ('ندارد','ندارم','خیر','نه') else q.strip()
                if url and not url.startswith(('https://','http://')):
                    await m.answer('لینک باید با https:// یا http:// شروع شود؛ یا بنویس «ندارد».'); return
                prod['product_url']=url or (SHOP_URL.rstrip('/')+'/?s='+quote(prod['name'])+'&post_type=product')
                state['step']='add_image'
                await m.answer('۸ از ۸: لینک مستقیم عکس محصول را بفرست (ترجیحاً JPG یا PNG و با https://). اگر عکس نداری بنویس «ندارد».')
                return
            if step=='add_image':
                image_url='' if normalize_fa(q) in ('ندارد','ندارم','خیر','نه') else q.strip()
                if image_url and not image_url.startswith(('https://','http://')):
                    await m.answer('لینک عکس باید با https:// یا http:// شروع شود؛ یا بنویس «ندارد».'); return
                pid='owner_'+hashlib.sha256((prod['name']+str(time.time_ns())).encode()).hexdigest()[:14]
                category=prod.get('category') or _guess_category(prod['name'])
                desc=prod.get('description') or describe_product({'name':prod['name'],'category':category,'variants':[]})
                variants=[]
                if prod.get('price_raw') or prod.get('package'):
                    variants=[{'id':pid,'name':prod['name'],'attribute':prod.get('package',''),'price_raw':prod.get('price_raw',''),'stock_file':prod.get('stock_file','نامشخص')}]
                newp={'id':pid,'sku':pid,'name':prod['name'],'category':category,'description':desc,'image':image_url,'published':True,'stock_file':prod.get('stock_file','نامشخص'),'variants':variants,'product_url':prod['product_url'],'owner_added':True}
                persist_owner_product(newp)
                OWNER_STATES.pop(uid,None)
                await m.answer('✅ محصول با موفقیت اضافه شد!\nاز همین حالا در فهرست محصولات، دسته‌بندی، جست‌وجو و زمینه پیشنهادهای هوش مصنوعی قابل استفاده است. هوش مصنوعی بر اساس نام، دسته، بسته‌بندی و توضیحات آن را پیدا می‌کند؛ برای اطلاعات دقیق‌تر، توضیحات واقعی محصول را وارد کن.\n\n'+product_text(newp),reply_markup=owner_panel_kb())
                return
        if step=='product_search':
            field=state.get('field','price')
            matches=search_products(q,limit=12)
            if not matches:
                await m.answer('محصولی با این عبارت پیدا نکردم. نام کوتاه‌تر یا بخشی از نام محصول را بفرست.')
                return
            rows=[]
            for item in matches[:12]:
                rows.append([btn('🧴 '+str(item.get('name','محصول'))[:45],f"owner:pick:{field}:{item.get('id')}")])
            rows.append([btn('↩️ پنل مدیریت','owner:panel')])
            await m.answer('کدام محصول را می‌خواهی ویرایش کنی؟',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        if step=='value':
            pid=str(state.get('product_id','')); field=state.get('field','')
            product=find(pid)
            if not product:
                OWNER_STATES.pop(uid,None); await m.answer('محصول پیدا نشد؛ دوباره از پنل شروع کن.',reply_markup=owner_panel_kb()); return
            overrides=owner_json_read(OWNER_OVERRIDES_PATH,{})
            if not isinstance(overrides,dict): overrides={}
            ov=overrides.setdefault(pid,{})
            if field=='price':
                digits=re.sub(r'[^0-9۰-۹٠-٩]','',q).translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789'))
                if not digits:
                    await m.answer('قیمت را به عدد تومان بفرست؛ مثلاً 125000.'); return
                ov['price_raw']=str(int(digits))
                reply='قیمت جدید ذخیره شد و روی همه تنوع‌های این محصول اعمال شد. 💰'
            elif field=='stock':
                if len(q)>120:
                    await m.answer('لطفاً وضعیت موجودی را کوتاه بنویس؛ مثلاً موجود یا ناموجود.'); return
                ov['stock_file']=q
                reply='وضعیت موجودی ذخیره شد. 📦'
            elif field=='description':
                if len(q)<5:
                    await m.answer('بیو کمی کوتاه است؛ متن کامل‌تری بفرست یا حداقل ۵ نویسه وارد کن.'); return
                ov['description']=q[:3000]
                reply='توضیحات جدید ذخیره شد. 📝'
            elif field in ('name','category'):
                if len(q)<2 or len(q)>180:
                    await m.answer('مقدار را بین ۲ تا ۱۸۰ نویسه بفرست.'); return
                ov[field]=q[:180]
                reply=('نام محصول' if field=='name' else 'دسته‌بندی')+' به‌روزرسانی شد.'
            elif field in ('image','product_url'):
                val='' if normalize_fa(q) in ('ندارد','ندارم','خیر','نه','پاک کن') else q.strip()
                if val and not val.startswith(('https://','http://')):
                    await m.answer('لینک باید با https:// یا http:// شروع شود؛ یا برای پاک‌کردن بنویس «ندارد».'); return
                ov[field]=val
                reply=('عکس محصول' if field=='image' else 'لینک صفحه محصول')+' به‌روزرسانی شد.'
            else:
                await m.answer('نوع ویرایش مشخص نیست.'); return
            owner_json_write(OWNER_OVERRIDES_PATH,overrides)
            apply_owner_overrides()
            OWNER_STATES.pop(uid,None)
            await m.answer('✅ '+reply+'\n\n'+product_text(product),reply_markup=owner_panel_kb())
            return
        if step=='faq_keywords':
            keys=[x.strip() for x in q.split('|') if x.strip()]
            if not keys:
                await m.answer('حداقل یک کلمه یا عبارت بفرست.'); return
            OWNER_STATES[uid]={'step':'faq_answer','keywords':keys}
            await m.answer('✍️ حالا پاسخ دلخواه ربات را بفرست. این پاسخ برای سؤال‌های مشابه استفاده می‌شود.')
            return
        if step=='faq_answer':
            if len(q)<2:
                await m.answer('پاسخ خیلی کوتاه است؛ پاسخ کامل‌تری بفرست.'); return
            learned=owner_learning(); learned['faqs'].append({'keywords':state.get('keywords',[]),'answer':q[:3000]})
            owner_json_write(OWNER_LEARNING_PATH,learned); OWNER_STATES.pop(uid,None)
            await m.answer('✅ آموزش پاسخ ذخیره شد. از این به بعد برای عبارت‌های مشابه، این جواب در اولویت است.\nبرای آموزش بعدی از پنل مدیریت استفاده کن.',reply_markup=owner_panel_kb())
            return
        if step=='rule_keywords':
            keys=[x.strip() for x in q.split('|') if x.strip()]
            if not keys:
                await m.answer('حداقل یک کلمه یا عبارت بفرست.'); return
            OWNER_STATES[uid]={'step':'rule_product_search','keywords':keys}
            await m.answer('🔎 حالا اسم محصولی را بفرست که می‌خواهی با این عبارت‌ها پیشنهاد شود.')
            return
        if step=='rule_product_search':
            matches=search_products(q,limit=12)
            if not matches:
                await m.answer('محصولی پیدا نشد؛ اسم محصول را کوتاه‌تر یا دقیق‌تر بفرست.'); return
            keys=state.get('keywords',[])
            rows=[[btn('🧴 '+str(item.get('name','محصول'))[:45],f"owner:rulepick:{hashlib.md5('|'.join(keys).encode()).hexdigest()[:6]}:{item.get('id')}")] for item in matches[:12]]
            OWNER_STATES[uid]={'step':'rule_choose','keywords':keys,'choices':{hashlib.md5('|'.join(keys).encode()).hexdigest()[:6]:keys}}
            # Keep the exact keyword list in state; callback only needs the product id.
            await m.answer('کدام محصول را در پاسخ به این عبارت‌ها پیشنهاد بدهم؟',reply_markup=InlineKeyboardMarkup(inline_keyboard=rows+[[btn('↩️ پنل مدیریت','owner:panel')]]))
            return
        if step=='rule_choose':
            await m.answer('از دکمه‌های محصولی که نمایش داده شده انتخاب کن.')
            return
    identity_query = normalize_fa(q)
    if any(normalize_fa(phrase) in identity_query for phrase in ('کی هستی','تو کی هستی','خودت کی هستی','معرفی خودت','اسمت چیه','تو چی هستی')):
        await m.answer('🤖 من دستیار هوش مصنوعی لیانا شاپ هستم؛ اینجام تا درباره محصولات آرایشی و بهداشتی، مراقبت پوست و مو و پیدا کردن محصولات مناسب راهنمایی‌ات کنم. 🌷')
        return
    if uid in AI_USERS and not q.startswith('/'):
        if len(q)<2: return
        # AI chat intentionally does not load or query local CSV/database data.
        history=history_get(uid)
        interpreted=contextual_query(q,history)
        # Let greetings, casual conversation, store questions and recommendation requests reach intent handling.
        answer=await call_gemini(q,history=history)
        history_add(uid,'user',q)
        history_add(uid,'assistant',answer)
        # Use the pre-turn history when deriving product buttons so a short answer
        # such as «گندمی» stays linked to the original tint/concealer question.
        # When a requested product family has no trustworthy catalog match, do not
        # attach fuzzy buttons that could contradict the "not found" message.
        if 'فعلاً محصولی با تطابق کافی' in answer:
            reply_markup = ai_kb()
        else:
            reply_markup = conversational_keyboard(q,history,answer)
        await m.answer(esc(answer),reply_markup=reply_markup)
        return
    if q.startswith('/') or len(q)<2: return
    # Outside AI mode, keep behavior deterministic: search the real product catalog only.
    # AI is activated exclusively by the explicit "گفت‌وگو با هوش مصنوعی" button.
    matches = search_products(q, limit=200)
    if matches:
        # شناسه کوتاه در callback_data استفاده می‌شود تا عبارت فارسی باعث عبور از محدودیت تلگرام نشود.
        search_key = hashlib.md5(q.encode("utf-8")).hexdigest()[:8]
        SEARCH_PAGE_QUERIES[search_key] = q
        await m.answer(
            f"🔎 <b>نتایج جست‌وجو در محصولات لیانا</b>\n{len(matches)} محصول پیدا شد.\n"
            "برای دیدن محصولات بیشتر از دکمه «بعدی» استفاده کن. محصول رو انتخاب کن تا جزئیات و راه استعلام قیمت رو ببینی 🌷",
            reply_markup=paginated(matches, 0, 'searchpage:' + search_key)
        )
        return
    await m.answer(
        "🌷 محصولی با این عبارت پیدا نکردم. اسم محصول رو کوتاه‌تر یا با املای دیگه بنویس؛ "
        "مثلاً «ریمل»، «بادی اسپلش» یا «مداد ابرو».\n\n"
        "برای پرسش‌های عمومی و پیشنهاد هوشمند، اول از منوی خانه دکمه «گفت‌وگو با هوش مصنوعی» رو بزن.",
        reply_markup=home_kb()
    )
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
    _,page,search_key=c.data.split(':',2); page=int(page)
    query=SEARCH_PAGE_QUERIES.get(search_key)
    if query is None:
        query=__import__('urllib.parse').parse.unquote(search_key)
    items=search_products(query, limit=200)
    if not items:
        await c.answer('نتایج این جست‌وجو دیگر در دسترس نیست؛ دوباره جست‌وجو کن.', show_alert=True); return
    await c.message.edit_text(
        f"🔎 نتایج جست‌وجو برای «{esc(query)}»\nصفحه {page+1} از {(len(items)+PAGE_SIZE-1)//PAGE_SIZE} — {len(items)} محصول",
        reply_markup=paginated(items,page,'searchpage:'+search_key)
    ); await c.answer()
@dp.callback_query(F.data.startswith('qpage:'))
async def cb_qpage(c:CallbackQuery):
    _,page,key=c.data.split(':',2); page=int(page); items=matching_question(key)
    await c.message.edit_text("<b>💡 پیشنهادهای دسته‌بندی‌شده</b>\nاین فهرست از دسته‌بندی نام محصولات ساخته شده؛ برای سازگاری با نوع پوست، مشخصات سازنده را بررسی کن.",reply_markup=paginated(items,page,'qpage:'+key)); await c.answer()
@dp.callback_query(F.data.startswith('aipage:'))
async def cb_aipage(c:CallbackQuery):
    try:
        _, page_text, page_key = c.data.split(':', 2)
        page = max(0, int(page_text))
        ids = AI_PAGE_PRODUCTS.get(page_key, [])
    except (ValueError, TypeError):
        await c.answer('فهرست پیشنهادها در دسترس نیست؛ سؤال را دوباره بپرس.', show_alert=True)
        return
    items = [p for pid in ids if (p := find(pid)) and p.get('published', True)]
    if not items:
        await c.answer('فهرست پیشنهادها در دسترس نیست؛ سؤال را دوباره بپرس.', show_alert=True)
        return
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, total_pages - 1)
    await c.message.answer(
        f"<b>🌷 همه پیشنهادهای مرتبط</b>\\nصفحه {page+1} از {total_pages} — {len(items)} محصول",
        reply_markup=paginated(items, page, 'aipage:'+page_key)
    )
    await c.answer()

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
    rows=[[btn('📞 شماره شعبه ۱','phone:1'),btn('📞 شماره شعبه ۲','phone:2')]]
    product_url=str(p.get('product_url') or '').strip()
    if product_url.startswith(('https://','http://')):
        rows.append([btn('🔗 صفحه محصول',url=product_url)])
    rows.append(nav_row())
    kb=InlineKeyboardMarkup(inline_keyboard=rows)
    await c.message.answer(text,reply_markup=kb); await c.answer()

async def main():
    apply_owner_catalog()
    apply_owner_overrides()
    init_db()
    if not isinstance(DATA, list):
        raise SystemExit('products.json باید فهرستی از محصولات باشد.')
    if not TOKEN: raise SystemExit('BOT_TOKEN را در فایل .env وارد کن؛ از BotFather دریافت می‌شود.')
    apply_owner_overrides()
    local_count=load_local_csv()
    apply_owner_overrides()
    print(f"[CSV] فایل محلی بررسی شد؛ {local_count} محصول CSV، کل کاتالوگ فعلی: {len(DATA)}")
    await refresh_csv_feed()
    asyncio.create_task(csv_refresh_loop())
    # Telegram polling is managed separately from Gemini requests. If Telegram's
    # network connection drops temporarily, reconnect with a bounded backoff.
    retry_delay = 2
    while True:
        try:
            await dp.start_polling(bot, close_bot_session=False)
            retry_delay = 2
        except (TelegramNetworkError, TelegramServerError, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            print(f'Telegram connection interrupted: {type(exc).__name__}: {exc}. Retry in {retry_delay}s.')
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
        except Exception as exc:
            # Log unexpected polling errors and retry rather than silently terminating the bot.
            print(f'Unexpected Telegram polling error: {type(exc).__name__}: {exc}. Retry in {retry_delay}s.')
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
if __name__=='__main__': asyncio.run(main())
