# Fixing Lazy Loading for Geometry Processing

## Status

Lazy attribute loading (`lazy=True`) works for Python-level attribute access but **crashes when the C++ geometry iterator is used**. This document explains the root causes and the fix needed.

## What Works

- `ifcopenshell.open(path, lazy=True)` — loads file with deferred attribute parsing (~40% less memory, ~43% faster)
- Python attribute access (`entity.GlobalId`, `entity.Name`, `entity.Representation`) — triggers materialization and single-ref resolution correctly
- `file.by_type("IfcWall")` — entity type index is populated during lazy load
- `test_lazy_loading.py` passes on Python 3.12

## What Breaks

When the C++ geometry iterator (`construct_iterator`) is used after lazy loading, it segfaults. Two root causes:

### Root Cause 1: Unresolved Entity References in Materialized Data

**File**: `src/ifcparse/IfcParse.cpp`, function `IfcEntityInstanceData::materialize()` (around line 2950)

When an entity is materialized (attributes parsed from file offset), `parse_context::construct()` is called. This function handles entity references (`#123`) by pushing them to an `unresolved_references` list and leaving the attribute slot as `Blank` in the `VariantArray`. In normal (non-lazy) mode, a post-pass in `read_from_stream()` resolves all references using the `byid_` map. But in lazy mode, this post-pass only runs for entities materialized during the initial GUID check — entities materialized later (from Python) have their references permanently stuck as `Blank`.

**Evidence**: After materializing all entities from Python, accessing `entity.OwnerHistory` returns the correct entity (single ref resolved by `get_attribute_value`), but `entity.Representations` returns null (aggregate ref left as Blank).

### Root Cause 2: Empty Inverse Table

**File**: `src/ifcparse/IfcParse.cpp`, function `read_from_stream()` (around line 1660)

In lazy mode, the `InstanceStreamer` skips attribute parsing (stores file offsets instead). As a side effect, `streamer.inverses()` is empty — the inverse table (`byref_excl_`) is never populated. The geometry iterator relies on inverse lookups (e.g., `RepresentationsInContext` on `IfcGeometricRepresentationContext`) to find geometric representations. With an empty inverse table, `iterator.initialize()` returns `False` (no geometry found).

**Evidence**:
```
Normal ctx #27: 393 inverses
Lazy   ctx #27: 0 inverses
```

### Root Cause 3: Aggregate Resolution via VariantArray::set() Causes Heap Corruption

**File**: `src/ifcparse/variantarray.h`, function `VariantArray::set()`

Attempted fix: resolve references by calling `storage->set(attr_index, aggregate_of_instance::ptr)` to replace Blank slots with resolved aggregate pointers. This works for single entity refs (`IfcBaseClass*`, 8 bytes) but causes cumulative heap corruption after ~3100 calls for aggregate refs (`boost::shared_ptr<aggregate_of_instance>`, 16 bytes). The corruption manifests as a segfault in unrelated code (`construct_iterator`) AFTER the resolution completes successfully.

**Evidence**: Binary search showed LIMIT=3000 aggregates works, LIMIT=3200+ segfaults. The crash is NOT in `set()` itself but in downstream code that reads the corrupted data.

**Hypothesis**: The 16-byte `boost::shared_ptr` exactly fills the `StorageType` aligned union (also 16 bytes). The placement-new construction may interact poorly with the VariantArray's memory layout over thousands of calls — possibly due to alignment edge cases, compiler optimization assumptions, or the `destroy_at_index` -> `set` transition from `Blank` (1 byte type) to `shared_ptr` (16 byte type).

## The Fix

### Approach: Inline Reference Resolution During Construction

Instead of building a `VariantArray` with Blank slots and patching them afterward, resolve references **during** `parse_context::construct()` when the `byid_` map is available.

### Implementation Steps

#### Step 1: Add `byid_` parameter to `construct()`

**File**: `src/ifcparse/IfcFile.cpp`, function `parse_context::construct()` (line ~237)

Current signature:
```cpp
IfcEntityInstanceData parse_context::construct(
    boost::optional<size_t> name,
    unresolved_references& references_to_resolve,
    const IfcParse::declaration* decl,
    boost::optional<size_t> expected_size,
    int resolve_reference_index,
    bool coerce_attribute_count
);
```

Add an optional `byid_` parameter:
```cpp
IfcEntityInstanceData parse_context::construct(
    boost::optional<size_t> name,
    unresolved_references& references_to_resolve,
    const IfcParse::declaration* decl,
    boost::optional<size_t> expected_size,
    int resolve_reference_index,
    bool coerce_attribute_count = false,
    const boost::unordered_map<uint32_t, IfcUtil::IfcBaseClass*>* byid = nullptr
);
```

#### Step 2: Resolve refs inline in `construct()` when `byid` is provided

In `construct()`, where entity references are currently deferred:
```cpp
// Current code (around line 294):
if constexpr (std::is_same_v<std::decay_t<decltype(v)>, IfcParse::reference_or_simple_type>) {
    if (name) {
        references_to_resolve.push_back(...);
    }
}
```

Change to:
```cpp
if constexpr (std::is_same_v<std::decay_t<decltype(v)>, IfcParse::reference_or_simple_type>) {
    if (byid) {
        // Resolve inline
        if (auto* ref = std::get_if<InstanceReference>(&v)) {
            auto it = byid->find(*ref);
            if (it != byid->end()) {
                storage.set(index, it->second);
            }
        } else if (auto* inst = std::get_if<IfcUtil::IfcBaseClass*>(&v)) {
            storage.set(index, *inst);
        }
    } else if (name) {
        references_to_resolve.push_back(...);
    }
}
```

Similarly for the aggregate case (around line 317):
```cpp
if constexpr (std::is_same_v<std::decay_t<decltype(v)>, std::vector<reference_or_simple_type>>) {
    if (byid) {
        // Resolve aggregate inline
        aggregate_of_instance::ptr agg(new aggregate_of_instance);
        for (const auto& vi : v) {
            if (auto* ref = std::get_if<InstanceReference>(&vi)) {
                auto it = byid->find(*ref);
                if (it != byid->end()) {
                    agg->push(it->second);
                }
            } else if (auto* inst = std::get_if<IfcUtil::IfcBaseClass*>(&vi)) {
                agg->push(*inst);
            }
        }
        if (agg->size() > 0) {
            storage.set(index, agg);
        }
    } else if (name) {
        references_to_resolve.push_back({...});
    }
}
```

**Key insight**: This works because during `construct()`, the VariantArray slot hasn't been written yet — `storage.set(index, value)` is the FIRST write, not a replacement. This is the same code path as normal parsing. No Blank-to-shared_ptr transition. No heap corruption.

#### Step 3: Pass `byid_` from `materialize()` when loading is complete

**File**: `src/ifcparse/IfcParse.cpp`, function `materialize()` (around line 2990)

Current:
```cpp
auto data = ps.construct(identity, refs, decl, boost::none, -1, true);
```

Change to:
```cpp
const auto* byid_ptr = file_storage->loading_complete_
    ? &file_storage->byid_
    : nullptr;
auto data = ps.construct(identity, refs, decl, boost::none, -1, true, byid_ptr);
```

When `loading_complete_` is true (post-load materialization), refs are resolved inline. When false (during GUID check in `read_from_stream`), refs are deferred as before.

#### Step 4: Rebuild inverse table after all entities are materialized

**File**: `src/ifcparse/IfcParse.cpp`, in `resolve_pending_lazy_refs()` or in Python

After all lazy entities are materialized and their refs resolved, call `build_inverses()` to populate the inverse table. This can be done:
- Automatically at the end of `resolve_pending_lazy_refs()`
- Or from Python after materializing all entities

Currently `build_inverses()` segfaults on Blank aggregate slots. With inline resolution in Step 2, there should be no Blank aggregate slots after materialization, so `build_inverses()` should work.

#### Step 5: Wire it in Python (tt-speckleifc)

**File**: `tt-speckleifc/src/tt_speckleifc/importers/job.py`

Add a pre-geometry materialization step:
```python
@staticmethod
def _prepare_lazy_file(ifc_file) -> None:
    """Materialize all lazy entities and rebuild inverses before geometry processing."""
    for entity in ifc_file:
        try:
            entity[0]
        except Exception:
            pass
    ifc_file.wrapped_data.resolve_lazy_refs()
    ifc_file.wrapped_data.build_inverses()

def pre_process_geometry(self) -> None:
    self._prepare_lazy_file(self.ifc_file)
    geom_iterator = self._create_geometry_iterator()
    ...
```

And set `lazy: bool = True` in `TtImportIfcFile`.

## Files to Modify

| File | Change |
|------|--------|
| `tt-ifcopenshell/src/ifcparse/IfcFile.cpp` | Add `byid` param to `parse_context::construct()`, resolve refs inline when provided |
| `tt-ifcopenshell/src/ifcparse/IfcParse.cpp` | Pass `byid_` from `materialize()` when `loading_complete_` is true |
| `tt-ifcopenshell/src/ifcparse/storage.h` | Update `construct()` declaration in `parse_context` (if declared there) |
| `tt-speckleifc/src/tt_speckleifc/importers/job.py` | Add pre-geometry materialization step |
| `tt-speckleifc/src/tt_speckleifc/importers/ifc_file.py` | Set `lazy: bool = True` |

## Testing

1. `test_lazy_loading.py` — basic lazy loading validation
2. `tt-speckleifc` full test suite: `uv run pytest tests/ -v --ignore=tests/test_memory_large_ifc.py`
3. Manual test:
```python
import ifcopenshell
from ifcopenshell.geom import iterator, settings
from ifcopenshell import ifcopenshell_wrapper

f = ifcopenshell.open('samples/Ifc2x3_Duplex_Architecture.ifc', lazy=True)
for e in f: e[0]  # materialize all
f.wrapped_data.resolve_lazy_refs()
f.wrapped_data.build_inverses()

s = settings()
s.set('triangulation-type', ifcopenshell_wrapper.TRIANGLE_MESH)
it = iterator(s, f, 1, geometry_library='hybrid-opencascade-cgal')
assert it.initialize()  # should be True
```

## Implementation Status (2026-04-05)

**Steps 1-3 DONE** — C++ inline reference resolution works. The geometry iterator succeeds after lazy loading.

### Additional Root Cause Discovered: Dangling Simple Type Instances

**Root Cause 4**: `materialize()` creates a temporary `in_memory_file_storage` for re-parsing. When entities contain SELECT-type values wrapping primitives (e.g., `IFCREAL(0.0174)` in `IfcMeasureWithUnit`), `load()` creates simple type instances owned by `temp_storage.read_simple_type_instances`. When `temp_storage` goes out of scope, these instances are freed, leaving dangling pointers in the entity's VariantArray. This causes a segfault in `build_inverses()` when it traverses entity attributes.

**Fix**: Transfer simple type instances from `temp_storage` to the file's main `read_simple_type_instances` before `temp_storage` goes out of scope.

### Additional fix: `resolve_pending_lazy_refs()` re-parses from file offset

Instead of patching individual VariantArray slots (which triggers Root Cause 3 heap corruption for aggregates), `resolve_pending_lazy_refs()` now re-parses each entity from its saved file offset using `construct()` with `byid_`. The `pending_lazy_refs_` map now stores `(file_offset, refs)` pairs.

## Current State of Code

The following changes are in the working tree:
- `src/ifcgeom/ConversionResult.h` — `#ifndef SWIG` guard for `enable_if_t` template (needed for build)
- `src/ifcparse/IfcEntityInstanceData.h` — `lazy_spf_attribute_storage`, `is_lazy()`, `materialize()` (from earlier commits)
- `src/ifcparse/IfcFile.h` — `resolve_lazy_refs()` method on IfcFile
- `src/ifcparse/IfcFile.cpp` — `construct()` with optional `byid` param for inline ref resolution
- `src/ifcparse/IfcParse.cpp` — `materialize()` with inline resolution + simple type transfer, `resolve_pending_lazy_refs()` with re-parse from file offset
- `src/ifcparse/storage.h` — `pending_lazy_refs_` now stores `(offset, refs)`, `construct()` declaration updated with `byid` param

## Build Commands

```bash
cd tt-ifcopenshell/build312
source ~/miniforge3/bin/activate ifcos-py312
make -j$(( $(nproc) / 2 ))

# Copy to both locations:
cp build312/ifcwrap/_ifcopenshell_wrapper.cpython-312-x86_64-linux-gnu.so \
   src/ifcopenshell-python/ifcopenshell/
cp build312/ifcwrap/ifcopenshell_wrapper.py \
   src/ifcopenshell-python/ifcopenshell/
```
