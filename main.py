import logging
from typing import Dict, List, Tuple, Optional
from enum import Enum, auto
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters
)

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Константы игры
INITIAL_CHIPS = 1000
MIN_PLAYERS = 2
MAX_PLAYERS = 6
ANTE_AMOUNT = 10
MIN_RAISE = 10
DEFAULT_TIMEOUT = 60  # seconds

# Состояния разговора
class GameState(Enum):
    WAITING_FOR_PLAYERS = auto()
    COLLECTING_ANTE = auto()
    DEALING_CARDS = auto()
    BIDDING = auto()
    COMPARING_HANDS = auto()
    SWARA = auto()
    GAME_OVER = auto()

# Модели данных
class Card:
    RANKS = ['6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
    SUITS = ['♥', '♦', '♣', '♠']
    
    RANK_VALUES = {
        '6': 6, '7': 7, '8': 8, '9': 9, '10': 10,
        'J': 10, 'Q': 10, 'K': 10, 'A': 11
    }
    
    def __init__(self, rank: str, suit: str):
        if rank not in self.RANKS:
            raise ValueError(f"Invalid rank: {rank}")
        if suit not in self.SUITS:
            raise ValueError(f"Invalid suit: {suit}")
        
        self.rank = rank
        self.suit = suit
        self.value = self.RANK_VALUES[rank]
    
    def __str__(self):
        return f"{self.rank}{self.suit}"
    
    def __repr__(self):
        return str(self)
    
    def __eq__(self, other):
        return self.rank == other.rank and self.suit == other.suit

class Player:
    def __init__(self, user_id: int, name: str):
        self.user_id = user_id
        self.name = name
        self.chips = INITIAL_CHIPS
        self.cards: List[Card] = []
        self.is_dark = False
        self.folded = False
        self.current_bet = 0
        self.ready = False
        self.last_action_time = None
    
    def reset_round(self):
        self.cards.clear()
        self.is_dark = False
        self.folded = False
        self.current_bet = 0
        self.ready = False
        self.last_action_time = None
    
    @property
    def can_play(self) -> bool:
        return not self.folded and self.chips > 0
    
    def bet(self, amount: int) -> bool:
        if amount > self.chips:
            return False
        
        self.chips -= amount
        self.current_bet += amount
        return True
    
    def get_hand_value(self) -> Tuple[int, str]:
        """Возвращает (очки, описание комбинации)"""
        # Проверка на три шестерки (особый случай)
        if all(card.rank == '6' for card in self.cards):
            return (34, "Три шестерки (34 очка)")
        
        # Проверка на два туза
        aces = [card for card in self.cards if card.rank == 'A']
        if len(aces) >= 2:
            return (22, "Два туза (22 очка)")
        
        # Подсчет по рангам
        rank_counts = {}
        for card in self.cards:
            rank_counts[card.rank] = rank_counts.get(card.rank, 0) + 1
        
        max_rank_score = 0
        best_rank_combination = ""
        for rank, count in rank_counts.items():
            if count >= 2:  # Учитываем пары и тройки
                current_score = Card.RANK_VALUES[rank] * count
                if current_score > max_rank_score:
                    max_rank_score = current_score
                    best_rank_combination = f"{count}x{rank} ({current_score} очков)"
        
        # Подсчет по мастям
        suit_scores = {}
        for card in self.cards:
            suit_scores[card.suit] = suit_scores.get(card.suit, 0) + card.value
        
        max_suit_score = max(suit_scores.values()) if suit_scores else 0
        best_suit = next((suit for suit, score in suit_scores.items() if score == max_suit_score), None)
        best_suit_combination = f"Масть {best_suit} ({max_suit_score} очков)" if best_suit else ""
        
        if max_rank_score > max_suit_score:
            return (max_rank_score, best_rank_combination)
        elif max_suit_score > max_rank_score:
            return (max_suit_score, best_suit_combination)
        else:
            # Если очки равны, предпочтение отдается комбинации по масти
            return (max_suit_score, best_suit_combination or best_rank_combination)

class Game:
    def __init__(self):
        self.players: Dict[int, Player] = {}
        self.deck: List[Card] = []
        self.pot = 0
        self.current_bidder_index = 0
        self.current_max_bet = 0
        self.state = GameState.WAITING_FOR_PLAYERS
        self.bid_history = []
        self.swara_pot = 0
        self.last_raiser: Optional[Player] = None
        self.min_raise = MIN_RAISE
        self.chat_id = None
        self.creator_id = None
    
    @property
    def active_players(self) -> List[Player]:
        return [p for p in self.players.values() if p.can_play]
    
    @property
    def current_player(self) -> Optional[Player]:
        active = self.active_players
        if not active:
            return None
        return active[self.current_bidder_index % len(active)]
    
    def add_player(self, player: Player):
        if len(self.players) >= MAX_PLAYERS:
            raise ValueError("Maximum players reached")
        self.players[player.user_id] = player
    
    def remove_player(self, user_id: int):
        if user_id in self.players:
            del self.players[user_id]
    
    def initialize_deck(self):
        self.deck = [Card(rank, suit) for suit in Card.SUITS for rank in Card.RANKS]
        random.shuffle(self.deck)
    
    def deal_cards(self):
        # Раздаем по 3 карты каждому игроку
        for _ in range(3):
            for player in self.players.values():
                if player.can_play and self.deck:
                    player.cards.append(self.deck.pop())
        
        # Выбираем случайного игрока для игры втемную
        if self.players:
            dark_player = random.choice(list(self.players.values()))
            dark_player.is_dark = True
    
    def collect_ante(self):
        for player in self.players.values():
            if player.bet(ANTE_AMOUNT):
                self.pot += ANTE_AMOUNT
            else:
                player.folded = True
    
    def reset_bidding(self):
        self.current_max_bet = 0
        self.last_raiser = None
        for player in self.players.values():
            player.current_bet = 0
    
    def next_turn(self):
        active = self.active_players
        if not active:
            return False
        
        self.current_bidder_index += 1
        next_player = self.current_player
        
        # Если следующий игрок уже уравнял ставку или это последний повышавший
        if next_player.current_bet >= self.current_max_bet or next_player == self.last_raiser:
            return False  # Конец круга торгов
        
        return True
    
    def end_round(self):
        for player in self.players.values():
            player.reset_round()
        
        self.pot = 0
        self.swara_pot = 0
        self.reset_bidding()
        self.deck.clear()
        self.bid_history.clear()
        self.current_bidder_index = 0
        self.state = GameState.WAITING_FOR_PLAYERS

# Глобальные переменные игры
active_games: Dict[int, Game] = {}  # key: chat_id
user_data_cache = {}

# Вспомогательные функции
async def send_private_message(context: ContextTypes.DEFAULT_TYPE, player: Player, text: str):
    try:
        await context.bot.send_message(chat_id=player.user_id, text=text)
    except Exception as e:
        logger.error(f"Failed to send message to {player.user_id}: {e}")
        return False
    return True

async def notify_all_players(context: ContextTypes.DEFAULT_TYPE, game: Game, text: str):
    for player in game.players.values():
        await send_private_message(context, player, text)

# Команды бота
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if chat_id in active_games:
        await update.message.reply_text("Игра уже идет. Используйте /join чтобы присоединиться.")
        return
    
    game = Game()
    game.chat_id = chat_id
    game.creator_id = user.id
    active_games[chat_id] = game
    
    # Автоматически добавляем создателя в игру
    player = Player(user.id, user.full_name)
    game.add_player(player)
    
    await update.message.reply_text(
        "🎮 Игра Сека начата! Используйте /join чтобы присоединиться.\n"
        f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n"
        "Когда все присоединятся, используйте /ready чтобы отметить готовность."
    )

async def join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if chat_id not in active_games:
        await update.message.reply_text("Нет активной игры. Начните с /start")
        return
    
    game = active_games[chat_id]
    
    if user.id in game.players:
        await update.message.reply_text("Вы уже в игре!")
        return
    
    try:
        player = Player(user.id, user.full_name)
        game.add_player(player)
    except ValueError:
        await update.message.reply_text(f"В игре уже максимальное количество игроков ({MAX_PLAYERS})!")
        return
    
    await update.message.reply_text(
        f"👋 {user.full_name} присоединился к игре!\n"
        f"Игроков: {len(game.players)}/{MAX_PLAYERS}\n"
        "Используйте /ready чтобы отметить готовность."
    )

async def ready(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user = update.effective_user
    
    if chat_id not in active_games:
        await update.message.reply_text("Нет активной игры. Начните с /start")
        return
    
    game = active_games[chat_id]
    
    if user.id not in game.players:
        await update.message.reply_text("Вы не участвуете в текущей игре!")
        return
    
    player = game.players[user.id]
    if player.ready:
        await update.message.reply_text("Вы уже готовы!")
        return
    
    player.ready = True
    
    ready_count = sum(1 for p in game.players.values() if p.ready)
    total_players = len(game.players)
    
    await update.message.reply_text(
        f"✅ {player.name} готов к игре.\n"
        f"Готовы: {ready_count}/{total_players}\n"
        "Ожидаем остальных игроков..."
    )
    
    if ready_count == total_players and total_players >= MIN_PLAYERS:
        await begin_game(update, context)

async def begin_game(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games[chat_id]
    
    if len(game.players) < MIN_PLAYERS:
        await update.message.reply_text(f"Нужно как минимум {MIN_PLAYERS} игрока для начала игры!")
        return
    
    if not all(player.ready for player in game.players.values()):
        await update.message.reply_text("Не все игроки готовы!")
        return
    
    game.state = GameState.COLLECTING_ANTE
    game.collect_ante()
    
    game.state = GameState.DEALING_CARDS
    game.initialize_deck()
    game.deal_cards()
    
    # Отправка карт игрокам в личные сообщения
    for player in game.players.values():
        cards_str = ", ".join(str(card) for card in player.cards)
        score, desc = player.get_hand_value()
        
        message = f"🃏 Ваши карты: {cards_str}\n"
        if player.is_dark:
            message += "👀 Вы играете втемную! Используйте кнопку 'Посмотреть' чтобы увидеть карты.\n"
        else:
            message += f"📊 Комбинация: {desc}\n"
        
        if not await send_private_message(context, player, message):
            await update.effective_chat.send_message(
                f"{player.name}, я не могу отправить вам карты. Пожалуйста, начните диалог с ботом."
            )
            player.folded = True
    
    game.state = GameState.BIDDING
    game.current_max_bet = ANTE_AMOUNT
    
    await send_bidding_options(update, context)

async def send_bidding_options(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games[chat_id]
    
    current_player = game.current_player
    if not current_player:
        await end_round(update, context)
        return
    
    keyboard = []
    
    # Основные действия
    keyboard.append([InlineKeyboardButton("📤 Упасть", callback_data="fold")])
    
    if current_player.current_bet < game.current_max_bet:
        call_amount = game.current_max_bet - current_player.current_bet
        keyboard.append([InlineKeyboardButton(f"📥 Поддержать ({call_amount})", callback_data="call")])
    else:
        keyboard.append([InlineKeyboardButton("✅ Проверить", callback_data="check")])
    
    if current_player.chips >= game.min_raise:
        keyboard.append([InlineKeyboardButton(f"📈 Повысить (+{game.min_raise})", callback_data="raise")])
    
    if game.last_raiser is not None:
        keyboard.append([InlineKeyboardButton("🃏 Вскрыться", callback_data="showdown")])
    
    if current_player.is_dark:
        keyboard.append([InlineKeyboardButton("👀 Посмотреть карты", callback_data="look")])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"🎲 Ход {current_player.name}\n"
                 f"💵 Текущая ставка: {game.current_max_bet}\n"
                 f"💰 Банк: {game.pot}",
            reply_markup=reply_markup
        )
    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения: {e}")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    chat_id = update.effective_chat.id
    user_id = query.from_user.id
    game = active_games.get(chat_id)
    
    if not game:
        await query.edit_message_text("Игра не найдена.")
        return
    
    current_player = game.current_player
    if not current_player or user_id != current_player.user_id:
        await query.answer("Сейчас не ваш ход!", show_alert=True)
        return
    
    action = query.data
    
    if action == "fold":
        current_player.folded = True
        await query.edit_message_text(f"📤 {current_player.name} сбрасывает карты.")
    elif action == "call":
        bet_amount = game.current_max_bet - current_player.current_bet
        if current_player.bet(bet_amount):
            game.pot += bet_amount
            await query.edit_message_text(f"📥 {current_player.name} поддерживает ставку {game.current_max_bet}.")
        else:
            current_player.folded = True
            await query.edit_message_text(f"💸 {current_player.name} не может поддержать ставку и выбывает.")
    elif action == "check":
        await query.edit_message_text(f"✅ {current_player.name} проверяет.")
    elif action == "raise":
        user_data_cache[user_id] = {
            "chat_id": chat_id,
            "message_id": query.message.message_id,
            "action": "raise"
        }
        
        await send_private_message(
            context,
            current_player,
            f"🎯 Текущая ставка: {game.current_max_bet}\n"
            f"Ваша текущая ставка: {current_player.current_bet}\n"
            f"Введите сумму повышения (минимальное: {game.min_raise}):"
        )
        return
    elif action == "showdown":
        await query.edit_message_text(f"🃏 {current_player.name} требует вскрытия карт!")
        await compare_hands(update, context)
        return
    elif action == "look":
        current_player.is_dark = False
        cards_str = ", ".join(str(card) for card in current_player.cards)
        score, desc = current_player.get_hand_value()
        await send_private_message(
            context,
            current_player,
            f"👀 Вы больше не играете втемную.\n"
            f"Ваши карты: {cards_str}\n"
            f"Комбинация: {desc}"
        )
        await query.edit_message_text(f"👀 {current_player.name} посмотрел свои карты.")
    
    # Переход хода или завершение круга торгов
    if not game.next_turn():
        game.reset_bidding()
        game.current_bidder_index = 0
    
    # Проверка на окончание торгов
    if len(game.active_players) <= 1:
        await end_round(update, context)
        return
    
    await send_bidding_options(update, context)

async def handle_raise_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data_cache:
        return
    
    try:
        raise_amount = int(update.message.text)
    except ValueError:
        await update.message.reply_text("Пожалуйста, введите целое число.")
        return
    
    data = user_data_cache[user_id]
    chat_id = data["chat_id"]
    game = active_games.get(chat_id)
    
    if not game or user_id not in game.players:
        await update.message.reply_text("Игра не найдена или вы не участник.")
        return
    
    player = game.players[user_id]
    min_raise = game.min_raise
    call_amount = game.current_max_bet - player.current_bet
    total_needed = call_amount + min_raise
    
    if raise_amount < min_raise:
        await update.message.reply_text(
            f"Минимальное повышение: {min_raise}. Попробуйте еще раз."
        )
        return
    
    total_bet = player.current_bet + call_amount + raise_amount
    
    if not player.bet(call_amount + raise_amount):
        await update.message.reply_text(
            f"У вас недостаточно фишек. Доступно: {player.chips + player.current_bet}"
        )
        return
    
    game.pot += call_amount + raise_amount
    game.current_max_bet = total_bet
    game.last_raiser = player
    
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"📈 {player.name} повышает ставку до {total_bet}!"
    )
    
    # Переход хода или завершение круга торгов
    if not game.next_turn():
        game.reset_bidding()
        game.current_bidder_index = 0
    
    # Проверка на окончание торгов
    if len(game.active_players) <= 1:
        await end_round(update, context)
        return
    
    await send_bidding_options(update, context)

async def compare_hands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games[chat_id]
    active_players = game.active_players
    
    if not active_players:
        await end_round(update, context)
        return
    
    if len(active_players) == 1:
        winner = active_players[0]
        await update.effective_chat.send_message(
            f"🏆 {winner.name} выигрывает банк в размере {game.pot}!"
        )
        winner.chips += game.pot
        await prepare_new_round(update, context)
        return
    
    # Оцениваем комбинации всех активных игроков
    results = []
    for player in active_players:
        score, desc = player.get_hand_value()
        cards_str = ", ".join(str(card) for card in player.cards)
        results.append((player, score, desc, cards_str))
    
    # Сортируем по убыванию очков
    results.sort(key=lambda x: x[1], reverse=True)
    
    # Формируем сообщение с результатами
    message = "📊 Результаты:\n"
    for i, (player, score, desc, cards) in enumerate(results, 1):
        message += f"\n{i}. {player.name}: {cards} - {desc}"
    
    # Проверяем на ничью (свара)
    if len(results) > 1 and results[0][1] == results[1][1]:
        winners = [r[0] for r in results if r[1] == results[0][1]]
        winner_names = ", ".join(w.name for w in winners)
        
        await update.effective_chat.send_message(
            f"{message}\n\n⚔ Ничья между {winner_names}! Начинается свара."
        )
        
        game.state = GameState.SWARA
        game.swara_pot = game.pot
        game.pot = 0
        
        # Каждый участник свары делает дополнительную ставку
        for player in winners[:]:  # Используем копию для безопасного удаления
            if player.bet(ANTE_AMOUNT):
                game.pot += ANTE_AMOUNT
            else:
                winners.remove(player)
        
        if len(winners) >= 2:
            # Раздаем по одной дополнительной карте
            for player in winners:
                if game.deck:
                    player.cards.append(game.deck.pop())
            
            # Отправляем новые карты игрокам
            for player in winners:
                cards_str = ", ".join(str(card) for card in player.cards)
                score, desc = player.get_hand_value()
                await send_private_message(
                    context,
                    player,
                    f"🃏 Ваши карты после свары: {cards_str}\n"
                    f"📊 Комбинация: {desc}"
                )
            
            # Начинаем торги для свары
            game.current_bidder_index = 0
            game.current_max_bet = 0
            game.last_raiser = None
            await update.effective_chat.send_message(
                "🎲 Торги в сваре начинаются. Первый ход у первого игрока."
            )
            await send_bidding_options(update, context)
        else:
            # Только один игрок остался в сваре
            winner = winners[0]
            total_pot = game.swara_pot + game.pot
            winner.chips += total_pot
            await update.effective_chat.send_message(
                f"🏆 {winner.name} выигрывает свару и получает {total_pot}!"
            )
            await prepare_new_round(update, context)
    else:
        # Есть явный победитель
        winner = results[0][0]
        winner.chips += game.pot
        await update.effective_chat.send_message(
            f"{message}\n\n🏆 {winner.name} выигрывает банк в размере {game.pot}!"
        )
        await prepare_new_round(update, context)

async def prepare_new_round(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    game = active_games[chat_id]
    
    # Удаляем игроков без фишек
    for player_id in list(game.players.keys()):
        if game.players[player_id].chips <= 0:
            player_name = game.players[player_id].name
            game.remove_player(player_id)
            await update.effective_chat.send_message(
                f"💸 {player_name} выбывает из игры из-за отсутствия фишек."
            )
    
    if len(game.players) < MIN_PLAYERS:
        await update.effective_chat.send_message(
            f"Недостаточно игроков ({len(game.players)}/{MIN_PLAYERS}). Игра завершена.\n"
            "Используйте /start чтобы начать новую игру."
        )
        del active_games[chat_id]
        return
    
    # Подготовка к новому раунду
    game.end_round()
    
    await update.effective_chat.send_message(
        "🔄 Раунд завершен. Используйте /ready чтобы отметить готовность к следующему раунду.\n"
        f"Игроков: {len(game.players)}/{MAX_PLAYERS}"
    )

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id in active_games:
        del active_games[chat_id]
    await update.message.reply_text("❌ Игра отменена.")

async def show_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if chat_id not in active_games:
        await update.message.reply_text("Нет активной игры.")
        return
    
    game = active_games[chat_id]
    balance_text = "\n".join(
        f"• {player.name}: {player.chips} фишек"
        for player in game.players.values()
    )
    
    await update.message.reply_text(
        "💰 Балансы игроков:\n" + balance_text
    )

async def rules(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rules_text = (
        "📖 Правила игры Сека:\n\n"
        "🎴 Каждый игрок получает 3 карты\n"
        "👀 Один игрок играет втемную (не видит свои карты)\n"
        "💰 Стартовая ставка (анте) - 10 фишек\n\n"
        "🔄 Ходы игроков:\n"
        "📤 Упасть - сбросить карты и выйти из раунда\n"
        "📥 Поддержать - уравнять текущую ставку\n"
        "📈 Повысить - увеличить ставку\n"
        "✅ Проверить - пропустить ход (если ставка уравнена)\n"
        "🃏 Вскрыться - завершить торги и сравнить карты\n\n"
        "🏆 Комбинации:\n"
        "• Три шестерки: 34 очка\n"
        "• Два туза: 22 очка\n"
        "• Комбинация по масти: сумма очков карт одной масти\n"
        "• Комбинация по рангу: сумма очков карт одного ранга\n\n"
        "⚔ При ничье объявляется свара (дополнительный раунд)\n\n"
        "🔄 Используйте /start чтобы начать игру!"
    )
    await update.message.reply_text(rules_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🛠 Доступные команды:\n\n"
        "🎮 /start - начать новую игру\n"
        "👋 /join - присоединиться к игре\n"
        "✅ /ready - отметить готовность\n"
        "💰 /balance - показать балансы\n"
        "📖 /rules - показать правила\n"
        "❌ /cancel - отменить игру\n\n"
        "🎲 Во время игры используйте кнопки для совершения действий."
    )
    await update.message.reply_text(help_text)

def main():
    # Замените 'YOUR_BOT_TOKEN' на реальный токен вашего бота
    application = Application.builder().token("6939360001:AAFI3w7MzpR-10314IstaCQwChx5ByFvMhk").build()
    
    # Обработчики команд
    command_handlers = {
        'start': start,
        'join': join,
        'ready': ready,
        'begin': begin_game,
        'balance': show_balance,
        'rules': rules,
        'help': help_command,
        'cancel': cancel
    }
    
    for command, handler in command_handlers.items():
        application.add_handler(CommandHandler(command, handler))
    
    # Обработчики сообщений
    application.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        handle_raise_amount
    ))
    
    # Обработчики callback-запросов (кнопки)
    application.add_handler(CallbackQueryHandler(button_handler))
    
    # Запуск бота
    application.run_polling()

if __name__ == '__main__':
    main()
