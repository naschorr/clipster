import json
import os
import random
import asyncio
import logging
from discord.ext import commands
from discord.ext.commands.errors import MissingRequiredArgument

import utilities
import dynamo_helper
from string_similarity import StringSimilarity

## Config
CONFIG_OPTIONS = utilities.load_config()

## Logging
logger = utilities.initialize_logging(logging.getLogger(__name__))

class Clip:
    def __init__(self, name, path, **kwargs):
        self.name = name
        self.path = path
        self.kwargs = kwargs

    def __str__(self):
        return "{} at {}, {}".format(self.name, self.path, self.kwargs)


class ClipGroup:
    def __init__(self, name, key, description):
        self.name = name
        self.key = key
        self.description = description
        self.clips = {}

    def add_clip(self, clip):
        if (isinstance(clip, Clip)):
            self.clips[clip.name] = clip
        else:
            logger.warning("Couldn't add clip: {}, as it's not a valid Clip object".format(clip))


class Clips(commands.Cog):
    ## Keys
    MANIFEST_FILE_NAME_KEY = "manifest.json"
    CLIPS_KEY = "clips"
    NAME_KEY = "name"
    PATH_KEY = "path"
    HELP_KEY = "help"
    BRIEF_KEY = "brief"
    DESCRIPTION_KEY = "description"


    def __init__(self, clipster, bot, clips_folder_path, **command_kwargs):
        self.clipster = clipster
        self.bot = bot
        self.dynamo_db = dynamo_helper.DynamoHelper()

        self.manifest_file_name = self.MANIFEST_FILE_NAME_KEY
        self.clips_folder_path = clips_folder_path
        self.command_kwargs = command_kwargs
        self.command_names = []
        self.command_group_names = []
        self.find_command_minimum_similarity = float(CONFIG_OPTIONS.get('find_command_minimum_similarity', 0.5))
        self.channel_timeout_clip_paths = CONFIG_OPTIONS.get('channel_timeout_clip_paths', [])

        ## Make sure context is always passed to the callbacks
        self.command_kwargs["pass_context"] = True

        ## The mapping of clips into groups 
        self.clip_groups = {}

        ## Load and add the clips
        self.init_clips()

    ## Properties

    @property
    def audio_player_cog(self):
        return self.clipster.get_audio_player_cog()

    ## Methods

    ## Removes all existing clips when the cog is unloaded
    def cog_unload(self):
        self.remove_clips()


    ## Searches the clips folder for folders containing a manifest.json file (which then describes the clips to be loaded)
    def scan_clips(self, path_to_scan):
        def is_clip_dir(file_path):
            if (os.path.isdir(file_path)):
                manifest_exists = os.path.isfile(os.path.sep.join([file_path, self.manifest_file_name]))
                is_populated = len([path for path in os.listdir(file_path)]) > 1

                return (manifest_exists and is_populated)
            
            return False

        clip_dirs = []
        for file in os.listdir(path_to_scan):
            full_file_path = os.sep.join([path_to_scan, file])
            if(is_clip_dir(full_file_path)):
                clip_dirs.append(full_file_path)

        return clip_dirs


    ## Builds a ClipGroup object from a directory containing clips and a manifest.json file
    def _build_clip_group(self, path):
        with open(os.path.sep.join([path, self.manifest_file_name])) as fd:
            group_raw = json.load(fd)
            name = group_raw.get('name', path.split(os.path.sep)[-1])
            key = group_raw.get('key', name)
            description = group_raw.get('description', None)

            return ClipGroup(name, key, description)


    ## Initialize the clips available to the bot
    def init_clips(self):
        clip_dir_paths = self.scan_clips(os.path.sep.join([utilities.get_root_path(), self.clips_folder_path]))

        counter = 0
        for clip_dir_path in clip_dir_paths:
            starting_count = counter
            clip_group = self._build_clip_group(clip_dir_path)

            for clip in self.load_clips(clip_dir_path):
                try:
                    self.add_clip(clip)
                    clip_group.add_clip(clip)
                except Exception as e:
                    logger.warn("Couldn't add clip", exc_info=True)
                else:
                    counter += 1

            ## Ensure we don't add in empty clip files into the groupings
            if(counter > starting_count):
                self.clip_groups[clip_group.key] = clip_group

                ## Set up a dummy command for the category, to assist with creating the help interface.
                ## asyncio.sleep is just a dummy command since commands.Command needs some kind of async callback
                help_command = commands.Command(self._create_noop_callback(), name=clip_group.key, hidden=True, no_pm=True)
                self.bot.add_command(help_command)
                self.command_group_names.append(clip_group.key) # Keep track of the 'parent' commands for later use

        logger.info("Loaded {} clip{}.".format(counter, "s" if counter != 1 else ""))
        return counter


    ## Unloads all clip commands, then reloads them from the clips.json file
    def reload_clips(self):
        self.remove_clips()
        return self.init_clips()


    ## Load clips from json into a list of clip objects
    def load_clips(self, clip_dir_path):
        ## Insert source[key] (if it exists) into target[key], else insert a default string
        def insert_if_exists(target, source, key, default=None):
            if(key in source):
                target[key] = source[key]
            return target

        clips = []
        manifest_path = os.path.sep.join([clip_dir_path, self.manifest_file_name])
        with open(manifest_path) as fd:
            for clip_raw in json.load(fd)[self.CLIPS_KEY]:
                try:
                    ## Todo: make this less ugly
                    kwargs = {}
                    help_value = clip_raw.get(self.HELP_KEY)  # fallback for the help submenus
                    kwargs = insert_if_exists(kwargs, clip_raw, self.HELP_KEY)
                    kwargs = insert_if_exists(kwargs, clip_raw, self.BRIEF_KEY, help_value)
                    kwargs = insert_if_exists(kwargs, clip_raw, self.DESCRIPTION_KEY, help_value)

                    clip_name = clip_raw[self.NAME_KEY]
                    clip = Clip(
                        clip_name,
                        os.path.sep.join([clip_dir_path, clip_raw[self.PATH_KEY]]),
                        **kwargs
                    )
                    clips.append(clip)
                    self.command_names.append(clip_name)
                except Exception as e:
                    logger.warning("Error loading {} from {}. Skipping...".format(clip_raw, fd), exc_info=True)

        ## Todo: This doesn't actually result in the clips in the help menu being sorted?
        return sorted(clips, key=lambda clip: clip.name)


    ## Unloads the preset clips from the bot's command list
    def remove_clips(self):
        for name in self.command_names + self.command_group_names:
            self.bot.remove_command(name)
        self.command_names = []
        self.command_group_names = []
        self.clip_groups = {} # yay garbage collection

        logger.info("Removed clips")
        return True


    ## Add a clip command to the bot's command list
    def add_clip(self, clip):
        if(not isinstance(clip, Clip)):
            raise TypeError("{} not instance of Clip.".format(clip))

        ## Manually build command to be added
        command = commands.Command(
            self._create_clip_callback(clip.path),
            name = clip.name,
            **clip.kwargs,
            **self.command_kwargs
        )
        ## _clip_callback doesn't have an instance linked to it, 
        ## (not technically a method of Clips?) so manually insert the correct instance anyway.
        ## This also fixes the broken category label in the default help page.
        command.instance = self

        self.bot.add_command(command)


    def _create_noop_callback(self):
        '''
        Build an async noop callback. This is used as a dummy callback for the help commands that make up the command
        categories
        '''

        async def _noop_callback(ctx):
            await asyncio.sleep(0)

        return _noop_callback


    def _create_clip_callback(self, path):
        '''Build a dynamic callback to invoke the bot's play_audio method'''

        async def _clip_callback(ctx):
            ## Pass a self arg to it now that the command.instance is set to self
            audio_player_cog = self.audio_player_cog
            play_audio = audio_player_cog.play_audio

            ## Attempt to get a target channel
            try:
                target = ctx.message.mentions[0]
            except:
                target = None

            await play_audio(ctx, path, target_member=target)

        return _clip_callback


    async def play_random_channel_timeout_clip(self, server_state, callback):
        '''Channel timeout logic, picks an appropriate sign-off message and plays it'''

        if (len(self.channel_timeout_clip_paths) > 0):
            await self.audio_player_cog._play_audio_via_server_state(
                server_state,
                os.path.sep.join([utilities.get_root_path(), random.choice(self.channel_timeout_clip_paths)]),
                callback
            )


    ## Says a random clip from the added clips
    @commands.command(no_pm=True)
    async def random(self, ctx):
        """Says a random clip from the list of clips."""

        random_clip = random.choice(self.command_names)
        command = self.bot.get_command(random_clip)
        await command.callback(ctx)


    def _calcSubstringScore(self, message, description):
        ## Todo: shrink instances of repeated letters down to a single letter in both message and description
        ##       (ex. yeeeee => ye or reeeeeboot => rebot)

        message_split = message.split(' ')
        word_frequency = 0
        for word in message_split:
            if (word in description.split(' ')):
                word_frequency += 1

        return word_frequency / len(message_split)


    @commands.command(no_pm=True)
    async def find(self, ctx, *, search_text = None):
        '''Find clips that are similar to the search text'''

        ## This method isn't ideal, as it breaks the command's signature. However it's the least bad option until
        ## Command.error handling doesn't always call the global on_command_error
        if (search_text is None):
            await self.find_error(ctx, MissingRequiredArgument(ctx.command.params['search_text']))
            return

        ## Strip all non alphanumeric and non whitespace characters out of the message
        message = ''.join(char for char in search_text.lower() if (char.isalnum() or char.isspace()))

        most_similar_command = (None, 0)
        for clip_group in self.clip_groups.values():
            for clip in clip_group.clips.values():
                ## Todo: Maybe look into filtering obviously bad descriptions from the calculation somehow?
                ##       A distance metric might be nice, but then if I could solve that problem, why not just use that
                ##       distance in the first place and skip the substring check?

                description = clip.kwargs.get(self.DESCRIPTION_KEY)
                if (not description):
                    continue

                ## Build a weighted distance using a traditional similarity metric and the previously calculated word
                ## frequency as well as the similarity of the actual string that invokes the clip
                distance =  (self._calcSubstringScore(message, description) * 0.5) + \
                            (StringSimilarity.similarity(description, message) * 0.3) + \
                            (StringSimilarity.similarity(message, clip.name) * 0.2)

                if (distance > most_similar_command[1]):
                    most_similar_command = (clip, distance)

        if (most_similar_command[1] > self.find_command_minimum_similarity):
            command = self.bot.get_command(most_similar_command[0].name)
            await command.callback(ctx)
        else:
            await ctx.send("I couldn't find anything close to that, sorry <@{}>.".format(ctx.message.author.id))


    @find.error
    async def find_error(self, ctx, error):
        '''
        Find command error handler. Addresses some common error scenarios that on_command_error doesn't really help with
        '''
        
        if (isinstance(error, MissingRequiredArgument)):
            output_raw = "Sorry <@{}>, but I need something to search for! Why not try: **{}find {}**?"
            await ctx.send(output_raw.format(
                ctx.message.author.id,
                CONFIG_OPTIONS.get("activation_string"),
                random.choice(self.command_names)
            ))
