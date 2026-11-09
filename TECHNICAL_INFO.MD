# FM5 Top Gear to FH2: technical implementation reference

Documented on 9 November 2026. Project: TrackLoader / TrackLoaderCPS.

This document describes the actual conversion, native runtime, experiments, and
confirmed fixes in this workspace. It is not a claim that arbitrary FM5 tracks
can now be converted automatically, nor that experimental map travel is safe for
every career/save. The companion [node graph](PORT_GRAPH.md) covers the conversion
dependencies, runtime decisions, issue-resolution chain, and every node call in
the current generated custom stateflow.

## Contents

1. [Current result and evidence boundaries](#1-current-result-and-evidence-boundaries)
2. [Source, destination, and identities](#2-source-destination-and-identities)
3. [Source inventory and native format changes](#3-source-inventory-and-native-format-changes)
4. [Geometry and texture streaming](#4-geometry-and-texture-streaming)
5. [Visibility, placements, and root assembly](#5-visibility-placements-and-root-assembly)
6. [Collision and physical surfaces](#6-collision-and-physical-surfaces)
7. [Navigation and static environment](#7-navigation-and-static-environment)
8. [Archive encoding and mounting](#8-archive-encoding-and-mounting)
9. [World textures: what was and was not converted](#9-world-textures-what-was-and-was-not-converted)
10. [Shader compatibility and lighting](#10-shader-compatibility-and-lighting)
11. [Grass, instancing, LODs, and residency](#11-grass-instancing-lods-and-residency)
12. [Pavement, missing paint, and normal instability](#12-pavement-missing-paint-and-normal-instability)
13. [Browser texture conversion](#13-browser-texture-conversion)
14. [Database registration and pack discovery](#14-database-registration-and-pack-discovery)
15. [Native ownership and custom travel](#15-native-ownership-and-custom-travel)
16. [Persistent activation, Airport return, and saving](#16-persistent-activation-airport-return-and-saving)
17. [Standalone and CPS dispatch](#17-standalone-and-cps-dispatch)
18. [Native DLC browser and automatic travel](#18-native-dlc-browser-and-automatic-travel)
19. [Disappearing tile caption: final working solution](#19-disappearing-tile-caption-final-working-solution)
20. [Media revision ledger](#20-media-revision-ledger)
21. [Reproduction and build procedure](#21-reproduction-and-build-procedure)
22. [Validation and regression testing](#22-validation-and-regression-testing)
23. [Issue-resolution reference](#23-issue-resolution-reference)
24. [Remaining limitations](#24-remaining-limitations)
25. [Code and evidence index](#25-code-and-evidence-index)

## 1. Current result and evidence boundaries

The owned FM5 Top Gear world has been converted into an additive FH2 map with
track/environment ID **10000**, logical media namespace **TopGearCPS**, and an
independent native entry/return lifecycle.

Observed in game:

- Geometry and sky load; the car contacts the road, drives, and steers.
- Surface grip is corrected by name-based FM5-to-FH2 material mapping.
- Previously missing road/runway paint appears; X/arrow flicker was corrected
  while retaining its original paint and normal textures.
- Red grass and black/red terrain strips have targeted compatibility workarounds.
- A small-map residency policy prevents observed drive-away/return texture
  corruption by keeping the relevant resources requested, not by repairing the
  underlying upload/reload defect.
- Repeated custom entry and Airport return work. A standalone round trip in the
  real Azimuth installation ended with the log reporting verified Airport arrival
  and saving enabled.
- The standalone DLC/Tracks browser opens without CPS or installed Storm Island.
  The caption remains visible across tested gallery returns and pause reopenings.

Evidence must be interpreted at the correct layer:

| Evidence | What it establishes | What it does not establish |
|---|---|---|
| Serializer byte roundtrip | The implemented native schema reproduces reference bytes | Engine acceptance of every new semantic value |
| Catalog/hash validation | The intended bytes and package closure are present | Visual correctness, driving, or safe saves |
| Shader disassembly / D3D shader creation | Valid patched shader bytecode | Correct live binding, material response, or motion stability |
| Native map/car/flow readiness | The owned transition completed its checked contract | Every mesh/material visibly correct |
| Screenshot | The captured visible state | Moving-camera flicker or persistence after unloading |
| Repeated live entry/return | Tested lifecycle behavior in that installation | General save/career integrity |

Some persisted builder reports still contain `engine_validated=false`,
`playable_map=false`, or historical blocker text. Those were deliberately
conservative build-time reports, not a live certification database. Do not edit
them to manufacture success or treat their old prose as the current runtime state.
The older [README history](README.txt) also contains explicitly superseded stages.

## 2. Source, destination, and identities

### Source and native references

Source game:

```text
E:\FM5_1.0.0.20_x64\Forza_1.0.0.20_x64__8wekyb3d8bbwe
  Mount\media\tracks\topgear
  Mount\media\db\gamedb.slt
  Mount\media\physics\surfaceTypes.xml
```

Native FH2 reference:

```text
E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount
  media\tracks\anthem
  media\tracks\homespace02
  media\tracks\test03
  media\Stripped\gamedbRC.slt
  media\physics\surfaceTypes.xml
  media\UI.zip
  media\stateflow.zip
```

The runtime is signature-checked against the FH2 executable built on
24 November 2015: PE timestamp `0x565501ED`, image size `0x03A18000`.
Game addresses in this document are RVAs, not portable API addresses. Azimuth
maps the game inside its own process; the preferred game base is `0x140000000`.

| Identity | Value / meaning |
|---|---|
| Mainland | Track 542, Anthem |
| Native Storm Island | Track 549; stock entitlement/install checks retained |
| Top Gear | Track 10000, environment 10000 |
| Media namespace | TopGearCPS |
| Default lifecycle namespace | trackloadercps |
| Top Gear metadata | Author suderek; version 0.3.2-beta |
| Project | This folder, TrackLoaderCPS |
| Mod platform source | [FH2-Mod-Platform](../FH2-Mod-Platform) |
| CPS source | [CarPackStudio](../CarPackStudio) |
| Texture helper | [SwatchbinConverter](../Forza/SwatchbinConverter) |

### Logical paths are not stock-file installation destinations

The engine asks for `game:\media\tracks\TopGearCPS\...`.
The verified package is stored beneath `fh2-mods\CustomTracks\TopGear`.
Publication uses the actual game's **Mount\mediaoverride**, through platform
`set_file`, with native archive mounts for archive members.

Do not rewrite shader/material URIs to disk `mediaoverride` paths. Do not install
custom files over stock Anthem assets. The game's logical namespace remains
`game:\media`; the mounted override/native archive devices resolve it.

The earlier attempt to put the entire custom track directly in stock `media`
was rejected during investigation. It is not the final deployment method.

## 3. Source inventory and native format changes

The audit in [port_topgear.py](port_topgear.py) established that copying the FM5
folder or changing version words would not produce an FH2 track.

| Family | FM5 source | FH2 target / treatment |
|---|---|---|
| Instance map | Unversioned IMAP; 8-byte geometry references | Version-1 reference uses `PAMI`, 7-byte references and counted additional model mappings; not header-patched |
| World visibility | FPVS v50; 76-byte texture descriptors and source world tables | Complete streamed FPVS v60 root; 56-byte descriptors and native counted tables |
| Geometry storage | Model/texture payloads inside source bin.zip | Native PGZP v100 GeoChunk archive plus ChunkMap keys |
| Visibility cells | Race-route waypoint visibility | Native PVSZ zones selected through a new free-roam HEXY grid |
| Collision | Big-endian legacy .col envelope v2 | FH2 streamed type-1 physics templates, roots, requests, surface remapping |
| Navigation | MLP named tables in AI.zip | NAVW v0x100 and OWWF v0x102, closed road and disabled AI metadata |
| Shader scenes | LSCE/DBLS v1.3 | Parsed compatible source scenes with relocated paths; compiled DXBC patched where required |
| UI art | FM5 RGBA8 tiled swatchbin | FH2 BC7 tiled swatchbin with native template envelope |

Verified source/world counts:

- FM5 PVS: **2,909,664 bytes**, fully consumed by the implemented reader.
- **3,567** instance/material-binding records.
- **3,187** primary and **380** secondary draw placements.
- **1,387** full row-vector transform groups.
- **2,161** per-instance material override bundles.
- **61** source visibility arrays: 58 route views plus 3 special views.
- **62** texture hash aliases; duplicate targets with distinct alias keys remain.
- Geometry component: **1,376** model payloads and **4,796** streamed texture
  payloads; **2,301** texture descriptors and resident texture containers.
- **38** source-declared streaming variants were absent from the source archive.
  They are recorded, not synthesized or silently substituted.

Model payload count, transform-group count, requested model closure, and draw
count are different concepts. Do not use 1,387 transform groups as an archive
model count or 3,567 draw placements as a unique-model count.

## 4. Geometry and texture streaming

Primary implementation:
[build_geometry.py](build_geometry.py), [fh2_world.py](fh2_world.py),
[fm5_pvs.py](fm5_pvs.py), [fm5_world.py](fm5_world.py).

### Asset container validation

The geometry builder validates the `burG` container, declared total size, section
directory, section/payload bounds, and accepted blob versions:

| Decoded blob | Accepted version |
|---|---|
| Skel | 0.0 |
| MatI | 1.0 |
| Mesh | 1.7 |
| IndB | 1.0 |
| VLay | 1.1 |
| VerB | 1.0 |
| Modl | 1.1 |
| Skin | 1.0 |
| Texture TXCB | 2.0 |

Tags may appear byte-reversed in the binary directory: for example `BCXT` for
TXCB. A model must actually contain the required model/mesh/layout sections;
recognizing the outer magic alone is insufficient.

Compatible model and texture payload bytes are retained. The important conversion
is their addressing, enclosing world metadata, dependencies, and runtime request
mechanism, not a blanket model re-export.

### Packed ChunkMap resource keys

Each ChunkMap entry is one little-endian uint32:

```text
packed_key = (resource_type << 24) | resource_id
```

| Type | Meaning |
|---|---|
| 1 | Streamed collision template |
| 4 | Model |
| 5 | Texture streaming payload |
| 12 | PVSZone |

A texture's resource ID uses its descriptor ordinal and four-bit level field:

```text
texture_key = 0x05000000 | (descriptor_index << 4) | (streaming_level - 1)
```

The base filename is level 1; suffix `_2` is level 2, and so forth. There is no
invented separate ChunkMap quality byte. Resident textures are a separate
descriptor-indexed STex store, not an extra streamed suffix.

The order of keys must match the order of entries in the archive. For example,
adding a probe payload without updating the parallel key table is invalid.

### Why the final package uses one geometry chunk

Early components used separate chunks for geometry, zones, and collision.
The live loader did not perform a free global resource search for every zone:
zone-associated resources were looked up through that zone's assigned chunk.

Assigning all zones to chunk 1 while leaving their models/textures in chunk 0
produced model entry index -1, empty resource names, and failed load state 6.
Additionally, the native open loop used the highest assigned chunk index to
determine how many archives to open, so an unassigned chunk 2 was never opened.

The verified Top Gear policy is:

1. Merge models, textures, zones, and collision resources into GeoChunk0.
2. Preserve their unique packed keys and archive/key ordering.
3. Set all 288 ChunkAssign values to zero.
4. Remove obsolete ChunkMap1/2 and GeoChunk1/2 from the new output.

This is a simple, valid small-map layout, not a universal streaming strategy for
large worlds.

## 5. Visibility, placements, and root assembly

Primary implementation:
[build_zones.py](build_zones.py), [spatial.py](spatial.py),
[build_spatial.py](build_spatial.py), [build_bindings.py](build_bindings.py),
[fh2_pvs.py](fh2_pvs.py), [build_root.py](build_root.py).

### PVSZ schema

The binary tag is **five bytes**: `PVSZ\0`. After it come counted arrays for draw
references, model IDs, texture descriptor ordinals, uint16 quality words,
dependency pairs, and auxiliary/final tables.

Interpreting the tag as four bytes shifts every subsequent table. Native schema
roundtrips were checked against 95 HomeSpace02 and 553 Anthem zones.
The legacy-quality conversion uses low byte 1 and high byte source quality.

A dependency record's first field is a runtime node index; its second field is a
packed resource key. They are not two interchangeable resource IDs.
PVSZLookup is a raw uint32 array mapping spatial ordinals to zone requests.
The initial combined zone/physics namespace had 288 + 210 = **498** runtime nodes.

### Free-roam spatial coverage

FM5 route-view selection depends on waypoint index/25. Top Gear has 1,435
waypoints, giving 58 route views plus 3 special views. Simply retaining this
race-only selector is not a valid general free-roam visibility solution.

The port builds a native HEXY v101 staggered-hex grid:

- 24 by 12 cells, 288 zones, radius 100.
- Coverage checked against every source collision vertex and waypoint.
- Native single-precision staggered-hex coordinate math.
- Nearby source route views and adjacent views combined at crossings.
- All three special source views retained conservatively.
- Native v100/v101 references roundtrip; v101 includes neighbor masks.

This is conservative visibility synthesis, not a newly baked occlusion solution.

### Native v60 root

Original HomeSpace02 (391,018 bytes) and Anthem (24,200,394 bytes) roots
roundtripped byte-for-byte through the v60 schema.

The source draw record is 73 bytes:

| Byte offset | Content |
|---|---|
| 0..3 | Model ID uint32 |
| 4..11 | Draw flags uint64 |
| 12, 14, 16 | Three uint16 fields, including old instance index |
| 18..25 | Four half-float LOD distance bands |
| 26 | Source flag byte |
| 27..38 | Position XYZ float32 |
| 39..56 | Rotation, nine half-floats |
| 57..72 | Four further float32 source fields; do not assume undocumented semantics |

FH2 v60 adds four per-band selection bytes, optional settings, and a 16-byte
extra vector. Constructor defaults were initially serialized, then LOD selection
was repaired from actual model-group references.

FH2 initialization clears the secondary table, so all 380 source secondary
placements are promoted into the usable primary representation. An audit also
found 381 placements absent from every source-backed route view. Their 44 models
and 101 dependent textures were requested globally rather than merely leaving
invisible records in the root. All **3,567** placements are retained.

The opaque optional FM5 route tree is omitted through the native absent-table
flag; it is not misinterpreted as draw IDs. Unsupported optional extra-world
schemas are rejected explicitly.

The later map-wide policy supersedes much of the selective request policy for
this small map; see [residency](#11-grass-instancing-lods-and-residency).

## 6. Collision and physical surfaces

Primary implementation: [collision.py](collision.py),
[build_bindings.py](build_bindings.py), [repack_media.py](repack_media.py).

### Structural conversion

The first two big-endian integers in the source .col are grid dimensions, not
version words. The real envelope has version 2 and marker `0xABCD1234`.
The reader validates cell directories, 2048-byte cell alignment, geometry
boundaries, counts, finite values, triangle vertex pointers, and subcell lists.

Conversion produces:

- 210 native physics templates.
- 65,236 vertices and 53,862 triangles.
- Vertex byte pointers converted to indices.
- Dense counted uint16 subcell triangle lists.
- Signed subcell offsets, with -1 for absent cells.
- Original XYZ, normals, and packed vertex metadata.
- Homogeneous target vertex W=1, rather than reusing a non-spatial source lane.
- World-space identity roots with bounds derived from source cells and heights.

The two original triangle attribute bytes retain their order. A ground word
`0x00770000` decodes to `(0,119)`, not `(119,0)`.
Native physics serialization was checked against 529 HomeSpace02 templates.

### Why the car originally fell through a correctly decoded road

Having physics payloads and root entries was insufficient. The PVSZone auxiliary
collision-request array was empty. Native collision instantiation consumes this
array; it does not infer instances from the generic dependency list alone.

`--zone-collision` places all 210 collision-root ordinals into every zone's
auxiliary array. Stock HomeSpace02 uses similarly conservative requests.
The car then stayed at road height. This increases collision residency but keeps
the required instance request path explicit.

### Why grip initially felt like ice

The second triangle attribute byte indexes each game's surfaceTypes.xml.
Matching numeric indices do not guarantee matching surfaces.

The decisive mismatch was FM5 index 119 **AsphaltTrackPenalty** versus FH2
index 119 **Snow**, whose friction was 0.2.

The remapper resolves names against the actual source/destination XML tables.
Explicit fallbacks include:

- AsphaltTrackPenalty -> AsphaltTrack.
- OffTrackVehicleBarrier -> ConcreteBarrier.

Other moved indices, including 127, 132, and 133, are handled through the same
name lookup, not hardcoded index copying. The persisted v16 pass modified
**6,163 triangles** across 210 templates. The user confirmed normal grip.
Unknown flag-bit semantics are not invented as part of this fix.

## 7. Navigation and static environment

Primary implementation: [navigation.py](navigation.py),
[openworld_navigation.py](openworld_navigation.py),
[build_openworld.py](build_openworld.py), [build_environment.py](build_environment.py).

The FM5 AI.zip contains typed MLP tables, 1,435 waypoints, and 18 segments.
The reader validates names, int/int32 aliases, byte tables, offsets, payload
coverage, overlapping tables, finite coordinates, and complete consumption.

Copying Track.geo was rejected: the FH2 variant contains a six-word iCRC field
absent from FM5. Zeroing or inventing this integrity data was not used.

Instead, the free-roam port writes:

| File | Native schema / purpose |
|---|---|
| TopGearCPS_00.nav | NAVW v0x100; 112-byte table header, nine separately 16-byte-aligned vectors |
| TopGearCPS_00.oww | OWWF v0x102; waypoint world/cell queries |
| TopGearCPSout_00.hex | HEXY v101; free-roam visibility selector |

NAVW and OWWF share a deterministic generation stamp and repeat the 16-byte
envelope as a footer. NAVW pointer fixups cover ten reference families.
Reference Anthem NAVW (1,691,824 bytes) and OWWF (5,568 bytes) roundtrip exactly.
An empty OWWF is not valid; it requires positive cell coverage.

The converted closed road has 1,435 nodes, 1,436 ordered references, reverse
incidences, and 98 waypoint cells of size 200. Coverage includes source waypoints
and collision vertices. AI and traffic are deliberately disabled through native
disabled-road metadata (`none_10000`), not advertised as a working FM5 race AI.

### Static-only dynamic-object boundary

The environment uses native FH2 TrackSettings structure while preserving audited
source fields, plus converted locators and collision-backed arrival information.
The explicit static-test profile writes:

- PhysicsDefinitions.bin: exactly eight zero bytes, a native uint64 count of zero.
- CollObjs.xml: an empty CollObjs root.

This follows the actual zero-count reader path. It does not fabricate a version
or transplant unrelated stock dynamic-object definitions. Ground/road collision
remains present; visual cones/barrels do not thereby gain movable-prop physics.

## 8. Archive encoding and mounting

### GeoChunk PGZP v100 is not an ordinary ZIP

[fh2_world.py](fh2_world.py) writes and rereads the minizip. The key live finding
was that the v100 native inflater requires a **4 KB zlib window**:

```python
compressor = zlib.compressobj(6, zlib.DEFLATED, 12)
```

Using default zlib with a 32 KB window produced streams the Python reader could
inflate but the game could not. The resulting parser crash appeared to be a
missing model `Modl` section, downstream of decompression failure.

Each compressed entry is padded to four-byte alignment. Flag bits 14..15 encode
the trailing padding count; lower bits encode the method. The native v101 path
uses raw-deflate semantics and must not be conflated with this v100 stream.

### Native bin.zip

[build_media.py](build_media.py) creates a native-compatible shader/support ZIP:

- Deflated members using raw DEFLATE (`wbits=-15`).
- No local-header extra fields.
- Central-directory extra field **0x1123**, size 4, containing the absolute offset
  of that member's compressed data.
- Member order, CRC, offsets, contents, and hashes verified after writing.

A plain unpacked `bin` folder did not substitute for the native mounted archive.
Changing only the destination directory also failed because resource devices
and member lookup semantics differed.

### Runtime mounts, not rewritten boot zipmanifest

The game reads zipmanifest during boot, before slow mod pack validation completes.
Publishing a new zipmanifest late did not reliably register custom archives.
Worse, killing a test left a stale override manifest pointing to old chunk sizes
or a removed GeoChunk2; the next boot crashed in the manifest reader.

Final solution: [track_mount.h](track_mount.h) follows the native DLC archive
mounting mechanism:

1. Wait for compatible game code and validate relevant signatures.
2. Resolve a logical custom archive path through the native file system.
3. If runtime publication is not indexed there yet, verify the published file
   under the actual game directory's mediaoverride and resolve that device path.
4. Construct the native archive volume with native allocation/string ABI.
5. Mount its logical member namespace with native priority and reference ownership.

The current map mounts bin.zip and GeoChunk0.minizip. No custom boot zipmanifest
publication is required.

### Loose-resource first-launch caveat

HEXY/OWWF files created only after the native boot index was built sometimes
failed as File Resources even though they existed on disk before travel.
HEXY failure selected a root-data fallback whose required pointer was null,
causing `game+0x13E2A10` during spatial queries.

Keeping published loose files across launches and restarting after first
installation avoided the observed failure. Do not delete the published map
folder immediately before a test and assume late publication is equivalent to
boot-time availability. The beta instructions retain this restart caveat;
there is no claim of a general native file-index refresh implementation.

## 9. World textures: what was and was not converted

### World payload preservation

The port does **not** decode and recompress every world texture to BC7.
Most source world swatchbin payloads already use accepted native texture blobs.
They are validated, repacked into streamed resources, and rebound through FH2
descriptors/world metadata.

The source AssetManifest compression census for all 2,301 descriptors is:

| Source compression | Descriptor count | Typical role / destination treatment |
|---|---:|---|
| UnsignedBc4 | 1,463 | Scalar maps including lightmaps/masks; compressed bytes retained |
| Bc7 | 470 | Colour/material textures; compressed bytes retained |
| UnsignedBc5 | 355 | Two-channel data, including normals; bytes retained |
| SignedBc5 | 8 | Signed two-channel data; bytes retained |
| UnsignedBc6H | 5 | HDR texture data, including probes; bytes retained |

These are manifest compression labels, not a newly inferred semantic assignment
for every individual texture. Relevant standard DXGI equivalents include
BC4_UNORM 80, BC5_UNORM 83, BC5_SNORM 84, BC6H_UF16 95, and BC7_UNORM 98.
The native serialized format words are not simply these DXGI enum integers.

The LightMapType census is 1,005 Default, 689 Direct0, 603 LightmapAO,
2 CubemapProbeSpecular, and 2 CubemapProbeDiffuse.
Direct0 and LightmapAO sampling compatibility required shader changes even when
their stored compression was supported.

### Texture descriptor translation

FM5's 76-byte PVS record becomes an FH2 56-byte record:

```python
fh2_descriptor = source_descriptor[:4] + source_descriptor[24:]
```

This retains the hash and schema-compatible tail while removing the source-only
20-byte segment. It is applied after manifest ordinal/hash validation, not as a
blind byte deletion in arbitrary files.

Resident STex bundles preserve their descriptor-indexed payload relationships.
Streamed payloads are mapped by descriptor ordinal and actual available levels.
Visual material correctness is not implied by descriptor-size compatibility.

### Diffuse probe exception: resident-only to streamed level 1

FM5's two diffuse probes were SmallOnly with QualityLevels=0. FH2 presentation
readiness required both selected specular/diffuse probe textures to be streaming
and ready; resident-only diffuse records never satisfied that contract.

`--stream-probe-diffuse`:

1. Identifies CubemapProbeDiffuse entries through the manifest.
2. Validates the resident payload (32x32 cubemap, 6 mips, 49,235 bytes total:
   0x53-byte envelope plus 0xC000 data).
3. Reuses that byte-compatible payload as streamed level 1.
4. Replaces the descriptor tail with the audited native FH2 diffuse-probe tail.
5. Adds packed keys **0x05000280** and **0x050002A0** in sorted texture-key order.
6. Rewrites root, archive, ChunkMap, file hashes, and catalog.

This is a request/descriptor adaptation, not a lossy cube re-encode.

## 10. Shader compatibility and lighting

Primary implementation: [shader_scenes.py](shader_scenes.py),
[shader_patch.py](shader_patch.py), [repack_media.py](repack_media.py).

All 36 source shader families have the required **1,152 compiled programs**.
LSCE/DBLS v1.3/v1.4 structures and both platform program closures were parsed.
No scene-version header was spoofed to pretend the layouts were identical.

The live FH2/Azimuth configuration loads **.durango.pso/.vso**, despite running
on PC. Diagnostics changing only .pc programs did nothing. All permanent relevant
passes therefore process both variants. Both contain usable DXBC SM5 bytecode.

### Light-scenario constant buffer

FM5 and FH2 place different data in lightScenarioCBuffer at cb3. Source shaders
were reading fog/specular/reflection data from unrelated FH2 rows.
Rows are float4 registers of 16 bytes.

| FM5 cb3 row | FH2 row | Purpose |
|---|---|---|
| 0..19 | unchanged | Initial light/shadow tuning and matrices |
| 20..24 | 53..57 | Fog |
| 25 | 104 | Cubemap/specular tuning |
| 26..29 | 1134..1137 | Projected-shadow transform |
| 30 | 1138 | Dynamic-track-object selection |
| 31 | 1139 | Planar-reflection plane |
| 32..35 | 1140..1143 | Reflection-map transform |

The target cb3 declaration has **1,144 rows / 18,304 bytes**.
Unexpected source rows fail instead of being assigned guessed destinations.
Used globalCBuffer cb2 rows were checked separately; they were not shifted merely
because cb3 changed.

### Why the ground was black

FM5 static pixel shaders sampled Direct0 baked direct-shadow data at t3.
FH2 did not bind that source lightmap path. Even a loaded colour texture therefore
received effectively no sun. Replacing instance slot 0 with slot 2 did not fix it.

The permanent base lighting pass remaps cb3 and replaces the source baked-shadow
sample with full visibility `(1,1,1,1)` for the audited lighting scenarios.
The initial pass changed 208 programs and 126 baked-shadow samples.
This intentionally removes that authored baked direct-shadow contribution; it
does not recreate FM5 lighting exactly.

Other source input roles include t0/t1 probes, t5 projected shadow, t6 legacy AO,
material inputs around t16..t22, and packed depth maps at t104.
Native FH2 uses different terrain-AO/shadow resources, including t107 and
CSM/PSSM resources around t108..t114.

`--neutralize-legacy-ao` replaces the incompatible t6 AO sample with white.
It changed 126 programs and improved initial object textures, but did not solve
their later drive-away/return corruption.

The accepted media chain also retains an earlier shadow diagnostic:
t104 packed shadow samples replaced with zero (the source shader computes
visibility as one minus sampled occlusion) and t5 projected samples with one.
Its report is `diag_ground_shadows`: 198 programs, 660 cascade samples,
462 projected samples. This is a retained compatibility compromise, not a
complete translation to FH2 dynamic shadows. It has no dedicated production
CLI option in the current repacker; do not silently omit it when claiming an
exact media-v32 rebuild.

### DXBC patch integrity

The patcher decodes instructions/operands rather than doing unconstrained byte
replacement. It handles immediate operands, swizzles, modifiers, declaration
sizes, SHEX/SHDR chunks, new chunk offsets, and total container length.
It recalculates the DXBC-specific MD5-derived checksum over bytes 20 onward.
Non-code chunks and unrelated sample/destination behavior are preserved.

Validation includes source checksum comparisons, idempotence for supported
passes, disassembly, native shader creation experiments, and finally live checks.
Do not interpret a successful CreatePixelShader as proof of correct bindings.

## 11. Grass, instancing, LODs, and residency

### Grass fade and red tint

FM5 trackParamsCBuffer uses a 32-byte layout while FH2 uses 64 bytes.
Source grass shaders read cb7[1].x/y as fade start/end; these are not the same
target contract. `--bake-grass-fade 75 100` substitutes the source TrackSettings
distances as immediates, preserving operand semantics (12 programs, 36 operands).

Red grass was isolated to incompatible macro RGB tint paths.
`--neutralize-macro-tint` targets grass_blend_002 and
terrain_3blend_ch4overlay. Only sampled macro RGB becomes white; authored alpha,
blend masks, and base diffuse detail remain. The later terrain pass separately
targets terrain_3blend_macrodiffuse.

This removes macro colour variation; it does not repair the original binding/UV
contract.

### Grass depth flicker

`--separate-grass-depth` raises the two static grass rendering layers by **5 cm**.
Physics geometry is not lifted. Driving tests confirmed the grass-layer flicker
stopped. This successful depth workaround must not be confused with later failed
paint-marking height lifts.

### Instanced pose stride

Source vertex shader t64 pose records use 272-byte structured records.
FH2 appends a reflection plane, making the stride **288 bytes** while retaining
earlier field offsets.
`--patch-instancing-poses` adjusts both declarations and structured-load stride
extensions: 792 programs, 792 declarations, and 4,608 loads in the persisted pass.

### Prop LODs: three separate concerns

1. **Cull distance:** some tyre/barrier draws used 0,0,0,25 and disappeared beyond
   25 m. Raising the final cull limit to 500 m improved these.
2. **Per-band selection:** constructor-default `7f7f7f7f` offsets did not encode
   actual FH2 model-group selection. `--repair-draw-lods` derives native offsets
   from group relationships, not by assigning arbitrary identical bytes.
3. **All-default bands:** all-minus-one source bands fell back badly in FH2.
   `--default-draw-distances 100 200 350 500` only replaces those complete default
   patterns; it does not overwrite authored distances.

Changing only TrackSettings LOD groups or only the final cull distance was not a
complete LOD fix.

### Accepted small-map workaround: 5 km global residency

Object textures initially looked correct after AO correction, then became corrupt
after driving away and returning. Comparable source/destination BC7 descriptors
and payload headers were compatible; that alone did not establish safe reload.

The accepted workaround, `--map-wide-distance 5000`, sets every final cull band
to at least 5,000 m, including partially default patterns. It preserves earlier
thresholds, transforms, physics, and existing longer cull limits.
Every zone requests the same full closure:

- 3,567 draws.
- 1,301 models, including needed LOD models.
- 2,053 textures after group/per-instance/material dependency expansion.
- 288 zones.
- Highest available actual streamed level; resident-only dependencies keep level 0.

The initial union had 1,905 textures; 148 group/instance/material dependencies
were added in v26. Keeping the map resident was user-accepted for this small
track. It increases memory/VRAM/rendering cost and masks, rather than fixes, the
unresolved resource eviction/reload/upload problem.

## 12. Pavement, missing paint, and normal instability

### Wrong asphalt/concrete images and black rectangles

Observed artifacts included oversized wheel/logo/safety-sign imagery and
camera-dependent dark patches on pavement.

Rejected/incomplete approaches:

- Removing channel-five overlays hid some giant markings but left black areas.
  Those authored overlays were restored.
- Legacy shadow neutralization helped some ranges but was not sufficient.
- Correct base colour decoding and global request closure alone did not solve it.

The accepted pavement fixes are deliberately narrow:

| Pass | Scope | Intentionally removed |
|---|---|---|
| --neutralize-pavement-skid | Programs declaring SkidmaskTextureTexture; reflected slot lookup, not every t16 | Authored baked skid-mask influence |
| --neutralize-blended-pavement-ao | road_2blend_threshold_001 | That material's AO-mask contribution |
| --neutralize-terrain-macro-tint | terrain_3blend_macrodiffuse | Macro RGB tint variation |

The skid pass changed 36 programs. The blended-pavement AO pass changed 6.
Base diffuse/normals, channel-five overlays, vertex weights, alpha, geometry,
and collision are preserved.

The user confirmed the concrete correction and later the black/red strip
correction. Pale asphalt and safe restoration of authored skids remain separate
limitations. These are not a comprehensive texture-binding repair.

### Missing runway and road paint: draw classification

Height lifts and solid-magenta paint shaders did not make initially missing
paint appear. The decisive fix was changing the audited draw class:

```text
0x1FFF1214 -> 0x0FFF1024
```

`--repair-topgear-markings` selects only models whose embedded materials are all
roadlines_topg_* (and contain marking material), then changes only the relevant
uint64 draw flag bytes. This restored 29 draws without altering paint textures,
shaders, position, depth, physics, or LODs.

Additional missing runway numbers/arrows belonged to mixed-material models.
`--repair-topgear-runway-markings` requires at least one roadlines_topg_* material
and allows only these audited companions:

- topg_runwaygrates
- topg_reflactor_001
- topg_harrierlandinglights_001
- topg_harrierlandinglights_002
- topg_harrierpane

It restored 32 more draws. Unrelated mixed models and standalone lights/grates
are excluded. The individual native flag-bit meanings are not fully decoded;
the known working classification is applied narrowly, not globally.

### X/arrow flicker: normal reconstruction, not polygon depth

After restoring those draws, some X/arrow surfaces flickered near the camera.
One- and five-centimeter height offsets did not help. Removing alpha/discard
made the result worse. A flat-normal diagnostic stopped flicker, isolating the
normal path.

Affected colour textures were BC7; normals were BC5. The source RoadLineDecal
shader reconstructed the third normal component using:

```text
z_squared = 1 - x*x - y*y
z = sqrt(z_squared)
```

Invalid/unexpected sampled values could produce negative-root NaNs or degenerate
normals. `--guard-marking-normals` retains original samples and adds:

```text
z = sqrt(max(z_squared, 0.000001))
```

The patch guards the audited first reconstruction sqrt only, in Track/Debug
pixel programs for both platforms. Four programs changed. Cubemap has no matching
normal reconstruction; unrelated later square roots are not modified.

Original normal textures, paint RGB/alpha, discard logic, geometry, and heights
are retained. The user confirmed the flicker stopped. The normal guard does not
claim to fix why the texture/binding produced problematic values.

## 13. Browser texture conversion

This is the separate path that actually performs pixel decoding and re-encoding.
Primary implementation: [build_thumbnails.py](build_thumbnails.py),
[build_swatchbins.cs](../Forza/SwatchbinConverter/build_swatchbins.cs),
[build.ps1](../Forza/SwatchbinConverter/build.ps1).

### Source artwork

Inside FM5 Mount\media\ui\textures\data_bound:

| Archive/member | Source dimensions | Format / layout |
|---|---|---|
| Track_Posters.zip / TopGear.swatchbin | 1085 x 611 | RGBA8, DXGI 28, XG tile mode 14, one mip |
| Track_Logos.zip / L_TRK_TopGear.swatchbin | 200 x 200 | RGBA8, DXGI 28, XG tile mode 14, one mip |

The source descriptor bytes checked by the script are **ae010090**.
The original bundle lengths are 2,785,363 and 229,459 bytes. Tiled allocation
size, not width*height*4 alone, determines the actual payload requirement.

### Native FH2 target templates

From FH2 Destinations.zip:

```text
Rivals_Destination_Images/StormIsland_Large.swatchbin
Rivals_Destination_Images/StormIsland_Small.swatchbin
```

| Output | Dimensions | Native layout | Bundle bytes |
|---|---|---|---:|
| Poster | 1390 x 516 | BC7_UNORM / DXGI 98, tile mode 14, one mip | 983,123 |
| Logo | 290 x 179 | BC7_UNORM / DXGI 98, tile mode 14, one mip | 131,155 |

The checked target descriptor is **2e0100b8**. The first 83 bytes of the native
template bundle are preserved exactly, including burG/BCXT/HCXT structure,
dimensions, and format fields.

### Exact conversion stages

1. Read the swatchbin envelope using the existing CPS swatch reader.
2. Validate one mip and the exact source descriptor; reject unsupported variants.
3. Create an XG texture computer for RGBA8/DXGI 28, tile mode 14.
4. Require the XG allocation size to equal the source payload length.
5. Detile into linear RGBA pixels.
6. Resize with Lanczos, keeping aspect ratio, onto a transparent canvas at the
   FH2 template dimensions. Do not stretch the square Top Gear logo.
7. Pass the PNG and destination templates into the ForzaTech-backed C# converter.
8. Premultiply RGB by alpha using integer rounding `(channel*alpha + 127)/255`.
9. Encode BC7 with BCnEncoder, **without generated mipmaps**.
10. Compute BC7 block pitches: `ceil(width/4)*16` bytes per block row and
    rowPitch*ceil(height/4) for depth pitch.
11. Identify the stock template's XG layout by exact detile/retile roundtrip.
    Candidate modes are checked, not guessed from file size; ambiguous matches fail.
12. Tile the BC7 payload using the identified mode.
13. Detile it again and require byte equality with the encoded linear BC7 data.
14. Require exact native bundle length and preserve the template's 83-byte header.
15. Decode previews and write thumbnails.json with hashes, format, and tile mode.
16. Install as browser\poster.swatchbin and browser\logo.swatchbin; put both
    SHA256 values in the pack's [Browser] section.

The roundtrip proves **tiling consistency**, not lossless RGBA-to-BC7 conversion.
BC7 compression itself is lossy.

The helper uses ForzaTech's xg.dll, BCnEncoder.dll, and
Microsoft.Toolkit.HighPerformance.dll, with a matching installed .NET runtime.
ForzaTech resources aided texture work; they were not an existing complete
world/PVS/navigation converter for this track.

### Placeholder and runtime publication

Storm Island currently uses the unmodified native destination images as
placeholders. Despite their names, they depict generic mainland scenery.

Runtime artwork validation checks native envelope, dimensions, single mip,
descriptor, length, and SHA256 before platform publication.
The runtime paths are track-ID based:

```text
UI\textures\data_bound\TrackLoader\track10000_large.swatchbin
UI\textures\data_bound\TrackLoader\track10000_small.swatchbin
```

The corresponding native UI URI ends in `_large.png` / `_small.png`; that URI
does not mean PNG files replaced the swatchbin payload on disk.
Missing optional artwork gets an explicit text placeholder.

## 14. Database registration and pack discovery

Primary implementation: [build_dlc.py](build_dlc.py),
[build_session.py](build_session.py), [TrackLoaderCPS.cpp](TrackLoaderCPS.cpp).

Discovery scans `fh2-mods\CustomTracks` first, then legacy `fh2-mods\TrackPacks`.
New packs belong in CustomTracks. Legacy layouts remain supported; do not leave
two independently active packs claiming the same identity.

IDs must be >=10000. Track IDs, environment IDs, media names (case-insensitive),
and lifecycle namespaces must be unique.
Namespaces are restricted to `trackloadercps` or `trackloadercps_<lowercase suffix>`
with a 32-character maximum. A second map can use trackloadercps_10001.

Registration is additive SQL against the actual FH2 schema:

- New Tracks and Environments rows, not replacements of 542 or 549.
- No paid DLC product/offer/license impersonation.
- No source game database writes.
- Conflicts and unsupported schemas fail explicitly.
- Stock rows verified unchanged in isolated registration tests.
- Runtime verifies resulting override-database rows before custom activation.

Database registration does not make stock SwitchOnActiveTrack a generic selector:
its native logic explicitly distinguishes 542 and 549. Custom targets need the
separate owned transition described below.

### Current Top Gear manifest contract

```ini
[Track]
Id=10000
Name=Top Gear (CPS)
Author=suderek
Version=0.3.2-beta
State=experimental

[LocalDLC]
Format=1
EnvironmentId=10000
MediaName=TopGearCPS
Delivery=mod-managed
Registration=registration.sql

[Media]
Directory=media
CatalogSha256=4e26743fefd2541f7cb286d3fc2675ad75379f56102572e7e38cde8be2fb85f5

[Experimental]
Enabled=1
RestoreSavingOnReturn=1
VisitDirectory=visit
VisitCatalogSha256=bbe149de5f173571cdc6934f6b1ee43efd9daca34299f61a29aaeb02665bdd09
RegistrationSha256=e09fe93fb0c35b986e6bd9853f7dee3a567a57777f79306210d9974b294a1e66
ArrivalPosition=283.592712,0.381009579,195.880096
ArrivalFacing=-0.998773813,-0.0034530554,0.0496612005

[Browser]
PosterSha256=6988400c2c2fafd661719b1dcab9b63ebc21205dd3f5ec9ebc8b99f59f2cd68a
LogoSha256=a128d0c5c76493d410123292f59e4634f8e16931cb04cf39451c55948fb6624d
```

These hashes identify this exact tested content; they are not reusable hashes for
an arbitrary rebuilt or second map. LifecycleNamespace may be omitted for this
legacy default namespace; specify a unique one for additional maps.

## 15. Native ownership and custom travel

Primary implementation: [track_session.h](track_session.h),
[track_group.h](track_group.h), [track_flow.h](track_flow.h),
[track_binding.h](track_binding.h), [track_target.h](track_target.h),
[track_player.h](track_player.h), [build_visit.py](build_visit.py).

### Why native ownership had to be decoded

Several early failures were wrong object interpretations, not bad media:

- Context accessor 0x8679F0 returns the executing Entity, not a global Group.
- EntitySystem+0x88 is not the concrete Group's entity map.
- Concrete Group constructor 0x18FE260 puts its entity map at Group+0x28.
- Runtime Entity+0x68 holds a circular type list; type+0x18 holds component nodes.
- Component nodes hold shared object/control references at +0x10/+0x18.
- Resolver 0x1906CA0 returned an interned identifier, not a Group.
- Global Group resolution uses 0x19065E0 and concrete Group vtable checks.

The code uses bounded/cycle-checked traversal and ownership pins, not reflection
serialization layouts. Player references are intrusive-pinned; shared references
retain their native control blocks. The profile at player+0x328 is borrowed and
revalidated, not falsely retained as an independent shared object.

### Additive stateflow registration

Five catalog-pinned resources are published:

1. stateflow\trackloadercps.xml
2. EntityModel\entities_trackloadercps_enter.xml
3. EntityModel\entities_trackloadercps_return.xml
4. EntityModel\entities_trackloadercps_static_route.xml
5. EntityModel\entities_trackloadercps_mainland.xml

The owning machine module is obtained using the native weak owner path.
GameStateMachineComponent+0x100 resolves through 0x3EB640; the verified module's
stateflow manager is at +0x50.
If boot enumeration missed the additive XML, the loader parses that exact
published file into the correct registry and checks compiled references for only
the owned custom group. Parser/compiler and native launch failures remain errors;
there is no fallback stock transition falsely reported as custom success.

### Scoped SwapTrack argument override

Native SwapTrack dispatch is synchronous through the audited callback path.
At instruction RVA **0x939410**, after shared descriptor fields have been read,
the preparation scope changes only:

- R14D: destination track ID.
- XMM6: destination position float4.
- XMM7 low float: heading.

Shared career/travel descriptors and unrelated registers are not rewritten.
Missing/repeated callbacks and nested scopes fail.
The descriptor's old track can legitimately be stale because this custom flow
does not stage the stock descriptor; the local arguments are fully overridden.
Rejecting solely on that stale descriptor value was an earlier false assumption.

### Entry flow

The exact node-by-node entry sequence is in [the graph](PORT_GRAPH.md#4-complete-custom-native-stateflow).
Important invariants:

- Begin from settled mainland free roam with idle persistence work.
- Hold the exclusive dispatch lease and save protection before transition.
- Keep the player car loaded across SwapTrack, as stock common_track_swap does.
- Load navigation, wait for locators, load the static route group.
- Restore the owned arrival pose and wait for streaming around the player.
- Resume HUD, start race/driving state, balance car-control enable/disable, and
  release the parking brake.

Unloading the car first left cars-ready/display gates unsatisfied and stalled
TrackSwapManager state 0xF. A later remaining stall was probe readiness, not a
reason to force the manager complete.

The driver states are Unavailable, Ready, Entering, Driving, Returning, Failed.
Transitions have a 180-second checked deadline. Completion requires settled
actual target, launched owned flow, one swap, one restore, and gameplay readiness.
Timeouts/failures do not become success through fallback.

## 16. Persistent activation, Airport return, and saving

### Activation

Enabled=1 remains enabled across launches; the loader no longer disarms or rewrites
the manifest after one session. Enabled=0 disables activation next launch.
Legacy SessionOnly is read only when Enabled is absent.

### Recorded custom arrival

Top Gear:

```text
position  283.592712, 0.381009579, 195.880096
facing   -0.998773813, -0.0034530554, 0.0496612005
```

Capture uses finite XYZ and a valid normalized facing. Native physics SIMD W may
contain a non-spatial NaN, so captured positions supply homogeneous W=1 rather
than rejecting a valid XYZ or copying the garbage fourth lane.

### Custom return is always Airport

```text
Mainland Airport position: 425.574, 110.821, -1478.082
```

The return copies the proven CPS native mainland staging:
global.unlock_destination -> career_swap.root_mainland -> SwapTrack ->
navigation/locator readiness -> career_swap.finish_swap.

An empty custom route originally provided no TrackSwapComponent destination.
Adding only presentation entities or retaining a selected native entity did not
solve it. The working static route supplies a real Mainland TrackSwapComponent
with Airport WorldPosition and no activities, triggers, or map markers.

The dedicated resume_mainland flow restores the Airport pose, streaming,
HUD/race/driving state, time, and weather. It intentionally does not launch a
second uncontrolled stock free-roam startup.

Position must be within **0.25 m** of Airport; independent live checks were within
0.05 m. Airport facing only needs to be valid; matching a particular departure
heading is not a completion requirement.

**Storm Island is different:** its native mainland return is left intact and
does not receive an Airport position override.

### Save protection is not a career rollback

The early protection worker starts before slow pack hashing/publication.
It observes the native profile constructor at 0x2BF410 with the required hooks
active; a profile that predates observation requires restart. It tracks the
owned profile, known submission/completion paths, microsave ownership, and jobs.
It does not adopt a pre-existing profile and assume unknown jobs are idle.

RestoreSavingOnReturn defaults to 0: protection remains until process exit.
The tested pack explicitly opts in with RestoreSavingOnReturn=1.
Release requires the guarded owned mainland return, actual map/car/pose
verification, required transition/resume completion, matching owners/profile,
and idle observed persistence work. Failed/incomplete returns keep protection.

This does not audit or roll back arbitrary career/profile mutation. Keep save
backups and do not call the mod generally save-safe.

## 17. Standalone and CPS dispatch

[build.bat](build.bat) builds the same source twice:

| DLL | Dispatch service |
|---|---|
| TrackLoaderCPS.dll | Cooperates with CPS native TrackVisit service |
| TrackLoader.dll | TRACKLOADER_STANDALONE; own signature-checked native dispatch |

The standalone implementation in [track_standalone.h](track_standalone.h) hooks
WaitForPause at RVA **0x964810**, checks gameplay readiness at **0x963560**, and
offers the same attach/acquire/release/held contract to the session driver.
Acquire/release must occur on its native dispatch tick, not an arbitrary command
thread. Duplicate attachment, nested ticks, ineligible entry, and wrong-thread
lease actions fail explicitly.

A shared exclusive owner prevents both new variants from activating together.
Older pre-guard TrackLoaderCPS DLLs still need to be removed/disabled explicitly.
Only one variant should be installed per game.

### The real cause of dead controller input

Balancing a duplicate disable_car_control call was necessary but not sufficient.
The CPS WaitPauseHook returned zero every frame while the visit lease was held,
so the stock WaitForPause gameplay/input poll never ran.

Once settled on a non-mainland map with gameplay ready, the dispatcher now calls
the original pause poll even while owning the visit. The standalone runtime has
the equivalent rule. Transition isolation remains active while not settled.
The user then confirmed driving/steering.

Import inspection of the standalone loader showed Windows system dependencies
bcrypt.dll, winsqlite3.dll, and KERNEL32.dll; it does not import CPS.
The custom browser additionally needs the paired development mod platform.

## 18. Native DLC browser and automatic travel

Primary implementation: [track_browser.h](track_browser.h),
[track_artwork.h](track_artwork.h), [TrackLoaderCPS.cpp](TrackLoaderCPS.cpp),
and platform [settings.cpp](../FH2-Mod-Platform/src/settings.cpp).

The local platform extends page tile ownership with IslandFeatured. It preserves
the original native IslandFeaturedLiveTile and callbacks but collapses that
control, adding an ordinary platform-owned button at the same far-right cell.
The parent tab reads DLC.

The gallery uses native HUD options framing, CommonListBox, selection slots, a
poster panel, metadata, thumbnails, and native FZMaterial images.
Storm Island 549 is always first; other entries sort by name and ID.
Author/version come from the manifest. GitHub update checking is **not**
implemented: repository text is informational, not a completed network check.

On mainland the button opens the gallery. On other settled maps, its dynamic
title becomes Return to mainland and A requests direct return instead.

### Why A originally required manual B

Queuing a custom travel request alone left the pause page open, so free-roam
dispatch could not execute it. Native HUD options pages only support the
registered `back` transition; invented extra exits were rejected by native
stateflow compilation.

An owner-only Page::SendEvent call also failed because actual HUD pages can have
page+0x310=NULL.

The working platform path:

1. Store one pending result/action.
2. Call signature-checked native Back **0xCEB240**, which handles owner and
   global-event-bus cases.
3. Consume the pending action after native pause-menu wiring.
4. For custom travel/resume, call Back again to leave pause and let the existing
   owned free-roam dispatcher execute the queued request.
5. For stock travel, invoke the original Expansion Packs event emitter
   **0xDBDD00**, with the pause expansion_packs exit redirected only while an
   IslandFeatured owner exists.

The native stock branch retains active-track switching, entitlement/install
checks, root_island/root_mainland, and native unpause behavior.
The MODS Get Mods button still opens its URL.
The Extras handler **0xDBDD60** was rejected as a shortcut because it performs
store/marketplace work rather than only emitting the desired event.

Storm Island need not be installed to open this ordinary custom browser. Its
own row can still appear, and the stock travel flow enforces availability.

## 19. Disappearing tile caption: final working solution

This issue had several false-positive fixes. Setter success or native string
readback did not prove that the rendered text survived a page transition.

Observed sequence:

1. A caption added to the native live tile disappeared after refresh.
2. A separate button avoided the original live control but template-bound
   FZTextBlock heading/count still vanished after gallery Back or full pause reuse.
3. Native pause XAML was observed loading before slow mod initialization ended.
4. Installing property hooks early restored the first caption, but not repeats.
5. FindAncestor versus inherited DataContext did not fix persistence.
6. Native Button.Content and Tag setters/readback returned Tracks / 2 tracks
   while the screen was blank. This disproved readback as a rendering test.

Final implementation:

- An ordinary button template contains a ContentPresenter, background material,
  and selection bar.
- Button content holds named native FZTextBlock heading/count elements, seeded
  with literal escaped strings.
- Pause wiring resolves the named text elements in page/button scope and finds
  their native Text property descriptors.
- Each reuse sets Text to an empty string before assigning the desired value.
  This forces a value change; assigning the identical string alone had not
  reliably regenerated visible text.
- Dynamic Return to mainland uses that same update path.
- Only the signature-checked pause-button wiring hook is prepared early.
  Property lookup/boxing hooks retain normal post-initialization validation.
- Early clicks and text refresh are guarded until the MODS hub is ready.
- Noisy temporary Content/Tag readback logging was removed.

Three consecutive gallery returns and full pause reopenings were visually
checked with the text invalidation path. A fresh launch with the narrowed final
hook setup also passed first-use gallery opening, return, and full pause reopening.
The actual custom-map title was then observed as Return to mainland.

Native helper details: FindName 0xC63120, AVUI String constructor 0x1663220,
string cleanup 0x16650A0; native property descriptors expose the setter at +0x30.
These are verified implementation details, not a general public AVUI API.

Another visibility pitfall: literal hex FZTextBlock.Color rendered invisible in
this UI path. Native typed colour resources, such as A_Color_White, worked.

## 20. Media revision ledger

This is a lineage/reference table, not a command to install every intermediate.
v14 was an unsuccessful branch; v15 returned to the validated v13 base.

| Revision | Persistent change / diagnostic result |
|---|---|
| v1..v7 | Progressive root/navigation/environment staging and catalog infrastructure; not playable conversion milestones by themselves |
| v8 | Native bin.zip layout |
| v9 | Native 4 KB-window minizip encoding |
| v10 | Geometry/zone plus physics merge into two chunks; still wrong zone assignment |
| v11 | Single GeoChunk0 and all-zero ChunkAssign |
| v12 | Two resident diffuse probes exposed as streamed level 1 |
| v13 | Each zone requests all 210 collision roots |
| v14 | Failed slot-0-to-slot-2 lighting experiment; not the production lineage |
| v15 | cb3 remap and baked direct-shadow neutralization from v13 |
| v16 | Name-based physical-surface remap; 6,163 triangle changes |
| v17 | Minimum 500 m cull pass; 1,355 draws |
| v18 | Bake source grass fade 75/100 |
| v19 | Grass/overlay macro RGB neutralization |
| v20 | Static grass render depth separation |
| v21 | Repair 3,567 draw LOD selections |
| v22 | 272-to-288-byte instancing-pose retarget |
| v23 | Default LOD bands 100/200/350/500; 1,340 draws |
| v24 | Legacy t6 AO neutralization |
| v25 | 5 km global residency, initial 1,905-texture closure |
| v26 | Closure expanded to 2,053 textures |
| v27 | Retained shadow-input diagnostic plus pavement skid-mask neutralization |
| v28 | Blended-pavement material AO-mask neutralization |
| v29 | 29 marking-only draw classifications repaired |
| v30 | Terrain macro RGB neutralization; black/red strips corrected |
| v31 | 32 audited mixed runway marking draws repaired |
| v32 | Four marking normal sqrt guards; original textures retained |

Current preserved artifacts:

| Artifact | SHA256 |
|---|---|
| media-v32 media.catalog | 4e26743fefd2541f7cb286d3fc2675ad75379f56102572e7e38cde8be2fb85f5 |
| visit-v9 media.catalog (lifecycle catalog) | bbe149de5f173571cdc6934f6b1ee43efd9daca34299f61a29aaeb02665bdd09 |
| Top Gear poster | 6988400c2c2fafd661719b1dcab9b63ebc21205dd3f5ec9ebc8b99f59f2cd68a |
| Top Gear logo | a128d0c5c76493d410123292f59e4634f8e16931cb04cf39451c55948fb6624d |
| Tested development DWrite.dll | 78e83674125275413eecee8d1a15392c065dddc8791a3f167f302c2143695a75 |

The current world catalog has **15 files**, totaling **1,266,282,195 bytes**:

```text
tracks\TopGearCPS\
  bin.zip                       6,970,726
  ChunkAssign.dat                   1,160
  ChunkMap0.dat                    26,688
  GeoChunk0.minizip             1,256,521,728
  PhysicsDefinitions.bin               8
  PVSZLookup.dat                    1,152
  TrackSettings.xml               51,144
  smoke.dds                       87,536
  staticcarcubemap.xpr            542,732
  ribbon_00\
    CollObjs.xml                      51
    Locators.xml                   3,121
    TopGearCPS_00.nav              92,368
    TopGearCPS_00.oww                 448
    TopGearCPS_00.pvs           1,979,557
    TopGearCPSout_00.hex            3,776
```

Earlier documentation's 1,354 world-file count describes an unpacked staging
layout. It is not the current 15-file native-archive layout.

## 21. Reproduction and build procedure

### Environment and safety

- Python with the existing project dependencies; Pillow is used for artwork.
- VS 2022 Build Tools for loader/native tests.
- Existing LLVM-MinGW/CMake setup for the mod platform.
- CPS swatch reader and local ForzaTech/XG/.NET/BCnEncoder resources for artwork.
- Read-only source FM5/FH2 assets from the user's installations.
- New output directory per stage; do not overwrite known-good media or edit
  shared hardlinked output files in place.

Several post-processors hardlink unchanged files to save space, then unlink
changed files before rewriting. Manual editing through a shared hardlink can
corrupt another revision. Always use the checked builders/repackers and retain
the known-good catalog hashes.

### Base conversion component order

From this project folder, these are the real tools and argument relationships.
Substitute NEW output names when rerunning; existing history directories are
preserved. The following illustrates the base chain, not a one-click certified
rebuild of v32:

```powershell
python .\port_topgear.py --help
python .\build_geometry.py --source-archive .\work\TopGear\source\bin.zip `
  --pvs .\work\TopGear\source\ribbon_00\TopGear_00.pvs --output <NEW-GEOMETRY>

python .\collision.py --source .\work\TopGear\source\ribbon_00\TopGear_track_00.col `
  --component-output <NEW-PHYSICS>

python .\build_spatial.py --source-pvs .\work\TopGear\source\ribbon_00\TopGear_00.pvs `
  --archive .\work\TopGear\source\bin.zip --source-ai .\work\TopGear\source\ribbon_00\AI.zip `
  --source-collision .\work\TopGear\source\ribbon_00\TopGear_track_00.col --output <NEW-SPATIAL>

python .\build_bindings.py --spatial <NEW-SPATIAL> --physics <NEW-PHYSICS> --output <NEW-BINDINGS>
python .\build_root.py --source-pvs .\work\TopGear\source\ribbon_00\TopGear_00.pvs `
  --source-archive .\work\TopGear\source\bin.zip --geometry <NEW-GEOMETRY> `
  --bindings <NEW-BINDINGS> --spatial <NEW-SPATIAL> --output <NEW-ROOT>

python .\build_openworld.py --source-ai .\work\TopGear\source\ribbon_00\AI.zip `
  --source-collision .\work\TopGear\source\ribbon_00\TopGear_track_00.col --output <NEW-NAVIGATION>
python .\build_environment.py --source .\work\TopGear\source `
  --native-settings <FH2-MOUNT>\media\tracks\Anthem\TrackSettings.xml `
  --physics <NEW-PHYSICS> --static-test --output <NEW-ENVIRONMENT>
python .\build_media.py --root <NEW-ROOT> --navigation <NEW-NAVIGATION> `
  --environment <NEW-ENVIRONMENT> --mediaoverride --native-bin-zip --output <NEW-BASE-MEDIA>
```

Angle-bracket tokens are descriptive placeholders, not literal executable
PowerShell syntax. `--help` on each tool gives the exact current options.
The source audit is intentionally an audit; it does not itself produce a
playable map. The schema components also retain conservative diagnostic reports.

### Post-processing

Each invocation accepts `--source <MEDIA-DIR> --output <NEW-MEDIA-DIR>` and one
operation. The intended dependencies are in [the graph](PORT_GRAPH.md).
Important supported operations:

```text
--single-chunk
--stream-probe-diffuse
--zone-collision
--patch-shaders
--remap-surfaces <FM5-surfaceTypes.xml> <FH2-surfaceTypes.xml>
--min-draw-distance 500
--bake-grass-fade 75 100
--neutralize-macro-tint
--separate-grass-depth
--repair-draw-lods
--patch-instancing-poses
--default-draw-distances 100 200 350 500
--neutralize-legacy-ao
--map-wide-distance 5000
--neutralize-pavement-skid
--neutralize-blended-pavement-ao
--repair-topgear-markings
--neutralize-terrain-macro-tint
--repair-topgear-runway-markings
--guard-marking-normals
```

The initial repack also corrects legacy minizip encoding. Follow the source's
single-operation validation; do not pass unrelated flags together and assume
they all execute.

**Exact-rebuild caveat:** the v27 lineage retains the separately generated
shadow-input diagnostic described above. Current public CLI flags alone do not
recreate that exact pass. Preserve the accepted v32 data and hashes; do not claim
a newly generated base plus the listed flags is byte-identical without comparing
every file/catalog. Folding all accepted passes into a canonical one-shot builder
is still future work.

### Native lifecycle and session package

```powershell
python .\build_visit.py --stateflow <FH2-MOUNT>\media\stateflow.zip `
  --entitymodel <FH2-ENTITYMODEL-ZIP> --media-name TopGearCPS --output <NEW-VISIT>
python .\build_session.py --media .\work\TopGear\media-v32 --visit <NEW-VISIT> `
  --database <FH2-MOUNT>\media\Stripped\gamedbRC.slt `
  --source-database <FM5-MOUNT>\media\db\gamedb.slt --output <NEW-SESSION> `
  --arrival-position 283.592712 0.381009579 195.880096 `
  --arrival-facing -0.998773813 -0.0034530554 0.0496612005 `
  --restore-saving-on-return
```

That last flag explicitly opts in to guarded save restoration, not guaranteed
career safety. Use --lifecycle-namespace and unique track/environment/media names
when assembling an independently converted second world.

### Browser art

```powershell
python .\build_thumbnails.py --fm5-mount <FM5-MOUNT> --fh2-mount <FH2-MOUNT> `
  --cps-source <CarPackStudio-SOURCE> --forzatech <FORZATECH-DIRECTORY> `
  --converter <Forza-SOURCE>\SwatchbinConverter\build.ps1 --output <NEW-ARTWORK>
python .\build_thumbnails.py --install-pack <PACK>\track.ini `
  --track TopGear --output <NEW-ARTWORK>
```

Close the game before pack changes. Installation writes the Browser hashes and
assets only after validating the artwork report.

### Native DLLs and deployment

```powershell
& $env:ComSpec /c 'E:\forza2port_win\project_data\TrackLoaderCPS\build.bat'
```

The batch file sets vcvars64 and builds both variants in the same cmd.exe
environment. The platform is built with its existing CMake build-mingw tree;
the post-build copies the shared platform output to DWrite.dll.

For standalone: paired development DWrite.dll beside azimuth.exe; only
TrackLoader.dll in fh2-mods; packs in CustomTracks. Do not install CPS variant
alongside it. For CPS wrapper testing, use the actual CPS runtime's fh2-mods,
not the separate real-game Azimuth directory.

Loaded DLLs do not change when disk copies change. Close the exact game process
before deployment, preserve backups, and restart. Never copy temporary input
probes or a whole working mods/config/save directory into a tester package.

## 22. Validation and regression testing

### Automated layers

| Layer | Relevant tests |
|---|---|
| Source/archive/PVS schemas | test_port, test_pvs, test_source_world, test_world, test_fh2_root |
| Geometry/spatial/root closure | test_zones, test_spatial, test_bindings, test_root_builder |
| Collision and navigation | test_collision, test_navigation, test_openworld_navigation, test_environment |
| Media/catalog/session | test_media, test_catalog_runtime, test_dlc, test_session, test_visit |
| Shader/visibility/artwork | test_shader_patch, test_shader_scenes, test_repack_shaders, test_map_visibility, test_thumbnails |
| Native contracts | tests/test_track_*.cpp and tests/native_host.cpp |
| Platform UI/API output | ../FH2-Mod-Platform/tests/platform_test.cpp |

Example targeted command actually used:

```powershell
$env:PYTHONPATH='.;tests'
python -m unittest test_thumbnails test_catalog_runtime test_visit test_session test_dlc -q
```

This selection passed **53 tests** before the beta archive. The isolated native
platform fixture reported ALL PASSED. Native browser and standalone lease checks
also passed. These counts describe the particular selections run, not the total
coverage or proof that all old test executables match newly changed headers.
Recompile relevant native tests after changing their source/header dependencies.

### Required live acceptance sequence

1. Back up saves; use offline testing and the exact intended installation.
2. Confirm correct DLL variant, platform version, actual executable path, and
   hash-pinned pack. Wait for initialization/early protection.
3. On mainland, verify DLC tab and far-right Tracks, then open gallery with A.
4. Verify poster/logo and metadata, not just generated XAML or source PNGs.
5. Repeat B, tab changes, pause close/reopen; inspect actual visible caption.
6. Enter Top Gear with A only; verify pause closes automatically.
7. Observe road contact, steering, grip, lighting, grass, props, paint.
8. Move the camera/drive; static screenshots are insufficient for flicker.
9. Check Return to mainland caption on the custom map; use A directly.
10. Verify actual mainland map, Airport XYZ, native readiness, persistence jobs,
    and release log before calling return complete.
11. Repeat entry/return without changing Enabled.
12. Test Storm Island separately only where legitimately installed; do not
    infer its native return position should be the custom Airport position.

Runtime status alone is not rendering proof. A successful property setter is not
visible-caption proof. An existing output file is not archive mount proof.

## 23. Issue-resolution reference

| Symptom | Established cause / finding | Working treatment | Rejected proxy or limit |
|---|---|---|---|
| Source copy does not load | Different IMAP/PVS/world schemas | Explicit native serializers | Header-only version spoof |
| Missing model Modl / parser AV | Wrong minizip zlib window | v100 4 KB window and native padding | Python inflate success |
| Archives exist but no requests | No native mounted archive handles | Runtime native volume mounts | Late boot zipmanifest |
| Boot crash after chunk change | Stale override zipmanifest | Stop publishing custom boot manifest | Keeping old offsets/removed paths |
| Many empty-name failed models | Zone chunk assignment excludes their payloads | Single chunk zero policy | More waiting or forced-ready |
| Loading never completes | Diffuse probes resident-only | Native streamed level-1 probe descriptors/payloads | Force transition/display flag |
| Falls through road | No zone auxiliary collision requests | Request root ordinals in every zone | Valid physics bytes alone |
| Occasional spatial AV | Loose HEXY failed, invalid native fallback | Keep published files; restart after installation | Empty/fake grid |
| Black world but visible sky/car | cb3 mismatch and unbound baked direct light | Constant remap and audited shadow neutralization | Slot 0 -> slot 2 alone |
| Shader experiment has no effect | Only .pc variant edited | Patch/test .durango too | D3D creation as live-binding proof |
| No steering/input | Stock WaitForPause poll suppressed under lease | Call original when custom gameplay settles | start_race alone |
| Ice-like road | FM5 surface 119 becomes FH2 Snow | Surface-name mapping | Copy numeric surface byte |
| Close prop popping | Cull/default bands and native selection metadata | Distance, default-band, selection and pose fixes | TrackSettings alone |
| Red grass / dark edge strip | Incompatible macro RGB path | Family-specific RGB neutralization | Delete blend masks/geometry |
| Grass ground flicker | Coplanar static grass render layers | Render-only 5 cm separation | Alter collision elevation |
| Object corruption after revisit | Reload/upload defect not repaired by AO | Accepted map-wide resource residency | Claim initial AO fix durable |
| Giant asphalt images/blocks | Incompatible mask inputs isolated experimentally | Reflected skid/AO workarounds | Remove authored overlays globally |
| Missing paint | Native draw classification | Narrow roadline class repair | Solid shader / height lift alone |
| More missing runway arrows | Audited mixed-material models skipped | Extended companion allowlist | Change all transparent draws |
| X/arrow flicker | Unsafe normal reconstruction isolated | Clamp z squared before sqrt | Alpha removal / 1-5 cm lift |
| Return load has no destination | Static route lacked TrackSwapComponent | Real Mainland Airport component | Presentation-only entity |
| A waits for manual B | Paused UI blocks queued free-roam action | Native Back + deferred action | Arbitrary HUD exits |
| Event never reaches transition | HUD page owner can be null | Native Back/event emitter fallback | Owner-only SendEvent |
| Blank Tracks after reopening | Same-value/template text not visibly regenerated | Named content Text invalidation on reuse | Correct Content/Tag readback |
| Real standalone has no gallery | Installed 0.5 build predates browser | Paired 0.6 loader + modified platform | Assuming CPS runtime install updates it |

## 24. Remaining limitations

- Not a universal FM5/FH2 world converter; several rules are Top Gear-specific.
- Source optional extra-world schemas and a full occlusion bake are unsupported.
- All accepted fixes are not yet folded into one canonical reproducible command.
- Texture upload/reload binding defects are not fully repaired; residency,
  white-mask/tint/AO changes, and normal guards are explicit compromises.
- FM5 baked direct shadows, some AO/skid effects, and macro colour variation are
  intentionally removed; FH2-native dynamic-shadow translation is incomplete.
- Pale asphalt and safe restoration of authored skid masks remain open.
- Movable-prop collision, racing activities, source AI, traffic, and replay
  recording are not a completed port.
- A second independently converted real map still needs live validation.
- Direct custom-to-custom travel is refused; return to mainland before another map.
- First-launch loose-resource/UI publication can require restart.
- The modified browser platform is development-only; official 2.8.0 binaries do
  not implement its trailing page fields and travel result extensions.
- GitHub update detection is pending.
- Save protection is deliberately conservative, not a complete career-state audit.
- No claim of compatibility across all Azimuth versions, third-party proxy DLLs,
  mod combinations, PCs, or save states.

## 25. Code and evidence index

| Area | Primary implementation |
|---|---|
| Audit / schema inspection | [port_topgear.py](port_topgear.py), [inspect_formats.py](inspect_formats.py) |
| Source assets/world | [fm5_pvs.py](fm5_pvs.py), [fm5_world.py](fm5_world.py) |
| Native archive/world schemas | [fh2_world.py](fh2_world.py), [fh2_pvs.py](fh2_pvs.py) |
| Geometry and visibility | [build_geometry.py](build_geometry.py), [build_zones.py](build_zones.py), [build_spatial.py](build_spatial.py) |
| Collision / bindings / root | [collision.py](collision.py), [build_bindings.py](build_bindings.py), [build_root.py](build_root.py) |
| Navigation / environment | [navigation.py](navigation.py), [openworld_navigation.py](openworld_navigation.py), [build_openworld.py](build_openworld.py), [build_environment.py](build_environment.py) |
| Media and accepted fixes | [build_media.py](build_media.py), [repack_media.py](repack_media.py), [shader_patch.py](shader_patch.py), [shader_scenes.py](shader_scenes.py) |
| Pack assembly | [build_dlc.py](build_dlc.py), [build_session.py](build_session.py), [build_visit.py](build_visit.py) |
| Runtime catalog/mount | [track_media.h](track_media.h), [track_mount.h](track_mount.h), [track_lifecycle.h](track_lifecycle.h) |
| Ownership / native contracts | [track_ownership.h](track_ownership.h), [track_shared.h](track_shared.h), [track_group.h](track_group.h), [track_flow.h](track_flow.h), [track_jobs.h](track_jobs.h) |
| Target / pose / saving | [track_target.h](track_target.h), [track_pose.h](track_pose.h), [track_save.h](track_save.h), [track_binding.h](track_binding.h), [track_live.h](track_live.h), [track_player.h](track_player.h) |
| Driver / standalone service | [track_session.h](track_session.h), [track_standalone.h](track_standalone.h), [track_visit.h](track_visit.h) |
| Browser / art | [track_browser.h](track_browser.h), [track_artwork.h](track_artwork.h), [build_thumbnails.py](build_thumbnails.py) |
| Entry points / diagnostics | [TrackLoaderCPS.cpp](TrackLoaderCPS.cpp), [track_diag.h](track_diag.h) |
| Platform UI / source notes | [settings.cpp](../FH2-Mod-Platform/src/settings.cpp), [API](../FH2-Mod-Platform/docs/api.md), [native game notes](../FH2-Mod-Platform/docs/game-notes.md) |
| Final world report | [media-v32 report](work/TopGear/media-v32/media.json) |
| Final lifecycle report | [visit-v9 report](work/TopGear/visit-v9/visit.json) |
| Geometry audit | [geometry-v2 report](work/TopGear/geometry-v2/geometry.json) |
| Artwork descriptors/hashes | [thumbnails report](work/browser-artwork-v1/thumbnails.json) |
| Tests | [tests](tests) |

Historical checkpoint summaries and runtime experiments were used to distinguish
failed attempts from accepted fixes. They are session-local evidence, not required
runtime dependencies. This document and the graph contain implementation
explanations, not copies of proprietary model/texture payloads.
