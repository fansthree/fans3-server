#!/usr/bin/env python
# pylint: disable=unused-argument, import-error
# This program is dedicated to the public domain under the CC0 license.

"""
Simple Bot to handle '(my_)chat_member' updates.
Greets new users & keeps track of which chats the bot is in.

Usage:
Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

import logging
from typing import Optional, Tuple

from telegram import Bot, Chat, ChatMember, ChatMemberUpdated, Update, ReplyKeyboardRemove
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import BadRequest, Forbidden
from telegram.ext import (
    Updater,
    Application,
    ChatMemberHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    CallbackQueryHandler,
    ConversationHandler
)

# Enable logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

token = "xxxxx:xxxxxxxxxxxxxx"
bot = Bot(token)

join_request_cache = {}

def extract_status_change(chat_member_update: ChatMemberUpdated) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get("is_member", (None, None))

    if status_change is None:
        return None

    old_status, new_status = status_change
    was_member = old_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (old_status == ChatMember.RESTRICTED and old_is_member is True)
    is_member = new_status in [
        ChatMember.MEMBER,
        ChatMember.OWNER,
        ChatMember.ADMINISTRATOR,
    ] or (new_status == ChatMember.RESTRICTED and new_is_member is True)

    return was_member, is_member


async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracks the chats the bot is in."""
    result = extract_status_change(update.my_chat_member)
    if result is None:
        return
    was_member, is_member = result

    # Let's check who is responsible for the change
    cause_name = update.effective_user.full_name

    # Handle chat types differently:
    chat = update.effective_chat
    if chat.type == Chat.PRIVATE:
        if not was_member and is_member:
            # This may not be really needed in practice because most clients will automatically
            # send a /start command after the user unblocks the bot, and start_private_chat()
            # will add the user to "user_ids".
            # We're including this here for the sake of the example.
            logger.info("%s unblocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s blocked the bot", cause_name)
            context.bot_data.setdefault("user_ids", set()).discard(chat.id)
    elif chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        if not was_member and is_member:
            logger.info("%s added the bot to the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)
        elif was_member and not is_member:
            logger.info("%s removed the bot from the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
    elif not was_member and is_member:
        logger.info("%s added the bot to the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    elif was_member and not is_member:
        logger.info("%s removed the bot from the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def greet_chat_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets new users in chats and announces when someone leaves"""
    result = extract_status_change(update.chat_member)
    if result is None:
        return

    was_member, is_member = result
    cause_name = update.chat_member.from_user.mention_html()
    member_name = update.chat_member.new_chat_member.user.mention_html()

    if not was_member and is_member:
        await update.effective_chat.send_message(
            f"{member_name} was added by {cause_name}. Welcome!",
            parse_mode=ParseMode.HTML,
        )
    elif was_member and not is_member:
        await update.effective_chat.send_message(
            f"{member_name} is no longer with us. Thanks a lot, {cause_name} ...",
            parse_mode=ParseMode.HTML,
        )


async def start_private_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Greets the user and records that they started a chat with the bot if it's a private chat.
    Since no `my_chat_member` update is issued when a user starts a private chat with the bot
    for the first time, we have to track it explicitly here.
    """
    user_name = update.effective_user.full_name
    chat = update.effective_chat
    if chat.type != Chat.PRIVATE or chat.id in context.bot_data.get("user_ids", set()):
        return

    logger.info("%s started a private chat with the bot", user_name)
    context.bot_data.setdefault("user_ids", set()).add(chat.id)

    await update.effective_message.reply_text(
        f"Welcome {user_name}. Use /show_chats to see what chats I'm in."
    )

# async def setting(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Sends a message with three inline buttons attached."""
#     keyboard = [
#         [
#             InlineKeyboardButton("关联钱包地址", callback_data="bind_address"),
#             InlineKeyboardButton("创建邀请链接", callback_data="create_invite_link"),
#         ],
#         [InlineKeyboardButton("Option 3-test", callback_data="3")],
#         [InlineKeyboardButton("Option 4-test", callback_data="4")],
#     ]

#     reply_markup = InlineKeyboardMarkup(keyboard)

#     await update.message.reply_text("Please choose:", reply_markup=reply_markup)

# async def button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
#     """Parses the CallbackQuery and updates the message text."""
#     query = update.callback_query

#     # CallbackQueries need to be answered, even if no notification to the user is needed
#     # Some clients may have trouble otherwise. See https://core.telegram.org/bots/api#callbackquery
#     await query.answer()
#     if query.data == "bind_address":
#         await query.edit_message_text(text="使用命令/bind_address 要绑定的钱包地址")
#     elif query.data == "create_invite_link":
#         print(update.effective_chat.invite_link)
#         link = await update.effective_chat.create_invite_link(member_limit=100)
#         await query.edit_message_text(text=f"{link}")
#     else:
#         await query.edit_message_text(text=f"Selected option: {query.data}")


async def end(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Returns `ConversationHandler.END`, which tells the
    ConversationHandler that the conversation is over.
    """
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(text="See you next time!")
    return ConversationHandler.END

async def bind_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    address = update.message.text.split(" ")[-1]
    print(f"bind success {user_id} with {address}")
    # TODO, request http to check&save
    await update.message.reply_text(f"Success!")

async def create_invite_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # link = await Chat(update.effective_chat, type=Chat.SUPERGROUP).create_invite_link()
    link = await update.effective_chat.create_invite_link(member_limit=100, creates_join_request=True)
    print("create invite link", link.invite_link)
    await update.effective_message.reply_text(link.invite_link)


async def join_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    receive use join request
    1. get tg_user_id
    2. get user_address by input
    3. check whether user is accessible
    """
    bot = context.bot
    user = update.chat_join_request.from_user
    tg_user_name = update.chat_join_request.from_user.name
    chat = update.chat_join_request.chat
    print(f"--------------user:{user.id} join chat:{chat.id} request")
    try:
        text = "You have to verify&bind your wallet address first"
        reply_markup = InlineKeyboardMarkup.from_button(
        InlineKeyboardButton(
            text="verify & bind your wallet address",
            callback_data=f"verify {chat.id}",
        )
    )
        message = await context.bot.send_message(
            chat_id=update.chat_join_request.user_chat_id, text=text, reply_markup= reply_markup)
    except Forbidden:
        # If the user blocked the bot, let's give the admins a chance to handle that
        # TG also notifies the user and forwards the message once the user unblocks the bot, but
        # forwarding it still doesn't hurt ...
        text = (
            f"User {user.mention_html()} with id {user.id} requested to join the group "
            f"{update.chat_join_request.chat.username} but has blocked me. Please manually handle this."
        )
        print(text)
        # await context.bot.send_message(chat_id=ERROR_CHANNEL_CHAT_ID, text=text)
        return

async def start_verify(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    _, chat_id = query.data.split(" ")
    user_id = query.from_user.id
    join_request_cache[user_id] = chat_id
    print(f"verify user:{update.callback_query.from_user.id} joining chat:{chat_id}")
    text = f"Please use /verify_address your_wallet_address to verfiy your address"
    message = await context.bot.send_message(
        chat_id=update.effective_chat.id, text=text, reply_markup=ReplyKeyboardRemove())
    return "verify_address"

async def verify_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(update.message.text)
    user_id = update.message.from_user.id
    requested_chat_id = join_request_cache.get(user_id, None)
    if requested_chat_id is None:
        print(f"verify_address cannot find user from request")
        return
    #TODO, request server to checkout whether user has bought shares
    if True:

        print(f"approve user:{user_id} joining chat:{requested_chat_id}")
        await context.bot.approve_chat_join_request(requested_chat_id, user_id) 
    else:
        print(f"decline user:{user_id} joining chat:{requested_chat_id}")
        await context.bot.decline_chat_join_request(requested_chat_id, user_id)
    return "end"

# reference & examples
#https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/conversationbot.py
#https://github.com/python-telegram-bot/rules-bot/blob/af3d63e83b73124cb4b374f9633f1c40fb2ac23d/components/joinrequests.py

def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(token).build()

    application.add_handler(ChatJoinRequestHandler(callback=join_handler))
    # application.add_handler(CallbackQueryHandler(verify, pattern="^verify"))
    application.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(start_verify, pattern="^verify")],
        states={
            "verify_address":[
                CommandHandler("verify_address", verify_address)
                # MessageHandler(filters=filters.ALL , callback=verify_start)
                ],
            # "end":[CallbackQueryHandler(verify, pattern="^verify")]
        },
        fallbacks=[CallbackQueryHandler(start_verify, pattern="^verify")]
    ))

    application.add_handler(CommandHandler("bind_address", bind_address))
    application.add_handler(CommandHandler("create_invite_link", create_invite_link))

    # application.add_handler(CommandHandler("setting", setting))
    # application.add_handler(CallbackQueryHandler(button))

    # # Keep track of which chats the bot is in
    application.add_handler(ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER))


    # # Handle members joining/leaving chats.
    application.add_handler(ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER))

    # Interpret any other command or text message as a start of a private chat.
    # This will record the user as being in a private chat with bot.
    # application.add_handler(MessageHandler(filters.ALL, start_private_chat))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()