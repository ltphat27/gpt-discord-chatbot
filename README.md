# GPT Discord Bot

Example Discord bot written in Python that uses the [Assistant API](https://platform.openai.com/docs/api-reference/assistants/createAssistant) to have conversations with the assistant, and the [moderations API](https://platform.openai.com/docs/guides/moderation) to filter the messages.

This bot uses the [OpenAI Python Library](https://github.com/openai/openai-python) and [discord.py](https://discordpy.readthedocs.io/).

The original repository: https://github.com/openai/gpt-discord-bot


# Features

- `@botname` to send a question with the chatbot in any chat channel.
- `/chat` starts a public thread, with a `message` argument which is the first user message passed to the bot. You can optionally also adjust the `temperature` and `max_tokens` parameters.
- The model will generate a reply for every user message in any threads started with `/chat`
- The entire thread will be passed to the model for each request, so the model will remember previous messages in the thread
- When the context limit is reached, or a max message count is reached in the thread, bot will close the thread
  
# Setup

1. Copy `.env.example` to `.env` and start filling in the values as detailed below
   
1. Go to https://platform.openai.com/api-keys, create a new API key, and fill in `OPENAI_API_KEY`
   
1. Go to https://platform.openai.com/assistants, create a new Assistant
    - Find the assistant ID and fill `OPENAI_ASSISTANT_ID`. Example ID: **asst_*****
    - You can change the model, the default model I use is the `gpt-4o-mini`
    - You can customize the bot instructions by modifying `System instructions`
    - Enable `File search` tool to allow assistant search file in the vector store
    - Below the `File search` tool, you can add knowledge bases for the assistant.
    
1. Create your own Discord application at https://discord.com/developers/applications
   
1. Go to the Bot tab and click "Add Bot"
    - Click "Reset Token" and fill in `DISCORD_BOT_TOKEN`
    - Disable "Public Bot" unless you want your bot to be visible to everyone
    - Enable "Message Content Intent" under "Privileged Gateway Intents"
1. Go to the OAuth2 tab, copy your "Client ID", and fill in `DISCORD_CLIENT_ID`
   
1. Copy the ID the server you want to allow your bot to be used in by right clicking the server icon and clicking "Copy ID". Fill in `ALLOWED_SERVER_IDS`. If you want to allow multiple servers, separate the IDs by "," like `server_id_1,server_id_2`. If you don't see the "Copy ID" option, please check if you have enabled **Developer Mode** on your Discord.
   
1. Install dependencies and run the bot
    ```
    pip install -r requirements.txt
    python -m src.main
    ```
    - You should see an invite URL in the console. Copy and paste it into your browser to add the bot to your server.
    - Note: make sure you are using Python 3.9+ (check with python --version)
    - I am currently using Python 3.11.9 (configured in .python-version)

# Optional configuration

1. If you want moderation messages, create and copy the channel id for each server that you want the moderation messages to send to in `SERVER_TO_MODERATION_CHANNEL`. This should be of the format: `server_id:channel_id,server_id_2:channel_id_2`
1. If you want to change the moderation settings for which messages get flagged or blocked, edit the values in `src/constants.py`. A higher value means less chance of it triggering, with 1.0 being no moderation at all for that category.

# FAQ

> Why isn't my bot responding to commands?

Ensure that the channels your bots have access to allow the bot to have these permissions.
- Send Messages
- Send Messages in Threads
- Create Public Threads
- Manage Messages (only for moderation to delete blocked messages)
- Manage Threads
- Read Message History
- Use Application Commands
