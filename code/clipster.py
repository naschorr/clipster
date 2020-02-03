import importlib
import inspect
import sys
import os
import time
import logging
from collections import OrderedDict

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

import utilities
import audio_player
import admin
import clips
import dynamo_helper
import help_command
from string_similarity import StringSimilarity

## Config
CONFIG_OPTIONS = utilities.load_config()

## Logging
logger = utilities.initialize_logging(logging.getLogger(__name__))

class ModuleEntry:
    def __init__(self, cls, is_cog, *init_args, **init_kwargs):
        self.module = sys.modules[cls.__module__]
        self.cls = cls
        self.name = cls.__name__
        self.is_cog = is_cog
        self.args = init_args
        self.kwargs = init_kwargs

    ## Methods

    ## Returns an invokable object to instantiate the class defined in self.cls
    def get_class_callable(self):
        return getattr(self.module, self.name)


class ModuleManager:
    ## Keys
    MODULES_FOLDER_KEY = "modules_folder"

    def __init__(self, clipster, bot):
        self.modules_folder = CONFIG_OPTIONS.get(self.MODULES_FOLDER_KEY, "")

        self.clipster = clipster
        self.bot = bot
        self.modules = OrderedDict()

    ## Methods

    ## Registers a module, class, and args necessary to instantiate the class
    def register(self, cls, is_cog=True, *init_args, **init_kwargs):
        if(not inspect.isclass(cls)):
            raise RuntimeError("Provided class parameter '{}' isn't actually a class.".format(cls))

        if(not init_args):
            init_args = [self.clipster, self.bot]

        module_entry = ModuleEntry(cls, is_cog, *init_args, **init_kwargs)
        self.modules[module_entry.name] = module_entry

        ## Add the module to the bot (if it's a cog), provided it hasn't already been added.
        if(not self.bot.get_cog(module_entry.name) and module_entry.is_cog):
            cog_cls = module_entry.get_class_callable()
            self.bot.add_cog(cog_cls(*module_entry.args, **module_entry.kwargs))
            logger.info("Registered cog: {} on bot.".format(module_entry.name))


    ## Finds and registers modules inside the modules folder
    def discover(self):
        ## Assumes that the modules folder is inside the root
        modules_folder_path = os.path.abspath(os.path.sep.join(["..", self.modules_folder]))
        ## Expose the modules folder to the interpreter, so modules can be loaded
        sys.path.append(modules_folder_path)

        ## Build a list of potential module paths and iterate through it...
        candidate_modules = os.listdir(modules_folder_path)
        for candidate in candidate_modules:
            ## If the file could be a python file...
            if(candidate[-3:] == ".py"):
                name = candidate[:-3]

                ## Attempt to import the module (akin to 'import [name]') and register it normally
                ## NOTE: Modules MUST have a 'main()' function that essentially returns a list containing all the args
                ##       needed by the 'register()' method of this ModuleManager class. At a minimum this list MUST
                ##       contain a reference to the class that serves as an entry point to the module. You should also
                ##       specify whether or not a given module is a cog (for discord.py) or not.
                try:
                    module = importlib.import_module(name)
                    declarations = module.main()

                    ## Validate the shape of the main() method's data, and attempt to tolerate poor formatting
                    if(not isinstance(declarations, list)):
                        declarations = [declarations]
                    elif(len(declarations) == 0):
                        raise RuntimeError("Module '{}' main() returned empty list. Needs a class object at minimum.".format(module.__name__))

                    self.register(*declarations)
                except Exception as e:
                    del module


    ## Reimport a single module
    def _reimport_module(self, module):
        try:
            importlib.reload(module)
        except Exception as e:
            logger.error("Error: ({}) reloading module: {}".format(e, module))
            return False
        else:
            return True


    ## Reloads a module with the provided name
    def _reload_module(self, module_name):
        module_entry = self.modules.get(module_name)
        assert module_entry is not None

        self._reimport_module(module_entry.module)


    ## Reload a cog attached to the bot
    def _reload_cog(self, cog_name):
        module_entry = self.modules.get(cog_name)
        assert module_entry is not None

        self.bot.remove_cog(cog_name)
        self._reimport_module(module_entry.module)
        cog_cls = module_entry.get_class_callable()
        self.bot.add_cog(cog_cls(*module_entry.args, **module_entry.kwargs))


    ## Reload all of the registered modules
    def reload_all(self):
        counter = 0
        for module_name in self.modules:
            try:
                if(self.modules[module_name].is_cog):
                    self._reload_cog(module_name)
                else:
                    self._reload_module(module_name)
            except Exception as e:
                logger.error("Error: {} when reloading cog: {}".format(e, module_name))
            else:
                counter += 1

        logger.info("Loaded {}/{} cogs.".format(counter, len(self.modules)))
        return counter


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
        self.module_manager = ModuleManager(self, self.bot)

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
