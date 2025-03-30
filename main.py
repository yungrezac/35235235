import os
import logging
import random
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client, Client
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    BotCommand
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ChatMemberHandler
)
import requests
from enum import Enum

load_dotenv()

# Инициализация Supabase
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Конфигурация
BOT_TOKEN = os.getenv("BOT_TOKEN")
CRYPTO_BOT_TOKEN = os.getenv("CRYPTO_BOT_TOKEN")
CRYPTO_BOT_NAME = os.getenv("CRYPTO_BOT_NAME")
STARTING_BALANCE = 1000
ADMIN_IDS = list(map(int, os.getenv("ADMIN_IDS", "").split(",")))
MIN_DEPOSIT = float(os.getenv("MIN_DEPOSIT", 100))
MIN_WITHDRAW = float(os.getenv("MIN_WITHDRAW", 100))
MIN_BET = 10
WEEKLY_BONUS = 300

# Настройка логов
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

class GameType(Enum):
    DICE = "dice"
    ROULETTE = "roulette"

class TransactionType(Enum):
    DEPOSIT = "deposit"
    WITHDRAW = "withdraw"
    BONUS = "bonus"
    WIN = "win"
    LOSS = "loss"

class CryptoBot:
    @staticmethod
    def create_invoice(amount: float, user_id: int):
        url = "https://pay.crypt.bot/api/createInvoice"
        headers = {
            "Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN,
            "Content-Type": "application/json"
        }
        data = {
            "asset": "USDT",
            "amount": str(amount),
            "description": f"Пополнение баланса для пользователя {user_id}",
            "payload": str(user_id)
        }
        response = requests.post(url, headers=headers, json=data)
        return response.json()

    @staticmethod
    def check_invoice(invoice_id: str):
        url = f"https://pay.crypt.bot/api/getInvoices?invoice_ids={invoice_id}"
        headers = {"Crypto-Pay-API-Token": CRYPTO_BOT_TOKEN}
        response = requests.get(url, headers=headers)
        return response.json()

class MiniGames:
    @staticmethod
    async def play_dice(user_id: int, bet: float):
        user_roll = random.randint(1, 6)
        bot_roll = random.randint(1, 6)
        
        if user_roll > bot_roll:
            win_amount = bet * 2
            result = f"🎲 Вы: {user_roll} vs Бот: {bot_roll}\n✅ Вы выиграли {win_amount}₽!"
            status = "win"
        elif user_roll < bot_roll:
            win_amount = 0
            result = f"🎲 Вы: {user_roll} vs Бот: {bot_roll}\n❌ Вы проиграли {bet}₽!"
            status = "lose"
        else:
            win_amount = bet
            result = f"🎲 Вы: {user_roll} vs Бот: {bot_roll}\n🤝 Ничья! Возврат {bet}₽"
            status = "draw"
        
        # Обновляем баланс (учитываем, что ставка уже списана)
        supabase.rpc('increment_balance', {'user_id': user_id, 'amount': win_amount}).execute()
        
        # Запись в историю
        supabase.table('games_history').insert({
            'user_id': user_id,
            'game_type': GameType.DICE.value,
            'bet_amount': bet,
            'win_amount': win_amount,
            'result': status
        }).execute()
        
        # Обновляем статистику
        supabase.rpc('update_user_stats', {
            'user_id': user_id,
            'games_played': 1,
            'wins': 1 if status == "win" else 0,
            'losses': 1 if status == "lose" else 0
        }).execute()
        
        return result

    @staticmethod
    async def play_roulette(user_id: int, bet: float, choice: str):
        number = random.randint(0, 36)
        is_red = number in [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
        
        # Определяем выигрыш
        if choice.isdigit():
            # Ставка на число
            if int(choice) == number:
                win_amount = bet * 36
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n🎉 Вы угадали число! Выигрыш {win_amount}₽!"
                status = "win"
            else:
                win_amount = 0
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n❌ Вы не угадали число! Проигрыш {bet}₽"
                status = "lose"
        elif choice in ["красное", "черное"]:
            # Ставка на цвет
            if (choice == "красное" and is_red) or (choice == "черное" and not is_red and number != 0):
                win_amount = bet * 2
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n✅ Вы угадали цвет! Выигрыш {win_amount}₽!"
                status = "win"
            else:
                win_amount = 0
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n❌ Вы не угадали цвет! Проигрыш {bet}₽"
                status = "lose"
        elif choice in ["четное", "нечетное"]:
            # Ставка на четность
            if number == 0:
                win_amount = 0
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n❌ Выпал 0! Проигрыш {bet}₽"
                status = "lose"
            elif (choice == "четное" and number % 2 == 0) or (choice == "нечетное" and number % 2 != 0):
                win_amount = bet * 2
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n✅ Вы угадали четность! Выигрыш {win_amount}₽!"
                status = "win"
            else:
                win_amount = 0
                result = f"🎯 Выпало: {number} ({'красное' if is_red else 'черное'})\n❌ Вы не угадали четность! Проигрыш {bet}₽"
                status = "lose"
        else:
            win_amount = 0
            result = "❌ Неверный тип ставки"
            status = "error"
        
        if status != "error":
            # Обновляем баланс
            if win_amount > 0:
                supabase.rpc('increment_balance', {'user_id': user_id, 'amount': win_amount}).execute()
            
            # Запись в историю
            supabase.table('games_history').insert({
                'user_id': user_id,
                'game_type': GameType.ROULETTE.value,
                'bet_amount': bet,
                'win_amount': win_amount,
                'result': status
            }).execute()
            
            # Обновляем статистику
            supabase.rpc('update_user_stats', {
                'user_id': user_id,
                'games_played': 1,
                'wins': 1 if status == "win" else 0,
                'losses': 1 if status == "lose" else 0
            }).execute()
        
        return result

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    ref_code = context.args[0] if context.args else None
    
    # Проверяем существование пользователя
    user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data
    if not user_data:
        # Создаем нового пользователя
        supabase.table('users').insert({
            'user_id': user.id,
            'username': user.username,
            'balance': STARTING_BALANCE,
            'created_at': datetime.now().isoformat(),
            'last_weekly_bonus': None
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
        [InlineKeyboardButton("🎮 Игры", callback_data="games")],
        [InlineKeyboardButton("💵 Пополнить", callback_data="deposit"),
         InlineKeyboardButton("💰 Вывести", callback_data="withdraw")],
        [InlineKeyboardButton("🎁 Бонус", callback_data="bonuses"),
         InlineKeyboardButton("👤 Профиль", callback_data="profile")],
        [InlineKeyboardButton("📊 Топ игроков", callback_data="leaderboard")]
    ]
    
    await update.message.reply_text(
        "🏆 Добро пожаловать в Casino Bot!\n\n"
        "🎰 Играйте в азартные игры и выигрывайте реальные деньги!",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    user = query.from_user
    
    if data == "games":
        keyboard = [
            [InlineKeyboardButton("🎲 Кости", callback_data="play_dice")],
            [InlineKeyboardButton("🎯 Рулетка", callback_data="play_roulette")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(
            "🎮 Выберите игру:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data in ["play_dice"]:
        context.user_data['game_type'] = data
        await query.edit_message_text(
            f"💰 Введите сумму ставки (мин. {MIN_BET}₽):",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="games")]])
        )
    
    elif data == "play_roulette":
        keyboard = [
            [InlineKeyboardButton("🔴 Красное", callback_data="roulette_красное"),
             InlineKeyboardButton("⚫ Черное", callback_data="roulette_черное")],
            [InlineKeyboardButton("🔢 Четное", callback_data="roulette_четное"),
             InlineKeyboardButton("🔣 Нечетное", callback_data="roulette_нечетное")],
            [InlineKeyboardButton("🎯 На число", callback_data="roulette_number")],
            [InlineKeyboardButton("🔙 Назад", callback_data="games")]
        ]
        await query.edit_message_text(
            "🎯 Выберите тип ставки в рулетке:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("roulette_"):
        choice_type = data.split("_")[1]
        if choice_type == "number":
            await query.edit_message_text(
                "🎯 Введите число от 0 до 36:",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="play_roulette")]])
            )
            context.user_data['roulette_type'] = "number"
        else:
            context.user_data['game_type'] = "play_roulette"
            context.user_data['roulette_choice'] = choice_type
            await query.edit_message_text(
                f"💰 Введите сумму ставки (мин. {MIN_BET}₽):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="play_roulette")]])
            )
    
    elif data == "deposit":
        await query.edit_message_text(
            f"💳 Пополнение баланса\n\nМинимальная сумма: {MIN_DEPOSIT}₽\n\nВведите сумму:",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]])
        )
    
    elif data == "withdraw":
        user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
        if user_data['balance'] < MIN_WITHDRAW:
            await query.answer(f"❌ Минимальная сумма вывода: {MIN_WITHDRAW}₽", show_alert=True)
            return
        
        await query.edit_message_text(
            f"💸 Вывод средств\n\nДоступно: {user_data['balance']}₽\nМинимум: {MIN_WITHDRAW}₽\n\n"
            "Введите сумму и адрес USDT (TRC20) в формате:\n<сумма> <адрес>\n\n"
            "Пример: 500 TA1b2c3d4e5f6g7h8j9k0",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]])
        )
    
    elif data == "bonuses":
        user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data[0]
        last_weekly = user_data.get('last_weekly_bonus')
        
        weekly_available = True if last_weekly is None else (datetime.now() - datetime.fromisoformat(last_weekly)).days >= 7
        
        keyboard = []
        if weekly_available:
            keyboard.append([InlineKeyboardButton("🎁 Еженедельный бонус (+300₽)", callback_data="claim_weekly")])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])
        
        text = "🎁 Доступные бонусы:\n\n"
        if not weekly_available:
            next_weekly = (datetime.fromisoformat(last_weekly) + timedelta(days=7))
            text += f"⏳ Следующий еженедельный бонус через: {next_weekly - datetime.now()}\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    
    elif data.startswith("claim_"):
        bonus_type = data.split("_")[1]
        amount = WEEKLY_BONUS
        
        # Проверяем, можно ли получить бонус
        user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data[0]
        last_bonus = user_data.get(f'last_{bonus_type}_bonus')
        
        if last_bonus is not None:
            delta = datetime.now() - datetime.fromisoformat(last_bonus)
            if (bonus_type == "weekly" and delta.days < 7):
                await query.answer("❌ Бонус уже получен", show_alert=True)
                return
        
        # Начисляем бонус
        supabase.rpc('increment_balance', {'user_id': user.id, 'amount': amount}).execute()
        supabase.table('users').update({
            f'last_{bonus_type}_bonus': datetime.now().isoformat()
        }).eq('user_id', user.id).execute()
        
        # Запись транзакции
        supabase.table('transactions').insert({
            'user_id': user.id,
            'amount': amount,
            'type': TransactionType.BONUS.value,
            'status': 'completed',
            'description': f'{bonus_type} bonus'
        }).execute()
        
        await query.edit_message_text(
            f"🎉 Вы получили {amount}₽ за {bonus_type} бонус!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="bonuses")]])
        )
    
    elif data == "profile":
        user_data = supabase.table('users').select('*').eq('user_id', user.id).execute().data[0]
        ref_count = supabase.table('referrals').select('count').eq('referrer_id', user.id).execute().data[0]['count']
        ref_link = f"https://t.me/{context.bot.username}?start={user.id}"
        
        text = (
            f"👤 Профиль @{user.username}\n\n"
            f"💰 Баланс: {user_data['balance']}₽\n"
            f"🎮 Игр сыграно: {user_data.get('games_played', 0)}\n"
            f"🏆 Побед: {user_data.get('wins', 0)}\n"
            f"💔 Поражений: {user_data.get('losses', 0)}\n"
            f"👥 Рефералов: {ref_count}\n\n"
            f"🔗 Реф. ссылка: {ref_link}\n"
            f"💸 Вы получаете 200₽ за каждого приглашенного друга!"
        )
        
        keyboard = [
            [InlineKeyboardButton("📜 История игр", callback_data="game_history")],
            [InlineKeyboardButton("💳 История транзакций", callback_data="transaction_history")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    
    elif data == "game_history":
        history = supabase.table('games_history').select('*').eq('user_id', user.id).order('created_at', desc=True).limit(10).execute().data
        
        if not history:
            text = "📜 История игр пуста"
        else:
            text = "📜 Последние 10 игр:\n\n"
            for game in history:
                result = "✅ Выигрыш" if game['result'] == "win" else "❌ Проигрыш" if game['result'] == "lose" else "🤝 Ничья"
                text += (
                    f"{game['game_type'].capitalize()} | {result}\n"
                    f"Ставка: {game['bet_amount']}₽ | Выигрыш: {game['win_amount']}₽\n"
                    f"Дата: {datetime.fromisoformat(game['created_at']).strftime('%d.%m.%Y %H:%M')}\n\n"
                )
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]])
        )
    
    elif data == "transaction_history":
        transactions = supabase.table('transactions').select('*').eq('user_id', user.id).order('created_at', desc=True).limit(10).execute().data
        
        if not transactions:
            text = "💳 История транзакций пуста"
        else:
            text = "💳 Последние 10 транзакций:\n\n"
            for tx in transactions:
                if tx['type'] == TransactionType.DEPOSIT.value:
                    emoji = "⬆️"
                    status = "Пополнение"
                elif tx['type'] == TransactionType.WITHDRAW.value:
                    emoji = "⬇️"
                    status = "Вывод"
                elif tx['type'] == TransactionType.BONUS.value:
                    emoji = "🎁"
                    status = "Бонус"
                else:
                    emoji = "🔄"
                    status = tx['type']
                
                text += (
                    f"{emoji} {status}: {tx['amount']}₽\n"
                    f"Статус: {tx['status']}\n"
                    f"Дата: {datetime.fromisoformat(tx['created_at']).strftime('%d.%m.%Y %H:%M')}\n\n"
                )
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="profile")]])
        )
    
    elif data == "leaderboard":
        top_users = supabase.table('users').select('user_id, username, balance').order('balance', desc=True).limit(10).execute().data
        
        text = "🏆 Топ 10 игроков по балансу:\n\n"
        for i, user_data in enumerate(top_users, 1):
            text += f"{i}. @{user_data['username']} - {user_data['balance']}₽\n"
        
        await query.edit_message_text(
            text,
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="main_menu")]])
        )
    
    elif data == "main_menu":
        keyboard = [
            [InlineKeyboardButton("🎮 Игры", callback_data="games")],
            [InlineKeyboardButton("💵 Пополнить", callback_data="deposit"),
             InlineKeyboardButton("💰 Вывести", callback_data="withdraw")],
            [InlineKeyboardButton("🎁 Бонус", callback_data="bonuses"),
             InlineKeyboardButton("👤 Профиль", callback_data="profile")],
            [InlineKeyboardButton("📊 Топ игроков", callback_data="leaderboard")]
        ]
        await query.edit_message_text(
            "🏆 Главное меню:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text
    
    # Обработка ставки для игры
    if 'game_type' in context.user_data:
        try:
            bet = float(text)
            if bet < MIN_BET:
                await update.message.reply_text(f"❌ Минимальная ставка - {MIN_BET}₽")
                return
            
            # Проверяем баланс
            user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
            if user_data['balance'] < bet:
                await update.message.reply_text("❌ Недостаточно средств!")
                return
            
            # Списание ставки
            supabase.rpc('increment_balance', {'user_id': user.id, 'amount': -bet}).execute()
            
            # Запуск игры
            game_type = context.user_data['game_type']
            if game_type == "play_dice":
                result = await MiniGames.play_dice(user.id, bet)
            elif game_type == "play_roulette":
                result = await MiniGames.play_roulette(user.id, bet, context.user_data['roulette_choice'])
            
            # Получаем обновленный баланс
            user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
            
            keyboard = [
                [InlineKeyboardButton("🎮 Играть еще", callback_data=game_type)],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
            ]
            
            await update.message.reply_text(
                f"{result}\n\n💰 Баланс: {user_data['balance']}₽",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
            # Удаляем информацию о игре из контекста
            context.user_data.clear()
            
        except ValueError:
            await update.message.reply_text("❌ Введите корректную сумму!")
    
    # Обработка числа для рулетки
    elif 'roulette_type' in context.user_data and context.user_data['roulette_type'] == "number":
        if text.isdigit() and 0 <= int(text) <= 36:
            context.user_data['game_type'] = "play_roulette"
            context.user_data['roulette_choice'] = text
            context.user_data.pop('roulette_type')
            await update.message.reply_text(
                f"💰 Введите сумму ставки (мин. {MIN_BET}₽):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Назад", callback_data="play_roulette")]])
            )
        else:
            await update.message.reply_text("❌ Введите число от 0 до 36!")
    
    # Обработка пополнения
    elif text.replace('.', '').isdigit() and float(text) >= MIN_DEPOSIT:
        amount = float(text)
        invoice = CryptoBot.create_invoice(amount, user.id)
        
        if invoice.get('result'):
            # Сохраняем транзакцию
            supabase.table('transactions').insert({
                'user_id': user.id,
                'amount': amount,
                'type': TransactionType.DEPOSIT.value,
                'status': 'pending',
                'invoice_id': invoice['result']['invoice_id']
            }).execute()
            
            keyboard = [
                [InlineKeyboardButton("💳 Оплатить", url=invoice['result']['pay_url'])],
                [InlineKeyboardButton("🔄 Проверить оплату", callback_data=f"check_{invoice['result']['invoice_id']}")],
                [InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]
            ]
            
            await update.message.reply_text(
                f"💸 Счет на {amount}₽ создан\n\n"
                "Ссылка действительна 15 минут",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        else:
            await update.message.reply_text("❌ Ошибка при создании счета")
    
    # Обработка вывода
    elif len(text.split()) == 2:
        try:
            amount, address = text.split()
            amount = float(amount)
            
            if amount < MIN_WITHDRAW:
                await update.message.reply_text(f"❌ Минимальный вывод: {MIN_WITHDRAW}₽")
                return
            
            user_data = supabase.table('users').select('balance').eq('user_id', user.id).execute().data[0]
            if user_data['balance'] < amount:
                await update.message.reply_text("❌ Недостаточно средств!")
                return
            
            # Здесь должна быть логика вывода через Crypto Bot
            # В демо-версии просто списываем средства
            supabase.rpc('increment_balance', {'user_id': user.id, 'amount': -amount}).execute()
            
            # Записываем транзакцию
            supabase.table('transactions').insert({
                'user_id': user.id,
                'amount': amount,
                'type': TransactionType.WITHDRAW.value,
                'status': 'completed',
                'address': address
            }).execute()
            
            await update.message.reply_text(
                f"✅ Заявка на вывод {amount}₽ принята!\n\n"
                f"Средства будут отправлены на адрес:\n{address}"
            )
            
        except ValueError:
            await update.message.reply_text("❌ Неверный формат. Используйте: <сумма> <адрес>")
    
    else:
        await update.message.reply_text("❌ Неизвестная команда")

async def check_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    invoice_id = query.data.split('_')[1]
    invoice_data = CryptoBot.check_invoice(invoice_id)
    
    if invoice_data.get('result') and invoice_data['result'][0]['status'] == 'paid':
        # Обновляем баланс
        user_id = int(invoice_data['result'][0]['payload'])
        amount = float(invoice_data['result'][0]['amount'])
        
        supabase.rpc('increment_balance', {'user_id': user_id, 'amount': amount}).execute()
        supabase.table('transactions').update({'status': 'completed'}).eq('invoice_id', invoice_id).execute()
        
        await query.edit_message_text(
            f"✅ Оплата {amount}₽ зачислена!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]])
        )
    else:
        await query.answer("❌ Оплата не найдена", show_alert=True)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error("Exception while handling an update:", exc_info=context.error)
    
    if update.callback_query:
        await update.callback_query.answer("❌ Произошла ошибка, попробуйте позже", show_alert=True)
    elif update.message:
        await update.message.reply_text("❌ Произошла ошибка, попробуйте позже")

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Команды бота
    commands = [
        BotCommand("start", "Запустить бота"),
        BotCommand("games", "Список игр"),
        BotCommand("deposit", "Пополнить баланс"),
        BotCommand("withdraw", "Вывести средства"),
        BotCommand("profile", "Ваш профиль")
    ]
    
    # Обработчики
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(CallbackQueryHandler(check_payment, pattern="^check_"))
    
    # Обработчик ошибок
    app.add_error_handler(error_handler)
    
    # Установка команд
    app.bot.set_my_commands(commands)
    
    app.run_polling()

if __name__ == "__main__":
    main()
