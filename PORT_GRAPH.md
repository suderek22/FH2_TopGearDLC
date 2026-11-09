# TrackLoader / Top Gear port: complete logical node graph

Companion to [TECHNICAL_INFO.MD](TECHNICAL_INFO.MD), documented 9 November 2026.
Open this file in a Markdown preview with Mermaid support.

## Scope and legend

These graphs cover all major conversion components, the accepted media
post-processing chain, runtime eligibility/ownership, browser navigation,
and **all 61 node calls across the four generated custom stateflow states**.
Repeated state calls are separate nodes where execution requires them.

This is not a dump of every mesh vertex, all 288 zones, or all engine-internal
nodes recursively called by stock stateflow. Those are data/runtime dependencies,
not additional authored custom stateflow nodes. The complete generated custom
call inventory is represented in section 4.

- Solid arrows: data dependency or ordered execution.
- Dashed arrows: validation, cross-reference, optional path, or return to caller.
- Branch labels: explicit runtime or readiness decisions.
- Rejected experiments are segregated from the accepted path.
- `E`, `R`, `M`, and `W` prefixes identify entry, return, mainland resume, and
  locator wait calls. Their numeric order matches [build_visit.py](build_visit.py).

## 1. Source-to-package dependency graph

```mermaid
flowchart TB
    FM5["FM5 owned Top Gear source"]
    FH2["FH2 native reference assets"]
    AUDIT["port_topgear.py / inspect_formats.py"]
    BIN["FM5 bin.zip and AssetManifest"]
    PVS["FM5 TopGear_00.pvs v50"]
    COL["FM5 TopGear_track_00.col"]
    AI["FM5 AI.zip / MLP named tables"]
    SETTINGS["Source settings / locators"]
    SURFACE["FM5 + FH2 surfaceTypes.xml"]
    REFROOT["Anthem / HomeSpace02 / test03 references"]
    REFDB["Read-only FM5 and FH2 databases"]
    REFFLOW["FH2 stateflow and entitymodel archives"]
    REFART["FM5 poster/logo + FH2 destination templates"]

    FM5 --> BIN
    FM5 --> PVS
    FM5 --> COL
    FM5 --> AI
    FM5 --> SETTINGS
    FM5 --> AUDIT
    FH2 --> AUDIT
    FH2 --> REFROOT
    FH2 --> REFDB
    FM5 --> REFDB
    FH2 --> REFFLOW
    FH2 --> REFART
    FM5 --> REFART
    FM5 --> SURFACE
    FH2 --> SURFACE

    DECODE["fm5_pvs.py: descriptors, levels, manifest closure"]
    WORLD["fm5_world.py: all placements, groups, overrides"]
    GEOMETRY["build_geometry.py: native payload validation and streaming keys"]
    GEODATA["GeoChunk0 / ChunkMap0 / descriptors / resident textures"]
    ZONES["build_zones.py: source geometry-only PVSZ"]
    COLLISION["collision.py: big-endian cells to FH2 physics"]
    PHYSDATA["210 templates / collision roots / physics component"]
    ROUTE["navigation.py: waypoint/source route validation"]
    SPATIAL["build_spatial.py + spatial.py: 288 HEXY free-roam zones"]
    BIND["build_bindings.py: zone/node/physics bindings"]
    ROOT["build_root.py + fh2_pvs.py: complete native v60 root"]
    NAV["build_openworld.py: NAVW + OWWF closed road"]
    ENV["build_environment.py: native settings / locators / static-only profile"]
    SCENES["shader_scenes.py: scenes and 1152 program closure"]
    MEDIA["build_media.py: staged native-bin world + catalog"]

    BIN --> DECODE
    PVS --> DECODE
    BIN --> WORLD
    PVS --> WORLD
    DECODE --> GEOMETRY --> GEODATA
    BIN --> GEOMETRY
    PVS --> ZONES
    DECODE --> ZONES
    REFROOT -.-> ZONES
    COL --> COLLISION --> PHYSDATA
    SURFACE -.-> COLLISION
    REFROOT -.-> COLLISION
    AI --> ROUTE
    ROUTE --> SPATIAL
    COL --> SPATIAL
    ZONES --> SPATIAL
    WORLD --> SPATIAL
    SPATIAL --> BIND
    PHYSDATA --> BIND
    BIND --> ROOT
    GEODATA --> ROOT
    WORLD --> ROOT
    SPATIAL --> ROOT
    REFROOT -.-> ROOT
    AI --> NAV
    COL --> NAV
    SETTINGS --> ENV
    PHYSDATA --> ENV
    REFROOT --> ENV
    BIN --> SCENES
    SCENES --> ROOT
    ROOT --> MEDIA
    NAV --> MEDIA
    ENV --> MEDIA

    POST["Accepted post-process chain: section 2"]
    FINAL["media-v32: 15 files + media.catalog"]
    VISIT["build_visit.py: 5 lifecycle resources + media.catalog"]
    SQL["build_dlc.py: additive Tracks/Environments SQL"]
    SESSION["build_session.py: persistent experimental pack"]
    ART["build_thumbnails.py + XG / BC7 converter"]
    ARTOUT["browser poster/logo and Browser hashes"]
    PACK["CustomTracks/TopGear: manifest + SQL + media + visit + optional art"]
    DLL["build.bat: TrackLoader.dll OR TrackLoaderCPS.dll"]
    PLATFORM["Paired modified FH2 Mod Platform / DWrite.dll"]
    RUNTIME["Runtime discovery/publication/native mounts: section 3"]

    MEDIA --> POST --> FINAL
    REFFLOW --> VISIT
    REFDB --> SQL
    FINAL --> SESSION
    VISIT --> SESSION
    SQL --> SESSION
    REFDB --> SESSION
    REFART --> ART --> ARTOUT
    SESSION --> PACK
    ARTOUT --> PACK
    PACK --> RUNTIME
    DLL --> RUNTIME
    PLATFORM --> RUNTIME
```

Audit/source serializers are not themselves a playable map. The geometry-only
zone component is an intermediate; final free-roam zones and the later global
closure supersede its selective source-view policy.

## 2. Accepted media chain and rejected branches

The sequence is grounded in persisted media reports, not a promise that every
operation already exists in one canonical build command.

```mermaid
flowchart TB
    BASE["v7: cataloged base world"]
    V8["v8: native bin.zip, central 0x1123 data offsets"]
    V9["v9: PGZP v100, zlib window 12 and alignment"]
    V10["v10: 2 chunks; still wrong zone assignment"]
    V11["v11: single GeoChunk0, ChunkAssign all zero"]
    V12["v12: 2 diffuse probes exposed as streamed level 1"]
    V13["v13: 210 auxiliary collision requests per zone"]
    V15["v15: cb3 remap + full baked-shadow visibility"]
    V16["v16: surface-name remap; 6163 triangle changes"]
    V17["v17: minimum final cull distance 500 m"]
    V18["v18: grass fade constants 75 / 100"]
    V19["v19: grass/overlay macro RGB neutralization"]
    V20["v20: grass render layers +5 cm; no physics shift"]
    V21["v21: native per-band LOD selection repair"]
    V22["v22: instanced pose stride 272 to 288"]
    V23["v23: default bands 100 / 200 / 350 / 500"]
    V24["v24: neutralize legacy t6 AO"]
    V25["v25: 5 km residency; initial closure"]
    V26["v26: add group/instance/material texture closure"]
    SHADOW["Retained diagnostic: t104 zero, t5 one; no production CLI"]
    V27["v27: reflected pavement skid masks neutralized"]
    V28["v28: blended pavement AO mask neutralized"]
    V29["v29: 29 marking-only draw flag repairs"]
    V30["v30: terrain macro RGB neutralization"]
    V31["v31: 32 audited mixed runway marking repairs"]
    V32["v32: 4 normal reconstruction sqrt guards"]
    VERIFIED["Confirmed media-v32 hash pinned; original paint textures restored"]

    BASE --> V8 --> V9 --> V10 --> V11 --> V12 --> V13
    V13 --> V15 --> V16 --> V17 --> V18 --> V19 --> V20
    V20 --> V21 --> V22 --> V23 --> V24 --> V25 --> V26
    V26 --> SHADOW --> V27 --> V28 --> V29 --> V30 --> V31 --> V32 --> VERIFIED

    V14["v14 rejected: instance Direct0 slot 0 to AO slot 2"]
    PALETTE["Diagnostics only: family palette / solid RGB shaders"]
    OVERLAY["Rejected: remove channel-five pavement overlay"]
    PAINTLIFT["Rejected: paint height lift / opaque alpha"]
    FLATNORMAL["Diagnostic only: flat paint normal"]
    RELOAD["Still unresolved: true texture upload/reload repair"]

    V13 -.-> V14
    V18 -.-> PALETTE
    V26 -.-> OVERLAY
    V29 -.-> PAINTLIFT
    V31 -.-> FLATNORMAL
    FLATNORMAL -.-> V32
    PALETTE -.-> V19
    V25 -. "avoids eviction; does not repair" .-> RELOAD
```

v14 is not a parent of v15. Diagnostic palette/height/alpha/flat-normal payloads
are not included in the accepted v32 result. The retained shadow diagnostic is
the explicit exception: it remains in the lineage with its limitations documented.

## 3. Runtime startup, ownership, and driver states

```mermaid
flowchart TB
    START["Platform starts loading mods"]
    PREP["Early signature-checked pause-button wiring preparation"]
    DISCOVER["Discover CustomTracks first, then legacy TrackPacks"]
    MANIFEST{"Manifest active and experimental?"}
    DIAGNOSTIC["Blocked/disabled pack: diagnostics only, no travel"]
    EARLY["Early code-ready native save/profile protection"]
    VALIDATE["Verify IDs, namespaces, catalog hashes and all files"]
    DB["Additive SQL + actual override-database verification"]
    PUBLISH["set_file publication to actual Mount/mediaoverride"]
    MOUNT["Native archive mounts: bin.zip + GeoChunk0"]
    FLOW["Compile/ensure owned additive stateflow registry"]
    OWNER{"Exactly one loader variant owns runtime?"}
    CPS["CPS TrackVisit dispatch"]
    STANDALONE["Standalone WaitForPause dispatch"]
    HUB["Finish guarded browser/hub initialization"]
    READY["Driver Ready"]
    REQUEST["Queue selected track request"]
    CHECK{"Settled 542 + gameplay ready + idle jobs + observed profile?"}
    PIN["Capture/pin group, player, car, machine and profile identities"]
    LEASE["Acquire exclusive native-tick lease; hold save gate"]
    ENTERING["Driver Entering: scoped SwapTrack + entry flow"]
    ENTERCHECK{"Actual target settled + one swap/restore + owned flow complete?"}
    DRIVING["Driver Driving: original input poll allowed"]
    RETURNREQUEST["Request return; no direct custom-to-custom swap"]
    RETURNING["Driver Returning: native mainland staging + dedicated resume"]
    RETURNCHECK{"542 + Airport within 0.25 m + valid pose + owners/jobs verified?"}
    SAVING{"RestoreSavingOnReturn opted in?"}
    RELEASE["Release save protection and dispatch lease"]
    KEEP["Keep save protection; release dispatch lease"]
    FAILED["Driver Failed: explicit error; protection remains if held"]
    TIMEOUT["180 s timeout / ownership or native failure"]

    START --> PREP
    START --> DISCOVER --> MANIFEST
    MANIFEST -- no --> DIAGNOSTIC
    MANIFEST -- yes --> EARLY
    MANIFEST -- yes --> VALIDATE
    VALIDATE --> DB --> PUBLISH --> MOUNT --> FLOW --> OWNER
    OWNER -- CPS variant --> CPS --> HUB
    OWNER -- standalone variant --> STANDALONE --> HUB
    EARLY --> READY
    HUB --> READY
    READY --> REQUEST --> CHECK
    CHECK -- no --> READY
    CHECK -- yes --> PIN --> LEASE --> ENTERING --> ENTERCHECK
    ENTERCHECK -- not yet --> ENTERING
    ENTERCHECK -- yes --> DRIVING --> RETURNREQUEST --> RETURNING
    RETURNING --> RETURNCHECK
    RETURNCHECK -- not yet --> RETURNING
    RETURNCHECK -- yes --> SAVING
    SAVING -- yes --> RELEASE --> READY
    SAVING -- no --> KEEP --> READY
    VALIDATE -. failure .-> FAILED
    DB -. failure .-> FAILED
    OWNER -. conflict .-> FAILED
    ENTERING -.-> TIMEOUT
    RETURNING -.-> TIMEOUT
    TIMEOUT --> FAILED
```

This is a dependency/state diagram, not a claim that the protection worker and
media validation run sequentially. Early protection deliberately runs in parallel.
The source driver also has **Unavailable** before initialization; Failed is not
silently reset to Ready after a native error.

## 4. Complete custom native stateflow

Source: [build_visit.py](build_visit.py), `flow("TopGearCPS")`.
The generated custom stategroup has four states:

| State | Node calls | Behaviors |
|---|---:|---|
| enter | 27 | GameControlWrapper, BlockPauseEntryWrapper, DisableMicroSaveWrapper |
| return_mainland | 14 | Same transition wrappers |
| resume_mainland | 18 | GameControlWrapper, BlockPauseEntryWrapper, DisableMicroSaveWrapper |
| wait_locators | 2 | Native condition and next-frame loop |
| Total | **61** | Wrapper behavior shown separately below |

Transition wrappers set GameControlWrapper force_take_control=false,
allow_failure=true, with onFailedToTakeControl handling. Native failure is not
drawn as a successful continuation. The player car is kept loaded across swaps.

### 4.1 Enter TopGearCPS: every node, in order

```mermaid
flowchart TB
    EROOT["trackloadercps.enter"]
    EWRAP["GameControlWrapper / BlockPauseEntryWrapper / DisableMicroSaveWrapper"]
    E01["track_swap.stop_car_and_fade_out"]
    E02["global.enable_mixer_snapshot"]
    E03["ui_screens.show_fmv_loading_screen_and_continue"]
    E04["global.apply_parking_brake_human"]
    E05["global.disable_car_control"]
    E06["track_swap.remove_all_ai_cars"]
    E07["track_swap.unload_navigation_world"]
    E08["track_swap.swap_track"]
    E09["track_swap.load_navigation_world"]
    E10["wait_locators"]
    E11["global.load_entity_group"]
    E12["global.ai_spawning_mode_none"]
    E13["global.restore_car_position"]
    E14["global.stop_car"]
    E15["global.apply_parking_brake_human"]
    E16["global.reset_camera_to_player"]
    E17["global.enable_track_streaming"]
    E18["global.wait_for_streaming_around_local_human_car"]
    E19["global.clear_mulligan_key_frames"]
    E20["global.disable_mixer_snapshot"]
    E21["global.fade_in"]
    E22["ui_screens.show_hud_and_continue"]
    E23["global.resume_all_huds"]
    E24["global.start_race"]
    E25["global.start_driving"]
    E26["global.enable_car_control"]
    E27["global.release_parking_brake_human"]
    EEND["Check owned native entry; then Driver Driving"]

    EROOT --> EWRAP --> E01 --> E02 --> E03 --> E04 --> E05 --> E06
    E06 --> E07 --> E08 --> E09 --> E10 --> E11 --> E12 --> E13 --> E14
    E14 --> E15 --> E16 --> E17 --> E18 --> E19 --> E20 --> E21
    E21 --> E22 --> E23 --> E24 --> E25 --> E26 --> E27 --> EEND
```

Call parameters not duplicated in the labels:

- E02/E20: snapshot BASE_Loading.
- E03: empty loading_reason.
- E08: wait=true; owned instruction scope sets track 10000 and recorded arrival.
- E09: track_name=TopGearCPS, ribbon=0.
- E11: entities_trackloadercps_static_route, wait_for_state_machine_exit=false,
  allow_replace_if_group_exists=true.
- E13: scoped position/facing restore.
- E19: start_recording_replay=false.
- E21: force=false.

### 4.2 Return from custom map: every node, in order

```mermaid
flowchart TB
    RROOT["trackloadercps.return_mainland"]
    RWRAP["GameControlWrapper / BlockPauseEntryWrapper / DisableMicroSaveWrapper"]
    R01["track_swap.stop_car_and_fade_out"]
    R02["global.enable_mixer_snapshot"]
    R03["ui_screens.show_fmv_loading_screen_and_continue"]
    R04["global.apply_parking_brake_human"]
    R05["global.disable_car_control"]
    R06["track_swap.remove_all_ai_cars"]
    R07["track_swap.unload_navigation_world"]
    R08["global.unlock_destination"]
    R09["career_swap.root_mainland"]
    R10["track_swap.swap_track"]
    R11["track_swap.load_navigation_world"]
    R12["wait_locators"]
    R13["career_swap.finish_swap"]
    R14["global.load_entity_group"]
    REND["Mainland entity autostarts trackloadercps.resume_mainland"]

    RROOT --> RWRAP --> R01 --> R02 --> R03 --> R04 --> R05 --> R06
    R06 --> R07 --> R08 --> R09 --> R10 --> R11 --> R12 --> R13 --> R14 --> REND
```

Parameters:

- R02: snapshot BASE_Loading.
- R03: empty loading_reason.
- R10: wait=true; scoped mainland/Airport target.
- R11: track_name=Anthem, ribbon=0.
- R14: entities_trackloadercps_mainland, wait_for_state_machine_exit=false,
  allow_replace_if_group_exists=true.
- The previously loaded static route contains a real Mainland TrackSwapComponent
  and WorldPosition 425.574,110.821,-1478.082.

### 4.3 Dedicated mainland resume: every node, in order

```mermaid
flowchart TB
    MROOT["trackloadercps.resume_mainland"]
    MWRAP["GameControlWrapper / BlockPauseEntryWrapper / DisableMicroSaveWrapper"]
    M01["global.ai_spawning_mode_none"]
    M02["global.restore_car_position"]
    M03["global.stop_car"]
    M04["global.apply_parking_brake_human"]
    M05["global.reset_camera_to_player"]
    M06["global.enable_track_streaming"]
    M07["global.wait_for_streaming_around_local_human_car"]
    M08["global.clear_mulligan_key_frames"]
    M09["global.start_time"]
    M10["global.unpause_weather"]
    M11["global.disable_mixer_snapshot"]
    M12["global.fade_in"]
    M13["ui_screens.show_hud_and_continue"]
    M14["global.resume_all_huds"]
    M15["global.start_race"]
    M16["global.start_driving"]
    M17["global.enable_car_control"]
    M18["global.release_parking_brake_human"]
    MEND["Verify Airport, ownership, native jobs, then apply save-release policy"]

    MROOT --> MWRAP --> M01 --> M02 --> M03 --> M04 --> M05 --> M06
    M06 --> M07 --> M08 --> M09 --> M10 --> M11 --> M12 --> M13
    M13 --> M14 --> M15 --> M16 --> M17 --> M18 --> MEND
```

M02 restores the owned Airport pose. M08 uses start_recording_replay=false.
M11 uses BASE_Loading; M12 uses force=false. Airport heading need only be valid,
not match a fixed desired heading. Release is conditional, not an unconditional
consequence of reaching M18.

### 4.4 Shared locator wait: every node and branch

```mermaid
flowchart TB
    WROOT["trackloadercps.wait_locators"]
    W01["track_swap.is_locators_module_ready"]
    W02["global.wait_for_next_frame"]
    WEXIT["Return to calling enter / return_mainland flow"]
    WROOT --> W01
    W01 -- yes --> WEXIT
    W01 -- no --> W02
    W02 --> W01
```

W01 has id `_is_locators_module_ready`; W02 has id `next_frame`.
Its exit targets are `.exit`, `next_frame`, and `_is_locators_module_ready`.
The higher-level driver deadline still applies; this loop is not forced ready.

## 5. Browser, native Back, and stock/custom routing

```mermaid
flowchart TB
    PAUSE["Open pause; native wiring refreshes named Text"]
    DLC["DLC tab, far-right ordinary replacement button"]
    MAP{"Current settled map?"}
    TRACKS["Tracks"]
    BACKTILE["Return to mainland"]
    GALLERY["A opens native HUD gallery"]
    SELECT["LB/RB or native list selection; Storm Island first"]
    DETAIL["Poster/logo, author/version; GitHub check not implemented"]
    A{"Press A on selected item"}
    CUSTOM["Queue custom native dispatch request"]
    STOCK["Choose stock Storm Island action"]
    NBACK["Native Back 0xCEB240 leaves gallery"]
    PENDING["Consume pending action after pause wiring"]
    RESUME["Native Back leaves pause"]
    DISPATCH["Free-roam tick executes queued custom entry or return"]
    EMIT["Original Expansion Packs emitter 0xDBDD00"]
    SWITCH["Native active-track switch"]
    GATES["Native entitlement and installation gates"]
    ISLAND["track_swap.root_island"]
    NATIVEHOME["track_swap.root_mainland; no Airport override"]
    DIRECT{"Return tile action"}
    CUSTOMHOME["Queue custom Airport return"]
    ERROR["Explicit refusal / normal native cancellation"]
    B["B from gallery"]
    TITLE["Refresh heading/count: empty Text then requested Text"]

    PAUSE --> DLC --> MAP
    MAP -- mainland 542 --> TRACKS --> GALLERY
    MAP -- other settled map --> BACKTILE --> DIRECT
    GALLERY --> SELECT --> DETAIL --> A
    A -- custom pack --> CUSTOM --> NBACK
    A -- 549 --> STOCK --> NBACK
    NBACK --> PENDING
    PENDING -- custom resume --> RESUME --> DISPATCH
    PENDING -- stock action --> EMIT --> SWITCH
    SWITCH -- mainland --> GATES
    GATES -- available --> ISLAND
    GATES -- unavailable --> ERROR
    SWITCH -- island --> NATIVEHOME
    DIRECT -- owned custom visit --> CUSTOMHOME --> RESUME
    DIRECT -- native island --> EMIT
    DIRECT -- invalid or unsettled --> ERROR
    GALLERY --> B --> TITLE --> DLC
```

The gallery works without installed Storm Island. Displaying its row does not
bypass its native travel checks. Owner-only event dispatch and arbitrary HUD
exit names were rejected; native Back handles pages with a null owner too.

## 6. Texture conversion paths

### 6.1 World data: retained compressed payloads

```mermaid
flowchart LR
    WT["FM5 world BC4 / BC5 / BC6H / BC7 payloads"]
    WC["Validate burG / TXCB and payload bounds"]
    WD["76-byte FM5 descriptor to 56-byte FH2 descriptor"]
    WK["Ordinal + streaming-level resource keys"]
    WR["Resident store plus GeoChunk streaming payloads"]
    WZ["Zone / group / instance / material request closure"]
    WS["Shader constants and sampling compatibility passes"]
    WF["Native FH2 rendering; remaining compromises explicit"]
    WT --> WC --> WR
    WT --> WD --> WK --> WR --> WZ --> WF
    WS --> WF
    WP["Diffuse probe exception: resident cube also becomes streamed level 1"]
    WR -.-> WP -.-> WR
```

### 6.2 UI artwork: actual pixel decode and recompression

```mermaid
flowchart TB
    U0["FM5 TopGear poster/logo swatchbin"]
    U1["Validate RGBA8 DXGI 28 / mode 14 / one mip"]
    U2["XG detile into linear RGBA"]
    U3["Aspect-preserving Lanczos + transparent letterbox"]
    U4["FH2 destination dimensions: 1390x516 / 290x179"]
    U5["Premultiply RGB by alpha"]
    U6["BCnEncoder BC7 DXGI 98; one mip"]
    U7["Identify template XG mode by exact roundtrip"]
    U8["Tile BC7; verify detile equals linear BC7"]
    U9["Keep native 83-byte header and exact bundle length"]
    U10["Preview + SHA256 + thumbnails.json"]
    U11["Install browser/poster + logo; Browser hash fields"]
    U12["Runtime verify format/size/hash"]
    U13["set_file track-ID swatchbin paths"]
    U14["Native FZMaterial URI displayed by gallery"]

    U0 --> U1 --> U2 --> U3 --> U4 --> U5 --> U6 --> U8
    U4 --> U7 --> U8 --> U9 --> U10 --> U11 --> U12 --> U13 --> U14
```

The byte-exact roundtrip is for tiling, not a claim that BC7 compression is
lossless. Native Storm Island placeholder images follow the runtime validation
path without FM5 image recompression.

## 7. Caption investigation: why the final path is different

```mermaid
flowchart TB
    C0["Native live tile caption disappears"]
    C1["Separate ordinary button"]
    C2["Template-bound heading and count still disappear on reuse"]
    C3["Early property hook: first-open improvement only"]
    C4["FindAncestor / inherited DataContext: no persistent fix"]
    C5["Content/Tag readback correct but rendered text blank"]
    C6["Named FZTextBlock children in ContentPresenter content"]
    C7["Resolve native Text descriptor through page/button namescope"]
    C8["Set empty Text, then requested heading/count"]
    C9["Visually verify gallery Back + full pause reopen repeatedly"]
    C10["Dynamic Return to mainland uses same path"]
    C11["Keep only pause wiring early; property hooks validated normally"]

    C0 --> C1 --> C2 --> C3 --> C4 --> C5 --> C6 --> C7 --> C8 --> C9
    C9 --> C10
    C9 --> C11
```

Native setter/readback success is not a substitute for C9. This distinction
prevented repeating the earlier false completion claims.

## 8. Validation gates

```mermaid
flowchart LR
    T0["Read-only source audit"]
    T1["Native schema roundtrips"]
    T2["Resource/hash/namespace closure"]
    T3["DXBC checksums + program validation"]
    T4["Offline loader/native host + platform API/XAML tests"]
    T5["Fresh correct-process launch"]
    T6["Actual rendering and moving-car/camera checks"]
    T7["Repeated gallery/pause and A-only travel checks"]
    T8["Verified Airport + job/owner/save-release checks"]
    T9["Repeat cycle without disarming Enabled"]
    T10["Retain known-good catalog and document limitations"]
    T0 --> T1 --> T2 --> T3 --> T4 --> T5 --> T6 --> T7 --> T8 --> T9 --> T10
```

Each gate establishes only its own layer. None eliminates the need for offline
save backups or turns this experimental port into a general career-safe release.

Documentation checks: all 12 Mermaid graphs parsed and rendered with Mermaid 11.
All 61 numbered native call nodes match the generator in order; the four states,
including wrappers, parameters, and exits, also match the preserved visit-v9 XML
after ignoring formatting whitespace. Relative document links and heading
anchors were checked against this workspace.
