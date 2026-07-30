# camera-image-dir

A Viam camera model that serves images sequentially from a directory on the host
machine.

This module was ported from Python to Go; the Python implementation is available
in the history prior to the port.

Model: `viam-labs:camera:image-dir`

## Configuration

```json
{
  "root_dir": "/tmp",
  "dir": "data",
  "ext": "jpg"
}
```

| Attribute  | Type   | Required | Default | Description |
|------------|--------|----------|---------|-------------|
| `dir`      | string | **yes**  | —       | Subdirectory of `root_dir` to read images from |
| `root_dir` | string | no       | `/tmp`  | Directory that `dir` is resolved against |
| `ext`      | string | no       | `jpg`   | One of `jpg`, `jpeg`, `png`, `gif` |

Configuration is rejected at validation time if `root_dir` does not exist, `dir`
is missing or not found beneath `root_dir`, `ext` is unsupported, or the
resolved directory contains no files with that extension.

## Playback order

The file list is built once when the resource is constructed and is fixed for
its lifetime; changing `dir` or `ext` requires a reconfigure.

Files are ordered by whichever scheme matches the majority of filenames, with
ties going to timestamps:

1. **Timestamp** — a leading `YYYY-MM-DDTHH_MM_SS[.mmm]Z` prefix, as produced by
   Viam data capture.
2. **Trailing integer** — e.g. `frame_12.jpg`, `7.jpg`. Sorted numerically, so
   unpadded names order correctly (`1`, `2`, `10`, not `1`, `10`, `2`).

Files matching neither scheme are skipped with a warning.

> **Known quirk, carried over from the Python module:** the timestamp sort key
> is *time-of-day only* — the calendar date is discarded. Images spanning
> multiple days interleave by wall-clock time rather than sorting
> chronologically. This behaviour was preserved deliberately so the port is a
> faithful one; see `timeOfDayKey` in `models/imagedir/imagedir.go`.

## Controlling the index

Each call to `GetImages` serves the current image and advances the index,
wrapping at the end of the list. The position can be steered two ways.

Via the `extra` map on `GetImages`:

| Key           | Type | Effect |
|---------------|------|--------|
| `index`       | int  | Serve this absolute index (wraps; negatives count from the end) |
| `index_reset` | bool | Serve index 0 |
| `index_jog`   | int  | Offset from the current index (wraps; may be negative) |

Precedence is `index` > `index_reset` > `index_jog`. Passing `dir` or `ext` in
`extra` is rejected rather than silently ignored — the file list is fixed.

These controls ride on `GetImages`. The camera API migrated from the singular
`GetImage` RPC to `GetImages` in 2025, and every SDK has been ported to it — the
Go and Python SDKs both implement `GetImages` only, on the client and the server
side alike. The Python implementation of this module was already reached through
`GetImages` too, so nothing changed here with the port.

Via `DoCommand`, which sets the index without serving an image and returns the
new position as `{"index": n}`:

```json
{ "set": { "index": 5 } }
{ "set": { "index_reset": true } }
{ "set": { "index_jog": -2 } }
```

## Building

The module is pure Go (built with the `no_cgo` tag), so every supported platform
cross-compiles from any host — no per-OS build runner needed:

```bash
make build          # host platform
make all-platforms  # linux/amd64, linux/arm64, darwin/arm64, windows/amd64
make module         # module.tar.gz for the registry
make test
make lint
```

## Differences from the Python module

- **Images are served as stored.** The Python module decoded each file and
  re-encoded it to RGB in the requested mime type. This port serves the original
  file bytes with the mime type implied by `ext`, which is lossless and cheaper,
  but means a corrupt file surfaces at the client rather than at read time.
- **Reconfigure rebuilds the resource** via `resource.AlwaysRebuild`, matching
  the behaviour the Python SDK now warns about (`Reconfigure is deprecated, and
  resources will always rebuild`).
