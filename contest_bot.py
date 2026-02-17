import os
import json
import random
import asyncio
from datetime import datetime, timedelta
import pytz
from aiogram import Bot, Dispatcher, types, F
from aiogram.types import FSInputFile
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

load_dotenv()

# Конфигурация
BOT_TOKEN = os.getenv('BOT_TOKEN')
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")
DATA_DIR = "data"
os.makedirs(DATA_DIR, exist_ok=True)
USERS_FILE = os.path.join(DATA_DIR, "users.json")
CONTESTS_FILE = os.path.join(DATA_DIR, "contests.json")
PHOTOS_FILE = os.path.join(DATA_DIR, "photos.json")
BOT_USERNAME = None

# Московское время
MOSCOW_TZ = pytz.timezone('Europe/Moscow')

# Инициализация бота
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# Модели данных
class User:
    def __init__(self, user_id: int, username: str = ""):
        self.id = user_id
        self.username = username
        self.is_admin = False
        self.invited_by = None
        self.invited_users = []

    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "is_admin": self.is_admin,
            "invited_by": self.invited_by,
            "invited_users": self.invited_users
        }

class Contest:
    def __init__(self, name: str, start_time: str, end_time: str, creator_id: int, photo_path: str = None):
        self.id = f"k{random.randint(1000000, 9999999)}"
        self.name = name
        self.start_time = start_time
        self.end_time = end_time
        self.creator_id = creator_id
        self.photo_path = photo_path
        self.participants = {}

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "creator_id": self.creator_id,
            "photo_path": self.photo_path,
            "participants": self.participants
        }

# Состояния FSM
class ContestCreation(StatesGroup):
    NAME = State()
    START_TIME = State()
    END_TIME = State()
    PHOTO = State()

class AdminActions(StatesGroup):
    ADD_ADMIN = State()
    DELETE_CONTEST = State()
    BROADCAST = State()  # Новое состояние для рассылки

class PhotoManagement(StatesGroup):
    SELECT_TYPE = State()
    WAITING_PHOTO = State()

# Утилиты для работы с данными
def load_photos() -> dict:
    if not os.path.exists(PHOTOS_FILE):
        return {"start": None, "profile": None, "contests": None}
    try:
        with open(PHOTOS_FILE, "r") as f:
            return json.load(f)
    except:
        return {"start": None, "profile": None, "contests": None}

def save_photos(photos: dict):
    with open(PHOTOS_FILE, "w") as f:
        json.dump(photos, f, indent=2)

def load_users() -> dict[int, User]:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r") as f:
            data = json.load(f)
            users = {}
            for user_id, user_data in data.items():
                user = User(int(user_id), user_data['username'])
                user.is_admin = user_data['is_admin']
                user.invited_by = user_data.get('invited_by')
                user.invited_users = user_data.get('invited_users', [])
                users[int(user_id)] = user
            return users
    except Exception as e:
        print(f"Error loading users: {e}")
        return {}

def load_contests() -> dict[str, Contest]:
    if not os.path.exists(CONTESTS_FILE):
        return {}
    try:
        with open(CONTESTS_FILE, "r") as f:
            contests_data = json.load(f)
            contests = {}
            for contest_id, contest_data in contests_data.items():
                try:
                    contest = Contest(
                        contest_data['name'],
                        contest_data['start_time'],
                        contest_data['end_time'],
                        contest_data['creator_id'],
                        contest_data.get('photo_path')
                    )
                    contest.id = contest_id
                    contest.participants = {int(k): v for k, v in contest_data['participants'].items()}
                    
                    # Нормализация времени при загрузке
                    contest.start_time = normalize_time(contest.start_time)
                    contest.end_time = normalize_time(contest.end_time)
                    
                    contests[contest_id] = contest
                except Exception as e:
                    print(f"Error loading contest {contest_id}: {e}")
            return contests
    except Exception as e:
        print(f"Error loading contests: {e}")
        return {}

def save_users(users: dict[int, User]):
    with open(USERS_FILE, "w") as f:
        data = {str(k): v.to_dict() for k, v in users.items()}
        json.dump(data, f, indent=2)

def save_contests(contests: dict[str, Contest]):
    with open(CONTESTS_FILE, "w") as f:
        data = {k: v.to_dict() for k, v in contests.items()}
        json.dump(data, f, indent=2)

def get_active_contests() -> list[Contest]:
    now = datetime.now(MOSCOW_TZ)
    active = []
    for contest in contests.values():
        try:
            start_time = MOSCOW_TZ.localize(datetime.strptime(contest.start_time, "%H:%M %d.%m.%Y"))
            end_time = MOSCOW_TZ.localize(datetime.strptime(contest.end_time, "%H:%M %d.%m.%Y"))
            if start_time <= now <= end_time:
                active.append(contest)
        except Exception as e:
            print(f"Ошибка обработки времени конкурса {contest.id}: {e}")
    return active


def format_user_profile(user: User) -> str:
    user_contests = []
    for contest in contests.values():
        if user.id in contest.participants:
            try:
                start_time = datetime.strptime(contest.start_time, "%H:%M %d.%m.%Y").replace(tzinfo=MOSCOW_TZ)
                end_time = datetime.strptime(contest.end_time, "%H:%M %d.%m.%Y").replace(tzinfo=MOSCOW_TZ)
                now = datetime.now(MOSCOW_TZ)
                if start_time <= now <= end_time:
                    user_contests.append(contest)
            except:
                continue
    
    contests_str = "\n".join([
        f"• {c.name} - {c.participants[user.id]} билетов"
        for c in user_contests
    ]) if user_contests else "Нет активных конкурсов"
    
    return (
        f"🗂 Профиль\n\n"
        f"🆔 ID: {user.id}\n"
        f"ℹ️ Имя: {user.username}\n\n"
        f"💎 Приглашено друзей: {len(user.invited_users)}\n\n"
        f"🏆 Ваши конкурсы:\n{contests_str}"
    )

def create_time_keyboard():
    builder = InlineKeyboardBuilder()
    builder.add(types.InlineKeyboardButton(
        text="Сейчас",
        callback_data="set_time_now"
    ))
    return builder.as_markup()

def main_menu_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="🗂 Профиль", callback_data="profile"),
        types.InlineKeyboardButton(text="🎯 Конкурсы", callback_data="contests")
    )
    return builder.as_markup()

def contests_list_keyboard(active_contests: list[Contest]):
    builder = InlineKeyboardBuilder()
    
    if active_contests:
        for contest in active_contests:
            builder.row(types.InlineKeyboardButton(
                text=contest.name,
                callback_data=f"contest_{contest.id}"
            ))
    
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Назад", 
        callback_data="main_menu"
    ))
    return builder.as_markup()

def back_to_main_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Назад", 
        callback_data="main_menu"
    ))
    return builder.as_markup()

def copy_link_keyboard(link: str):
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="📋 Копировать ссылку",
        callback_data=f"copy_{link}"
    ))
    return builder.as_markup()

def delete_contest_keyboard():
    builder = InlineKeyboardBuilder()
    active_contests = get_active_contests()
    
    if active_contests:
        for contest in active_contests:
            builder.row(types.InlineKeyboardButton(
                text=f"❌ Удалить {contest.name}",
                callback_data=f"delete_contest_{contest.id}"
            ))
    
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Отмена", 
        callback_data="admin_menu"
    ))
    return builder.as_markup()

def photo_management_keyboard():
    builder = InlineKeyboardBuilder()
    builder.row(types.InlineKeyboardButton(
        text="📸 Старт (/start)",
        callback_data="set_start_photo"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🖼 Профиль",
        callback_data="set_profile_photo"
    ))
    builder.row(types.InlineKeyboardButton(
        text="🏆 Конкурсы",
        callback_data="set_contests_photo"
    ))
    builder.row(types.InlineKeyboardButton(
        text="⬅️ Отмена",
        callback_data="admin_menu"
    ))
    return builder.as_markup()

def normalize_time(time_str: str) -> str:
    parts = time_str.split()
    if len(parts) < 2:
        return time_str
        
    time_part = parts[0]
    date_part = ' '.join(parts[1:])
    
    if ':' in time_part:
        time_parts = time_part.split(':')
        hours = time_parts[0].zfill(2)
        minutes = time_parts[1].zfill(2) if len(time_parts) > 1 else "00"
        time_part = f"{hours}:{minutes}"
    
    return f"{time_part} {date_part}"

# Функция для отображения главного меню
async def show_main_menu(user_id: int):
    welcome_text = (
        "👋Добро пожаловать в НЕТВОРКИНГ HUB BOT 🌐\n\n"
        "🔥В этом боте можно поучаствовать в конкурсах, проводимых медиа-проектом"
    )
    
    start_photo_path = photos_config.get("start")
    
    # Удаляем предыдущее сообщение, если есть
    if user_id in user_messages:
        try:
            await bot.delete_message(user_id, user_messages[user_id])
        except:
            pass
    
    try:
        if start_photo_path and os.path.exists(start_photo_path):
            msg = await bot.send_photo(
                chat_id=user_id,
                photo=types.FSInputFile(start_photo_path),
                caption=welcome_text,
                reply_markup=main_menu_keyboard()
            )
            user_messages[user_id] = msg.message_id
        else:
            msg = await bot.send_message(
                user_id,
                welcome_text,
                reply_markup=main_menu_keyboard()
            )
            user_messages[user_id] = msg.message_id
    except Exception as e:
        msg = await bot.send_message(
            user_id,
            welcome_text,
            reply_markup=main_menu_keyboard()
        )
        user_messages[user_id] = msg.message_id

# Инициализация данных
users = load_users()
contests = load_contests()
photos_config = load_photos()

# Добавляем админа по умолчанию
if 1234435 not in users:
    admin_user = User(1234435, "Admin")
    admin_user.is_admin = True
    users[1234435] = admin_user
    save_users(users)

# Состояние сообщений пользователя
user_messages = {}

# Обработчики команд
@dp.message(Command("start"))
async def start_cmd(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name

    if user_id not in users:
        users[user_id] = User(user_id, username)
    else:
        if users[user_id].username != username:
            users[user_id].username = username
    
    # Обработка реферальной ссылки
    contest_registered = None
    ref_link = None
    
    if len(message.text.split()) > 1:
        args = message.text.split()[1]
        if args.startswith('k') and 'ref' in args:
            contest_id_part, ref_id_part = args.split('ref', 1)
            contest_id = contest_id_part
            try:
                ref_id = int(ref_id_part)
                if contest_id in contests and ref_id in users:
                    contest = contests[contest_id]
                    
                    if user_id not in contest.participants:
                        contest.participants[user_id] = 1
                        
                        if ref_id != user_id:
                            contest.participants[ref_id] = contest.participants.get(ref_id, 0) + 1
                            if user_id not in users[ref_id].invited_users:
                                users[ref_id].invited_users.append(user_id)
                            users[user_id].invited_by = ref_id
                        
                        save_contests(contests)
                        save_users(users)
                        contest_registered = contest
                        ref_link = f"https://t.me/{BOT_USERNAME}?start={contest_id}ref{user_id}"
            except ValueError:
                pass

    if contest_registered and ref_link:
        await message.answer(
            f"🎉 Вы участвуете в конкурсе '{contest_registered.name}'!\n"
            f"Приглашайте друзей по вашей ссылке:\n"
            f"<code>{ref_link}</code>\n\n"
            "Нажмите на ссылку, чтобы скопировать",
            parse_mode="HTML"
        )

    # Показываем главное меню
    await show_main_menu(user_id)

# Общая функция для обновления сообщений
async def update_user_message(user_id: int, text: str, reply_markup: types.InlineKeyboardMarkup):
    try:
        await bot.edit_message_text(
            chat_id=user_id,
            message_id=user_messages[user_id],
            text=text,
            reply_markup=reply_markup
        )
    except:
        try:
            await bot.delete_message(user_id, user_messages[user_id])
        except:
            pass
        new_msg = await bot.send_message(
            chat_id=user_id,
            text=text,
            reply_markup=reply_markup
        )
        user_messages[user_id] = new_msg.message_id

# Обработчики callback-запросов
@dp.callback_query(F.data == "main_menu")
async def main_menu_callback(callback: types.CallbackQuery):
    await show_main_menu(callback.from_user.id)

@dp.callback_query(F.data == "profile")
async def profile_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    user = users.get(user_id)
    if not user:
        return
    
    profile_text = format_user_profile(user)
    profile_photo_path = photos_config.get("profile")
    
    if profile_photo_path and os.path.exists(profile_photo_path):
        try:
            await bot.delete_message(user_id, user_messages[user_id])
        except:
            pass
            
        msg = await bot.send_photo(
            chat_id=user_id,
            photo=FSInputFile(profile_photo_path),
            caption=profile_text,
            reply_markup=back_to_main_keyboard()
        )
        user_messages[user_id] = msg.message_id
    else:
        await update_user_message(
            user_id,
            profile_text,
            back_to_main_keyboard()
        )

@dp.callback_query(F.data == "contests")
async def contests_callback(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    active_contests = get_active_contests()
    text = "🎯 Активные конкурсы:" if active_contests else "🚫 Сейчас нет активных конкурсов"
    await update_user_message(
        user_id,
        text,
        contests_list_keyboard(active_contests)
    )

@dp.callback_query(F.data.startswith("contest_"))
async def contest_detail_callback(callback: types.CallbackQuery):
    contest_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    if contest_id in contests:
        contest = contests[contest_id]
        tickets = contest.participants.get(user_id, 0)
        ref_link = f"https://t.me/{BOT_USERNAME}?start={contest_id}ref{user_id}"
        text = (
            f"🏆 {contest.name}\n\n"
            f"🕒 Начало: {contest.start_time}\n"
            f"🕓 Окончание: {contest.end_time}\n\n"
            f"🎫 Ваши билеты: {tickets}\n\n"
            f"🔗 Пригласите друзей по ссылке:\n"
            f"{ref_link}\n\n"
            f"Нажмите на ссылку, чтобы скопировать"
        )

        
        if contest.photo_path and os.path.exists(contest.photo_path):
            try:
                await bot.delete_message(user_id, user_messages[user_id])
            except:
                pass
                
            msg = await bot.send_photo(
                chat_id=user_id,
                photo=FSInputFile(contest.photo_path),
                caption=text,
                reply_markup=back_to_main_keyboard(),
                parse_mode="HTML"
            )
            user_messages[user_id] = msg.message_id
        else:
            await update_user_message(
                user_id,
                text,
                back_to_main_keyboard()
            )

@dp.callback_query(F.data.startswith("copy_"))
async def copy_link_callback(callback: types.CallbackQuery):
    await callback.answer("Ссылка скопирована! Теперь вы можете поделиться ею", show_alert=False)

@dp.message(Command("admin"))
async def admin_menu(message: types.Message):
    user_id = message.from_user.id
    if user_id in users and users[user_id].is_admin:
        builder = InlineKeyboardBuilder()
        builder.row(types.InlineKeyboardButton(
            text="🎯 Создать конкурс",
            callback_data="create_contest")
        )
        builder.row(types.InlineKeyboardButton(
            text="❌ Удалить конкурс",
            callback_data="delete_contest")
        )
        builder.row(types.InlineKeyboardButton(
            text="👑 Добавить админа",
            callback_data="add_admin")
        )
        builder.row(types.InlineKeyboardButton(
            text="🖼 Управление фото",
            callback_data="manage_photos")
        )
        builder.row(types.InlineKeyboardButton(
            text="📢 Сделать рассылку",
            callback_data="broadcast")
        )
        await message.answer("⚙️ Админ-панель:", reply_markup=builder.as_markup())

# Управление конкурсами - удаление
@dp.callback_query(F.data == "delete_contest")
async def delete_contest_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminActions.DELETE_CONTEST)
    await callback.message.answer(
        "Выберите конкурс для удаления:",
        reply_markup=delete_contest_keyboard()
    )

@dp.callback_query(AdminActions.DELETE_CONTEST, F.data.startswith("delete_contest_"))
async def process_delete_contest(callback: types.CallbackQuery, state: FSMContext):
    contest_id = callback.data.split("_")[2]
    if contest_id in contests:
        contest_name = contests[contest_id].name
        if contests[contest_id].photo_path and os.path.exists(contests[contest_id].photo_path):
            try:
                os.remove(contests[contest_id].photo_path)
            except:
                pass
        
        del contests[contest_id]
        save_contests(contests)
        
        # Обновляем клавиатуру после удаления
        await callback.message.edit_reply_markup(reply_markup=delete_contest_keyboard())
        await callback.answer(f"✅ Конкурс '{contest_name}' удален!")
    else:
        await callback.answer("❌ Конкурс не найден")
    
    # Не очищаем состояние, чтобы можно было удалять несколько конкурсов подряд

# Создание конкурса
@dp.callback_query(F.data == "create_contest")
async def create_contest_start(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(ContestCreation.NAME)
    await callback.message.answer("Введите название конкурса:")

@dp.message(ContestCreation.NAME)
async def process_contest_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await state.set_state(ContestCreation.START_TIME)
    
    time_example = "<code>21:11 06.08.2025</code>"
    await message.answer(
        f"🕔 Введите время начала в формате: {time_example} (Московское время)",
        reply_markup=create_time_keyboard(),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "set_time_now")
async def set_time_now(callback: types.CallbackQuery, state: FSMContext):
    current_time = datetime.now(MOSCOW_TZ).strftime("%H:%M %d.%m.%Y")
    formatted_time = f"<code>{current_time}</code>"
    
    await state.update_data(start_time=current_time)
    await state.set_state(ContestCreation.END_TIME)
    await callback.message.answer(
        f"Время установлено: {formatted_time}\n"
        f"Введите время окончания в формате: <code>21:11 06.08.2025</code> (Московское время)",
        parse_mode="HTML"
    )

@dp.message(ContestCreation.START_TIME)
async def process_custom_start_time(message: types.Message, state: FSMContext):
    normalized_time = normalize_time(message.text)
    await state.update_data(start_time=normalized_time)
    await state.set_state(ContestCreation.END_TIME)
    
    time_example = "<code>21:11 06.08.2025</code>"
    await message.answer(
        f"Введите время окончания в формате: {time_example} (Московское время)",
        parse_mode="HTML"
    )

@dp.message(ContestCreation.END_TIME)
async def process_end_time(message: types.Message, state: FSMContext):
    data = await state.get_data()
    end_time = normalize_time(message.text)
    start_time = data.get('start_time', datetime.now(MOSCOW_TZ).strftime("%H:%M %d.%m.%Y"))
    
    await state.update_data(
        start_time=start_time,
        end_time=end_time
    )
    await state.set_state(ContestCreation.PHOTO)
    await message.answer(
        "📸 Отправьте фото для конкурса (или нажмите /skip чтобы пропустить)"
    )

@dp.message(ContestCreation.PHOTO, F.photo)
async def process_contest_photo(message: types.Message, state: FSMContext):
    photo_id = message.photo[-1].file_id
    file_info = await bot.get_file(photo_id)
    ext = file_info.file_path.split('.')[-1] if '.' in file_info.file_path else 'jpg'
    photo_path = os.path.join(DATA_DIR, f"contest_{int(datetime.now().timestamp())}.{ext}")
    
    await bot.download_file(file_info.file_path, photo_path)
    
    data = await state.get_data()
    contest = Contest(
        name=data['name'],
        start_time=data['start_time'],
        end_time=data['end_time'],
        creator_id=message.from_user.id,
        photo_path=photo_path
    )
    
    contests[contest.id] = contest
    contest.participants[message.from_user.id] = 1
    save_contests(contests)
    
    ref_link = f"https://t.me/{BOT_USERNAME}?start={contest.id}ref{message.from_user.id}"
    
    await state.clear()
    
    await message.answer_photo(
        photo=FSInputFile(photo_path),
        caption=(
            f"🎉 Конкурс создан!\n"
            f"Название: {contest.name}\n"
            f"ID: {contest.id}\n\n"
            f"🔗 Ссылка для приглашения:\n"
            f"<code>{ref_link}</code>\n\n"
            f"Нажмите на ссылку, чтобы скопировать"
        ),
        parse_mode="HTML"
    )

@dp.message(ContestCreation.PHOTO, Command("skip"))
async def skip_contest_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    contest = Contest(
        name=data['name'],
        start_time=data['start_time'],
        end_time=data['end_time'],
        creator_id=message.from_user.id
    )
    
    contests[contest.id] = contest
    contest.participants[message.from_user.id] = 1
    save_contests(contests)
    
    ref_link = f"https://t.me/{BOT_USERNAME}?start={contest.id}ref{message.from_user.id}"
    
    await state.clear()
    
    await message.answer(
        f"🎉 Конкурс создан!\n"
        f"Название: {contest.name}\n"
        f"ID: {contest.id}\n\n"
        f"🔗 Ссылка для приглашения:\n"
        f"<code>{ref_link}</code>\n\n"
        f"Нажмите на ссылку, чтобы скопировать",
        parse_mode="HTML"
    )

# Управление админами
@dp.callback_query(F.data == "add_admin")
async def add_admin_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminActions.ADD_ADMIN)
    await callback.message.answer("Введите ID пользователя для назначения админом:")

@dp.message(AdminActions.ADD_ADMIN)
async def process_add_admin(message: types.Message, state: FSMContext):
    try:
        new_admin_id = int(message.text)
        if new_admin_id in users:
            users[new_admin_id].is_admin = True
            save_users(users)
            await message.answer(f"✅ Пользователь {new_admin_id} теперь администратор!")
        else:
            await message.answer("❌ Пользователь не найден. Попросите его сначала запустить бота.")
    except ValueError:
        await message.answer("❌ Неверный формат ID. Введите числовой ID.")
    await state.clear()

# Управление фото
@dp.callback_query(F.data == "manage_photos")
async def manage_photos_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(PhotoManagement.SELECT_TYPE)
    await callback.message.answer(
        "Выберите тип фото для загрузки:",
        reply_markup=photo_management_keyboard()
    )

@dp.callback_query(PhotoManagement.SELECT_TYPE, F.data.startswith("set_"))
async def select_photo_type(callback: types.CallbackQuery, state: FSMContext):
    photo_type = callback.data.split('_')[1]
    await state.update_data(photo_type=photo_type)
    await state.set_state(PhotoManagement.WAITING_PHOTO)
    await callback.message.answer(f"Отправьте фото для {photo_type} (или /skip для сброса)")

@dp.message(PhotoManagement.WAITING_PHOTO, F.photo)
async def receive_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photo_type = data['photo_type']
    
    photo_id = message.photo[-1].file_id
    file_info = await bot.get_file(photo_id)
    ext = file_info.file_path.split('.')[-1] if '.' in file_info.file_path else 'jpg'
    photo_name = f"{photo_type}_photo.{ext}"
    photo_path = os.path.join(DATA_DIR, photo_name)
    
    await bot.download_file(file_info.file_path, photo_path)
    
    # Удаляем старое фото
    old_photo = photos_config.get(photo_type)
    if old_photo and os.path.exists(old_photo):
        try:
            os.remove(old_photo)
        except:
            pass
    
    photos_config[photo_type] = photo_path
    save_photos(photos_config)
    
    await message.answer(f"✅ Фото для {photo_type} успешно установлено!")
    await state.clear()

@dp.message(PhotoManagement.WAITING_PHOTO, Command("skip"))
async def skip_photo(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photo_type = data['photo_type']
    
    if photo_type in photos_config:
        old_photo = photos_config[photo_type]
        if old_photo and os.path.exists(old_photo):
            try:
                os.remove(old_photo)
            except:
                pass
        photos_config[photo_type] = None
        save_photos(photos_config)
    
    await message.answer(f"✅ Фото для {photo_type} сброшено.")
    await state.clear()

# Система розыгрыша
async def check_contests_periodically():
    while True:
        now = datetime.now(MOSCOW_TZ)
        for contest_id, contest in list(contests.items()):
            try:
                end_time = datetime.strptime(contest.end_time, "%H:%M %d.%m.%Y").replace(tzinfo=MOSCOW_TZ)
                
                # Если конкурс только что закончился (1 минута назад или меньше)
                if end_time <= now <= end_time + timedelta(minutes=1) and not hasattr(contest, 'results_sent'):
                    # Помечаем конкурс как завершенный
                    contest.results_sent = True
                    save_contests(contests)
                    
                    # Ждем 1 минуту перед подведением итогов
                    await asyncio.sleep(60)
                    
                    # Определяем победителя
                    participants = list(contest.participants.keys())
                    winner_id = random.choice(participants) if participants else 0
                    
                    # Формируем сообщение для участников
                    winner_message = (
                        f"🏆 Конкурс завершен: {contest.name}\n\n"
                        f"🎉 Победитель: {format_username(winner_id)}\n\n"
                        f"Спасибо за участие!"
                    )
                    
                    # Формируем детальный отчет для админов
                    admin_report = (
                        f"🏆 Конкурс завершен: {contest.name}\n"
                        f"👥 Участников: {len(participants)}\n\n"
                        f"📊 Результаты:\n"
                    )
                    
                    # Добавляем список участников с количеством билетов
                    for uid, tickets in contest.participants.items():
                        admin_report += f"• {format_username(uid)} - {tickets} билетов\n"
                    
                    admin_report += f"\n🎉 Победитель: {format_username(winner_id)}"
                    
                    # Отправляем уведомления участникам
                    for participant_id in participants:
                        try:
                            if contest.photo_path and os.path.exists(contest.photo_path):
                                await bot.send_photo(
                                    participant_id,
                                    photo=FSInputFile(contest.photo_path),
                                    caption=winner_message
                                )
                            else:
                                await bot.send_message(
                                    participant_id,
                                    winner_message
                                )
                        except Exception as e:
                            print(f"Не удалось отправить уведомление участнику {participant_id}: {e}")
                    
                    # Отправляем отчет админам
                    for user in users.values():
                        if user.is_admin:
                            try:
                                if contest.photo_path and os.path.exists(contest.photo_path):
                                    await bot.send_photo(
                                        user.id,
                                        photo=FSInputFile(contest.photo_path),
                                        caption=admin_report
                                    )
                                else:
                                    await bot.send_message(
                                        user.id,
                                        admin_report
                                    )
                            except Exception as e:
                                print(f"Не удалось отправить отчет админу {user.id}: {e}")
                    
                    # Удаляем конкурс после отправки уведомлений
                    if contest.photo_path and os.path.exists(contest.photo_path):
                        try:
                            os.remove(contest.photo_path)
                        except:
                            pass
                    
                    del contests[contest_id]
                    save_contests(contests)
                    
            except Exception as e:
                print(f"Ошибка при обработке конкурса {contest_id}: {e}")
                if contest_id in contests:
                    del contests[contest_id]
                    save_contests(contests)
        
        await asyncio.sleep(10)

def format_username(user_id):
    if user_id == 0:
        return "❌ Нет участников"
    if user_id in users:
        user = users[user_id]
        return f"@{user.username}" if user.username else f"ID:{user_id}"
    return f"ID:{user_id}"
# Рассылка сообщений
@dp.callback_query(F.data == "broadcast")
async def broadcast_callback(callback: types.CallbackQuery, state: FSMContext):
    await state.set_state(AdminActions.BROADCAST)
    await callback.message.answer(
        "Отправьте сообщение для рассылки (текст, фото или документ):"
    )

@dp.message(AdminActions.BROADCAST, F.text | F.photo | F.document)
@dp.message(AdminActions.BROADCAST)
async def process_broadcast_message(message: types.Message, state: FSMContext):
    total_users = len(users)
    await message.answer(f"📢 Рассылка для {total_users} пользователей началась...")

    success, fail = 0, 0
    for user_id in users.keys():
        try:
            if message.photo:
                await bot.send_photo(user_id, message.photo[-1].file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.document:
                await bot.send_document(user_id, message.document.file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.video:
                await bot.send_video(user_id, message.video.file_id, caption=message.caption or "", parse_mode="HTML")
            elif message.text:
                await bot.send_message(user_id, message.text, parse_mode="HTML")
            success += 1
        except:
            fail += 1
        await asyncio.sleep(0.05)

    await message.answer(f"✅ Рассылка завершена!\nУспешно: {success}\n❌ Не доставлено: {fail}")
    await state.clear()


# Запуск бота
async def main():
    global BOT_USERNAME
    bot_info = await bot.get_me()
    BOT_USERNAME = bot_info.username
    print(f"Бот запущен: @{BOT_USERNAME}")
    
    asyncio.create_task(check_contests_periodically())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())