# tests/test_config.py
import os
import shutil
import time
import tempfile
from typing import Iterable
import pytest
from PIL import Image
from unittest.mock import Mock
from google.protobuf.struct_pb2 import Struct
from viam.proto.app.robot import ComponentConfig
from viam.errors import ViamError
from src.models.image_dir import imageDir


# ----------------------- Fixtures (shared) -----------------------
def _write_img(path: str, size=(8, 8)):
    img = Image.new("RGB", size, (200, 100, 50))
    img.save(path)


def _set_mtime(path: str, ts: float):
    os.utime(path, (ts, ts))


@pytest.fixture
def write_img():
    return _write_img


@pytest.fixture
def temp_root():
    d = tempfile.mkdtemp(prefix="imgdir_")
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def make_config():
    def _mk(name: str, root_dir: str, sub_dir: str, ext: str = "jpg"):
        cfg = Mock(spec=ComponentConfig)
        cfg.name = name
        s = Struct()
        s.fields["root_dir"].string_value = root_dir
        s.fields["dir"].string_value = sub_dir
        s.fields["ext"].string_value = ext
        cfg.attributes = s
        return cfg

    return _mk


@pytest.fixture
def make_dir_with_images(temp_root):
    """Create a subdir with images; optional per-file mtimes."""

    def _mk(sub_dir: str, bases: Iterable[str], ext: str = "jpg", set_mtimes=None):
        target = os.path.join(temp_root, sub_dir)
        os.makedirs(target, exist_ok=True)
        out = []
        now = time.time()
        for i, base in enumerate(bases):
            fname = f"{base}.{ext}"
            fpath = os.path.join(target, fname)
            _write_img(fpath)
            ts = set_mtimes[i] if set_mtimes is not None and i < len(set_mtimes) else now + i
            _set_mtime(fpath, ts)
            out.append(fname)
        return temp_root, sub_dir, out

    return _mk


# ----------------------- Config / reconfigure tests -----------------------
def test_validate_config_root_missing(make_config):
    cfg = make_config("cam", root_dir="/does/not/exist", sub_dir="seq", ext="jpg")
    errors, warnings = imageDir.validate_config(cfg)
    assert any("root_dir" in e for e in errors)
    assert warnings == []


def test_validate_config_ext_unsupported(temp_root, make_config):
    cfg = make_config("cam", root_dir=temp_root, sub_dir="seq", ext="tif")
    errors, _ = imageDir.validate_config(cfg)
    assert any("unsupported 'ext'" in e for e in errors)


def test_validate_config_dir_required(temp_root, make_config):
    cfg = make_config("cam", root_dir=temp_root, sub_dir="", ext="jpg")
    errors, _ = imageDir.validate_config(cfg)
    assert any("'dir' is required" in e for e in errors)


def test_validate_config_dir_not_found(temp_root, make_config):
    cfg = make_config("cam", root_dir=temp_root, sub_dir="missing", ext="jpg")
    errors, _ = imageDir.validate_config(cfg)
    assert any("requested 'dir' not found" in e for e in errors)


def test_validate_config_no_matching_files(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1"], ext="png")
    cfg = make_config("cam", root, sub, ext="jpg")
    errors, _ = imageDir.validate_config(cfg)
    assert any("no files ending with .jpg" in e for e in errors)


def test_reconfigure_happy_path(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    assert cam.root_dir == root
    assert cam.sub_dir == sub
    assert cam.ext == "jpg"
    assert cam.sub_dir_len == 3
    assert len(cam.sorted_files) == 3
    assert cam.directory_index == {os.path.join(root, sub): 0}


def test_reconfigure_raises_when_no_images(temp_root, make_config):
    empty_sub = "empty"
    os.makedirs(os.path.join(temp_root, empty_sub), exist_ok=True)
    cam = imageDir("cam")
    with pytest.raises(ViamError, match=r"No images with valid timestamp or numeric index"):
        cam.reconfigure(make_config("cam", temp_root, empty_sub, "jpg"), {})


@pytest.mark.asyncio
async def test_get_properties(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    props = await cam.get_properties()
    assert props.supports_pcd is False
    assert props.intrinsic_parameters is None
    assert props.distortion_parameters is None
