# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
import asyncio
from textwrap import dedent
from report import *
from reactions import Reaction, ReactionDelegator
from time import time


# Controls whether messages in DMs with the bot should also be monitored
# This is for testing and is False by default
FILTER_DMS = False

# Controls whether message that are hidden behind spoilers should account for adversarial markdown formatting
# Trying to "|| a message ||" behind spoilers leads to "|||| a message ||||" (i.e., the spoilers cancel each other out)
# Turning this on will look for that and other markdown tricks (like ``` code blocks ```) for trying to get around spoilers
# This is True by default
SMART_SPOILERS = False
# DM the bot .debug smart_spoilers enable/disable/toggle to turn them on and off


# Set up logging to the console
logger = logging.getLogger('discord')
logger.setLevel(logging.DEBUG)
handler = logging.FileHandler(filename='discord.log', encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)

# There should be a file called 'token.json' inside the same folder as this file
token_path = 'tokens.json'
if not os.path.isfile(token_path):
    raise Exception(f"{token_path} not found!")
with open(token_path) as f:
    # If you get an error here, it means your token is formatted incorrectly. Did you put it in quotes?
    tokens = json.load(f)
    discord_token = tokens['discord']
    perspective_key = tokens['perspective']


class ModBot(discord.Client, ReactionDelegator):
    smart_spoilers = SMART_SPOILERS
    def __init__(self, key):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None   
        self.flows = {}
        self.messages_pending_edit = {}
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.user_reports = {} # Map from user IDs to the state of their report
        self.reviewing_messages = {} # Map from user IDs to a boolean indicating if they are reviewing content
        self.perspective_key = key
        self.message_aliases = {}
        self.message_pairs = {}
        self.helpers = {}
        self.last_messages = {}

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is in these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
        if match:
            self.group_num = match.group(1)
        else:
            raise Exception("Group number not found in bot's name. Name format should be \"Group # Bot\".")
        
        # Find the mod channel in each guild that this bot should report to
        for guild in self.guilds:
            for channel in guild.text_channels:
                if channel.name == f'group-{self.group_num}-mod':
                    self.mod_channels[guild.id] = channel

    async def on_message(self, message):
        '''
        This function is called whenever a message is sent in a channel that the bot can see (including DMs). 
        Currently the bot is configured to only handle messages that are sent over DMs or in your group's "group-#" channel. 
        '''
        # Ignore messages from us 
        if message.author.id == self.user.id:
            return

        self.last_messages[message.author.id] = time()
        
        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def on_raw_message_edit(self, payload):
        # Try to get the guild ID
        guild_id = payload.data.get("guild_id", None)
        if guild_id is None:
            return

        message = await self.get_guild(int(guild_id)).get_channel(payload.channel_id).fetch_message(payload.message_id)

        if message.content.strip() == "":
            return

        scores = self.eval_text(message)

        if payload.message_id in self.messages_pending_edit:
            return await self.messages_pending_edit[payload.message_id].edited(message)

        if scores["SEXUALLY_EXPLICIT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.SEXUAL)
        elif scores["SEVERE_TOXICITY"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.HARASS)
        elif scores["THREAT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.VIOLENCE)
        elif scores["IDENTITY_ATTACK"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.HATEFUL)
        elif scores["SEXUALLY_EXPLICIT"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.SEXUAL)
        elif scores["THREAT"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.VIOLENCE)
        elif scores["IDENTITY_ATTACK"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HATEFUL)
        elif scores["TOXICITY"] > 0.9 or scores["INSULT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HARASS)
        elif scores["FLIRTATION"] > 0.8:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HARASS, explanation="as flirtation")
        elif scores["SPAM"] > 0.8:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.SPAM)

    async def notify_user_edit_message(self, message, explicit=False, reason=None, explanation=""):
        message.author.dm_channel or await message.author.create_dm()
        flow = EditedBadMessageFlow(
            client=self,
            message=message,
            explicit=explicit,
            reason=reason,
            explanation=explanation
        )
        self.messages_pending_edit[message.id] = flow
        self.flows[message.author.id] = self.flows.get(message.author.id, [])
        self.flows[message.author.id].append(flow)

    async def handle_dm(self, message):
        content = message.content.strip()

        author_id = message.author.id
        responses = []

        # Ensure there is a DM channel between us and the user (which there should be since we are handling a DM message, but just in case)
        message.author.dm_channel or await message.author.create_dm()

        # Handle smart_spoilers
        if content.lower() == ".debug smart_spoilers toggle":
            self.smart_spoilers = not self.smart_spoilers
            await message.channel.send(embed=discord.Embed(description=f"Smart spoilers have been {'enabled' if self.smart_spoilers else 'disabled'}."))
            return
        if content.lower() == ".debug smart_spoilers enable":
            self.smart_spoilers = True
            await message.channel.send(embed=discord.Embed(description="Smart spoilers have been enabled."))
            return
        if content.lower() == ".debug smart_spoilers disable":
            self.smart_spoilers = False
            await message.channel.send(embed=discord.Embed(description="Smart spoilers have been disabled."))
            return

        if len(self.flows.get(message.author.id, [])):
            return await self.flows[message.author.id][-1].forward_message(message)

        # Check if the user is currently reviewing one of their own messages
        if self.reviewing_messages.get(author_id, False):
            if message.content.lower() in YES_KEYWORDS:
                await self.allow_user_message(**self.reviewing_messages[author_id])
            elif message.content.lower() in NO_KEYWORDS:
                await self.reject_user_message(**self.reviewing_messages[author_id])
            else:
                await message.channel.send(content="I didn't understand that. Reply with either `yes` or `no`.")
            return

        # Check if the user has an open report associated with them
        if author_id in self.user_reports:
            return await self.user_reports[author_id].forward_message(message)

        # Handle a report message
        if content.lower() in START_KEYWORDS:
            # Start a new UserReportCreationFlow
            self.user_reports[author_id] = UserReportCreationFlow(self, message.author)
            return

        # Starts an AddHelpersFlow to add more helpers
        if re.search(r"^(?:add\shelpers?|helpers?\sadd)$", content.lower()):
            self.flows[message.author.id] = self.flows.get(message.author.id, [])
            self.flows[message.author.id].append(AddHelpersFlow(
                client=self,
                user=message.author
            ))
            return

        # Shows a list of helpers for the user
        if re.search(r"^(?:list\shelpers?|helpers?\slist)$", content.lower()):
            helpers = self.helpers.get(message.author.id, None)
            if helpers is None or len(helpers) == 0:
                await message.channel.send("You don't have any helpers. Say `add helpers` to add some!")
                return
            helpers = "\n".join(f" {i+1}. {helper.mention} – **{helper.display_name}**#{helper.discriminator}" for i, helper in enumerate(helpers))
            await message.channel.send(f"Your helpers are:\n{helpers}\nSay `add helpers` or `remove helpers` to add or remove them.")
            return

        if re.search(r"^(?:(?:remove|delete)\shelpers?|helpers?\s(?:remove|delete))$", content.lower()):
            if not self.helpers.get(message.author.id, False):
                await message.channel.send("You don't have any helpers to remove. Say `add helpers` to add some!")
                return
            self.flows[message.author.id] = self.flows.get(message.author.id, [])
            self.flows[message.author.id].append(RemoveHelpersFlow(
                client=self,
                user=message.author
            ))
            return

        if re.search(r"^(?:helpers?(?:\shelp)?|help\shelpers?)$", content.lower()):
            await message.channel.send("Say `add helpers` to add helpers, `list helpers` to list your current helpers, or `remove helpers` to remove helpers.")
            return
        
        # Handle a help message
        if content.lower() in HELP_KEYWORDS:
            await message.channel.send("Use the `report` command to begin the reporting process.")
            return

        # Tell the user how to start a new report
        await message.channel.send("You do not have a report open; use the `report` command to begin the reporting process, or use `help` for more help.")
        # Filter this message if FILTER_DMS is on
        if FILTER_DMS:
            await self.handle_channel_message(message)


    async def handle_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel or DMs
        if not (FILTER_DMS and isinstance(message.channel, discord.DMChannel)) and message.channel.name != f'group-{self.group_num}':
           return

        if message.content.strip() == "":
            return

        scores = self.eval_text(message)

        if scores["SEXUALLY_EXPLICIT"] > 0.9:
            return await self.confirm_user_message(message, explicit=True, reason=AbuseType.SEXUAL)
        elif scores["SEVERE_TOXICITY"] > 0.9:
            return await self.confirm_user_message(message, explicit=True, reason=AbuseType.HARASS)
        elif scores["THREAT"] > 0.9:
            return await self.confirm_user_message(message, explicit=True, reason=AbuseType.VIOLENCE)
        elif scores["IDENTITY_ATTACK"] > 0.9:
            return await self.confirm_user_message(message, explicit=True, reason=AbuseType.HATEFUL)
        elif scores["SEXUALLY_EXPLICIT"] > 0.75:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.SEXUAL)
        elif scores["THREAT"] > 0.75:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.VIOLENCE)
        elif scores["IDENTITY_ATTACK"] > 0.75:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.HATEFUL)
        elif scores["TOXICITY"] > 0.9 or scores["INSULT"] > 0.9:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.HARASS)
        elif scores["FLIRTATION"] > 0.8:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.HARASS, explanation="as flirtation")
        elif scores["SPAM"] > 0.8:
            return await self.confirm_user_message(message, explicit=False, reason=AbuseType.SPAM)


    def eval_text(self, message):
        '''
        Given a message, forwards the message to Perspective and returns a dictionary of scores.
        '''
        PERSPECTIVE_URL = 'https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze'

        url = PERSPECTIVE_URL + '?key=' + self.perspective_key
        data_dict = {
            'comment': {
                'text': message.content
            },
            'languages': ['en'],
            'requestedAttributes': {
                'SEVERE_TOXICITY': {},
                'IDENTITY_ATTACK': {},
                'INSULT': {},
                'THREAT': {},
                'TOXICITY': {},
                'SPAM': {},
                'SEXUALLY_EXPLICIT': {},
                'FLIRTATION': {}
            },
            'doNotStore': True
        }
        response = requests.post(url, data=json.dumps(data_dict))
        response_dict = response.json()

        scores = {}
        for attr in response_dict["attributeScores"]:
            scores[attr] = response_dict["attributeScores"][attr]["summaryScore"]["value"]

        return scores


    async def confirm_user_message(self, message, explicit=False, reason=None, explanation=""):
        # DMs a user asking if they are sure they want to send a message
        # explicit indicates whether the message should be hidden if they do decide to send it
        # reason is the reason for flagging the initial message (should be an AbuseType)

        # message.delete() fails in DMs
        try:
            await message.delete()
        except:
            pass

        confirmUserMessageSession = {
            "message": message,
            "explicit": explicit,
            "reason": reason
        }
        self.reviewing_messages[message.author.id] = confirmUserMessageSession

        # Build an Embed to show them their initial message
        dmChannel = message.author.dm_channel or await message.author.create_dm()
        msgEmbed = discord.Embed(
            color=discord.Color.greyple(),
            description=message.content
        )
        msgEmbed.set_author(name=message.author.display_name, icon_url=message.author.avatar_url)

        if explanation:
            textReason = " " + explanation
        elif reason:
            textReason = {
                AbuseType.SPAM: " as spam",
                AbuseType.VIOLENCE: " for inciting violence",
                AbuseType.HATEFUL: " as hateful",
                AbuseType.HARASS: " as toxic",
                AbuseType.SEXUAL: " as overtly sexual"
            }[reason]
        else:
            textReason = ""

        # Show the user their initial message
        await dmChannel.send(content=f"Your message was flagged{textReason} and removed:", embed=msgEmbed)
        # Ask if they really want to send it
        # If it's marked as explicit, show that their message will be hidden behind a || spoiler ||
        lastMsg = await dmChannel.send(content="Are you sure you want to send this message?" + (" It will be hidden from most users unless they decide to interact with the message." if explicit else ""))

        # Add a Yes Reaction to choose to continue sending the message
        # Saying the word `yes` does the same thing
        await Reaction(
            "✅",
            click_handler=lambda self, client, reaction, user: \
                client.reviewing_messages.get(user.id, False) is confirmUserMessageSession and \
                asyncio.create_task(client.allow_user_message(**client.reviewing_messages[user.id]))
        ).register_message(lastMsg)

        # Add a No Reaction to prevent sending the message
        # Saying the word `no` does the same thing
        await Reaction(
            "🚫",
            click_handler=lambda self, client, reaction, user: \
                client.reviewing_messages.get(user.id, False) is confirmUserMessageSession and \
                asyncio.create_task(client.reject_user_message(**client.reviewing_messages[user.id])) 
        ).register_message(lastMsg)

    async def allow_user_message(self, message, explicit=False, reason=None):
        # This is run when a user decides to send a message that the bot flagged.

        # Get the original message channel
        origChannel = message.channel

        sentMsg = None
        prefixMsg = None

        # An "explicit" message is shown in spoilers to be the equivalent of Instagram's "Show Sensitive Content" functionality
        if explicit:
            content = message.content

            if self.smart_spoilers:
                # This alters the message slightly to disallow clever markdown formatting from getting through the spoiler
                # Displayed code block elements are converted into inline code blocks since displayed code blocks are not hidden by spoilers
                reMatch = re.search(r"```(?:\S*\n)?([\s\S]*?)\n?```", content)
                while reMatch:
                    code = reMatch.group(1).split("\n")
                    longestLine = max(map(lambda line: len(line), code))
                    code = "\n".join(f"`{{:{longestLine}}}`".format(line) for line in code)
                    content = content[:reMatch.start()] + code + content[reMatch.end():]
                    reMatch = re.search(r"```(?:\S*\n)?([\s\S]*?)\n?```", content)

                # Now, any "||" in code blocks are converted to a look-alike (by inserting a zero-width space in between them)
                # This is to prevent them from being recognized as closing spoiler elements
                # Outside of code blocks, we can just escape the double bars with a "\|" but code blocks will show the literal "\"
                reMatch = re.search(r"(`(?:[^`]|\|(?!\|))*?\|)(\|(?:[^`]|\|(?!\|))*?`)", content)
                while reMatch:
                    content = content[:reMatch.start()] + reMatch.group(1) + "\u200b" + reMatch.group(2) + content[reMatch.end():]
                    reMatch = re.search(r"(`(?:[^`]|\|(?!\|))*?\|)(\|(?:[^`]|\|(?!\|))*?`)", content)

                # Remove any remaining spoiler tags in the comment by escaping each "|"
                content = content.replace("||", "\\|\\|")

            # Send a message to show who this message is from
            prefixMsg = await origChannel.send(content=f"*The following message may contain inappropriate content. Click the black bar to reveal it.*\n*{message.author.mention} says:*")
            
            # Send the hidden message
            sentMsg = await origChannel.send(content="||" + content + "||")
        else:
            # Send a message to show who this message is from
            prefixMsg = await origChannel.send(content=f"*{message.author.mention} says:*")
            # Show a non-explicit message as-is
            sentMsg = await origChannel.send(content=message.content)

        # Show the user that their message was sent
        await (message.author.dm_channel or await message.author.create_dm()).send(
            content="Your original message has been re-sent. You can jump to it by clicking below. Thank you for taking the time to reconsider your message:",
            embed=discord.Embed(description=f"[Go to your message]({sentMsg.jump_url})", color=discord.Color.blue())
        )
        self.reviewing_messages[message.author.id] = False

        self.message_aliases[prefixMsg.id] = message
        self.message_aliases[sentMsg.id] = message

        self.message_pairs[sentMsg.id] = prefixMsg
        self.message_pairs[prefixMsg.id] = sentMsg

        if reason:
            urgency = {
                AbuseType.SPAM: 0,
                AbuseType.VIOLENCE: 2,
                AbuseType.SEXUAL: 1,
                AbuseType.HATEFUL: 1,
                AbuseType.HARASS: 1
            }[reason]
        else:
            urgency = 0

        report = AutomatedReport(
            client=self,
            urgency=urgency,
            abuse_type=reason,
            message=message,
            replacement_message=sentMsg,
            prefix_message=prefixMsg,
            message_hidden=explicit,
            message_deleted=False
        )

        await asyncio.gather(*(report.send_to_channel(channel, assignable=True) for channel in self.mod_channels.values()))

        for member in origChannel.members:
            now = time()
            # Only look for users who have sent a message in the last hour
            # this is to prevent spam for people who are offline
            if member.id in self.last_messages:
                if now - self.last_messages[member.id] > 60 * 60:
                    # Remove them from the list of active users
                    del self.last_messages[member.id]
                else:
                    await self.send_helper_message(member, sentMsg)

    async def reject_user_message(self, message, explicit=False, reason=None):
        await (message.author.dm_channel or await message.author.create_dm()).send(content="Thank you for taking the time to reconsider your message.")
        self.reviewing_messages[message.author.id] = False

    async def send_helper_message(self, member, message):
        if member.id not in self.helpers:
            return
        if message.id in self.message_aliases:
            orig_message = self.message_aliases[message.id]
            embed = discord.Embed(
                color=discord.Color.blurple(),
                description=f"[Jump to message]({message.jump_url})\n{orig_message.content}"
            ).set_author(name=orig_message.author.display_name, icon_url=orig_message.author.avatar_url)
        else:
            embed = discord.Embed(
                color=discord.Color.blurple(),
                description=f"[Jump to message]({message.jump_url})\n{message.content}"
            ).set_author(name=message.author.display_name, icon_url=message.author.avatar_url)
        for helper in self.helpers[member.id]:
            channel = helper.dm_channel or await helper.create_dm()
            await channel.send(content=f"{member.mention} wants you to review this message as one of their helpers:", embed=embed)
            lastMsg = await channel.send(content="Do you want to start a user report for this message on the behalf.")

client = ModBot(perspective_key)
client.run(discord_token)