#!/usr/bin/env python
# pylint: disable=unused-argument, import-error
# This program is dedicated to the public domain under the CC0 license.

"""
Usage:
1. Message @BotFather to create a new bot, set the following commands with your bot.
```
start - Start using Fans3
```
2. Run `pip3 install -r requirements.txt ` to install dependencies.

3. Copy `.env.example` to `.env` file, fill `TGBOT_KEY` and `ETH_RPC`, then run
```
python3 ./tg_bot.py
```

Press Ctrl-C on the command line or send a signal to the process to stop the
bot.
"""

import logging, os, sys, urllib, json, traceback, base64, datetime, pytz

MIN_PYTHON = (3, 11)
if sys.version_info < MIN_PYTHON:
    sys.exit("Python %s.%s or later is required.\n" % MIN_PYTHON)

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
STATE_VERIFY_ADDRESS = range(1)
CALLBACK_CHECK_FIRST_SHARE = "check_first_share"
CALLBACK_START_VERIFY_ADDRESS = "start_verify_address"
CALLBACK_CREATE_GROUP = "create_group"
CALLBACK_CANCEL = "CANCEL"
PREFIX_CHAT_ADDRESS = "chat_addr_"
PREFIX_USER_ADDRESS = "user_addr_"
PREFIX_CHAT_INFO = "chat_info_"
PREFIX_CHAT_LINK = "chat_link_"
PREFIX_ADDRESS_CHATS = "addr_chat_"
KEY_BIND_ADDRESS = "bind_address"
ABI = json.load(open("fans3.json"))
w3 = Web3(HTTPProvider(os.environ["ETH_RPC"]))
db = Rdict("tg.db")


def db_get(key: str) -> str:
    return db.get(key, None)


def db_set(key: str, value: str):
    db[key] = value
    # bytes(value, "utf-8")


def db_delete(key: str):
    db.delete(key)


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


async def check_first_share(
    chat: Chat, address: str, context: ContextTypes.DEFAULT_TYPE
):
    """Check if group's first share is bought"""
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
                            "Buy the first share",
                            url=f"{BASE_URL}/tg/create?tg="
                            + urllib.parse.quote(f"{chat.title}(id: {chat.id})"),
                        ),
                    ],
                    [
                        InlineKeyboardButton(
                            "I've bought the first share",
                            callback_data=CALLBACK_CHECK_FIRST_SHARE,
                        ),
                    ],
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

        await group_start(
            chat,
            update.my_chat_member.new_chat_member,
            await context.bot.get_chat_member(
                chat.id, update.my_chat_member.from_user.id
            ),
            context,
        )

    elif not was_member and is_member:
        logger.info("%s added the bot to the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).add(chat.id)
    elif was_member and not is_member:
        logger.info("%s removed the bot from the channel %s", cause_name, chat.title)
        context.bot_data.setdefault("channel_ids", set()).discard(chat.id)


async def reply_group_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Bind address for a user"""
    # check if we are waiting for address in a group
    if (
        update.effective_chat.type not in [Chat.GROUP, Chat.SUPERGROUP]
        or context.chat_data.get(KEY_BIND_ADDRESS) != True
    ):
        return
    member = await context.bot.get_chat_member(
        update.effective_chat.id, update.message.from_user.id
    )
    if member.status != ChatMemberStatus.OWNER:
        await update.message.reply_text("Only owner can do this.")
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
    db_set(
        f"{PREFIX_ADDRESS_CHATS}{address}_{update.effective_chat.id}",
        update.effective_chat.id,
    )
    del context.chat_data[KEY_BIND_ADDRESS]
    await check_first_share(update.effective_chat, address, context)


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


async def verify_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    receive use join request
    1. get tg_user_id
    2. get user_address by input
    3. check whether user is accessible
    """
    user = update.chat_join_request.from_user
    chat = update.chat_join_request.chat
    address = db_get(f"{PREFIX_USER_ADDRESS}{user.id}")
    shareHolder = db_get(f"{PREFIX_CHAT_ADDRESS}{chat.id}")
    if address == None:
        await context.bot.send_message(
            chat_id=update.chat_join_request.user_chat_id,
            text="Join group failed as you don't have a verified address",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Verify your address to continue",
                            callback_data=CALLBACK_START_VERIFY_ADDRESS,
                        )
                    ]
                ]
            ),
        )
        await update.chat_join_request.decline()
        return
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    balance = contract.functions.sharesBalance(
        Web3.to_checksum_address(shareHolder), Web3.to_checksum_address(address)
    ).call()
    if balance > 0:
        await update.chat_join_request.approve()
    else:
        await context.bot.send_message(
            chat_id=update.chat_join_request.user_chat_id,
            text=f"Join group failed as you don't have a share, check your address or click [here]({BASE_URL}/tg/buy/{shareHolder}) to buy a share",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=True,
        )
        await update.chat_join_request.decline()


async def group_start(
    chat: Chat,
    member_bot: ChatMember,
    member_user: ChatMember,
    context: ContextTypes.DEFAULT_TYPE,
):
    """deal with group startup"""
    # check if the bot is admin
    if member_bot.status != ChatMemberStatus.ADMINISTRATOR:
        await chat.send_message("Please promote me to admin to work.")
        return

    # we are admin, now check group permission
    current = (await context.bot.get_chat(chat.id)).permissions
    if current.can_invite_users != False:
        perms = current.to_dict()
        perms["can_invite_users"] = False
        await chat.set_permissions(ChatPermissions(api_kwargs=perms))
        chat.send_message("Permission changed to disallow users to invite others.")

    # check if we know group wallet address
    address = db_get(f"{PREFIX_CHAT_ADDRESS}{chat.id}")
    if address == None:
        if member_user.status != ChatMemberStatus.OWNER:
            await chat.send_message(
                "Group owner needs to set group address with command /start."
            )
            return
        context.chat_data.setdefault(KEY_BIND_ADDRESS, True)
        await chat.send_message(
            "Now Tell us your wallet address, so that anyone bought your share can join this group.",
            reply_markup=ForceReply(
                input_field_placeholder="Enter your wallet address"
            ),
        )
        return

    await check_first_share(chat, address, context)


async def get_link(chat_id: int, bot: Bot):
    """Get an invite link for a group"""
    link = db_get(f"{PREFIX_CHAT_LINK}{chat_id}")
    if link != None and isinstance(link, str):
        return link
    link = (
        await bot.create_chat_invite_link(
            chat_id, name="Fans3Bot", creates_join_request=True
        )
    ).invite_link
    if link != None:
        db_set(f"{PREFIX_CHAT_LINK}{chat_id}", link)
    return link


async def get_holdings(address: str, bot: Bot) -> str | None:
    """Get holding groups of an address"""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    holdings = contract.functions.getHoldings(Web3.to_checksum_address(address)).call()
    if holdings == None or len(holdings) == 0:
        return None
    message = ""
    for holding in holdings:
        prefix = f"{PREFIX_ADDRESS_CHATS}{holding}"
        for k, chat_id in db_range(prefix):
            if not k.startswith(prefix):
                break
            if db_get(f"{PREFIX_CHAT_ADDRESS}{chat_id}") != holding:
                db_delete(f"{PREFIX_CHAT_ADDRESS}{chat_id}")
                continue
            chat = Chat.de_json(json.loads(db_get(f"{PREFIX_CHAT_INFO}{chat_id}")), bot)
            title = "Unknown"
            link = "#"
            if chat != None:
                title = chat.title
                link = await get_link(chat.id, bot)
            message += f"[{title}]({link})({holding})\n"
    return message


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start command handler"""
    logger.debug(update)
    if update.effective_chat.type in [Chat.GROUP, Chat.SUPERGROUP]:
        await group_start(
            update.effective_chat,
            await update.effective_chat.get_member(context.bot.id),
            await update.effective_chat.get_member(
                (update.message or update.callback_query).from_user.id
            ),
            context,
        )
        return
    message = await update.message.reply_text("A moment please...")
    text = ""
    contract = w3.eth.contract(
        address=Web3.to_checksum_address(os.environ["CONTRACT_ADDRESS"]), abi=ABI
    )
    address = db_get(f"{PREFIX_USER_ADDRESS}{update.message.from_user.id}")
    if Web3.is_address(address):
        text = await get_holdings(address, context.bot)
        if text == None:
            text = ""
        else:
            text = "\nGroups that you can join:\n" + text
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

    buttons = []
    if Web3.is_address(address):
        buttons.append(
            [
                InlineKeyboardButton(
                    f"Change your wallet address({address})",
                    callback_data=CALLBACK_START_VERIFY_ADDRESS,
                )
            ]
        )
    elif len(text) != 0:
        buttons.append(
            [
                InlineKeyboardButton(
                    "Verify address and list groups that you can join",
                    callback_data=CALLBACK_START_VERIFY_ADDRESS,
                )
            ]
        )
    buttons.append(
        [InlineKeyboardButton("Create a group", callback_data=CALLBACK_CREATE_GROUP)]
    )
    buttons.append([InlineKeyboardButton("Cancel", callback_data=CALLBACK_CANCEL)])

    if len(text) != 0:
        text = "Thanks for choosing Fans3, join or create your own group!\n" + text
    else:
        text = "Thanks for choosing Fans3, no known group yet, let's create the first group!"

    await message.edit_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(buttons),
        disable_web_page_preview=True,
    )


async def create_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle create request."""
    logger.debug(update)
    query = update.callback_query
    await query.message.reply_text(
        "Invite this bot to your group to turn it into a Fans3 group!"
    )
    await query.answer("Invite this bot to your group to turn it into a Fans3 group!")
    await query.edit_message_reply_markup(None)


async def start_verify_address(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> int:
    """Handle join request."""
    logger.debug(update)
    query = update.callback_query
    await query.message.reply_text(
        f"[Click here]({BASE_URL}/tg/verify/{urllib.parse.quote(query.from_user.username+'('+str(query.from_user.id)+')')}) to verify your address and then paste the code you got.",
        reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        parse_mode=ParseMode.MARKDOWN,
    )
    await query.edit_message_reply_markup(None)
    return STATE_VERIFY_ADDRESS


async def verify_address(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user address binding."""
    logger.debug(update)
    signatures = update.message.text.split("|")
    if len(signatures) != 2:
        await update.message.reply_text(
            "Bad code, please enter a valid one",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return STATE_VERIFY_ADDRESS
    time = str(base64.b64decode(signatures[0]) or b"", "utf-8")
    time_now = datetime.datetime.now(pytz.utc)
    time_sign = datetime.datetime.fromisoformat(time)
    if time_sign >= time_now:
        await update.message.reply_text(
            "Code from future, check your time or try it later",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return STATE_VERIFY_ADDRESS
    elif time_now - datetime.timedelta(minutes=30) > time_sign:
        await update.message.reply_text(
            "Code expires, please try again",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return STATE_VERIFY_ADDRESS
    signature = base64.b64decode(signatures[1])
    message = encode_defunct(
        text=f"Sign this message to allow telegram user\n\n{update.message.from_user.username}({str(update.message.from_user.id)})\n\nto join groups that you own a share.\n\nAvailable for 30 minutes.\nTime now: {time}"
    )
    address = Account.recover_message(message, signature=signature)
    logger.debug(f"{time} {time_now} {time_sign} {signature} {message}")
    db_set(f"{PREFIX_USER_ADDRESS}{update.message.from_user.id}", address)
    if not Web3.is_address(address):
        await update.message.edit_message_text(
            "Bad code, can not recover your address from code, please enter a valid one",
            reply_markup=ForceReply(input_field_placeholder="Paste the code here"),
        )
        return STATE_VERIFY_ADDRESS
    holdings = await get_holdings(address, context.bot)
    if holdings == None:
        await update.message.reply_text(
            f"You are now {address} but no group share found, use /start to create or join one",
        )
        return ConversationHandler.END
    message = (
        f"You are now {address}, here are your groups, click to join!\n\n" + holdings
    )
    await update.message.reply_text(
        message, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancels and ends the conversation."""
    query = update.callback_query
    await query.message.reply_text("You can start with /start again at any time.")
    await query.edit_message_reply_markup(None)


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

    # start command for chats and groups
    application.add_handler(CommandHandler("start", start))

    # create group callback
    application.add_handler(
        CallbackQueryHandler(create_group, pattern=f"^{CALLBACK_CREATE_GROUP}$")
    )

    # when clicking cancel
    application.add_handler(
        CallbackQueryHandler(cancel, pattern=f"^{CALLBACK_CANCEL}$")
    )

    # check if first group share is bought in groups
    application.add_handler(
        CallbackQueryHandler(start, pattern=f"^{CALLBACK_CHECK_FIRST_SHARE}$")
    )

    # verify address in private chat
    application.add_handler(
        ConversationHandler(
            entry_points=[
                CallbackQueryHandler(
                    start_verify_address, pattern=f"^{CALLBACK_START_VERIFY_ADDRESS}$"
                )
            ],
            states={
                STATE_VERIFY_ADDRESS: [MessageHandler(filters.REPLY, verify_address)]
            },
            fallbacks=[CommandHandler("start", start)],
        )
    )

    # verify if a join request can be approved
    application.add_handler(ChatJoinRequestHandler(callback=verify_join_request))

    # Keep track of which chats the bot is in
    application.add_handler(
        ChatMemberHandler(track_chats, ChatMemberHandler.MY_CHAT_MEMBER)
    )

    # check replies when binding address
    application.add_handler(MessageHandler(filters.REPLY, reply_group_address))

    # Handle members joining/leaving chats.
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
