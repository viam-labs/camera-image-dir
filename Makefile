SHELL := /bin/bash

ifeq ($(OS),Windows_NT)
  VENV_BIN := venv/Scripts
  EXE_EXT  := .exe
  SYS_PY   := py -3.11
else
  VENV_BIN := venv/bin
  EXE_EXT  :=
  SYS_PY   := python3.11
endif

VENV := venv
PY   := $(VENV_BIN)/python
PIP  := $(PY) -m pip

.PHONY: venv dev-deps test test-verbose lint lint-fix setup build module clean fix-venv

# ---- venv ---------------------------------------------------------------
$(PY):
	$(SYS_PY) -m venv $(VENV)
	$(PIP) install -U pip setuptools wheel

venv: $(PY)

# ---- deps ---------------------------------------------------------------
dev-deps: venv
	$(PIP) install -e .[dev]

# ---- tests --------------------------------------------------------------
test: dev-deps
	PYTHONPATH=. $(PY) -m pytest -v

test-verbose: dev-deps
	PYTHONPATH=. $(PY) -m pytest -vv -s

# ---- lint ---------------------------------------------------------------
lint: dev-deps
	$(PY) -m ruff check src/ tests/

lint-fix: dev-deps
	$(PY) -m ruff check --fix src/ tests/
	$(PY) -m ruff format src/ tests/

# ---- build via scripts (single source of truth) -------------------------
setup: dev-deps
	bash ./setup.sh

build: setup
	bash ./build.sh

module: build
	cp dist/archive.tar.gz module.tar.gz

# ---- clean --------------------------------------------------------------
clean:
	rm -rf .pytest_cache build dist *.spec module.tar.gz dist/archive.tar.gz
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true

fix-venv:
	rm -rf $(VENV)
	$(SYS_PY) -m venv $(VENV)
	$(PIP) install -U pip setuptools wheel
