"""Audit reaction status for a linked Discord message."""

import asyncio
import os
import re
from typing import Optional
import requests
from datetime import datetime, timezone

import discord
from dotenv import load_dotenv
from discord.ext import commands

# Load local development values from .env; hosted environments use their own variables.
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

NOTION_API_URL = "https://api.notion.com/v1"
NOTION_API_VERSION = "2026-03-11"

# STUFF TO CONFIGURE IF NEEDED
# The channel where users run !audit and receive its report.
AUDIT_CHANNEL_ID = 1540806036666056844

# The channel containing announcement messages that may be audited.
ANNOUNCEMENT_CHANNEL_ID = 1011391665640058934


# Track each member by Discord username and show their real name in audit results.
people = [
    {
        "discordName": "maxschwehr",
        "realName": "Max Schwehr",
    },
    {
        "discordName": "benll",
        "realName": "Ben Lim",
    },
    {
        "discordName": "peanutt0",
        "realName": "Yinlin Cen",
    },
    {
        "discordName": "poerizm",
        "realName": "Matthew Mata",
    },
    {
        "discordName": "crombler._22440",
        "realName": "Andy Machado",
    },
    {
        "discordName": "adel_4131",
        "realName": "Adel Perez",
    },
    {
        "discordName": "keyboard3771",
        "realName": "Muqing Zhang",
    },
    {
        "discordName": "baiggg",
        "realName": "Muhmood Baig",
    },
    {
        "discordName": "vqierie",
        "realName": "Valerie Matamoros",
    },
    {
        "discordName": "jovelreyes",
        "realName": "Jovel Reyes",
    },
    {
        "discordName": "27_liyah",
        "realName": "Liyah Ortega",
    },
    {
        "discordName": "REPLACE_WITH_KELLYS_FULL_USERNAME",
        "realName": "Kelly Tan Liu",
    },
    {
        "discordName": "jayleen_22",
        "realName": "Jayleen Mendez",
    },
    {
        "discordName": "etin.427",
        "realName": "Ethan Nogoy",
    },
    {
        "discordName": "pankeji",
        "realName": "Amy Tang",
    },
    {
        "discordName": "meeperton_",
        "realName": "Kellan Farrell",
    },
    {
        "discordName": "tasffiii",
        "realName": "Tasfia Zaman",
    },
    {
        "discordName": "alexalandaverde",
        "realName": "Alexa Landaverde",
    },
    {
        "discordName": "henryirlam._30089",
        "realName": "Henry Irlam",
    },
]

# Match copied Discord message URLs and capture their channel and message IDs.
MESSAGE_LINK_PATTERN = re.compile(
    r"^https?://discord\.com/channels/(?:\d+|@me)/(\d+)/(\d+)/?$"
)

intents = discord.Intents.default()
# Required for Discord to deliver messages beginning with !audit to the bot.
intents.message_content = True
# Configure the command prefix and pass the intents required by discord.py.
bot = commands.Bot(command_prefix="!", intents=intents)
# Prevent duplicate ready messages after a temporary Discord reconnect.
has_announced_ready = False
# Store the names from the most recent successful audit for !deduct.
last_non_reactors: Optional[list[str]] = None


def message_ids_from_link(message_link: str) -> tuple[int, int]:
    """Extract the channel and message IDs from a copied Discord message link.

    The IDs uniquely identify the exact message the user wants to audit.
    """
    # Remove Discord's optional angle brackets before validating the pasted link.
    match = MESSAGE_LINK_PATTERN.fullmatch(message_link.strip("<>"))
    if match is None:
        raise ValueError("Please provide a Discord message link.")

    return int(match.group(1)), int(match.group(2))


async def reaction_status(target_message: discord.Message) -> tuple[str, list[str]]:
    """Build a reaction report and collect its non-reacting people.

    The collected names let !deduct create the corresponding Notion pages.
    """

    reacted_discord_names = set()
    for reaction in target_message.reactions:
        # The API returns users per emoji, so collect them into one shared set.
        async for user in reaction.users():
            reacted_discord_names.add(user.name.casefold())

    non_reactor_lines = []
    reactor_lines = []
    non_reactors = []
    for person in people:
        # Compare normalized usernames so uppercase and lowercase do not matter.
        did_react = person["discordName"].casefold() in reacted_discord_names
        status = "✅" if did_react else "❌"
        message = "reacted" if did_react else "did not react"
        line = f'{status} {person["realName"]} {message}.'
        if not did_react:
            non_reactor_lines.append(line)
            non_reactors.append(person["realName"])
        else:
            reactor_lines.append(line)

    lines = [f"Reaction status for [this message]({target_message.jump_url}):"]
    lines.extend(non_reactor_lines)
    lines.extend(reactor_lines)
    return "\n".join(lines), non_reactors


def notion_headers() -> dict[str, str]:
    """Build the authorization and version headers for Notion API calls."""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        raise ValueError("Set NOTION_API_KEY and NOTION_DATABASE_ID in .env.")

    return {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": NOTION_API_VERSION,
    }


def notion_property(properties: dict, expected_name: str) -> tuple[str, dict]:
    """Find a Notion property by name without relying on capitalization."""
    for property_name, property_config in properties.items():
        if property_name.casefold() == expected_name.casefold():
            return property_name, property_config

    raise ValueError(f"The Notion data source needs a '{expected_name}' property.")


def create_notion_pages(full_names: list[str]) -> int:
    """Create one Notion page per name and relate it to the matching member page."""
    headers = notion_headers()
    database_response = requests.get(
        f"{NOTION_API_URL}/databases/{NOTION_DATABASE_ID}",
        headers=headers,
        timeout=10,
    )
    database_response.raise_for_status()

    data_sources = database_response.json().get("data_sources", [])
    if len(data_sources) != 1:
        raise ValueError("The Notion database must contain exactly one data source.")

    data_source_id = data_sources[0]["id"]
    data_source_response = requests.get(
        f"{NOTION_API_URL}/data_sources/{data_source_id}",
        headers=headers,
        timeout=10,
    )
    data_source_response.raise_for_status()
    properties = data_source_response.json()["properties"]

    full_name_key, full_name_config = notion_property(properties, "Full name")
    member_key, member_config = notion_property(properties, "Member object")
    managed_key, managed_config = notion_property(properties, "Managed")
    full_name_type = full_name_config["type"]
    if full_name_type not in {"title", "rich_text"}:
        raise ValueError("The Notion 'Full name' property must be text.")
    if member_config["type"] != "relation":
        raise ValueError("The Notion 'Member object' property must be a relation.")
    if managed_config["type"] != "select":
        raise ValueError("The Notion 'Managed' property must be a select.")

    managed_options = managed_config["select"].get("options", [])
    if "No" not in {option["name"] for option in managed_options}:
        raise ValueError("The Notion 'Managed' property must include a 'No' option.")

    member_data_source_id = member_config["relation"]["data_source_id"]
    member_source_response = requests.get(
        f"{NOTION_API_URL}/data_sources/{member_data_source_id}",
        headers=headers,
        timeout=10,
    )
    member_source_response.raise_for_status()
    member_properties = member_source_response.json()["properties"]
    member_title_properties = [
        property_name
        for property_name, property_config in member_properties.items()
        if property_config["type"] == "title"
    ]
    if len(member_title_properties) != 1:
        raise ValueError("The related Members data source must have one title property.")

    member_title_key = member_title_properties[0]
    member_pages = []
    for full_name in full_names:
        member_query_response = requests.post(
            f"{NOTION_API_URL}/data_sources/{member_data_source_id}/query",
            headers=headers,
            json={
                "filter": {
                    "property": member_title_key,
                    "title": {"equals": full_name},
                },
                "page_size": 2,
            },
            timeout=10,
        )
        member_query_response.raise_for_status()
        matching_pages = member_query_response.json()["results"]
        if len(matching_pages) != 1:
            raise ValueError(f"Expected one Members page titled '{full_name}'.")

        member_pages.append((full_name, matching_pages[0]["id"]))

    for full_name, member_page_id in member_pages:
        text_value = [{"type": "text", "text": {"content": full_name}}]
        page_response = requests.post(
            f"{NOTION_API_URL}/pages",
            headers=headers,
            json={
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": {
                    full_name_key: {full_name_type: text_value},
                    member_key: {"relation": [{"id": member_page_id}]},
                    managed_key: {"select": {"name": "No"}},
                },
            },
            timeout=10,
        )
        page_response.raise_for_status()

    return len(full_names)


@bot.command()
async def audit(ctx: commands.Context, message_link: str) -> None:
    """Reply with the tracked reaction status for a linked announcement message.

    Commands outside the configured audit channel are intentionally ignored.
    """
    global last_non_reactors

    # Keep audit results limited to the channel selected in the configuration.
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    try:
        channel_id, message_id = message_ids_from_link(message_link)
        # Prevent users from auditing messages outside the announcement channel.
        if channel_id != ANNOUNCEMENT_CHANNEL_ID:
            raise ValueError("Please provide a message link from the announcement channel.")

        # Fetch the specific channel and message rather than relying on local cache data.
        channel = await bot.fetch_channel(channel_id)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("That link does not point to a text message.")

        target_message = await channel.fetch_message(message_id)
        status, non_reactors = await reaction_status(target_message)
        await ctx.send(status)
        last_non_reactors = non_reactors
    except (discord.DiscordException, TypeError, ValueError) as error:
        await ctx.send(f"Could not audit that message: {error}")


@bot.command()
async def deduct(ctx: commands.Context) -> None:
    """Add the non-reacting people from the latest audit to the Notion database."""
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    if last_non_reactors is None:
        await ctx.send("Run `!audit` before using `!deduct`.")
    elif not last_non_reactors:
        await ctx.send("Everyone reacted to the most recently audited message.")
    else:
        try:
            created_count = await asyncio.to_thread(create_notion_pages, last_non_reactors)
        except (KeyError, ValueError, requests.RequestException) as error:
            print(f"Could not add members to Notion: {error}")
            await ctx.send("Could not add members to Notion. Check the bot configuration.")
        else:
            await ctx.send(
                f"Added {created_count} member(s) to the list of upcoming deductions. "
                "Open the [Reaction Enforcement page](https://app.notion.com/p/"
                "3c403d7c745c80c4b15fd5c2c177154f?v=3c403d7c745c8066be06000c93c98ed1"
                "&source=copy_link) on Notion, and click `Deduct 1 Point` on each item."
            )


@bot.event
async def on_ready() -> None:
    """Announce that the bot is ready once per process start.

    Discord can reconnect without restarting the process, so this avoids repeat posts.
    """
    global has_announced_ready

    print(f"Logged in as {bot.user}.")
    if has_announced_ready:
        return

    try:
        # Prefer Discord's local channel cache, then fetch if it is unavailable.
        channel = bot.get_channel(AUDIT_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(AUDIT_CHANNEL_ID)

        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("The configured audit channel is not a text channel.")

        await channel.send(
            "Reaction Audits Ready! Use `!audit` followed by a space and the message "
            "link to check its reactions. "
        )
        has_announced_ready = True
    except (discord.DiscordException, TypeError) as error:
        print(f"Could not post the ready message: {error}")


# Stop immediately with a clear message if no token was supplied.
if not TOKEN:
    raise ValueError("Set DISCORD_TOKEN in your .env file.")

# Start the persistent Discord connection and begin accepting commands.
bot.run(TOKEN)
