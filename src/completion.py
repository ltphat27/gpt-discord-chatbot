from enum import Enum
from dataclasses import dataclass
import openai
from openai import AsyncOpenAI
import os
import asyncio

from src.moderation import moderate_message
from typing import Optional, List
from src.constants import (
    BOT_INSTRUCTIONS,
    BOT_NAME,
    EXAMPLE_CONVOS,
)
import discord
from src.base import Message, Prompt, Conversation, ThreadConfig
from src.utils import split_into_shorter_messages, close_thread, logger
from src.moderation import (
    send_moderation_flagged_message,
    send_moderation_blocked_message,
)

MY_BOT_NAME = BOT_NAME
MY_BOT_EXAMPLE_CONVOS = EXAMPLE_CONVOS

ASSISTANT_ID = os.environ.get("OPENAI_ASSISTANT_ID")
if not ASSISTANT_ID:
    logger.error("OPENAI_ASSISTANT_ID environment variable not set.")

POLL_INTERVAL_S = 0.5


class CompletionResult(Enum):
    OK = 0
    TOO_LONG = 1
    INVALID_REQUEST = 2
    OTHER_ERROR = 3
    MODERATION_FLAGGED = 4
    MODERATION_BLOCKED = 5


@dataclass
class CompletionData:
    status: CompletionResult
    reply_text: Optional[str]
    status_text: Optional[str]


client = AsyncOpenAI()

# chat with the assistant


async def generate_completion_response(
    openai_thread_id: str,
    last_user_message: str,
    user: str,
) -> CompletionData:

    if not ASSISTANT_ID:
        return CompletionData(
            status=CompletionResult.OTHER_ERROR,
            reply_text=None,
            status_text="Error: OPENAI_ASSISTANT_ID is not config.",
        )

    try:
        # add the user's message to the thread
        await client.beta.threads.messages.create(
            thread_id=openai_thread_id,
            role="user",
            content=last_user_message
        )

        # create a run to run the assistant
        run = await client.beta.threads.runs.create(
            thread_id=openai_thread_id,
            assistant_id=ASSISTANT_ID,
        )

        # wait for the run to complete
        while run.status in ["queued", "in_progress"]:
            await asyncio.sleep(POLL_INTERVAL_S)
            run = await client.beta.threads.runs.retrieve(
                thread_id=openai_thread_id,
                run_id=run.id
            )

        # check the final status of the run
        if run.status == "completed":
            # get the latest message from the thread
            messages = await client.beta.threads.messages.list(
                thread_id=openai_thread_id,
                order="desc",
                limit=1
            )

            reply = ""
            if messages.data and messages.data[0].role == "assistant":
                for content_part in messages.data[0].content:
                    if content_part.type == "text":
                        reply += content_part.text.value

            reply = reply.strip()

            if not reply:
                # return OK but no text, main.py will handle it
                return CompletionData(
                    status=CompletionResult.OK, reply_text=None, status_text="Assistant did not return a text message."
                )

            # moderate the response
            moderate_context = (last_user_message + reply)[-500:]
            flagged_str, blocked_str = moderate_message(
                message=moderate_context, user=user
            )

            if len(blocked_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_BLOCKED,
                    reply_text=reply,
                    status_text=f"from_response:{blocked_str}",
                )

            if len(flagged_str) > 0:
                return CompletionData(
                    status=CompletionResult.MODERATION_FLAGGED,
                    reply_text=reply,
                    status_text=f"from_response:{flagged_str}",
                )

            # All OK
            return CompletionData(
                status=CompletionResult.OK, reply_text=reply, status_text=None
            )

        # handle the error status of the run
        elif run.status == "failed":
            error_message = run.last_error.message if run.last_error else "Unknown error"
            logger.error(
                f"Run failed for thread {openai_thread_id}: {error_message}")
            return CompletionData(
                status=CompletionResult.OTHER_ERROR,
                reply_text=None,
                status_text=f"Run failed: {error_message}",
            )
        else:
            # handle the unhandled status of the run
            logger.error(f"Run ended with unhandled status: {run.status}")
            return CompletionData(
                status=CompletionResult.OTHER_ERROR,
                reply_text=None,
                status_text=f"Run ended with status: {run.status}",
            )

    except openai.BadRequestError as e:
        logger.exception(e)
        return CompletionData(
            status=CompletionResult.INVALID_REQUEST,
            reply_text=None,
            status_text=str(e),
        )
    except Exception as e:
        logger.exception(e)
        return CompletionData(
            status=CompletionResult.OTHER_ERROR, reply_text=None, status_text=str(e)
        )


async def process_response(
    user: str, thread: discord.Thread, response_data: CompletionData
):
    status = response_data.status
    reply_text = response_data.reply_text
    status_text = response_data.status_text
    if status is CompletionResult.OK or status is CompletionResult.MODERATION_FLAGGED:
        sent_message = None
        if not reply_text:
            sent_message = await thread.send(
                embed=discord.Embed(
                    description=f"**Invalid response** - empty response",
                    color=discord.Color.yellow(),
                )
            )
        else:
            shorter_response = split_into_shorter_messages(reply_text)
            for r in shorter_response:
                sent_message = await thread.send(r)
        if status is CompletionResult.MODERATION_FLAGGED:
            await send_moderation_flagged_message(
                guild=thread.guild,
                user=user,
                flagged_str=status_text,
                message=reply_text,
                url=sent_message.jump_url if sent_message else "no url",
            )

            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **This conversation has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )
    elif status is CompletionResult.MODERATION_BLOCKED:
        await send_moderation_blocked_message(
            guild=thread.guild,
            user=user,
            blocked_str=status_text,
            message=reply_text,
        )

        await thread.send(
            embed=discord.Embed(
                description=f"❌ **The response has been blocked by moderation.**",
                color=discord.Color.red(),
            )
        )
    elif status is CompletionResult.TOO_LONG:
        await close_thread(thread)
    elif status is CompletionResult.INVALID_REQUEST:
        await thread.send(
            embed=discord.Embed(
                description=f"**Invalid request** - {status_text}",
                color=discord.Color.yellow(),
            )
        )
    else:
        await thread.send(
            embed=discord.Embed(
                description=f"**Error** - {status_text}",
                color=discord.Color.yellow(),
            )
        )
