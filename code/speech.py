import os
os.environ = {} # Remove env variables to give os.system a semblance of security
import sys
import asyncio
import time
import inspect
from math import ceil
from random import choice

import utilities
import dynamo_helper
from discord import errors
from discord.ext import commands

## Config
CONFIG_OPTIONS = utilities.load_config()


class SpeechEntry:
    def __init__(self, requester, channel, player, file_path, callback=None):
        self.requester = requester
        self.channel = channel
        self.player = player
        self.file_path = file_path
        self.callback = callback


    def __str__(self):
        return "'{}' in '{}' wants '{}'".format(self.requester, self.channel, self.file_path)


class SpeechState:
    def __init__(self, bot, join_channel):
        self.bot = bot
        self.voice_client = None
        self.current_speech = None
        self.join_channel = join_channel
        self.next = asyncio.Event()
        self.skip_votes = set() # set of users that voted to skip
        self.speech_queue = asyncio.Queue()
        self.speech_player = self.bot.loop.create_task(self.speech_player())
        self.last_speech_time = self.get_current_time()

    ## Property(s)

    @property
    def player(self):
        return self.current_speech.player

    ## Methods

    ## Calculates the current UTC time in seconds
    def get_current_time(self):
        return int(time.time())


    ## Returns a list of members in the current voice channel
    async def get_members(self):
        return self.current_speech.channel.voice_members


    ## Returns a bool to determine if the bot is speaking in this state.
    def is_speaking(self):
        if(self.voice_client is None or self.current_speech is None):
            return False

        return not self.player.is_done()


    ## Skips the currently playing speech (magic happens in speech_player)
    async def skip_speech(self):
        self.skip_votes.clear()
        if(self.is_speaking()):
            self.player.stop()


    ## Triggers the next speech in speech_queue to be played
    def next_speech(self):
        self.bot.loop.call_soon_threadsafe(self.next.set)


    ## Speech player event loop task
    async def speech_player(self):
        while(True):
            self.next.clear()
            self.current_speech = await self.speech_queue.get()
            await self.join_channel(self.current_speech.channel)
            self.last_speech_time = self.get_current_time()
            self.current_speech.player.start()
            await self.next.wait()  # 'next' semaphore gets triggered after the ffmpeg_player finished playing the file

            ## Perform callback after the speech has finished (assuming it's defined)
            callback = self.current_speech.callback
            if(callback):
                if(asyncio.iscoroutinefunction(callback)):
                    await callback()
                else:
                    callback()


class Speech:
    ## Keys
    CLIPS_FOLDER_PATH = "clips_folder_path"
    SKIP_VOTES_KEY = "skip_votes"
    SKIP_PERCENTAGE_KEY = "skip_percentage"
    SPEECH_STATES_KEY = "speech_states"
    FFMPEG_PARAMETERS_KEY = "ffmpeg_parameters"
    FFMPEG_POST_PARAMETERS_KEY = "ffmpeg_post_parameters"
    CHANNEL_TIMEOUT_KEY = "channel_timeout"
    CHANNEL_TIMEOUT_CLIP_PATHS_KEY = "channel_timeout_clip_paths"


    def __init__(self, bot, **kwargs):
        self.bot = bot
        self.speech_states = {}
        self.clips_folder_path = CONFIG_OPTIONS.get(self.CLIPS_FOLDER_PATH, "clips")
        self.skip_votes = int(CONFIG_OPTIONS.get(self.SKIP_VOTES_KEY, 3))
        self.skip_percentage = int(CONFIG_OPTIONS.get(self.SKIP_PERCENTAGE_KEY, 33))
        self.ffmpeg_parameters = CONFIG_OPTIONS.get(self.FFMPEG_PARAMETERS_KEY, "")
        self.ffmpeg_post_parameters = CONFIG_OPTIONS.get(self.FFMPEG_POST_PARAMETERS_KEY, "")
        self.channel_timeout = int(CONFIG_OPTIONS.get(self.CHANNEL_TIMEOUT_KEY, 15 * 60))
        self.channel_timeout_clip_paths = CONFIG_OPTIONS.get(self.CHANNEL_TIMEOUT_CLIP_PATHS_KEY, [])
        self.dynamo_db = dynamo_helper.DynamoHelper()

    ## Methods

    ## Removes the players in all of the speech_states, and disconnects any voice_clients
    def __unload(self):
        for state in self.speech_states.values():
            try:
                state.speech_player.cancel()
                if(state.voice_client):
                    self.bot.loop.create_task(state.voice_client.disconnect())
                #state.speech_player.stop()
            except:
                pass


    ## Returns/creates a speech state in the speech_states dict with key of server.id
    def get_speech_state(self, server):
        state = self.speech_states.get(server.id)
        if(state is None):
            state = SpeechState(self.bot, self.join_channel)
            self.speech_states[server.id] = state

        return state


    ## Creates a voice_client in state.voice_client
    async def create_voice_client(self, channel):
        voice_client = await self.bot.join_voice_channel(channel)
        state = self.get_speech_state(channel.server)
        state.voice_client = voice_client


    ## Tries to get the bot to join a channel
    async def join_channel(self, channel):
        state = self.get_speech_state(channel.server)

        ## Check if we've already got a voice client
        if(state.voice_client):
            ## Check if bot is already in the desired channel
            if(state.voice_client.channel == channel):
                return True

            ## Otherwise, move it into the desired channel
            try:
                await state.voice_client.move_to(channel)
            except Exception as e:
                utilities.debug_print("Voice client exists", e, debug_level=2)
                return False
            else:
                return True

        ## Otherwise, create a new one
        try:
            await self.create_voice_client(channel)
        except (discord.ClientException, discord.InvalidArgument) as e:
            utilities.debug_print("Voice client doesn't exist", e, debug_level=2)
            return False
        else:
            return True


    ## Tries to get the bot to leave a state's channel
    async def leave_channel(self, channel):
        ## Todo: the channel and state manipulation for this method is no bueno. Move to kwargs or something
        state = self.get_speech_state(channel.server)

        if(state.voice_client):
            ## Disconnect and un-set the voice client
            await state.voice_client.disconnect()
            state.voice_client = None
            return True
        else:
            return False


    ## Tries to disonnect the bot from the given state's voice channel if it hasn't been used in a while.
    async def attempt_leave_channel(self, state):
        ## Handy closure to preserve leave_channel's argument
        async def leave_channel_closure():
            await self.leave_channel(state.voice_client.channel)

        ## Attempt to leave the state's channel
        await asyncio.sleep(self.channel_timeout)
        if(state.last_speech_time + self.channel_timeout <= state.get_current_time() and state.voice_client):
            utilities.debug_print("Leaving channel", debug_level=4)
            if(len(self.channel_timeout_clip_paths) > 0):
                ## Todo: play the chosen clip
                await self._play_clip_via_speech_state(state, os.path.sep.join([utilities.get_root_path(), choice(self.channel_timeout_clip_paths)]), leave_channel_closure)
            else:
                await leave_channel_closure()


    ## Checks if a given command fits into the back of a string (ex. '\say' matches 'say')
    def is_matching_command(self, string, command):
        to_check = string[len(command):]
        return (command == to_check)

    ## Commands

    ## Tries to summon the bot to a user's channel
    @commands.command(pass_context=True, no_pm=True)
    async def summon(self, ctx):
        """Summons the bot to join your voice channel."""

        ## Check that the requester is in a voice channel
        summoned_channel = ctx.message.author.voice_channel
        if(summoned_channel is None):
            await self.bot.say("{} isn't in a voice channel.".format(ctx.message.author))
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False
        else:
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        return await self.join_channel(summoned_channel)


    ## Initiate/Continue a vote to skip on the currently playing speech
    @commands.command(pass_context=True, no_pm=True)
    async def skip(self, ctx):
        """Vote to skip the current speech."""

        state = self.get_speech_state(ctx.message.server)
        if(not state.is_speaking()):
            await self.bot.say("I'm not speaking at the moment.")
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False
        else:
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        voter = ctx.message.author
        if(voter == state.current_speech.requester):
            await self.bot.say("<@{}> skipped their own speech.".format(voter.id))
            await state.skip_speech()
            return False
        elif(voter.id not in state.skip_votes):
            state.skip_votes.add(voter.id)

            ## Todo: filter total_votes by members actually in the channel
            total_votes = len(state.skip_votes)
            total_members = len(await state.get_members()) - 1  # Subtract one for the bot itself
            vote_percentage = ceil((total_votes / total_members) * 100)

            if(total_votes >= self.skip_votes or vote_percentage >= self.skip_percentage):
                await self.bot.say("Skip vote passed, skipping current speech.")
                await state.skip_speech()
                return True
            else:
                raw = "Skip vote added, currently at {}/{} or {}%/{}%"
                await self.bot.say(raw.format(total_votes, self.skip_votes, vote_percentage, self.skip_percentage))

        else:
            await self.bot.say("<@{}> has already voted!".format(voter.id))


    ## Interface for playing the clip for the invoker's channel
    async def play_clip(self, ctx, clip_path):
        """Plays the given clip aloud to your channel"""

        ## Check that the requester is in a voice channel
        voice_channel = ctx.message.author.voice_channel
        if (voice_channel is None):
            await self.bot.say("<@{}> isn't in a voice channel.".format(ctx.message.author.id))
            self.dynamo_db.put(dynamo_helper.DynamoItem(
                ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False

        ## Make sure clip_path points to an actual file in the clips folder
        if (not os.path.isfile(clip_path)):
            await self.bot.say("Sorry, <@{}>, your clip couldn't be played.".format(ctx.message.author.id))
            self.dynamo_db.put(dynamo_helper.DynamoItem(
                ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False

        state = self.get_speech_state(ctx.message.server)
        if(state.voice_client is None):
            ## Todo: Handle exception if unable to create a voice client
            await self.create_voice_client(voice_channel)

        ## Create a player for the clip
        player = state.voice_client.create_ffmpeg_player(
            clip_path,
            before_options=self.ffmpeg_parameters,
            options=self.ffmpeg_post_parameters,
            after=state.next_speech
        )

        ## Build a SpeechEntry and push it into the queue
        await state.speech_queue.put(SpeechEntry(ctx.message.author, voice_channel, player, clip_path))
        self.dynamo_db.put(dynamo_helper.DynamoItem(
            ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        ## Start a timeout to disconnect the bot if the bot hasn't spoken in a while
        await self.attempt_leave_channel(state)

        return True


    async def _play_clip_via_speech_state(self, speech_state, clip_path, callback=None):
        ## Make sure clip_path points to an actual file in the clips folder
        if (not os.path.isfile(clip_path)):
            utilities.debug_print("Unable to find clip at: {}, exiting...", debug_level=2)
            return False

        ## Build a player for the clip
        player = speech_state.voice_client.create_ffmpeg_player(
            clip_path,
            before_options = self.ffmpeg_parameters,
            options = self.ffmpeg_post_parameters,
            after = speech_state.next_speech
        )

        ## On successful player creation, build a SpeechEntry and push it into the queue
        await speech_state.speech_queue.put(SpeechEntry(None, speech_state.voice_client.channel, player, clip_path, callback))

        ## Start a timeout to disconnect the bot if the bot hasn't spoken in a while
        await self.attempt_leave_channel(speech_state)

        return True
