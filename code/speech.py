import os
os.environ = {} # Remove env variables to give os.system a semblance of security
import sys
import asyncio
import time
import inspect
import logging
from math import ceil
from random import choice
from typing import Callable

import utilities
import dynamo_helper

import discord
from discord import errors
from discord.ext import commands
from discord.member import Member

## Config
CONFIG_OPTIONS = utilities.load_config()

## Logging
logger = logging.getLogger(__name__)

class SpeechRequest:
    '''
    Represents a user's request for the bot to speak.
    Instances of this class form the 'speech_queue' in a ServerStateManager instance.
    '''

    def __init__(
        self,
        member: discord.Member,
        channel: discord.VoiceChannel,
        audio: discord.FFmpegPCMAudio,
        file_path: str,
        callback: Callable = None
    ):
        self.member = member
        self.channel = channel
        self.audio = audio
        self.file_path = file_path
        self.callback = callback


    def __str__(self):
        return "'{}' in '{}' wants '{}'".format(self.member.name, self.channel.name, self.file_path)


class ServerStateManager:
    '''
    Manages the state of the bot in a given server.
    This class helps to manage the bot, initiate speech, and move between channels.
    '''

    def __init__(self, ctx, bot: commands.Bot, speech_cog):
        self.ctx = ctx
        self.bot = bot
        self.speech_cog = speech_cog
        self.active_speech_request: SpeechRequest = None
        self.next = asyncio.Event()
        self.skip_votes = set() # set of users that voted to skip
        self.speech_queue = asyncio.Queue()
        self.speech_player = self.bot.loop.create_task(self.speech_player_loop())
        self.last_speech_time = int(time.time())

        ## Lazy config
        self.channel_timeout_seconds = int(CONFIG_OPTIONS.get('channel_timeout_seconds', 15 * 60))
        self.channel_timeout_clip_paths = CONFIG_OPTIONS.get('channel_timeout_clip_paths', [])

    ## Property(s)

    @property
    def audio(self) -> discord.FFmpegPCMAudio:
        return self.active_speech_request.audio


    @property
    def channel(self) -> discord.VoiceChannel:
        return self.active_speech_request.channel

    ## Methods

    async def get_members(self) -> set:
        '''Returns a set of members in the current voice channel'''

        ## todo: does this include bots?
        return self.active_speech_request.channel.members


    def is_playing(self) -> bool:
        '''Returns a bool to determine if the bot is speaking in this state.'''

        if(self.ctx.voice_client is None or self.active_speech_request is None):
            return False

        return self.ctx.voice_client.is_playing()


    async def add_speech_request(self, speech_request: SpeechRequest):
        '''Pushes the given speech_request into the speech_queue'''

        await self.speech_queue.put(speech_request)


    async def get_voice_client(self, channel: discord.VoiceChannel):
        '''Handles voice client management by connecting, and moving between voice channels'''

        ## NOTE: There's an issue where if you reset the app, while the bot is connected to a voice channel, upon the 
        ## bot reconnecting and joining the same voice channel, playing audio won't work.
        ## See: https://github.com/Rapptz/discord.py/issues/2284

        if (self.ctx.voice_client is not None):
            ## Check to see if the bot is already in the correct channel
            if (self.ctx.voice_client.channel.id == channel.id):
                return self.ctx.voice_client
            else:
                return await self.ctx.voice_client.move_to(channel)

        return await channel.connect()


    async def skip_speech(self):
        '''Skips the currently playing speech. If more speech is queued up, it will be played immediately.'''

        self.skip_votes.clear()
        if(self.is_playing()):
            self.ctx.voice_client.stop()

        self.bot.loop.call_soon_threadsafe(self.next.set)


    async def disconnect_if_inactive(self):
        '''Tries to disonnect the bot from this state's voice channel if it hasn't been used in a while'''

        self.last_speech_time = int(time.time())

        ## Sleep for the desired timeout duration. If no other speech happens in this period, the bot will disconnect
        await asyncio.sleep(self.channel_timeout_seconds)

        if(self.ctx.voice_client is not None and self.last_speech_time + self.channel_timeout_seconds <= int(time.time())):
            logger.debug("Attempting to leave channel {}, clip_paths: {}".format(self.ctx.voice_client.channel.id, len(self.channel_timeout_clip_paths)))   

            if (len(self.channel_timeout_clip_paths) > 0):
                ## Play a random sign off clip before disconnecting
                await self.speech_cog._play_clip_via_speech_state(
                    self,
                    os.path.sep.join([utilities.get_root_path(), choice(self.channel_timeout_clip_paths)]),
                    self.ctx.voice_client.disconnect
                )
            else:
                await self.ctx.voice_client.disconnect()


    async def speech_player_loop(self):
        '''
        Speech player event loop task.
        This event loop handles processing the speech queue by joining the requester's channel, playing the requested 
        speech, and handling successful skip requests
        '''

        ## Overly commented because I'm dumb and this helps me explain it to myself
        while(True):
            try:
                ## Make sure the semaphor hasn't been set
                self.next.clear()

                ## Pop the oldest speech request off the queue (or wait until the queue is populated if empty)
                self.active_speech_request = await self.speech_queue.get()

                ## Join the requester's voice channel & start playing!
                voice_client = await self.get_voice_client(self.active_speech_request.channel)
                voice_client.play(self.active_speech_request.audio, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
                
                ## Wait for voice client to finish playing speech request audio
                await self.next.wait()

                ## Perform callback after the speech has finished (assuming it's defined)
                callback = self.active_speech_request.callback
                if(callback):
                    if(asyncio.iscoroutinefunction(callback)):
                        await callback()
                    else:
                        callback()

                ## Clear the active speech, so it doesn't persist as 'active' after the speech_queue has emptied
                self.active_speech_request = None
            except Exception as e:
                logger.exception("Exception inside speech player event loop", exc_info=e)


class Speech(commands.Cog):
    ## Keys
    CLIPS_FOLDER_PATH = "clips_folder_path"
    SKIP_VOTES_KEY = "skip_votes"
    SKIP_PERCENTAGE_KEY = "skip_percentage"
    FFMPEG_PARAMETERS_KEY = "ffmpeg_parameters"
    FFMPEG_POST_PARAMETERS_KEY = "ffmpeg_post_parameters"
    CHANNEL_TIMEOUT_KEY = "channel_timeout_seconds"
    CHANNEL_TIMEOUT_CLIP_PATHS_KEY = "channel_timeout_clip_paths"


    def __init__(self, bot: commands.Bot, **kwargs):
        self.bot = bot
        self.server_states = {}
        self.clips_folder_path = CONFIG_OPTIONS.get(self.CLIPS_FOLDER_PATH, "clips")
        self.skip_votes = int(CONFIG_OPTIONS.get(self.SKIP_VOTES_KEY, 3))
        self.skip_percentage = int(CONFIG_OPTIONS.get(self.SKIP_PERCENTAGE_KEY, 33))
        self.ffmpeg_parameters = CONFIG_OPTIONS.get(self.FFMPEG_PARAMETERS_KEY, "")
        self.ffmpeg_post_parameters = CONFIG_OPTIONS.get(self.FFMPEG_POST_PARAMETERS_KEY, "")
        self.dynamo_db = dynamo_helper.DynamoHelper()

    ## Methods

    def get_server_state(self, ctx) -> ServerStateManager:
        '''Retrieves the server state for the provided server_id, or creates a new one if no others exist'''

        server_id = ctx.message.guild.id
        server_state = self.server_states.get(server_id, None)

        if (server_state is None):
            server_state = ServerStateManager(ctx, self.bot, self)
            self.server_states[server_id] = server_state

        return server_state


    def is_matching_command(self, string, command) -> bool:
        '''Checks if a given command fits into the back of a string (ex. '\say' matches 'say')'''

        to_check = string[len(command):]
        return (command == to_check)


    def build_player(self, file_path) -> discord.FFmpegPCMAudio:
        '''Builds an audio player for playing the file located at 'file_path'.'''

        return discord.FFmpegPCMAudio(
            file_path,
            before_options=self.ffmpeg_parameters,
            options=self.ffmpeg_post_parameters
        )

    ## Commands

    @commands.command(no_pm=True)
    async def skip(self, ctx, **kwargs):
        """Vote to skip the current speech."""

        state = self.get_server_state(ctx)

        if(not state.is_playing()):
            await ctx.send("I'm not speaking at the moment.")
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False
        else:
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        voter = ctx.message.author
        ## Todo: Add extra skip logic when sending preset phrases to someone else?
        if(voter == state.active_speech_request.member):
            await ctx.send("<@{}> skipped their own speech.".format(voter.id))
            await state.skip_speech()
            return False
        elif(voter.id not in state.skip_votes):
            state.skip_votes.add(voter.id)

            ## Todo: filter total_votes by members actually in the channel
            total_votes = len(state.skip_votes)
            total_members = len(await state.get_members()) - 1  # todo: filter out all bots
            vote_percentage = ceil((total_votes / total_members) * 100)

            if(total_votes >= self.skip_votes or vote_percentage >= self.skip_percentage):
                await ctx.send("Skip vote passed, skipping current speech.")
                await state.skip_speech()
                return True
            else:
                raw = "Skip vote added, currently at {}/{} or {}%/{}%"
                await ctx.send(raw.format(total_votes, self.skip_votes, vote_percentage, self.skip_percentage))
        else:
            await ctx.send("<@{}> has already voted!".format(voter.id))


    ## Interface for playing the clip for the invoker's channel
    async def play_clip(self, ctx, clip_path: str, target_member = None):
        """Plays the given clip aloud to your channel"""

        ## Verify that the target/requester is in a channel
        if (not target_member or not isinstance(target_member, Member)):
            target_member = ctx.message.author

        voice_channel = target_member.voice.channel
        if(voice_channel is None):
            await ctx.send("<@{}> isn't in a voice channel.".format(target_member.id))
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False

        ## Make sure clip_path points to an actual file in the clips folder
        if (not os.path.isfile(clip_path)):
            await ctx.send("Sorry, <@{}>, your clip couldn't be played.".format(ctx.message.author.id))
            self.dynamo_db.put(dynamo_helper.DynamoItem(
                ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False

        ## Get/Build a state for this speech, build the player, and add it to the state
        state = self.get_server_state(ctx)
        player = self.build_player(clip_path)
        await state.add_speech_request(SpeechRequest(ctx.message.author, voice_channel, player, clip_path))

        self.dynamo_db.put(dynamo_helper.DynamoItem(
            ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        ## Attempt to disconnect if the bot is inactive for too long after the command finishes
        ## todo: Move disconnect logic into speech queue event loop (or rather make separate timeout event loop?)
        await state.disconnect_if_inactive()

        return True


    async def _play_clip_via_speech_state(self, server_state: ServerStateManager, clip_path: str, callback = None):
        '''Internal method for playing clips without a requester. Instead it'll play from the active voice_client.'''

        ## Make sure clip_path points to an actual file in the clips folder
        if (not os.path.isfile(clip_path)):
            logger.error("Unable to find clip at: {}".format(clip_path))
            return False

        ## Create a player for the clip
        player = self.build_player(clip_path)

        ## On successful player creation, build a SpeechRequest and push it into the queue
        speech_request = SpeechRequest(None, server_state.ctx.voice_client.channel, player, clip_path, callback)
        await server_state.add_speech_request(speech_request)

        return True
