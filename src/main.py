from collections import defaultdict
from typing import Literal, Optional, Union

import discord
from discord import Message as DiscordMessage, app_commands
import logging
from src.base import Message, Conversation
from src.constants import (
    BOT_INVITE_URL,
    DISCORD_BOT_TOKEN,
    ACTIVATE_THREAD_PREFX,
    MAX_THREAD_MESSAGES,
    SECONDS_DELAY_RECEIVING_MSG,
)
import asyncio
from src.utils import (
    logger,
    should_block,
    close_thread,
    is_last_message_stale,
    discord_message_to_message,
    split_into_shorter_messages,
)
from src import completion
from src.completion import generate_completion_response, process_response, client as openai_client  # Import client
from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)

from src.moderation import (
    moderate_message,
    send_moderation_blocked_message,
    send_moderation_flagged_message,
)

import os
from flask import Flask
from threading import Thread

logging.basicConfig(
    format="[%(asctime)s] [%(filename)s:%(lineno)d] %(message)s", level=logging.INFO
)

intents = discord.Intents.default()
intents.message_content = True

client = discord.Client(intents=intents)
tree = discord.app_commands.CommandTree(client)

openai_thread_mapping = {}
user_mention_threads = {}


@client.event
async def on_ready():
    logger.info(
        f"We have logged in as {client.user}. Invite URL: {BOT_INVITE_URL}")

    completion.MY_BOT_NAME = client.user.name

    await tree.sync()


@tree.command(name="chat", description="Create a new thread for conversation")
@discord.app_commands.checks.has_permissions(send_messages=True)
@discord.app_commands.checks.has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(send_messages=True)
@discord.app_commands.checks.bot_has_permissions(view_channel=True)
@discord.app_commands.checks.bot_has_permissions(manage_threads=True)
@app_commands.describe(message="The first prompt to start the chat with")
async def chat_command(
    int: discord.Interaction,
    message: str,
):
    try:
        # only support creating thread in text channel
        if not isinstance(int.channel, discord.TextChannel):
            return

        # block servers not in allow list
        if should_block(guild=int.guild):
            return

        user = int.user
        logger.info(f"Chat command by {user} {message[:20]}")

        try:
            # moderate the message
            flagged_str, blocked_str = moderate_message(
                message=message, user=user)
            await send_moderation_blocked_message(
                guild=int.guild,
                user=user,
                blocked_str=blocked_str,
                message=message,
            )
            if len(blocked_str) > 0:
                # message was blocked
                await int.response.send_message(
                    f"Your prompt has been blocked by moderation.\n{message}",
                    ephemeral=True,
                )
                return

            embed = discord.Embed(
                description=f"<@{user.id}> wants to chat! 🤖💬",
                color=discord.Color.green(),
            )

            embed.add_field(name=user.name, value=message)

            if len(flagged_str) > 0:
                # message was flagged
                embed.color = discord.Color.yellow()
                embed.title = "⚠️ This prompt was flagged by moderation."

            await int.response.send_message(embed=embed)
            response = await int.original_response()

            await send_moderation_flagged_message(
                guild=int.guild,
                user=user,
                flagged_str=flagged_str,
                message=message,
                url=response.jump_url,
            )
        except Exception as e:
            logger.exception(e)
            await int.response.send_message(
                f"Failed to start chat {str(e)}", ephemeral=True
            )
            return

        # create the discord thread
        thread = await response.create_thread(
            name=f"{ACTIVATE_THREAD_PREFX} {user.name[:20]} - {message[:30]}",
            slowmode_delay=1,
            reason="gpt-bot",
            auto_archive_duration=60,
        )

        # create a new openai thread
        try:
            new_openai_thread = await openai_client.beta.threads.create()
        except Exception as e:
            logger.exception("Failed to create OpenAI thread.")
            await thread.send(f"Bot lỗi: Không thể tạo luồng OpenAI. {str(e)}")
            return

        # save the link
        openai_thread_mapping[thread.id] = new_openai_thread.id
        logger.info(
            f"Created new OpenAI thread {new_openai_thread.id} for Discord thread {thread.id}")

        async with thread.typing():
            # fetch completion
            response_data = await generate_completion_response(
                openai_thread_id=new_openai_thread.id,
                last_user_message=message,
                user=user
            )

            # send the result
            await process_response(
                user=user, thread=thread, response_data=response_data
            )
    except Exception as e:
        logger.exception(e)
        await int.response.send_message(
            f"Failed to start chat {str(e)}", ephemeral=True
        )


# handle when bot is mentioned
async def handle_mention_message(message: DiscordMessage):
    try:
        # Remove the bot mention from the message content
        content = message.content
        for mention in message.mentions:
            if mention.id == client.user.id:
                content = content.replace(
                    f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        content = content.strip()

        # If message is empty after removing mentions, ignore
        if not content:
            await message.channel.send(
                f"Hi <@{message.author.id}>! Can I help you? 🤖",
                reference=message
            )
            return

        # moderate the message
        flagged_str, blocked_str = moderate_message(
            message=content, user=message.author
        )
        await send_moderation_blocked_message(
            guild=message.guild,
            user=message.author,
            blocked_str=blocked_str,
            message=content,
        )
        if len(blocked_str) > 0:
            await message.channel.send(
                embed=discord.Embed(
                    description=f"❌ **{message.author.mention}'s message has been blocked by moderation.**",
                    color=discord.Color.red(),
                ),
                reference=message
            )
            return

        await send_moderation_flagged_message(
            guild=message.guild,
            user=message.author,
            flagged_str=flagged_str,
            message=content,
            url=message.jump_url,
        )

        if len(flagged_str) > 0:
            await message.channel.send(
                embed=discord.Embed(
                    description=f"⚠️ **{message.author.mention}'s message has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                ),
                reference=message
            )

        # Show typing indicator
        async with message.channel.typing():

            # find the openai thread for the user
            user_id = message.author.id
            openai_thread_id = user_mention_threads.get(user_id)

            if not openai_thread_id:
                try:
                    new_thread = await openai_client.beta.threads.create()
                    openai_thread_id = new_thread.id
                    user_mention_threads[user_id] = openai_thread_id
                    logger.info(
                        f"Created new mention thread {openai_thread_id} for user {user_id}")
                except Exception as e:
                    logger.exception(
                        f"Failed to create OpenAI thread for user {user_id}")
                    await message.channel.send(f"Bot lỗi: Không thể tạo luồng OpenAI. {str(e)}", reference=message)
                    return

            # Generate response
            response_data = await generate_completion_response(
                openai_thread_id=openai_thread_id,
                last_user_message=content,
                user=message.author,
            )

            # Send response
            status = response_data.status
            reply_text = response_data.reply_text
            status_text = response_data.status_text

            if status is completion.CompletionResult.OK or status is completion.CompletionResult.MODERATION_FLAGGED:
                if not reply_text:
                    await message.channel.send(
                        embed=discord.Embed(
                            description=f"**Invalid response** - empty response",
                            color=discord.Color.yellow(),
                        ),
                        reference=message
                    )
                else:
                    shorter_response = split_into_shorter_messages(reply_text)
                    for i, r in enumerate(shorter_response):
                        # Only reference the original message for the first reply
                        if i == 0:
                            await message.channel.send(r, reference=message)
                        else:
                            await message.channel.send(r)

                if status is completion.CompletionResult.MODERATION_FLAGGED:
                    await message.channel.send(
                        embed=discord.Embed(
                            description=f"⚠️ **This response has been flagged by moderation.**",
                            color=discord.Color.yellow(),
                        )
                    )
            elif status is completion.CompletionResult.MODERATION_BLOCKED:
                await message.channel.send(
                    embed=discord.Embed(
                        description=f"❌ **This response has been blocked by moderation.**",
                        color=discord.Color.red(),
                    ),
                    reference=message
                )
            elif status is completion.CompletionResult.INVALID_REQUEST:
                await message.channel.send(
                    embed=discord.Embed(
                        description=f"**Invalid request** - {status_text}",
                        color=discord.Color.yellow(),
                    ),
                    reference=message
                )
            else:
                await message.channel.send(
                    embed=discord.Embed(
                        description=f"**Error** - {status_text}",
                        color=discord.Color.yellow(),
                    ),
                    reference=message
                )
    except Exception as e:
        logger.exception(e)
        await message.channel.send(
            f"Sorry, an error occurred while processing your message: {str(e)}",
            reference=message
        )


# calls for each message
@client.event
async def on_message(message: DiscordMessage):
    try:
        # block servers not in allow list
        if should_block(guild=message.guild):
            return

        # ignore messages from the bot
        if message.author == client.user:
            return

        # check if bot is mentioned in a non-thread channel
        channel = message.channel
        if not isinstance(channel, discord.Thread):
            # handle mentions in regular channels
            if client.user.mentioned_in(message):
                await handle_mention_message(message)
            return

        # ignore threads not created by the bot
        thread = channel
        if thread.owner_id != client.user.id:
            return

        # ignore threads that are archived locked or title is not what we want
        if (
            thread.archived
            or thread.locked
            or not thread.name.startswith(ACTIVATE_THREAD_PREFX)
        ):
            # ignore this thread
            return

        if thread.message_count > MAX_THREAD_MESSAGES:
            # too many messages, no longer going to reply
            await close_thread(thread=thread)
            return

        # moderate the message
        flagged_str, blocked_str = moderate_message(
            message=message.content, user=message.author
        )
        await send_moderation_blocked_message(
            guild=message.guild,
            user=message.author,
            blocked_str=blocked_str,
            message=message.content,
        )
        if len(blocked_str) > 0:
            try:
                await message.delete()
                await thread.send(
                    embed=discord.Embed(
                        description=f"❌ **{message.author}'s message has been deleted by moderation.**",
                        color=discord.Color.red(),
                    )
                )
                return
            except Exception as e:
                await thread.send(
                    embed=discord.Embed(
                        description=f"❌ **{message.author}'s message has been blocked by moderation but could not be deleted. Missing Manage Messages permission in this Channel.**",
                        color=discord.Color.red(),
                    )
                )
                return
        await send_moderation_flagged_message(
            guild=message.guild,
            user=message.author,
            flagged_str=flagged_str,
            message=message.content,
            url=message.jump_url,
        )
        if len(flagged_str) > 0:
            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **{message.author}'s message has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )

        # wait a bit in case user has more messages
        if SECONDS_DELAY_RECEIVING_MSG > 0:
            await asyncio.sleep(SECONDS_DELAY_RECEIVING_MSG)
            if is_last_message_stale(
                interaction_message=message,
                last_message=thread.last_message,
                bot_id=client.user.id,
            ):
                # there is another message, so ignore this one
                return

        logger.info(
            f"Thread message to process - {message.author}: {message.content[:50]} - {thread.name} {thread.jump_url}"
        )

        # get the openai thread id for the thread
        openai_thread_id = openai_thread_mapping.get(thread.id)

        if not openai_thread_id:
            # handle the case where the thread is not found
            logger.warning(
                f"Cannot find OpenAI thread_id for Discord thread {thread.id}. Skipping.")
            await thread.send("Bot error: Cannot find OpenAI thread ID. This thread may be old. Please start again with `/chat`.")
            return

        # generate the response
        async with thread.typing():
            response_data = await generate_completion_response(
                openai_thread_id=openai_thread_id,
                last_user_message=message.content,
                user=message.author,
            )

        if is_last_message_stale(
            interaction_message=message,
            last_message=thread.last_message,
            bot_id=client.user.id,
        ):
            # there is another message and its not from us, so ignore this response
            return

        # send response
        await process_response(
            user=message.author, thread=thread, response_data=response_data
        )
    except Exception as e:
        logger.exception(e)


app = Flask(__name__)


@app.route('/')
def home():
    return "Bot is running!"


def run_flask():
    # get port from environment variable, if not set, use 8080
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)


# run flask
flask_thread = Thread(target=run_flask)
flask_thread.start()

client.run(DISCORD_BOT_TOKEN)
