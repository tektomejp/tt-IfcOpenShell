# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

IfcOpenShell is an open-source library for working with Industry Foundation Classes (IFC) — a standardized format for Building Information Modeling (BIM). It provides C++ and Python APIs, geometry processing, and tools including IfcConvert and the Bonsai Blender add-on. This is a fork maintained at `tektomejp/tt-IfcOpenShell` (branch `tt-dev`, upstream base `v0.8.0`).

## Licensing

- **Library code** (everything except Bonsai): LGPL-3.0-or-later
- **Bonsai** (`src/bonsai/`): GPL-3.0-or-later

## Build Commands

### C++ (CMake)

```bash
# From repo root
mkdir build && cd build
cmake -DCMAKE_BUILD_TYPE=Release \
      "-DSCHEMA_VERSIONS=2x3;4;4x3_add2" \
      -DGLTF_SUPPORT=On \
      ../cmake
make -j$(nproc)
sudo make install
```

Key CMake options: `BUILD_IFCGEOM`, `BUILD_IFCPYTHON`, `BUILD_CONVERT`, `WITH_OPENCASCADE`, `WITH_CGAL`, `WITH_ROCKSDB`, `BUILD_ONLY_COMMON_SCHEMAS`.

### Pixi (alternative, mainly Windows)

```bash
pixi run -e dev configure-debug    # Configure
pixi run -e dev build-debug        # Build
pixi run -e tests test             # Test
```

## Linting and Formatting

```bash
# Python: black (120 char line length) + ruff
poe format          # Run black + ruff
poe black           # Black only
poe ruff            # Ruff only

# Type checking
poe ty-venv         # Set up venvs first
poe ty              # Run ty type checker

# C++
clang-format        # Config in .clang-format
clang-tidy          # Config in .clang-tidy

# CMake
poe cmake-format    # gersemi
```

Configuration is in `pyproject.toml`. Excluded from formatting: `ifcopenshell/express/`, `ifcopenshell/mvd/`, `ifcopenshell/simple_spf/`, `src/ifc2ca/templates/`, `src/svgfill`, `src/exterior-shell-extractor`.

## Testing

### Python (ifcopenshell-python) — the most common test target

```bash
cd src/ifcopenshell-python

# Run all tests (serial)
make test
# Equivalent to: pytest -p no:pytest-blender test --ignore=test/util/test_shape_builder.py

# Run all tests (parallel)
make test-parallel

# Run a single test file
pytest -p no:pytest-blender test/api/geometry/test_connect_path.py

# Run a single test
pytest -p no:pytest-blender test/api/geometry/test_connect_path.py::TestConnectPath::test_some_method

# Shape builder tests (requires mathutils)
make test-mathutils
```

Always use `-p no:pytest-blender` to disable the Blender plugin. Always `--ignore=test/util/test_shape_builder.py` when running the full suite (it requires mathutils).

### Other packages

Each package in `src/` has its own `Makefile` with a `test` target:

```bash
cd src/bcf && make test
cd src/ifcpatch && make test
cd src/ifctester && make test
cd src/ifcdiff && make test
cd src/bsdd && make test
```

These all use `pytest -p no:pytest-blender test` (or `tests`).

## Architecture

### C++ Core (`src/`)

- **`ifcparse/`** — IFC file parsing. Supports IFC2x3, IFC4, IFC4x1, IFC4x2, IFC4x3. Schema-specific code is compiled conditionally. Contains large auto-generated schema files.
- **`ifcgeom/`** — Geometry processing with dual-kernel support (OpenCASCADE + CGAL).
- **`ifcwrap/`** — SWIG Python bindings to C++ core.
- **`serializers/`** — Output format serializers (glTF, Collada, SVG, etc.).
- **`ifcconvert/`** — CLI tool for converting IFC files to other formats.

### Python API (`src/ifcopenshell-python/ifcopenshell/`)

- **`api/`** — 35+ functional API modules (aggregate, alignment, boundary, classification, context, cost, geometry, georeference, material, pset, root, sequence, spatial, structural, style, unit, etc.). Each module is a directory with individual function files.
- **`util/`** — Utility functions for working with IFC data.
- **`geom/`** — Python geometry processing interface.
- **`express/`**, **`mvd/`**, **`simple_spf/`** — Submodules (excluded from formatting/linting).

### Ecosystem packages (`src/`)

- **`bonsai/`** — Blender add-on for IFC authoring (GPL-3.0, has its own large test suite)
- **`bcf/`** — BIM Collaboration Format library
- **`ifcpatch/`** — IFC file manipulation scripts
- **`ifctester/`** — IDS (Information Delivery Specification) model auditing
- **`ifcclash/`** — Clash detection
- **`ifccsv/`** — Schedule import/export
- **`ifcdiff/`** — Model comparison
- **`ifcmcp/`** — MCP server integration

### IFC Schema Versions

The library supports IFC2x3 TC1, IFC4 Add2 TC1, IFC4x1, IFC4x2, and IFC4x3 Add2. Be aware of which schema versions your change affects. Common CI builds only compile `2x3;4;4x3_add2`.

## AI Contribution Requirements (from AGENTS.md)

- Commit messages: include "Generated with the assistance of an AI coding tool" in the body
- New AI-generated files: add a comment at the top indicating AI generation
- PRs: indicate which parts are AI-generated in the description
- Commit subjects: 50 chars or less, imperative mood
- PRs should be single-issue, focused, with no scope creep or cosmetic changes
- Run formatters before submitting
- Include tests for testable code changes

## Code Style

- **Python**: black (120 chars), ruff linter, C-style naming in API (e.g., `add_representation`, `assign_object`)
- **C++**: C++17, clang-format, clang-tidy
