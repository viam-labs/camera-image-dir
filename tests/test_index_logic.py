# tests/test_index_logic.py
import os
import shutil
import tempfile
import time

import pytest
from PIL import Image
from google.protobuf.struct_pb2 import Struct

from viam.errors import ViamError
from viam.proto.app.robot import ComponentConfig
from src.models.image_dir import imageDir


def write_img(path: str, size=(8, 8)) -> None:
    """Create a tiny image file at `path`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img = Image.new("RGB", size, color=(255, 255, 255))
    img.save(path)


def make_dir_with_images(
    seq_name: str,
    bases,
    ext: str = "jpg",
    set_mtimes=None,
):
    """
    Create a temp root directory, a subdirectory named `seq_name`,
    and write one image per entry in `bases`.

    Returns (root, sub, filenames).
    """
    root = tempfile.mkdtemp(prefix="image-dir-")
    sub = seq_name
    seq_dir = os.path.join(root, sub)
    os.makedirs(seq_dir, exist_ok=True)

    bases = list(bases)
    if set_mtimes is None:
        set_mtimes = [time.time()] * len(bases)
    else:
        set_mtimes = list(set_mtimes)
        # pad if fewer mtimes than bases
        if len(set_mtimes) < len(bases):
            set_mtimes += [set_mtimes[-1]] * (len(bases) - len(set_mtimes))

    filenames = []
    for base, mtime in zip(bases, set_mtimes):
        filename = f"{base}.{ext}"
        path = os.path.join(seq_dir, filename)
        write_img(path)
        os.utime(path, (mtime, mtime))
        filenames.append(filename)

    return root, sub, filenames


def make_config(name: str, root_dir: str, sub_dir: str, ext: str) -> ComponentConfig:
    """Minimal ComponentConfig for imageDir.new."""
    cfg = ComponentConfig()
    cfg.name = name
    attrs = Struct()
    attrs.update(
        {
            "root_dir": root_dir,
            "dir": sub_dir,
            "ext": ext,
        }
    )
    cfg.attributes.CopyFrom(attrs)
    return cfg


# ---- timestamp parsing ----
def test_parse_timestamp_valid():
    cam = imageDir("cam")
    dt = cam._parse_timestamp_from_filename("2025-10-09T15_27_01.690Z_abc.jpeg")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond) == (
        2025,
        10,
        9,
        15,
        27,
        1,
        690_000,
    )

def test_parse_timestamp_invalid():
    cam = imageDir("cam")
    assert cam._parse_timestamp_from_filename("nope.jpeg") is None
    assert cam._parse_timestamp_from_filename("2025-13-99T25_61_61.999Z.jpeg") is None


def test_timestamp_parsing_edge_cases():
    cam = imageDir("cam")
    dt = cam._parse_timestamp_from_filename("2025-01-15T23_59_59Z_test.jpg")
    assert dt and (dt.hour, dt.minute, dt.second) == (23, 59, 59)
    dt = cam._parse_timestamp_from_filename("2024-02-29T12_00_00.000Z_leap.jpg")
    assert dt and (dt.year, dt.month, dt.day) == (2024, 2, 29)
    assert cam._parse_timestamp_from_filename("2025-02-30T12_00_00.000Z_bad.jpg") is None
    # no extension still OK (pattern at start)
    assert cam._parse_timestamp_from_filename("2025-01-15T12_00_00.000Z_noext") is not None


# ---- sorting (timestamp vs index) ----
def test_get_sorted_files_prefers_parsed_timestamp():
    bases = ["2025-10-09T10_00_00.000Z_a", "2025-10-09T09_00_00.000Z_b"]
    later = time.time() + 100
    earlier = time.time() - 100
    root, sub, _ = make_dir_with_images("seq", bases, ext="jpg", set_mtimes=[later, earlier])
    cam = imageDir("cam")
    seq_dir = os.path.join(root, sub)
    files = cam._get_sorted_files(seq_dir, "jpg")
    assert files == ["2025-10-09T09_00_00.000Z_b.jpg", "2025-10-09T10_00_00.000Z_a.jpg"]


def test_get_sorted_files_no_pattern_returns_empty():
    root, sub, _ = make_dir_with_images("nopat", ["img_a", "img_b", "img_c"], ext="jpg")
    cam = imageDir("cam")
    files = cam._get_sorted_files(os.path.join(root, sub), "jpg")
    assert files == []


def test_extension_case_insensitive():
    root, sub, _ = make_dir_with_images("mixed_case", [], ext="jpg")
    target = os.path.join(root, sub)
    # Use numeric names (valid 'idx' pattern); JPEG should be excluded because ext="jpg"
    for name, ext in [("0", "jpg"), ("1", "JPG"), ("2", "Jpg"), ("3", "JPEG")]:
        write_img(os.path.join(target, f"{name}.{ext}"))
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    assert cam.sub_dir_len == 3
    # Ensure index ordering
    assert cam.sorted_files == ["0.jpg", "1.JPG", "2.Jpg"]


# ---- get_image & index mechanics ----
@pytest.mark.asyncio
async def test_get_image_cycles_in_order():
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    await cam.get_image()
    await cam.get_image()
    await cam.get_image()
    await cam.get_image()
    assert cam.directory_index[os.path.join(root, sub)] == 1


@pytest.mark.asyncio
async def test_get_image_rejects_dir_or_ext_override():
    root, sub, _ = make_dir_with_images("seq", ["0", "1"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    with pytest.raises(ViamError):
        await cam.get_image(extra={"dir": "other"})
    with pytest.raises(ViamError):
        await cam.get_image(extra={"ext": "png"})


@pytest.mark.asyncio
async def test_get_image_index_jog_reset_direct():
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image()
    await cam.get_image()
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index_jog": -1})
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index_reset": True})
    assert cam.directory_index[seq_path] == 1

    await cam.get_image(extra={"index": 5})  # 5 % 3 = 2 -> inc -> 0
    assert cam.directory_index[seq_path] == 0


@pytest.mark.asyncio
async def test_index_priority_order():
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2", "3", "4"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image(extra={"index": 3, "index_reset": True, "index_jog": 2})
    assert cam.directory_index[seq_path] == 4

    await cam.get_image(extra={"index_reset": True, "index_jog": 2})
    assert cam.directory_index[seq_path] == 1


@pytest.mark.asyncio
async def test_negative_and_large_indices():
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image(extra={"index": -1})  # -> 2
    assert cam.directory_index[seq_path] == 0

    await cam.get_image(extra={"index": 100})  # -> 1
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index": 0})
    assert cam.directory_index[seq_path] == 1


# ---- get_images & do_command ----
@pytest.mark.asyncio
async def test_get_images_filters_by_source_name():
    root, sub, _ = make_dir_with_images("seqA", ["0", "1"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    images, _ = await cam.get_images(filter_source_names=["other"])
    assert images == []
    images, _ = await cam.get_images(filter_source_names=[sub])
    assert len(images) == 1 and images[0].name == sub and hasattr(images[0], "data")


@pytest.mark.asyncio
async def test_do_command_set_index_reset_and_jog():
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})

    out = await cam.do_command({"set": {"index": 5}})
    assert out["index"] == 2

    out = await cam.do_command({"set": {"index_reset": True}})
    assert out["index"] == 0

    out = await cam.do_command({"set": {"index_jog": -1}})
    assert out["index"] == 2


@pytest.mark.asyncio
async def test_do_command_invalid_operations():
    root, sub, _ = make_dir_with_images("seq", ["0", "1"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    with pytest.raises(ViamError):
        await cam.do_command({"set": {"dir": "other"}})
    with pytest.raises(ViamError):
        await cam.do_command({"set": {"ext": "png"}})
    assert await cam.do_command({}) == {}
    assert await cam.do_command({"unknown": "command"}) == {}


# ---- runtime edge case (dir removed after config) ----
@pytest.mark.asyncio
async def test_directory_deleted_after_config():
    root, sub, _ = make_dir_with_images("seq", ["0"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    shutil.rmtree(os.path.join(root, sub))
    with pytest.raises(ViamError, match=r"directory no longer exists"):
        await cam.get_image()


# ---- perf ----
@pytest.mark.asyncio
async def test_large_directory_performance():
    # numeric suffixes → accepted by sorter
    bases = [f"img_{i:04d}" for i in range(100)]  # trailing digits OK (idx mode)
    root, sub, _ = make_dir_with_images("large", bases, ext="jpg")

    start = time.time()
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    assert cam.sub_dir_len == 100
    assert time.time() - start < 1.0

    start = time.time()
    await cam.get_image(extra={"index": 50})
    assert time.time() - start < 0.1


# ---- mixed timestamp + regular (regular are skipped) ----
def test_mixed_timestamp_and_regular_files_sort():
    bases = [
        "2025-01-01T12_00_00.000Z_a",
        "regular_image",  # skipped
        "2025-01-01T08_00_00.000Z_b",
    ]
    root, sub, _ = make_dir_with_images("mixed", bases, ext="jpg")
    cam = imageDir("cam")
    files = cam._get_sorted_files(os.path.join(root, sub), "jpg")
    assert files == [
        "2025-01-01T08_00_00.000Z_b.jpg",
        "2025-01-01T12_00_00.000Z_a.jpg",
    ]
