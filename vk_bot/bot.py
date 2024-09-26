from pprint import pprint

import vk_api
import re
from vk_api.longpoll import VkEventType, VkLongPoll

from database.db_funcs import (
    BlackListDBManager, FavoritesDBManager, UserDBManager
)
from settings import COMMANDS, KEYBOARDS, MESSAGES
from vk_bot import UserInfoRetriever
from vk_bot.keyboard import VKKeyboard
from vk_bot.search_paginator import Paginator

VK_URL_PATTERN = r"https://vk\.com/id(\d+)"


class VKBot:
    def __init__(self, group_token: str, db_session) -> None:
        self.user_id = None
        self.token = group_token
        self.session = db_session
        self.vk = vk_api.VkApi(token=self.token)
        self.longpoll = VkLongPoll(self.vk)
        self.user_db = UserDBManager()
        self.favorites_db = FavoritesDBManager()
        self.black_list_db = BlackListDBManager()
        self.received_profile_info = UserInfoRetriever(self.session)
        self.keyboard = VKKeyboard()
        # Инициализация пагинатора поиска пользователей
        self.paginator = Paginator(self.received_profile_info)
        # Инициализация счётчиков команд
        self.next_command_count = 0
        self.match_info_count = 0
        # Для хранения полученного актуального списка мэтчей.
        # Нужен для работы со списком избранных и черным списком.
        self.current_match_list = None
        # Для хранения состояний выбранного действия в меню.
        # Ключ - id пользователя, значение - состояние в меню
        self.USER_STATE = {}

    def send_message(
            self,
            user_id: int,
            msg: str,
            btns: dict[str, list[tuple[str, str]] | bool] | None = None
    ) -> None:
        keyboard_json: str | None = self.keyboard.create_markup(btns)

        self.vk.method(
            "messages.send",
            {
                "user_id": user_id,
                "message": msg,
                "random_id": 0,
                "keyboard": keyboard_json
            }
        )

    def send_match_info(
            self,
            vk_user_id: int,
            attachment: str = None,
            count: int = 0,
            btns: dict[str, list[tuple[str, str]] | bool] | None = None
    ) -> list:
        keyboard_json: str | None = self.keyboard.create_markup(btns)
        match_info = self.user_db.match_data_layout(vk_user_id)

        if 0 <= count < len(match_info):
            user_name_lastname = match_info[count][0]
            user_profile_url = match_info[count][1]
            user_photos = match_info[count][3]

            user_info_text = f'{user_name_lastname}\n{user_profile_url}'

            if user_photos:
                attachment = ','.join(user_photos)

            self.vk.method('messages.send', {
                'user_id': vk_user_id,
                'message': user_info_text,
                'random_id': 0,
                'attachment': attachment,
                'keyboard': keyboard_json
            })

        return match_info

    def start(self) -> None:
        for event in self.longpoll.listen():
            if event.type == VkEventType.MESSAGE_NEW and event.to_me:
                request = event.text.strip().lower()
                self.user_id = event.user_id
                self._handle_user_request(request)

    def get_user_id(self) -> int:
        return self.user_id

    def _handle_user_request(self, request: str) -> None:

        if request in COMMANDS["start"]:
            self.send_message(
                self.user_id,
                MESSAGES["start"],
                KEYBOARDS["start"]
            )
            #: Получаю информацию о пользователе,
            #: который взаимодействует с ботом
            data = self.received_profile_info.get_profile_info(self.user_id)
            #: Вывожу полученные данные о пользователе в консоль (для отладки)
            # print(data)
            # print(self.received_profile_info.get_profile_info(self.found_user_id))
            #: Загружаю данные пользователя в БД
            self.user_db.add_bot_user_to_db(data)
            # print(get_user_params(self.user_id, session))

            # Делаю поиск подходящих пользователей для мэтчей.
            match = self.received_profile_info.search_users(self.user_id)
            # print(self.received_profile_info._add_user_photos_and_url(match))
            #: (для отладки) Вывожу полученные в ходе поиска данные
            # пользователей
            print(len(match))  # из 10 пользователей 3 отпадает из-за неактивности
            pprint(match)

            #: Загружаю данные найденных подходящих пользователей в БД
            self.user_db.add_match_user_to_db(match, self.user_id)
        elif request in COMMANDS["hello"]:
            self.send_message(
                self.user_id,
                MESSAGES["hello"]
            )
        elif request in COMMANDS["goodbye"]:
            self.send_message(self.user_id, MESSAGES["goodbye"])
        elif request in COMMANDS["next"] or request in COMMANDS["show"]:
            # Обработка введенной команды пользователем "следующий, next",
            # и/или обработка введенной команды пользователем "показать, show".
            # При вводе этой команды бот высылает по одной информацию о мэтче
            # и увеличивает счетчик match_info_count на единицу.
            # А также увеличивает счетчик next_command_count на единицу.
            self.current_match_list = self.send_match_info(
                self.user_id,
                count=self.match_info_count,
                btns=KEYBOARDS["card"]
            )
            self.match_info_count += 1

            print("ПАГИНАТОР ПОИСКА")  # Для отладки
            self.next_command_count += 1  # Увеличиваем счетчик команды "next"
            print("Счетчик команды:", self.next_command_count)  # Для отладки

            if self.next_command_count == 3:
                # Если счетчик команды "next" или "show" равен 3,
                # то добавляем новые мэтчи в базу данных и сбрасываем счетчик
                # "next_command_count" на 0.
                # Т.е. если у нас есть 3 мэтча, то новые мэтчи будут добавлены
                # в базу данных при достижении пользователем 3 мэтча.
                match = self.paginator.next(self.user_id)
                self.user_db.add_match_user_to_db(match, self.user_id)
                self.next_command_count = 0
        elif request in COMMANDS["add_to_favorites"]:
            self.favorites_db.add_match_to_favorites(
                self.user_id,
                self.current_match_list,
                self.match_info_count - 1
            )
            self.send_message(
                self.user_id,
                MESSAGES["add_to_favorites"],
                KEYBOARDS["add_to_favorites"]
            )
        elif request in COMMANDS["show_favorites"]:
            self.send_message(
                self.user_id,
                self.favorites_db.show_favorites(self.user_id),
                KEYBOARDS["del_from_favorites"]
            )
        elif request in COMMANDS["add_to_black_list"]:
            self.black_list_db.add_match_to_black_list(
                self.user_id,
                self.current_match_list,
                self.match_info_count - 1
            )
            self.send_message(
                self.user_id,
                MESSAGES["add_to_black_list"],
                KEYBOARDS["add_to_black_list"]
            )
        elif request in COMMANDS["show_black_list"]:
            self.send_message(
                self.user_id,
                self.black_list_db.show_black_list(self.user_id),
                KEYBOARDS["del_from_black_list"]
            )
        elif request in COMMANDS["del_from_black_list"]:
            self.send_message(
                self.user_id,
                MESSAGES["del_from_black_list_instruction"],
                KEYBOARDS["next"]
            )
            self.USER_STATE[self.user_id] = 'delete_blacklist'
            print(self.USER_STATE)
        elif request in COMMANDS["del_from_favorites"]:
            self.send_message(
                self.user_id,
                MESSAGES["del_from_favorites_instruction"],
                KEYBOARDS["next"]
            )
            self.USER_STATE[self.user_id] = 'delete_favorites'
        elif re.match(VK_URL_PATTERN, request):
            match = re.match(VK_URL_PATTERN, request)
            del_user_id = int(match.group(1))

            if self.USER_STATE.get(self.user_id) == 'delete_blacklist':
                self.black_list_db.remove_from_black_list(
                    self.user_id, del_user_id
                )
                self.send_message(
                    self.user_id,
                    f"Пользователь с ID {del_user_id} "
                    f"был удален из черного списка.",
                    KEYBOARDS["next"]
                )
            elif self.USER_STATE.get(self.user_id) == 'delete_favorites':
                self.favorites_db.remove_from_favorites(
                    self.user_id, del_user_id
                )
                self.send_message(
                    self.user_id,
                    f"Пользователь с ID {del_user_id} "
                    f"был удален из избранного.",
                    KEYBOARDS["next"]
                )
                self.USER_STATE[self.user_id] = None
        else:
            self.send_message(
                self.user_id,
                MESSAGES["unknown_command"]
            )
