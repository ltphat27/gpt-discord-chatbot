from enum import Enum
from dataclasses import dataclass
import openai
from openai import AsyncOpenAI
import os
import asyncio

from typing import Optional, List
from src.constants import (
    BOT_NAME,
    EXAMPLE_CONVOS,
)

import discord
from src.utils import split_into_shorter_messages, close_thread, logger

from src.constants import BOT_INSTRUCTIONS, DEFAULT_MODEL

MY_BOT_NAME = BOT_NAME
MY_BOT_EXAMPLE_CONVOS = EXAMPLE_CONVOS

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


client = AsyncOpenAI(
    api_key=os.environ["COMPASS_LLM_KEY"],
    base_url='https://compass.llm.shopee.io/compass-api/v1',
)

default_model = DEFAULT_MODEL
system_prompt = BOT_INSTRUCTIONS


def format_results(results):
    formatted_results = ''
    for result in results.data:
        formatted_result = f"<result file_id='{result.file_id}' file_name='{result.filename}'>"
        for part in result.content:
            formatted_result += f"<content>{part.text}</content>"
        formatted_results += formatted_result + "</result>"
    return f"<sources>{formatted_results}</sources>"

# chat with the assistant


async def generate_completion_response(
    openai_thread_id: str,
    last_user_message: str,
    user: str,
) -> CompletionData:

    try:
        vector_stores = await client.vector_stores.list(
            limit=1,
            order="desc"
        )

        if not vector_stores.data:
            return CompletionData(
                status=CompletionResult.OTHER_ERROR,
                reply_text=None,
                status_text="Error: Could not find any vector store.",
            )

        vector_store_id = vector_stores.data[0].id
        user_query = last_user_message

        results = await client.vector_stores.search(
            vector_store_id=vector_store_id,
            query=user_query,
        )

        formatted_results = format_results(results)

        completion = await client.chat.completions.create(
            model=default_model,
            temperature=0.1,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": f"Sources: {formatted_results}\n\nQuery: '{user_query}'"
                }
            ],
        )

        reply = completion.choices[0].message.content
        reply = reply.strip()

        if not reply:
            return CompletionData(
                status=CompletionResult.OK, reply_text=None, status_text="Assistant did not return a text message."
            )

        return CompletionData(
            status=CompletionResult.OK, reply_text=reply, status_text=None
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

            await thread.send(
                embed=discord.Embed(
                    description=f"⚠️ **This conversation has been flagged by moderation.**",
                    color=discord.Color.yellow(),
                )
            )
    elif status is CompletionResult.MODERATION_BLOCKED:

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
