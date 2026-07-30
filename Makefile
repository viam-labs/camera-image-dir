SHELL := /bin/bash

BIN     := image-dir
PKG     := .
DIST    := dist
# RDK pulls in cgo-only media drivers we don't use; no_cgo keeps the module
# pure Go, which is what makes cross-compilation work.
TAGS    := no_cgo
LDFLAGS := -s -w

# Platforms published to the registry. Keep in sync with meta.json build.arch.
PLATFORMS := linux/amd64 linux/arm64 darwin/arm64 windows/amd64

# Viam cloud build sets these per target platform. Honour them so `make module`
# builds for the requested target rather than for the build host — otherwise a
# cloud build for windows/amd64 would ship a Linux binary in a Windows package.
# Empty values leave GOOS/GOARCH unset, which means "host default".
GOOS_TARGET   := $(VIAM_BUILD_OS)
GOARCH_TARGET := $(VIAM_BUILD_ARCH)
BIN_EXT       :=

# viam-server resolves the entrypoint from meta.json, so a Windows package must
# ship a binary named .exe and a meta.json that points at it.
ifeq ($(VIAM_TARGET_OS),windows)
  GOOS_TARGET   := windows
  GOARCH_TARGET := amd64
  BIN_EXT       := .exe
endif

.PHONY: build test lint fmt vet tidy module packages upload clean all-platforms $(PLATFORMS)

# ---- local build --------------------------------------------------------
build:
	GOOS=$(GOOS_TARGET) GOARCH=$(GOARCH_TARGET) CGO_ENABLED=0 \
	  go build -tags $(TAGS) -ldflags "$(LDFLAGS)" -o $(BIN)$(BIN_EXT) $(PKG)

# ---- checks -------------------------------------------------------------
test:
	go test -tags $(TAGS) ./...

vet:
	go vet -tags $(TAGS) ./...

fmt:
	gofmt -l -w .

# Fails if anything is unformatted, rather than silently reformatting.
lint: vet
	@out="$$(gofmt -l .)"; \
	if [ -n "$$out" ]; then echo "gofmt needed:"; echo "$$out"; exit 1; fi
	@echo "lint ok"

tidy:
	go mod tidy

# ---- cross compilation --------------------------------------------------
# Every target below builds from any host; no per-OS runner required.
$(PLATFORMS):
	@os=$(word 1,$(subst /, ,$@)); arch=$(word 2,$(subst /, ,$@)); \
	ext=""; [ "$$os" = "windows" ] && ext=".exe"; \
	echo "building $$os/$$arch"; \
	mkdir -p $(DIST)/$$os-$$arch; \
	GOOS=$$os GOARCH=$$arch CGO_ENABLED=0 \
	  go build -tags $(TAGS) -ldflags "$(LDFLAGS)" -o $(DIST)/$$os-$$arch/$(BIN)$$ext $(PKG)

all-platforms: $(PLATFORMS)

# ---- registry package ---------------------------------------------------
# Builds for the host platform and stages the archive Viam expects.
module: build
	rm -rf $(DIST)/pkg && mkdir -p $(DIST)/pkg/bin
	cp $(BIN)$(BIN_EXT) $(DIST)/pkg/bin/$(BIN)$(BIN_EXT)
	jq --arg ep "bin/$(BIN)$(BIN_EXT)" '.entrypoint = $$ep' meta.json > $(DIST)/pkg/meta.json
	[ -f README.md ] && cp README.md $(DIST)/pkg/ || true
	tar -czf module.tar.gz -C $(DIST)/pkg .
	@echo "built module.tar.gz (target=$(if $(VIAM_TARGET_OS),$(VIAM_TARGET_OS),host) entrypoint=bin/$(BIN)$(BIN_EXT))"

# Packages every platform into dist/module-<os>-<arch>.tar.gz, rewriting the
# meta.json entrypoint per platform (Windows needs the .exe suffix).
packages: all-platforms
	@for p in $(PLATFORMS); do \
	  os=$${p%/*}; arch=$${p#*/}; \
	  ext=""; if [ "$$os" = "windows" ]; then ext=".exe"; fi; \
	  stage=$(DIST)/pkg-$$os-$$arch; \
	  rm -rf $$stage; mkdir -p $$stage/bin; \
	  cp $(DIST)/$$os-$$arch/$(BIN)$$ext $$stage/bin/$(BIN)$$ext; \
	  jq --arg ep "bin/$(BIN)$$ext" '.entrypoint = $$ep' meta.json > $$stage/meta.json; \
	  [ -f README.md ] && cp README.md $$stage/ || true; \
	  tar -czf $(DIST)/module-$$os-$$arch.tar.gz -C $$stage .; \
	  echo "packaged $$os/$$arch -> $(DIST)/module-$$os-$$arch.tar.gz"; \
	done

# ---- registry upload ----------------------------------------------------
# The Go rewrite lands as 0.2.0, not a 0.1.x patch: the module is a full
# reimplementation in a different language, not a bug fix.
# Override with e.g. `make upload VERSION=1.2.3`.
VERSION ?= 0.2.0

# Builds every arch, then PRINTS the upload commands rather than running them.
# Publishing to the public registry stays a deliberate, manual step: review the
# commands, then paste the ones you want.
upload: packages
	@echo
	@echo "Review, then run the commands you want:"
	@echo
	@for p in $(PLATFORMS); do \
	  os=$${p%/*}; arch=$${p#*/}; \
	  echo viam module upload --version \"$(VERSION)\" --platform \"$$p\" $(DIST)/module-$$os-$$arch.tar.gz; \
	done

clean:
	rm -rf $(BIN) $(BIN).exe $(DIST) module.tar.gz
