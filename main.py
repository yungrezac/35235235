import os
import logging
import random
from datetime import datetime, timedelta
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    JobQueue
)

load_dotenv()

# Инициализация Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
MIN_BET = 50
MAX_BET = 5000
REFERRAL_BONUS = 100

# Настройка логов
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class GameManager:
    @staticmethod
    async def generate_cards():
        return [random.randint(1, 36) for _ in range(3)]

    @staticmethod
    async def determine_winner(cards):
        unique = len(set(cards))
        if unique == 1:
            return 'трипл'
        elif unique == 2:
            return 'пара'
        else:
            return 'старшая карта'

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_code = context.args[0] if context.args else None
    
    user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data
    if not user_data:
        # Реферальная логика
        if ref_code:
            referrer = supabase.table('users').select('*').eq('user_id', ref_code).execute().data
            if referrer:
                supabase.table('referrals').insert({
                    'referrer_id': ref_code,
                    'referred_id': user.id
                }).execute()
                supabase.rpc('increment_referrals', {'user_id': ref_code}).execute()
                supabase.rpc('update_balance', {'user_id': user.id, 'amount': REFERRAL_BONUS}).execute()
        
        supabase.table('users').insert({
            'user_id': user.id,
            'username': user.username,
            'balance': REFERRAL_BONUS if ref_code else 0
        }).execute()
    
    keyboard = [
        [KeyboardButton("💰 Баланс"), KeyboardButton("🎮 Начать игру")],
        [KeyboardButton("📥 Пополнить"), KeyboardButton("📊 Статистика")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    
    await update.message.reply_text(
        f"👋 Добро пожаловать, {user.first_name}!\n\n"
        "🚀 Используйте кнопки ниже для управления:",
        reply_markup=reply_markup
    )

async def balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
    await update.message.reply_text(f"💵 Ваш баланс: {data['balance']}₽")

async def deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🥝 QIWI", callback_data="deposit_qiwi"),
         InlineKeyboardButton("💳 Card", callback_data="deposit_card")]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Выберите способ пополнения:", reply_markup=markup)

async def handle_deposit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # Генерация платежных данных
    payment_data = ... # Интеграция с платежной системой
    
    await query.edit_message_text(
        f"💳 Для пополнения:\n\n"
        f"Сумма: {payment_data['amount']}₽\n"
        f"Кошелек: {payment_data['wallet']}\n\n"
        "⚠️ Отправьте точную сумму в течение 15 минут"
    )

async def start_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        bet = float(update.message.text.split()[-1])
        if not (MIN_BET <= bet <= MAX_BET):
            raise ValueError
        
        user = update.effective_user
        user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
        
        if user_data['balance'] < bet:
            await update.message.reply_text("❌ Недостаточно средств!")
            return
        
        # Создание игры
        game = supabase.table('games').insert({
            'chat_id': update.effective_chat.id,
            'creator_id': user.id,
            'bet_amount': bet,
            'status': 'waiting'
        }).execute().data[0]
        
        # Кнопка присоединения
        keyboard = [[InlineKeyboardButton("✅ Присоединиться", callback_data=f"join_{game['id']}")]]
        markup = InlineKeyboardMarkup(keyboard)
        
        msg = await update.message.reply_text(
            f"🎮 Игра #{game['id']}\n"
            f"💵 Ставка: {bet}₽\n"
            f"⏳ Осталось времени: 60 сек",
            reply_markup=markup
        )
        
        # Запуск таймера
        context.job_queue.run_once(end_game, 60, data={
            'chat_id': update.effective_chat.id,
            'message_id': msg.message_id,
            'game_id': game['id']
        })
        
    except:
        await update.message.reply_text(f"❌ Некорректная ставка! Диапазон: {MIN_BET}-{MAX_BET}₽")

async def join_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    game_id = int(query.data.split('_')[1])
    
    user = query.from_user
    game = supabase.table('games').select('*').eq('id', game_id).execute().data[0]
    
    # Проверки
    if supabase.table('game_players').select('*').eq('game_id', game_id).eq('user_id', user.id).execute().data:
        await query.answer("Вы уже в игре!")
        return
    
    if supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]['balance'] < game['bet_amount']:
        await query.answer("Недостаточно средств!")
        return
    
    # Добавление игрока
    supabase.table('game_players').insert({
        'game_id': game_id,
        'user_id': user.id
    }).execute()
    
    await query.answer("Вы успешно присоединились!")
    await query.edit_message_text(
        query.message.text + f"\n👤 Участник: {user.first_name}"
    )

async def end_game(context: ContextTypes.DEFAULT_TYPE):
    job = context.job
    game = supabase.table('games').select('*').eq('id', job.data['game_id']).execute().data[0]
    
    if game['status'] != 'waiting':
        return
    
    players = supabase.table('game_players').select('user_id').eq('game_id', job.data['game_id']).execute().data
    
    if len(players) < 2:
        await context.bot.edit_message_text(
            "❌ Игра отменена: недостаточно участников",
            chat_id=job.data['chat_id'],
            message_id=job.data['message_id']
        )
        return
    
    # Генерация карт и определение победителя
    results = []
    for player in players:
        cards = await GameManager.generate_cards()
        result = await GameManager.determine_winner(cards)
        supabase.table('game_players').update({
            'cards': str(cards),
            'result': result
        }).eq('game_id', job.data['game_id']).eq('user_id', player['user_id']).execute()
        results.append((player['user_id'], result, max(cards)))
    
    # Определение победителя
    winner = sorted(results, key=lambda x: (x[1], x[2]), reverse=True)[0]
    total_pot = game['bet_amount'] * len(players)
    
    # Обновление балансов
    supabase.rpc('update_balance', {'user_id': winner[0], 'amount': total_pot}).execute()
    supabase.table('games').update({'status': 'finished'}).eq('id', job.data['game_id']).execute()
    
    # Отправка результатов
    result_text = "🏆 Результаты игры:\n\n"
    for user_id, res, card in results:
        user = await context.bot.get_chat(user_id)
        result_text += f"{user.first_name}: {res} ({card})\n"
    
    result_text += f"\n🎉 Победитель: {await context.bot.get_chat(winner[0]).first_name} +{total_pot}₽"
    
    await context.bot.edit_message_text(
        result_text,
        chat_id=job.data['chat_id'],
        message_id=job.data['message_id']
    )

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    data = supabase.table('users').select('*').eq('user_id', user.id).execute().data[0]
    
    stats_text = (
        f"📊 Ваша статистика:\n\n"
        f"🎮 Игр сыграно: {data['games_played']}\n"
        f"🏆 Побед: {data['wins']}\n"
        f"💸 Общий выигрыш: {data['total_won']}₽\n"
        f"👥 Рефералы: {data['referrals_count']}"
    )
    
    await update.message.reply_text(stats_text)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex(r"^💰 Баланс$"), balance))
    app.add_handler(MessageHandler(filters.Regex(r"^🎮 Начать игру"), start_game))
    app.add_handler(MessageHandler(filters.Regex(r"^📥 Пополнить"), deposit))
    app.add_handler(MessageHandler(filters.Regex(r"^📊 Статистика"), stats))
    app.add_handler(CallbackQueryHandler(handle_deposit, pattern="^deposit_"))
    app.add_handler(CallbackQueryHandler(join_game, pattern="^join_"))
    
    app.run_polling()

if __name__ == "__main__":
    main()
