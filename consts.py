from enum import Enum, auto

HELP_KEYWORDS = ("help", "?")
CANCEL_KEYWORDS = ("cancel", "quit", "exit")
START_KEYWORDS = ("report",)
YES_KEYWORDS = ("yes", "y", "yeah", "yup", "sure")
NO_KEYWORDS = ("no", "n", "nah", "naw", "nope")

class AbuseType(Enum):
    SPAM      = "Misinformation or Spam"
    HATEFUL   = "Hateful Content"
    SEXUAL    = "Sexual Content"
    HARASS    = "Harassment"
    BULLYING  = "Bullying"
    HARMFUL   = "Harmful/Dangerous Content"
    VIOLENCE  = "Promoting Violence or Terrorism"
    CSAM      = "Child Abuse"

class ReportStatus(Enum):
    NEW      = auto()
    PENDING  = auto()
    RESOLVED = auto()