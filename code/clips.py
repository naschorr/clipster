import json
import os
import random
from discord import errors
from discord.ext import commands

import utilities
import dynamo_helper

## Config
CONFIG_OPTIONS = utilities.load_config()


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
            utilities.debug_print("Couldn't add clip: {}, as it's not a valid Clip object".format(clip), debug_level=2)


class Clips:
    ## Keys
    CLIPS_FOLDER_PATH_KEY = "clips_folder_path"
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
        self.manifest_file_name = self.MANIFEST_FILE_NAME_KEY
        self.clips_folder_path = clips_folder_path
        self.command_kwargs = command_kwargs
        self.command_names = []
        self.command_group_names = []

        self.dynamo_db = dynamo_helper.DynamoHelper()

        ## Make sure context is always passed to the callbacks
        self.command_kwargs["pass_context"] = True

        ## The mapping of clips into groups 
        self.clip_groups = {}

        ## Load and add the clips
        self.init_clips()

    ## Properties

    @property
    def speech_cog(self):
        return self.clipster.get_speech_cog()

    ## Methods

    ## Removes all existing clips when the cog is unloaded
    def __unload(self):
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
                    utilities.debug_print(e, "Skipping...", debug_level=2)
                else:
                    counter += 1

            ## Ensure we don't add in empty clip files into the groupings
            if(counter > starting_count):
                self.clip_groups[clip_group.key] = clip_group

                ## Set up a dummy command for the category, to help with the help interface. See help_formatter.py
                help_command = commands.Command(clip_group.key, lambda noop: None, hidden=True, no_pm=True)
                self.bot.add_command(help_command)
                self.command_group_names.append(clip_group.key) # Keep track of the 'parent' commands for later use

        print("Loaded {} clip{}.".format(counter, "s" if counter != 1 else ""))
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
                    utilities.debug_print("Error loading {} from {}. Skipping...".format(clip_raw, fd), e, debug_level=3)

        ## Todo: This doesn't actually result in the clips in the help menu being sorted?
        return sorted(clips, key=lambda clip: clip.name)


    ## Unloads the preset clips from the bot's command list
    def remove_clips(self):
        for name in self.command_names + self.command_group_names:
            self.bot.remove_command(name)
        self.command_names = []
        self.command_group_names = []
        self.clip_groups = {} # yay garbage collection

        return True


    ## Add a clip command to the bot's command list
    def add_clip(self, clip):
        if(not isinstance(clip, Clip)):
            raise TypeError("{} not instance of Clip.".format(clip))

        ## Manually build command to be added
        command = commands.Command(
            clip.name,
            self._create_clip_callback(clip.path),
            **clip.kwargs,
            **self.command_kwargs
        )
        ## _clip_callback doesn't have an instance linked to it, 
        ## (not technically a method of Clips?) so manually insert the correct instance anyway.
        ## This also fixes the broken category label in the default help page.
        command.instance = self

        self.bot.add_command(command)


    ## Build a dynamic callback to invoke the bot's say method
    def _create_clip_callback(self, path):
        ## Create a callback for speech.say
        async def _clip_callback(self, ctx):
            ## Pass a self arg to it now that the command.instance is set to self
            speech_cog = self.speech_cog
            play_clip = speech_cog.play_clip
            await play_clip(ctx, path)

        return _clip_callback


    ## Says a random clip from the added clips
    @commands.command(pass_context=True, no_pm=True)
    async def random(self, ctx):
        """Says a random clip from the list of clips."""

        random_clip = random.choice(self.command_names)
        command = self.bot.get_command(random_clip)
        await command.callback(self, ctx)

