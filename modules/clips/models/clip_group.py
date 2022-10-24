import copy
import json
import logging
from pathlib import Path
from typing import List

from common.logging import Logging
from modules.clips.models.clip import Clip
from modules.clips.to_dict import ToDict

## Logging
LOGGER = Logging.initialize_logging(logging.getLogger(__name__))


class ClipGroup(ToDict):
    def __init__(self, name: str, key: str, description: str, path: Path, **kwargs):
        self.name = name
        self.key = key
        self.description = description
        self.path = path
        self.kwargs = kwargs

        self.clips = {}

    ## Methods

    def add_clip(self, clip: Clip):
        self.clips[clip.name] = clip


    def add_all_clips(self, clips: List[Clip]):
        for clip in clips:
            self.add_clip(clip)


    def to_dict(self) -> dict:
        data = super().to_dict()

        del data['path']
        del data['kwargs']

        for key, value in self.kwargs.items():
            data[key] = value

        data['clips'] = [clip.to_dict() for clip in self.clips.values()]

        return data
