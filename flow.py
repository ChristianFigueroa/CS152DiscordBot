from enum import Enum, auto
import discord
import re
import asyncio
import time
from abc import ABC, abstractmethod
from io import BytesIO
from textwrap import dedent as _dedent
import cv2
from reactions import Reaction
from contextlib import suppress
import report
from consts import *


# Dedents a string and leaves non-strings alone
def dedent(obj):
    return _dedent(obj) if isinstance(obj, str) else obj

# Creates a textual preview of a message's content
# Usually, it's jsut the message's content but can also include images and files.
def message_preview_text(message):
    preview = ""

    # Show the message's textual content if it has any
    if len(message.content.strip()) > 0:
        preview = message.content.strip()
        if len(message.attachments) > 0:
            preview += " + "

    if len(message.attachments) > 0:
        # Get the number of images in the attachment list
        images = sum(1 for attachment in message.attachments if attachment.height is not None)
        # Same for all other files
        files = sum(1 for attachment in message.attachments if attachment.height is None)
        # Show the number of images/files in the message
        if images > 0 and files > 0:
            preview += f"*{images} image{'s' if images > 1 else ''} & {files} file{'s' if files > 1 else ''}*"
        elif images > 0:
            preview += f"*{images} image{'s' if images > 1 else ''}*"
        else:
            preview += f"*{files} file{'s' if files > 1 else ''}*"

    # Show that the message has no content
    if len(message.content.strip()) == 0 and len(message.attachments) == 0:
        preview = "*[No message content]*"

    return preview


# Helps with back and forth communication between the bot and a user
class Flow():
    def __init__(self, client, channel, start_state, quit_state=None):
        self.client = client
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
            except (TypeError, discord.errors.Forbidden):
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
        msgs = (dedent(msgs),) if isinstance(msgs, str) or isinstance(msgs, discord.Embed) or isinstance(msgs, discord.File) else tuple(dedent(msg) for msg in msgs) or ()

        lastMessage = None
        for msg in msgs:
            if isinstance(msg, Reaction):
                asyncio.create_task(msg.register_message(lastMessage))
            elif isinstance(msg, discord.Embed):
                lastMessage = await self.channel.send(embed=msg)
            elif isinstance(msg, discord.File):
                lastMessage = await self.channel.send(file=msg)
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
        except (TypeError, discord.errors.Forbidden):
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


# Just a abstract base class to inherit from for any Flow pertaining to reviewing a report
class ReportReviewFlow(ABC):
    @abstractmethod
    def __init__(self):
        pass


# A Flow that shows the user a warning that one of the messages they sent has been flagged and allows them to re-send it
class SentBadMessageFlow(Flow):
    State = Enum("SentBadMessageFlowState", (
        "START"
    ))
    def __init__(self, client, message, always_report=False, explicit=False, abuse_type=None, explanation=None, urgency=None):
        super().__init__(client=client, channel=message.author.dm_channel, start_state=SentBadMessageFlow.State.START)
        # The original discord.Message that got flagged
        self.message = message
        # Whether the message is explicit enough to hide the message with spoilers
        self.explicit = explicit
        # The type of abuse this message should be reported as
        self.abuse_type = abuse_type
        # A textual explanation for the content was flagged
        self.explanation = explanation
        # The AutomatedReport that may get sent
        self.report = None
        # A reference to the message that gets sent by the bot if the user decides to send their message
        self.replacement_message = None
        # A reference to the prefix message if the user decides to send their original message
        self.prefix_message = None
        # `always_report` indicates whether a report should be sent regardless of whether the user actually chooses to send the message
        self.always_report = always_report

        # Assign an urgency to this message based on the abuse type (or if urgency was specified)
        self.urgency = {
            None: 0,
            AbuseType.SPAM: 0,
            AbuseType.VIOLENCE: 2,
            AbuseType.SEXUAL: 1,
            AbuseType.HATEFUL: 1,
            AbuseType.HARASS: 1
        }[self.abuse_type] if urgency is None else urgency

        # After five minutes of inactivity, cancel this flow and tell the user that their message can no longer be re-sent by the bot
        # This is to prevent someone from just not answering anything, because we still need to send a report no matter what
        self.timeout_task = client.loop.call_later(5 * 60, self.timeout_reponse)

    async def start(self, message, simulated=False, introducing=False):
        # Show the user that their message was flagged and requires immediate action.
        if introducing:
            return (
                f"Your message was flagged{' ' + self.explanation if self.explanation else ''} and removed:",
                discord.Embed(
                    color=discord.Color.greyple(),
                    description=message_preview_text(self.message)
                ).set_author(name=self.message.author.display_name, icon_url=self.message.author.avatar_url),
                "Are you sure you want to send this message?" + (" It will be hidden from most users unless they decide to interact with the message." if self.explicit else ""),
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                # Resend the user's original message
                await self.resend_message()
                # Send an Automated Report for this message
                await self.send_report(outcome=True)
                # Delete the flow from the user's list of flows
                self.client.flows[self.message.author.id].remove(self)
                # Prevent the timeout timer from activating later
                self.timeout_task.cancel()
                # Show the user that their message was sent
                return (
                    "Your original message has been re-sent. You can jump to it by clicking below. Thank you for taking the time to reconsider your message:",
                    discord.Embed(
                        color=discord.Color.blue(), # Blue to match the link color
                        description=f"[Go to your message]({self.replacement_message.jump_url})"
                    )
                )
            elif message.lower() in NO_KEYWORDS:
                # Delete the flow from the user's list of flows
                self.client.flows[self.message.author.id].remove(self)
                # Prevent the timeout timer from activating later
                self.timeout_task.cancel()
                # Send a report is alwways_report is True
                if self.always_report:
                    await self.send_report(outcome=False)
                return "Thank you for taking the time to reconsider your message."
            else:
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    # Re-send the original message
    async def resend_message(self):
        # Get the original message channel to resend it to
        origChannel = self.message.channel

        # Get the files that were in the original message so we can resend them
        files = tuple(filter(
            lambda file: isinstance(file, discord.File),
            await asyncio.gather(*(attachment.to_file(use_cached=True, spoiler=True) for attachment in self.message.attachments), return_exceptions=True)
        ))

        # An "explicit" message is shown in spoilers to be the equivalent of Instagram's "Show Sensitive Content" functionality
        if self.explicit:
            content = self.message.content

            if self.client.smart_spoilers:
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
            self.prefix_message = await origChannel.send(content=f"*The following message may contain inappropriate content. Click the black bar to reveal it.*\n*{self.message.author.mention} says:*")
            
            # Send the hidden message
            self.replacement_message = await origChannel.send(content="||" + content + "||" if content else "", files=files)
        else:
            # Send a message to show who this message is from
            self.prefix_message = await origChannel.send(content=f"*{self.message.author.mention} says:*")
            # Show a non-explicit message as-is
            self.replacement_message = await origChannel.send(content=self.message.content, files=files)

        # Update message_aliases so that we know which original message these two replacement messages actually represent
        self.client.message_aliases[self.prefix_message.id] = self.message
        self.client.message_aliases[self.replacement_message.id] = self.message
        self.client.message_aliases[self.message.id] = self.replacement_message

        # Update message_pairs so that we can reference each of the two sent messages from each other
        self.client.message_pairs[self.replacement_message.id] = self.prefix_message
        self.client.message_pairs[self.prefix_message.id] = self.replacement_message

        # The replacement message gets an SOS reaction added to it
        await self.replacement_message.add_reaction("🆘")

    # Sends an Automated Report to the mod channel
    async def send_report(self, outcome):
        # `outcome` is a boolean indicating whether the user ended up resending the message
        self.report = report.AutomatedReport(
            client=self.client,
            urgency=self.urgency,
            abuse_type=self.abuse_type,
            message=self.message,
            replacement_message=self.replacement_message,
            prefix_message=self.prefix_message,
            message_hidden=self.explicit,
            message_deleted=not outcome
        )

        await asyncio.gather(*(self.report.send_to_channel(channel, assignable=True) for channel in self.client.mod_channels.values()))

    # A callback that gets called after five minutes if the user doesn't take any action
    def timeout_reponse(self):
        # Delete the flow from the user's list of flows
        self.client.flows[self.message.author.id].remove(self)
        # Tell the user that their inactivity caused them to not be able to take action anymore
        asyncio.create_task(self.say(("Your message can no longer be sent due to inactivity. You can send your original message manually if you'd like.")))
        # Send a report if always_report is enabled
        if self.always_report:
            asyncio.create_task(self.send_report(outcome=False))


# This Flow mimics the SentBadMessageFlow, except it is only a dummy warning that does not actually send a report to the mod channel
class CSAMDummyWarningFlow(SentBadMessageFlow):
    def __init__(self, client, message):
        super().__init__(client=client, message=message, always_report=False, explicit=True, abuse_type=AbuseType.SEXUAL, explanation="as having a sexually suggestive image", urgency=None)

    # We do not send a report for this dummy warning because a report has already been sent for its CSAM content
    async def send_report(self, *args, **kwargs):
        pass


# A Flow for when the user edits a message that gets flagged and needs to take action
class EditedBadMessageFlow(Flow):
    State = Enum("EditedBadMessageFlowState", (
        "START",
        "RESEND",
        "UNACCEPTABLE_EDIT",
        "ACCEPTABLE_EDIT",
        "TIME_EXPIRED"
    ))

    def __init__(self, client, message, explicit=False, reason=None, explanation=None, expiration_time=10 * 60):
        super().__init__(client=client, channel=message.author.dm_channel, start_state=EditedBadMessageFlow.State.START)
        self.author = message.author
        self.message = message
        self.explicit = explicit
        self.reason = reason
        self.explanation = explanation
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
        try:
            await self.message.delete()
        except discord.errors.NotFound:
            pass
        await self.close()
        await self.say("Your edited message was deleted due to inaction.")

    @Flow.help_message("Either say `re-send` to have the bot re-send your newly edited message, or make another edit to your message to something less inappropriate. If no action is taken within ten minutes, the message will be deleted.")
    async def start(self, message, simulated=False, introducing=False):
        if introducing:
            textReason = " " + self.explanation if self.explanation else ""

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
        if self.timer_message is not None:
            await self.timer_message.delete()
        self.timer_message = None
        self.client.flows[self.author.id].remove(self)
        del self.client.messages_pending_edit[self.message.id]



# A Flow that gets activated when someone reacts with SOS on a message
class SOSFlow(Flow):
    State = Enum("SOSFlowState", (
        "START"
    ))

    def __init__(self, client, message, user):
        super().__init__(client=client, channel=user.dm_channel, start_state=SOSFlow.State.START)
        self.message = message
        self.replacement_message = None
        if self.message.id in client.message_pairs:
            self.message = client.message_aliases[self.message.id]
            self.replacement_message = message
        self.user = user

    def start(self, message, simulated=False, introducing=False):
        self.client.flows[self.user.id].remove(self)
        return (
            "You clicked 🆘 on the following message:",
            discord.Embed(
                color=discord.Color.greyple(),
                description=message_preview_text(self.message)
            ).set_author(name=self.message.author.display_name, icon_url=self.message.author.avatar_url),
            discord.Embed(
                color=discord.Color.blurple(),
                description="If you need to, you can [find a local counselor](https://findtreatment.samhsa.gov/) or contact the National Suicide Prevention Lifeline at (800) 273-8255 or by visiting [suicidepreventionlifeline.org](https://suicidepreventionlifeline.org). We also recommend connecting with friends or loved ones for support."
            ),
            "If this message has inappropriate content, we encourage you to report it by clicking the 📋 below. If this is an emergency, **please call 911 right away**.",
            Reaction("📋", click_handler=self.start_report, once_per_message=False)
        )

    def start_report(self, reaction, discordClient, discordReaction, user):
        self.client.flows[self.user.id] = self.client.flows.get(self.user.id, [])
        self.client.flows[self.user.id].append(UserReportCreationFlow(
            client=self.client,
            reporter=self.user,
            message=self.replacement_message or self.message
        ))


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

    def __init__(self, client, reporter, message=None):
        super().__init__(client=client, channel=reporter.dm_channel, start_state=UserReportCreationFlow.State.REPORT_START, quit_state=UserReportCreationFlow.State.REPORT_QUIT)
        self.reporter = reporter
        self.abuse_type = None
        self.sent_report = None
        self.message = message
        if self.message and self.message.id in self.client.message_pairs:
            self.replacement_message = self.message
            self.message = self.client.message_aliases[self.message.id]
        else:
            self.replacement_message = None

    # Show an introduction and then go to AWAITING_MESSAGE_LINK state
    async def report_start(self, message, simulated=False, introducing=False):
        if introducing:
            await self.say("""
                Thank you for starting the reporting process.
                You can say `help` or `?` at any step for more information.
                Say `cancel` or `quit` at any time to cancel your report.
            """)
        # If we already know which message, we can skip to the next step in the process.
        if self.message:
            await self.transition_to_state(UserReportCreationFlow.State.AWAITING_ABUSE_TYPE)
        else:
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
            if message.id in self.client.message_pairs:
                self.replacement_message = message
                message = self.client.message_aliases[message.id]
            else:
                self.replacement_message = None

            # Save the message
            self.message = message

            await self.say((
                "I found this message:",
                discord.Embed(
                    description=message_preview_text(message),
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
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

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
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

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
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

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
                self.sent_report = report.UserReport(
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
        self.client.flows[self.reporter.id].remove(self)
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
                self.client.flows[self.reporter.id].remove(self)
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
            value=self.message.content or "*[No text]*",
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


# A Flow for reviewing flagged CSAM images
class CSAMImageReviewFlow(Flow, ReportReviewFlow):
    State = Enum("CSAMImageReviewFlowState", (
        "START",
        "VIEWING_IMAGE",
        "REPORTING",
        "IS_ADULT",
        "RESOLVING",
        "QUIT"
    ))

    def __init__(self, client, report, reviewer):
        super().__init__(client=client, channel=reviewer.dm_channel, start_state=CSAMImageReviewFlow.State.START, quit_state=CSAMImageReviewFlow.State.QUIT)
        self.report = report
        self.reviewer = reviewer

    @Flow.help_message("Say `yes` to view the image, or say `no` to unassign yourself.")
    async def start(self, message, simulated=False, introducing=False):
        if introducing:
            return (
                "You are about to view a grayscale image that was flagged as Child Sexual Abuse Material. Are you ready to view it?",
                self.react_yes(),
                self.react_no()
            )
        else:
            if message.lower() in YES_KEYWORDS:
                await self.transition_to_state(CSAMImageReviewFlow.State.VIEWING_IMAGE)
            elif message.lower() in NO_KEYWORDS + ("unassign",):
                await self.transition_to_state(CSAMImageReviewFlow.State.QUIT)
            else:
                return "Sorry, I didn't understand that. Please reply with `yes` or `no` or click one of the buttons above."

    @Flow.help_message("""
        Use one of the following text commands or click on the corresponding button above.
        🚼 `ncmec` – Send a report to NCMEC and close the report.
        🔞 `adult` – Make a new report for adult sexual content.
        🚫 `unassign` – Unassign yourself from this report.
        ✅ `resolve` – Resolve this report without taking any action.
    """)
    async def viewing_image(self, message, simulated=False, introducing=False):
        if introducing:
            _, buf = cv2.imencode(".jpg", cv2.cvtColor(self.report.img_array, cv2.COLOR_BGR2GRAY))
            return (
                discord.File(BytesIO(buf), self.report.img_name, spoiler=True),
                "Use the buttons below or text commands to take action. Say `help` for more information.",
                Reaction("🚼", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(CSAMImageReviewFlow.State.REPORTING)), once_per_message=False),
                Reaction("🔞", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(CSAMImageReviewFlow.State.IS_ADULT)), once_per_message=False),
                Reaction("🚫", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(CSAMImageReviewFlow.State.QUIT)), once_per_message=False),
                Reaction("✅", toggle_handler=lambda reaction, discordClient, discordReaction, user: asyncio.create_task(self.transition_to_state(CSAMImageReviewFlow.State.RESOLVING)), once_per_message=False)
            )
        else:
            if message.lower() == "ncmec":
                self.transition_to_state(CSAMImageReviewFlow.State.REPORTING)
            elif message.lower() == "adult":
                self.transition_to_state(CSAMImageReviewFlow.State.IS_ADULT)
            elif message.lower() == "resolve":
                self.transition_to_state(CSAMImageReviewFlow.State.RESOLVING)
            elif message.lower() == "unassign":
                self.transition_to_state(CSAMImageReviewFlow.State.QUIT)
            else:
                return "Sorry, I didn't understand that. Say help for a list of text commands you can use."

    async def reporting(self, message, simulated=False, introducing=False):
        # Check that the report is still active
        if self.report.status != ReportStatus.PENDING:
            return

        # Send the report to NCMEC
        self.client.report_ncmec(self.report.message.author, self.report.image)

        # Pretend to ban the user even though we can't :(

        # Delete the message
        try:
            await self.report.message.delete()
        except (discord.errors.Forbidden, discord.errors.NotFound):
            pass
        # Delete its alias message if one exists
        if self.report.message.id in self.client.message_aliases:
            replacement_message = self.client.message_aliases[self.report.message.id]
            prefix_message = self.client.message_pairs[replacement_message.id]
            try:
                await replacement_message.delete()
                await prefix_message.delete()
            except discord.errors.NotFound:
                pass

        # Save the image's hash in our csam.hashlist file for the future
        self.client.reviewer.save_hash(self.report.img_array)
        
        self.report.resolve()
        return await self.inform("You successfully reported the image to NCMEC. This report has been resolved.")

    async def is_adult(self, message, simulated=False, introducing=False):
        # Check that the report is still active
        if self.report.status != ReportStatus.PENDING:
            return

        if introducing:
            return (
                "A new user report for sexual content will be generated from this image. If you'd like to add any additional comments to the report, you can do so here, or say `done` to skip.",
                self.react_done()
            )
        else:
            # Fill out all the other fields that we need for a UserReport
            if message.lower() == "done":
                self.comments = None
            elif message.lower() == "unassign":
                self.transition_to_state(CSAMImageReviewFlow.State.QUIT)
            else:
                self.comments = message
            self.abuse_type = AbuseType.SEXUAL
            self.message = self.report.message
            self.reporter = self.reviewer
            self.replacement_message = self.client.message_aliases[self.report.message.id] if self.report.message.id in self.client.message_aliases else None
            self.victim = None
            self.urgent = False
            # Send the report
            self.sent_report = report.UserReport(
                report_creation_flow=self,
                notify_on_resolve=False
            )
            asyncio.gather(*(self.sent_report.send_to_channel(channel, assignable=True) for channel in self.client.mod_channels.values()))
            self.report.resolve()
            return await self.inform("This report for CSAM has been resolved, and another User Report for sexual content has been created.")

    async def resolving(self, message, simulated=False, introducing=False):
        # Check that the report is still active
        if self.report.status != ReportStatus.PENDING:
            return

        self.report.resolve()
        return await self.inform("Thank you for resolving this report. It has been removed from the list of reports.")

    async def quit(self, message, simulated=False, introducing=False, revert=None):
        # Check that the report is still active
        if self.report.status != ReportStatus.PENDING:
            return

        self.report.unassign()
        return await self.inform("You've unassigned yourself from this report. It is now up for grabs again.")


# Flow for reviewing AutomatedReports
class AutomatedReportReviewFlow(Flow, ReportReviewFlow):
    State = Enum("AutomatedReportReviewFlowState", (
        "REVIEW_START",
        "REVIEW_RESTART",
        "CONFIRM_DELETE",
        "CONFIRM_KICK",
        "CONFIRM_BAN",
        "REVIEW_QUIT"
    ))

    def __init__(self, client, report, reviewer):
        super().__init__(client=client, channel=reviewer.dm_channel, start_state=AutomatedReportReviewFlow.State.REVIEW_START, quit_state=AutomatedReportReviewFlow.State.REVIEW_QUIT)
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