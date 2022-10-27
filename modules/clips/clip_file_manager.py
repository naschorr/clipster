import json
import logging
import os
import re
from pathlib import Path
from typing import List

from common.configuration import Configuration
from common.logging import Logging
from modules.clips.models.clip import Clip
from modules.clips.models.clip_group import ClipGroup

## Config & logging
CONFIG_OPTIONS = Configuration.load_config(Path(__file__).parent)
LOGGER = Logging.initialize_logging(logging.getLogger(__name__))


class ClipFileManager:
    def __init__(self):
        self.clips_manifest_file_name = CONFIG_OPTIONS.get('clips_manifest_file_name', 'manifest.json')
        self.non_letter_regex = re.compile('\W+')   # Compile a regex for filtering non-letter characters

        clips_folder_path = CONFIG_OPTIONS.get('clips_folder_path')
        if (clips_folder_path):
            self.clips_folder_path = Path(clips_folder_path)
        else:
            self.clips_folder_path = Path.joinpath(Path(__file__).parent, CONFIG_OPTIONS.get('clips_folder', 'clips'))


    def discover_clip_groups(self, path_to_scan: Path) -> List[Path]:
        '''Searches the clips folder for .json files that can potentially contain clip groups & clips'''

        clip_files = []
        for directory in os.listdir(path_to_scan):
            file_path: Path = path_to_scan / directory / self.clips_manifest_file_name
            if (file_path.exists()):
                clip_files.append(file_path)

        return clip_files


    def _build_clips(self, clip_directory_path: Path, clips_json: dict) -> List[Clip]:
        '''
        Given a JSON dict representing an unparsed ClipGroup's list of Clips, build a list of Clip objects from
        it, and return that list
        '''

        ## Insert source[key] (if it exists) into target[key], else insert a default string
        def insert_if_exists(target, source, key, default=None):
            if(key in source):
                target[key] = source[key]
            return target

        clips = []
        for clip_raw in clips_json:
            try:
                name = clip_raw['name']

                path = clip_directory_path / clip_raw['path']
                if (not path.exists()):
                    raise FileNotFoundError(f"Clip {name}'s path doesn't exist! {path}")

                kwargs = {}
                kwargs = insert_if_exists(kwargs, clip_raw, 'description')
                ## Todo: make this less ugly
                help_value = clip_raw.get('help')  # fallback for the help submenus
                kwargs = insert_if_exists(kwargs, clip_raw, 'help')
                kwargs = insert_if_exists(kwargs, clip_raw, 'brief', help_value)

                clip = Clip(name, path, **kwargs)
                clips.append(clip)
            except FileNotFoundError as e:
                LOGGER.warn(f"Unable to find clip associate with '{name}'. Skipping...")
                continue
            except Exception as e:
                LOGGER.warn(f"Error loading clip '{clip_raw['name']}'. Skipping...", e)
                continue

        return sorted(clips, key=lambda clip: clip.name)


    def load_clip_group(self, path: Path) -> ClipGroup:
        '''
        Loads a ClipGroup from a given clip file json path.

        Traverses the json file, creates a ClipGroup, populates the metadata, and then traverses the clip objects.
        Clips are built from that data, and added to the ClipGroup. The completed ClipGroup is returned.
        '''

        with open(path) as fd:
            data = json.load(fd)

            try:
                clip_group_name = None
                clip_group_key = None
                clip_group_description = None
                kwargs = {}

                ## Loop over the key-values in the json file. Handle each expected pair appropriately, and store
                ## unexpected pairs in the kwargs variable. Unexpected data is fine, but it needs to be preserved so
                ## that re-saved files will be equivalent to the original file.
                for key, value in data.items():
                    if (key == 'name'):
                        clip_group_name = value
                    elif (key == 'key'):
                        clip_group_key = value
                    elif  (key == 'description'):
                        clip_group_description = value
                    elif (key == 'clips'):
                        clips = self._build_clips(path.parent, value)
                    else:
                        kwargs[key] = value

                ## With the loose pieces processed, make sure the required pieces exist.
                if (clip_group_name == None or clip_group_key == None or clip_group_description == None or len(clips) == 0):
                    LOGGER.warning(f"Error loading clip group '{clip_group_name}', from '{path}'. Missing 'name', 'key', 'description', or non-zero length 'clips' list. Skipping...")
                    return None

                ## Construct the ClipGroup, and add the Clips to it.
                clip_group = ClipGroup(clip_group_name, clip_group_key, clip_group_description, path, **kwargs)
                clip_group.add_all_clips(clips)

                return clip_group
            except Exception as e:
                LOGGER.warning(f"Error loading clip group '{clip_group_name}' from '{path}''. Skipping...", e)
                return None


    def save_clip_group(self, path: Path, clip_group: ClipGroup):
        '''Saves the given ClipGroup as a JSON object at the given path.'''

        data = clip_group.to_dict()

        with open(path, 'w') as fd:
            json.dump(data, fd, indent=4, ensure_ascii=False)
