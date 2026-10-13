import asyncio, json, os, re
from pathlib import Path
from urllib.parse import quote
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

load_dotenv()
TOKEN=os.getenv("BOT_TOKEN", "").strip()
PHONE_1=os.getenv("PHONE_1", "09369952103").strip()
PHONE_2=os.getenv("PHONE_2", "09904358418").strip()
SHOP_URL=os.getenv("SHOP_URL", "https://liyanashop.ir").strip()
TG_URL=os.getenv("TELEGRAM_URL", "https://t.me/liyanashop_ir").strip()
RUBIKA_URL=os.getenv("RUBIKA_URL", "https://rubika.ir/liyanashop_gnv").strip()
DATA=json.loads(Path(__file__).with_name("products.json").read_text(encoding="utf-8"))
PAGE_SIZE=8
bot=Bot(TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp=Dispatcher()

HOME_TEXT=("<b>✦ LIYANA BEAUTY ✦</b>\n"
"<i>فروشگاه آرایشی و بهداشتی لیانا</i>\n\n"
"سلام خوش اومدی 🌷\nاز منوی زیر می‌تونی محصولات رو ببینی، محصول مناسب رو پیدا کنی یا از فروشگاه درباره قیمت و موجودی بپرسی.")

def btn(text, data=None, url=None):
    if url: return InlineKeyboardButton(text=text,url=url)
    return InlineKeyboardButton(text=text,callback_data=data)

def nav_row(): return [btn("↩️ بازگشت", "back"),btn("🏠 خانه", "home")]
def home_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
      [btn("🛍 مشاهده محصولات", "catalog:0"),btn("🔎 موجودی و قیمت", "stocksearch")],
      [btn("💡 از من سؤال بپرس", "questions"),btn("🏬 اطلاعات فروشگاه", "shop")],
      [btn("📣 کانال تلگرام",url=TG_URL),btn("🛒 سایت فروشگاه",url=SHOP_URL)]
    ])
def cats(): return sorted(set(p['category'] for p in DATA if p.get('published',True)))
def category_code(category):
    return str(cats().index(category))
def category_from_code(code):
    try: return cats()[int(code)]
    except (ValueError, IndexError): return None
def categories_kb():
    rows=[]; c=cats()
    for i in range(0,len(c),2):
        rows.append([btn("✨ "+c[i],f"cat:{category_code(c[i])}:0")]+([btn("✨ "+c[i+1],f"cat:{category_code(c[i+1])}:0")] if i+1<len(c) else []))
    rows.append([btn("📚 همه محصولات", "catalog:0")]); rows.append(nav_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)
def product_keyboard(p):
    rows=[[btn("💰 استعلام قیمت",f"ask:price:{p['id']}"),btn("📦 استعلام موجودی",f"ask:stock:{p['id']}")],
          [btn("🔗 صفحه محصول",url=p['product_url'])],nav_row()]
    return InlineKeyboardMarkup(inline_keyboard=rows)
def product_text(p):
    desc=p.get('description','').strip()
    if not desc: desc='برای دریافت مشخصات دقیق‌تر، صفحه محصول یا از فروشگاه استعلام بگیر.'
    variants=p.get('variants',[])
    opt=''
    if variants:
      labels=[]
      for v in variants[:8]:
        a=v.get('attribute') or v.get('name','')
        if a and a not in labels: labels.append(a)
      if labels: opt='\n\n<b>تنوع‌ها:</b> '+ '، '.join(labels)
    prices=variant_lines(p)
    price_text=("\n\n<b>قیمت‌های ثبت‌شده:</b>\n"+"\n".join(prices)) if prices else ""
    return f"<b>✨ {esc(p['name'])}</b>\n\n{esc(desc[:700])}{opt}{price_text}\n\n<i>برای تأیید قیمت نهایی و موجودی لحظه‌ای از دکمه‌های استعلام استفاده کن.</i>"
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
async def start(m:Message): await m.answer(HOME_TEXT,reply_markup=home_kb())
@dp.message(Command("home"))
async def home(m:Message): await m.answer(HOME_TEXT,reply_markup=home_kb())
@dp.message(F.text)
async def text_search(m:Message):
    q=(m.text or '').strip()
    if q.startswith('/') or len(q)<2: return
    found=[p for p in DATA if q.casefold() in p['name'].casefold() and p.get('published',True)][:40]
    if not found:
      await m.answer("محصولی با این نام پیدا نکردم. می‌تونی از دسته‌بندی‌ها انتخاب کنی یا نام دیگری بنویسی.",reply_markup=categories_kb()); return
    await m.answer(f"🔎 <b>{len(found)}</b> نتیجه برای «{esc(q)}» (حداکثر ۴۰ نتیجه):",reply_markup=paginated(found,0,'searchpage:'+quote(q,safe='')))
@dp.callback_query(F.data=='home')
async def cb_home(c:CallbackQuery): await c.message.edit_text(HOME_TEXT,reply_markup=home_kb()); await c.answer()
@dp.callback_query(F.data=='back')
async def cb_back(c:CallbackQuery): await c.message.edit_text("از منوی زیر ادامه بده 🌷",reply_markup=home_kb()); await c.answer()
@dp.callback_query(F.data=='categories')
async def cb_cats(c:CallbackQuery): await c.message.edit_text("<b>🗂 دسته‌بندی محصولات</b>\nکدوم دسته رو می‌خوای ببینی؟",reply_markup=categories_kb()); await c.answer()
@dp.callback_query(F.data.startswith('catalog:'))
async def cb_catalog(c:CallbackQuery):
    page=int(c.data.split(':')[1]); items=[p for p in DATA if p.get('published',True)]
    await c.message.edit_text(f"<b>🛍 محصولات لیانا</b>\nصفحه {page+1} از {(len(items)+PAGE_SIZE-1)//PAGE_SIZE}\nبرای مشاهده جزئیات، محصول رو انتخاب کن.",reply_markup=paginated(items,page,'catalog')); await c.answer()
@dp.callback_query(F.data.startswith('cat:'))
async def cb_cat(c:CallbackQuery):
    _,code,page=c.data.split(':',2); page=int(page); cat=category_from_code(code)
    if cat is None: await c.answer('دسته‌بندی پیدا نشد',show_alert=True); return
    items=[p for p in DATA if p['category']==cat and p.get('published',True)]
    await c.message.edit_text(f"<b>✨ {esc(cat)}</b>\n{len(items)} محصول در این دسته",reply_markup=paginated(items,page,'catpage:'+code)); await c.answer()
@dp.callback_query(F.data.startswith('catpage:'))
async def cb_catpage(c:CallbackQuery):
    _,page,code=c.data.split(':',2); page=int(page); cat=category_from_code(code)
    if cat is None: await c.answer('دسته‌بندی پیدا نشد',show_alert=True); return
    items=[p for p in DATA if p['category']==cat and p.get('published',True)]
    await c.message.edit_text(f"<b>✨ {esc(cat)}</b>\n{len(items)} محصول در این دسته",reply_markup=paginated(items,page,'catpage:'+code)); await c.answer()
@dp.callback_query(F.data.startswith('searchpage:'))
async def cb_searchpage(c:CallbackQuery):
    _,page,query=c.data.split(':',2); page=int(page); query=__import__('urllib.parse').parse.unquote(query)
    items=[p for p in DATA if query.casefold() in p['name'].casefold() and p.get('published',True)][:40]
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
def money_toman(raw):
    try:
        n=int(str(raw).replace(",","").strip())
        return f"{n:,}".replace(",", "٬") + " تومان"
    except (TypeError, ValueError):
        return None

def variant_lines(p):
    out=[]
    for v in p.get("variants", []):
        attr=(v.get("attribute") or v.get("name") or "").strip()
        price=money_toman(v.get("price_raw"))
        stock=(v.get("stock_file") or p.get("stock_file") or "نامشخص").strip()
        bits=[]
        if attr: bits.append(esc(attr))
        if price: bits.append("<b>"+esc(price)+"</b>")
        bits.append("موجودی فایل: "+esc(stock))
        out.append("• "+" — ".join(bits))
    return out

@dp.callback_query(F.data.startswith('phone:'))
async def cb_phone(c:CallbackQuery):
    number=PHONE_1 if c.data.endswith(':1') else PHONE_2
    await c.answer()
    await c.message.answer("📞 شماره تماس فروشگاه:\\n<code>"+esc(number)+"</code>\\nشماره را لمس و کپی کن.")

@dp.callback_query(F.data.startswith('ask:'))
async def cb_ask(c:CallbackQuery):
    _,kind,pid=c.data.split(':',2); p=find(pid)
    if not p: await c.answer('محصول پیدا نشد',show_alert=True); return
    lines=variant_lines(p)
    if kind == 'price':
        if lines:
            text=(f"<b>💰 قیمت ثبت‌شده در فایل</b>\n\nمحصول: <b>{esc(p['name'])}</b>\n\n"
                  + "\n".join(lines)
                  + "\n\n<i>قیمت‌ها از فایل داده خوانده شده‌اند؛ پیش از خرید قیمت نهایی را با فروشگاه تأیید کنید.</i>")
        else:
            text=(f"<b>💰 استعلام قیمت</b>\n\nمحصول: <b>{esc(p['name'])}</b>\n"
                  "برای این محصول قیمت قابل‌خواندن در فایل ثبت نشده است؛ لطفاً از فروشگاه استعلام بگیر.")
    else:
        status=p.get('stock_file') or 'نامشخص'
        text=(f"<b>📦 وضعیت موجودی ثبت‌شده</b>\n\nمحصول: <b>{esc(p['name'])}</b>\n"
              f"وضعیت کلی فایل: <b>{esc(status)}</b>")
        if lines: text += "\n\n" + "\n".join(lines)
        text += "\n\n<i>این وضعیت مربوط به فایل داده است و ممکن است با موجودی لحظه‌ای فروشگاه فرق داشته باشد.</i>"
    kb=InlineKeyboardMarkup(inline_keyboard=[[btn('📞 شماره ۱','phone:1'),btn('📞 شماره ۲','phone:2')],[btn('🔗 صفحه محصول',url=p['product_url'])],nav_row()])
    await c.message.answer(text,reply_markup=kb); await c.answer()

async def main():
    if not TOKEN: raise SystemExit('BOT_TOKEN را در فایل .env وارد کن؛ از BotFather دریافت می‌شود.')
    await dp.start_polling(bot)
if __name__=='__main__': asyncio.run(main())
