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


class imageDir(Camera, Reconfigurable):
    class Properties(NamedTuple):
        supports_pcd: bool = False
        intrinsic_parameters = None
        distortion_parameters = None

    MODEL: ClassVar[Model] = Model(ModelFamily("viam-labs", "camera"), "image-dir")

    camera_properties: Camera.Properties = Properties()
    # will store current get_image index for a given directory here
    directory_index: dict
    root_dir: str = "/tmp"
    ext: str = "jpg"
    dir: str

    # Constructor
    @classmethod
    def new(
        cls, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]
    ) -> Self:
        my_class = cls(config.name)
        my_class.reconfigure(config, dependencies)
        return my_class

    # Validates JSON Configuration
    @classmethod
    def validate(cls, config: ComponentConfig) -> Tuple[Sequence[str], Sequence[str]]:
        errors = []
        warnings = []

        root_dir = config.attributes.fields["root_dir"].string_value or "/tmp"
        if not os.path.isdir(root_dir):
            errors.append("specified 'root_dir' does not exist")

        # No implicit dependencies for this camera
        return errors, warnings

    # Handles attribute reconfiguration
    def reconfigure(
        self, config: ComponentConfig, dependencies: Mapping[ResourceName, ResourceBase]
    ):
        self.directory_index = {}
        self.root_dir = config.attributes.fields["root_dir"].string_value or "/tmp"
        self.ext = config.attributes.fields["ext"].string_value or "jpg"
        self.dir = config.attributes.fields["dir"].string_value

    async def get_image(
        self,
        mime_type: str = "image/jpeg",
        *,
        timeout: Optional[float] = None,
        extra: Optional[Mapping[str, Any]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        **kwargs,
    ) -> ViamImage:
        if extra is None:
            extra = {}

        if extra.get("dir") is None:
            if self.dir is None:
                raise ViamError(
                    "'dir' must be passed in with 'extra', specifying image directory relative to the configured 'root_dir'"
                )
            else:
                extra["dir"] = self.dir

        if len(mime_type) == 0:
            LOGGER.warning("mime_type is empty, setting to image/jpeg")
            mime_type = CameraMimeType.JPEG

        requested_dir = os.path.join(self.root_dir, extra["dir"])

        if not os.path.isdir(requested_dir):
            raise ViamError("requested 'dir' not found within configured 'root_dir'")

        image_index: int
        if extra.get("index") is not None:
            image_index = extra["index"]

        if extra.get("index_reset") is not None:
            if extra["index_reset"]:
                # reset
                image_index = self._get_oldest_image_index(requested_dir)

        if extra.get("index_jog") is not None:
            image_index = self._jog_index(extra["index_jog"], requested_dir)
        elif self.directory_index.get(requested_dir) is not None:
            image_index = self.directory_index[requested_dir]
        else:
            image_index = self._get_oldest_image_index(requested_dir)

        ext = self.ext
        if extra.get("ext") is not None:
            if extra["ext"] in ["jpg", "jpeg", "png", "gif"]:
                ext = extra["ext"]
        LOGGER.info(f"ext: {ext}")

        # Get max index to handle wraparound
        max_index = self._get_greatest_image_index(requested_dir)

        # Wrap around if we've gone past the end
        if image_index > max_index:
            image_index = 0
            LOGGER.info("Reached end of directory, wrapping to index 0")

        file_path = self._get_file_path(requested_dir, image_index, ext)
        if not os.path.isfile(file_path):
            if extra.get("index"):
                # specific index was requested, if it does not exist return error
                raise ViamError("Image does not request at specified index")
            else:
                # loop back to 0 index, we might be at the last image in dir
                image_index = 0
                file_path = os.path.join(requested_dir, str(image_index) + "." + ext)
                if not os.path.isfile(file_path):
                    raise ViamError("No image at 0 index for " + file_path)

        img = Image.open(file_path)
        # LOGGER.info(f"Serving image {file_path} for index {image_index}")

        # increment for next get_image() call
        self.directory_index[requested_dir] = image_index + 1

        return pil_to_viam_image(img.convert("RGB"), CameraMimeType.from_string(mime_type))

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
        match = re.match(pattern, name_without_ext)

        if match:
            year, month, day, hour, minute, second, millisecond = match.groups()
            try:
                dt = datetime(
                    int(year),
                    int(month),
                    int(day),
                    int(hour),
                    int(minute),
                    int(second),
                    int(millisecond) * 1000,  # Convert milliseconds to microseconds
                    tzinfo=timezone.utc,
                )
                return dt
            except ValueError as e:
                LOGGER.warning(f"Failed to parse timestamp from {filename}: {e}")
                return None

        return None

    def _get_sorted_files(self, dir, ext):
        """
        Get all files in directory, sorted by timestamp (if parseable) or mtime.
        Returns list of filenames in chronological order.
        """
        files = [f for f in os.listdir(dir) if f.endswith(f".{ext}")]
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

    def _get_file_path(self, dir, index, ext):
        """Get file path by index from sorted file list."""
        sorted_files = self._get_sorted_files(dir, ext)
        if not sorted_files or index >= len(sorted_files):
            return None
        return os.path.join(dir, sorted_files[index])

    def _get_oldest_image_index(self, requested_dir):
        """Return index 0 (oldest file after sorting)."""
        return 0

    def _get_greatest_image_index(self, requested_dir):
        """Get the maximum valid index (count of files - 1)."""
        sorted_files = self._get_sorted_files(requested_dir, self.ext)
        if not sorted_files:
            return 0
        return len(sorted_files) - 1

    def _jog_index(self, index_jog, requested_dir):
        """Move index forward or backward, wrapping around."""
        current_index = self.directory_index.get(requested_dir, 0)
        requested_index = current_index + index_jog
        max_index = self._get_greatest_image_index(requested_dir)

        if max_index == 0:
            return 0

        # Wrap around if out of bounds
        return requested_index % (max_index + 1)

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
        if filter_source_names is not None and len(filter_source_names) > 0:
            # If filtering is requested and our source isn't in the list, return empty
            if source_name not in filter_source_names:
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
            if setDict.get("dir") is not None:
                self.dir = setDict.get("dir")
            requested_dir = os.path.join(self.root_dir, self.dir)
            if setDict.get("ext") is not None:
                self.ext = setDict.get("ext")
            if setDict.get("index") is not None:
                if isinstance(setDict["index"], int):
                    self.directory_index[requested_dir] = setDict["index"]
            if setDict.get("index_reset") is not None:
                if setDict["index_reset"]:
                    # reset
                    index = self._get_oldest_image_index(requested_dir)
                    self.directory_index[requested_dir] = index
                    ret = {"index": index}
            if setDict.get("index_jog") is not None:
                index = self._jog_index(setDict["index_jog"], requested_dir)
                self.directory_index[requested_dir] = index
                ret = {"index": index}
        return ret

    async def get_properties(self, *, timeout: Optional[float] = None, **kwargs) -> Properties:
        return self.camera_properties
