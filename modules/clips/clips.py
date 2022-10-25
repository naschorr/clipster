import logging
import random
from pathlib import Path

from common.audio_player import AudioPlayer
from common.command_management.invoked_command import InvokedCommand
from common.command_management.invoked_command_handler import InvokedCommandHandler
from common.command_management.command_reconstructor import CommandReconstructor
from common.configuration import Configuration
from common.database.database_manager import DatabaseManager
from common.logging import Logging
from common.string_similarity import StringSimilarity
from common.module.discoverable_module import DiscoverableCog
from common.module.module_initialization_container import ModuleInitializationContainer
from modules.clips.clip_file_manager import ClipFileManager
from modules.clips.models.clip_group import ClipGroup
from modules.clips.models.clip import Clip

import discord
from discord import Interaction, Member
from discord.app_commands import autocomplete, Choice, describe
from discord.ext.commands import Context, Bot

## Config & logging
CONFIG_OPTIONS = Configuration.load_config(Path(__file__).parent)
LOGGER = Logging.initialize_logging(logging.getLogger(__name__))


class Clips(DiscoverableCog):
    CLIPS_NAME = "clips"
    CLIP_COMMAND_NAME = "clip"
    RANDOM_COMMAND_NAME = "random"
    FIND_COMMAND_NAME = "find"

    def __init__(self, bot: Bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)

        self.bot = bot

        self.audio_player_cog: AudioPlayer = kwargs.get('dependencies', {}).get('AudioPlayer')
        assert (self.audio_player_cog is not None)
        self.admin_cog = kwargs.get('dependencies', {}).get('AdminCog')
        assert (self.admin_cog is not None)
        self.invoked_command_handler: InvokedCommandHandler = kwargs.get('dependencies', {}).get('InvokedCommandHandler')
        assert(self.invoked_command_handler is not None)
        self.database_manager: DatabaseManager = kwargs.get('dependencies', {}).get('DatabaseManager')
        assert (self.database_manager is not None)
        self.command_reconstructor: CommandReconstructor = kwargs.get('dependencies', {}).get('CommandReconstructor')
        assert (self.command_reconstructor is not None)

        self.clip_file_manager = ClipFileManager()

        self.clips: dict[str, Clip] = {}
        self.clip_groups: dict[str, ClipGroup] = {}
        self.find_command_minimum_similarity = float(CONFIG_OPTIONS.get('find_command_minimum_similarity', 0.5))
        self.clips_folder_path = self.clip_file_manager.clips_folder_path
        self.channel_timeout_clip_paths = self.gather_channel_timeout_clip_paths()
        self.audio_player_cog.channel_timeout_handler = self.play_random_channel_timeout_clip

        ## Load and add the clips
        self.init_clips()
        self.add_clip_commands()

        self.successful = True

        ## This decorator needs to reference the injected dependency, thus we're declaring the command here.
        @self.admin_cog.admin.command(no_pm=True)
        async def reload_clips(ctx: Context):
            """Reloads the bot's list of clips"""

            await self.database_manager.store(ctx)

            count = self.reload_clips()

            loaded_clips_string = "Loaded {} clip{}.".format(count, "s" if count != 1 else "")
            await ctx.reply(loaded_clips_string)

            return (count >= 0)

    ## Lifecycle-ish

    def cog_unload(self):
        """Removes all existing clips when the cog is unloaded"""

        self.remove_clips()
        self.remove_clip_commands()


    def reload_clips(self):
        """Unloads all clip commands from the bot, then reloads all of the clips, and reapplies them to the bot"""

        self.remove_clips()
        self.remove_clip_commands()

        loaded_clips = self.init_clips()
        self.add_clip_commands()

        return loaded_clips

    ## Methods

    def remove_clips(self):
        """Unloads the preset clips from the bot's command list."""

        self.clips = {}
        self.clip_groups = {}


    def add_clip_commands(self):
        """Adds the clip commands to the bot"""

        ## Don't register clip commands if no clips have been loaded!
        if (self.clips):
            ## Add the random command
            self.add_command(discord.app_commands.Command(
                name=Clips.RANDOM_COMMAND_NAME,
                description=self.random_command.__doc__,
                callback=self.random_command
            ))

            # Add the find command
            self.add_command(discord.app_commands.Command(
                name=Clips.FIND_COMMAND_NAME,
                description=self.find_command.__doc__,
                callback=self.find_command
            ))

            ## Add the clip command
            ## Wrap the clip command to have access to self in the autocomplete decorator. Unfortunately the parameter
            ## description decorators must also be moved up here.
            ## todo: Investigate a workaround that's less ugly?
            @autocomplete(name=self._clip_name_command_autocomplete)
            @describe(name="The name of the clip to speak")
            @describe(user="The user to speak the clip to")
            async def clip_command_wrapper(interaction: Interaction, name: str, user: discord.Member = None):
                await self.clip_command(interaction, name, user)

            self.add_command(discord.app_commands.Command(
                name=Clips.CLIP_COMMAND_NAME,
                description=self.clip_command.__doc__,
                callback=clip_command_wrapper,
                extras={"cog": self}
            ))


    def remove_clip_commands(self):
        self.bot.tree.remove_command(Clips.RANDOM_COMMAND_NAME)
        self.bot.tree.remove_command(Clips.CLIP_COMMAND_NAME)
        self.bot.tree.remove_command(Clips.FIND_COMMAND_NAME)


    def gather_channel_timeout_clip_paths(self) -> list[Path]:
        channel_timeout_clip_paths = list(CONFIG_OPTIONS.get('channel_timeout_clip_paths', []))

        output = []
        relative_clip_path: str
        for relative_clip_path in channel_timeout_clip_paths:
            path = self.clips_folder_path / Path(relative_clip_path)

            if (path.exists()):
                output.append(path)

        return output


    async def play_random_channel_timeout_clip(self, server_state, callback):
        '''Channel timeout logic, picks an appropriate sign-off message and plays it'''

        try:
            if (len(self.channel_timeout_clip_paths) > 0):
                clip_path = random.choice(self.channel_timeout_clip_paths)

                await self.audio_player_cog._play_audio_via_server_state(server_state, clip_path, callback)
        except Exception as e:
            LOGGER.exception("Exception during channel sign-off", e)
            await callback()



    def init_clips(self) -> int:
        """Initialize the clips available to the bot"""

        clip_group_file_paths = self.clip_file_manager.discover_clip_groups(self.clips_folder_path)
        counter = 0
        for clip_file_path in clip_group_file_paths:
            starting_count = counter
            clip_group = self.clip_file_manager.load_clip_group(clip_file_path)

            clip: Clip
            for clip in clip_group.clips.values():
                try:
                    self.clips[clip.name] = clip
                except Exception as e:
                    LOGGER.warning("Skipping...", e)
                else:
                    counter += 1

            ## Ensure we don't add in empty clip files into the groupings
            ## todo: this isn't necessary any more, is it?
            if(counter > starting_count):
                self.clip_groups[clip_group.key] = clip_group

        LOGGER.info(f'Loaded {counter} clip{"s" if counter != 1 else ""}.')
        return counter


    def build_clip_command_string(self, clip: Clip, activation_str: str = None) -> str:
        """Builds an example string to invoke the specified clip"""

        return f"{activation_str or '/'}{Clips.CLIP_COMMAND_NAME} {clip.name}"


    async def play_clip(
            self,
            clip: Clip,
            author: Member,
            target_member: Member = None,
            interaction: Interaction = None
    ) -> InvokedCommand:
        '''Internal clip player method'''

        try:
            await self.audio_player_cog.play_audio(clip.path, author, target_member or author, interaction)

        except NoVoiceChannelAvailableException as e:
            LOGGER.error("No voice channel available", e)
            if (e.target_member.id == author.id):
                return InvokedCommand(False, e, f"Sorry <@{author.id}>, you're not in a voice channel.")
            else:
                return InvokedCommand(False, e, f"Sorry <@{author.id}>, that person isn't in a voice channel.")

        except UnableToConnectToVoiceChannelException as e:
            ## Logging handled in AudioPlayer

            error_values = []
            if (not e.can_connect):
                error_values.append("connect to")
            if (not e.can_speak):
                error_values.append("speak in")

            return InvokedCommand(False, e, f"Sorry <@{author.id}>, I'm not able to {' or '.join(error_values)} that channel. Check the permissions and try again later.")

        except FileNotFoundError as e:
            LOGGER.error("FileNotFound when invoking `play_audio`", e)
            return InvokedCommand(False, e, f"Sorry <@{author.id}>, I can't say that right now.")

        return InvokedCommand(True)

    ## Commands

    @describe(user="The user to speak the clip to")
    async def random_command(self, interaction: Interaction, user: discord.Member = None):
        """Plays a random clip"""

        clip: Clip = random.choice(list(self.clips.values()))


        async def callback(invoked_command: InvokedCommand):
            if (invoked_command.successful):
                await self.database_manager.store(interaction)
                await interaction.response.send_message(
                    f"<@{interaction.user.id}> randomly chose **{self.build_clip_command_string(clip)}**"
                )
            else:
                await self.database_manager.store(interaction, valid=False)
                await interaction.response.send_message(invoked_command.human_readable_error_message, ephemeral=True)


        action = lambda: self.play_clip(
            clip,
            author=interaction.user,
            target_member=user or interaction.user,
            interaction=interaction
        )
        await self.invoked_command_handler.invoke_command(interaction, action, ephemeral=False, callback=callback)


    async def _clip_name_command_autocomplete(self, interaction: Interaction, current: str) -> list[Choice]:
        def generate_choice(clip: Clip) -> Choice:
            return Choice(name=f"{clip.name} - {clip.help or clip.brief}", value=clip.name)


        if (current.strip() == ""):
            clips = random.choices(list(self.clips.values()), k=5)
            return [generate_choice(clip) for clip in clips]
        else:
            clips = [generate_choice(clip) for clip in self.clips.values() if current in clip.name or current in clip.help]
            return clips[:25] ## Max of 25 results can be returned at once


    async def clip_command(self, interaction: Interaction, name: str, user: discord.Member = None):
        """Plays the specific clip"""

        ## Get the actual clip from the clip name provided by autocomplete
        clip: Clip = self.clips.get(name)
        if (clip is None):
            await self.database_manager.store(interaction, valid=False)
            await interaction.response.send_message(
                f"Sorry <@{interaction.user.id}>, **{name}** isn't a valid clip.",
                ephemeral=True
            )
            return


        async def callback(invoked_command: InvokedCommand):
            if (invoked_command.successful):
                await self.database_manager.store(interaction)
                clip_command_string = self.build_clip_command_string(clip)
                await interaction.response.send_message(f"<@{interaction.user.id}> used **{clip_command_string}**")
            else:
                await self.database_manager.store(interaction, valid=False)
                await interaction.response.send_message(invoked_command.human_readable_error_message, ephemeral=True)


        action = lambda: self.play_clip(
            clip,
            author=interaction.user,
            target_member=user or interaction.user,
            interaction=interaction
        )
        await self.invoked_command_handler.invoke_command(interaction, action, ephemeral=False, callback=callback)


    @describe(search="The text to search the clips for")
    @describe(user="The user to speak the clip to, if a match is found")
    async def find_command(self, interaction: Interaction, search: str, user: discord.Member = None):
        """Plays the most similar clip"""

        def calc_substring_score(message: str, description: str) -> float:
            """Scores a given string (message) based on how many of it's words exist in another string (description)"""

            ## Todo: shrink instances of repeated letters down to a single letter in both message and description
            ##       (ex. yeeeee => ye or reeeeeboot => rebot)

            message_split = message.split(' ')
            word_frequency = sum(word in description.split(' ') for word in message_split)

            return word_frequency / len(message_split)


        ## Strip all non alphanumeric and non whitespace characters out of the message
        search = "".join(char for char in search.lower() if (char.isalnum() or char.isspace()))

        most_similar_clip = (None, 0)
        clip: Clip
        for clip in self.clips.values():
            scores = []

            ## Score the clip
            scores.append(
                calc_substring_score(search, clip.name) +
                StringSimilarity.similarity(search, clip.name) / 2
            )
            if (clip.description is not None):
                scores.append(
                    calc_substring_score(search, clip.description) +
                    StringSimilarity.similarity(search, clip.description) / 2
                )

            distance = sum(scores) / len(scores)
            if (distance > most_similar_clip[1]):
                most_similar_clip = (clip, distance)

        if (most_similar_clip[1] < self.find_command_minimum_similarity):
            await self.database_manager.store(interaction, valid=False)
            await interaction.response.send_message(
                f"Sorry <@{interaction.user.id}>, I couldn't find anything close to that.", ephemeral=True
            )
            return

        ## With the clip found, prepare to speak it!

        async def callback(invoked_command: InvokedCommand):
            if (invoked_command.successful):
                await self.database_manager.store(interaction)
                command_string = self.command_reconstructor.reconstruct_command_string(interaction)
                clip_string = self.build_clip_command_string(most_similar_clip[0])
                await interaction.response.send_message(
                    f"<@{interaction.user.id}> searched with **{command_string}**, and found **{clip_string}**"
                )
            else:
                await self.database_manager.store(interaction, valid=False)
                await interaction.response.send_message(invoked_command.human_readable_error_message, ephemeral=True)


        action = lambda: self.play_clip(
            most_similar_clip[0],
            author=interaction.user,
            target_member=user or interaction.user,
            interaction=interaction
        )
        await self.invoked_command_handler.invoke_command(interaction, action, ephemeral=False, callback=callback)


def main() -> ModuleInitializationContainer:
    return ModuleInitializationContainer(Clips, dependencies=["AdminCog", "AudioPlayer", "InvokedCommandHandler", "DatabaseManager", "CommandReconstructor"])
