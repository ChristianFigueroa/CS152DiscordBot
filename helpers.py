from textwrap import dedent as _dedent
import discord
from reactions import Reaction
import report as Report
import difflib
import re

# A file for helper functions and data

HELP_KEYWORDS = ("help", "?")
CANCEL_KEYWORDS = ("cancel", "quit", "exit")
START_KEYWORDS = ("report")

YES_KEYWORDS = ("yes", "y", "yeah", "yup", "sure")
NO_KEYWORDS = ("no", "n", "nah", "naw", "nope")

# Dedents a string and leaves non-strings alone
def dedent(obj):
    return _dedent(obj) if isinstance(obj, str) else obj

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
            return msgs if message.lower() in HELP_KEYWORDS else await func(self, message, *args, **kwargs)
        return innerwrapper
    return wrapper

# Compiles the report's data into one single Embed.
def reportPreview(report):
    embed = discord.Embed(
        color=discord.Color.blurple()
    )
    if report.author:
        embed.set_author(name=report.author.display_name, icon_url=report.author.avatar_url)
    for key, val in report.report_fields.items():
        val = "*[Empty]*" if val is None or val == "" else \
            val.content if isinstance(val, discord.Message) else \
            ("Yes" if val else "No") if isinstance(val, bool) else \
            val.value if isinstance(val, Report.AbuseType) else \
            f"{val.mention} – **{val.display_name}**#{val.discriminator}" if isinstance(val, discord.Member) else \
            val
        embed.add_field(name=key, value=val, inline=False)
    return embed

# Shortcut for generating a `yes` and `no` Reaction
def reactYesNo(report):
    return (
        Reaction("✅", click_handler=report.simulateReply("yes")),
        Reaction("🚫", click_handler=report.simulateReply("no"))
    )

# Shortcut for generating a `done` Reaction
def reactDone(report):
    return (
        Reaction("✅", click_handler=report.simulateReply("done")),
    )

# Shortcut for generating a numerical list of reactions (from 1 to 10 choices)
def reactNumerical(report, choices):
    if isinstance(choices, int):
        choices = range(1, choices + 1)
    choices = tuple(choices)
    if len(choices) > 10:
        raise ValueError("Too many choices; only ten choices allowed.")
    return tuple(Reaction(("1️⃣","2️⃣","3️⃣","4️⃣","5️⃣","6️⃣","7️⃣","8️⃣","9️⃣","🔟")[i], click_handler=report.simulateReply(str(choices[i]))) for i in range(len(choices)))

def findUsers(guild, name):
    members = guild.members

    # Parse out a discriminator if the name includes one
    discrim = re.search(r"#\d+$", name)
    if discrim is not None:
        discrim = str(int(discrim.group(0)[1:]))
        name = name[:-len(discrim) - 1]
        # Filter out users with only that discriminator
        
        members = tuple(filter(lambda member: member.discriminator == discrim, members))

    matches = set(filter(lambda member: member.name.lower() == name.lower(), members))
    matches.update(filter(lambda member: member.display_name.lower() == name.lower(), members))

    return tuple(matches)

# Combines a list of strings into one string to be used as one comment
def oneComment(*msgs):
    return "\n".join(re.sub("^\n|\n$", "", dedent(msg)) for msg in msgs)