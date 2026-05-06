import os
import re
import tempfile
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, CallbackContext
)
import pandas as pd
try:
    import pytesseract
except ImportError:
    pytesseract = None
from PIL import Imagefrom rapidfuzz import process

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = '8132772993:AAEzemQlQ5RGFq-cQkKFQ9o0xeNWl5e_1S4'

our_price_df = None
user_photos = {}

# ========== ФУНКЦИИ ==========
def ocr_image(image_path):
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='rus+eng')
        return text
    except Exception as e:
        return ""

def extract_price_weight_from_text(text):
    if not text:
        return None, None, None
    
    text = text.lower()
    
    price_match = re.search(r'(\d+[\.,]?\d*)\s*[рруб₽]', text)
    if not price_match:
        price_match = re.search(r'(\d+[\.,]?\d*)\s*$', text)
    if not price_match:
        return None, None, None
    
    price = float(price_match.group(1).replace(',', '.'))
    
    weight_match = re.search(r'(\d+[\.,]?\d*)\s*(гр?|грамм|g|кг|kg)', text)
    weight_kg = None
    if weight_match:
        value = float(weight_match.group(1).replace(',', '.'))
        unit = weight_match.group(2).lower()
        if unit in ('г', 'гр', 'грамм', 'g'):
            weight_kg = value / 1000
        elif unit in ('кг', 'kg'):
            weight_kg = value
    
    name_match = re.search(r'^([а-яёa-z\s\-]+)', text)
    name = name_match.group(1).strip() if name_match else "неизвестно"
    name = re.sub(r'[^\w\s\-]', '', name)[:50]
    
    return name, price, weight_kg

def find_our_product(product_name):
    if our_price_df is None or our_price_df.empty:
        return None
    
    matches = process.extract(
        product_name.lower(),
        our_price_df['наименование_нижн'].tolist(),
        limit=1,
        scorer=process.fuzz.ratio
    )
    
    if matches and matches[0][1] >= 70:
        idx = matches[0][2]
        return our_price_df.iloc[idx]
    return None

def process_photo(image_path):
    try:
        text = ocr_image(image_path)
        
        if not text.strip():
            return {'status': 'error', 'message': 'Не удалось распознать текст'}
        
        name, price, weight_kg = extract_price_weight_from_text(text)
        
        if not price:
            return {'status': 'error', 'message': 'Не найдена цена'}
        
        if not weight_kg:
            return {'status': 'error', 'message': 'Не найден вес'}
        
        our_product = find_our_product(name)
        if our_product is None:
            return {'status': 'error', 'message': f'Товар "{name}" не найден'}
        
        competitor_price_kg = price / weight_kg
        our_price_kg = float(our_product['цена_за_кг'])
        diff = our_price_kg - competitor_price_kg
        
        if diff < -5:
            recommend = '🔻 СНИЗИТЬ цену'
        elif diff > 5:
            recommend = '🔺 ПОДНЯТЬ цену'
        else:
            recommend = '✅ Оставить'
        
        if weight_kg < 0.1:
            weight_display = f"{weight_kg*1000:.0f} г"
        else:
            weight_display = f"{weight_kg:.2f} кг"
        
        return {
            'status': 'ok',
            'name': our_product['наименование'],
            'our_price_kg': round(our_price_kg, 2),
            'competitor_raw': f"{price:.0f} руб за {weight_display}",
            'competitor_price_kg': round(competitor_price_kg, 2),
            'diff': round(diff, 2),
            'recommend': recommend
        }
    except Exception as e:
        return {'status': 'error', 'message': str(e)[:100]}

def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📸 Добавить фото", callback_data='add_photo')],
        [InlineKeyboardButton("✅ Обработать", callback_data='done')],
        [InlineKeyboardButton("📂 Загрузить прайс", callback_data='upload_price')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_photos:
        user_photos[user_id] = []
    
    await update.message.reply_text(
        "🤖 Бот мониторинга цен\n\n"
        "1. Добавьте фото\n"
        "2. Нажмите Обработать\n"
        "3. Получите Excel",
        reply_markup=get_main_keyboard()
    )

async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'add_photo':
        await query.edit_message_text(
            f"Отправьте фото. В очереди: {len(user_photos.get(user_id, []))}/100",
            reply_markup=get_main_keyboard()
        )
    
    elif query.data == 'done':
        photos = user_photos.get(user_id, [])
        if not photos:
            await query.edit_message_text("Нет фото", reply_markup=get_main_keyboard())
            return
        
        await query.edit_message_text(f"Обработка {len(photos)} фото...")
        
        results = []
        for path in photos:
            res = process_photo(path)
            results.append(res)
            try:
                os.unlink(path)
            except:
                pass
        
        df_ok = pd.DataFrame([r for r in results if r['status'] == 'ok'])
        df_err = pd.DataFrame([r for r in results if r['status'] == 'error'])
        
        output_file = f'report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
        with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
            if not df_ok.empty:
                cols = ['name', 'our_price_kg', 'competitor_raw', 'competitor_price_kg', 'diff', 'recommend']
                df_ok[cols].to_excel(writer, sheet_name='Анализ', index=False)
            if not df_err.empty:
                df_err[['message']].to_excel(writer, sheet_name='Ошибки', index=False)
        
        with open(output_file, 'rb') as f:
            await query.message.reply_document(
                document=f,
                filename=output_file,
                caption=f"✅ Успешно: {len(df_ok)}\n❌ Ошибок: {len(df_err)}"
            )
        
        os.unlink(output_file)
        user_photos[user_id] = []
        await query.message.reply_text("Готово!", reply_markup=get_main_keyboard())
    
    elif query.data == 'upload_price':
        await query.edit_message_text(
            "Отправьте Excel файл\nКолонки: наименование, цена_за_кг",
            reply_markup=get_main_keyboard()
        )
    
    elif query.data == 'help':
        await query.edit_message_text(
            "Формат: винегрет 230г 200р",
            reply_markup=get_main_keyboard()
        )

async def handle_photo(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_photos:
        user_photos[user_id] = []
    
    if len(user_photos[user_id]) >= 100:
        await update.message.reply_text("Лимит 100 фото")
        return
    
    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        await photo_file.download_to_drive(tmp.name)
        user_photos[user_id].append(tmp.name)
    
    count = len(user_photos[user_id])
    await update.message.reply_text(f"✅ Фото {count}/100 принято")

async def handle_excel(update: Update, context: CallbackContext):
    global our_price_df
    doc = update.message.document
    if not doc.file_name.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("Нужен Excel файл")
        return
    
    file = await doc.get_file()
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        try:
            df = pd.read_excel(tmp.name)
            df.columns = df.columns.str.lower()
            if 'наименование' not in df.columns or 'цена_за_кг' not in df.columns:
                await update.message.reply_text("Нужны колонки: наименование, цена_за_кг")
                return
            
            our_price_df = df.copy()
            our_price_df['наименование_нижн'] = our_price_df['наименование'].str.lower()
            our_price_df['цена_за_кг'] = pd.to_numeric(our_price_df['цена_за_кг'], errors='coerce')
            our_price_df = our_price_df.dropna(subset=['цена_за_кг'])
            
            await update.message.reply_text(f"✅ Прайс загружен: {len(our_price_df)} товаров")
        except Exception as e:
            await update.message.reply_text(f"Ошибка: {e}")
        finally:
            os.unlink(tmp.name)

def load_demo_price():
    global our_price_df
    demo_data = {
        'наименование': ['винегрет', 'оливье', 'сельдь под шубой', 'цезарь', 'греческий', 'борщ', 'солянка', 'плов'],
        'цена_за_кг': [450, 520, 680, 890, 750, 350, 420, 600]
    }
    our_price_df = pd.DataFrame(demo_data)
    our_price_df['наименование_нижн'] = our_price_df['наименование'].str.lower()

def main():
    load_demo_price()
    print(f"✅ Демо-прайс: {len(our_price_df)} товаров")
    
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_excel))
    
    print("🚀 Бот запущен!")
    app.run_polling()

if __name__ == '__main__':
    main()