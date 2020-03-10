import os
os.environ = {} # Remove env variables to give os.system a semblance of security
import sys
import asyncio
import async_timeout
import time
import inspect
import logging
from math import ceil
from random import choice
from typing import Callable
from concurrent import futures

import utilities
import dynamo_helper
import exceptions

import discord
from discord import errors
from discord.ext import commands
from discord.member import Member

## Config
CONFIG_OPTIONS = utilities.load_config()

## Logging
logger = utilities.initialize_logging(logging.getLogger(__name__))

class AudioPlayRequest:
    '''
    Represents a user's request for the bot to play some audio.
    Instances of this class form the 'audio_play_queue' in a ServerStateManager instance.
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
        self.skipped = False


    def __str__(self):
        return "'{}' in '{}' wants '{}'".format(self.member.name, self.channel.name, self.file_path)


class ServerStateManager:
    '''
    Manages the state of the bot in a given server.
    This class helps to manage the bot, initiate audio play requests, and move between channels.
    '''

    def __init__(self, ctx, bot: commands.Bot, audio_player_cog):
        self.ctx = ctx
        self.bot = bot
        self.audio_player_cog = audio_player_cog
        self.active_play_request: AudioPlayRequest = None
        self.next = asyncio.Event() # flag for alerting the audio_player to play the next AudioPlayRequest
        self.skip_votes = set() # set of Members that voted to skip
        self.audio_play_queue = asyncio.Queue() # queue of AudioPlayRequest to play
        self.audio_player = self.bot.loop.create_task(self.audio_player_loop())

        ## Lazy config
        self.channel_timeout_seconds = int(CONFIG_OPTIONS.get('channel_timeout_seconds', 15 * 60))
        self.channel_timeout_clip_paths = CONFIG_OPTIONS.get('channel_timeout_clip_paths', [])

    ## Property(s)

    @property
    def audio(self) -> discord.FFmpegPCMAudio:
        return self.active_play_request.audio


    @property
    def channel(self) -> discord.VoiceChannel:
        return self.active_play_request.channel

    ## Methods

    async def get_members(self) -> set:
        '''Returns a set of members in the current voice channel'''

        ## todo: does this include bots?
        return self.active_play_request.channel.members


    def is_playing(self) -> bool:
        '''Returns a bool to determine if the bot is speaking in this state.'''

        if(self.ctx.voice_client is None):
            return False

        return self.ctx.voice_client.is_playing()


    async def add_play_request(self, play_request: AudioPlayRequest):
        '''Pushes the given play_request into the audio_play_queue'''

        await self.audio_play_queue.put(play_request)


    async def get_voice_client(self, channel: discord.VoiceChannel):
        '''Handles voice client management by connecting, and moving between voice channels'''

        ## Make sure the bot can actually connect to the requested VoiceChannel
        permissions = channel.guild.me.permissions_in(channel)
        if (not permissions.connect or not permissions.speak):
            raise exceptions.UnableToConnectToVoiceChannelException(
                "Unable to speak and/or connect to the channel",
                channel,
                can_speak=permissions.speak,
                can_connect=permissions.connect
            )

        if (self.ctx.voice_client is not None):
            ## Check to see if the bot is already in the correct channel
            if (self.ctx.voice_client.channel.id == channel.id):
                return self.ctx.voice_client
            else:
                return await self.ctx.voice_client.move_to(channel)
        else:
            ## NOTE: There's an issue where if you reset the app, while the bot is connected to a voice channel, upon the 
            ## bot reconnecting and joining the same voice channel, playing audio won't work.
            ## See: https://github.com/Rapptz/discord.py/issues/2284
            already_in_channel = next(filter(lambda member: member.id == self.bot.user.id, channel.members), None)
            if (already_in_channel):
                raise exceptions.AlreadyInVoiceChannelException(
                    "Old instance of bot already exists in the channel",
                    channel
                )

        return await channel.connect()


    def skip_audio(self):
        '''Skips the currently playing audio. If more audio is queued up, it will be played immediately.'''

        if(self.is_playing()):
            logger.debug("Skipping file at: {}, in channel: {}, in server: {}".format(
                self.active_play_request.file_path,
                self.ctx.voice_client.channel.name,
                self.ctx.guild.name
            ))

            self.ctx.voice_client.stop()

        self.active_play_request.skipped = True
        self.next.set()
        self.skip_votes.clear()


    async def disconnect(self, inactive=False):
        ## No voice client to disconnect!
        if (not self.ctx.voice_client):
            return

        logger.debug("Attempting to leave channel: {}, in server: {}, due to inactivity for past {} seconds".format(
                self.ctx.voice_client.channel.name,
                self.ctx.guild.name,
                self.channel_timeout_seconds
            ))   

        if (inactive and len(self.channel_timeout_clip_paths) > 0):
            ## Play a random sign off clip before disconnecting
            await self.audio_player_cog._play_audio_via_server_state(
                self,
                os.path.sep.join([utilities.get_root_path(), choice(self.channel_timeout_clip_paths)]),
                self.ctx.voice_client.disconnect
            )
            return

        ## Default to a regular voice client disconnect
        await self.ctx.voice_client.disconnect()


    async def audio_player_loop(self):
        '''
        Audio player event loop task.
        This event loop handles processing the play_queue by joining the requester's channel, playing the requested 
        audio, and handling successful skip requests
        '''

        while(True):
            try:
                self.next.clear()
                active_play_request = None

                try:
                    async with async_timeout.timeout(self.channel_timeout_seconds):
                        self.active_play_request = await self.audio_play_queue.get()
                        active_play_request = self.active_play_request
                except asyncio.TimeoutError:
                    if (self.ctx.voice_client and self.ctx.voice_client.is_connected()):
                        self.bot.loop.create_task(self.disconnect(inactive=True))
                    continue
                except asyncio.CancelledError:
                    logger.exception("CancelledError during audio_player_loop, ignoring and continuing loop.")
                    continue

                ## Join the requester's voice channel & play their clip (Or Handle the appropriate exception)
                voice_client = None
                try:
                    voice_client = await self.get_voice_client(self.active_play_request.channel)
                except futures.TimeoutError:
                    logger.error("Timed out trying to connect to the voice channel")

                    await self.ctx.send("Sorry <@{}>, I can't connect to that channel right now.".format(active_play_request.member.id))
                    continue

                except exceptions.UnableToConnectToVoiceChannelException as e:
                    logger.error("Unable to connect to voice channel")

                    required_permission_phrases = []
                    if (not e.can_connect):
                        required_permission_phrases.append("connect to that channel")
                    if (not e.can_speak):
                        required_permission_phrases.append("speak in that channel")

                    await self.ctx.send("Sorry <@{}>, I don't have permission to {}.".format(
                        self.ctx.message.author.id,
                        " or ".join(required_permission_phrases)
                    ))
                    continue

                except exceptions.AlreadyInVoiceChannelException as e:
                    logger.error("Unable to connect to voice channel, old instance of the bot already exists")

                    await self.ctx.send(
                        "Uh oh <@{}>, looks like I'm still in the channel! Wait until I disconnect before trying again."
                        .format(self.ctx.message.author.id)
                    )
                    continue

                if (voice_client.is_playing()):
                    voice_client.stop()

                def after_play_callback_builder():
                    ## Wrap this in a closure to keep it available even when it should be out of scope
                    current_active_play_request = active_play_request
                    
                    def after_play(_):
                        if (id(self.active_play_request) == id(current_active_play_request)):
                            self.next.set()

                            ## Perform callback after the audio has finished (assuming it's defined)
                            callback = current_active_play_request.callback
                            if(callback):
                                if(asyncio.iscoroutinefunction(callback)):
                                    self.bot.loop.create_task(callback())
                                else:
                                    callback()

                    return after_play

                logger.debug('Playing file at: {}, in channel: {}, in server: {}, for user: {}'.format(
                    self.active_play_request.file_path,
                    self.active_play_request.channel.name,
                    self.active_play_request.channel.guild.name,
                    self.active_play_request.member.name if self.active_play_request.member else None
                ))
                voice_client.play(self.active_play_request.audio, after=after_play_callback_builder())
                await self.next.wait()
            
            except Exception as e:
                logger.exception('Exception inside audio player event loop', exc_info=e)


class AudioPlayer(commands.Cog):
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
        """Vote to skip the current audio."""

        state = self.get_server_state(ctx)

        if(not state.is_playing()):
            await ctx.send("I'm not speaking at the moment.")
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False))
            return False
        else:
            self.dynamo_db.put(dynamo_helper.DynamoItem(ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        voter = ctx.message.author
        ## Todo: Add extra skip logic when sending preset phrases to someone else?
        if(voter == state.active_play_request.member):
            await ctx.send("<@{}> skipped their own audio.".format(voter.id))
            state.skip_audio()
            return False
        elif(voter.id not in state.skip_votes):
            state.skip_votes.add(voter.id)

            ## Todo: filter total_votes by members actually in the channel
            total_votes = len(state.skip_votes)
            total_members = len(await state.get_members()) - 1  # todo: filter out all bots
            vote_percentage = ceil((total_votes / total_members) * 100)

            if(total_votes >= self.skip_votes or vote_percentage >= self.skip_percentage):
                await ctx.send("Skip vote passed, skipping current audio.")
                state.skip_audio()
                return True
            else:
                raw = "Skip vote added, currently at {}/{} or {}%/{}%"
                await ctx.send(raw.format(total_votes, self.skip_votes, vote_percentage, self.skip_percentage))
        else:
            await ctx.send("<@{}> has already voted!".format(voter.id))


    ## Interface for playing the clip for the invoker's channel
    async def play_audio(self, ctx, clip_path: str, target_member = None):
        """Plays the given clip aloud to your channel"""

        ## Verify that the target/requester is in a channel
        if (not target_member or not isinstance(target_member, Member)):
            target_member = ctx.message.author

        voice_channel = None
        if (target_member.voice):   ## Handle users not in a voice channel
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

        ## Get/Build a state for this audio, build the player, and add it to the state
        state = self.get_server_state(ctx)
        player = self.build_player(clip_path)
        await state.add_play_request(AudioPlayRequest(ctx.message.author, voice_channel, player, clip_path))

        self.dynamo_db.put(dynamo_helper.DynamoItem(
            ctx, ctx.message.content, inspect.currentframe().f_code.co_name, True))

        return True


    async def _play_audio_via_server_state(self, server_state: ServerStateManager, clip_path: str, callback = None):
        '''Internal method for playing clips without a requester. Instead it'll play from the active voice_client.'''

        ## Make sure clip_path points to an actual file in the clips folder
        if (not os.path.isfile(clip_path)):
            logger.error("Unable to find clip at: {}".format(clip_path))
            return False

        ## Create a player for the clip
        player = self.build_player(clip_path)

        ## On successful player creation, build a AudioPlayRequest and push it into the queue
        play_request = AudioPlayRequest(None, server_state.ctx.voice_client.channel, player, clip_path, callback)
        await server_state.add_play_request(play_request)

        return True
