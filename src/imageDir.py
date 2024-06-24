from typing import ClassVar, Mapping, Sequence, Any, Dict, Optional, Tuple, Final, List, cast
from typing_extensions import Self

from typing import Any, Dict, Final, List, NamedTuple, Optional, Tuple, Union

from PIL import Image

from viam.media.video import NamedImage
from viam.proto.common import ResponseMetadata

from viam.module.types import Reconfigurable
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.components.component_base import ValueTypes

from viam.components.camera import Camera
from viam.media.video import ViamImage, CameraMimeType
from viam.media.utils.pil import pil_to_viam_image

from viam.logging import getLogger
from viam.errors import ViamError, NotSupportedError
from viam.media.video import CameraMimeType

import os
import io

LOGGER = getLogger(__name__)

class imageDir(Camera, Reconfigurable):

    class Properties(NamedTuple):
        supports_pcd: bool = False
        intrinsic_parameters = None
        distortion_parameters = None

    MODEL: ClassVar[Model] = Model(ModelFamily("viam-labs", "camera"), "image-dir")
    
    camera_properties: Camera.Properties = Properties()
    # will store current get_image index for a given directory here
    directory_index: dict
    root_dir: str = '/tmp'
    ext: str = 'jpg'
    dir: str

    # Constructor
    @classmethod
    def new(cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]) -> Self:
        my_class = cls(config.name)
        my_class.reconfigure(config, dependencies)
        return my_class

    # Validates JSON Configuration
    @classmethod
    def validate(cls, config: ComponentConfig):
        root_dir = config.attributes.fields["root_dir"].string_value or '/tmp'
        if not os.path.isdir(root_dir):
            raise Exception("specified 'root_dir' does not exist")
        return

    # Handles attribute reconfiguration
    def reconfigure(self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]):
        self.directory_index = {}
        self.root_dir = config.attributes.fields["root_dir"].string_value or '/tmp'
        self.dir = config.attributes.fields["dir"].string_value

        return
    
    async def get_image(
        self, mime_type: str = "", *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> ViamImage:
        if extra.get('dir') == None:
            if self.dir == None:
                raise ViamError("'dir' must be passed in with 'extra', specifying image directory relative to the configured 'root_dir'")
            else:
                extra['dir'] = self.dir
        requested_dir = os.path.join(self.root_dir, extra['dir'])
        if not os.path.isdir(requested_dir):
            raise ViamError("requested 'dir' not found within configured 'root_dir'")
        image_index: int
        if extra.get('index') != None:
            image_index = extra['index']
        if extra.get('index_reset') != None:
            if extra['index_reset'] == True:
                # reset
                image_index = self._get_oldest_image_index(requested_dir)
        if extra.get('index_jog') != None:
            image_index = self._jog_index(extra['index_jog'], requested_dir)
        elif self.directory_index.get(requested_dir) != None:
            image_index = self.directory_index[requested_dir]
        else:
            image_index = self._get_oldest_image_index(requested_dir)
    
        ext = self.ext
        if extra.get('ext') != None:
            if extra['ext'] in ['jpg', 'jpeg', 'png', 'gif']:
                ext = extra['ext']
        file_path = self._get_file_path(requested_dir, image_index, ext)
        if not os.path.isfile(file_path):
            if extra.get("index"):
                # specific index was requested, if it does not exist return error
                raise ViamError("Image does not request at specified index")
            else:
                # loop back to 0 index, we might be at the last image in dir
                image_index = 0
                file_path = os.path.join(requested_dir, str(image_index) + '.' + ext)
                if not os.path.isfile(file_path):
                    raise ViamError("No image at 0 index for " + file_path)
        img = Image.open(file_path)

        # increment for next get_image() call
        self.directory_index[requested_dir] = image_index + 1
            
        return pil_to_viam_image(img.convert('RGB'), CameraMimeType.JPEG)


    def _get_file_path(self, dir, index, ext):
        return os.path.join(dir, str(index) + '.' + ext)
    
    def _get_oldest_image_index(self, requested_dir):
        mtime = lambda f: os.stat(os.path.join(requested_dir, f)).st_mtime
        return int(os.path.splitext(list(sorted(os.listdir(requested_dir), key=mtime))[0])[0])
    
    def _get_greatest_image_index(self, requested_dir):
        index = lambda f: os.path.basename(os.path.splitext(os.path.join(requested_dir, f))[0])
        return int(os.path.splitext(list(sorted(os.listdir(requested_dir), key=index))[0])[0])
    
    def _jog_index(self, index_jog, requested_dir):
        requested_index = self.directory_index[requested_dir] + index_jog
        max_index = self._get_greatest_image_index(requested_dir)
        if requested_index < 0:
            return max_index - requested_index
        elif requested_index > max_index:
            return requested_index - max_index - 1

    async def get_images(self, *, timeout: Optional[float] = None, **kwargs) -> Tuple[List[NamedImage], ResponseMetadata]:
        raise NotImplementedError()

    async def get_point_cloud(
        self, *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Tuple[bytes, str]:
        raise NotImplementedError()

    # Implements the do_command which will respond to a map with key "request"
    async def do_command(self, command: Mapping[str, ValueTypes], *,
                         timeout: Optional[float] = None,
                         **kwargs) -> Mapping[str, ValueTypes]:
        ret = {}
        if command.get('set') != None:
            setDict = command.get('set')
            if setDict.get('dir') != None:
                self.dir = setDict.get('dir')
            requested_dir = os.path.join(self.root_dir, self.dir)
            if setDict.get('ext') != None:
                self.ext = setDict.get('ext')
            if setDict.get('index') != None:
                if isinstance(setDict['index'], int):
                    self.directory_index[requested_dir] = setDict['index']
            if setDict.get('index_reset') != None:
                if setDict['index_reset'] == True:
                    # reset
                    index = self._get_oldest_image_index(requested_dir)
                    self.directory_index[requested_dir]  = index
                    ret = { "index" : index }
            if setDict.get('index_jog') != None:
                index = self._jog_index(setDict['index_jog'], requested_dir)
                self.directory_index[requested_dir] = index
                ret = { "index" : index }
        return ret
    
    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> Properties:
       return self.camera_properties

