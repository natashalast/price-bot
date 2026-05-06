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
import pytesseract
from PIL import Image
from rapidfuzz import process

# ========== КОНФИГУРАЦИЯ ==========
TOKEN = '8132772993:AAEzemQlQ5RGFq-cQkKFQ9o0xeNWl5e_1S4'

# Путь к Tesseract (для Render)
pytesseract.pytesseract.tesseract_cmd = '/usr/bin/tesseract'

our_price_df = None
user_photos = {}

# ========== ФУНКЦИИ OCR И ПАРСИНГА ==========
def ocr_image(image_path):
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang='rus+eng')
        return text
    except Exception as e:
        print(f"OCR ошибка: {e}")
        return ""

def extract_price_weight_from_text(text):
    if not text:
        return None, None, None
    
    text = text.lower()
    
    # Поиск цены
    price_match = re.search(r'(\d+[\.,]?\d*)\s*[рруб₽]', text)
    if not price_match:
        price_match = re.search(r'(\d+[\.,]?\d*)\s*$', text)
    if not price_match:
        return None, None, None
    
    price = float(price_match.group(1).replace(',', '.'))
    
    # Поиск веса
    weight_match = re.search(r'(\d+[\.,]?\d*)\s*(гр?|грамм|g|кг|kg)', text)
    weight_kg = None
    if weight_match:
        value = float(weight_match.group(1).replace(',', '.'))
        unit = weight_match.group(2).lower()
        if unit in ('г', 'гр', 'грамм', 'g'):
            weight_kg = value / 1000
        elif unit in ('кг', 'kg'):
            weight_kg = value
    
    # Поиск названия
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
            return {'status': 'error', 'message': 'Не найден вес (нужны г или кг)'}
        
        our_product = find_our_product(name)
        if our_product is None:
            return {'status': 'error', 'message': f'Товар "{name}" не найден в ассортименте'}
        
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

# ========== КЛАВИАТУРА ==========
def get_main_keyboard():
    keyboard = [
        [InlineKeyboardButton("📸 Добавить фото", callback_data='add_photo')],
        [InlineKeyboardButton("✅ Обработать все фото", callback_data='done')],
        [InlineKeyboardButton("📂 Загрузить прайс", callback_data='upload_price')],
        [InlineKeyboardButton("ℹ️ Помощь", callback_data='help')]
    ]
    return InlineKeyboardMarkup(keyboard)

# ========== ОБРАБОТЧИКИ КОМАНД ==========
async def start(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_photos:
        user_photos[user_id] = []
    
    await update.message.reply_text(
        "🤖 **Бот мониторинга цен**\n\n"
        "📌 **Как работать:**\n"
        "1️⃣ Нажмите «Добавить фото»\n"
        "2️⃣ Отправьте фото ценника\n"
        "3️⃣ Нажмите «Обработать все фото»\n"
        "4️⃣ Получите Excel-отчет\n\n"
        "📝 **Пример ценника:** винегрет 230г 200р\n\n"
        "📂 Можно загрузить свой Excel-прайс",
        reply_markup=get_main_keyboard(),
        parse_mode='Markdown'
    )

async def button_handler(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    
    if query.data == 'add_photo':
        await query.edit_message_text(
            f"📸 Отправьте фото ценника\n\n"
            f"📊 В очереди: {len(user_photos.get(user_id, []))}/100 фото\n\n"
            f"После отправки всех фото нажмите «Обработать все фото»",
            reply_markup=get_main_keyboard()
        )
    
    elif query.data == 'done':
        photos = user_photos.get(user_id, [])
        if not photos:
            await query.edit_message_text("❌ Нет фото для обработки", reply_markup=get_main_keyboard())
            return
        
        status_msg = await query.edit_message_text(f"⏳ Обработка 0/{len(photos)} фото...")
        
        results = []
        for i, path in enumerate(photos):
            await status_msg.edit_text(f"⏳ Обработка {i+1}/{len(photos)}...")
            res = process_photo(path)
            results.append(res)
            try:
                os.unlink(path)
            except:
                pass
        
        df_ok = pd.DataFrame([r for r in results if r['status'] == 'ok'])
        df_err = pd.DataFrame([r for r in results if r['status'] == 'error'])
        
        output_file = f'price_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
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
                caption=f"✅ **Обработка завершена!**\n\n"
                       f"📊 Успешно: {len(df_ok)}\n"
                       f"❌ Ошибок: {len(df_err)}"
            )
        
        os.unlink(output_file)
        user_photos[user_id] = []
        await query.message.reply_text("🔄 Готов к новой проверке!", reply_markup=get_main_keyboard())
    
    elif query.data == 'upload_price':
        await query.edit_message_text(
            "📂 **Загрузка прайс-листа**\n\n"
            "Отправьте Excel файл с колонками:\n"
            "• `наименование` - название товара\n"
            "• `цена_за_кг` - цена за кг\n\n"
            "**Пример:**\n"
            "винегрет | 450\n"
            "оливье | 520",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )
    
    elif query.data == 'help':
        await query.edit_message_text(
            "❓ **Помощь**\n\n"
            "**Примеры правильных ценников:**\n"
            "• винегрет 230г 200р\n"
            "• оливье 500г 300₽\n"
            "• цезарь 0.35кг 400руб\n\n"
            "**Что делать с ошибками:**\n"
            "• Если товар не найден — проверьте название в прайсе\n"
            "• Если не распознан вес — укажите г или кг\n"
            "• Если фото мутное — переснимите",
            reply_markup=get_main_keyboard(),
            parse_mode='Markdown'
        )

# ========== ОБРАБОТЧИКИ ФАЙЛОВ ==========
async def handle_photo(update: Update, context: CallbackContext):
    user_id = update.effective_user.id
    if user_id not in user_photos:
        user_photos[user_id] = []
    
    if len(user_photos[user_id]) >= 100:
        await update.message.reply_text("⚠️ Лимит 100 фото. Нажмите «Обработать все фото»")
        return
    
    photo_file = await update.message.photo[-1].get_file()
    with tempfile.NamedTemporaryFile(suffix='.jpg', delete=False) as tmp:
        await photo_file.download_to_drive(tmp.name)
        user_photos[user_id].append(tmp.name)
    
    count = len(user_photos[user_id])
    await update.message.reply_text(
        f"✅ Фото {count}/100 принято!\n"
        f"Отправляйте еще или нажмите «Обработать все фото»"
    )

async def handle_excel(update: Update, context: CallbackContext):
    global our_price_df
    
    doc = update.message.document
    if not doc.file_name.endswith(('.xlsx', '.xls')):
        await update.message.reply_text("❌ Отправьте Excel файл (.xlsx или .xls)")
        return
    
    file = await doc.get_file()
    with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as tmp:
        await file.download_to_drive(tmp.name)
        try:
            df = pd.read_excel(tmp.name)
            df.columns = df.columns.str.lower()
            
            if 'наименование' not in df.columns or 'цена_за_кг' not in df.columns:
                await update.message.reply_text(
                    "❌ Неправильный формат!\n\n"
                    "Нужны колонки: **наименование** и **цена_за_кг**",
                    parse_mode='Markdown'
                )
                return
            
            our_price_df = df.copy()
            our_price_df['наименование_нижн'] = our_price_df['наименование'].str.lower()
            our_price_df['цена_за_кг'] = pd.to_numeric(our_price_df['цена_за_кг'], errors='coerce')
            our_price_df = our_price_df.dropna(subset=['цена_за_кг'])
            
            await update.message.reply_text(
                f"✅ **Прайс-лист загружен!**\n\n"
                f"📦 Товаров: {len(our_price_df)}\n"
                f"💰 Цены от {our_price_df['цена_за_кг'].min():.0f} до {our_price_df['цена_за_кг'].max():.0f} руб/кг",
                reply_markup=get_main_keyboard(),
                parse_mode='Markdown'
            )
        except Exception as e:
            await update.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")
        finally:
            os.unlink(tmp.name)

# ========== ТЕКСТОВЫЕ КОМАНДЫ (FALLBACK) ==========
async def add_command(update: Update, context: CallbackContext):
    """Альтернативная команда /add если кнопки не работают"""
    await update.message.reply_text(
        "📸 Отправьте фото ценника.\n"
        "После отправки всех фото используйте команду /process"
    )

async def process_command(update: Update, context: CallbackContext):
    """Альтернативная команда /process если кнопки не работают"""
    user_id = update.effective_user.id
    photos = user_photos.get(user_id, [])
    
    if not photos:
        await update.message.reply_text("❌ Нет фото для обработки. Сначала отправьте фото через команду /add")
        return
    
    await update.message.reply_text(f"⏳ Начинаю обработку {len(photos)} фото...")
    
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
        await update.message.reply_document(
            document=f,
            filename=output_file,
            caption=f"✅ Успешно: {len(df_ok)}\n❌ Ошибок: {len(df_err)}"
        )
    
    os.unlink(output_file)
    user_photos[user_id] = []
    await update.message.reply_text("✅ Готово!")

# ========== ДЕМО-ПРАЙС ==========
def load_demo_price():
    global our_price_df
    demo_data = {
        'наименование': ['винегрет', 'оливье', 'сельдь под шубой', 'цезарь', 'греческий', 'борщ', 'солянка', 'плов'],
        'цена_за_кг': [450, 520, 680, 890, 750, 350, 420, 600]
    }
    our_price_df = pd.DataFrame(demo_data)
    our_price_df['наименование_нижн'] = our_price_df['наименование'].str.lower()

# ========== ЗАПУСК ==========
def main():
    load_demo_price()
    print(f"✅ Демо-прайс загружен: {len(our_price_df)} товаров")
    
    app = Application.builder().token(TOKEN).build()
    
    # Обработчики команд
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('add', add_command))  # текстовый fallback
    app.add_handler(CommandHandler('process', process_command))  # текстовый fallback
    
    # Обработчики кнопок
    app.add_handler(CallbackQueryHandler(button_handler))
    
    # Обработчики файлов
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_excel))
    
    print("🚀 Бот запущен!")
    print("📱 Доступные команды: /start, /add, /process")
    app.run_polling()

if __name__ == '__main__':
    main()