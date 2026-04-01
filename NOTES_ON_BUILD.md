# Building IfcOpenShell with Lazy Attribute Loading

This document explains how to build and test the lazy attribute loading feature in IfcOpenShell.

## Overview

The `feat/lazy-attribute-loading` branch adds a memory optimization that defers parsing of IFC entity attributes until they are first accessed. This can reduce memory consumption by ~40% during file loading for large IFC files.

## Prerequisites

### Required Dependencies

- **CMake** >= 3.18
- **C++ Compiler** with C++17 support (GCC 9+, Clang 10+, MSVC 2019+)
- **Boost** >= 1.70 (system, program_options, regex, thread, date_time, iostreams)
- **libxml2**

### Optional Dependencies (for full build with Python)

- **OpenCASCADE** >= 7.5 (for geometry processing)
- **CGAL** >= 5.0 (for advanced geometry)
- **Python** >= 3.8 with development headers
- **SWIG** >= 4.0 (for Python bindings)
- **Eigen3** (for geometry kernels)

## Building IfcParse Only (Minimal Build)

For testing the lazy loading feature without geometry dependencies:

```bash
cd tt-ifcopenshell
mkdir -p build && cd build

cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_IFCGEOM=OFF \
  -DBUILD_CONVERT=OFF \
  -DBUILD_GEOMSERVER=OFF \
  -DBUILD_IFCPYTHON=OFF \
  -DWITH_OPENCASCADE=OFF \
  -DWITH_CGAL=OFF \
  -DCOLLADA_SUPPORT=OFF \
  -DHDF5_SUPPORT=OFF

make -j$(nproc) IfcParse
```

This builds `libIfcParse.a` which contains the lazy loading implementation.

## Building with Python Bindings

For full testing with Python, you need all dependencies:

### Ubuntu/Debian

```bash
sudo apt-get install -y \
  cmake \
  libboost-all-dev \
  libocct-data-exchange-dev \
  libocct-draw-dev \
  libocct-foundation-dev \
  libocct-modeling-algorithms-dev \
  libocct-modeling-data-dev \
  libocct-ocaf-dev \
  libocct-visualization-dev \
  libcgal-dev \
  libeigen3-dev \
  libxml2-dev \
  libhdf5-dev \
  swig \
  python3-dev
```

### Build Commands

```bash
cd tt-ifcopenshell
mkdir -p build && cd build

cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DBUILD_IFCPYTHON=ON

make -j$(nproc)

# Install to Python site-packages
make install
# Or use in-place by adding build directory to PYTHONPATH
```

### Using Conda/Mamba (Recommended)

```bash
mamba create -n ifcopenshell-dev -c conda-forge \
  python=3.11 \
  cmake \
  boost-cpp \
  occt \
  cgal-cpp \
  eigen \
  swig \
  hdf5

mamba activate ifcopenshell-dev

cd tt-ifcopenshell
mkdir -p build && cd build

cmake ../cmake \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_PREFIX_PATH=$CONDA_PREFIX \
  -DBUILD_IFCPYTHON=ON

make -j$(nproc)
```

## Testing Lazy Loading

### Python Test

```python
import ifcopenshell

# Test lazy loading (when integrated into IfcFile)
ifc = ifcopenshell.open("large_file.ifc", lazy=True)  # Future API

# Current testing via low-level API
from ifcopenshell import ifcopenshell_wrapper

# Create streamer with lazy loading enabled
streamer = ifcopenshell_wrapper.InstanceStreamer("file.ifc")
streamer.setLazyLoading(True)

# Read instances - attributes are NOT parsed yet
while streamer:
    inst = streamer.readInstancePy()
    if inst:
        print(f"#{inst[0]} = {inst[1]}")  # Only ID and type parsed
        # Accessing attributes would trigger materialization
```

### Memory Profiling Test

```python
import psutil
import ifcopenshell

def get_memory_mb():
    return psutil.Process().memory_info().rss / (1024 * 1024)

# Baseline
baseline = get_memory_mb()

# Load with lazy loading
ifc = ifcopenshell.open("large_file.ifc")  # Will use lazy loading when enabled

# Check memory after load
after_load = get_memory_mb()
print(f"Memory used: {after_load - baseline:.1f} MB")

# Access some entities (triggers materialization)
walls = ifc.by_type("IfcWall")
after_access = get_memory_mb()
print(f"After accessing walls: {after_access - baseline:.1f} MB")
```

## Implementation Details

### Key Files Modified

1. **`src/ifcparse/IfcEntityInstanceData.h`**
   - `lazy_spf_attribute_storage` struct
   - `lazy_file_offset_` field in `IfcEntityInstanceData`
   - `is_lazy()` and `materialize()` methods

2. **`src/ifcparse/IfcFile.h`**
   - `setLazyLoading()` / `isLazyLoading()` on `InstanceStreamer`

3. **`src/ifcparse/IfcFile.cpp`**
   - `skip_to_semicolon()` helper
   - Modified `readInstance()` with lazy path

4. **`src/ifcparse/IfcParse.cpp`**
   - `materialize()` implementation
   - Modified `get_attribute_value()` for auto-materialization

### How It Works

```
Normal mode:
  #123=IFCWALL('guid',#1,$,...);
  → Parse ALL attributes into memory immediately

Lazy mode:
  #123=IFCWALL('guid',#1,$,...);
  → Store file offset (position of "IFCWALL")
  → Skip to semicolon without parsing
  → On first attribute access: seek back, parse, cache
```

### Enabling Lazy Loading

```cpp
// C++ API
IfcParse::InstanceStreamer streamer(filename);
streamer.setLazyLoading(true);

while (auto inst = streamer.readInstance()) {
    // inst contains lazy data, attributes parsed on demand
}
```

## Known Limitations

1. **Forward References**: Entities with forward references (referencing entities not yet parsed) may need special handling during materialization.

2. **Thread Safety**: Materialization is not thread-safe. Use external synchronization if accessing entities from multiple threads.

3. **Memory-Mapped Mode**: Lazy loading requires the file to remain accessible. Works best with memory-mapped files or files that stay open.

## Benchmarks

Expected improvements for a 600 MB IFC file:

| Phase | Normal Mode | Lazy Mode | Reduction |
|-------|------------|-----------|-----------|
| Load  | ~6.6 GB    | ~4.0 GB   | ~40%      |
| Access all | ~6.6 GB | ~6.6 GB  | 0%        |
| Access 10% | ~6.6 GB | ~4.6 GB  | ~30%      |

The benefit is greatest when only a subset of entities are accessed after loading.
