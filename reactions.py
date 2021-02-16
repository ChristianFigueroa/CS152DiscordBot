import asyncio

# Represents a reaction with an optional click handler
class Reaction():
	def __init__(self, reaction, click_handler=None, unclick_handler=None, toggle_handler=None, once_per_message=True):
		"""
			`reaction` is the reaction to show and is usually a plain unicode emoji like 1️⃣
			`click_handler` is a (list of) function(s) to be called when a reaction is added (i.e., clicked)
			`unclick_handler` is a (list of) function(s) to be called when a reaction is removed (i.e., clicked again after clicking)
			`toggle_handler` is a (list of) function(s) to be called when a reaction is either added or removed (i.e., toggled)
			`once_per_message` is a boolean indicating if the reactions should stop listening for clicks after any of the reactions on a message are clicked
		"""
		if not isinstance(reaction, str):
			raise TypeError("reaction must be a string")

		self.reaction = reaction

		click_handler = [] if click_handler is None else [click_handler] if callable(click_handler) else click_handler
		unclick_handler = [] if unclick_handler is None else [unclick_handler] if callable(unclick_handler) else unclick_handler
		toggle_handler = [] if toggle_handler is None else [toggle_handler] if callable(toggle_handler) else toggle_handler

		try:
			self.click_handlers = tuple(iter(click_handler))
			self.unclick_handlers = tuple(iter(unclick_handler))
			self.toggle_handlers = tuple(iter(toggle_handler))
		except:
			raise TypeError("handlers must be a callable or list of callables")

		self.once_per_message = once_per_message
		self._registeredMessages = []

	# Attaches the reaction to a specified message and adds it to _registeredMessages
	async def register_message(self, message):
		try:
			await message.add_reaction(self.reaction)
		except Exception as e:
			raise e
		registeredMessage = (message, self)
		self._registeredMessages.append(registeredMessage)
		_registeredMessages.append(registeredMessage)

	async def unregister_message(self, client, message):
		await message.remove_reaction(self.reaction, client.user)
		for regmsg in _registeredMessages:
			if regmsg[0] == message and regmsg[1] == self:
				_registeredMessages.remove(regmsg)


# A superclass for discord.Client to handle reactiosn automatically
class ReactionDelegator():
	async def on_reaction_add(discordClient, reaction, user):
		if user == discordClient.user:
			return
		removeAfter = []
		for regmsg in _registeredMessages:
			if regmsg[0] == reaction.message:
				if regmsg[1].reaction == reaction.emoji:
					for handler in regmsg[1].click_handlers:
						asyncio.create_task(handler(regmsg[1], discordClient, reaction, user)) if asyncio.iscoroutinefunction(handler) else handler(regmsg[1], discordClient, reaction, user)
					for handler in regmsg[1].toggle_handlers:
						asyncio.create_task(handler(regmsg[1], discordClient, reaction, user)) if asyncio.iscoroutinefunction(handler) else handler(regmsg[1], discordClient, reaction, user)
				if regmsg[1].once_per_message:
					removeAfter.append(regmsg)
		for regmsg in removeAfter:
			asyncio.create_task(regmsg[1].unregister_message(discordClient, regmsg[0]))


	async def on_reaction_remove(discordClient, reaction, user):
		if user == discordClient.user:
			return
		for regmsg in _registeredMessages:
			if regmsg[0] == reaction.message and regmsg[1].reaction == reaction.emoji:
				for handler in regmsg[1].unclick_handlers:
					asyncio.create_task(handler(regmsg[1], discordClient, reaction, user)) if asyncio.iscoroutinefunction(handler) else handler(regmsg[1], discordClient, reaction, user)
				for handler in regmsg[1].toggle_handlers:
					asyncio.create_task(handler(regmsg[1], discordClient, reaction, user)) if asyncio.iscoroutinefunction(handler) else handler(regmsg[1], discordClient, reaction, user)

_registeredMessages = []