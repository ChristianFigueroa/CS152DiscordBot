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
class State(Enum):
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
    EDIT_REPORT           = auto()
    REPORT_COMPLETE       = auto()

class AbuseType(Enum):
    SPAM      = "Misinformation or Spam"
    HATEFUL   = "Hateful Content"
    SEXUAL    = "Sexual Content"
    HARASS    = "Harassment"
    BULLYING = "Bullying"
    HARMFUL   = "Harmful/Dangerous Content"
    VIOLENCE  = "Promoting Violence or Terrorism"
    CSAM      = "Child Abuse"

emergencyWarning = discord.Embed(
    title="Call 911 in an emergency.",
    description="We will review your report as soon as we can, but calling 911 or other local authorities is the fastest and most effective way to handle emergencies.",
    color=discord.Color.red()
)


class Report:
    START_KEYWORDS = START_KEYWORDS
    CANCEL_KEYWORDS = CANCEL_KEYWORDS
    HELP_KEYWORDS = HELP_KEYWORDS

    def __init__(self, client, author=None):
        self.state = State.REPORT_START
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
            self.state = State.REPORT_COMPLETE
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
        self.state = State.AWAITING_MESSAGE_LINK
        return """
            Thank you for starting the reporting process.
            You can say `help` or `?` at any step for more information.
            Please copy paste the link to the message you want to report.
            You can obtain this link by right-clicking the message and clicking `Copy Message Link`.
        """


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
        self.state = State.AWAITING_ABUSE_TYPE
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
            self.state = State.SPAM_ENTRY
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
            self.state = State.HATEFUL_ENTRY
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
            self.state = State.SEXUAL_ENTRY
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
            self.state = State.HARASS_ENTRY
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
            self.state = State.BULLYING_ENTRY
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
            self.state = State.HARMFUL_ENTRY
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
            self.state = State.VIOLENCE_ENTRY
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
            self.state = State.CSAM_ENTRY
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

        self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
            return (
                """
                    If you have any comments you want to add to your report, enter them now.
                    Otherwise, you can push the checkmark below, or say `done`.
                """,
                *reactDone(self)
            )
        elif message.lower() in NO_KEYWORDS:
            self.report_fields["Personally Involved"] = False
            self.state = State.BULLYING_ADD_USER
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
            self.state = State.ADDITIONAL_COMMENT
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

                self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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
            self.state = State.ADDITIONAL_COMMENT
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

        self.state = State.FINALIZE_REPORT
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
            self.state = State.REPORT_COMPLETE
            return self.sendReport()
        else:
            return (
                """
                    Sorry, I didn't understand that.
                    Please say `done` when you're ready to send your report, or press the checkmark below.
                """,
                *reactDone(self)
            )


    async def edit_report(self, message, simulated=False):
        pass


    # Returns whether the Report has been completed (or cancelled).
    def report_complete(self):
        return self.state == State.REPORT_COMPLETE

    # Used as a helper function to define Reaction handlers
    # Returns a function that, when called, will simulate a reply from the user
    # Calling this function on its own will not simulate the reply;
    # Calling the function that this function returns will do it
    def simulateReply(self, reply):
        currentState = self.state
        async def sendReply(client, reaction, user, *args, **kwargs):
            if self.state is not currentState:
                return

            try:
                responses = await self.handle_message(reply, *args, simulated=True, **kwargs)
            except Exception as e:
                await reaction.message.channel.send("Uh oh! There was a problem in the code! Check the console for more information.")
                raise e

            lastMessage = None
            for response in responses:
                if isinstance(response, Reaction):
                    asyncio.create_task(response.registerMessage(lastMessage))
                elif isinstance(response, discord.Embed):
                    lastMessage = await reaction.message.channel.send(embed=response)
                else:
                    lastMessage = await reaction.message.channel.send(content=response)
        return sendReply

    def sendReport(self):
        reportDict = reportPreview(self).to_dict()

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
        reportEmbed.set_footer(text=f"This report was submitted on {time.strftime('%b %d, %Y at %I:%M %p %Z')}")

        for channel in self.client.mod_channels.values():
            asyncio.create_task(channel.send(embed=reportEmbed))

        return """
            Thank you for reporting! You will receive a message when someone on the moderation team has reviewed your report.
        """