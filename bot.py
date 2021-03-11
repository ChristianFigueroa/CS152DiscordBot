# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import asyncio
from textwrap import dedent
from report import *
from flow import *
from reactions import Reaction, ReactionDelegator
from time import time
from content_reviewer import ContentReviewer, CSAM_SCORE_THRESHOLD
from azure.cognitiveservices.vision.computervision import ComputerVisionClient
from msrest.authentication import CognitiveServicesCredentials
from consts import *


# Controls whether messages in DMs with the bot should also be monitored
# This is for testing and is False by default
FILTER_DMS = False

# Controls whether message that are hidden behind spoilers should account for adversarial markdown formatting
# Trying to "|| a message ||" behind spoilers leads to "|||| a message ||||" (i.e., the spoilers cancel each other out)
# Turning this on will look for that and other markdown tricks (like ``` code blocks ```) for trying to get around spoilers
# This is True by default
SMART_SPOILERS = True
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
    azure_key = tokens["azure"]
    azure_endpoint = tokens["azure_endpoint"]

class ModBot(discord.Client, ReactionDelegator):
    smart_spoilers = SMART_SPOILERS
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(intents=intents)
        self.group_num = None   
        self.flows = {}
        self.messages_pending_edit = {}
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.message_aliases = {}
        self.message_pairs = {}
        self.reviewer = ContentReviewer()

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is in these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search(r"[gG]roup (\d+) [bB]ot", self.user.name)
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

        try:
            message = await self.get_guild(int(guild_id)).get_channel(payload.channel_id).fetch_message(payload.message_id)
        except discord.errors.NotFound:
            # Do nothing for messages that no longer exist
            return

        # If the message that was edited was the bot's own message, do nothing
        if message.author.id == self.user.id:
            return

        if message.content.strip() == "":
            return

        scores = self.reviewer.review_text(message)

        if payload.message_id in self.messages_pending_edit:
            return await self.messages_pending_edit[payload.message_id].edited(message)

        if scores["SEXUALLY_EXPLICIT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.SEXUAL, explanation="as sexually explicit")
        elif scores["SEVERE_TOXICITY"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.HARASS, explanation="as toxic")
        elif scores["THREAT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.VIOLENCE, explanation="as threatening")
        elif scores["IDENTITY_ATTACK"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=True, reason=AbuseType.HATEFUL, explanation="as hateful")
        elif scores["SEXUALLY_EXPLICIT"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.SEXUAL, explanation="as sexually explicit")
        elif scores["THREAT"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.VIOLENCE, explanation="for inciting violence")
        elif scores["IDENTITY_ATTACK"] > 0.75:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HATEFUL, explanation="as hateful")
        elif scores["TOXICITY"] > 0.9 or scores["INSULT"] > 0.9:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HARASS, explanation="as toxic")
        elif scores["FLIRTATION"] > 0.8:
            return await self.notify_user_edit_message(message, explicit=False, reason=AbuseType.HARASS, explanation="as flirtation")

    async def on_raw_reaction_add(self, payload):
        # Do nothing if we are the one adding the reaction
        if payload.user_id == self.user.id:
            return

        # We only care about SOS emojis
        if payload.emoji.name != "🆘":
            return

        # If there is no guild_id, do nothing
        guild_id = payload.guild_id
        if guild_id is None:
            return

        try:
            guild = self.get_guild(int(guild_id))
            channel = guild.get_channel(payload.channel_id)
            message = await channel.fetch_message(payload.message_id)
        except discord.errors.NotFound:
            # Do nothing for messages that no longer exist
            return

        member = guild.get_member(payload.user_id)

        await message.remove_reaction("🆘", member)

        await self.ensure_dm_channel(member)
        self.flows[payload.user_id] = self.flows.get(payload.user_id, [])
        self.flows[payload.user_id].append(SOSFlow(
            client=self,
            message=message,
            user=guild.get_member(payload.user_id)
        ))

    async def notify_user_edit_message(self, message, explicit=False, reason=None, explanation=None):
        await self.ensure_dm_channel(message.author)
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
        # Ignore messages from us 
        if message.author.id == self.user.id:
            return

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

        # Handle a report message
        if content.lower() in START_KEYWORDS:
            # Start a new UserReportCreationFlow
            self.flows[message.author.id] = self.flows.get(message.author.id, [])
            self.flows[message.author.id].append(UserReportCreationFlow(
                client=self,
                reporter=message.author
            ))
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

        addedReaction = False

        # If the message that was sent is from us (the bot), don't filter
        if message.author.id != self.user.id:
            # First filter through images since those are more likely to be seen than text
            if len(message.attachments) > 0:
                # Analyze all the attachments of a message
                scores_list = await self.reviewer.review_images(message)

                # If a message is found to be a 73% match for CSAM, we will tell the user that their message was
                # flagged as sexually suggestive and ask if they really want to send it, siomilar to the normal flow
                # when an image gets flagged. The user won't know that it was flagged as CSAM, only for being sex-
                # ually suggestive, which will deter them from actually sending it, without tipping them off that
                # they are being reviewed. Even if they choose to NOT send the message, a report for CSAM will be
                # generated no matter what.

                # We also scan the rest of their messages for any other offenses. If we do find one, we will only
                # show them that. That way, they will still be presented with the normal warning without tipping
                # them off that something is wrong, and a CSAM report will be sent. If their message does not get
                # flagged by other means though, we will default to showing them the warning for being sexually
                # suggestive.

                # First, we do all the other scanning and send a warning as usual for that.
                # If one of the images matches a hash however, we skip straight to banning the user and removing their image.
                flaggedByOther = False
                if not any(scores["CSAM_HASH"] for scores in scores_list):
                    # Make an object that has the maximum score of each category for the attachments
                    max_scores = {}
                    for scores in scores_list:
                        for key in scores:
                            max_scores[key] = max(max_scores.get(key, 0), scores[key])

                    if max_scores["ADULT"] > 0.85:
                        await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.SEXUAL, explanation="for having a sexually explicit image")
                        flaggedByOther = True
                    elif max_scores["GORE"] > 0.75:
                        await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.VIOLENCE, explanation="as promoting violence for having a bloody/gory image")
                        flaggedByOther = True
                    elif max_scores["RACY"] > 0.8:
                        await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.SEXUAL, explanation="as having a sexually suggestive image")
                        flaggedByOther = True
                else:
                    try:
                        await message.delete()
                    except discord.errors.Forbidden:
                        pass

                # Now we do scanning for CSAM.

                # Get a list of messages that are a 73% match for CSAM, and their corresponding scores.
                csam_messages, csam_scores = tuple(zip(*((message.attachments[i], scores_list[i]) for i in range(len(message.attachments)) if scores_list[i]["CSAM"] > 0.73 or scores_list[i]["CSAM_HASH"]))) or ((), ())

                # Now, we will send a CSAM report to the mod channel no matter what.
                # If their message has already been flagged for something else, we will not show another warning.
                # Otherwise, we will show them a dummy warning for being sexually suggestive.
                if len(csam_messages) > 0:
                    return await self.mark_as_csam(message, csam_messages, csam_scores, show_warning=not flaggedByOther)

                # If a warning was already sent, don't send another for its textual content
                if flaggedByOther:
                    return

            # Now analyze the message's textual content
            if len(message.content.strip()) > 0:
                scores = self.reviewer.review_text(message)

                # Explicit message
                if scores["SEXUALLY_EXPLICIT"] > 0.9:
                    return await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.SEXUAL, explanation="as sexually explicit")
                elif scores["SEVERE_TOXICITY"] > 0.9:
                    return await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.HARASS, explanation="as toxic")
                elif scores["THREAT"] > 0.9:
                    return await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.VIOLENCE, explanation="as threatening")
                elif scores["IDENTITY_ATTACK"] > 0.9:
                    return await self.confirm_user_message(message, explicit=True, abuse_type=AbuseType.HATEFUL, explanation="as hateful")
                # Non-explicit messages
                elif scores["SEXUALLY_EXPLICIT"] > 0.75:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.SEXUAL, explanation="as sexually explicit")
                elif scores["THREAT"] > 0.75:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.VIOLENCE, explanation="for inciting violence")
                elif scores["IDENTITY_ATTACK"] > 0.75:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.HATEFUL, explanation="as hateful")
                elif scores["TOXICITY"] > 0.9 or scores["INSULT"] > 0.9:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.HARASS, explanation="as toxic")
                elif scores["FLIRTATION"] > 0.8:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.HARASS, explanation="as flirtation")
                elif scores["SPAM"] > 0.9:
                    return await self.confirm_user_message(message, explicit=False, abuse_type=AbuseType.SPAM, explanation="as spam")
                # Uncertain textual message get an SOS
                elif scores["SEXUALLY_EXPLICIT"] > 0.65 or scores["SEVERE_TOXICITY"] > 0.65 or scores["THREAT"] > 0.65 or scores["IDENTITY_ATTACK"] > 0.65 or scores["TOXICITY"] > 0.65 or scores["INSULT"] > 0.7 or scores["FLIRTATION"] > 0.65 or scores["SPAM"] > 0.75:
                    await message.add_reaction("🆘")
                    addedReaction = True

        # If a message includes any attachments, an SOS is automatically added no matter what since attachments are so hard to scan for
        if len(message.attachments) > 0 and not addedReaction:
            await message.add_reaction("🆘")

    async def mark_as_csam(self, message, images, scores, show_warning):
        # If show_warning is disabled, then the message has already been flagged for something else and the message has already been deleted
        message_deleted = not show_warning
        for i in range(len(images)):
            # If this image is in our database as a flagged image, report it to NCMEC automatically
            if scores[i]["CSAM_HASH"]:
                self.report_ncmec(message.author, images[i])

                # We should also ban the user here, but we don't have permission to do so :(

                # Delete the message if it hasn't already been deleted
                if not message_deleted:
                    try:
                        await message.delete()
                    except discord.errors.Forbidden:
                        pass
                    message_deleted = True

                # No need to do anything else for this image since we already reported it and deleted the message
                continue

            # A report will be sent to the mod channel for this individual image
            report = await CSAMImageReport(
                client=self,
                message=message,
                image=images[i],
                score=scores[i]["CSAM"]
            )

            await asyncio.gather(*(report.send_to_channel(channel, assignable=True) for channel in self.mod_channels.values()))

        # If show warning is on, we will send the user a dummy warning telling them that their image was marked as sexually suggestive
        # This is the same as normal, except a report will not be generated if they select yes because we already sent a report for each image
        if show_warning and not message_deleted:
            # The message gets deleted as it normally does
            if not isinstance(message.channel, discord.DMChannel):
                await message.delete()

            await self.ensure_dm_channel(message.author)
            flow = CSAMDummyWarningFlow(
                client=self,
                message=message
            )

            self.flows[message.author.id] = self.flows.get(message.author.id, [])
            self.flows[message.author.id].append(flow)

    def report_ncmec(self, user, image):
        # This is supposed to mimic us sending a report to NCMEC, which we of course can't ACTUALLY do
        print(f"A report was sent to NCMEC with {user.name}'s information for the image at {image.proxy_url}.")

    async def confirm_user_message(self, message, always_report=False, explicit=False, abuse_type=None, explanation="", urgency=None):
        # DMs a user asking if they are sure they want to send a message

        # The message first gets auto-deleted by the bot
        # We cannot delete a mesasge from DMs though
        if not isinstance(message.channel, discord.DMChannel):
            await message.delete()

        await self.ensure_dm_channel(message.author)
        flow = SentBadMessageFlow(
            client=self,
            message=message,
            always_report=False,
            explicit=explicit,
            abuse_type=abuse_type,
            explanation=explanation,
            urgency=urgency
        )

        self.flows[message.author.id] = self.flows.get(message.author.id, [])
        self.flows[message.author.id].append(flow)

    async def ensure_dm_channel(self, user):
        return user.dm_channel or await user.create_dm()

    async def on_disconnect(self):
        # Closes the csam.hashlist file when the bot disconnects (which is essentually never because we Ctrl+C to kill it instead of doing it the right way...)
        for file in self.reviewer.hashlists.values():
            file.close()

client = ModBot()
client.run(discord_token)