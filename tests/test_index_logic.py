# tests/test_index_logic.py
import os, time, shutil, pytest
from viam.errors import ViamError
from src.models.image_dir import imageDir
from .test_config import make_config, temp_root, make_dir_with_images, write_img 

# ---- timestamp parsing ----
def test_parse_timestamp_valid():
    cam = imageDir("cam")
    dt = cam._parse_timestamp_from_filename("2025-10-09T15_27_01.690Z_abc.jpeg")
    assert dt is not None
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second, dt.microsecond) == \
           (2025, 10, 9, 15, 27, 1, 690_000)

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
def test_get_sorted_files_prefers_parsed_timestamp(make_dir_with_images):
    bases = ["2025-10-09T10_00_00.000Z_a", "2025-10-09T09_00_00.000Z_b"]
    later = time.time() + 100
    earlier = time.time() - 100
    root, sub, _ = make_dir_with_images("seq", bases, ext="jpg", set_mtimes=[later, earlier])
    cam = imageDir("cam")
    seq_dir = os.path.join(root, sub)
    files = cam._get_sorted_files(seq_dir, "jpg")
    assert files == ["2025-10-09T09_00_00.000Z_b.jpg", "2025-10-09T10_00_00.000Z_a.jpg"]

def test_get_sorted_files_no_pattern_returns_empty(make_dir_with_images):
    root, sub, _ = make_dir_with_images("nopat", ["img_a", "img_b", "img_c"], ext="jpg")
    cam = imageDir("cam")
    files = cam._get_sorted_files(os.path.join(root, sub), "jpg")
    assert files == []

def test_extension_case_insensitive(make_dir_with_images, make_config, write_img):
    root, sub, _ = make_dir_with_images("mixed_case", [], ext="jpg")
    target = os.path.join(root, sub)
    # Use numeric names (valid 'idx' pattern); JPEG should be excluded because ext="jpg"
    for name, ext in [("0", "jpg"), ("1", "JPG"), ("2", "Jpg"), ("3", "JPEG")]:
        write_img(os.path.join(target, f"{name}.{ext}"))
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    assert cam.dir_len == 3
    # Ensure index ordering
    assert cam.sorted_files == ["0.jpg", "1.JPG", "2.Jpg"]

# ---- get_image & index mechanics ----
@pytest.mark.asyncio
async def test_get_image_cycles_in_order(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    await cam.get_image(); await cam.get_image(); await cam.get_image(); await cam.get_image()
    assert cam.directory_index[os.path.join(root, sub)] == 1

@pytest.mark.asyncio
async def test_get_image_rejects_dir_or_ext_override(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    with pytest.raises(ViamError):
        await cam.get_image(extra={"dir": "other"})
    with pytest.raises(ViamError):
        await cam.get_image(extra={"ext": "png"})

@pytest.mark.asyncio
async def test_get_image_index_jog_reset_direct(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image(); await cam.get_image()
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index_jog": -1})
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index_reset": True})
    assert cam.directory_index[seq_path] == 1

    await cam.get_image(extra={"index": 5})  # 5 % 3 = 2 -> inc -> 0
    assert cam.directory_index[seq_path] == 0

@pytest.mark.asyncio
async def test_index_priority_order(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2", "3", "4"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image(extra={"index": 3, "index_reset": True, "index_jog": 2})
    assert cam.directory_index[seq_path] == 4

    await cam.get_image(extra={"index_reset": True, "index_jog": 2})
    assert cam.directory_index[seq_path] == 1

@pytest.mark.asyncio
async def test_negative_and_large_indices(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    seq_path = os.path.join(root, sub)

    await cam.get_image(extra={"index": -1})   # -> 2
    assert cam.directory_index[seq_path] == 0

    await cam.get_image(extra={"index": 100})  # -> 1
    assert cam.directory_index[seq_path] == 2

    await cam.get_image(extra={"index": 0})
    assert cam.directory_index[seq_path] == 1

# ---- get_images & do_command ----
@pytest.mark.asyncio
async def test_get_images_filters_by_source_name(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seqA", ["0", "1"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    images, _ = await cam.get_images(filter_source_names=["other"])
    assert images == []
    images, _ = await cam.get_images(filter_source_names=[sub])
    assert len(images) == 1 and images[0].name == sub and hasattr(images[0], "data")

@pytest.mark.asyncio
async def test_do_command_set_index_reset_and_jog(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0", "1", "2"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})

    out = await cam.do_command({"set": {"index": 5}})
    assert out["index"] == 2

    out = await cam.do_command({"set": {"index_reset": True}})
    assert out["index"] == 0

    out = await cam.do_command({"set": {"index_jog": -1}})
    assert out["index"] == 2

@pytest.mark.asyncio
async def test_do_command_invalid_operations(make_dir_with_images, make_config):
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
async def test_directory_deleted_after_config(make_dir_with_images, make_config):
    root, sub, _ = make_dir_with_images("seq", ["0"], ext="jpg")
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    shutil.rmtree(os.path.join(root, sub))
    with pytest.raises(ViamError, match=r"directory no longer exists"):
        await cam.get_image()

# ---- perf ----
@pytest.mark.asyncio
async def test_large_directory_performance(make_dir_with_images, make_config):
    # numeric suffixes → accepted by sorter
    bases = [f"img_{i:04d}" for i in range(100)]  # trailing digits OK (idx mode)
    root, sub, _ = make_dir_with_images("large", bases, ext="jpg")

    start = time.time()
    cam = imageDir.new(make_config("cam", root, sub, "jpg"), {})
    assert cam.dir_len == 100
    assert time.time() - start < 1.0

    start = time.time()
    await cam.get_image(extra={"index": 50})
    assert time.time() - start < 0.1

# ---- mixed timestamp + regular (regular are skipped) ----
def test_mixed_timestamp_and_regular_files_sort(make_dir_with_images):
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

