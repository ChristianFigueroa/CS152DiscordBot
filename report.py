from enum import Enum, auto
import discord
import re
import asyncio
from textwrap import dedent as _dedent
from reactions import Reaction

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
    HARRASS_ENTRY         = auto()
    HARRASS_ADD_COMMENT   = auto()
    BULLYING_ENTRY        = auto()
    HARMFUL_ENTRY         = auto()
    VIOLENCE_ENTRY        = auto()
    CSAM_ENTRY            = auto()
    REPORT_COMPLETE       = auto()
    FINALIZE_REPORT       = auto()
    EDIT_REPORT           = auto()

class AbuseType(Enum):
    SPAM      = "Misinformation or Spam"
    HATEFUL   = "Hateful Content"
    SEXUAL    = "Sexual Content"
    HARRASS   = "Harrassment"
    BULLLYING = "Bullying"
    HARMFUL   = "Hamrful/Dangerous Content"
    VIOLENCE  = "Promoting Violence or Terrorism"
    CSAM      = "Child Abuse"


# Used to generate help messages for each State
# Use as a decorator: @makeHelpMsg("Some help message here!") [rest of function after it]
def makeHelpMsg(*msgs):
    if len(msgs) == 1 and not isinstance(msgs[0], str):
        try:
            msgs = tuple(iter(msgs[0]))
        except:
            pass
    def wrapper(func):
        async def innerwrapper(self, message, *args, **kwargs):
            print(message, msgs)
            return msgs if message.lower() in Report.HELP_KEYWORDS else await func(self, message, *args, **kwargs)
        return innerwrapper
    return wrapper

def dedent(obj):
    return _dedent(obj) if isinstance(obj, str) else obj

emergencyWarning = discord.Embed(title="In an emergency, call 911.", description="We will review your report as soon as we can, but calling 911 or other local authorities is the fastest and most effective way to handle emergencies.", color=discord.Color.red())


class Report:
    START_KEYWORDS = ("report")
    CANCEL_KEYWORDS = ("cancel", "quit", "exit")
    HELP_KEYWORDS = ("help", "?")

    def __init__(self, client, author=None):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
        self.abuse_type = None
        self.personally_involved = None
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
        self.message = message
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
                  4. Harrassment
                  5. Bullying
                  6. Harmful/Dangerous Content
                  7. Promoting Violence or Terrorism
                  8. Child Abuse
                You can enter a keyword to choose one, or select a button below.
            """,
            Reaction("1️⃣", click_handler=self.simulateReply("1")),
            Reaction("2️⃣", click_handler=self.simulateReply("2")),
            Reaction("3️⃣", click_handler=self.simulateReply("3")),
            Reaction("4️⃣", click_handler=self.simulateReply("4")),
            Reaction("5️⃣", click_handler=self.simulateReply("5")),
            Reaction("6️⃣", click_handler=self.simulateReply("6")),
            Reaction("7️⃣", click_handler=self.simulateReply("7")),
            Reaction("8️⃣", click_handler=self.simulateReply("8"))
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
                Reaction("✅", click_handler=self.simulateReply("done"))
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
                Reaction("✅", click_handler=self.simulateReply("done"))
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
                Reaction("✅", click_handler=self.simulateReply("done"))
            )
        elif message == "4" or any(keyword in ("harrassment", "harrass", "harrassing") for keyword in keywords):
            self.state = State.HARRASS_ENTRY
            return (
                """
                    You selected: __4. Harrassment__
                """,
                """
                    Does the content target you specifically?
                """,
                Reaction("✅", click_handler=self.simulateReply("yes")),
                Reaction("🚫", click_handler=self.simulateReply("no"))
            )
        elif message == "5" or any(keyword in ("bullying", "bully", "bullies", "cyberbullying", "cyberbully", "cyberbullies") for keyword in keywords):
            self.state = State.BULLYING_ENTRY
            return (
                """
                    You selected: __5. Bullying__
                """,
                emergencyWarning
            )
        elif message == "5" or any(keyword in ("harmful", "dangerous", "harm", "danger", "self-harm") for keyword in keywords):
            self.state = State.HARMFUL_ENTRY
            return (
                """
                    You selected: __6. Harmful/Dangerous Content__
                """,
                emergencyWarning
            )
        elif message == "6" or any(keyword in ("violence", "violent", "terrorism", "terror", "terrorist", "promote", "incite") for keyword in keywords):
            self.state = State.VIOLENCE_ENTRY
            return (
                """
                    You selected: __7. Promoting Violence or Terrorism__
                """,
                emergencyWarning
            )
        elif message == "7" or any(keyword in ("child", "children", "kid", "kids", "minor", "minors", "abuse", "csam") for keyword in keywords):
            self.state = State.CSAM_ENTRY
            return (
                """
                    You selected: __8. Child Abuse__
                """,
                emergencyWarning
            )


        return """
            Sorry, I didn't understand your reply. Try different words, or click one of the buttons above.
        """


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def spam_entry(self, message, simulated=False):
        self.abuse_type = AbuseType.SPAM
        self.report_comment = None if message.lower() == "done" else message

        self.state = State.FINALIZE_REPORT
        return (
            """
                This is what your report looks like so far:
            """,
            self.previewReport(),
            """
                Are you ready to send it?
            """,
            Reaction("✅", click_handler=self.simulateReply("yes")),
            Reaction("🚫", click_handler=self.simulateReply("no"))
        )


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def hateful_entry(self, message, simulated=False):
        self.abuse_type = AbuseType.SEXUAL
        self.report_comment = None if message.lower() == "done" else message

        self.state = State.FINALIZE_REPORT
        return (
            """
                This is what your report looks like so far:
            """,
            self.previewReport(),
            """
                Are you ready to send it?
            """,
            Reaction("✅", click_handler=self.simulateReply("yes")),
            Reaction("🚫", click_handler=self.simulateReply("no"))
        )


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def sexual_entry(self, message, simulated=False):
        self.abuse_type = AbuseType.SEXUAL
        self.report_comment = None if message.lower() == "done" else message

        self.state = State.FINALIZE_REPORT
        return (
            """
                This is what your report looks like so far:
            """,
            self.previewReport(),
            """
                Are you ready to send it?
            """,
            Reaction("✅", click_handler=self.simulateReply("yes")),
            Reaction("🚫", click_handler=self.simulateReply("no"))
        )


    @makeHelpMsg("""
        Select whether the message you are reporting is harrassing you specifically.
        If it is affecting someone else you know, select no.
        You can type `yes` or `no`, or select one of the buttons above.
    """)
    async def harrass_entry(self, message, simulated=False):
        self.abuse_type = AbuseType.HARRASS
        if message.lower() in ("yes", "y", "yeah", "yup", "sure"):
            self.personally_involved = True
        elif message.lower() in ("no", "n", "nah", "naw", "nope"):
            self.personally_involved = False
        self.state = State.HARRASS_ADD_COMMENT
        return (
            """
                If you have any comments you want to add to your report, enter them now.
                Otherwise, you can push the checkmark below, or say `done`.
            """,
            Reaction("✅", click_handler=self.simulateReply("done"))
        )


    @makeHelpMsg("""
        Enter additional comments to submit alongside your report, or type `done` to skip this step.
    """)
    async def harrass_add_comment(self, message, simulated=False):
        self.report_comment = None if message.lower() == "done" else message

        self.state = State.FINALIZE_REPORT
        return (
            """
                This is what your report looks like so far:
            """,
            self.previewReport(),
            """
                Are you ready to send it?
            """,
            Reaction("✅", click_handler=self.simulateReply("yes")),
            Reaction("🚫", click_handler=self.simulateReply("no"))
        )


    async def bullying_entry(self, message, simulated=False):
        return "Not Implemented"


    async def harmful_entry(self, message, simulated=False):
        return "Not Implemented"


    async def violence_entry(self, message, simulated=False):
        return "Not Implemented"


    async def csam_entry(self, message, simulated=False):
        return "Not Implemented"


    @makeHelpMsg("""
        Decide whether you're ready to send your report by replying with `yes` or `no`, or clicking one of the buttons above.
    """)
    async def finalize_report(self, message, simulated=False):
        if message.lower() in ("yes", "y", "yeah", "yup", "sure"):
            return self.sendReport()
        elif message.lower() in ("no", "n", "nah", "naw", "nope"):
            self.state = State.EDIT_REPORT

            fields = ["Type", "Message"]
            if self.abuse_type == AbuseType.HARRASS:
                fields.append("Personally Involved")
            fields.append("Additional Comments")
            fields = "\n".join(f"  {i + 1}. {content}" for i, content in enumerate(fields))

            return (
                "What would you like to change?\n" + fields
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

    # Compiles the report's metadata into one single Embed.
    def previewReport(self):
        embed = discord.Embed(
            title=f"Report from {self.author.display_name}" if self.author else "Report",
            color=discord.Color.blurple()
        )
        embed.add_field(name="Type", value=self.abuse_type.value, inline=False)
        embed.add_field(name="Message", value=self.message.content, inline=False)
        if self.personally_involved is not None:
            embed.add_field(name="Personally Involved", value="Yes" if self.personally_involved else "No", inline=False)
        embed.add_field(name="Additional Comments", value="*[No additional comments]*" if self.report_comment is None else self.report_comment, inline=False)
        return embed

    def sendReport(self):
        ###### TODO: Send report to mod channel
        return """
            Thank you for reporting! You will receive a message when someone on the moderation team has reviewed your report.
        """