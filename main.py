import os
import logging
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    BotCommand
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    InlineQueryHandler,
    ChatMemberHandler
)

load_dotenv()

# Инициализация Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
STARTING_BALANCE = 1000  # Демо баланс
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))

# Настройка логов
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_code = context.args[0] if context.args else None
    
    user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data
    if not user_data:
        # Создание пользователя с демо балансом
        supabase.table('users').insert({
            'user_id': user.id,
            'username': user.username,
            'balance': STARTING_BALANCE
        }).execute()
        
        # Реферальная система
        if ref_code and ref_code != str(user.id):
            supabase.table('referrals').insert({
                'referrer_id': ref_code,
                'referred_id': user.id
            }).execute()
            supabase.rpc('increment_balance', {'user_id': ref_code, 'amount': 200}).execute()
            supabase.rpc('increment_balance', {'user_id': user.id, 'amount': 100}).execute()
    
    # Главное меню
    keyboard = [
        [InlineKeyboardButton("🎮 Начать игру", callback_data="main_game")],
        [InlineKeyboardButton("💼 Профиль", callback_data="profile"),
         InlineKeyboardButton("💵 Пополнить", callback_data="deposit")],
        [InlineKeyboardButton("➕ Добавить в чат", callback_data="add_to_chat"),
         InlineKeyboardButton("🌐 Чаты с ботом", callback_data="active_chats")]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(
            "🏆 Добро пожаловать в Casino Bot!\nВыберите действие:",
            reply_markup=reply_markup
        )
    else:
        await update.callback_query.edit_message_text(
            "🏆 Добро пожаловать в Casino Bot!\nВыберите действие:",
            reply_markup=reply_markup
        )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user = query.from_user
    
    if data == "main_game":
        # Кнопки выбора типа игры
        keyboard = [
            [InlineKeyboardButton("🎰 Быстрая игра", callback_data="quick_game")],
            [InlineKeyboardButton("👥 Мультиплеер", callback_data="multiplayer")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "🎮 Выберите тип игры:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "profile":
        user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data[0]
        ref_link = f"https://t.me/{context.bot.username}?start={user.id}"
        
        text = (
            f"👤 Ваш профиль:\n\n"
            f"💵 Баланс: {user_data['balance']}₽\n"
            f"📈 Игр сыграно: {user_data.get('games_played', 0)}\n"
            f"🎉 Побед: {user_data.get('wins', 0)}\n\n"
            f"🔗 Реф. ссылка: {ref_link}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "deposit":
        keyboard = [
            [InlineKeyboardButton("🥝 QIWI", callback_data="deposit_qiwi"),
             InlineKeyboardButton("💳 Карта", callback_data="deposit_card")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "💰 Выберите способ пополнения:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "add_to_chat":
        bot_username = context.bot.username
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]]
        await query.edit_message_text(
            f"👥 Чтобы добавить бота в чат:\n\n"
            f"1. Откройте нужный чат\n"
            f"2. Добавьте @{bot_username}\n"
            f"3. Выдайте права администратора\n\n"
            "После этого бот будет доступен в списке чатов!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data == "active_chats":
        chats = supabase.table('chats').select('chat_id,title').execute().data
        if not chats:
            await query.answer("😢 Пока нет активных чатов")
            return
        
        keyboard = []
        for chat in chats:
            keyboard.append([InlineKeyboardButton(
                f"💬 {chat['title']}", 
                callback_data=f"joinchat_{chat['chat_id']}"
            )])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
        
        await query.edit_message_text(
            "🌐 Доступные чаты для игры:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("joinchat_"):
        chat_id = data.split("_")[1]
        await query.answer("ℹ️ Используйте команду /start в выбранном чате!")
    
    elif data == "main_menu":
        await start(update, context)

async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик добавления/удаления бота из чатов"""
    if update.my_chat_member:
        chat = update.my_chat_member.chat
        new_status = update.my_chat_member.new_chat_member.status
        
        if new_status == 'administrator':
            supabase.table('chats').upsert({
                'chat_id': chat.id,
                'title': chat.title,
                'type': chat.type,
                'added_at': datetime.now().isoformat()
            }).execute()
        elif new_status in ['kicked', 'left']:
            supabase.table('chats').delete().eq('chat_id', chat.id).execute()

async def handle_game_creation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды начала игры в чатах"""
    if update.message.chat.type == 'private':
        return
    
    try:
        bet = float(context.args[0])
        user = update.effective_user
        
        user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data
        if not user_data or user_data[0]['balance'] < bet:
            await update.message.reply_text("❌ Недостаточно средств!")
            return
        
        # Создание игры
        game = supabase.table('games').insert({
            'chat_id': update.message.chat.id,
            'creator_id': user.id,
            'bet_amount': bet,
            'status': 'waiting'
        }).execute().data[0]
        
        # Сообщение с кнопкой присоединения
        keyboard = [[InlineKeyboardButton("✅ Присоединиться", callback_data=f"join_{game['id']}")]]
        await update.message.reply_text(
            f"🎮 Новая игра!\nСтавка: {bet}₽\nУчастники: 1",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    except:
        await update.message.reply_text("Использование: /game <ставка>")

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("game", handle_game_creation))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(ChatMemberHandler(track_chats))
    
    # Фильтры
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    
    app.run_polling()

async def post_init(application: Application):
    await application.bot.set_my_commands([
        BotCommand("start", "Запустить бота"),
        BotCommand("game", "Начать новую игру (в чате)"),
        BotCommand("profile", "Ваш профиль")
    ])

if __name__ == "__main__":
    main()
