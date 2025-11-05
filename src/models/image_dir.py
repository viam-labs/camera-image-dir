from typing import ClassVar, Mapping, Any, Dict, Optional, Tuple, List, Sequence
from typing_extensions import Self

from typing import NamedTuple

from PIL import Image

from viam.media.video import NamedImage
from viam.proto.common import ResponseMetadata

from viam.module.types import Reconfigurable
from viam.proto.app.robot import ComponentConfig
from viam.proto.common import ResourceName
from viam.resource.base import ResourceBase
from viam.resource.types import Model, ModelFamily
from viam.components.component_base import ValueTypes

from viam.components.camera import Camera, ViamImage
from viam.media.utils.pil import pil_to_viam_image, CameraMimeType

from viam.logging import getLogger
from viam.errors import ViamError

import os
from datetime import datetime, timezone

import re

LOGGER = getLogger(__name__)
SUPPORTED_EXTS = {"jpg", "jpeg", "png", "gif"}


class imageDir(Camera, Reconfigurable):
    class Properties(NamedTuple):
        supports_pcd: bool = False
        intrinsic_parameters: Optional[Any] = None
        distortion_parameters: Optional[Any] = None

    MODEL: ClassVar[Model] = Model(ModelFamily("viam-labs", "camera"), "image-dir")

    camera_properties: Camera.Properties = Properties()
    # will store current get_image index for a given directory here
    directory_index: dict
    root_dir: str = "/tmp"
    ext: str = "jpg"
    dir: str
    
    # Precomputed once per configured (dir, ext)
    sorted_files: List[str]
    dir_len: int
    
    # Constructor
    @classmethod
    def new(
        cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]
    ) -> Self:
        my_class = cls(config.name)
        my_class.reconfigure(config, dependencies)
        return my_class
    
    @classmethod
    def validate_config(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        """Validates  the configuration for the imageDir camera."""
        errors: List[str] = []
        warnings: List[str] = []

        attrs = config.attributes.fields
        root_dir = attrs.get("root_dir").string_value if "root_dir" in attrs else "/tmp"
        ext = attrs.get("ext").string_value if "ext" in attrs else "jpg"
        sub_dir = attrs.get("dir").string_value if "dir" in attrs else ""

        # Root dir must exist
        if not os.path.isdir(root_dir):
            errors.append(f"specified 'root_dir' does not exist: {root_dir}")

        # ext must be supported
        if ext and ext.lower() not in SUPPORTED_EXTS:
            errors.append(f"unsupported 'ext': {ext}. Supported: {sorted(SUPPORTED_EXTS)}")

        # dir must be provided
        if not sub_dir:
            errors.append("'dir' is required and must be a subdirectory of 'root_dir'")

        # If prior checks passed, validate the requested directory and files
        if not errors:
            requested_dir = os.path.join(root_dir, sub_dir)
            if not os.path.isdir(requested_dir):
                errors.append(f"requested 'dir' not found within configured 'root_dir': {requested_dir}")
            else: 
                files = [f for f in os.listdir(requested_dir) if f.lower().endswith(f".{ext.lower()}")]
                if not files:
                    errors.append(f"no files ending with .{ext} found in {requested_dir}")

        return errors, warnings
    # Handles attribute reconfiguration
    def reconfigure(
        self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]
    ):
        attrs = config.attributes.fields
        self.root_dir = attrs.get("root_dir").string_value if "root_dir" in attrs else "/tmp"
        self.ext = (attrs.get("ext").string_value if "ext" in attrs else "jpg").lower()
        self.dir = attrs.get("dir").string_value if "dir" in attrs else ""

        requested_dir = os.path.join(self.root_dir, self.dir) if self.dir else self.root_dir
        
        # Build the fixed, chronologically sorted image list (files in the directory) once
        self.sorted_files = self._get_sorted_files(requested_dir, self.ext)
        self.dir_len = len(self.sorted_files)
        
        if self.dir_len == 0:
            raise ViamError(f"No images with .{self.ext} found in {requested_dir}")
        
        self.directory_index = {requested_dir: 0}
    async def get_image(
        self,
        mime_type: str = "image/jpeg",
        *,
        timeout: Optional[float] = None,
        extra: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **kwargs,
    ) -> ViamImage:
        extra = extra or {}
        # Fixed image list from configured directory; do not allow per-call dir/ext overrides.
        if extra.get("dir") and extra["dir"] != self.dir:
            raise ViamError("Per-call 'dir' override not supported; reconfigure instead.")
        if extra.get("ext") and extra["ext"].lower() != self.ext:
            raise ViamError("Per-call 'ext' override not supported; reconfigure instead.")

        requested_dir = os.path.join(self.root_dir, self.dir)
        if not os.path.isdir(requested_dir):
            raise ViamError("configured directory no longer exists")
        if self.dir_len == 0 or not self.sorted_files:
            raise ViamError("No images preloaded. Did reconfigure fail?")

        image_index: int
        
        if extra.get("index") is not None:
            image_index = extra["index"]

        if extra.get("index_reset") is not None and extra["index_reset"]:
            image_index = 0

        if extra.get("index_jog") is not None:
            image_index = self._jog_index(extra["index_jog"], requested_dir)
        elif self.directory_index.get(requested_dir) is not None:
            image_index = self.directory_index[requested_dir]
        else:
            image_index = 0
            
        # Wrap with precomputed length and use precomputed sorted list
        n = self.dir_len
        image_index = int(image_index) % n
        
        file_path = os.path.join(requested_dir, self.sorted_files[image_index])
        if not os.path.isfile(file_path):
            raise ViamError(f"Image file not found: {file_path}")
        
        # Open safely and convert to ViamImage
        with Image.open(file_path) as img:
            vi_img = pil_to_viam_image(img.convert("RGB"), CameraMimeType.from_string(mime_type))
        
        # Increment safely with modulo 
        self.directory_index[requested_dir] = (image_index + 1) % n 
        
        return vi_img
    

    def _parse_timestamp_from_filename(self, filename: str) -> Optional[datetime]:
        """
        Parse timestamp from filename format:
        2025-10-09T15_27_01.690Z_<hash>.jpeg

        Returns None if no timestamp can be parsed.
        """
        # Remove extension
        name_without_ext = os.path.splitext(filename)[0]

        # Pattern: YYYY-MM-DDTHH_MM_SS.mmmZ
        # Example: 2025-10-09T15_27_01.690Z
        pattern = r"^(\d{4})-(\d{2})-(\d{2})T(\d{2})_(\d{2})_(\d{2})\.(\d{3})Z"
        if match := re.match(pattern, name_without_ext):
            year, month, day, hour, minute, second, millisecond = match.groups()
            try:
                return datetime(
                    int(year),
                    int(month),
                    int(day),
                    int(hour),
                    int(minute),
                    int(second),
                    int(millisecond)
                    * 1000,  # Convert milliseconds to microseconds
                    tzinfo=timezone.utc,
                )
            except ValueError as e:
                LOGGER.warning(f"Failed to parse timestamp from {filename}: {e}")
                return None

        return None

    def _get_sorted_files(self, dir, ext):
        """
        Get all files in directory, sorted by timestamp (if parseable) or mtime.
        Returns list of filenames in chronological order.
        """
        files = [f for f in os.listdir(dir) if f.lower().endswith(f".{ext.lower()}")]
        if not files:
            return []

        # Sort by parsed timestamp, fall back to mtime
        def sort_key(f):
            parsed_time = self._parse_timestamp_from_filename(f)
            if parsed_time:
                return parsed_time.timestamp()
            # Fall back to file modification time
            return os.stat(os.path.join(dir, f)).st_mtime

        files.sort(key=sort_key)
        return files

    def _jog_index(self, index_jog, requested_dir):
        n = self.dir_len if self.dir_len > 0 else 1
        current_index = self.directory_index.get(requested_dir, 0) % n
        
        return (current_index + int(index_jog)) % n

    async def get_images(
        self,
        *,
        timeout: Optional[float] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        extra: Optional[Mapping[str, Any]] = None,
        filter_source_names: Optional[List[str]] = None,
        **kwargs,
    ) -> Tuple[List[NamedImage], ResponseMetadata]:
        if extra is None:
            extra = {}

        # Determine source name
        source_name = self.dir or ""

        # Apply filtering if specified
        if filter_source_names is not None and len(filter_source_names) > 0 and source_name not in filter_source_names:
            return [], ResponseMetadata()

        # Get the image
        image = await self.get_image(extra=extra, timeout=timeout)

        # Create NamedImage
        named_image = NamedImage(name=source_name, data=image.data, mime_type=image.mime_type)

        # Return with metadata
        return [named_image], ResponseMetadata()

    async def get_point_cloud(
        self, *, extra: Optional[Dict[str, Any]] = None, timeout: Optional[float] = None, **kwargs
    ) -> Tuple[bytes, str]:
        raise NotImplementedError()

    # Implements the do_command which will respond to a map with key "request"
    async def do_command(
        self, command: Mapping[str, ValueTypes], *, timeout: Optional[float] = None, **kwargs
    ) -> Mapping[str, ValueTypes]:
        ret = {}
        if command.get("set") is not None:
            setDict = command.get("set")
            # Keep image list fixed; require reconfigure() to change dir/ext
            if setDict.get("dir") is not None or setDict.get("ext") is not None:
                raise ViamError("Changing 'dir' or 'ext' requires a reconfigure.")
            
            requested_dir = os.path.join(self.root_dir, self.dir)
            
            if setDict.get("index") is not None:
                n = max(1, self.dir_len)
                self.directory_index[requested_dir] = setDict["index"] % n
                ret = {"index": self.directory_index[requested_dir]}
                
            if setDict.get("index_reset") is not None and setDict["index_reset"]:
                self.directory_index[requested_dir] = 0
                ret = {"index": 0}
            
            if setDict.get("index_jog") is not None:
                idx = self._jog_index(setDict["index_jog"], requested_dir)
                self.directory_index[requested_dir] = idx
                ret = {"index": idx}

        return ret

    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> Properties:
        return self.camera_properties
