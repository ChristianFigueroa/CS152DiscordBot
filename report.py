from enum import Enum, auto
import discord
import re
import asyncio
import time
from reactions import Reaction
from textwrap import dedent as _dedent
from contextlib import suppress

HELP_KEYWORDS = ("help", "?")
CANCEL_KEYWORDS = ("cancel", "quit", "exit")
START_KEYWORDS = ("report")

YES_KEYWORDS = ("yes", "y", "yeah", "yup", "sure")
NO_KEYWORDS = ("no", "n", "nah", "naw", "nope")

# Dedents a string and leaves non-strings alone
def dedent(obj):
    return _dedent(obj) if isinstance(obj, str) else obj

# The different states the Report can be in.
# Each State's name identifies the method that gets called when a message comes in.
# E.g., if the Report is in the AWAITING_MESSAGE_LINK state, the Report.awaiting_message_link method will be called each time a message is sent.
class OpenUserReportState(Enum):
    REPORT_START          = auto()
    AWAITING_MESSAGE_LINK = auto()
    AWAITING_ABUSE_TYPE   = auto()
    SPAM_ENTRY            = auto()
    HATEFUL_ENTRY         = auto()
    SEXUAL_ENTRY          = auto()
    HARASS_ENTRY          = auto()
    HARASS_ADD_COMMENT    = auto()
    BULLYING_ENTRY        = auto()
    BULLYING_ADD_USER     = auto()
    BULLYING_ADD_COMMENT  = auto()
    HARMFUL_ENTRY         = auto()
    VIOLENCE_ENTRY        = auto()
    CSAM_ENTRY            = auto()
    ADDITIONAL_COMMENT    = auto()
    FINALIZE_REPORT       = auto()
    REPORT_COMPLETE       = auto()

class ReportStatus(Enum):
    NEW      = auto()
    PENDING  = auto()
    RESOLVED = auto()

class AbuseType(Enum):
    SPAM      = "Misinformation or Spam"
    HATEFUL   = "Hateful Content"
    SEXUAL    = "Sexual Content"
    HARASS    = "Harassment"
    BULLYING  = "Bullying"
    HARMFUL   = "Harmful/Dangerous Content"
    VIOLENCE  = "Promoting Violence or Terrorism"
    CSAM      = "Child Abuse"


class Report():
    def __init__(self, urgency=0, message=None, abuse_type=None, client=None, reviewer=None):
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
        if discordClient.pending_reports.get(user.id, None):
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
        self.review_flow = (AutomatedReportReviewFlow if isinstance(self, AutomatedReport) else UserReportReviewFlow)(self, moderator)
        self.client.pending_reports[moderator.id] = self.review_flow

    # Remove an assignee 
    def unassign(self):
        if self.assignee is None:
            return
        self.set_status(ReportStatus.NEW)
        self.client.pending_reports[self.assignee.id] = None
        self.assignee = None
        self.review_flow = None
        assignReaction = Reaction("✋", click_handler=self.reaction_attempt_assign, once_per_message=False)
        for msg in self._channel_messages:
            if msg[1]: # Check if message is "assignable"
                asyncio.create_task(assignReaction.register_message(msg[0]))

    def resolve(self):
        self.resolution_time = time.localtime()
        self.set_status(ReportStatus.RESOLVED)
        self.client.pending_reports[self.assignee.id] = None
        self.review_flow = None
        for msg in list(self._channel_messages):
            if msg[2]: # Check if message is self_destructible
                asyncio.create_task(msg[0].delete())
                self._channel_messages.remove(msg)


class UserReport(Report):
    def __init__(self, *args, report_creation_flow=None, **kwargs):
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
        super().__init__(*args, urgency=urgency, client=report_creation_flow.client, message=report_creation_flow.message, abuse_type=report_creation_flow.abuse_type, **kwargs)
        self.comments = report_creation_flow.comments
        self.author = report_creation_flow.reporter
        self.urgent = False
        self.message_deleted = False
        self.replacement_message = report_creation_flow.replacement_message
        self.victim = None
        if abuse_type == AbuseType.HARASS or abuse_type == AbuseType.BULLYING:
            self.victim = report_creation_flow.victim
        elif abuse_type == AbuseType.VIOLENCE or abuse_type == AbuseType.HARMFUL or abuse_type == AbuseType.CSAM:
            self.urgent = report_creation_flow.urgent

    def as_embed(self, *args, **kwargs):
        embed = super().as_embed(*args, **kwargs).add_field(
            name="Reported Message" + (" (Deleted)" if self.message_deleted else ""),
            value=f"[Jump to message]({self.message.jump_url})\n"+self.message.content,
            inline=False
        )
        if hasattr(self, "victim"):
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
        super().__init__(*args, **kwargs)
        self.message_hidden = message_hidden
        self.message_deleted = message_deleted
        self.replacement_message = replacement_message
        self.prefix_message = prefix_message

    # Extra content for the returned Embed
    def as_embed(self, *args, **kwargs):
        return super().as_embed(*args, **kwargs).add_field(
            name="Original Message",
            value=f"[Jump to message]({self.replacement_message.jump_url})\n"+self.message.content,
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
                self.replacement_message.edit(content="||" + content + "||")
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


# Helps with back and forth communication between the bot and a user
class Flow():
    def __init__(self, channel, start_state, quit_state=None):
        self.channel = channel # The DM channel to send the messages in
        asyncio.create_task(self.transition_to_state(start_state)) # Perform a transition to the initial start state
        self._quit_state = quit_state
        self._prequit_state = None

    # Accepts forwarded messages and sends a reply
    async def forward_message(self, message, simulated=False):
        message = message.content.strip() if isinstance(message, discord.Message) else message
        async def revert():
            await self.transition_to_state(self._prequit_state)
            self._prequit_state = None
        if message.lower() in CANCEL_KEYWORDS and self._quit_state:
            self._prequit_state = self.state
            self.state = self._quit_state
            cb = getattr(self, self._quit_state.name.lower())
            try:
                await self.say((await cb("", introducing=True, simulated=simulated, revert=revert) if asyncio.iscoroutinefunction(cb) else cb("", introducing=True, simulated=simulated, revert=revert)) or ())
            except TypeError:
                pass
        else:
            await self.say(await self.resolve_message(message, simulated=simulated))

    # Turns the message into dialogue that the bot can reply with
    async def resolve_message(self, message, simulated=False):
        async def revert():
            await self.transition_to_state(self._prequit_state)
            self._prequit_state = None
        message = message.content.strip() if isinstance(message, discord.Message) else message
        cb = getattr(self, self.state.name.lower())
        if self._quit_state and self._prequit_state and self.state is self._quit_state:
            return (await cb(message, simulated=simulated, revert=revert) if asyncio.iscoroutinefunction(cb) else cb(message, simulated=simulated, revert=revert)) or ()
        else:
            return (await cb(message, simulated=simulated) if asyncio.iscoroutinefunction(cb) else cb(message, simulated=simulated)) or ()

    # Sends a message to the channel from the bot to act as a reply
    async def say(self, msgs):
        msgs = (dedent(msgs),) if isinstance(msgs, str) or isinstance(msgs, discord.Embed) else tuple(dedent(msg) for msg in msgs) or ()

        lastMessage = None
        for msg in msgs:
            if isinstance(msg, Reaction):
                asyncio.create_task(msg.register_message(lastMessage))
            elif isinstance(msg, discord.Embed):
                lastMessage = await self.channel.send(embed=msg)
            else:
                lastMessage = await self.channel.send(content=msg)

    # Creates an Embed to inform the user of something
    async def inform(self, msg, return_embed=False):
        embed = discord.Embed(
            color=discord.Color.greyple(),
            description=msg
        )
        return embed if return_embed else await self.say(embed)

    # Creates an Embed to warn the user of something
    async def warn(self, msg, return_embed=False):
        embed = discord.Embed(
            color=discord.Color.gold(),
            description=msg
        )
        return embed if return_embed else await self.say(embed)

    # Transition to another state and run the function with the introducing parameter
    # The function with the name of the state (in all lowercase) is called.
    async def transition_to_state(self, state):
        self.state = state
        cb = getattr(self, self.state.name.lower())
        try:
            return await self.say((await cb("", introducing=True, simulated=False) if asyncio.iscoroutinefunction(cb) else cb("", introducing=True, simulated=False)) or ())
        except TypeError:
            pass

    # Simulates a reply by sending a message to forward_message with simulated=True
    async def simulate_reply(self, message):
        return await self.forward_message(message, simulated=True)

    # Returns a function that, when called, simulates a reply (via self.simulate_reply)
    # This compares states so that a simulated reply is only performed when the state hasn't changed
    def simulate_reply_handler(self, message):
        frozenState = self.state
        async def handler(*args, **kwargs):
            if self.state == frozenState:
                return await self.simulate_reply(message)
        return handler

    # Returns a Reaction that will simulate a `yes` reply
    def react_yes(self):
        return Reaction("✅", toggle_handler=self.simulate_reply_handler("yes"))

    # Same for simulating `no`
    def react_no(self):
        return Reaction("🚫", toggle_handler=self.simulate_reply_handler("no"))

    # Same for simulating `done`
    def react_done(self):
        return Reaction("✅", toggle_handler=self.simulate_reply_handler("done"))

    # Returns a reaction that will simulate a specific number between 1 and 10
    def react_index(self, index):
        return Reaction(("0️⃣","1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟")[index], click_handler=self.simulate_reply_handler(str(index)))

    # A method decorator for adding help messages to each state
    @classmethod
    def help_message(cls, *msgs):
        if len(msgs) == 1 and not isinstance(msgs[0], str):
            try:
                msgs = tuple(iter(msgs[0]))
            except:
                pass
        def wrapper(func):
            async def asyncinnerwrapper(self, message, *args, **kwargs):
                return msgs if message.lower() in HELP_KEYWORDS else await func(self, message, *args, **kwargs)
            def innerwrapper(self, message, *args, **kwargs):
                return msgs if message.lower() in HELP_KEYWORDS else func(self, message, *args, **kwargs)
            return asyncinnerwrapper if asyncio.iscoroutinefunction(func) else innerwrapper
        return wrapper


class EditedBadMessageFlow(Flow):
    State = Enum("EditedBadMessageFlowState", (
        "START",
        "RESEND",
        "UNACCEPTABLE_EDIT",
        "ACCEPTABLE_EDIT",
        "TIME_EXPIRED"
    ))
    def __init__(self, client, message, explicit=False, reason=None, expiration_time=10 * 60):
        super().__init__(channel=message.author.dm_channel, start_state=EditedBadMessageFlow.State.START)
        self.client = client
        self.author = message.author
        self.message = message
        self.explicit = explicit
        self.reason = reason
        self.second_timer = asyncio.ensure_future(self._second_timer())
        self.time_elapsed = 0
        self.expiration_time = expiration_time
        self.second_timer_cancelled = False
        self.timer_message = None

    def timer_embed(self):
        seconds_left = self.expiration_time - self.time_elapsed
        color = discord.Color.green() if seconds_left > self.expiration_time * 0.5 else \
            discord.Color.gold () if seconds_left > self.expiration_time * 0.2 else \
            discord.Color.orange() if seconds_left > self.expiration_time * 0.075 else \
            discord.Color.red()
        minutes_left, seconds_left = divmod(seconds_left, 60)
        return discord.Embed(
            description = "{:02}:{:02}".format(minutes_left, seconds_left),
            color=color
        )

    async def _second_timer(self):
        while not self.second_timer_cancelled:
            await asyncio.sleep(1)
            self.time_elapsed += 1
            if self.timer_message:
                try:
                    await self.timer_message.edit(embed=self.timer_embed())
                except discord.errorsNotFound:
                    self.timer_message = None
            if self.time_elapsed >= self.expiration_time:
                await self.transition_to_state(EditedBadMessageFlow.State.TIME_EXPIRED)

    async def time_expired(self, message, simulated=False, introducing=False):
        self.second_timer.cancel()
        self.second_timer_cancelled = True
        with suppress(asyncio.CancelledError):
            await self.second_timer
        await self.message.delete()
        await self.close()
        await self.say("Your edited message was deleted due to inaction.")

    @Flow.help_message("Either say `re-send` to have the bot re-send your newly edited message, or make another edit to your message to something less inappropriate. If no action is taken within ten minutes, the message will be deleted.")
    async def start(self, message, simulated=False, introducing=False):
        if introducing:
            if self.reason:
                textReason = {
                    AbuseType.SPAM: " as spam",
                    AbuseType.VIOLENCE: " for inciting violence",
                    AbuseType.HATEFUL: " as hateful",
                    AbuseType.HARASS: " as toxic",
                }[self.reason]
            else:
                textReason = ""
            await self.say((
                f"Your edited message below has been flagged{textReason}.",
                discord.Embed(
                    description=f"[Jump to message]({self.message.jump_url})\n{self.message.content}",
                    color=discord.Color.blurple()
                ).set_author(name=self.author.display_name, icon_url=self.author.avatar_url),
                """
                    You have the option to either edit it back into something less inappropriate, or have the bot re-send your message with the new edit.
                    Note however that the message will appear back at the bottom of the channel instead of where it is now.
                """,
                """
                    You can re-edit it now if you wish to do that, or say `re-send` to have the bot re-send the message with this new edit. You can also push the button below to re-send it.
                    If no action is taken within ten minutes, the bot will delete the message from the channel altogether.
                """,
                Reaction("🗨", click_handler=lambda *args: asyncio.create_task(self.transition_to_state(EditedBadMessageFlow.State.RESEND)))
            ))
            self.timer_message = await self.channel.send(embed=self.timer_embed())
        else:
            if message.lower() in ("resend", "re-send", "send"):
                await self.transition_to_state(EditedBadMessageFlow.State.RESEND)
            else:
                return "Sorry, I didn't understand that. Say `re-send` to have the bot re-send your message, or make another to your edited message to something less inappropriate."

    async def resend(self, message, simulated=False, introducing=False):
        await self.message.delete()
        await self.client.allow_user_message(self.message, self.explicit, self.reason)
        await self.close()

    async def edited(self, new_message):
        self.message = new_message
        scores = self.client.eval_text(self.message)

        still_bad = False
        if scores["SPAM"] > 0.8:
            still_bad = True
        elif scores["THREAT"] > 0.75:
            still_bad = True
        elif scores["IDENTITY_ATTACK"] > 0.75:
            still_bad = True
        elif scores["SEVERE_TOXICITY"] > 0.9:
            still_bad = True
        elif scores["TOXICITY"] > 0.9 or scores["INSULT"] > 0.9:
            still_bad = True

        if still_bad:
            await self.transition_to_state(EditedBadMessageFlow.State.UNACCEPTABLE_EDIT)
        else:
            await self.transition_to_state(EditedBadMessageFlow.State.ACCEPTABLE_EDIT)

    async def unacceptable_edit(self, message, simulated=False, introducing=False):
        self.state = EditedBadMessageFlow.State.START
        return "The edit you just made to your message has still been flagged. Please make an edit to something less inappropriate, or say `re-send` to have the bot re-send your message."

    async def acceptable_edit(self, message, simulated=False, introducing=False):
        await self.close()
        return "Your message has been edited to something less inappropriate. Thank you for taking the time to reconsider your message."

    async def close(self):
        await self.timer_message.delete()
        self.timer_message = None
        self.client.flows[self.author.id].remove(self)
        del self.client.messages_pending_edit[self.message.id]


# A Flow for creating and submitting new UserReports
class UserReportCreationFlow(Flow):
    State = Enum("UserReportCreationFlowState", (
        "REPORT_START",
        "AWAITING_MESSAGE_LINK",
        "AWAITING_ABUSE_TYPE",
        "ADD_COMMENT",
        "CHECK_IF_VICTIM",
        "ASK_FOR_VICTIM",
        "SUICIDE_CHECK",
        "CURRENT_EVENTS_CHECK",
        "FINALIZE_REPORT",
        "FINISH_REPORT",
        "REPORT_QUIT"
    ))

    def __init__(self, client, reporter):
        super().__init__(channel=reporter.dm_channel, start_state=UserReportCreationFlow.State.REPORT_START, quit_state=UserReportCreationFlow.State.REPORT_QUIT)
        self.client = client
        self.reporter = reporter
        self.abuse_type = None
        self.sent_report = None

    # Show an introduction and then go to AWAITING_MESSAGE_LINK state
    async def report_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("""
                Thank you for starting the reporting process.
                You can say `help` or `?` at any step for more information.
                Say `cancel` or `quit` at any time to cancel your report.
            """)
        await self.transition_to_state(UserReportCreationFlow.State.AWAITING_MESSAGE_LINK)

    @Flow.help_message("""
        Select a message to report and paste the link here.
        You can obtain a message's link by right-clicking the message and clicking `Copy Message Link`.
    """)
    async def awaiting_message_link(self, message, simulated=False, introducing=False):
        if introducing:
            return """
                Please copy and paste the link to the message you want to report.
                You can obtain this link by right-clicking the message and clicking Copy Message Link.
            """
        else:
            # Parse out the three ID strings from the message link
            m = re.search(r"/(\d+|@me)/(\d+)/(\d+)", message)

            if not m:
                return """
                    I'm sorry, I couldn't read that link.
                    Please try again or say `cancel` to cancel.
                """

            guild = m.group(1)
            if guild == "@me":
                return """
                    It looks like you specified a message in your DMs (notice the `@me` in the link). I can only access messages in guilds I am a part of.
                    Please try again or say `cancel` to cancel.
                """
            else:
                guild = self.client.get_guild(int(guild))
                if not guild:
                    return """
                        I cannot accept reports of messages from guilds that I'm not in.
                        Please have the guild owner add me to the guild and try again, or say `cancel` to cancel.
                    """

            channel = guild.get_channel(int(m.group(2)))
            if not channel:
                return """
                    It seems this channel was deleted or never existed.
                    Please try again or say `cancel` to cancel.
                """

            try:
                message = await channel.fetch_message(int(m.group(3)))
            except discord.errors.NotFound:
                return """
                    It seems this message was deleted or never existed.
                    Please try again or say `cancel` to cancel.
                """

            # Check if the user is reporting one of our messages that was created from auto-flagging
            # If so, reference the original message
            if message.id in self.client.message_aliases:
                self.replacement_message = message
                message = self.client.message_aliases[message.id]
            else:
                self.replacement_message = None

            # Save the message
            self.message = message

            await self.say((
                "I found this message:",
                discord.Embed(
                    description=message.content,
                    color=discord.Color.greyple()
                ).set_author(name=message.author.display_name, icon_url=message.author.avatar_url)
            ))
            await self.transition_to_state(UserReportCreationFlow.State.AWAITING_ABUSE_TYPE)

    @Flow.help_message("Enter a keyword from one of the abuse types above, or select one of the buttons to choose it.")
    async def awaiting_abuse_type(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                """
                    Please tell us what you think is inappropriate about this message:
                     1. Misinformation or Spam
                     2. Hateful Content
                     3. Sexual Content
                     4. Harassment
                     5. Bullying
                     6. Harmful/Dangerous Content
                     7. Promoting Violence or Terrorism
                     8. Child Abuse
                    You can enter a keyword to choose one, or select a button below.
                """,
                *(self.react_index(index + 1) for index in range(8))
            )
        else:
            emergencyWarning = discord.Embed(
                title="Call 911 in an emergency.",
                description="We will review your report as soon as we can, but calling 911 or other local authorities is the fastest and most effective way to handle emergencies.",
                color=discord.Color.red()
            )
            keywords = message.lower().split()
            if message == "1" or any(keyword in ("misinformation", "disinformation", "spam", "misinfo", "disinfo", "information", "info") for keyword in keywords):
                self.abuse_type = AbuseType.SPAM
                await self.say("You selected __1. Misinformation or Spam__.")
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message == "2" or any(keyword in ("hateful", "hate", "hatred", "racism", "racist", "sexist", "sexism") for keyword in keywords):
                self.abuse_type = AbuseType.HATEFUL
                await self.say((
                    "You selected: __2. Hateful Content__.",
                    await self.warn("Please note that content that incites violence should be reported as Promoting Violence or Terrorism.", return_embed=True)
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message == "3" or any(keyword in ("sexual", "sex", "nude", "nudity", "naked") for keyword in keywords):
                self.abuse_type = AbuseType.SEXUAL
                await self.say((
                    "You selected: __3. Sexual Content__.",
                    await self.warn("Please note that any sexual content involving minors should be reported as Child Abuse.", return_embed=True)
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message == "4" or any(keyword in ("harassment", "harass", "harassing") for keyword in keywords):
                self.abuse_type = AbuseType.HARASS
                await self.say("You selected: __4. Harassment__.")
                return await self.transition_to_state(UserReportCreationFlow.State.CHECK_IF_VICTIM)
            elif message == "5" or any(keyword in ("bullying", "bully", "bullies", "cyberbullying", "cyberbully", "cyberbullies") for keyword in keywords):
                self.abuse_type = AbuseType.BULLYING
                await self.say((
                    "You selected __5. Bullying__.",
                    emergencyWarning
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.CHECK_IF_VICTIM)
            elif message == "6" or any(keyword in ("harmful", "dangerous", "harm", "danger", "self-harm", "suicide", "suicidal") for keyword in keywords):
                self.abuse_type = AbuseType.HARMFUL
                await self.say((
                    "You selected __6. Harmful or Dangerous Content__.",
                    emergencyWarning
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.SUICIDE_CHECK)
            elif message == "7" or any(keyword in ("violence", "violent", "terrorism", "terror", "terrorist", "promote", "incite", "inciting", "incites") for keyword in keywords):
                self.abuse_type = AbuseType.VIOLENCE
                await self.say((
                    "You selected: __7. Promoting Violence or Terrorism__.",
                    emergencyWarning
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.CURRENT_EVENTS_CHECK)
            elif message == "8" or any(keyword in ("child", "children", "kid", "kids", "minor", "minors", "abuse", "csam") for keyword in keywords):
                self.abuse_type = AbuseType.CSAM
                await self.say((
                    "You selected: __8. Child Abuse__.",
                    emergencyWarning
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.CURRENT_EVENTS_CHECK)
            else:
                return "Sorry, I didn't understand your reply. Try different words, or click one of the buttons above."

    # Check if the person submitting the report is the victim
    @Flow.help_message("""
        Select whether you are the victimized user. If you are submitting this report on someone else's behalf, select no (you'll have a chance to specify who).
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def check_if_victim(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                "Does the content target you specifically?",
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                self.victim = self.reporter
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message.lower() in NO_KEYWORDS:
                return await self.transition_to_state(UserReportCreationFlow.State.ASK_FOR_VICTIM)
            else:
                return "Sorry, I didn't understand that; please say `yes` or `no`."

    # Ask the user to supply the victimized user
    @Flow.help_message("""
        Type a username to search for them. The `@` at the beginning isn't necessary (since they won't appear in DMs).
        You can also search by their nickname in a guild.
    """)
    async def ask_for_victim(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                """
                    If you want to specify the user being victimized, you can do so here. This will help us review your report faster.
                    Otherwise, you can push the checkmark below, or say `done` to leave this empty.
                """,
                self.react_done()
            )
        else:
            if message.lower() == "done":
                self.victim = None
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)

            if message[0] == "@":
                message = message[1:]

            # Get a list of guilds that both the bot and the user are both in
            commonGuilds = []
            for guild in self.client.guilds:
                if discord.utils.get(guild.members, id=self.reporter.id) is not None:
                    commonGuilds.append(guild)

            # Parse out a discriminator if the name includes one
            discrim = re.search(r"#\d+$", message)
            if discrim is not None:
                discrim = str(int(discrim.group(0)[1:]))
                username = message[:-len(discrim) - 1]
            else:
                username = message

            # Search each common guild for a user with the specified user name or display name.
            for guild in commonGuilds:
                # Filter out users if a discriminator was given
                if discrim is not None:
                    members = tuple(filter(lambda member: member.discriminator == discrim, guild.members))
                else:
                    members = guild.members

                matches = set(filter(lambda member: member.name.lower() == username.lower(), members))
                matches.update(filter(lambda member: member.display_name.lower() == username.lower(), members))
                matches = tuple(matches)

                # Check if we only got one result
                if len(matches) == 1:
                    member = matches[0]
                    self.victim = member
                    await self.say(f"You selected {member.mention} – **{member.display_name}**#{member.discriminator}")
                    await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)

                # Show that there were multiple users (ask for username AND discriminator)
                elif len(matches) >= 2:
                    matches = matches[:10]
                    return (
                        "There were multiple results for your search:\n" +
                        "\n".join(f" {i+1}. {text}" for i, text in enumerate(map(lambda member: f"{member.mention} – **{member.display_name}**#{member.discriminator}", matches))) +
                        f"\nPlease search using both the **Username** *and* #Discriminator (e.g., `{self.reporter.name}#{self.reporter.discriminator}`).",
                    )

                # Show that there were no results
                else:
                    return f"""
                        I couldn't find any users with the user name `{message}`. Only users in guilds we are both a part of are searchable.
                        Please try again or say `done` to skip this step.
                    """

    # Check if there is any suicide or self-harm in the reported message
    @Flow.help_message("""
        Please let us know whether this situation requires immediate action. including if someone is in immediate danger of committing suicide or self-harm.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def suicide_check(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                "Does the content contain any self-harm or suicide that requires immediate action?",
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                self.urgent = True
                await self.say(discord.Embed(
                    title="Call 911.",
                    description="We will do what we can to reach out to this person on our end as soon as we can, but please take immediate action or let someone know who can. Time-sensitive emergencies can be best handled by local authorities.",
                    color=discord.Color.red()
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message.lower() in NO_KEYWORDS:
                self.urgent = False
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            else:
                return "Sorry, I didn't understand that; please say `yes` or `no`."

    # Check if the events in the reported message
    @Flow.help_message("""
        Please let us know whether this situation requires immediate action.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def current_events_check(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                "Does the content contain any events that are currently happening and require immediate action?",
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                self.urgent = True
                await self.say(discord.Embed(
                    title="Call 911.",
                    description="We will do what we can to reach out to this person on our end as soon as we can, but please take immediate action or let someone know who can. Time-sensitive emergencies can be best handled by local authorities.",
                    color=discord.Color.red()
                ))
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            elif message.lower() in NO_KEYWORDS:
                self.urgent = False
                return await self.transition_to_state(UserReportCreationFlow.State.ADD_COMMENT)
            else:
                return "Sorry, I didn't understand that; please say `yes` or `no`."

    # Ask the user to add any additional comments if they have any
    @Flow.help_message("Enter additional comments to submit alongside your report, or type `done` to skip this step.")
    async def add_comment(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                self.react_done()
            )
        else:
            if message.lower() == "done":
                self.comments = None
            else:
                self.comments = message
            await self.transition_to_state(UserReportCreationFlow.State.FINALIZE_REPORT)

    # Let the user look at their own report and decide when they want to submit.
    @Flow.help_message("Review your report above and type `done` when you're ready to submit.")
    async def finalize_report(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                "This is what your report looks like so far:",
                self.as_embed(),
                "Press the checkmark below, or type `done` when you're ready to send it.",
                self.react_done()
            )
        else:
            if message.lower() == "done":
                self.sent_report = UserReport(
                    report_creation_flow=self
                )

                asyncio.gather(*(self.sent_report.send_to_channel(channel, assignable=True) for channel in self.client.mod_channels.values()))

                return await self.transition_to_state(UserReportCreationFlow.State.FINISH_REPORT)
            else:
                return (
                    "Sorry, I didn't understand that. Please reply with `done` when you're ready to submit your report, or click the checkmark below.",
                    self.react_done()
                )

    async def finish_report(self, message, simulated=False, introducing=False):
        del self.client.user_reports[self.reporter.id]
        return "Thank you for reporting! You will receive a message when someone on the content moderation team has reviewed your report."

    @Flow.help_message("""
        Decide whether you really want to cancel the reporting process.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def report_quit(self, message, simulated=False, introducing=False, revert=None):
        if introducing:
            return (
                "Are you sure you want to quit the reporting process? All the progress you've made will be lost.",
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                await self.say("Your report has been canceled.")
                del self.client.user_reports[self.reporter.id]
            elif message.lower() in NO_KEYWORDS:
                await revert()
            else:
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    def as_embed(self):
        embed = discord.Embed(
            color=discord.Color.blurple()
        ).set_author(
            name=self.reporter.display_name,
            icon_url=self.reporter.avatar_url
        ).add_field(
            name="Abuse Type",
            value=self.abuse_type.value if isinstance(self.abuse_type, AbuseType) else "*[Unspecified]*",
            inline=False
        ).add_field(
            name="Message",
            value=self.message.content,
            inline=False
        )
        if hasattr(self, "victim"):
            embed.add_field(
                name="Victimized User",
                value=self.victim.mention if self.victim else "*[Unspecified]*",
                inline=False
            )
        if hasattr(self, "urgent"):
            embed.add_field(
                name="Urgent",
                value="Yes" if self.urgent else "No",
                inline=False
            )
        embed.add_field(
            name="Additional Comments",
            value="*[None]*" if self.comments is None else self.comments,
            inline=False
        )
        if self.sent_report:
            if self.sent_report.status == ReportStatus.NEW:
                embed.set_footer(text=f"This report was opened on {time.strftime('%b %d, %Y at %I:%M %p %Z', self.sent_report.creation_time)}.")
            elif self.sent_report.status == ReportStatus.PENDING:
                embed.set_footer(text=f"This report is being addressed.")
            elif self.sent_report.status == ReportStatus.RESOLVED:
                embed.set_footer(text=f"This report was closed on {time.strftime('%b %d, %Y at %I:%M %p %Z', self.sent_report.resolution_time)}.")
        else:
            embed.set_footer(text="This report has not yet been submitted.")
        return embed


# A Flow for reviewing UserReports
class UserReportReviewFlow(Flow):
    State = Enum("UserReportReviewFlowState", (
        "REVIEW_START",
        "REVIEW_RESTART",
        "CONFIRM_DELETE",
        "CONFIRM_KICK",
        "CONFIRM_BAN",
        "CONFIRM_ESCALATE",
        "CONFIRM_NCMEC",
        "REVIEW_QUIT"
    ))

    def __new__(cls, report, *args, **kwargs):
        # A UserReportReviewFlow is differentiated into a subclass depending on its abuse type to allow for different flows
        # Calling UserReportReviewFlow automatically returns one of its subclasses
        if cls is not UserReportReviewFlow:
            return super().__new__(cls)
        abuse_type = report.abuse_type
        if abuse_type == AbuseType.SPAM:
            return SpamUserReportReviewFlow.__new__(SpamUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.HATEFUL:
            return GenericUserReportReviewFlow.__new__(GenericUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.SEXUAL:
            return GenericUserReportReviewFlow.__new__(GenericUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.HARASS:
            return GenericUserReportReviewFlow.__new__(GenericUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.BULLYING:
            return BullyingUserReportReviewFlow.__new__(BullyingUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.HARMFUL:
            return HarmfulUserReportReviewFlow.__new__(HarmfulUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.VIOLENCE:
            return ViolenceUserReportReviewFlow.__new__(ViolenceUserReportReviewFlow, report, *args, **kwargs)
        elif abuse_type == AbuseType.CSAM:
            return CSAMUserReportReviewFlow.__new__(CSAMUserReportReviewFlow, report, *args, **kwargs)
        else:
            return None

    def __init__(self, report, reviewer):
        super().__init__(channel=reviewer.dm_channel, start_state=UserReportReviewFlow.State.REVIEW_START, quit_state=UserReportReviewFlow.State.REVIEW_QUIT)
        self.report = report
        self.reviewer = reviewer

    # Performs a specified action
    # Called each time the reviewer takes an action
    async def perform_action(self, action):
        if self.report.status == ReportStatus.NEW:
            await self.warn(f"This report is not currently assigned to anyone. Assign it to yourself to take action.")
            return

        if self.report.status == ReportStatus.RESOLVED:
            await self.warn(f"This report has already been resolved by {self.report.assignee.mention} on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")
            return

        if self.reviewer.id != self.report.assignee.id:
            await self.warn(f"This report has been assigned to {self.report.assignee.mention}.")
            return

        if action == "delete":
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
                return

            if await self.report.delete_message():
                await self.inform("The message has been deleted.")
            else:
                await self.warn("There was a problem while attempting to delete the message.")

            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "warn":
            if await self.report.warn_user():
                await self.inform("A warning has been sent to the user.")
            else:
                await self.warn("There was a problem while attempting to warn the user.")

            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "kick":
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            if await self.report.kick_user():
                await self.inform("The user has been kicked from the guild.")
            else:
                await self.warn("There was a problem while attempting to kick the user from the guild.")

            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "ban":
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            if discord.utils.find(lambda user: user.id == self.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            if await self.report.ban_user():
                await self.inform("The user has been banned from the guild.")
            else:
                await self.warn("There was a problem while attempting to ban the user from the guild.")
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "escalate":
            if await self.report.contact_local_authorities():
                await self.inform("Your report has been escalated to local authorities.")
            else:
                await self.warn("There was a problem while attempting to escalate to local authorities.")
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "unassign":
            self.report.unassign()
            return await self.inform("You've unassigned yourself from this report. It is now up for grabs again.")
        elif action == "resolve":
            self.report.resolve()
            return await self.inform("Thank you for resolving this report. It has been removed from the list of reports.")

    # The starting state for a report review
    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            # Show the user the available actions
            await self.say("You've been assigned to the following report:")
            await self.report.send_to_channel(self.channel, assignable=False, self_destructible=False)
            return (
                """
                    Use the buttons below, or text commands to take action.
                    Say `help [command name]` for more information about each command.
                    🗑 `delete` – Delete the offending comment
                    ⚠️ `warn` – Warn the user that repeat offenses will get them kicked or banned
                    🥾 `kick` – Kick the offending user off the channel
                    💀 `ban` – Ban the offending user from the channel
                    🚨 `escalate` – Escalate this incident to local authorities
                    🚫 `unassign` – Unassign yourself from this report
                    ✅ `resolve` – Mark this report as resolved
                """,
                Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
            )
        else:
            message = message.lower()
            # Check if the message is just "help" on its own
            if message in HELP_KEYWORDS:
                return (
                    """
                        Select one of the buttons below to choose the associated action, or reply with one of these command names.
                        Say `help [command name]` for more information about each command.
                        🗑 `delete`
                        ⚠️ `warn`
                        🥾 `kick`
                        💀 `ban`
                        🚨 `escalate`
                        🚫 `unassign`
                        ✅ `resolve`
                    """,
                    Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                    Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                    Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                    Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                    Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                    Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                    Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
                )
            # Check if the message starts with "help" and is followed by a command name
            elif message.split()[0] in HELP_KEYWORDS:
                message = message[len(message.split()[0]):].strip()
                if message == "delete":
                    return "🗑 `delete` will permanently delete the message. The user who originally wrote the message will be notified that someone on the moderation team deleted their message."
                if message == "warn":
                    return "⚠️ `warn` will send a warning to the offending user that repeat offenses may get them kicked or banned from the server in the future. This should only be used when the reported message has been identified as an actual offense."
                elif message == "kick":
                    return "🥾 `kick` will kick the user off the guild. The user will still be able to re-join the guild as normal afterward if they choose to."
                elif message == "ban":
                    return "💀 `ban` will ban the user from the guild. This prevents the user from being able to re-join indefinitely as long as the ban isn't lifted. This should only be used for the most serious offenses."
                elif message == "escalate":
                    return "🚨 `escalate` will escalate this report to local authorities."
                elif message == "unassign":
                    return "🚫 `unasssign` will unassign this report from you. Any changes you made will remain in effect, but someone else will be able to assign themselves to handle the report instead."
                elif message == "resolve":
                    return "✅ `resolve` will mark this report as resolved and disappear from the report queue. This should only be done once you are done taking action with the report with other commands."
                else:
                    return f"There is no `{message}` command. Please use `help` for a list of options."
            # Check if the message matches any command names
            else:
                if message in ("warn", "unassign", "resolve"):
                    await self.perform_action(message)
                elif message in CANCEL_KEYWORDS:
                    await self.perform_action("unassign")
                elif message == "delete":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)
                elif message == "kick":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)
                elif message == "ban":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)
                elif message == "escalate":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)
                else:
                    return f"There is no `{message}` command. Please say `help` for a list of options."

    # Shows the list of command and then goes back into the REVIEW_START
    def review_restart(self, message, simulated=False, introducing=False):
        self.state = UserReportReviewFlow.State.REVIEW_START
        return (
            """
                You can use all these commands, or use buttons from the first message above.
                Say `help [command name]` for more information about each command.
                🗑 `delete` – Delete the offending comment
                ⚠️ `warn` – Warn the user that repeat offenses will get them kicked or banned
                🥾 `kick` – Kick the offending user off the channel
                💀 `ban` – Ban the offending user from the channel
                🚨 `escalate` – Escalate this incident to local authorities
                🚫 `unassign` – Unassign yourself from this report
                ✅ `resolve` – Mark this report as resolved
            """
        )

    # Ask the user to confirm that they actually want to delete the message
    @Flow.help_message("Confirm whether you really want to delete this message by saying `yes` or `no`.")
    async def confirm_delete(self, message, simulated=False, introducing=False):
        if introducing:
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to permanently delete this message. This action **cannot be undone**. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("delete")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Ask for confirmation for kicking a user
    @Flow.help_message("Confirm whether you really want to kick this user off the guild by saying `yes` or `no`.")
    async def confirm_kick(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to kick this user from the guild. This action can only be undone on the user's end by re-joining the guild. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("kick")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Ask for confirmation for banning a user
    @Flow.help_message("Confirm whether you really want to ban this user from the guild by saying `yes` or `no`.")
    async def confirm_ban(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            if discord.utils.find(lambda user: user.id == self.report.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to ban this user from the guild. This action is very hard to undo and should only be used for the most serious offenses. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("ban")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Ask for confirmation for escalating to local authority
    @Flow.help_message("Confirm whether you really want to escalate this report to local authorities by saying `yes` or `no`.")
    async def confirm_escalate(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to local authorities to be handled by law enforcement. This should only be used for illegal offenses or if someone is in danger.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("escalate")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    async def review_quit(self, message, simulated=False, introducing=False, revert=None):
        await self.perform_action("unassign")


# A subclass of UserRpoertReviewFlow specifically for handling Misinformation or Spam reports
class SpamUserReportReviewFlow(UserReportReviewFlow):
    # Show a message showing how spam messages should be handled, and then show the rest of review_start
    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.inform("""
                    This report is labeled as Misinformation or Spam. For these types of reports, the typical review flow is to:
                     1. Identify whether the message is indeed misinformation or spam.
                     2. If it is:
                      a. 🗑 `delete` the message.
                      b. ⚠️ `warn` the user about repeat offenses.
                     3. ✅ `resolve` the report.
                """, return_embed=True),
                *(await super().review_start(message, simulated=simulated, introducing=introducing))
            )
        else:
            return await super().review_start(message, simulated=simulated, introducing=introducing)

    # Show a confirmation, but emphasize that spamming rarely required kicking.
    @Flow.help_message("Confirm whether you really want to kick this user off the guild by saying `yes` or `no`.")
    async def confirm_kick(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to kick this user from the guild. For Misinformation or Spam reports, this is generally unnecessary unless the user has a history of repeated offenses. This action can only be undone on the user's end by re-joining the guild. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("kick")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Show a confirmation, but emphasize that spamming rarely required banning.
    @Flow.help_message("Confirm whether you really want to ban this user from the guild by saying `yes` or `no`.")
    async def confirm_ban(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            if discord.utils.find(lambda user: user.id == self.report.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to ban this user from the guild. For Misinformation or Spam reports, this is almost always unnecessary unless the user has a very long history of repeated offenses. This action is very hard to undo and should only be used for the most serious offenses. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("ban")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Show a confirmation, but emphasize that spamming almost never requires escalating.
    @Flow.help_message("Confirm whether you really want to escalate this report to local authorities by saying `yes` or `no`.")
    async def confirm_escalate(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to local authorities to be handled by law enforcement. For Misinformation or Spam reports, this action is **never** necessary unless the report has been mislabeled *and* is putting someone in danger or contains illegal content. Make sure this is the case before continuing.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("escalate")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."


# A subclass of UserReportReviewFlow that acts as a generic flow and does not implement any new methods
class GenericUserReportReviewFlow(UserReportReviewFlow):
    # Inherits all the methods from UserReportReviewFlow without modifications
    pass


# A subclass of UserReportReviewFlow specifically for handling Bullying
class BullyingUserReportReviewFlow(UserReportReviewFlow):
    # Add a new action to perform_action: "assist"
    async def perform_action(self, action):
        if self.report.status == ReportStatus.NEW:
            await self.warn(f"This report is not currently assigned to anyone. Assign it to yourself to take action.")
            return

        if self.report.status == ReportStatus.RESOLVED:
            await self.warn(f"This report has already been resolved by {self.report.assignee.mention} on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")
            return

        if self.reviewer.id != self.report.assignee.id:
            await self.warn(f"This report has been assigned to {self.report.assignee.mention}.")
            return

        if action == "assist":
            if await self.report.show_user_bullying_help():
                await self.inform("The user has received a help message.")
            else:
                await self.warn("There was a problem while attempting to send the user a help message.")
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        else:
            return await super().perform_action(action)

    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("You've been assigned to the following report:")
            await self.report.send_to_channel(self.channel, assignable=False, self_destructible=False)
            return (
                await self.inform("""
                    This report is labeled as Bullying. This report may contain sensitive content. For these types of reports, the typical review flow is to:
                     1. Identify whether the message is actually an instance of bullying.
                     2. If is a single instance of harassment:
                      a. 🗑 `delete` the message.
                      b. ⚠️ `warn` the user about repeat offenses.
                      a. Take action against the user according to the level of severity.
                     3. If it is consistent bullying:
                      a. 🗑 `delete` the message.
                      b. 🥾 `kick` or 💀 `ban` the user according to the level of severity.
                      c. 💬 `assist` the victim by sending them a message.
                      d. 🚨 `escalate` to local authorities if legal consequences are required or someone is in immediate danger.
                     4. ✅ `resolve` the report.
                """, return_embed=True),
                """
                    Use the buttons below, or text commands to take action.
                    Say `help [command name]` for more information about each command.
                    🗑 `delete` – Delete the offending comment
                    ⚠️ `warn` – Warn the user that more posts like this can result in more serious action
                    🥾 `kick` – Kick the offending user off the channel
                    💀 `ban` – Ban the offending user from the channel
                    🚨 `escalate` – Escalate this incident to local authorities
                    💬 `assist` – Send the user a message showing the suicide lifeline and to reach out for help
                    🚫 `unassign` – Unassign yourself from this report
                    ✅ `resolve` – Mark this report as resolved
                """,
                Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                Reaction("💬", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("assist")), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
            )
        else:
            message = message.lower()
            if message in HELP_KEYWORDS:
                return (
                    """
                        Select one of the buttons below to choose the associated action, or reply with one of these command names.
                        Say `help [command name]` for more information about each command.
                        🗑 `delete`
                        ⚠️ `warn`
                        🥾 `kick`
                        💀 `ban`
                        🚨 `escalate`
                        💬 `assist`
                        🚫 `unassign`
                        ✅ `resolve`
                    """,
                    Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                    Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                    Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                    Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                    Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                    Reaction("💬", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("assist")), once_per_message=False),
                    Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                    Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
                )
            elif message.split()[0] in HELP_KEYWORDS:
                message = message[len(message.split()[0]):].strip()
                if message == "delete":
                    return "🗑 `delete` will permanently delete the message. The user who originally wrote the message will be notified that someone on the moderation team deleted their message."
                if message == "warn":
                    return "⚠️ `warn` will send a warning to the offending user that repeat offenses may get them kicked or banned from the server in the future. This should only be used for unintentional cases, such as a showering toddler in a non-sexual context."
                elif message == "kick":
                    return "🥾 `kick` will kick the user off the guild. The user will still be able to re-join the guild as normal afterward if they choose to."
                elif message == "ban":
                    return "💀 `ban` will ban the user from the guild. This prevents the user from being able to re-join indefinitely as long as the ban isn't lifted. This should only be used for the most serious offenses."
                elif message == "escalate":
                    return "🚨 `escalate` will escalate this report to local authorities."
                elif message == "ncmec":
                    return "💬 `assist` will send the user a help message with the suicide lifeline and advice to reach out to friends."
                elif message == "unassign":
                    return "🚫 `unasssign` will unassign this report from you. Any changes you made will remain in effect, but someone else will be able to assign themselves to handle the report instead."
                elif message == "resolve":
                    return "✅ `resolve` will mark this report as resolved and disappear from the report queue. This should only be done once you are done taking action with the report with other commands."
                else:
                    return f"There is no `{message}` command. Please use `help` for a list of options."
            else:
                if message in ("warn", "unassign", "resolve", "assist"):
                    await self.perform_action(message)
                elif message in CANCEL_KEYWORDS:
                    await self.perform_action("unassign")
                elif message == "delete":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)
                elif message == "kick":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)
                elif message == "ban":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)
                elif message == "escalate":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)
                else:
                    return f"There is no `{message}` command. Please say `help` for a list of options."

    def review_restart(self, message, simulated=False, introducing=False):
        self.state = UserReportReviewFlow.State.REVIEW_START
        return (
            """
                You can use all these commands, or use buttons from the first message above.
                Say `help [command name]` for more information about each command.
                🗑 `delete` – Delete the offending comment
                ⚠️ `warn` – Warn the user that repeat offenses will get them kicked or banned
                🥾 `kick` – Kick the offending user off the channel
                💀 `ban` – Ban the offending user from the channel
                🚨 `escalate` – Escalate this incident to local authorities
                💬 `assist` – Send the user a message showing the suicide lifeline and to reach out for help
                🚫 `unassign` – Unassign yourself from this report
                ✅ `resolve` – Mark this report as resolved
            """
        )

    @Flow.help_message("Confirm whether you really want to escalate this report to local authorities by saying `yes` or `no`.")
    async def confirm_escalate(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to local authorities to be handled by law enforcement. This action can be taken either for when the bullying involves illegal content or threats, or you believe the victim is in immediate danger. Make sure this is the case before continuing.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("escalate")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)


# A subclass of UserReportReviewFlow specifically for handling Harmful or Dangerous Content
class HarmfulUserReportReviewFlow(UserReportReviewFlow):
    # Add a new action to perform_action: "assist"
    async def perform_action(self, action):
        if self.report.status == ReportStatus.NEW:
            await self.warn(f"This report is not currently assigned to anyone. Assign it to yourself to take action.")
            return

        if self.report.status == ReportStatus.RESOLVED:
            await self.warn(f"This report has already been resolved by {self.report.assignee.mention} on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")
            return

        if self.reviewer.id != self.report.assignee.id:
            await self.warn(f"This report has been assigned to {self.report.assignee.mention}.")
            return

        if action == "assist":
            if await self.report.show_user_suicide_help():
                await self.inform("The user has received a help message.")
            else:
                await self.warn("There was a problem while attempting to send the user a help message.")
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        else:
            return await super().perform_action(action)

    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("You've been assigned to the following report:")
            await self.report.send_to_channel(self.channel, assignable=False, self_destructible=False)
            return (
                await self.inform("""
                    This report is labeled as harmful or dangerous content, which may include instances of suicide or self-harm These types of reports are very sensitive and should be handled with care. The typical review flow is to:
                     1. Identify whether the message is an instance of suicide or self-harm.
                     2. If it is:
                      a. 💬 `assist` the user by sending them a message.
                      b. 🚨 `escalate` the report to local authorities if you believe the user is in immediate danger.
                     3. If the content *promotes* suicide or self-harm:
                      a. 🗑 `delete` the post.
                      b. Take action against the user according to the level of severity.
                     4. ✅ `resolve` the report.
                """, return_embed=True),
                """
                    Use the buttons below, or text commands to take action.
                    Say `help [command name]` for more information about each command.
                    🗑 `delete` – Delete the offending comment
                    ⚠️ `warn` – Warn the user that more posts like this can result in more serious action
                    🥾 `kick` – Kick the offending user off the channel
                    💀 `ban` – Ban the offending user from the channel
                    🚨 `escalate` – Escalate this incident to local authorities
                    💬 `assist` – Send the user a message showing the suicide lifeline and to reach out for help
                    🚫 `unassign` – Unassign yourself from this report
                    ✅ `resolve` – Mark this report as resolved
                """,
                Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                Reaction("💬", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("assist")), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
            )
        else:
            message = message.lower()
            if message in HELP_KEYWORDS:
                return (
                    """
                        Select one of the buttons below to choose the associated action, or reply with one of these command names.
                        Say `help [command name]` for more information about each command.
                        🗑 `delete`
                        ⚠️ `warn`
                        🥾 `kick`
                        💀 `ban`
                        🚨 `escalate`
                        💬 `assist`
                        🚫 `unassign`
                        ✅ `resolve`
                    """,
                    Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                    Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                    Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                    Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                    Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                    Reaction("💬", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("assist")), once_per_message=False),
                    Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                    Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
                )
            elif message.split()[0] in HELP_KEYWORDS:
                message = message[len(message.split()[0]):].strip()
                if message == "delete":
                    return "🗑 `delete` will permanently delete the message. The user who originally wrote the message will be notified that someone on the moderation team deleted their message."
                if message == "warn":
                    return "⚠️ `warn` will send a warning to the offending user that repeat offenses may get them kicked or banned from the server in the future. This should only be used for unintentional cases, such as a showering toddler in a non-sexual context."
                elif message == "kick":
                    return "🥾 `kick` will kick the user off the guild. The user will still be able to re-join the guild as normal afterward if they choose to."
                elif message == "ban":
                    return "💀 `ban` will ban the user from the guild. This prevents the user from being able to re-join indefinitely as long as the ban isn't lifted. This should only be used for the most serious offenses."
                elif message == "escalate":
                    return "🚨 `escalate` will escalate this report to local authorities."
                elif message == "ncmec":
                    return "💬 `assist` will send the user a help message with the suicide lifeline and advice to reach out to friends."
                elif message == "unassign":
                    return "🚫 `unasssign` will unassign this report from you. Any changes you made will remain in effect, but someone else will be able to assign themselves to handle the report instead."
                elif message == "resolve":
                    return "✅ `resolve` will mark this report as resolved and disappear from the report queue. This should only be done once you are done taking action with the report with other commands."
                else:
                    return f"There is no `{message}` command. Please use `help` for a list of options."
            else:
                if message in ("warn", "unassign", "resolve", "assist"):
                    await self.perform_action(message)
                elif message in CANCEL_KEYWORDS:
                    await self.perform_action("unassign")
                elif message == "delete":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)
                elif message == "kick":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)
                elif message == "ban":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)
                elif message == "escalate":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)
                else:
                    return f"There is no `{message}` command. Please say `help` for a list of options."

    def review_restart(self, message, simulated=False, introducing=False):
        self.state = UserReportReviewFlow.State.REVIEW_START
        return (
            """
                You can use all these commands, or use buttons from the first message above.
                Say `help [command name]` for more information about each command.
                🗑 `delete` – Delete the offending comment
                ⚠️ `warn` – Warn the user that repeat offenses will get them kicked or banned
                🥾 `kick` – Kick the offending user off the channel
                💀 `ban` – Ban the offending user from the channel
                🚨 `escalate` – Escalate this incident to local authorities
                💬 `assist` – Send the user a message showing the suicide lifeline and to reach out for help
                🚫 `unassign` – Unassign yourself from this report
                ✅ `resolve` – Mark this report as resolved
            """
        )

    @Flow.help_message("Confirm whether you really want to escalate this report to local authorities by saying `yes` or `no`.")
    async def confirm_escalate(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to local authorities to be handled by law enforcement. This action should only be taken if someone is at risk of committing self-harm or suicide. Make sure this is the case before continuing.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("escalate")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."


# A subclass of UserReportReviewFlow specifically for handling Promoting Violence or Terrorism
class ViolenceUserReportReviewFlow(UserReportReviewFlow):
    # Show a typical review flow message
    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.inform("""
                    This report is labeled as Promoting Violence or Terrorism. This report may contain sensitive content. For these types of reports, the typical review flow is to:
                     1. Identify whether the message indeed promotes violence or terrorism.
                     2. If it does:
                      a. 🚨 `escalate` the message to local authorities if anyone is in immediate danger or is being threatened.
                      b. 🗑 `delete` the message.
                      c. Take action against the user according to the level of severity.
                     3. ✅ `resolve` the report.
                """, return_embed=True),
                *(await super().review_start(message, simulated=simulated, introducing=introducing))
            )
        else:
            return await super().review_start(message, simulated=simulated, introducing=introducing)


# A subclass of UserReportReviewFlow specifically for handling Child Abuse
class CSAMUserReportReviewFlow(UserReportReviewFlow):
    # Add a new action to perform_action: "ncmec"
    async def perform_action(self, action):
        if self.report.status == ReportStatus.NEW:
            await self.warn(f"This report is not currently assigned to anyone. Assign it to yourself to take action.")
            return

        if self.report.status == ReportStatus.RESOLVED:
            await self.warn(f"This report has already been resolved by {self.report.assignee.mention} on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")
            return

        if self.reviewer.id != self.report.assignee.id:
            await self.warn(f"This report has been assigned to {self.report.assignee.mention}.")
            return

        if action == "ncmec":
            if await self.report.contact_local_authorities():
                await self.inform("Your report has been escalated to NCMEC.")
            else:
                await self.warn("There was a problem while attempting to escalate to NCMEC.")
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
            return
        else:
            return await super().perform_action(action)

    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("You've been assigned to the following report:")
            await self.report.send_to_channel(self.channel, assignable=False, self_destructible=False)
            return (
                await self.inform("""
                    This report is labeled as Child Abuse. These types of reports are very sensitive and should be handled with care. The typical review flow is to:
                     1. Identify whether the message is actually an instance of child abuse.
                     2. If it is:
                      a. 🚼 send the report to `ncmec`.
                      b. 🚨 `escalate` the report to local authorities.
                      c. 💀 `ban` the user. 
                      d. 🗑 `delete` the post.
                     3. If it questionable (e.g., a picture of a toddler in the shower in a non-sexual context):
                      a. ⚠️ `warn` the user.
                      b. 🗑 `delete` the post.
                     4. ✅ `resolve` the report.
                """, return_embed=True),
                """
                    Use the buttons below, or text commands to take action.
                    Say `help [command name]` for more information about each command.
                    🗑 `delete` – Delete the offending comment
                    ⚠️ `warn` – Warn the user that more posts like this can result in more serious action
                    🥾 `kick` – Kick the offending user off the channel
                    💀 `ban` – Ban the offending user from the channel
                    🚨 `escalate` – Escalate this incident to local authorities
                    🚼 `ncmec` – Send the report to NCMEC
                    🚫 `unassign` – Unassign yourself from this report
                    ✅ `resolve` – Mark this report as resolved
                """,
                Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                Reaction("🚼", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_NCMEC)), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
            )
        else:
            message = message.lower()
            if message in HELP_KEYWORDS:
                return (
                    """
                        Select one of the buttons below to choose the associated action, or reply with one of these command names.
                        Say `help [command name]` for more information about each command.
                        🗑 `delete`
                        ⚠️ `warn`
                        🥾 `kick`
                        💀 `ban`
                        🚨 `escalate`
                        🚼 `ncmec`
                        🚫 `unassign`
                        ✅ `resolve`
                    """,
                    Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                    Reaction("⚠️", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("warn")), once_per_message=False),
                    Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                    Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                    Reaction("🚨", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)), once_per_message=False),
                    Reaction("🚼", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(UserReportReviewFlow.State.CONFIRM_NCMEC)), once_per_message=False),
                    Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                    Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
                )
            elif message.split()[0] in HELP_KEYWORDS:
                message = message[len(message.split()[0]):].strip()
                if message == "delete":
                    return "🗑 `delete` will permanently delete the message. The user who originally wrote the message will be notified that someone on the moderation team deleted their message."
                if message == "warn":
                    return "⚠️ `warn` will send a warning to the offending user that repeat offenses may get them kicked or banned from the server in the future. This should only be used for unintentional cases, such as a showering toddler in a non-sexual context."
                elif message == "kick":
                    return "🥾 `kick` will kick the user off the guild. The user will still be able to re-join the guild as normal afterward if they choose to."
                elif message == "ban":
                    return "💀 `ban` will ban the user from the guild. This prevents the user from being able to re-join indefinitely as long as the ban isn't lifted. This should only be used for the most serious offenses."
                elif message == "escalate":
                    return "🚨 `escalate` will escalate this report to local authorities."
                elif message == "ncmec":
                    return "🚼 `ncmec` will send the report to NCMEC."
                elif message == "unassign":
                    return "🚫 `unasssign` will unassign this report from you. Any changes you made will remain in effect, but someone else will be able to assign themselves to handle the report instead."
                elif message == "resolve":
                    return "✅ `resolve` will mark this report as resolved and disappear from the report queue. This should only be done once you are done taking action with the report with other commands."
                else:
                    return f"There is no `{message}` command. Please use `help` for a list of options."
            else:
                if message in ("warn", "unassign", "resolve"):
                    await self.perform_action(message)
                elif message in CANCEL_KEYWORDS:
                    await self.perform_action("unassign")
                elif message == "delete":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_DELETE)
                elif message == "kick":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_KICK)
                elif message == "ban":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_BAN)
                elif message == "escalate":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_ESCALATE)
                elif message == "ncmec":
                    await self.transition_to_state(UserReportReviewFlow.State.CONFIRM_NCMEC)
                else:
                    return f"There is no `{message}` command. Please say `help` for a list of options."

    def review_restart(self, message, simulated=False, introducing=False):
        self.state = UserReportReviewFlow.State.REVIEW_START
        return (
            """
                You can use all these commands, or use buttons from the first message above.
                Say `help [command name]` for more information about each command.
                🗑 `delete` – Delete the offending comment
                ⚠️ `warn` – Warn the user that repeat offenses will get them kicked or banned
                🥾 `kick` – Kick the offending user off the channel
                💀 `ban` – Ban the offending user from the channel
                🚨 `escalate` – Escalate this incident to local authorities
                🚼 `ncmec` – Send the report to NCMEC
                🚫 `unassign` – Unassign yourself from this report
                ✅ `resolve` – Mark this report as resolved
            """
        )

    @Flow.help_message("Confirm whether you really want to kick this user off the guild by saying `yes` or `no`.")
    async def confirm_kick(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to kick this user from the guild. For Child Abuse reports, the user should be banned completely if this is an actual instance of child abuse. This action can only be undone on the user's end by re-joining the guild. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("kick")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("Confirm whether you really want to ban this user from the guild by saying `yes` or `no`.")
    async def confirm_ban(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            if discord.utils.find(lambda user: user.id == self.report.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = UserReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to ban this user from the guild. Only take this action if the report is an actual instance of Child Abuse. This action is very hard to undo. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("ban")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("Confirm whether you really want to escalate this report to local authorities by saying `yes` or `no`.")
    async def confirm_escalate(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to local authorities to be handled by law enforcement. This action should only be taken if this is actually an instance of Child Abuse. Make sure this is the case before continuing.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("escalate")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("Confirm whether you really want to escalate this report to NCMEC by saying `yes` or `no`.")
    async def confirm_ncmec(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                await self.warn("You are about to send this report to NCMEC. This action should only be taken if this is actually an instance of Child Abuse. Make sure this is the case before continuing.", return_embed=True),
                "Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("ncmec")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(UserReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."


# Flow for reviewing AutomatedReports
class AutomatedReportReviewFlow(Flow):
    State = Enum("AutomatedReportReviewFlowState", (
        "REVIEW_START",
        "REVIEW_RESTART",
        "CONFIRM_DELETE",
        "CONFIRM_KICK",
        "CONFIRM_BAN",
        "REVIEW_QUIT"
    ))

    def __init__(self, report, reviewer):
        super().__init__(channel=reviewer.dm_channel, start_state=AutomatedReportReviewFlow.State.REVIEW_START, quit_state=AutomatedReportReviewFlow.State.REVIEW_QUIT)
        self.report = report
        self.reviewer = reviewer

    async def perform_action(self, action):
        if self.report.status == ReportStatus.NEW:
            await self.warn(f"This report is not currently assigned to anyone. Assign it to yourself to take action.")
            return

        if self.report.status == ReportStatus.RESOLVED:
            await self.warn(f"This report has already been resolved by {self.report.assignee.mention} on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")
            return

        if self.reviewer.id != self.report.assignee.id:
            await self.warn(f"This report has been assigned to {self.report.assignee.mention}.")
            return

        if action == "toggle_visibility":
            action = "reveal" if self.report.message_hidden else "hide"

        if action == "hide":
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
                return

            if await self.report.hide_message():
                await self.inform("The message has been hidden.")
            else:
                await self.warn("There was a problem while attempting to hide the message.")

            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "reveal":
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
                return

            if await self.report.reveal_message():
                await self.inform("The message has been revealed.")
            else:
                await self.warn("There was a problem while attempting to reveal the message.")

            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "delete":
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
                return

            if await self.report.delete_message():
                await self.inform("The message has been deleted.")
            else:
                await self.warn("There was a problem while attempting to delete the message.")

            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "kick":
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            if await self.report.kick_user():
                await self.inform("The user has been kicked from the guild.")
            else:
                await self.warn("There was a problem while attempting to kick the user from the guild.")

            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "ban":
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return
            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return
            if discord.utils.find(lambda user: user.id == self.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            if await self.report.ban_user():
                await self.inform("The user has been banned from the guild.")
            else:
                await self.warn("There was a problem while attempting to ban the user from the guild.")
            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
            return
        elif action == "unassign":
            self.report.unassign()
            return await self.inform("You've unassigned yourself from this report. It is now up for grabs again.")
        elif action == "resolve":
            self.report.resolve()
            return await self.inform("Thank you for resolving this report. It has been removed from the list of reports.")

    async def review_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("You've been assigned to the following report:")
            await self.report.send_to_channel(self.channel, assignable=False, self_destructible=False)
            return (
                """
                    Use the buttons below, or text commands to take action.
                    Say `help [command name]` for more information about each command.
                    👁 `hide`/`reveal` – Hide the message behind spoilers or reveal it
                    🗑 `delete` – Delete the offending comment
                    🥾 `kick` – Kick the offending user off the channel
                    💀 `ban` – Ban the offending user from the channel
                    🚫 `unassign` – Unassign yourself from this report
                    ✅ `resolve` – Mark this report as resolved
                """,
                Reaction("👁", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("toggle_visibility")), once_per_message=False),
                Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
            )
        else:
            message = message.lower()
            if message in HELP_KEYWORDS:
                return (
                    """
                        Select one of the buttons below to choose the associated action, or reply with one of these command names.
                        Say `help [command name]` for more information about each command.
                        👁 `hide`/`reveal`
                        🗑 `delete`
                        🥾 `kick`
                        💀 `ban`
                        🚫 `unassign`
                        ✅ `resolve`
                    """,
                    Reaction("👁", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("toggle_visibility")), once_per_message=False),
                    Reaction("🗑", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_DELETE)), once_per_message=False),
                    Reaction("🥾", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_KICK)), once_per_message=False),
                    Reaction("💀", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_BAN)), once_per_message=False),
                    Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("unassign")), once_per_message=False),
                    Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.perform_action("resolve")), once_per_message=False)
                )
            elif message.split()[0] in HELP_KEYWORDS:
                message = message[len(message.split()[0]):].strip()
                if message == "hide":
                    return "👁 `hide` will hide the original message behind spoilers: ||like this||. This makes sure that only someone who actively clicks on the message will have to see it."
                elif message == "reveal":
                    return "👁 `reveal` will reveal a hidden message so that anyone can see it. A message may have been hidden automatically, or hidden using the `hide` command."
                elif message == "delete":
                    return "🗑 `delete` will permanently delete the message. The user who originally wrote the message will be notified that someone on the moderation team deleted their message."
                elif message == "kick":
                    return "🥾 `kick` will kick the user off the guild. The user will still be able to re-join the guild as normal afterward if they choose to."
                elif message == "ban":
                    return "💀 `ban` will ban the user from the guild. This prevents the user from being able to re-join indefinitely as long as the ban isn't lifted. This should only be used for the most serious offenses."
                elif message == "unassign":
                    return "🚫 `unasssign` will unassign this report from you. Any changes you made will remain in effect, but someone else will be able to assign themselves to handle the report instead."
                elif message == "resolve":
                    return "✅ `resolve` will mark this report as resolved and disappear from the report queue. This should only be done once you are done taking action with the report with other commands."
                else:
                    return f"There is no `{message}` command. Please use `help [command name]` with one of the following commands: `hide`, `reveal`, `delete`, `kick`, `ban`, `unassign`, `resolve`."
            else:
                if message in ("hide", "reveal", "unassign", "resolve"):
                    await self.perform_action(message)
                elif message in CANCEL_KEYWORDS:
                    await self.perform_action("unassign")
                elif message == "delete":
                    await self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_DELETE)
                elif message == "kick":
                    await self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_KICK)
                elif message == "ban":
                    await self.transition_to_state(AutomatedReportReviewFlow.State.CONFIRM_BAN)
                else:
                    return f"There is no `{message}` command. Please enter one of the following commands or say `help`: `hide`, `reveal`, `delete`, `kick`, `ban`, `unassign`, `resolve`."
        return

    def review_restart(self, message, simulated=False, introducing=False):
        self.state = AutomatedReportReviewFlow.State.REVIEW_START
        return (
            """
                You can use all these commands, or use buttons from the first message above.
                Say `help [command name]` for more information about each command.
                👁 `hide`/`reveal` – Hide the message behind spoilers or reveal it
                🗑 `delete` – Delete the offending comment
                🥾 `kick` – Kick the offending user off the channel
                💀 `ban` – Ban the offending user from the channel
                🚫 `unassign` – Unassign yourself from this report
                ✅ `resolve` – Mark this report as resolved
            """
        )

    @Flow.help_message("Confirm whether you really want to delete this message by saying `yes` or `no`.")
    async def confirm_delete(self, message, simulated=False, introducing=False):
        if introducing:
            if self.report.message_deleted:
                await self.inform("The message has already been deleted.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to permanently delete this message. This action **cannot be undone**. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("delete")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("Confirm whether you really want to kick this user off the guild by saying `yes` or `no`.")
    async def confirm_kick(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't kick a user from a private DM channel.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return
            if self.report.message.guild.get_member(self.report.message.author.id) is None:
                await self.inform("The user is no longer in the guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to kick this user from the guild. This action can only be undone on the user's end by re-joining the guild. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("kick")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("Confirm whether you really want to ban this user from the guild by saying `yes` or `no`.")
    async def confirm_ban(self, message, simulated=False, introducing=False):
        if introducing:
            if isinstance(self.report.message.channel, discord.DMChannel):
                await self.warn("You can't ban someone form a DM channel.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            try:
                bans = await self.report.message.guild.bans()
            except discord.errors.Forbidden:
                await self.warn("You don't have the right permissions to ban people from this guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            if discord.utils.find(lambda user: user.id == self.report.message.author.id, bans):
                await self.inform("This user has already been banned from this guild.")
                self.state = AutomatedReportReviewFlow.State.REVIEW_START
                return

            return (
                "You are about to ban this user from the guild. This action is very hard to undo and should only be used for the most serious offenses. Are you sure you want to continue?",
                self.react_yes(),
                self.react_no()
            )
        elif message.lower() in YES_KEYWORDS:
            await self.perform_action("ban")
        elif message.lower() in NO_KEYWORDS:
            await self.transition_to_state(AutomatedReportReviewFlow.State.REVIEW_RESTART)
        else:
            return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    async def review_quit(self, message, simulated=False, introducing=False, revert=None):
        await self.perform_action("unassign")