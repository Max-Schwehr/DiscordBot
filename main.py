"""Audit reaction status for a linked Discord message."""

import os
import re

import discord
from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# STUFF TO CONFIGURE IF NEEDED
# The channel where users run !audit and receive its report.
AUDIT_CHANNEL_ID = 1540596699075059763

# The channel containing announcement messages that may be audited.
ANNOUNCEMENT_CHANNEL_ID = 1540548385331875982

people = [
    {
        "discordName": "maxschwehr",
        "realName": "Max Schwehr",
    },
    {
        "discordName": "alice",
        "realName": "Alice Example",
    },
    {
        "discordName": "ben",
        "realName": "Ben Example",
    },
    {
        "discordName": "casey",
        "realName": "Casey Example",
    },
]




MESSAGE_LINK_PATTERN = re.compile(
    r"^https?://discord\.com/channels/(?:\d+|@me)/(\d+)/(\d+)/?$"
)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)



def message_ids_from_link(message_link: str) -> tuple[int, int]:
    """Extract channel and message IDs from a copied Discord message link."""
    match = MESSAGE_LINK_PATTERN.fullmatch(message_link.strip("<>"))
    if match is None:
        raise ValueError("Please provide a Discord message link.")

    return int(match.group(1)), int(match.group(2))


async def reaction_status(target_message: discord.Message) -> str:
    """Build a checkmark report for the tracked people on one message."""

    reacted_discord_names = set()
    for reaction in target_message.reactions:
        async for user in reaction.users():
            reacted_discord_names.add(user.name.casefold())

    lines = [f"Reaction status for [this message]({target_message.jump_url}):"]
    for person in people:
        did_react = person["discordName"].casefold() in reacted_discord_names
        status = "✅" if did_react else "❌"
        message = "reacted" if did_react else "did not react"
        lines.append(f'{status} {person["realName"]} {message}.')

    return "\n".join(lines)


@bot.command()
async def audit(ctx: commands.Context, message_link: str) -> None:
    """Reply with the tracked reaction status for a linked Discord message."""
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    try:
        channel_id, message_id = message_ids_from_link(message_link)
        if channel_id != ANNOUNCEMENT_CHANNEL_ID:
            raise ValueError("Please provide a message link from the announcement channel.")

        channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("That link does not point to a text message.")

        target_message = await channel.fetch_message(message_id)
        await ctx.send(await reaction_status(target_message))
    except (discord.DiscordException, TypeError, ValueError) as error:
        await ctx.send(f"Could not audit that message: {error}")


@bot.event
async def on_ready() -> None:
    """Confirm the bot is ready to receive audit commands."""
    print(f"Logged in as {bot.user}.")


if not TOKEN:
    raise ValueError("Set DISCORD_TOKEN in your .env file.")

bot.run(TOKEN)
