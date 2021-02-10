# bot.py
import discord
from discord.ext import commands
import os
import json
import logging
import re
import requests
import asyncio
from textwrap import dedent
from report import Report
from reactions import Reaction, ReactionDelegator

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
    perspective_key = tokens['perspective']


class ModBot(discord.Client, ReactionDelegator):
    def __init__(self, key):
        intents = discord.Intents.default()
        intents.members = True
        super().__init__(command_prefix='.', intents=intents)
        self.group_num = None   
        self.mod_channels = {} # Map from guild to the mod channel id for that guild
        self.reports = {} # Map from user IDs to the state of their report
        self.perspective_key = key

    async def on_ready(self):
        print(f'{self.user.name} has connected to Discord! It is in these guilds:')
        for guild in self.guilds:
            print(f' - {guild.name}')
        print('Press Ctrl-C to quit.')

        # Parse the group number out of the bot's name
        match = re.search('[gG]roup (\d+) [bB]ot', self.user.name)
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
        # Ignore messages from us 
        if message.author.id == self.user.id:
            return
        
        # Check if this message was sent in a server ("guild") or if it's a DM
        if message.guild:
            await self.handle_channel_message(message)
        else:
            await self.handle_dm(message)

    async def handle_dm(self, message):
        content = message.content.strip()

        author_id = message.author.id
        responses = []

        # If the previous report was already completed, remove it from our map
        if author_id in self.reports and self.reports[author_id].report_complete():
            self.reports.pop(author_id)

        # Check if the user does not already have a report associated with them
        if author_id not in self.reports:
            # Handle a help message
            if content.lower() in Report.HELP_KEYWORDS:
                await message.channel.send(dedent("""
                    Use the `report` command to begin the reporting process.
                """))
                return

            # Tell the user how to start a new report
            if content.lower() not in Report.START_KEYWORDS:
                await message.channel.send(dedent("""
                    You do not have a report open; use the `report` command to begin the reporting process, or `help` for more help.
                """))
                return

            # Start a new Report
            self.reports[author_id] = Report(self, message.author)


        # Let the report class handle this message; forward all the messages it returns to us
        try:
            responses = await self.reports[author_id].handle_message(message)
        except Exception as e:
            await message.channel.send("Uh oh! There was a problem in the code! Check the console for more information.")
            raise e

        lastMessage = None
        for response in responses:
            if isinstance(response, Reaction):
                asyncio.create_task(response.registerMessage(lastMessage))
            elif isinstance(response, discord.Embed):
                lastMessage = await message.channel.send(embed=response)
            else:
                lastMessage = await message.channel.send(content=response)

    async def handle_channel_message(self, message):
        # Only handle messages sent in the "group-#" channel
        if not message.channel.name == f'group-{self.group_num}':
            return 
        
        # Forward the message to the mod channel
        mod_channel = self.mod_channels[message.guild.id]
        await mod_channel.send(f'Forwarded message:\n{message.author.name}: "{message.content}"')

        scores = self.eval_text(message)
        await mod_channel.send(self.code_format(json.dumps(scores, indent=2)))

    def eval_text(self, message):
        '''
        Given a message, forwards the message to Perspective and returns a dictionary of scores.
        '''
        PERSPECTIVE_URL = 'https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze'

        url = PERSPECTIVE_URL + '?key=' + self.perspective_key
        data_dict = {
            'comment': {'text': message.content},
            'languages': ['en'],
            'requestedAttributes': {
                'SEVERE_TOXICITY': {},
                'PROFANITY': {},
                'IDENTITY_ATTACK': {},
                'THREAT': {},
                'TOXICITY': {},
                'FLIRTATION': {}
            },
            'doNotStore': True
        }
        response = requests.post(url, data=json.dumps(data_dict))
        response_dict = response.json()

        scores = {}
        for attr in response_dict["attributeScores"]:
            scores[attr] = response_dict["attributeScores"][attr]["summaryScore"]["value"]

        return scores
    
    def code_format(self, text):
        return "```" + text + "```"
            
        
client = ModBot(perspective_key)
client.run(discord_token)