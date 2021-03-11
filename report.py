import discord
import re
import asyncio
import time
from io import BytesIO
import cv2
import numpy as np
from asyncinit import asyncinit
from reactions import Reaction
import flow
from consts import *


class Report():
    def __init__(self, client, flow_class, urgency=0, message=None, abuse_type=None, reviewer=None):
        self.urgency = urgency
        self.message = message
        self.abuse_type = abuse_type
        self.creation_time = time.localtime() # Time that the report was created
        self.resolution_time = None # Time that the report was resolved
        self.status = ReportStatus.NEW # Status of the report
        self.assignee = None # Who took on the report
        self._channel_messages = set()
        self.client = client
        self.reviewer = reviewer
        self.ReviewFlow = flow_class

    def as_embed(self):
        embed = discord.Embed(
            color=(
                discord.Color.dark_gray().value,
                discord.Color.green().value,
                discord.Color.gold().value,
                discord.Color.orange().value,
                discord.Color.red().value
            )[self.urgency]
        ).add_field(
            name="Urgency",
            value=("Very Low", "Low", "Moderate", "High", "Very High")[self.urgency],
            inline=False
        ).add_field(
            name="Abuse Type",
            value=self.abuse_type.value if isinstance(self.abuse_type, AbuseType) else "*[Unspecified]*",
            inline=False
        )
        if self.status == ReportStatus.NEW:
            embed.set_footer(text=f"This report was opened on {time.strftime('%b %d, %Y at %I:%M %p %Z', self.creation_time)}.")
        elif self.status == ReportStatus.PENDING:
            embed.set_footer(text=f"This report is being addressed by {self.assignee.display_name}.")
        elif self.status == ReportStatus.RESOLVED:
            embed.set_footer(text=f"This report was closed on {time.strftime('%b %d, %Y at %I:%M %p %Z', self.resolution_time)} by {self.assignee.display_name}.")
        return embed

    # Sets the status of this report and updates any embeds that show the status
    def set_status(self, status):
        self.status = status
        self.update_embeds()

    def update_embeds(self):
        embed = self.as_embed()
        asyncio.gather(*(msg[0].edit(embed=embed) for msg in self._channel_messages))

    # Send an Embed to the specified channel that shows the report
    # reactions is a list of Reactions to show under the report
    # self_destructible indicates whether the message should delete itself once the report is resolves
    async def send_to_channel(self, channel, assignable=False, self_destructible=True):
        # Get the embed for this report
        embed = self.as_embed()

        # Send a message to all the specified channels
        message = await channel.send(embed=embed)
        # Keep track of this message
        self._channel_messages.add((message, assignable, self_destructible))
        # Display the assignable Reaction on the new Message
        if assignable:
            await Reaction("✋", click_handler=self.reaction_attempt_assign, once_per_message=False).register_message(message)

        return message

    # Tries to assign a report to a user by checking if they are already assigned to another report
    # Used as a callback for Reaction click_handlers
    async def reaction_attempt_assign(self, reaction, discordClient, discordReaction, user):
        if any(map(lambda _flow: isinstance(_flow, flow.ReportReviewFlow), discordClient.flows.get(user.id, []))):
            try:
                await asyncio.gather(
                    # Remove the user's reaction
                    discordReaction.message.remove_reaction(discordReaction, user),
                    # Tell the user who tried to assign themselves that they are already assigned to another report
                    (user.dm_channel or await user.create_dm()).send(content="You already have a report assigned to you. Finish this one, or use the `unassign` command to unassign yourself.")
                )
            except discord.errors.Forbidden:
                # A Forbidden error can arise in DMs
                pass
        else:
            try:
                await asyncio.gather(
                    # Remove the bot's reaction from the message
                    reaction.unregister_message(discordClient, discordReaction.message),
                    # Remove the user's reaction from the message
                    discordReaction.message.remove_reaction(discordReaction, user),
                    # Assign the report to the user
                    self.assign_to(user)
                )
            except discord.errors.Forbidden:
                # A Forbidden error can arise in DMs
                pass

    # Assigns this report to a specified discord.Member
    async def assign_to(self, moderator):
        self.assignee = moderator
        self.set_status(ReportStatus.PENDING)
        if moderator.dm_channel is None:
            await moderator.create_dm()
        self.review_flow = self.ReviewFlow(report=self, reviewer=moderator, client=self.client)
        self.client.flows[moderator.id] = self.client.flows.get(moderator.id, [])
        self.client.flows[moderator.id].append(self.review_flow)

    # Remove an assignee 
    def unassign(self):
        if self.assignee is None:
            return
        self.set_status(ReportStatus.NEW)
        self.client.flows[self.assignee.id].remove(self.review_flow)
        self.assignee = None
        self.review_flow = None
        assignReaction = Reaction("✋", click_handler=self.reaction_attempt_assign, once_per_message=False)
        for msg in self._channel_messages:
            if msg[1]: # Check if message is "assignable"
                asyncio.create_task(assignReaction.register_message(msg[0]))

    def resolve(self):
        self.resolution_time = time.localtime()
        self.set_status(ReportStatus.RESOLVED)
        self.client.flows[self.assignee.id].remove(self.review_flow)
        self.review_flow = None
        for msg in list(self._channel_messages):
            if msg[2]: # Check if message is self_destructible
                asyncio.create_task(msg[0].delete())
                self._channel_messages.remove(msg)


@asyncinit
class CSAMImageReport(Report):
    async def __init__(self, image, score, *args, **kwargs):
        super().__init__(*args, flow_class=flow.CSAMImageReviewFlow, urgency=4 if score > 0.9 else 3, abuse_type=AbuseType.CSAM, **kwargs)
        self.score = score
        self.image = image
        # Download the image and save it as a numpy array
        self.img_stream = BytesIO()
        await image.save(self.img_stream, use_cached=True)
        self.img_array = cv2.imdecode(np.asarray(bytearray(self.img_stream.read()), dtype=np.uint8), cv2.IMREAD_COLOR)
        self.img_name = image.filename

    # Extra content for the returned Embed
    def as_embed(self, *args, **kwargs):
        return super().as_embed(*args, **kwargs).add_field(
            name="Score",
            value=f"{self.score * 100:0.2f}"
        ).set_author(
            name="CSAM Image Report"
        )


class UserReport(Report):
    def __init__(self, *args, report_creation_flow=None, notify_on_resolve=True, **kwargs):
        self.report_creation_flow = report_creation_flow
        abuse_type = report_creation_flow.abuse_type
        if abuse_type == AbuseType.SPAM:
            urgency = 0
        elif abuse_type == AbuseType.HATEFUL or abuse_type == AbuseType.SEXUAL:
            urgency = 1
        elif abuse_type == AbuseType.HARASS:
            urgency = 3 if report_creation_flow.victim == report_creation_flow.reporter else 2
        elif abuse_type == AbuseType.BULLYING:
            urgency = 3
        elif abuse_type == AbuseType.VIOLENCE or abuse_type == AbuseType.HARMFUL:
            urgency = 4 if report_creation_flow.urgent else 3
        elif abuse_type == AbuseType.CSAM:
            urgency = 4
        super().__init__(*args, flow_class=flow.UserReportReviewFlow, urgency=urgency, client=report_creation_flow.client, message=report_creation_flow.message, abuse_type=abuse_type, **kwargs)
        self.comments = report_creation_flow.comments
        self.author = report_creation_flow.reporter
        self.urgent = False
        self.message_deleted = False
        self.replacement_message = report_creation_flow.replacement_message
        self.victim = None
        self.notify_on_resolve = notify_on_resolve
        if abuse_type == AbuseType.HARASS or abuse_type == AbuseType.BULLYING:
            self.victim = report_creation_flow.victim
        elif abuse_type == AbuseType.VIOLENCE or abuse_type == AbuseType.HARMFUL or abuse_type == AbuseType.CSAM:
            self.urgent = report_creation_flow.urgent

    def as_embed(self, *args, **kwargs):
        embed = super().as_embed(*args, **kwargs).add_field(
            name="Reported Message" + (" (Deleted)" if self.message_deleted else ""),
            value=f"[Jump to message]({self.replacement_message.jump_url if self.replacement_message else self.message.jump_url})\n" + flow.message_preview_text(self.message),
            inline=False
        )
        if hasattr(self, "victim") and self.abuse_type in (AbuseType.HARASS, AbuseType.BULLYING):
            embed.add_field(name="Victim", value=(self.victim.mention + (" (Reporter)" if self.victim == self.author else "")) if self.victim else "*[Not specified]*", inline=False)
        embed.add_field(
            name="Requires Immediate Attention",
            value="Yes" if self.urgent else "No",
            inline=False
        ).add_field(
            name="Additional Comments",
            value=self.comments if self.comments else "*None*",
            inline=False
        ).set_author(
            name="User Report"
        ).insert_field_at(
            0,
            name="Reporter",
            value=self.author.mention,
            inline=False
        )
        return embed

    # Resolves a Report as normal, but also DMs the Report's author that their report has been resolved
    def resolve(self, *args, **kwargs):
        super().resolve(*args, **kwargs)
        if self.notify_on_resolve:
            asyncio.create_task(self.author.dm_channel.send(content="Your report has been resolved by our content moderation team:", embed=self.report_creation_flow.as_embed()))

    # Deletes a comment
    async def delete_message(self):
        # Don't try to delete an already-deleted message
        if self.message_deleted:
            return True

        # Return the message that replaces the actual message
        if self.replacement_message is not None:
            # Try to delete both message
            try:
                await self.replacement_message.delete()
                await self.client.message_pairs[self.replacement_message.id].delete()
            except:
                return False
        else:
            # Try to delete the user's message
            try:
                await self.message.delete()
            except:
                return False

        # DM the user that their message has been deleted
        if self.message.author.id != self.client.user.id:
            dm_channel = self.message.author.dm_channel or self.message.author.create_dm()
            await dm_channel.send(
                content="Your message was deleted by our content moderation team:",
                embed=discord.Embed(
                    description=self.message.content,
                    color=discord.Color.dark_red()
                ).set_author(
                    name=self.message.author.display_name,
                    icon_url=self.message.author.avatar_url
                )
            )

        self.message_deleted = True
        self.update_embeds()

        return True

    # Kick the user from the guild (can still join back)
    async def kick_user(self):
        # We can't kick someone from a DM channel
        if isinstance(self.message.channel, discord.DMChannel):
            return False

        member = self.message.guild.get_member(self.message.author.id)
        # The member is not in the guild (i.e. already kicked off)
        if member is None:
            return True

        try:
            await self.message.guild.kick(member)
        except:
            return False

        return True

    # Ban a user from a guild
    async def ban_user(self):
        # We can't ban someone from a DM channel
        if isinstance(self.message.channel, discord.DMChannel):
            return False

        try:
            bans = await self.message.guild.bans()
        except discord.errors.Forbidden:
            # We don't have permission to ban people
            return False

        if discord.utils.find(lambda user: user.id == self.message.author.id, bans):
            # User is already banned
            return True

        try:
            await self.message.guild.ban(self.message.author)
        except:
            return False

        return True

    # Sends a warning to the offender that repeat offenses will get them kicked off or banned from the server
    async def warn_user(self, msg=None):
        dm_channel = self.message.author.dm_channel or self.message.author.create_dm()
        embed = discord.Embed(
            description=self.message.content,
            color=discord.Color.blurple()
        ).set_author(
            name=self.message.author.display_name,
            icon_url=self.message.author.avatar_url
        )

        if self.abuse_type == AbuseType.CSAM:
            # A warning for CSAM is more specific:
            await dm_channel.send(
                content="Your message has been reported as an unintentional instance of child sexualization:",
                embed=embed
            )
            await dm_channel.send("While this post on its own should not warrant any serious consequences, repeated posts like it *will* potentially lead to kicking, banning, or involving law enforcement" + (":\n" + msg if msg is not None else "."))
        else:
            await dm_channel.send(
                content="Your message has been reported:",
                embed=embed
            )
            await dm_channel.send("One of our content moderators felt the need to warn you that repeat offenses may get you kicked from the server in the future, or potentially banned" + (":\n" + msg if msg is not None else "."))
        return True

    # DMs the message author a tip for helping for suicides
    async def show_user_suicide_help(self):
        dm_channel = self.message.author.dm_channel or self.message.author.create_dm()
        await dm_channel.send(embed=discord.Embed(
            title="We're reaching out to offer help.",
            description="One of your friends believes you may benefit from us reaching out to offer help. You can [find a local counselor](https://findtreatment.samhsa.gov/) or contact the National Suicide Prevention Lifeline at (800) 273-8255 or by visiting [suicidepreventionlifeline.org](https://suicidepreventionlifeline.org). We also recommend connecting with friends or loved ones for support.",
            color=discord.Color.blurple()
        ))
        return True

    async def show_user_bullying_help(self):
        dm_channel = self.message.author.dm_channel or self.message.author.create_dm()
        await dm_channel.send(embed=discord.Embed(
            title="We're reaching out to offer help.",
            description="One of our content moderators believes you may be the victim of online bullying and wanted to reach out to offer help. You can [find a local counselor](https://findtreatment.samhsa.gov/) or contact the National Suicide Prevention Lifeline at (800) 273-8255 or by visiting [suicidepreventionlifeline.org](https://suicidepreventionlifeline.org). We also recommend connecting with friends or loved ones for support.",
            color=discord.Color.blurple()
        ))
        return True

    # "Reports" something to local authorities (actually does nothing)
    async def contact_local_authorities(self):
        return "The reported has been escalated to local authorities for immediate action."

    # "Reports" something to NCMEC (actually does nothing)
    async def contact_ncmec(self):
        return "The report has been escalated to NCMEC for investigation."


# A class representing an Automated Report from the bot automatically flagging messages
class AutomatedReport(Report):
    def __init__(self, *args, message_hidden=False, message_deleted=False, replacement_message=None, prefix_message=None, **kwargs):
        super().__init__(flow_class=flow.AutomatedReportReviewFlow, *args, **kwargs)
        self.message_hidden = message_hidden
        self.message_deleted = message_deleted
        self.replacement_message = replacement_message
        self.prefix_message = prefix_message

    # Extra content for the returned Embed
    def as_embed(self, *args, **kwargs):
        return super().as_embed(*args, **kwargs).add_field(
            name="Original Message",
            value=(f"[Jump to message]({self.replacement_message.jump_url if not self.message_deleted else self.message.jump_url})\n") + flow.message_preview_text(self.message),
            inline=False
        ).add_field(
            name="Message Visibility",
            value="Deleted" if self.message_deleted else "Hidden" if self.message_hidden else "Visible"
        ).set_author(
            name="Automated Report"
        )

    # Deletes a comment's replacement
    async def delete_message(self):
        # Don't try to delete an already-deleted message
        if self.message_deleted:
            return True

        # Delete both the message and the message before it specifying who it's from
        try:
            await asyncio.gather(
                self.prefix_message.delete(),
                self.replacement_message.delete()
            )
        except:
            return False

        # DM the user that their message has been deleted
        dm_channel = self.message.author.dm_channel or self.message.author.create_dm()
        await dm_channel.send(
            content="Your message was deleted by our content moderation team:",
            embed=discord.Embed(
            description=self.message.content,
                color=discord.Color.dark_red()
            ).set_author(
                name=self.message.author.display_name,
                icon_url=self.message.author.avatar_url
            )
        )

        self.message_deleted = True
        self.update_embeds()

        return True

    # Hides a message's content behind spoilers if it is not already hidden
    async def hide_message(self):
        # An already hidden message should do nothing
        if self.message_hidden:
            return True

        # This code is taken from ModBot.allow_user_message
        content = self.message.content
        if self.client.smart_spoilers:
            reMatch = re.search(r"```(?:\S*\n)?([\s\S]*?)\n?```", content)
            while reMatch:
                code = reMatch.group(1).split("\n")
                longestLine = max(map(lambda line: len(line), code))
                code = "\n".join(f"`{{:{longestLine}}}`".format(line) for line in code)
                content = content[:reMatch.start()] + code + content[reMatch.end():]
                reMatch = re.search(r"```(?:\S*\n)?([\s\S]*?)\n?```", content)

            reMatch = re.search(r"(`(?:[^`]|\|(?!\|))*?\|)(\|(?:[^`]|\|(?!\|))*?`)", content)
            while reMatch:
                content = content[:reMatch.start()] + reMatch.group(1) + "\u200b" + reMatch.group(2) + content[reMatch.end():]
                reMatch = re.search(r"(`(?:[^`]|\|(?!\|))*?\|)(\|(?:[^`]|\|(?!\|))*?`)", content)

            content = content.replace("||", "\\|\\|")
        try:
            await asyncio.gather(
                self.prefix_message.edit(content=f"*The following message may contain inappropriate content. Click the black bar to reveal it.*\n*{self.message.author.mention} says:*"),
                self.replacement_message.edit(content="||" + content + "||" if content else "")
            )
        except:
            return False

        self.message_hidden = True
        self.update_embeds()

        return True

    # Shows the original message's content if the replacement message is hidden
    async def reveal_message(self):
        # An already revealed message should do nothing
        if not self.message_hidden:
            return True

        try:
            await asyncio.gather(
                self.prefix_message.edit(content=f"*{self.message.author.mention} says:*"),
                self.replacement_message.edit(content=self.message.content)
            )
        except:
            return False

        self.message_hidden = False
        self.update_embeds()

        return True

    # Kick the user from the guild (can still join back)
    async def kick_user(self):
        # We can't kick someone from a DM channel
        if isinstance(self.message.channel, discord.DMChannel):
            return False

        member = self.message.guild.get_member(self.message.author.id)
        # The member is not in the guild (i.e. already kicked off)
        if member is None:
            return True

        try:
            await self.message.guild.kick(member)
        except:
            return False

        return True

    # Ban a user from a guild
    async def ban_user(self):
        # We can't ban someone from a DM channel
        if isinstance(self.message.channel, discord.DMChannel):
            return False

        try:
            bans = await self.message.guild.bans()
        except discord.errors.Forbidden:
            # We don't have permission to ban people
            return False

        if discord.utils.find(lambda user: user.id == self.message.author.id, bans):
            # User is already banned
            return True

        try:
            await self.message.guild.ban(self.message.author)
        except:
            return False

        return True
