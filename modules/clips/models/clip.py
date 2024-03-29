import logging
from pathlib import Path

from common.logging import Logging
from modules.clips.to_dict import ToDict

## Logging
LOGGER = Logging.initialize_logging(logging.getLogger(__name__))

class Clip(ToDict):
    def __init__(self, name: str, path: Path, **kwargs):
        self.name = name
        self.path = path
        self.help = kwargs.get('help')
        self.brief = kwargs.get('brief')
        self.description = kwargs.get('description')
        self._derived_description = kwargs.get('derived_description', False)
        self.is_music = kwargs.get('is_music', False)
        self.kwargs = kwargs


    def __str__(self):
        return f"{self.name} - {self.__dict__}"

    ## Methods

    def to_dict(self) -> dict:
        data = super().to_dict()

        del data['kwargs']

        if (self.encoded != True):
            del data['encoded']
        if (self.help is None):
            del data['help']
        if (self.brief is None):
            del data['brief']
        if (not self.is_music):
            del data['is_music']
        if (self._derived_description and 'description' in data):
            del data['description']

        return data
