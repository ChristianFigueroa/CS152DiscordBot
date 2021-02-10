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
    BULLYING_ENTRY        = auto()
    HARMFUL_ENTRY         = auto()
    VIOLENCE_ENTRY        = auto()
    CSAM_ENTRY            = auto()
    REPORT_COMPLETE       = auto()

# Used to generate help messages for each State
# Use as a decorator: @makeHelpMsg("Some help message here!") [rest of function after it]
def makeHelpMsg(*msgs):
    if len(msgs) == 1:
        try:
            iter(msgs)
            msgs = msgs[0]
        except:
            pass
    def wrapper(func):
        async def innerwrapper(self, message, *args, **kwargs):
            return msgs if message.content.strip().lower() in Report.HELP_KEYWORDS else await func(self, message, *args, **kwargs)
        return innerwrapper
    return wrapper

def dedent(obj):
    return _dedent(obj) if isinstance(obj, str) else obj

emergencyWarning = discord.Embed(title="In an emergency, call 911.", description="We will review your report as soon as we can, but calling 911 or other local authorities is the fastest way and most effective way to handle emergencies.", color=discord.Color.red())


class Report:
    START_KEYWORDS = ("report")
    CANCEL_KEYWORDS = ("cancel", "quit", "exit")
    HELP_KEYWORDS = ("help", "?")

    def __init__(self, client):
        self.state = State.REPORT_START
        self.client = client
        self.message = None
    
    async def handle_message(self, message, *args, **kwargs):
        '''
        This function makes up the meat of the user-side reporting flow. It defines how we transition between states and what 
        prompts to offer at each of those states. You're welcome to change anything you want; this skeleton is just here to
        get you started and give you a model for working with Discord. 
        '''

        content = message.content.strip()

        # If they say "cancel", cancel the report
        if content.lower() in self.CANCEL_KEYWORDS:
            self.state = State.REPORT_COMPLETE
            return ["Report cancelled."]


        # Branch to the appropriate function depending on the state
        cb = getattr(self, self.state.name.lower(), None)
        if cb == None:
            raise Exception("The bot is in state {}, but no method with the name `{}` is given.".format(self.state, self.state.name.lower()))
        ret = await cb(message, *args, **kwargs) or []
        return (dedent(ret),) if isinstance(ret, str) or isinstance(ret, discord.Embed) else tuple(dedent(msg) for msg in ret) or ()


    ##################
    #                #
    #  Begin States  #
    #                #
    ##################


    async def report_start(self, message):
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
    async def awaiting_message_link(self, message):
        content = message.content.strip()
        # Parse out the three ID strings from the message link
        m = re.search(r"/(\d+|@me)/(\d+)/(\d+)", message.content)

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
                **1**. Misinformation or Spam
                **2**. Hateful Content
                **3**. Sexual Content
                **4**. Bullying
                **5**. Harmful/Dangerous Content
                **6**. Promoting Violence or Terrorism
                **7**. Child Abuse
                You can enter a keyword to choose one, or select a button below.
            """,
            Reaction("1️⃣", click_handler=self.transToState(State.SPAM_ENTRY, messages="""
                You selected: 1️⃣  Misinformation or Spam
            """)),
            Reaction("2️⃣", click_handler=self.transToState(State.HATEFUL_ENTRY, messages="""
                You selected: 2️⃣  Hateful Content
            """)),
            Reaction("3️⃣", click_handler=self.transToState(State.SEXUAL_ENTRY, messages="""
                You selected: 3️⃣  Sexual Content
            """)),
            Reaction("4️⃣", click_handler=self.transToState(State.BULLYING_ENTRY, messages=(
                """
                    You selected: 4️⃣  Bullying
                """,
                emergencyWarning
            ))),
            Reaction("5️⃣", click_handler=self.transToState(State.HARMFUL_ENTRY, messages=(
                """
                    You selected: 5️⃣  Harmful/Dangerous Content
                """,
                emergencyWarning
            ))),
            Reaction("6️⃣", click_handler=self.transToState(State.VIOLENCE_ENTRY, messages=(
                """
                    You selected: 6️⃣  Promoting Violence or Terrorism
                """,
                emergencyWarning
                )
            )),
            Reaction("7️⃣", click_handler=self.transToState(State.CSAM_ENTRY, messages=(
                """
                    You selected: 7️⃣  Child Abuse
                """,
                emergencyWarning
                )
            ))
        )


    @makeHelpMsg("""
        Enter a keyword from one of the abuse types above, or select one of the buttons to choose it.
    """)
    async def awaiting_abuse_type(self, message):
        content = message.content.strip()

        keywords = content.lower().split()

        # Check for either numbers of certain keywords
        if content == "1" or any(keyword in ("misinformation", "disinformation", "spam", "misinfo", "disinfo", "information", "info") for keyword in keywords):
            self.state = State.SPAM_ENTRY
            return """
                You selected: **1**. Misinformation or Spam
            """
        elif content == "2" or any(keyword in ("hateful", "hate", "hatred", "racism", "racist", "sexist", "sexism") for keyword in keywords):
            self.state = State.HATEFUL_ENTRY
            return """
                You selected: **2**. Hateful Content
            """
        elif content == "3" or any(keyword in ("sexual", "sex", "nude", "nudity", "naked") for keyword in keywords):
            self.state = State.SEXUAL_ENTRY
            return """
                You selected: **3**. Sexual Content
            """
        elif content == "4" or any(keyword in ("bullying", "bully", "bullies", "cyberbullying", "cyberbully", "cyberbullies") for keyword in keywords):
            self.state = State.BULLYING_ENTRY
            return (
                """
                    You selected: **4**. Bullying
                """,
                emergencyWarning
            )
        elif content == "5" or any(keyword in ("harmful", "dangerous", "harm", "danger", "self-harm") for keyword in keywords):
            self.state = State.HARMFUL_ENTRY
            return (
                """
                    You selected: **5**. Harmful/Dangerous Content
                """,
                emergencyWarning
            )
        elif content == "6" or any(keyword in ("violence", "violent", "terrorism", "terror", "terrorist", "promote", "incite") for keyword in keywords):
            self.state = State.VIOLENCE_ENTRY
            return (
                """
                    You selected: **6**. Promoting Violence or Terrorism
                """,
                emergencyWarning
            )
        elif content == "7" or any(keyword in ("child", "children", "kid", "kids", "minor", "minors", "abuse", "csam") for keyword in keywords):
            self.state = State.CSAM_ENTRY
            return (
                """
                    You selected: **7**. Child Abuse
                """,
                emergencyWarning
            )


        return """
            Sorry, I didn't understand your reply. Try different words, or click one of the buttons above.
        """


    async def spam_entry(self, message):
        return "Not Implemented"

    async def hateful_entry(self, message):
        return "Not Implemented"

    async def sexual_entry(self, message):
        return "Not Implemented"

    async def bullying_entry(self, message):
        return "Not Implemented"

    async def harmful_entry(self, message):
        return "Not Implemented"

    async def violence_entry(self, message):
        return "Not Implemented"

    async def csam_entry(self, message):
        return "Not Implemented"


    # Returns whether the Report has been completed (or cancelled).
    def report_complete(self):
        return self.state == State.REPORT_COMPLETE

    # Helper function for creating a function that will transition the bot to another state
    # This can be used in Reaction handlers to make the bot switch states when a reaction is clicked.
    def transToState(self, state, messages=()):
        currentState = self.state
        async def toState(client, reaction, user, *args, **kwargs):
            # Prevent transition to another state when we have already switched states.
            if self.state is not currentState:
                return

            if messages:
                msgs = (dedent(messages),) if isinstance(messages, str) or isinstance(messages, discord.Embed) else tuple(dedent(msg) for msg in messages) or ()
                lastMessage = None
                for msg in msgs:
                    if isinstance(msg, Reaction):
                        asyncio.create_task(msg.registerMessage(lastMessage))
                    elif isinstance(msg, discord.Embed):
                        lastMessage = await reaction.message.channel.send(embed=msg)
                    else:
                        lastMessage = await reaction.message.channel.send(content=msg)

            # Transition to the new state
            self.state = state
        return toState