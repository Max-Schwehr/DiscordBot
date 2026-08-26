"""Audit reaction status for a linked Discord message."""

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Optional
import requests
from datetime import date, datetime, timezone

import discord
from dotenv import load_dotenv
from discord.ext import commands

# Load local development values from .env; hosted environments use their own variables.
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")
NOTION_API_KEY = os.getenv("NOTION_API_KEY")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
NOTION_MEETING_DATABASE_ID = os.getenv("NOTION_MEETING_DATABASE_ID")
NOTION_MEMBERS_DATABASE_ID = os.getenv("NOTION_MEMBERS_DATABASE_ID")

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
# Required to look up tracked members before sending reaction reminders.
intents.members = True
# Configure the command prefix and pass the intents required by discord.py.
bot = commands.Bot(command_prefix="!", intents=intents)
# Prevent duplicate ready messages after a temporary Discord reconnect.
has_announced_ready = False
# Prevent duplicate birthday announcements after a temporary Discord reconnect.
has_checked_birthdays = False

@dataclass(frozen=True)
class ReactionAudit:
    """Keep the latest audit's recipients and announcement for follow-up commands."""

    non_reactors: list[dict[str, str]]
    announcement_url: str
    guild_id: int


# Store the latest successful audit for !deduct and !dmthem.
last_reaction_audit: Optional[ReactionAudit] = None


def message_ids_from_link(message_link: str) -> tuple[int, int]:
    """Extract the channel and message IDs from a copied Discord message link.

    The IDs uniquely identify the exact message the user wants to audit.
    """
    # Remove Discord's optional angle brackets before validating the pasted link.
    match = MESSAGE_LINK_PATTERN.fullmatch(message_link.strip("<>"))
    if match is None:
        raise ValueError("Please provide a Discord message link.")

    return int(match.group(1)), int(match.group(2))


async def reaction_status(
    target_message: discord.Message,
) -> tuple[str, list[dict[str, str]]]:
    """Build a reaction report and collect its non-reacting people.

    The collected names let !deduct create the corresponding Notion pages.
    """

    reacted_discord_names = set()
    # Treat the announcement's author as having participated without a reaction.
    reacted_discord_names.add(target_message.author.name.casefold())
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
            non_reactors.append(person)
        else:
            reactor_lines.append(line)

    lines = [f"Reaction status for [this message]({target_message.jump_url}):"]
    lines.extend(non_reactor_lines)
    lines.extend(reactor_lines)
    return "\n".join(lines), non_reactors


async def tracked_members(
    guild: discord.Guild, tracked_people: list[dict[str, str]]
) -> tuple[list[discord.Member], list[str]]:
    """Match configured Discord usernames to members in the audit's server."""
    tracked_by_username = {
        person["discordName"].casefold(): person for person in tracked_people
    }
    matched_members = {}

    # Fetch members because the cache is not guaranteed to include inactive members.
    async for member in guild.fetch_members(limit=None):
        username = member.name.casefold()
        if username in tracked_by_username:
            matched_members[username] = member
            if len(matched_members) == len(tracked_by_username):
                break

    missing_names = [
        person["realName"]
        for username, person in tracked_by_username.items()
        if username not in matched_members
    ]
    return list(matched_members.values()), missing_names


def notion_headers() -> dict[str, str]:
    """Build the authorization and version headers for Notion API calls."""
    if not NOTION_API_KEY:
        raise ValueError("Set NOTION_API_KEY in .env.")

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


def notion_data_source_id(headers: dict[str, str], database_id: str) -> str:
    """Return the single data source attached to the supplied Notion database."""
    database_response = requests.get(
        f"{NOTION_API_URL}/databases/{database_id}",
        headers=headers,
        timeout=10,
    )
    database_response.raise_for_status()

    data_sources = database_response.json().get("data_sources", [])
    if len(data_sources) != 1:
        raise ValueError("The Notion database must contain exactly one data source.")

    return data_sources[0]["id"]


def birthday_names_today() -> list[str]:
    """Return the names of Members rows with a Birthday date equal to today."""
    if not NOTION_MEMBERS_DATABASE_ID:
        raise ValueError("Set NOTION_MEMBERS_DATABASE_ID in .env.")

    headers = notion_headers()
    data_source_id = notion_data_source_id(headers, NOTION_MEMBERS_DATABASE_ID)
    data_source_response = requests.get(
        f"{NOTION_API_URL}/data_sources/{data_source_id}",
        headers=headers,
        timeout=10,
    )
    data_source_response.raise_for_status()
    properties = data_source_response.json()["properties"]
    birthday_key, birthday_config = notion_property(properties, "Birthday")
    if birthday_config["type"] != "date":
        raise ValueError("The Notion 'Birthday' property must be a date.")
    title_keys = [
        property_name
        for property_name, property_config in properties.items()
        if property_config["type"] == "title"
    ]
    if len(title_keys) != 1:
        raise ValueError("The Members data source must have one title property.")

    birthday_query_response = requests.post(
        f"{NOTION_API_URL}/data_sources/{data_source_id}/query",
        headers=headers,
        json={
            "filter": {
                "property": birthday_key,
                "date": {"equals": date.today().isoformat()},
            },
            "page_size": 100,
        },
        timeout=10,
    )
    birthday_query_response.raise_for_status()
    return [
        notion_text_value(page["properties"][title_keys[0]], "title")
        for page in birthday_query_response.json()["results"]
    ]


def notion_text_value(property_value: dict, property_type: str) -> str:
    """Read a comparable name from a Notion text, select, or multi-select property."""
    if property_type == "select":
        selected_option = property_value.get("select")
        return selected_option.get("name", "").strip() if selected_option else ""
    if property_type == "multi_select":
        selected_options = property_value.get("multi_select", [])
        if len(selected_options) != 1:
            return ""
        return selected_options[0].get("name", "").strip()

    return "".join(
        text_item.get("plain_text", "")
        for text_item in property_value.get(property_type, [])
    ).strip()


def matching_member_page_id(
    headers: dict[str, str], member_data_source_id: str, member_title_key: str, full_name: str
) -> str:
    """Find the one Members page whose title exactly matches the supplied full name."""
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

    return matching_pages[0]["id"]


def create_notion_pages(full_names: list[str]) -> int:
    """Create one Notion page per name and relate it to the matching member page."""
    if not NOTION_DATABASE_ID:
        raise ValueError("Set NOTION_DATABASE_ID in .env.")

    headers = notion_headers()
    data_source_id = notion_data_source_id(headers, NOTION_DATABASE_ID)
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
        member_page_id = matching_member_page_id(
            headers, member_data_source_id, member_title_key, full_name
        )
        member_pages.append((full_name, member_page_id))

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


def sync_member_relations() -> tuple[int, int, list[str]]:
    """Link unassigned meeting rows to Members pages with the same full-name value."""
    if not NOTION_MEETING_DATABASE_ID:
        raise ValueError("Set NOTION_MEETING_DATABASE_ID in .env.")

    headers = notion_headers()
    data_source_id = notion_data_source_id(headers, NOTION_MEETING_DATABASE_ID)
    data_source_response = requests.get(
        f"{NOTION_API_URL}/data_sources/{data_source_id}",
        headers=headers,
        timeout=10,
    )
    data_source_response.raise_for_status()
    properties = data_source_response.json()["properties"]

    full_name_key, full_name_config = notion_property(properties, "Full name")
    member_key, member_config = notion_property(properties, "Member object")
    full_name_type = full_name_config["type"]
    if full_name_type not in {"title", "rich_text", "select", "multi_select"}:
        raise ValueError(
            "The Notion 'Full name' property must be text, select, or multi-select."
        )
    if member_config["type"] != "relation":
        raise ValueError("The Notion 'Member object' property must be a relation.")

    member_data_source_id = member_config["relation"]["data_source_id"]
    member_source_response = requests.get(
        f"{NOTION_API_URL}/data_sources/{member_data_source_id}",
        headers=headers,
        timeout=10,
    )
    member_source_response.raise_for_status()
    member_title_properties = [
        property_name
        for property_name, property_config in member_source_response.json()["properties"].items()
        if property_config["type"] == "title"
    ]
    if len(member_title_properties) != 1:
        raise ValueError("The related Members data source must have one title property.")

    updated_count = 0
    skipped_count = 0
    unmatched_names = []
    member_page_ids = {}
    next_cursor = None
    while True:
        query = {"page_size": 100}
        if next_cursor is not None:
            query["start_cursor"] = next_cursor
        board_query_response = requests.post(
            f"{NOTION_API_URL}/data_sources/{data_source_id}/query",
            headers=headers,
            json=query,
            timeout=10,
        )
        board_query_response.raise_for_status()
        board_results = board_query_response.json()

        for board_page in board_results["results"]:
            board_properties = board_page["properties"]
            if board_properties[member_key]["relation"]:
                skipped_count += 1
                continue

            full_name = notion_text_value(
                board_properties[full_name_key], full_name_type
            )
            if not full_name:
                unmatched_names.append("(empty Full name)")
                continue

            try:
                member_page_id = member_page_ids.get(full_name)
                if member_page_id is None:
                    member_page_id = matching_member_page_id(
                        headers,
                        member_data_source_id,
                        member_title_properties[0],
                        full_name,
                    )
                    member_page_ids[full_name] = member_page_id
            except ValueError:
                unmatched_names.append(full_name)
                continue

            page_response = requests.patch(
                f"{NOTION_API_URL}/pages/{board_page['id']}",
                headers=headers,
                json={
                    "properties": {
                        member_key: {"relation": [{"id": member_page_id}]},
                    },
                },
                timeout=10,
            )
            page_response.raise_for_status()
            updated_count += 1

        if not board_results.get("has_more"):
            break
        next_cursor = board_results["next_cursor"]

    return updated_count, skipped_count, unmatched_names


@bot.command()
async def audit(ctx: commands.Context, message_link: str) -> None:
    """Reply with the tracked reaction status for a linked announcement message.

    Commands outside the configured audit channel are intentionally ignored.
    """
    global last_reaction_audit

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
        if target_message.guild is None:
            raise TypeError("That link does not point to a server message.")
        last_reaction_audit = ReactionAudit(
            non_reactors=non_reactors,
            announcement_url=target_message.jump_url,
            guild_id=target_message.guild.id,
        )
    except (discord.DiscordException, TypeError, ValueError) as error:
        await ctx.send(f"Could not audit that message: {error}")


@bot.command()
async def deduct(ctx: commands.Context) -> None:
    """Add the non-reacting people from the latest audit to the Notion database."""
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    if last_reaction_audit is None:
        await ctx.send("Run `!audit` before using `!deduct`.")
    elif not last_reaction_audit.non_reactors:
        await ctx.send("Everyone reacted to the most recently audited message.")
    else:
        try:
            created_count = await asyncio.to_thread(
                create_notion_pages,
                [person["realName"] for person in last_reaction_audit.non_reactors],
            )
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


@bot.command(name="dmthem")
async def dm_them(ctx: commands.Context) -> None:
    """DM every member who missed the reaction in the most recent audit."""
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    if last_reaction_audit is None:
        await ctx.send("Run `!audit` before using `!dmthem`.")
        return
    if not last_reaction_audit.non_reactors:
        await ctx.send("Everyone reacted to the most recently audited message.")
        return

    guild = bot.get_guild(last_reaction_audit.guild_id)
    if guild is None:
        await ctx.send("Could not find the server for the most recently audited message.")
        return

    members, missing_names = await tracked_members(guild, last_reaction_audit.non_reactors)
    sent_count = 0
    failed_names = []
    for member in members:
        try:
            await member.send(
                "Hi! Please react to this announcement when you can: "
                f"{last_reaction_audit.announcement_url}"
            )
            sent_count += 1
        except discord.Forbidden:
            failed_names.append(member.display_name)
        except discord.HTTPException:
            failed_names.append(member.display_name)

    summary = f"Sent {sent_count} reaction reminder(s)."
    if missing_names:
        summary += f" Could not find: {', '.join(missing_names)}."
    if failed_names:
        summary += f" Could not DM: {', '.join(failed_names)}."
    await ctx.send(summary)


@bot.command(name="syncmembers")
async def sync_members(ctx: commands.Context) -> None:
    """Fill blank Member object relations from matching Full name values."""
    if ctx.channel.id != AUDIT_CHANNEL_ID:
        return

    try:
        updated_count, skipped_count, unmatched_names = await asyncio.to_thread(
            sync_member_relations
        )
    except (KeyError, ValueError, requests.RequestException) as error:
        print(f"Could not sync member relations in Notion: {error}")
        await ctx.send("Could not sync member relations. Check the bot configuration.")
        return

    message = (
        f"Linked {updated_count} meeting item(s) to Members. "
        f"Skipped {skipped_count} item(s) that already had a member relation."
    )
    if unmatched_names:
        unmatched_list = ", ".join(sorted(set(unmatched_names)))
        message += f" No unique Members match was found for: {unmatched_list}."
    await ctx.send(message)


async def announce_today_birthdays() -> None:
    """Post one birthday message for each Members row that matches today's date."""
    global has_checked_birthdays

    # The external scheduler may start the bot twice daily; only the earlier run posts.
    if has_checked_birthdays or datetime.now().hour >= 16:
        return

    try:
        birthday_names = await asyncio.to_thread(birthday_names_today)
        if not birthday_names:
            has_checked_birthdays = True
            return

        # Prefer Discord's local channel cache, then fetch if it is unavailable.
        channel = bot.get_channel(ANNOUNCEMENT_CHANNEL_ID)
        if channel is None:
            channel = await bot.fetch_channel(ANNOUNCEMENT_CHANNEL_ID)
        if not isinstance(channel, (discord.TextChannel, discord.Thread)):
            raise TypeError("The configured announcement channel is not a text channel.")

        member_role = discord.utils.get(channel.guild.roles, name="Member")
        if member_role is None:
            raise ValueError("Could not find a Discord role named 'Member'.")

        for name in birthday_names:
            await channel.send(
                f"{member_role.mention} Please wish {name} a happy birthday!!!! 🎈🎈🎉🎊 "
                "https://klipy.com/gifs/birthday-geburtstag-1",
                allowed_mentions=discord.AllowedMentions(roles=[member_role]),
            )
        has_checked_birthdays = True
    except (
        discord.DiscordException,
        KeyError,
        TypeError,
        ValueError,
        requests.RequestException,
    ) as error:
        print(f"Could not post the birthday announcement: {error}")


@bot.event
async def on_ready() -> None:
    """Announce that the bot is ready once per process start.

    Discord can reconnect without restarting the process, so this avoids repeat posts.
    """
    global has_announced_ready

    print(f"Logged in as {bot.user}.")
    if has_announced_ready:
        return

    await announce_today_birthdays()

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
