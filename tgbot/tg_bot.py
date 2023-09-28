#!/usr/bin/env python
# pylint: disable=unused-argument, import-error
# This program is dedicated to the public domain under the CC0 license.

"""
Usage:
1. Message @BotFather to create a new bot, set the following commands with your bot.
```
start - Start using this bot
verify_address - Verify address
bind_address - Bind address
get_link - Get join link
```
2. use /set_domain to allow domain `demo.fans3.org`

3. Run `pip3 install -r requirements.txt ` to install dependencies.

4. Put `TGBOT_KEY=xxx:xxxxxx` in `.env` file, then run
```
python3 ./tg_bot.py
```

Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

import logging, os, sys, urllib, json, traceback, base64

from typing import Optional, Tuple

from telegram import (
    Bot,
    Chat,
    ChatMember,
    ChatMemberUpdated,
    ChatPermissions,
    ForceReply,
    LoginUrl,
    Update,
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.constants import ParseMode, ChatMemberStatus
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
    ConversationHandler,
)

from dotenv import load_dotenv
from web3 import Web3, HTTPProvider
from eth_account.messages import encode_defunct
from eth_account import Account
from rocksdict import Rdict, Options

# add source dir
# file_dir = os.path.dirname(__file__)
# sys.path.append(file_dir)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# add env
load_dotenv(os.path.join(BASE_DIR, ".env"))
sys.path.append(BASE_DIR)

BASE_URL = os.environ["BASE_URL"]
CANCEL, START, CREATE, JOIN, LIST, ADDRESS = range(6)
PREFIX_CHAT_ADDRESS = "chat_addr_"
PREFIX_USER_ADDRESS = "user_addr_"
PREFIX_CHAT_INFO = "chat_info_"
KEY_BIND_ADDRESS = "bind_address"
ABI = json.load(open("fans3.json"))
w3 = Web3(HTTPProvider(os.environ["ETH_RPC"]))
db = Rdict("tg.db")


def db_get(key: str) -> str:
    return db.get(key, "")
    # if value == None:
    #     return None
    # return str(value, "utf-8")


def db_set(key: str, value: str):
    db[key] = value
    # bytes(value, "utf-8")


def db_range(start: str, reverse: bool = False):
    return db.items(from_key=start, backwards=reverse)


if ABI == None:
    logger.error("fans3 abi not found in `fans3.json`")

# Enable logging

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

# set higher logging level for httpx to avoid all GET and POST requests being logged
logging.getLogger("httpx").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)
if os.environ["LOG_LEVEL"] != None:
    logger.setLevel(os.environ["LOG_LEVEL"])

join_request_cache = {}


def extract_status_change(
    chat_member_update: ChatMemberUpdated,
) -> Optional[Tuple[bool, bool]]:
    """Takes a ChatMemberUpdated instance and extracts whether the 'old_chat_member' was a member
    of the chat and whether the 'new_chat_member' is a member of the chat. Returns None, if
    the status didn't change.
    """
    status_change = chat_member_update.difference().get("status")
    old_is_member, new_is_member = chat_member_update.difference().get(
        "is_member", (None, None)
    )

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


async def check_supply(chat: Chat, address: str, context: ContextTypes.DEFAULT_TYPE):
    # check if your first share is bought
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    supply = contract.functions.sharesSupply(Web3.to_checksum_address(address)).call()
    if supply == 0:
        await chat.send_message(
            "Now buy your first share to let others buy and join your group.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Buy your first share",
                            login_url=LoginUrl(
                                f"{BASE_URL}/tg/create?tg="
                                + urllib.parse.quote(f"{chat.title}(id: {chat.id})"),
                            ),
                        ),
                    ]
                ]
            ),
        )
        return

    db_set(f"{PREFIX_CHAT_INFO}{chat.id}", chat.to_json())
    await chat.send_message(
        f"You are all set!\n\nNow your fans can buy your share at {BASE_URL}/tg/buy/{address} to join your group!"
    )


async def track_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Tracks the chats the bot is in."""
    logger.debug(update)
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
        if was_member and not is_member:
            logger.info("%s removed the bot from the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).discard(chat.id)
            return
        if not was_member and is_member:
            logger.info("%s added the bot to the group %s", cause_name, chat.title)
            context.bot_data.setdefault("group_ids", set()).add(chat.id)

        # check if the bot is admin
        member = update.my_chat_member.new_chat_member
        if member.status != ChatMemberStatus.ADMINISTRATOR:
            await update.effective_chat.send_message(
                "Please promote me to admin to work."
            )
            return

        # we are admin, now check group permission
        current = (await context.bot.get_chat(chat.id)).permissions
        if current.can_invite_users != False:
            perms = current.to_dict()
            perms["can_invite_users"] = False
            await update.effective_chat.set_permissions(
                ChatPermissions(api_kwargs=perms)
            )
            update.effective_chat.send_message(
                "Permission changed to disallow users to invite others."
            )

        # check if we know group wallet address
        address = db_get(f"{PREFIX_CHAT_ADDRESS}{chat.id}")
        if address == None:
            member = await context.bot.get_chat_member(
                update.effective_chat.id, update.my_chat_member.from_user.id
            )
            if member.status != ChatMemberStatus.OWNER:
                await update.effective_chat.send_message(
                    "Group owner needs to set group address with command /bind_address."
                )
                return
            context.chat_data.setdefault(KEY_BIND_ADDRESS, True)
            await update.effective_chat.send_message(
                "Now Tell us your wallet address, so that anyone bought your share can join this group.",
                reply_markup=ForceReply(
                    input_field_placeholder="Enter your wallet address"
                ),
            )
            return

        await check_supply(chat, address, context)

    elif not was_member and is_member:
        logger.info("%s added the bot to the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    elif was_member and not is_member:
        logger.info("%s removed the bot from the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def reply_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug(update)
    logger.debug(context)
    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.message.from_user.id
    )
    if member.status != ChatMemberStatus.OWNER:
        await update.message.reply_text("Only owner can do this.")
        return
    if context.chat_data.get(KEY_BIND_ADDRESS) != True:
        return
    address = update.message.text
    if not Web3.is_address(address):
        await update.message.reply_text(
            f"{address} is not a valid address, please enter a valid one.",
            reply_markup=ForceReply(
                input_field_placeholder="Please enter your wallet address"
            ),
        )
        return
    db_set(f"{PREFIX_CHAT_ADDRESS}{update.effective_chat.id}", address)
    del context.chat_data[KEY_BIND_ADDRESS]
    await check_supply(update.effective_chat, address, context)


async def greet_chat_members(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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


async def start_private_chat(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
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
    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.message.from_user.id
    )
    if member.status != ChatMemberStatus.OWNER:
        await update.message.reply_text("Only owner can do this.")
        return
    address = db_get(f"{PREFIX_CHAT_ADDRESS}{update.effective_chat.id}")
    if address == None:
        message = "Please enter your wallet address."
    else:
        message = f"Your group address is {address}, change this will kick all members that do not own new one."
    context.chat_data.setdefault(KEY_BIND_ADDRESS, True)
    await update.message.reply_text(
        message,
        reply_markup=ForceReply(
            input_field_placeholder="Please enter your wallet address"
        ),
    )
    return


async def create_invite_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # link = await Chat(update.effective_chat, type=Chat.SUPERGROUP).create_invite_link()
    link = await update.effective_chat.create_invite_link(
        member_limit=100, creates_join_request=True
    )
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
            chat_id=update.chat_join_request.user_chat_id,
            text=text,
            reply_markup=reply_markup,
        )
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
        chat_id=update.effective_chat.id, text=text, reply_markup=ReplyKeyboardRemove()
    )
    return "verify_address"


async def verify_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    print(update.message.text)
    user_id = update.message.from_user.id
    requested_chat_id = join_request_cache.get(user_id, None)
    if requested_chat_id is None:
        print(f"verify_address cannot find user from request")
        return
    # TODO, request server to checkout whether user has bought shares
    if True:
        print(f"approve user:{user_id} joining chat:{requested_chat_id}")
        await context.bot.approve_chat_join_request(requested_chat_id, user_id)
    else:
        print(f"decline user:{user_id} joining chat:{requested_chat_id}")
        await context.bot.decline_chat_join_request(requested_chat_id, user_id)
    return "end"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug(update)
    message = await update.message.reply_text("A moment please...")
    text = ""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    address = db_get(f"{PREFIX_USER_ADDRESS}{update.message.from_user.id}")
    if Web3.is_address(address):
        holdings = contract.functions.getHoldings(
            Web3.to_checksum_address(address)
        ).call()
        if holdings != None and len(holdings) > 0:
            text += "\nGroups that you can join:\n"
            for holding in holdings:
                chat = context.bot_data.get(f"CHAT_{holding}")
                title = "Unknown"
                link = "#"
                if chat != None:
                    title = chat.title
                    link = context.bot.export_chat_invite_link(chat.id)
                text += f"[{title}({holding}]({link})\n"
    group_text = ""
    for k, info in db_range(PREFIX_CHAT_INFO):
        if not k.startswith(PREFIX_CHAT_INFO):
            break
        chat = Chat.de_json(json.loads(info), context.bot)
        chat_address = db_get(f"{PREFIX_CHAT_ADDRESS}{chat.id}")
        price = contract.functions.getBuyPrice(
            Web3.to_checksum_address(chat_address), 1
        ).call()
        priceEth = Web3.from_wei(price, "ether")
        group_text += f"[{chat.title}]({BASE_URL}/tg/buy/{chat_address}) (`{priceEth} ETH` `{chat_address}`)\n"

    if len(group_text) != 0:
        text += "\nKnown groups: (click and buy a share to join)\n" + group_text

    buttons = [[InlineKeyboardButton("Create a group", callback_data=str(CREATE))]]
    if Web3.is_address(address):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Change your wallet address({address})",
                    callback_data=str(JOIN),
                )
            ]
        )
    elif len(text) != 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Verify address and list groups that you can join",
                    callback_data=str(JOIN),
                )
            ]
        )
    buttons.append([InlineKeyboardButton("Cancel", callback_data=str(CANCEL))])

    if len(text) != 0:
        text = "Thanks for choosing Fans3, join or create your own group!\n" + text
    else:
        text = "Thanks for choosing Fans3, no known group yet, let's create the first group!"

    await message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
    )
    return START


async def create_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle create request."""
    logger.debug(update)
    query = update.callback_query
    await query.message.reply_text(
        "Invite this bot to your group to turn it into a Fans3 group!"
    )
    await query.answer("Invite this bot to your group to turn it into a Fans3 group!")
    await query.edit_message_reply_markup(None)
    return ConversationHandler.END


async def join_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle join request."""
    logger.debug(update)
    query = update.callback_query
    await query.message.reply_text(
        f"[Click here]({BASE_URL}/tg/verify/{urllib.parse.quote(query.from_user.username+'('+str(query.from_user.id)+')')}) to verify your address and then paste the code you got.",
        reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        parse_mode=ParseMode.MARKDOWN,
    )
    await query.edit_message_reply_markup(None)
    return ADDRESS


async def join_group_with_address(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle user address binding."""
    logger.debug(update)
    signatures = update.message.text.split("|")
    if len(signatures) != 2:
        await update.message.edit_message_text(
            "Bad code, please enter a valid one",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return ADDRESS
    time = str(base64.b64decode(signatures[0]) or b"", "utf-8")
    signature = base64.b64decode(signatures[1])
    message = encode_defunct(
        text=f"Sign this message to allow telegram user\n\n${update.message.from_user.username}(${str(update.message.from_user.id)})\n\nto join groups that you own a share.\n\nAvailable for 30 minutes.\nTime now: ${time}"
    )
    address = Account.recover_message(message, signature=signature)
    db_set(f"{PREFIX_USER_ADDRESS}{update.message.from_user.id}", address)
    if not Web3.is_address(address):
        await update.message.edit_message_text(
            "Bad code, can not recover your address from code, please enter a valid one",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return ADDRESS
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    holdings = contract.functions.getHoldings(
        Web3.to_checksum_address(Web3.to_checksum_address(address))
    ).call()
    if holdings == None or len(holdings) == 0:
        await update.message.reply_text(
            f"You are now {address} but no group share found, use /start to create or join one",
        )
        return ConversationHandler.END
    message = f"You are now {address}, here are your groups, click to join!\n\n"
    for holding in holdings:
        chat = context.bot_data.get(f"CHAT_{holding}")
        title = "Unknown"
        link = "#"
        if chat != None:
            title = chat.title
            link = context.bot.export_chat_invite_link(chat.id)
        message += f"[{title}({holding})]({link})\n"
    await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END


async def list_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle list request."""
    query = update.callback_query
    group_text = ""
    for k, info in db_range(PREFIX_CHAT_INFO):
        if not k.startswith(PREFIX_CHAT_INFO):
            break
        chat = Chat.de_json(json.loads(info), context.bot)
        address = db_get(f"{PREFIX_CHAT_ADDRESS}{chat.id}")
        group_text += f"[{chat.title}({address}]({BASE_URL}/tg/buy/{address})\n"

    if len(group_text) == 0:
        await query.answer("No group yet, you can create one")
        return START
    await query.answer("Select a group to buy share and join")
    await query.delete_message()
    await query.message.reply_text(
        "Here are known groups, buy a share and join!\n\n" + group_text,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancels and ends the conversation."""
    query = update.callback_query
    await query.message.reply_text("You can start with /start again at any time.")
    await query.edit_message_reply_markup(None)
    return ConversationHandler.END


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels and ends the conversation."""
    await update.effective_chat.send_message("Sorry, something went wrong...")
    logger.error("Exception while handling an update:", exc_info=context.error)
    dev_chat_id = os.environ.get("DEVELOPER_CHAT_ID")
    if dev_chat_id == None:
        return
    # traceback.format_exception returns the usual python message about an exception, but as a
    # list of strings rather than a single string, so we have to join them together.
    tb_list = traceback.format_exception(
        None, context.error, context.error.__traceback__
    )
    tb_string = "".join(tb_list)

    # Build the message with some markup and additional information about what happened.
    # You might need to add some logic to deal with messages longer than the 4096 character limit.
    update_str = update.to_dict() if isinstance(update, Update) else str(update)
    message = f"""An exception was raised while handling an update

```
update = {json.dumps(update_str, indent=2, ensure_ascii=False)}

context.chat_data = {str(context.chat_data)}

context.user_data = {str(context.user_data)}

{tb_string}
```"""

    # Finally, send the message
    await context.bot.send_message(
        chat_id=dev_chat_id, text=message, parse_mode=ParseMode.MARKDOWN
    )


# reference & examples
# https://github.com/python-telegram-bot/python-telegram-bot/blob/master/examples/conversationbot.py
# https://github.com/python-telegram-bot/rules-bot/blob/af3d63e83b73124cb4b374f9633f1c40fb2ac23d/components/joinrequests.py
def main() -> None:
    """Start the bot."""
    # Create the Application and pass it your bot's token.
    application = Application.builder().token(os.environ["TGBOT_KEY"]).build()

    application.add_handler(
        ConversationHandler(
            entry_points=[CommandHandler("start", start)],
            states={
                START: [
                    CallbackQueryHandler(create_group, pattern="^" + str(CREATE) + "$"),
                    CallbackQueryHandler(join_group, pattern="^" + str(JOIN) + "$"),
                    CallbackQueryHandler(list_group, pattern="^" + str(LIST) + "$"),
                    CallbackQueryHandler(cancel, pattern="^" + str(CANCEL) + "$"),
                ],
                ADDRESS: [
                    MessageHandler(filters.REPLY, join_group_with_address),
                ],
            },
            fallbacks=[CommandHandler("cancel", cancel)],
        )
    )

    application.add_handler(ChatJoinRequestHandler(callback=join_handler))
    # application.add_handler(CallbackQueryHandler(verify, pattern="^verify"))
    application.add_handler(
        ConversationHandler(
            entry_points=[CallbackQueryHandler(start_verify, pattern="^verify")],
            states={
                "verify_address": [
                    CommandHandler("verify_address", verify_address)
                    # MessageHandler(filters=filters.ALL , callback=verify_start)
                ],
                # "end":[CallbackQueryHandler(verify, pattern="^verify")]
            },
            fallbacks=[CallbackQueryHandler(start_verify, pattern="^verify")],
        )
    )

    application.add_handler(CommandHandler("bind_address", bind_address))
    application.add_handler(CommandHandler("get_link", create_invite_link))

    # application.add_handler(CommandHandler("setting", setting))
    # application.add_handler(CallbackQueryHandler(button))

    # # Keep track of which chats the bot is in
    application.add_handler(
        ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER)
    )
    application.add_handler(MessageHandler(filters.REPLY, reply_address))

    # # Handle members joining/leaving chats.
    application.add_handler(
        ChatMemberHandler(greet_chat_members, ChatMemberHandler.CHAT_MEMBER)
    )

    application.add_error_handler(error_handler)

    # Interpret any other command or text message as a start of a private chat.
    # This will record the user as being in a private chat with bot.
    # application.add_handler(MessageHandler(filters.ALL, start_private_chat))

    # Run the bot until the user presses Ctrl-C
    # We pass 'allowed_updates' handle *all* updates including `chat_member` updates
    # To reset this, simply pass `allowed_updates=[]`
    application.run_polling(allowed_updates=Update.ALL_TYPES)
    db.close()


if __name__ == "__main__":
    main()
