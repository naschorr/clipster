import os
import inspect
import logging

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

import utilities
import audio_player
import admin
import clips
import dynamo_helper
import help_command
import module_manager
from string_similarity import StringSimilarity

## Config
CONFIG_OPTIONS = utilities.load_config()

## Logging
logger = utilities.initialize_logging(logging.getLogger(__name__))


class Clipster:
    ## Keys
    VERSION_KEY = "version"
    ACTIVATION_STRING_KEY = "activation_string"
    DESCRIPTION_KEY = "description"
    TOKEN_FILE_PATH_KEY = "token_file_path"
    CLIPS_FOLDER_PATH_KEY = "clips_folder_path"


    ## Initialize the bot, and add base cogs
    def __init__(self):
        self.version = CONFIG_OPTIONS.get(self.VERSION_KEY, 'No version information found')
        self.activation_string = CONFIG_OPTIONS.get(self.ACTIVATION_STRING_KEY)
        self.description = CONFIG_OPTIONS.get(self.DESCRIPTION_KEY, 'No bot description found')
        self.token_file_path = CONFIG_OPTIONS.get(self.TOKEN_FILE_PATH_KEY)
        self.clips_folder_path = CONFIG_OPTIONS.get(self.CLIPS_FOLDER_PATH_KEY)
        self.invalid_command_minimum_similarity = float(CONFIG_OPTIONS.get("invalid_command_minimum_similarity", 0.25))
        self.dynamo_db = dynamo_helper.DynamoHelper()

        ## Make sure we've got the bare minimums to instantiate and run the bot
        assert(self.activation_string is not None)
        assert(self.token_file_path is not None)

        ## Init the bot and module manager
        self.bot = commands.Bot(
            command_prefix=commands.when_mentioned_or(self.activation_string),
            description=self.description
        )
        self.module_manager = module_manager.ModuleManager(self, self.bot)

        ## Apply customized HelpCommand
        self.bot.help_command = help_command.ClipsterHelpCommand()

        ## Register the modules (Order of registration is important, make sure dependancies are loaded first)
        self.module_manager.register(audio_player.AudioPlayer, True, self.bot)
        self.module_manager.register(admin.Admin, True, self, self.bot)
        self.module_manager.register(clips.Clips, True, self, self.bot, self.clips_folder_path)

        ## Load any dynamic modules inside the /modules folder
        self.module_manager.discover()

        ## Give some feedback for when the bot is ready to go, and provide some help text via the 'playing' status
        @self.bot.event
        async def on_ready():
            ## todo: Activity instead of Game? Potentially remove "Playing" text below bot
            bot_status = discord.Game(type=0, name="Use {}help".format(self.activation_string))
            await self.bot.change_presence(activity=bot_status)

            logger.info("Logged in as '{}' (version: {}), (id: {})".format(self.bot.user.name, self.version, self.bot.user.id))


        @self.bot.event
        async def on_command_error(ctx, exception):
            '''Handles command errors. Attempts to find a similar command and suggests it, otherwise directs the user to the help prompt.'''
            
            logger.exception("Unable to process command.", exc_info=exception)
            self.dynamo_db.put(dynamo_helper.DynamoItem(
                ctx, ctx.message.content, inspect.currentframe().f_code.co_name, False, str(exception)))

            ## Attempt to find a command that's similar to the one they wanted. Otherwise just direct them to the help page
            most_similar_command = self.find_most_similar_command(ctx.message.content)

            if (most_similar_command[0] == ctx.invoked_with):
                ## Handle issues where the command is valid, but couldn't be completed for whatever reason.
                await ctx.send("I'm sorry <@{}>, I'm afraid I can't do that.\n" \
                    "Discord is having some issues that won't let me speak right now."
                    .format(ctx.message.author.id))
            else:
                help_text_chunks = [
                    "Sorry <@{}>, **{}{}** isn't a valid command.".format(ctx.message.author.id, ctx.prefix, ctx.invoked_with)
                ]

                if (most_similar_command[1] > self.invalid_command_minimum_similarity):
                    help_text_chunks.append("Did you mean **{}{}**?".format(self.activation_string, most_similar_command[0]))
                else:
                    help_text_chunks.append("Try the **{}help** page.".format(self.activation_string))

                ## Dump output to user
                await ctx.send(" ".join(help_text_chunks))
                return

    ## Methods

    ## Add an arbitary cog to the bot
    def add_cog(self, cls):
        self.bot.add_cog(cls)


    ## Returns a cog with a given name
    def get_cog(self, cls_name):
        return self.bot.get_cog(cls_name)


    ## Returns the bot's audio player cog
    def get_audio_player_cog(self):
        return self.bot.get_cog("AudioPlayer")


    ## Returns the bot's clips cog
    def get_clips_cog(self):
        return self.bot.get_cog("Clips")


    ## Register an arbitrary module with clipster (easy wrapper for self.module_manager.register)
    def register_module(self, cls, is_cog, *init_args, **init_kwargs):
        self.module_manager.register(cls, is_cog, *init_args, **init_kwargs)


    ## Finds the most similar command to the supplied one
    def find_most_similar_command(self, command):
        ## Build a message string that we can compare with.
        try:
            message = command[len(self.activation_string):]
        except TypeError:
            message = command

        ## Get a list of all visible commands 
        commands = [cmd.name for cmd in self.bot.commands if not cmd.hidden]

        ## Find the most similar command
        most_similar_command = (None, 0)
        for key in commands:
            distance = StringSimilarity.similarity(key, message)
            if (distance > most_similar_command[1]):
                most_similar_command = (key, distance)

        return most_similar_command


    def run(self):
        '''Starts the bot up'''

        ## So ideally there would be some flavor of atexit.register or signal.signal command to gracefully shut the bot
        ## down upon SIGTERM or SIGINT. However that doesn't seem to be possible at the moment. Discord.py's got most of
        ## the functionality built into the base close() method that fires on SIGINT and SIGTERM, but the bot never ends
        ## up getting properly disconnected from the voice channels that it's connected to. I end up having to wait for
        ## a time out. Otherwise the bot will be in a weird state upon starting back up, and attempting to speak in one
        ## of the channels that it was previously in. Fortunately this bad state will self-recover in a minute or so,
        ## but it's still unpleasant. A temporary fix is to bump up the RestartSec= property in the service config to be
        ## long enough to allow for the bot to be forcefully disconnected

        logger.info('Starting up the bot.')
        self.bot.run(utilities.load_json(os.path.sep.join([utilities.get_root_path(), self.token_file_path]))["token"])


if (__name__ == "__main__"):
    clipster = Clipster()
    # clipster.register_module(ArbitraryClass(*init_args, **init_kwargs))
    # or,
    # clipster.add_cog(ArbitaryClass(*args, **kwargs))

    clipster.run()
