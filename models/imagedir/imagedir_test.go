package imagedir

import (
	"bytes"
	"context"
	"errors"
	"image"
	"image/color"
	"image/jpeg"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"go.viam.com/rdk/components/camera"
	"go.viam.com/rdk/logging"
	"go.viam.com/rdk/resource"
)

// writeJPEG writes a real (tiny) JPEG so tests exercise genuine image bytes.
func writeJPEG(t *testing.T, path string) {
	t.Helper()
	img := image.NewRGBA(image.Rect(0, 0, 2, 2))
	img.Set(0, 0, color.RGBA{R: 255, A: 255})
	var buf bytes.Buffer
	if err := jpeg.Encode(&buf, img, nil); err != nil {
		t.Fatalf("encoding fixture jpeg: %v", err)
	}
	if err := os.WriteFile(path, buf.Bytes(), 0o600); err != nil {
		t.Fatalf("writing fixture %s: %v", path, err)
	}
}

// makeDir creates root/sub populated with the given filenames.
func makeDir(t *testing.T, sub string, names ...string) (root string) {
	t.Helper()
	root = t.TempDir()
	dir := filepath.Join(root, sub)
	if err := os.MkdirAll(dir, 0o750); err != nil {
		t.Fatal(err)
	}
	for _, n := range names {
		writeJPEG(t, filepath.Join(dir, n))
	}
	return root
}

func newTestCam(t *testing.T, root, sub, ext string) *imageDir {
	t.Helper()
	conf := resource.Config{
		Name:                "test",
		API:                 camera.API,
		Model:               Model,
		ConvertedAttributes: &Config{RootDir: root, Dir: sub, Ext: ext},
	}
	c, err := newImageDir(context.Background(), nil, conf, logging.NewTestLogger(t))
	if err != nil {
		t.Fatalf("constructing camera: %v", err)
	}
	return c.(*imageDir)
}

// ---- config validation ---------------------------------------------------

func TestValidate(t *testing.T) {
	realRoot := makeDir(t, "seq", "0.jpg")

	tests := []struct {
		name    string
		cfg     Config
		wantErr string
	}{
		{"root missing", Config{RootDir: "/definitely/not/here", Dir: "seq"}, "root_dir"},
		{"ext unsupported", Config{RootDir: realRoot, Dir: "seq", Ext: "bmp"}, "unsupported 'ext'"},
		{"dir required", Config{RootDir: realRoot}, "'dir' is required"},
		{"dir not found", Config{RootDir: realRoot, Dir: "nope"}, "requested 'dir' not found"},
		{"no matching files", Config{RootDir: realRoot, Dir: "seq", Ext: "png"}, "no files ending with .png"},
		{"happy path", Config{RootDir: realRoot, Dir: "seq"}, ""},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			_, _, err := tc.cfg.Validate("")
			switch {
			case tc.wantErr == "" && err != nil:
				t.Fatalf("expected no error, got %v", err)
			case tc.wantErr != "" && err == nil:
				t.Fatalf("expected error containing %q, got nil", tc.wantErr)
			case tc.wantErr != "" && !strings.Contains(err.Error(), tc.wantErr):
				t.Fatalf("expected error containing %q, got %v", tc.wantErr, err)
			}
		})
	}
}

func TestValidateDefaults(t *testing.T) {
	cfg := &Config{Dir: "seq"}
	if cfg.rootDir() != "/tmp" {
		t.Errorf("root_dir default = %q, want /tmp", cfg.rootDir())
	}
	if cfg.ext() != "jpg" {
		t.Errorf("ext default = %q, want jpg", cfg.ext())
	}
	if (&Config{Ext: "JPEG"}).ext() != "jpeg" {
		t.Error("ext should be lowercased")
	}
}

func TestConstructorFailsWithNoParsableImages(t *testing.T) {
	// Files match the extension but carry neither a timestamp nor a trailing int.
	root := makeDir(t, "seq", "alpha.jpg", "beta.jpg")
	conf := resource.Config{
		Name: "test", API: camera.API, Model: Model,
		ConvertedAttributes: &Config{RootDir: root, Dir: "seq"},
	}
	_, err := newImageDir(context.Background(), nil, conf, logging.NewTestLogger(t))
	if err == nil {
		t.Fatal("expected an error when no file has a timestamp or numeric index")
	}
	if !strings.Contains(err.Error(), "no images with valid timestamp or numeric index") {
		t.Fatalf("unexpected error: %v", err)
	}
}

// ---- filename ordering ---------------------------------------------------

func TestTimeOfDayKey(t *testing.T) {
	tests := []struct {
		filename string
		ok       bool
	}{
		{"2025-01-15T12_30_45.123Z_x.jpg", true},
		{"2025-01-15T23_59_59.999Z.jpg", true},
		{"2024-02-29T00_00_00.000Z_leap.jpg", true},
		{"2025-01-15T12_00_00.000Z_noext", true}, // no extension is still parsable
		{"nope.jpeg", false},
		{"2025-13-99T25_61_61.999Z.jpeg", false}, // out-of-range fields
	}
	for _, tc := range tests {
		_, ok := timeOfDayKey(tc.filename)
		if ok != tc.ok {
			t.Errorf("timeOfDayKey(%q) ok = %v, want %v", tc.filename, ok, tc.ok)
		}
	}

	// Key is time-of-day only; the date is deliberately ignored (ported behaviour).
	a, _ := timeOfDayKey("2025-01-15T10_00_00.000Z.jpg")
	b, _ := timeOfDayKey("2099-12-31T10_00_00.000Z.jpg")
	if a != b {
		t.Errorf("expected identical keys for same time on different dates: %d vs %d", a, b)
	}
}

func TestGetSortedFilesPrefersTimestamp(t *testing.T) {
	root := makeDir(t, "seq",
		"2025-10-09T10_00_00.000Z_a.jpg",
		"2025-10-09T09_00_00.000Z_b.jpg",
	)
	got, err := getSortedFiles(filepath.Join(root, "seq"), "jpg", logging.NewTestLogger(t))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"2025-10-09T09_00_00.000Z_b.jpg", "2025-10-09T10_00_00.000Z_a.jpg"}
	if !equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestGetSortedFilesNumericIndex(t *testing.T) {
	// Unpadded names must order numerically, not lexicographically.
	root := makeDir(t, "seq", "10.jpg", "2.jpg", "1.jpg")
	got, err := getSortedFiles(filepath.Join(root, "seq"), "jpg", logging.NewTestLogger(t))
	if err != nil {
		t.Fatal(err)
	}
	want := []string{"1.jpg", "2.jpg", "10.jpg"}
	if !equal(got, want) {
		t.Errorf("got %v, want %v", got, want)
	}
}

func TestGetSortedFilesNoPatternReturnsEmpty(t *testing.T) {
	root := makeDir(t, "seq", "alpha.jpg", "beta.jpg")
	got, err := getSortedFiles(filepath.Join(root, "seq"), "jpg", logging.NewTestLogger(t))
	if err != nil {
		t.Fatal(err)
	}
	if len(got) != 0 {
		t.Errorf("got %v, want empty", got)
	}
}

func TestExtensionCaseInsensitive(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.JPG", "2.Jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	want := []string{"0.jpg", "1.JPG", "2.Jpg"}
	if !equal(cam.sortedFiles, want) {
		t.Errorf("got %v, want %v", cam.sortedFiles, want)
	}
}

// ---- playback ------------------------------------------------------------

func TestImagesCyclesInOrder(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	for i := 0; i < 5; i++ {
		imgs, _, err := cam.Images(ctx, nil, nil)
		if err != nil {
			t.Fatal(err)
		}
		if len(imgs) != 1 {
			t.Fatalf("expected 1 image, got %d", len(imgs))
		}
		if got, want := cam.index, (i+1)%3; got != want {
			t.Errorf("after call %d index = %d, want %d", i, got, want)
		}
	}
}

func TestImagesRejectsDirOrExtOverride(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"dir": "other"}); err == nil {
		t.Error("expected error for per-call dir override")
	}
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"ext": "png"}); err == nil {
		t.Error("expected error for per-call ext override")
	}
	// Matching values are a no-op, not an error.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"dir": "seq", "ext": "JPG"}); err != nil {
		t.Errorf("matching dir/ext should be accepted, got %v", err)
	}
}

func TestImagesIndexControls(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	// Direct index: serves 1, leaves next at 2.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index": 1}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 2 {
		t.Errorf("after index=1, next index = %d, want 2", cam.index)
	}

	// Reset serves 0, leaves next at 1.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index_reset": true}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 1 {
		t.Errorf("after reset, next index = %d, want 1", cam.index)
	}

	// Jog +1 from current 1 serves 2, leaves next at 0 (wrapped).
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index_jog": 1}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 0 {
		t.Errorf("after jog, next index = %d, want 0", cam.index)
	}
}

func TestIndexPriorityOrder(t *testing.T) {
	// index beats index_reset, which beats index_jog.
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg", "3.jpg", "4.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	_, _, err := cam.Images(ctx, nil, map[string]interface{}{
		"index": 3, "index_reset": true, "index_jog": 2,
	})
	if err != nil {
		t.Fatal(err)
	}
	if cam.index != 4 {
		t.Errorf("index should win: next = %d, want 4", cam.index)
	}

	_, _, err = cam.Images(ctx, nil, map[string]interface{}{"index_reset": true, "index_jog": 2})
	if err != nil {
		t.Fatal(err)
	}
	if cam.index != 1 {
		t.Errorf("index_reset should beat index_jog: next = %d, want 1", cam.index)
	}
}

func TestNegativeAndLargeIndices(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	// -1 wraps to the last element (2), so next is 0.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index": -1}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 0 {
		t.Errorf("index=-1 -> next = %d, want 0", cam.index)
	}

	// 100 % 3 == 1, so next is 2.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index": 100}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 2 {
		t.Errorf("index=100 -> next = %d, want 2", cam.index)
	}

	// Negative jog wraps backwards.
	if _, _, err := cam.Images(ctx, nil, map[string]interface{}{"index_jog": -1}); err != nil {
		t.Fatal(err)
	}
	if cam.index != 2 {
		t.Errorf("jog=-1 from 2 -> next = %d, want 2", cam.index)
	}
}

func TestImagesFiltersBySourceName(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	imgs, _, err := cam.Images(ctx, []string{"somethingelse"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(imgs) != 0 {
		t.Errorf("expected no images for non-matching filter, got %d", len(imgs))
	}

	imgs, _, err = cam.Images(ctx, []string{"seq"}, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(imgs) != 1 || imgs[0].SourceName != "seq" {
		t.Errorf("expected one image named seq, got %+v", imgs)
	}
}

func TestImagesReturnsRealBytes(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	imgs, _, err := cam.Images(context.Background(), nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	b, err := imgs[0].Bytes(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := jpeg.Decode(bytes.NewReader(b)); err != nil {
		t.Fatalf("served bytes are not a decodable JPEG: %v", err)
	}
	if imgs[0].MimeType() != "image/jpeg" {
		t.Errorf("mime = %q, want image/jpeg", imgs[0].MimeType())
	}
}

// ---- DoCommand -----------------------------------------------------------

func TestDoCommandSetIndexResetAndJog(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	// Values arrive as float64 over the wire.
	ret, err := cam.DoCommand(ctx, map[string]interface{}{
		"set": map[string]interface{}{"index": float64(2)},
	})
	if err != nil {
		t.Fatal(err)
	}
	if ret["index"] != 2 {
		t.Errorf("index = %v, want 2", ret["index"])
	}

	ret, err = cam.DoCommand(ctx, map[string]interface{}{
		"set": map[string]interface{}{"index_jog": float64(2)},
	})
	if err != nil {
		t.Fatal(err)
	}
	if ret["index"] != 1 { // (2+2) % 3
		t.Errorf("jogged index = %v, want 1", ret["index"])
	}

	ret, err = cam.DoCommand(ctx, map[string]interface{}{
		"set": map[string]interface{}{"index_reset": true},
	})
	if err != nil {
		t.Fatal(err)
	}
	if ret["index"] != 0 {
		t.Errorf("reset index = %v, want 0", ret["index"])
	}
}

func TestDoCommandRejectsDirExtChange(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	for _, key := range []string{"dir", "ext"} {
		_, err := cam.DoCommand(context.Background(), map[string]interface{}{
			"set": map[string]interface{}{key: "x"},
		})
		if err == nil {
			t.Errorf("expected error when setting %q", key)
		}
	}
}

func TestDoCommandEmpty(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	ret, err := cam.DoCommand(context.Background(), map[string]interface{}{})
	if err != nil {
		t.Fatal(err)
	}
	if len(ret) != 0 {
		t.Errorf("expected empty response, got %v", ret)
	}
}

// ---- properties ----------------------------------------------------------

func TestProperties(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	props, err := cam.Properties(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if props.SupportsPCD {
		t.Error("SupportsPCD should be false")
	}
	if props.IntrinsicParams != nil || props.DistortionParams != nil {
		t.Error("intrinsic/distortion params should be nil")
	}
}

func TestNextPointCloudUnsupported(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	if _, err := cam.NextPointCloud(context.Background(), nil); err == nil {
		t.Error("expected NextPointCloud to report unsupported")
	}
}

// ---- Close ---------------------------------------------------------------

func TestCloseIsIdempotent(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	// The contract requires Close to be safe to call repeatedly.
	for i := 0; i < 3; i++ {
		if err := cam.Close(ctx); err != nil {
			t.Fatalf("Close call %d returned %v, want nil", i+1, err)
		}
	}
}

func TestClosePreventsFurtherUse(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	if _, _, err := cam.Images(ctx, nil, nil); err != nil {
		t.Fatalf("Images before Close: %v", err)
	}
	if err := cam.Close(ctx); err != nil {
		t.Fatal(err)
	}

	if _, _, err := cam.Images(ctx, nil, nil); !errors.Is(err, ErrClosed) {
		t.Errorf("Images after Close = %v, want ErrClosed", err)
	}

	_, err := cam.DoCommand(ctx, map[string]interface{}{
		"set": map[string]interface{}{"index": 1},
	})
	if !errors.Is(err, ErrClosed) {
		t.Errorf("DoCommand after Close = %v, want ErrClosed", err)
	}
}

func TestCloseReleasesImageList(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg")
	cam := newTestCam(t, root, "seq", "jpg")

	if err := cam.Close(context.Background()); err != nil {
		t.Fatal(err)
	}
	cam.mu.Lock()
	defer cam.mu.Unlock()
	if cam.sortedFiles != nil {
		t.Errorf("sortedFiles = %v, want nil after Close", cam.sortedFiles)
	}
}

// TestConcurrentImagesAndClose is meaningful under -race: Close nils the image
// list, so any unsynchronised read in Images would be reported.
func TestConcurrentImagesAndClose(t *testing.T) {
	root := makeDir(t, "seq", "0.jpg", "1.jpg", "2.jpg")
	cam := newTestCam(t, root, "seq", "jpg")
	ctx := context.Background()

	var wg sync.WaitGroup
	for i := 0; i < 8; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			// Either a served image or ErrClosed is fine; a race or panic is not.
			_, _, _ = cam.Images(ctx, nil, nil)
		}()
	}
	wg.Add(1)
	go func() {
		defer wg.Done()
		_ = cam.Close(ctx)
	}()
	wg.Wait()

	if _, _, err := cam.Images(ctx, nil, nil); !errors.Is(err, ErrClosed) {
		t.Errorf("Images after Close = %v, want ErrClosed", err)
	}
}

// ---- helpers -------------------------------------------------------------

func TestWrapMatchesPythonModulo(t *testing.T) {
	tests := []struct{ v, n, want int }{
		{0, 3, 0}, {1, 3, 1}, {3, 3, 0}, {4, 3, 1},
		{-1, 3, 2}, {-3, 3, 0}, {-4, 3, 2}, {100, 3, 1},
		{5, 0, 0}, // guard against divide-by-zero
	}
	for _, tc := range tests {
		if got := wrap(tc.v, tc.n); got != tc.want {
			t.Errorf("wrap(%d, %d) = %d, want %d", tc.v, tc.n, got, tc.want)
		}
	}
}

func TestToInt(t *testing.T) {
	for _, v := range []interface{}{1, int32(1), int64(1), float32(1), float64(1), "1"} {
		got, err := toInt(v)
		if err != nil || got != 1 {
			t.Errorf("toInt(%v of %T) = %d, %v", v, v, got, err)
		}
	}
	if _, err := toInt("abc"); err == nil {
		t.Error("expected error for non-numeric string")
	}
	if _, err := toInt(nil); err == nil {
		t.Error("expected error for nil")
	}
}

func equal(a, b []string) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
