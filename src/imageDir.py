from typing import ClassVar, Mapping, Sequence, Any, Dict, Optional, Tuple, Final, List, cast
from typing_extensions import Self

from typing import Any, Dict, Final, List, NamedTuple, Optional, Tuple, Union

from PIL.Image import Image

from viam.media.video import NamedImage
from viam.proto.common import ResponseMetadata


from viam.components.camera import DistortionParameters, IntrinsicParameters, RawImage



from viam.module.types import Reconfigurable
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName, Vector3
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily

from viam.components.camera import Camera
from viam.logging import getLogger
from viam.errors import NotImplementedError, ViamError, NotSupportedError
from viam.media.video import CameraMimeType

import time
import asyncio
import os
import io

LOGGER = getLogger(__name__)

class imageDir(Camera, Reconfigurable):

    class Properties(NamedTuple):
        supports_pcd: bool = False
        intrinsic_parameters: IntrinsicParameters
        distortion_parameters: DistortionParameters    

    MODEL: ClassVar[Model] = Model(ModelFamily("viam-labs", "camera"), "image-dir")
    
    camera_properties: Camera.Properties
    # will store current get_image index for a given directory here
    directory_index: dict
    root_dir: str = '/tmp'
    ext: str = 'jpg'

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
        return
    
    async def get_image(
        self, mime_type: str = "", *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Union[Image, RawImage]:
        if extra.get('dir') == None:
            raise ViamError("'dir' must be passed in with 'extra', specifying image directory relative to the configured 'root_dir'")
        requested_dir = os.path.join(self.root_dir, extra['dir'])
        if not os.path.isdir(requested_dir):
            raise ViamError("requested 'dir' not found within configured 'root_dir'")
        image_index: int
        if extra.get('index') != None:
            if isinstance(extra['index'], int):
                if extra['index'] == -1:
                    # reset
                    image_index = self._get_oldest_image_index(requested_dir)
                else:
                    image_index = extra['index']
        elif self.directory_index.get(requested_dir) != None:
            image_index = self.directory_index[requested_dir]
        else:
            image_index = self._get_oldest_image_index(requested_dir)
    
        ext = self.ext
        if extra.get('ext') != None:
            if extra['ext'] in ['jpg', 'jpeg', 'png', 'gif']:
                ext = extra['ext']
        file_path = os.path.join(requested_dir, str(image_index) + '.' + ext)
        img: Image = Image.open(file_path)
        if (mime_type == None) or (mime_type == CameraMimeType.JPEG):
            return img.convert('RGB')
        elif mime_type == CameraMimeType.VIAM_RGBA:
            buf = io.BytesIO()
            img.save(buf, format='JPEG')
            byte_im = buf.getvalue()
            return RawImage(byte_im, CameraMimeType.JPEG)
        else:
            raise NotSupportedError("mime_type not supported")

    def _get_oldest_image_index(self, requested_dir):
        mtime = lambda f: os.stat(os.path.join(requested_dir, f)).st_mtime
        return os.path.splitext(list(sorted(os.listdir(requested_dir), key=mtime))[0])[0]

    async def get_images(self, *, timeout: Optional[float] = None, **kwargs) -> Tuple[List[NamedImage], ResponseMetadata]:
        raise NotImplementedError()


    
    async def get_point_cloud(
        self, *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Tuple[bytes, str]:
        raise NotImplementedError()


    
    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> Properties:
       return self.camera_properties

