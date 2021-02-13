from enum import Enum, auto
import discord
import re
import asyncio
import time
from reactions import Reaction
from collections import OrderedDict
from difflib import SequenceMatcher
from helpers import *

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

emergencyWarning = discord.Embed(
    title="Call 911 in an emergency.",
    description="We will review your report as soon as we can, but calling 911 or other local authorities is the fastest and most effective way to handle emergencies.",
    color=discord.Color.red()
)


class OpenUserReport:
    START_KEYWORDS = START_KEYWORDS
    CANCEL_KEYWORDS = CANCEL_KEYWORDS
    HELP_KEYWORDS = HELP_KEYWORDS

    def __init__(self, client, author=None):
        self.state = OpenUserReportState.REPORT_START
        self.client = client
        self.report_fields = OrderedDict.fromkeys(("Abuse Type", "Message"))
        self.author = author
    
    async def handle_message(self, message, *args, simulated=False, **kwargs):
        """
        This function is the entry point for handling all messages. Depending on the
        Report's State, it'll branch to the corresponding method, which allows us to
        keep each State's logic separate and distinct.
        The simulated argument is a boolean indicating if this message originated from
        simulateReply.
        """

        content = message.strip() if isinstance(message, str) else message.content.strip()

        # If they say "cancel", cancel the report
        if content.lower() in self.CANCEL_KEYWORDS:
            self.state = OpenUserReportState.REPORT_COMPLETE
            return ["Report cancelled."]


        # Branch to the appropriate function depending on the state
        cb = getattr(self, self.state.name.lower(), None)
        if cb == None:
            raise Exception(f"The bot is in state {self.state}, but no method with the name `{self.state.name.lower()}` is given.")
        ret = await cb(content, simulated=simulated, *args, **kwargs) or []
        return (dedent(ret),) if isinstance(ret, str) or isinstance(ret, discord.Embed) else tuple(dedent(msg) for msg in ret) or ()


    ##################
    #                #
    #  Begin States  #
    #                #
    ##################


    async def report_start(self, message, simulated=False):
        self.state = OpenUserReportState.AWAITING_MESSAGE_LINK
        return (
            """
                Thank you for starting the reporting process.
                You can say `help` or `?` at any step for more information.
                Say `cancel` or `quit` at any time to cancel your report.
            """,
            """
                Please copy and paste the link to the message you want to report.
                You can obtain this link by right-clicking the message and clicking `Copy Message Link`.
            """
        )


    @makeHelpMsg("""
        Select a message to report and paste the link here.
        You can obtain a message's link by right-clicking the message and clicking `Copy Message Link`.
    """)
    async def awaiting_message_link(self, message, simulated=False):
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

        # We found the message
        self.report_fields["Message"] = message
        self.state = OpenUserReportState.AWAITING_ABUSE_TYPE
        return (
            "I found this message:",
            discord.Embed(
                title=message.author.name,
                description=message.content,
                color=discord.Color.greyple()
            ),
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
            *reactNumerical(self, 8)
        )


    @makeHelpMsg("""
        Enter a keyword from one of the abuse types above, or select one of the buttons to choose it.
    """)
    async def awaiting_abuse_type(self, message, simulated=False):
        keywords = message.lower().split()

        # Check for either numbers or certain keywords
        if message == "1" or any(keyword in ("misinformation", "disinformation", "spam", "misinfo", "disinfo", "information", "info") for keyword in keywords):
            self.state = OpenUserReportState.SPAM_ENTRY
            return (
                """
                    You selected: __1. Misinformation or Spam__
                """,
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message == "2" or any(keyword in ("hateful", "hate", "hatred", "racism", "racist", "sexist", "sexism") for keyword in keywords):
            self.state = OpenUserReportState.HATEFUL_ENTRY
            return (
                """
                    You selected: __2. Hateful Content__
                """,
                discord.Embed(
                    description="Please note that content that incites violence should be reported as Promoting Violence or Terrorism.", color=discord.Color.gold()
                ),
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message == "3" or any(keyword in ("sexual", "sex", "nude", "nudity", "naked") for keyword in keywords):
            self.state = OpenUserReportState.SEXUAL_ENTRY
            return (
                """
                    You selected: __3. Sexual Content__
                """,
                discord.Embed(
                    description="Please note that any sexual content involving minors should be reported as Child Abuse.", color=discord.Color.gold()
                ),
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message == "4" or any(keyword in ("harassment", "harass", "harassing") for keyword in keywords):
            self.state = OpenUserReportState.HARASS_ENTRY
            return (
                """
                    You selected: __4. Harassment__
                """,
                """
                    Does the content target you specifically?
                """,
                *reactYesNo(self)
            )
        elif message == "5" or any(keyword in ("bullying", "bully", "bullies", "cyberbullying", "cyberbully", "cyberbullies") for keyword in keywords):
            self.state = OpenUserReportState.BULLYING_ENTRY
            return (
                """
                    You selected: __5. Bullying__
                """,
                emergencyWarning,
                """
                    Are you specifically the target of this bullying?
                """,
                *reactYesNo(self)
            )
        elif message == "6" or any(keyword in ("harmful", "dangerous", "harm", "danger", "self-harm") for keyword in keywords):
            self.state = OpenUserReportState.HARMFUL_ENTRY
            return (
                """
                    You selected: __6. Harmful/Dangerous Content__
                """,
                emergencyWarning,
                """
                    Does the content contain any self-harm or suicide that requires immediate action?
                """,
                *reactYesNo(self)
            )
        elif message == "7" or any(keyword in ("violence", "violent", "terrorism", "terror", "terrorist", "promote", "incite", "inciting", "incites") for keyword in keywords):
            self.state = OpenUserReportState.VIOLENCE_ENTRY
            return (
                """
                    You selected: __7. Promoting Violence or Terrorism__
                """,
                emergencyWarning,
                """
                    Does the content contain any events that are currently happening and require immediate action?
                """,
                *reactYesNo(self)
            )
        elif message == "8" or any(keyword in ("child", "children", "kid", "kids", "minor", "minors", "abuse", "csam") for keyword in keywords):
            self.state = OpenUserReportState.CSAM_ENTRY
            return (
                """
                    You selected: __8. Child Abuse__
                """,
                emergencyWarning,
                """
                    Does the content contain any events that are currently happening and require immediate action?
                """,
                *reactYesNo(self)
            )


        return """
            Sorry, I didn't understand your reply. Try different words, or click one of the buttons above.
        """


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def spam_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.SPAM
        return await self.additional_comment(message, simulated=simulated)


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def hateful_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.HATEFUL
        return await self.additional_comment(message, simulated=simulated)


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def sexual_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.SEXUAL
        return await self.additional_comment(message, simulated=simulated)


    @makeHelpMsg("""
        Select whether the message you are reporting is harassing you specifically.
        If it is affecting someone else you know, select no.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def harass_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.HARASS
        if message.lower() in YES_KEYWORDS:
            self.report_fields["Personally Involved"] = True
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Personally Involved"] = False
        else:
            return """
                Sorry, I didn't understand that, please say `yes` or `no`.
            """

        self.state = OpenUserReportState.ADDITIONAL_COMMENT
        return (
            """
                If you have any comments you want to add to your report, enter them now.
                Otherwise, you can push the checkmark below, or say `done`.
            """,
            *reactDone(self)
        )


    @makeHelpMsg("""
        Select whether you are the victim of the bullying.
        If you are submitting this report on someone else's behalf, select no (you'll have a chance to specify who).
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def bullying_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.BULLYING
        if message.lower() in YES_KEYWORDS:
            self.report_fields["Personally Involved"] = True
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Personally Involved"] = False
            self.state = OpenUserReportState.BULLYING_ADD_USER
            return (
                """
                    If you want to specify the user being victimized, you can do so here. This will help us review your report faster.
                    Otherwise, you can push the checkmark below, or say `done` to leave this empty.
                """,
                *reactDone(self)
            )
        else:
            return """
                Sorry, I didn't understand that, please say `yes` or `no`.
            """


    @makeHelpMsg("""
        Type a username to search for them. The `@` at the beginning isn't necessary (since they won't appear in DMs).
        You can also search by their nickname in a guild.
    """)
    async def bullying_add_user(self, message, simulated=False):
        if message.lower() == "done":
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )

        if message[0] == "@":
            message = message[1:]

        # Get a list of guilds that both the bot and the user are both in
        commonGuilds = []
        for guild in self.client.guilds:
            if discord.utils.get(guild.members, id=self.author.id) is not None:
                commonGuilds.append(guild)

        # Search each common guild for a user with the specified user name or display name.
        for guild in commonGuilds:
            matches = findUsers(guild, message)

            # Check if we only got one result
            if len(matches) == 1:
                member = matches[0]
                if "Personally Involved" in self.report_fields:
                    del self.report_fields["Personally Involved"]
                self.report_fields["Victimized User"] = member

                self.state = OpenUserReportState.ADDITIONAL_COMMENT
                return (
                    f"""
                        You selected {member.mention} – **{member.display_name}**#{member.discriminator}
                    """,
                    """
                        If you have any comments you want to add to your report, enter them now.
                        Otherwise, you can push the checkmark below, or say `done`.
                    """,
                    *reactDone(self)
                )
            # Show that there were multiple users (ask for username AND discriminator)
            elif len(matches) >= 2:
                matches = matches[:10]
                return (
                    oneComment(
                        """
                            There were multiple results for your search:
                        """,
                        *(f" {i+1}. {text}" for i, text in enumerate(map(lambda member: f"{member.mention} – **{member.display_name}**#{member.discriminator}", matches))),
                        f"""
                            Please search using both the **Username** *and* #Discriminator (e.g., `{self.author.name}#{self.author.discriminator}`).
                        """
                    ),
                    *reactNumerical(self, (f"{member.name}#{member.discriminator}" for member in matches))
                )
            # Show that there were no results
            else:
                return f"""
                    I couldn't find any users with the user name `{message}`. Only users in guilds we are both a part of are searchable.
                    Please try again or say `done` to skip this step.
                """


    @makeHelpMsg("""
        Please let us know whether this situation requires immediate action.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def harmful_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.HARMFUL
        if message.lower() in YES_KEYWORDS:
            self.report_fields["Urgent Situation"] = True
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                discord.Embed(
                    title="Call 911",
                    description="""
                        We will do what we can to reach out to this person on our end as soon as we can, but please take immediate action or let someone know who can. Time-sensitive emergencies can be best handled by local authorities.
                    """,
                    color=discord.Color.red()
                ),
                """
                    If you have any additional info, please add it here, including any important details you think will help us solve this issue as fast as possible.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Urgent Situation"] = False
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        else:
            return """
                Sorry, I didn't understand that, please say `yes` or `no`.
            """


    @makeHelpMsg("""
        Please let us know whether this situation requires immediate action.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def violence_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.VIOLENCE
        if message.lower() in YES_KEYWORDS:
            self.report_fields["Urgent Situation"] = True
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                discord.Embed(
                    title="Call 911",
                    description="""
                        We will do what we can to reach out to this person on our end as soon as we can, but please take immediate action or let someone know who can. Time-sensitive emergencies can be best handled by local authorities.
                    """,
                    color=discord.Color.red()
                ),
                """
                    If you have any additional info, please add it here, including any important details you think will help us solve this issue as fast as possible.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Urgent Situation"] = False
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return ("""
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        else:
            return """
                Sorry, I didn't understand that, please say `yes` or `no`.
            """


    @makeHelpMsg("""
        Please let us know whether this situation requires immediate action.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def csam_entry(self, message, simulated=False):
        self.report_fields["Abuse Type"] = AbuseType.CSAM
        if message.lower() in YES_KEYWORDS:
            self.report_fields["Urgent Situation"] = True
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return (
                discord.Embed(
                    title="Call 911",
                    description="""
                        We will do what we can to reach out to this person on our end as soon as we can, but please take immediate action or let someone know who can. Time-sensitive emergencies can be best handled by local authorities.
                    """,
                    color=discord.Color.red()
                ),
                """
                    If you have any additional info, please add it here, including any important details you think will help us solve this issue as fast as possible.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Urgent Situation"] = False
            self.state = OpenUserReportState.ADDITIONAL_COMMENT
            return ("""
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        else:
            return """
                Sorry, I didn't understand that, please say `yes` or `no`.
            """


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def additional_comment(self, message, simulated=False):
        self.report_fields["Additional Comments"] = None if message.lower() == "done" else message

        self.state = OpenUserReportState.FINALIZE_REPORT
        return (
            """
                This is what your report looks like so far:
            """,
            reportPreview(self),
            """
                Press the checkmark below, or type `done` when you're ready to send it.
            """,
            *reactDone(self)
        )


    @makeHelpMsg("""
        Review your report above and type `done` when you're ready to submit.
    """)
    async def finalize_report(self, message, simulated=False):
        if message.lower() in ("done", "ready"):
            self.state = OpenUserReportState.REPORT_COMPLETE
            return self.sendReport()
        else:
            return (
                """
                    Sorry, I didn't understand that.
                    Please say `done` when you're ready to send your report, or press the checkmark below.
                """,
                *reactDone(self)
            )


    # Returns whether the Report has been completed (or canceled).
    def report_complete(self):
        return self.state == OpenUserReportState.REPORT_COMPLETE

    # Used as a helper function to define Reaction handlers
    # Returns a function that, when called, will simulate a reply from the user
    # Calling this function on its own will not simulate the reply;
    # Calling the function that this function returns will do it
    def simulateReply(self, reply):
        currentState = self.state
        async def sendReply(reaction, discordClient, discordReaction, user, *args, **kwargs):
            if self.state is not currentState:
                return

            try:
                responses = await self.handle_message(reply, *args, simulated=True, **kwargs)
            except Exception as e:
                await discordReaction.message.channel.send("Uh oh! There was a problem in the code! Check the console for more information.")
                raise e

            lastMessage = None
            for response in responses:
                if isinstance(response, Reaction):
                    asyncio.create_task(response.registerMessage(lastMessage))
                elif isinstance(response, discord.Embed):
                    lastMessage = await discordReaction.message.channel.send(embed=response)
                else:
                    lastMessage = await discordReaction.message.channel.send(content=response)
        return sendReply

    def sendReport(self):
        abuseType = self.report_fields["Abuse Type"]
        if abuseType == AbuseType.SPAM:
            urgency = 0
        elif abuseType == AbuseType.HATEFUL or abuseType == AbuseType.SEXUAL:
            urgency = 1
        elif abuseType == AbuseType.HARASS:
            urgency = 3 if self.report_fields["Personally Involved"] else 2
        elif abuseType == AbuseType.BULLYING:
            urgency = 3
        elif abuseType == AbuseType.VIOLENCE or abuseType == AbuseType.HARMFUL:
            urgency = 4 if self.report_fields["Urgent Situation"] else 3
        elif abuseType == AbuseType.CSAM:
            urgency = 4

        reportDict = reportPreview(self).to_dict()

        reportDict["color"] = (
            discord.Color.dark_gray().value,
            discord.Color.green().value,
            discord.Color.gold().value,
            discord.Color.orange().value,
            discord.Color.red().value
        )[urgency]

        reportEmbed = discord.Embed.from_dict(reportDict)

        reportEmbed.insert_field_at(0, name="Urgency", value=("Very Low", "Low", "Moderate", "High", "Very High")[urgency])
        reportEmbed.set_author(name=f"{self.author.display_name} – User Report", icon_url=self.author.avatar_url)
        reportEmbed.set_footer(text=f"This report was submitted on {time.strftime('%b %d, %Y at %I:%M %p %Z')}.")

        for channel in self.client.mod_channels.values():
            asyncio.create_task(channel.send(embed=reportEmbed))

        return """
            Thank you for reporting! You will receive a message when someone on the moderation team has reviewed your report.
        """


class Report():
    def __init__(self, urgency=0, message=None, abuse_type=None, client=None):
        self.urgency = urgency
        self.message = message
        self.abuse_type = abuse_type
        self.creation_time = time.localtime() # Time that the report was created
        self.resolution_time = None # Time that the report was resolved
        self.status = ReportStatus.NEW # Status of the report
        self.assignee = None # Who took on the report
        self._channel_messages = set()
        self.client = client

    def as_embed(self):
        embed = discord.Embed(
            color=(
                discord.Color.dark_gray().value,
                discord.Color.green().value,
                discord.Color.gold().value,
                discord.Color.orange().value,
                discord.Color.red().value
            )[self.urgency]
        )
        embed.add_field(name="Urgency", value=("Very Low", "Low", "Moderate", "High", "Very High")[self.urgency])
        embed.add_field(name="Abuse Type", value=self.abuse_type.value if isinstance(self.abuse_type, AbuseType) else "*[Unspecified]*", inline=False)
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
            await Reaction("✋", click_handler=self.reaction_attempt_assign, once_per_message=False).registerMessage(message)

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
                    reaction.unregisterMessage(discordClient, discordReaction.message),
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
                asyncio.create_task(assignReaction.registerMessage(msg[0]))

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
    def __init__(self, *args, author=None, comments=None, fields=None, **kwargs):
        super().__init__(*args, **kwargs)


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
        embed = super().as_embed(*args, **kwargs)
        embed.add_field(name="Original Message", value=f"[Jump to message]({self.replacement_message.jump_url})\n"+self.message.content, inline=False)
        embed.add_field(name="Visibility", value="Deleted" if self.message_deleted else "Hidden" if self.message_hidden else "Visible")
        embed.set_author(name="Automated Report")
        return embed

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
        embed = discord.Embed(
            description=self.message.content,
            color=discord.Color.dark_red()
        )
        embed.set_author(name=self.message.author.display_name, icon_url=self.message.author.avatar_url)
        await dm_channel.send(
            content="Your message was deleted by our content moderation team:",
            embed=embed
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

    # Kick the user from the channel (can still join back)
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


class Flow():
    def __init__(self, channel, start_state):
        self.channel = channel
        asyncio.create_task(self.transition_to_state(start_state))

    async def forward_message(self, message, simulated=False):
        await self.say(await self.resolve_message(message, simulated=False))

    async def resolve_message(self, message, simulated=False):
        message = message.content.strip() if isinstance(message, discord.Message) else message
        cb = getattr(self, self.state.name.lower())
        return (await cb(message, simulated=simulated) if asyncio.iscoroutinefunction(cb) else cb(message, simulated=simulated)) or ()

    async def say(self, msgs):
        msgs = (dedent(msgs),) if isinstance(msgs, str) or isinstance(msgs, discord.Embed) else tuple(dedent(msg) for msg in msgs) or ()

        lastMessage = None
        for msg in msgs:
            if isinstance(msg, Reaction):
                asyncio.create_task(msg.registerMessage(lastMessage))
            elif isinstance(msg, discord.Embed):
                lastMessage = await self.channel.send(embed=msg)
            else:
                lastMessage = await self.channel.send(content=msg)

    # Creates an Embed to inform the user of something
    async def inform(self, msg):
        return await self.say(discord.Embed(
            color=discord.Color.greyple(),
            description=msg
        ))

    # Creates an Embed to warn the user of something
    async def warn(self, msg):
        return await self.say(discord.Embed(
            color=discord.Color.gold(),
            description=msg
        ))

    # Transition to another state and run the function with the introducing parameter
    async def transition_to_state(self, state):
        self.state = state
        cb = getattr(self, self.state.name.lower())
        try:
            return await self.say((await cb("", introducing=True, simulated=False) if asyncio.iscoroutinefunction(cb) else cb("", introducing=True, simulated=True)) or ())
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


class AutomatedReportReviewFlow(Flow):
    COMMANDS = (
        "hide",
        "reveal",
        "delete",
        "kick",
        "ban",
        "unassign",
        "resolve"
    )

    State = Enum("AutomatedReportReviewFlowState", (
        "REVIEW_START",
        "REVIEW_RESTART",
        "CONFIRM_DELETE",
        "CONFIRM_KICK",
        "CONFIRM_BAN"
    ))

    def __init__(self, report, reviewer):
        super().__init__(channel=reviewer.dm_channel, start_state=AutomatedReportReviewFlow.State.REVIEW_START)
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