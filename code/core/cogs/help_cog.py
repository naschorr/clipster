from inspect import signature
import logging
import os
import random
from functools import reduce

from common.audio_player import AudioPlayer
from common.configuration import Configuration
from common.database.database_manager import DatabaseManager
from common.logging import Logging
from common.module.module import Cog
from common.ui.component_factory import ComponentFactory
from modules.clips.clips import Clips
from modules.clips.models.clip import Clip
from modules.clips.models.clip_group import ClipGroup

from discord import app_commands, Interaction, Embed
from discord.ext import commands
from discord.app_commands import autocomplete, choices, describe, Choice

## Config & logging
CONFIG_OPTIONS = Configuration.load_config()
LOGGER = Logging.initialize_logging(logging.getLogger(__name__))


class HelpCog(Cog):
    """Builds and displays the help interface"""

    ## Note: This whole class is terrible and I hate it. I'll rework it eventually, but time is of the essence, and I
    ## need to get the full slash command conversion done before the Discord message content intent goes away. While
    ## terrible, and no doubt needlessly difficult to maintain in the future, it works.

    def __init__(self, bot: commands.Bot, *args, **kwargs):
        super().__init__(bot, *args, **kwargs)

        self.bot = bot

        self.clips_cog: Clips = kwargs.get('dependencies', {}).get('Clips')
        assert(self.clips_cog is not None)
        self.component_factory: ComponentFactory = kwargs.get('dependencies', {}).get('ComponentFactory')
        assert(self.component_factory is not None)
        self.database_manager: DatabaseManager = kwargs.get('dependencies', {}).get('DatabaseManager')
        assert (self.database_manager is not None)

        self.name = CONFIG_OPTIONS.get("name", "help").capitalize()
        self.version = CONFIG_OPTIONS.get("version", "1.0.0")
        self.description = CONFIG_OPTIONS.get("description")
        self.repo_url = CONFIG_OPTIONS.get("repo_url")
        self.activation_str = "/"   ## Slash commands

        ## Simple cog -> commands mapping
        self.cog_command_tree = self.build_cog_command_tree()
        ## Similar to the cog_command_tree, but with a focus on commands and grouping
        self.command_tree = self.build_command_tree(self.cog_command_tree)

        ## Ensure the clip groups get added to the clips
        for clip in self.clips_cog.clip_groups.values():
            self.command_tree[1][self.clips_cog.__class__.__name__.lower()][clip.key] = clip


        ## Add the help command
        ## Wrap the help command to have access to self in the autocomplete decorator. Unfortunately the parameter
        ## description decorators must also be moved up here.
        ## todo: Investigate a workaround that's less ugly?
        @choices(command=self._help_command_command_choices())
        @describe(command="The command to learn more about")
        @autocomplete(subcommand=self._help_command_subcommand_autocomplete)
        @describe(subcommand="The command's subcommand to learn more about")
        async def help_command_wrapper(interaction: Interaction, command: str = None, subcommand: str = None):
            await self._help_command(interaction, command, subcommand)

        self.help_command = app_commands.Command(
            name="help",
            description=self._help_command.__doc__,
            callback=help_command_wrapper,
            extras={"cog": self}
        )
        self.add_command(self.help_command)
        self.cog_command_tree[HelpCog.__name__] = {self.help_command.name: self.help_command}
        self.command_tree[0][self.help_command.name] = self.help_command

    ## Methods

    def build_cog_command_tree(self) -> dict[str, dict[str, app_commands.Command]]:
        result = {}

        for command in self.bot.tree.get_commands():
            cog = command.binding or command.extras["cog"]
            cog_name = cog.__class__.__name__ or None
            command_name = command.name

            if (cog_name not in result):
                result[cog_name] = {command_name: command}
            else:
                result[cog_name][command_name] = command

        return result


    def build_command_tree(self, cog_command_tree: dict[str, dict[str, app_commands.Command]]) -> tuple[list[app_commands.Command], dict[str, dict[str, app_commands.Command]]]:
        root_commands = {}
        cog_tree = {}

        for cog_name, cog in cog_command_tree.items():
            commands = []
            commands_map = {}
            for command in cog.values():
                commands.append(command)
                commands_map[command.name] = command

            if (len(commands) == 1):
                root_commands[commands[0].name] = commands[0]
            else:
                cog_tree[cog_name.lower()] = commands_map

        return (root_commands, cog_tree)


    def build_header_help_embed(self) -> Embed:
        ## Build a more all-encompassing description for the bot
        description = [
            self.description,
            ## The "{'ㅤ' * 10}" adds non-collapsing whitespace to the end of the final line, which forces the embed to
            ## fully expand. Otherwise all of the embeds will be slightly misaligned, and that's not very nice.
            ## This is the least ugly solution, currently. Alternatively, a wide 1 pixel tall image might work as well.
            ## However, hosting needs to be considered
            f"Learn more, contribute, or ⭐ me on [GitHub]({self.repo_url}).{'ㅤ' * 10}",
        ]

        header = self.component_factory.create_embed(
            self.name,
            os.linesep.join(description) if description else None,
            self.repo_url
        )
        header.set_footer(text=f"Version {self.version}")

        return header


    def build_command_signature(self, command: app_commands.Command, ignore_optional = False) -> str:
        required_parameters = []
        optional_parameters = []

        for parameter in command.parameters:
            if (parameter.required):
                required_parameters.append(parameter)
            elif (not ignore_optional):
                optional_parameters.append(parameter)

        required_parameter_str = " ".join([f"<{parameter.name}>" for parameter in required_parameters])
        optional_parameter_str = " ".join([f"[{parameter.name}]" for parameter in optional_parameters])

        signature = f"{self.activation_str}{command.qualified_name}"
        if (required_parameter_str):
            signature += f" {required_parameter_str}"
        if (optional_parameter_str):
            signature += f" {optional_parameter_str}"

        return signature


    def build_commands_help_embed(self) -> Embed:
        commands = {command.name : command for command in self.bot.tree.get_commands()}
        command_signatures = {command.name: self.build_command_signature(command, True) for command in commands.values()}
        longest_command_length = reduce(lambda length, signature: max(length, len(signature)), command_signatures.values(), 0)


        def build_command_help_line(command: app_commands.Command) -> str:
            if (command is None):
                return None

            if (command.name in commands):
                commands.pop(command.name)  ## Keep track of the remaining commands

            signature = command_signatures[command.name]
            padding = " " * (longest_command_length - len(signature) + 1)

            return f"{signature}{padding}{f' {command.description}' if command.description else ''}"


        ## Build the core help commands first, as these are the most commonly used ones and should be organized and at
        ## the top for easy viewing
        command_help = [
            ## Use class and method references to avoid any magic strings
            build_command_help_line(self.cog_command_tree[Clips.__name__][Clips.CLIP_COMMAND_NAME]),
            build_command_help_line(self.cog_command_tree[Clips.__name__][Clips.FIND_COMMAND_NAME]),
            build_command_help_line(self.cog_command_tree[AudioPlayer.__name__][AudioPlayer.SKIP_COMMAND_NAME]),
            build_command_help_line(self.cog_command_tree[Clips.__name__][Clips.RANDOM_COMMAND_NAME])
        ]

        ## Remove the help command now...
        commands.pop(self.help_command.name)

        ## Add the rest of the commands in below
        for command in sorted(list(commands.values()), key=lambda command: command.name):
            command_help.append(build_command_help_line(command))

        ## ...so the help command can be added at the end
        command_help.append(build_command_help_line(self.cog_command_tree[HelpCog.__name__][self.help_command.name]))

        description = [
            f"These are the core commands that {self.name} offers. You can also, start typing the command's name to "
            "see additional configuration",
            f"```{os.linesep.join(command_help)}```"
        ]

        return self.component_factory.create_basic_embed(
            "Basic Commands",
            os.linesep.join(description)
        )


    def build_clip_group_help_command(self, clip_group: ClipGroup) -> str:
        return f"{self.activation_str}help {self.clips_cog.CLIPS_NAME} {clip_group.key}"


    def build_clip_groups_help_description(self, clip_groups: list[ClipGroup]) -> str:
        if (not clip_groups):
            return None

        clip_group_signatures = {clip_group.name: self.build_clip_group_help_command(clip_group) for clip_group in clip_groups}
        longest_clip_group_length = reduce(lambda length, signature: max(length, len(signature)), clip_group_signatures.values(), 0)


        def build_clip_group_help_line(clip_group: ClipGroup) -> str:
            if (clip_group is None):
                return None

            signature = clip_group_signatures[clip_group.name]
            padding = " " * (longest_clip_group_length - len(signature) + 1)

            return f"{signature}{padding}{f' {clip_group.description}' if clip_group.description else ''}"


        return os.linesep.join([
            f"{self.name} also offers more clip categories to play with.",
            f"```{os.linesep.join([build_clip_group_help_line(clip_group) for clip_group in sorted(clip_groups, key=lambda clip_group: clip_group.name)])}```"
        ])


    def build_clips_help_embed(self, clip_group: ClipGroup, limit: int = None) -> Embed:
        clip_groups: set[ClipGroup] = set(self.clips_cog.clip_groups.values())
        if (limit is None):
            clip_groups.remove(clip_group)
        clips = list(clip_group.clips.values())
        random.shuffle(clips)
        clips = clips[:limit]

        clip_signatures = {clip.name: self.clips_cog.build_clip_command_string(clip) for clip in clips}
        longest_clip_length = reduce(lambda length, clip: max(length, len(clip)), clip_signatures.values(), 0)


        def build_clip_help_line(clip: Clip) -> str:
            if (clip is None):
                return None

            signature = clip_signatures[clip.name]
            padding = " " * (longest_clip_length - len(signature) + 1)

            return f"{signature}{padding} {clip.help or clip.brief}"


        description = [(
            f"{self.name} offers a bunch of preset clips, which give you easy access to the classics (and more!) "
            "without searching"
        )]

        see_more_clips = (
            "Here's a few of them for you to play with. See the rest with: "
            f"**{self.build_clip_group_help_command(clip_group)}**"
        )
        if (limit is not None):
            description[0] = f"{description[0]} {see_more_clips}"

        description.append(f"```{os.linesep.join([build_clip_help_line(clip) for clip in sorted(clips, key=lambda clip: clip.name)])}```")

        if (clip_groups_description := self.build_clip_groups_help_description(list(clip_groups))):
            description.append(clip_groups_description)

        return self.component_factory.create_basic_embed(
            "Clips",
            os.linesep.join(description)
        )


    def build_command_help_embed(self, command: app_commands.Command) -> Embed:
        embed = self.component_factory.create_basic_embed(
            f"{self.activation_str}{command.qualified_name}",
            command.description
        )

        usage = [
            "```",
            self.build_command_signature(command),
            os.linesep.join([f"  {parameter.name} - {parameter.description}" for parameter in command.parameters]),
            "```"
        ]
        embed.add_field(name="Usage", value=os.linesep.join(usage), inline=False)

        return embed


    def build_help_embeds(self) -> list[Embed]:
        embeds = [
            self.build_header_help_embed(),
            self.build_commands_help_embed()
        ]

        clip_group = self.clips_cog.clip_groups.get("internet")
        if (clip_group is not None):
            embeds.append(self.build_clips_help_embed(clip_group, limit=10))

        return embeds

    ## Commands

    def _help_command_command_choices(self) -> list[Choice]:
        root_commands = list(self.command_tree[0].values())
        groups = self.command_tree[1].keys()

        output = []
        output.extend([Choice(name=command.name, value=command.name) for command in root_commands])
        output.extend([Choice(name=group, value=group) for group in groups])

        return output


    async def _help_command_subcommand_autocomplete(self, interaction: Interaction, current: str) -> list[Choice]:
        def build_choice(item: app_commands.Command | ClipGroup) -> Choice:
            if (isinstance(item, app_commands.Command)):
                return Choice(name=item.name, value=item.name)
            else:
                return Choice(name=item.key, value=item.key)


        def is_current_command(item: app_commands.Command | ClipGroup) -> bool:
            if (isinstance(item, app_commands.Command)):
                normalized = item.name
            else:
                normalized = item.key

            return normalized.startswith(current)


        command = [option for option in interaction.data.get("options", []) if option["name"] == "command"]
        if (not command):
            return []
        else:
            command = command[0]

        subcommand_parent = self.command_tree[1].get(command["value"])
        if (subcommand_parent is None):
            return []

        return [build_choice(element) for element in list(subcommand_parent.values()) if is_current_command(element)]


    async def _help_command(self, interaction: Interaction, command: str = None, subcommand: str = None):
        """Shows the help page"""

        if (command is not None and subcommand is None):
            target_command = self.command_tree[0].get(command)
            if (target_command is None):
                await self.database_manager.store(interaction, valid=False)
                potential_subcommand = self.command_tree[1].get(command)
                if (potential_subcommand is not None):
                    await interaction.response.send_message(f"Sorry <@{interaction.user.id}>, the command '{command}' requires a subcommand.", ephemeral=True)
                else:
                    await interaction.response.send_message(f"Sorry <@{interaction.user.id}>, the command '{command}' isn't valid.", ephemeral=True)
                return

            await self.database_manager.store(interaction)
            embeds = [self.build_command_help_embed(target_command)]
            await interaction.response.send_message(embeds=embeds, ephemeral=True)
            return

        elif (command is not None and subcommand is not None):
            target_command = self.command_tree[1].get(command)
            if (target_command is None):
                await self.database_manager.store(interaction, valid=False)
                await interaction.response.send_message(f"Sorry <@{interaction.user.id}>, the command '{command}' isn't valid.", ephemeral=True)
                return

            target_subcommand = target_command.get(subcommand)
            if (target_subcommand is None):
                await self.database_manager.store(interaction, valid=False)
                await interaction.response.send_message(f"Sorry <@{interaction.user.id}>, the subcommand '{subcommand}' isn't valid.", ephemeral=True)
                return

            if (isinstance(target_subcommand, app_commands.Command)):
                embeds = [self.build_command_help_embed(target_subcommand)]
            else:
                embeds = [self.build_clips_help_embed(target_subcommand)]

            await self.database_manager.store(interaction)
            await interaction.response.send_message(embeds=embeds, ephemeral=True)
            return

        elif (command is None and subcommand is not None):
            await self.database_manager.store(interaction, valid=False)
            await interaction.response.send_message(f"Sorry <@{interaction.user.id}>, I need a `command` and `subcommand` to provide more specific help.", ephemeral=True)
            return

        else:
            await self.database_manager.store(interaction)
            embeds = self.build_help_embeds()
            await interaction.response.send_message(embeds=embeds)
            return
