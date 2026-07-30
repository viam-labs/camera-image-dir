// Package imagedir implements a Viam camera that serves images sequentially
// from a directory on the host machine.
package imagedir

import (
	"context"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"sync"

	"go.viam.com/rdk/components/camera"
	"go.viam.com/rdk/data"
	"go.viam.com/rdk/logging"
	"go.viam.com/rdk/pointcloud"
	"go.viam.com/rdk/resource"
	"go.viam.com/rdk/spatialmath"
)

// Model is the resource model this module registers.
var Model = resource.NewModel("viam-labs", "camera", "image-dir")

const (
	defaultRootDir = "/tmp"
	defaultExt     = "jpg"
)

// supportedExts are the file extensions this camera will serve, keyed lowercase.
var supportedExts = map[string]string{
	"jpg":  "image/jpeg",
	"jpeg": "image/jpeg",
	"png":  "image/png",
	"gif":  "image/gif",
}

func sortedSupportedExts() []string {
	out := make([]string, 0, len(supportedExts))
	for e := range supportedExts {
		out = append(out, e)
	}
	sort.Strings(out)
	return out
}

func init() {
	resource.RegisterComponent(camera.API, Model,
		resource.Registration[camera.Camera, *Config]{Constructor: newImageDir})
}

// Config is the JSON configuration for the image-dir camera.
type Config struct {
	// RootDir is the directory that Dir is resolved against. Defaults to /tmp.
	RootDir string `json:"root_dir"`
	// Ext is the image file extension to serve. Defaults to jpg.
	Ext string `json:"ext"`
	// Dir is the subdirectory of RootDir to read images from. Required.
	Dir string `json:"dir"`
}

func (cfg *Config) rootDir() string {
	if cfg.RootDir == "" {
		return defaultRootDir
	}
	return cfg.RootDir
}

func (cfg *Config) ext() string {
	if cfg.Ext == "" {
		return defaultExt
	}
	return strings.ToLower(cfg.Ext)
}

// requestedDir resolves the directory images are read from.
func (cfg *Config) requestedDir() string {
	if cfg.Dir == "" {
		return cfg.rootDir()
	}
	return filepath.Join(cfg.rootDir(), cfg.Dir)
}

// Validate checks the config and reports every problem found, matching the
// Python module's behaviour of accumulating errors rather than failing on the first.
func (cfg *Config) Validate(path string) ([]string, []string, error) {
	var errs []string

	if fi, err := os.Stat(cfg.rootDir()); err != nil || !fi.IsDir() {
		errs = append(errs, fmt.Sprintf("specified 'root_dir' does not exist: %s", cfg.rootDir()))
	}

	if _, ok := supportedExts[cfg.ext()]; !ok {
		errs = append(errs, fmt.Sprintf("unsupported 'ext': %s. Supported: %v",
			cfg.Ext, sortedSupportedExts()))
	}

	if cfg.Dir == "" {
		errs = append(errs, "'dir' is required and must be a subdirectory of 'root_dir'")
	}

	// Only inspect the directory once the values above are known good.
	if len(errs) == 0 {
		dir := cfg.requestedDir()
		if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
			errs = append(errs,
				fmt.Sprintf("requested 'dir' not found within configured 'root_dir': %s", dir))
		} else {
			matches, err := filesWithExt(dir, cfg.ext())
			if err != nil {
				errs = append(errs, err.Error())
			} else if len(matches) == 0 {
				errs = append(errs, fmt.Sprintf("no files ending with .%s found in %s", cfg.ext(), dir))
			}
		}
	}

	if len(errs) > 0 {
		return nil, nil, errors.New(strings.Join(errs, "; "))
	}
	return nil, nil, nil
}

// ErrClosed is returned by methods called after the resource has been closed.
var ErrClosed = errors.New("image-dir camera is closed")

// imageDir serves images from a fixed, pre-sorted list built at construction.
type imageDir struct {
	resource.Named
	resource.AlwaysRebuild

	logger logging.Logger

	rootDir string
	ext     string
	subDir  string
	mimeTy  string

	mu sync.Mutex
	// sortedFiles is fixed for the lifetime of the resource; changing dir or ext
	// requires a reconfigure, which rebuilds the resource. Guarded by mu so Close
	// can release it without racing an in-flight read.
	sortedFiles []string
	index       int
	closed      bool
}

// Close releases the image list and prevents further use of the camera.
//
// The resource holds no file handles or goroutines — files are opened and closed
// per read — so there is nothing to tear down. What Close does provide is the
// half of the resource.Resource contract that matters here: it is idempotent,
// and subsequent calls fail with ErrClosed rather than serving stale state.
func (c *imageDir) Close(ctx context.Context) error {
	c.mu.Lock()
	defer c.mu.Unlock()

	if c.closed {
		return nil
	}
	c.closed = true
	c.sortedFiles = nil
	c.index = 0

	c.logger.Infof("image-dir: closed %s", c.Name().ShortName())
	return nil
}

func newImageDir(
	ctx context.Context,
	_ resource.Dependencies,
	conf resource.Config,
	logger logging.Logger,
) (camera.Camera, error) {
	cfg, err := resource.NativeConfig[*Config](conf)
	if err != nil {
		return nil, err
	}
	if _, _, err := cfg.Validate(""); err != nil {
		return nil, err
	}

	dir := cfg.requestedDir()
	files, err := getSortedFiles(dir, cfg.ext(), logger)
	if err != nil {
		return nil, err
	}
	if len(files) == 0 {
		return nil, fmt.Errorf("no images with valid timestamp or numeric index found in %s", dir)
	}

	logger.Infof("image-dir: configured name=%s root=%s sub_dir=%s ext=%s images=%d",
		conf.ResourceName().ShortName(), cfg.rootDir(), cfg.Dir, cfg.ext(), len(files))

	return &imageDir{
		Named:       conf.ResourceName().AsNamed(),
		logger:      logger,
		rootDir:     cfg.rootDir(),
		ext:         cfg.ext(),
		subDir:      cfg.Dir,
		mimeTy:      supportedExts[cfg.ext()],
		sortedFiles: files,
	}, nil
}

// Images returns the image at the current index and advances the index.
//
// The extra map supports the same keys the Python module accepted:
//
//	index       int  - serve this absolute index (wraps)
//	index_reset bool - serve index 0
//	index_jog   int  - offset from the current index (wraps, may be negative)
func (c *imageDir) Images(
	ctx context.Context,
	filterSourceNames []string,
	extra map[string]interface{},
) ([]camera.NamedImage, resource.ResponseMetadata, error) {
	sourceName := c.subDir

	if len(filterSourceNames) > 0 && !contains(filterSourceNames, sourceName) {
		return nil, resource.ResponseMetadata{}, nil
	}

	// dir and ext are fixed at construction; reject per-call overrides rather
	// than silently ignoring them.
	if v, ok := extra["dir"]; ok {
		if s, _ := v.(string); s != "" && s != c.subDir {
			return nil, resource.ResponseMetadata{},
				errors.New("per-call 'dir' override not supported; reconfigure instead")
		}
	}
	if v, ok := extra["ext"]; ok {
		if s, _ := v.(string); s != "" && !strings.EqualFold(s, c.ext) {
			return nil, resource.ResponseMetadata{},
				errors.New("per-call 'ext' override not supported; reconfigure instead")
		}
	}

	dir := filepath.Join(c.rootDir, c.subDir)
	if fi, err := os.Stat(dir); err != nil || !fi.IsDir() {
		return nil, resource.ResponseMetadata{}, errors.New("configured directory no longer exists")
	}

	// Resolve the index and snapshot the filename under the lock, then read the
	// file outside it so a slow disk doesn't serialise concurrent callers.
	c.mu.Lock()
	if c.closed {
		c.mu.Unlock()
		return nil, resource.ResponseMetadata{}, ErrClosed
	}
	idx, err := c.resolveIndexLocked(extra)
	if err != nil {
		c.mu.Unlock()
		return nil, resource.ResponseMetadata{}, err
	}
	name, n := c.sortedFiles[idx], len(c.sortedFiles)
	c.mu.Unlock()

	path := filepath.Join(dir, name)
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, resource.ResponseMetadata{}, fmt.Errorf("image file not found: %s: %w", path, err)
	}

	named, err := camera.NamedImageFromBytes(raw, sourceName, c.mimeTy, data.Annotations{})
	if err != nil {
		return nil, resource.ResponseMetadata{}, err
	}

	// Advance only after a successful read, so a bad file is retried rather than
	// silently skipped. A Close racing the read wins: the index stays cleared.
	c.mu.Lock()
	if !c.closed {
		c.index = (idx + 1) % n
	}
	c.mu.Unlock()

	return []camera.NamedImage{named}, resource.ResponseMetadata{}, nil
}

// resolveIndexLocked picks the index to serve, honouring the extra overrides.
// Callers must hold c.mu.
func (c *imageDir) resolveIndexLocked(extra map[string]interface{}) (int, error) {
	n := len(c.sortedFiles)
	if n == 0 {
		return 0, errors.New("no images preloaded")
	}

	switch {
	case extra["index"] != nil:
		v, err := toInt(extra["index"])
		if err != nil {
			return 0, fmt.Errorf("'index': %w", err)
		}
		return wrap(v, n), nil
	case isTrue(extra["index_reset"]):
		return 0, nil
	case extra["index_jog"] != nil:
		v, err := toInt(extra["index_jog"])
		if err != nil {
			return 0, fmt.Errorf("'index_jog': %w", err)
		}
		return wrap(wrap(c.index, n)+v, n), nil
	default:
		return wrap(c.index, n), nil
	}
}

// DoCommand supports {"set": {...}} to move the playback index.
func (c *imageDir) DoCommand(ctx context.Context, cmd map[string]interface{}) (map[string]interface{}, error) {
	ret := map[string]interface{}{}

	rawSet, ok := cmd["set"]
	if !ok || rawSet == nil {
		return ret, nil
	}
	set, ok := rawSet.(map[string]interface{})
	if !ok {
		return nil, errors.New("'set' must be a map")
	}

	if set["dir"] != nil || set["ext"] != nil {
		return nil, errors.New("changing 'dir' or 'ext' requires a reconfigure")
	}

	c.mu.Lock()
	defer c.mu.Unlock()

	if c.closed {
		return nil, ErrClosed
	}

	n := len(c.sortedFiles)
	if n == 0 {
		return nil, errors.New("no images preloaded")
	}

	if v := set["index"]; v != nil {
		i, err := toInt(v)
		if err != nil {
			return nil, fmt.Errorf("'index': %w", err)
		}
		c.index = wrap(i, n)
		ret["index"] = c.index
	}

	if isTrue(set["index_reset"]) {
		c.index = 0
		ret["index"] = 0
	}

	if v := set["index_jog"]; v != nil {
		i, err := toInt(v)
		if err != nil {
			return nil, fmt.Errorf("'index_jog': %w", err)
		}
		c.index = wrap(wrap(c.index, n)+i, n)
		ret["index"] = c.index
	}

	return ret, nil
}

func (c *imageDir) Properties(ctx context.Context) (camera.Properties, error) {
	return camera.Properties{
		SupportsPCD: false,
		MimeTypes:   []string{c.mimeTy},
	}, nil
}

func (c *imageDir) NextPointCloud(ctx context.Context, extra map[string]interface{}) (pointcloud.PointCloud, error) {
	return nil, errors.New("image-dir camera does not support point clouds")
}

func (c *imageDir) Geometries(ctx context.Context, extra map[string]interface{}) ([]spatialmath.Geometry, error) {
	return nil, nil
}

// ---- file ordering -------------------------------------------------------

// tsPattern matches the leading timestamp Viam data-capture puts on filenames,
// e.g. 2026-05-30T11_36_12.018Z_...
var tsPattern = regexp.MustCompile(`^(\d{4})-(\d{2})-(\d{2})T(\d{2})_(\d{2})_(\d{2})(?:\.(\d{3}))?Z`)

// trailingIntPattern matches a trailing integer, e.g. frame_12
var trailingIntPattern = regexp.MustCompile(`(\d+)$`)

func filesWithExt(dir, ext string) ([]string, error) {
	entries, err := os.ReadDir(dir)
	if err != nil {
		return nil, err
	}
	suffix := "." + strings.ToLower(ext)
	var out []string
	for _, e := range entries {
		if e.IsDir() {
			continue
		}
		if strings.HasSuffix(strings.ToLower(e.Name()), suffix) {
			out = append(out, e.Name())
		}
	}
	return out, nil
}

// stripKnownExt removes the extension only when it is one this module supports,
// so a dot elsewhere in the name is left alone.
func stripKnownExt(filename string) string {
	i := strings.LastIndex(filename, ".")
	if i < 0 {
		return filename
	}
	if _, ok := supportedExts[strings.ToLower(filename[i+1:])]; ok {
		return filename[:i]
	}
	return filename
}

// timeOfDayKey returns microseconds since midnight for a timestamped filename.
//
// NOTE: this deliberately ignores the calendar date, matching the Python
// module it was ported from. Files spanning multiple days therefore interleave
// by time of day rather than sorting chronologically.
func timeOfDayKey(filename string) (int64, bool) {
	m := tsPattern.FindStringSubmatch(stripKnownExt(filename))
	if m == nil {
		return 0, false
	}
	atoi := func(s string) int64 { v, _ := strconv.ParseInt(s, 10, 64); return v }

	month, day := atoi(m[2]), atoi(m[3])
	hour, minute, sec := atoi(m[4]), atoi(m[5]), atoi(m[6])
	if month < 1 || month > 12 || day < 1 || day > 31 ||
		hour > 23 || minute > 59 || sec > 59 {
		return 0, false
	}
	var ms int64
	if m[7] != "" {
		ms = atoi(m[7])
	}
	return ((hour*60+minute)*60+sec)*1_000_000 + ms*1000, true
}

func trailingIndexKey(filename string) (int64, bool) {
	base := filename
	if i := strings.LastIndex(filename, "."); i >= 0 {
		base = filename[:i]
	}
	m := trailingIntPattern.FindStringSubmatch(base)
	if m == nil {
		return 0, false
	}
	v, err := strconv.ParseInt(m[1], 10, 64)
	if err != nil {
		return 0, false
	}
	return v, true
}

type keyed struct {
	name string
	key  int64
}

// getSortedFiles builds the fixed playback order. Files are ordered by embedded
// timestamp when most of them carry one, otherwise by trailing integer index.
func getSortedFiles(dir, ext string, logger logging.Logger) ([]string, error) {
	files, err := filesWithExt(dir, ext)
	if err != nil {
		return nil, err
	}
	if len(files) == 0 {
		return nil, nil
	}

	var ts, idx []keyed
	for _, f := range files {
		if k, ok := timeOfDayKey(f); ok {
			ts = append(ts, keyed{f, k})
			continue
		}
		if k, ok := trailingIndexKey(f); ok {
			idx = append(idx, keyed{f, k})
		}
	}

	sortKeyed := func(in []keyed) []string {
		sort.Slice(in, func(i, j int) bool {
			if in[i].key != in[j].key {
				return in[i].key < in[j].key
			}
			return in[i].name < in[j].name // stable & deterministic
		})
		out := make([]string, len(in))
		for i, k := range in {
			out[i] = k.name
		}
		return out
	}

	warnSkipped := func(kept []keyed, why string) {
		if logger == nil {
			return
		}
		in := make(map[string]bool, len(kept))
		for _, k := range kept {
			in[k.name] = true
		}
		for _, f := range files {
			if !in[f] {
				logger.Warnf("Skipping file without %s: %s", why, f)
			}
		}
	}

	// Tie goes to timestamp ordering, matching the Python module.
	if len(ts) >= len(idx) && len(ts) > 0 {
		warnSkipped(ts, "parsable timestamp")
		return sortKeyed(ts), nil
	}
	if len(idx) > 0 {
		warnSkipped(idx, "numeric index")
		return sortKeyed(idx), nil
	}

	warnSkipped(nil, "timestamp or numeric index")
	return nil, nil
}

// ---- small helpers -------------------------------------------------------

// wrap returns v mod n using Python's floor-modulo semantics, so negative
// offsets wrap around the end of the list rather than going out of range.
func wrap(v, n int) int {
	if n <= 0 {
		return 0
	}
	return ((v % n) + n) % n
}

// toInt accepts the numeric shapes that survive a JSON/protobuf round trip.
func toInt(v interface{}) (int, error) {
	switch t := v.(type) {
	case int:
		return t, nil
	case int32:
		return int(t), nil
	case int64:
		return int(t), nil
	case float32:
		return int(t), nil
	case float64:
		return int(t), nil
	case string:
		i, err := strconv.Atoi(t)
		if err != nil {
			return 0, fmt.Errorf("expected an integer, got %q", t)
		}
		return i, nil
	default:
		return 0, fmt.Errorf("expected an integer, got %T", v)
	}
}

func isTrue(v interface{}) bool {
	b, ok := v.(bool)
	return ok && b
}

func contains(haystack []string, needle string) bool {
	for _, s := range haystack {
		if s == needle {
			return true
		}
	}
	return false
}
