TrackLoader / TrackLoaderCPS 0.7.0-beta.4 -- PERSISTENT EXPERIMENTAL MULTI-MAP TRAVEL

STANDALONE RUNTIME

build.bat produces both bin\TrackLoaderCPS.dll and bin\TrackLoader.dll.
TrackLoaderCPS retains the CPS native dispatch/cooperation service.
TrackLoader uses its own signature-checked WaitForPause free-roam dispatcher
and native tick lease. It does not request CarPackStudio.TrackVisit or import
a CPS DLL. Besides Windows system DLLs, only FH2 Mod Platform 2.8.0 is required.
Keep only one loader variant enabled per game installation. New builds publish
an exclusive runtime owner; the second variant refuses file/SQL/hook activation.
Older TrackLoaderCPS DLLs predate that guard and must be disabled explicitly.

Standalone installation:
  E:\forza2port_win\azimuth\fh2-mods\TrackLoader.dll
  E:\forza2port_win\azimuth\fh2-mods\CustomTracks\TopGear\track.ini
The confirmed media-v32/visit-v9 pack was copied intact into CustomTracks.
The old blocked TrackPacks\TopGear remains untouched and is skipped because
CustomTracks takes precedence. The previous real-game TrackLoaderCPS.dll was
preserved as TrackLoaderCPS.dll.standalone-disabled (not loaded).
CPS tests/installations still use the separate CPS runtime fh2-mods directory.

Both variants expose platform commands, alongside the local-development
native DLC/Tracks browser described below:
  fh2mod TrackLoader status
  fh2mod TrackLoader list
  fh2mod TrackLoader travel 10000
  fh2mod TrackLoader return
Use --pid <PID> before the mod name when multiple games run. Wait for native
entry ready and settled mainland free roam before entering a custom track.
Custom-track return targets Airport; valid facing need not match departure facing.
Storm Island uses native mainland restoration without injected coordinates.
The same verified map/car ownership, 0.25 m position and idle persistence-job
checks guard saving restoration in both builds.

Live standalone validation: two consecutive Top Gear entry/Airport-return
cycles passed with CarPackStudio.dll AND TrackLoaderCPS.dll excluded from
loading. Platform command enumeration confirmed neither CPS DLL was loaded;
saving was released after both returns. Independent final position was within
0.05 m of Airport. CPS DLLs were then restored to their normal test installation.
The test used the existing CPS runtime executable/media, not the CPS mod.
After restoring CPS, the 0.5.0-test CPS variant also passed two consecutive
entry/Airport-return cycles with saving released after each return.
47 targeted Python/native-host tests pass across both DLL variants, including
mixed-variant exclusion, legacy pack compatibility and lifecycle collisions.
tests\test_track_standalone.cpp checks thread/lease/eligibility/repeat ownership.

NATIVE BROWSER DEVELOPMENT (local test build)

The released platform supports only the Achievements tile. The local checkout
at ..\FH2-Mod-Platform now also accepts FH2Page.tile = "IslandFeatured".
This hides the Storm Island tab's far-right native live tile and replaces it
with a platform-owned ordinary button using the existing pause-menu wiring.
The native live control, template and callbacks remain intact but hidden;
other featured tiles are unchanged. Its native text children are explicitly
invalidated and refreshed on pause reuse; a separate template/binding alone
did not prevent disappearing text. No Achievements substitution or shared-handler hook.
The local platform and offline API/XAML regression tests build and pass with
the documented LLVM-MinGW toolchain. The paired development platform and
TrackLoaderCPS are installed in the CPS runtime; the CPS mod is restored.
The standalone variant was tested there separately with CPS disabled.
The real-game standalone installation now has TrackLoader 0.6.0-dev and the
paired development platform; its previous 0.5.0-test DLL had no native browser.
The checkout's API documentation and tests preserve the changes for a later
PR. No commit or PR has been created.

build_thumbnails.py reads TopGear.swatchbin from FM5's Track_Posters.zip and
L_TRK_TopGear.swatchbin from Track_Logos.zip. Their RGBA8/tile14 descriptor is
checked explicitly, rather than guessed from payload size. The existing
Forza\SwatchbinConverter converts them to native FH2 BC7 using the game's
StormIsland_Large/Small destination templates. Conversion and preview decoding
verify an exact tiled/linear round trip; the native bundle header is preserved.
Transparent letterboxing retains the poster/logo aspect ratio.
Storm Island artwork is loaded directly from the game's The_Island archive:
Destinations\9.swatchbin and loading_watermark.swatchbin. It is not shipped.

Generated output: work\browser-artwork-v1
  TopGear_Large/Small.swatchbin
  decoded PNG previews and thumbnails.json (hashes, logical paths, DXGI/tile mode)
All four decoded previews and native runtime images were visually verified:
Storm Island placeholder poster/logo and Top Gear poster/logo switch correctly.
Assets are installed in both the CPS runtime and the real standalone installation.

Tool arguments (all required):
  --fm5-mount <FM5 Mount> --fh2-mount <FH2 Mount>
  --cps-source <CarPackStudio source> --forzatech <ForzaTechStudio folder>
  --converter <Forza\SwatchbinConverter\build.ps1> --output <staging folder>
Requires the existing CPS swatch reader/XG, Pillow and converter dependencies.

The 0.6.0-dev loader registers an autoshow-style gallery, publishes verified
native artwork, renames the parent tab DLC, and displays author/version metadata.
The first thumbnail itself is named FirstItem, so initial native page focus
does not consume the first direction press just to enter the list. Metadata
and status text stay on dark panels; selected thumbnails have an orange
underline. Travel, Back, directional navigation and bumper hints use the
native controller help bar, not instructions over the scene background.
TrackLoader publishes artwork only under data_bound\TrackLoader; it does not
replace Panama/Porsche autoshow, bucket-list or rivals tile textures. If these
fail to load, check the Panama.zip central-directory metadata in zipmanifest.xml
against the installed archive before changing any textures.
Use --install-pack <track.ini> --track TopGear --output <artwork>
to install the staged browser images and their manifest hashes.
Dunsfold Aerodrome metadata is Author=suderek, Version=0.7.0-beta.4.
Pressing A queues custom travel and automatically closes the gallery and pause
menu through native Back; no manual B/resume step is needed. Stock Storm Island
549 remains first and uses the native entitlement/install-gated travel flow.
On maps other than 542 the featured tile reads "Return to mainland" and A
returns directly, without opening the browser. Owned custom visits return to
Airport through the existing dispatcher; 549 uses native mainland restoration
without injecting Airport coordinates.
Live CPS checks verified both map travel actions without manual B,
the return caption outside mainland, direct
native Storm Island return and direct custom Airport return with saving released.
The standalone build also passed Top Gear entry/Airport return and Storm Island
entry/native mainland return using only A, with CarPackStudio.dll and
TrackLoaderCPS.dll excluded. Saving was released after its custom return.
The real standalone browser also opens without Storm Island installed. Top Gear
poster/logo and the then-current metadata were visually verified there.
Native caption persistence was checked through three consecutive gallery returns
and full pause reopenings after adding explicit text invalidation; earlier
template-only and unchanged-value setter attempts failed those repeat checks.
Gallery text uses the native
font style and typed Color resource (a literal hex FZTextBlock.Color rendered
invisible). Platform file comparisons use bounded chunks to avoid allocating
and reading the 1.2 GB track archive byte-by-byte during every startup.
53 targeted loader tests and the isolated platform suite pass.
The browser compares the pack version with the latest published GitHub release,
including prereleases; failed checks are reported rather than silently ignored.
A fast/skip-launcher first boot can still
cache pause XAML before pack validation finishes; restart after initial
publication when installing/changing this development build.

MULTI-TRACK CORE

Discovery reads fh2-mods\CustomTracks first, then legacy fh2-mods\TrackPacks.
Existing Top Gear packs do not need to move or change their confirmed media.
Track IDs and environment IDs must be >= 10000; both IDs and case-insensitive
media namespaces must be unique across the discovered packs.
Each enabled map has its own arrival pose, saving policy and native lifecycle.
Registration is checked for every enabled map in the actual override database;
all verified map archives are mounted before custom travel is made available.

New packs must set Experimental.LifecycleNamespace to a unique name such as
trackloadercps_10001. Only trackloadercps or trackloadercps_<lowercase suffix>
are accepted, with a 32-character total limit; stock flow names are forbidden.
Omitting the setting retains the legacy trackloadercps namespace for Top Gear.
Two enabled packs cannot share it. Five lifecycle resources remain required.
The per-pack flow, entity filenames and ownership checks use the same namespace,
so a second map cannot overwrite the first map's navigation or native startup.

Generate an independent visit component with build_visit.py:
  --media-name SecondMap --lifecycle-namespace trackloadercps_10001
Assemble a verified world/visit package with build_session.py:
  --track-id 10001 --environment-id 10001 --display-name "Second map"
  --source-media-name <source Tracks.MediaName>
Existing media/visit/database/source-database/output arguments remain required.
This packages an already converted native world; it is not an automatic converter
for arbitrary source maps. World and visit media namespaces must match.
The default Top Gear build applies 13:30, freezes the clock, and leaves live
atmosphere, rain, and environment at 0. Track lighting and directional fog are
baked into that environment's TrackSettings.xml to avoid the rain observed with
high live atmosphere. The verified mainland return restores the captured
pre-entry time/weather. Use
--no-time-weather to omit the entry preset; --entry-time-hours, --no-freeze-entry-time,
--atmosphere, --rain and --environment override its defaults.

The per-environment build options --ambient-scale, --direct-scale,
--fog-density-scale and --fog-distance-scale tune static lighting/fog in
TrackSettings.xml. Defaults of 1 preserve source values. The current Top Gear
test profile preserves FM5 Top Gear ambient and directional lighting (scales 1),
with 2.0 fog density and 0.85 fog range. The fog values need in-game visual
review.

Use TrackLoaderCPS travel <ID> to select a map from settled mainland free roam.
Return to mainland Airport and wait for verified return before selecting another
map. Direct custom-map-to-custom-map swaps are deliberately refused.
TrackLoaderCPS pack <ID> reports the selected lifecycle namespace and arrival.
Top Gear's default lifecycle generation keeps the live-confirmed resource bytes.

Validation: build.bat builds the CPS DLL. Compile tests\native_host.cpp into
bin\native_host.exe with the existing MSVC environment, then run:
  python -m unittest discover -s tests -p test_catalog_runtime.py
  python -m unittest discover -s tests -p test_visit.py
  python -m unittest discover -s tests -p test_session.py
Native track_flow, track_group and track_binding tests cover independent second-map
dispatch, ignored first-map parsing, arrival/target binding and mainland resume.
tests\test_track_time_weather.cpp verifies preset parsing, signature checks,
snapshot capture, application, hold, release, and restoration.
The catalog host uses synthetic archive payloads; it verifies publication and
fail-closed startup, not engine loading or rendering of a second map.
The 0.4.0-test runtime completed two live Top Gear entry/Airport-return cycles.
Both returns released save protection and retained Enabled=1. Independent final
Airport position was within 0.05 m of the destination. Airport facing is no longer
a completion requirement. A second real converted map still needs live validation.
One attempted fresh launch exited during hook registration without a reported
crash cause; a retry booted and completed both cycles. Startup stability remains
part of release validation.
Standalone runtime is implemented above; the native DLC/Tracks browser is next.

CURRENT EXPERIMENTAL TEST SETUP

LIVE RETURN VALIDATION (0.3.0-test, visit-v9):
Two consecutive Top Gear entry/return cycles completed in the same game
process. Native return verified mainland Airport position/facing and idle
persistence jobs, released save protection and allowed another entry.
An independent live pose read after return was within 0.05 m of Airport.
This verifies the native travel/save-gate lifecycle, NOT long-term save
integrity or visual correctness.
The standalone runtime is implemented; the native DLC/Tracks browser is not yet.

The runtime suppresses all known
offline profile/microsave submission and cleanup branches
until verified mainland return when RestoreSavingOnReturn=1 (explicit
career-risk opt-in), otherwise for the entire game process.
Enabled=1 persists across launches without disarming. This is not a
general-purpose map-loader release.

Run offline through the CPS wrapper. After entering settled mainland
free roam (542), use the platform-command console:
  TrackLoaderCPS status
  TrackLoaderCPS travel 10000
After native entry/streaming completes, test steering, ground collision
and map rendering. Then use:
  TrackLoaderCPS return
  TrackLoaderCPS status

Repeated custom visits are allowed after verified return. Return checks the actual
mainland map, new player car, Airport position and completed
transition/resume machines before releasing CPS's exclusive lease.
With RestoreSavingOnReturn=0 it never releases the session save gate.
Career changes are not audited; back up your save before opting in.
AI/traffic/mulligan recording
remain disabled. Native pause/menu travel and CPS mutation commands are
blocked during the custom visit; use the wrapper's Stop if a load fails.
No engine failure is force-completed or crash-recovered into success.

Return uses the same native mainland staging as CPS's working track 542
command: global.unlock_destination, career_swap.root_mainland, SwapTrack,
navigation/locator readiness, then career_swap.finish_swap. The custom
route publishes a single TrackSwapComponent destination (Mainland/Airport),
as stock map destinations do, without activities, triggers or map markers.
The previous empty route left SwapTrack with no destination component to
visit. Adding only a route presentation entity or retaining the selected
native entity did not solve that; those rejected workarounds are absent.
Arrival remains the owned dedicated mainland resume so the Airport pose,
job readiness and save gate are checked rather than assuming completion.
Airport return accepts any valid car facing; it is not required to match the
departure facing. Position must remain within 0.25 m of the Airport destination.
Top Gear's recorded arrival facing and the other ownership/job checks are unchanged.

status must report native entry ready before requesting
travel. "native custom map/car/flow ready" is native lifecycle readiness,
NOT proof of visible geometry/material correctness or successful driving.
These require the first actual in-game observation. Engine material,
lighting and physical-surface interpretation remain experimental.

TOP GEAR MEDIA POST-PROCESSES

Run repack_media.py with --source and a NEW --output directory. Apply one
post-process per invocation; each output includes a refreshed catalog hash
for track.ini. Close the game before deploying media.

  --baked-shadow-visibility <0..1>
    Optional value for --patch-shaders. Defaults to 0.15, the visually approved
    Top Gear brightness setting. Use 0..1 to choose fallback visibility for
    FM5's unbound Direct0 shadow samples; 1 restores the brighter full-light setting.
  --scale-baked-shadow-fallback <0..1> --fm5-bin-zip <original FM5 Top Gear bin.zip>
    Diagnostic post-pass for an already-patched package. Scales only the replaced
    Direct0 samples and uses original FM5 shaders to identify them. The Top Gear
    screenshot test improved at 0.45 but remained too bright. The test install
    now uses 0.15; the prior package is kept in
    CustomTracks\TopGear\shadow-profile-045 for rollback. This is not native FH2
    shadow-map integration.

  --min-draw-distance 500
    Extends short final prop cull distances (not texture streaming quality).
  --bake-grass-fade 75 100
    Bakes FM5 TrackSettings grass fade distances instead of reading FH2's
    incompatible trackParamsCBuffer rows.
  --neutralize-macro-tint
    Experimental visual compatibility option for grass_blend_002 and
    terrain_3blend_ch4overlay. Their macro RGB tint path produces red grass
    in FH2. Neutralizing RGB keeps the base diffuse textures and authored
    alpha/blend masks, but intentionally removes the macro colour variation.
    The underlying FM5/FH2 macro-tint incompatibility remains under investigation.
  --neutralize-terrain-macro-tint
    Applies the same RGB-only workaround to terrain_3blend_macrodiffuse.
    Confirmed in-game to remove black/red strips on the grass side of Top
    Gear's roads. The strips are terrain meshes, not road-edge decals.
    Retains diffuse A/C textures, vertex blend weights, the channel-five
    terrain blend mask, alpha, geometry and physics. All three lighting
    scenarios and both platforms are processed. Authored macro RGB tint
    variation is intentionally removed; the underlying binding/UV
    incompatibility is not repaired. Can be applied after the existing
    grass/overlay macro-tint pass without changing those shaders again.
  --neutralize-legacy-ao
    Experimental diagnostic for FM5's baked ambient-occlusion sample at t6.
    Native FH2 shaders use terrain AO at t107 instead; the old input is not
    a compatible FH2 AO binding. Replaces those samples with full ambient
    visibility, leaving base textures, normal maps and specular probes intact.
    Intentionally removes authored baked AO, rather than translating it to
    FH2 terrain AO. Initial in-game testing showed corrected object textures,
    but corruption returned after driving away and back (resource reload).
    This is not a complete texture lifecycle fix. Changing concrete/asphalt
    patches and oversized markings also remain unresolved.
  --neutralize-pavement-skid
    Applies the white skid-mask workaround confirmed on the firehouse apron
    to all programs declaring SkidmaskTextureTexture, including asphalt,
    concrete, blended roads, edges and line decals. Resolves the input slot
    from shader reflection, rather than assuming all t16 inputs are masks.
    Preserves base diffuse/normal/AO textures, channel-five overlays and
    road-marking colour/alpha. Intentionally removes authored baked skid
    masks on affected roads, including the main track. Geometry, physics,
    LODs and map-wide residency are unchanged. This avoids contaminated
    mask sampling; it does not repair the underlying texture binding issue.
    Other shader inputs can still cause artifacts and need separate testing.
  --neutralize-blended-pavement-ao
    White material AO-mask workaround for road_2blend_threshold_001.
    Confirmed in-game on the concrete strip beneath the large aircraft.
    Removes this material AO contribution while retaining diffuse/normal
    textures, vertex blend weights and the authored threshold/dirt mask.
    All compiled scenarios and both platforms are processed. This is
    separate from legacy scene AO at t6 and the pavement skid workaround.
  --separate-grass-depth
    Raises the two static TrackLightScenario grass render layers by 5 cm.
    Physics is unchanged. Driving tests confirmed the grass flicker stopped.
  --repair-draw-lods
    Repairs constructor-default FH2 LOD selection offsets using model-group
    references. New root/placement builds also populate these automatically.
    Cull distance alone does not repair unresolved per-band selection.
  --repair-topgear-markings
    Restores painted runway/road lines using the in-game-confirmed FH2 road
    draw classification (0x1FFF1214 -> 0x0FFF1024). Selects only models whose
    embedded materials are all roadlines_topg_*; unrelated transparent draws
    and mixed-material models are untouched. Requires those model resources
    in chunk zero. Keeps original shaders, diffuse alpha, heights, geometry,
    physics, LODs and residency. A height lift and solid-colour shader alone
    did not restore visibility; draw classification did. Individual flag-bit
    semantics are not yet decoded. This does not restore baked skid masks or
    fix black/red road edges and pale asphalt.
  --repair-topgear-runway-markings
    Extends the marking draw repair to mixed runway-marking models containing
    only roadlines_topg_* plus audited companions: topg_runwaygrates,
    topg_reflactor_001, topg_harrierlandinglights_001/002 and topg_harrierpane.
    At least one roadlines_topg_* material is required; unrelated mixed
    models and standalone lights/grates remain untouched. Preserves the
    existing marking repair, shaders, textures, heights and physics.
    In-game testing confirmed the additional runway number, arrows and lines
    appear. X/arrow flicker requires the separate normal guard below.
  --guard-marking-normals
    Confirmed in-game to stop X/arrow flicker while preserving original paint
    textures. Clamps RoadLineDecal's reconstructed normal Z squared to 1e-6
    before sqrt/normalization, preventing negative-root NaNs and zero-length
    normals. Patches Track/Debug lighting programs on both platforms; Cubemap
    has no normal reconstruction. All texture samples, alpha/discard, geometry
    and heights remain unchanged. Height lifts (1/5 cm) did not help; flat
    normal sampling isolated the issue and this guard fixed it with original
    textures restored. The underlying normal texture/binding compatibility is
    still unresolved; this guards shader arithmetic, not the upload lifecycle.
  --patch-instancing-poses
    Retargets vertex-shader t64 pose declarations and structured-load stride
    extensions from FM5's 272-byte records to FH2's 288-byte records. Existing
    pose fields keep their offsets; FH2 appends its reflection plane.
  --default-draw-distances 100 200 350 500
    Replaces all-minus-one LOD bands only. FH2's fallback distances made
    FM5 tents, cameras, cones and barriers appear very close to the car.
    Driving tests confirmed explicit bands stop this popping. Authored
    non-default bands are preserved; the final distance is the cull limit.
  --map-wide-distance 5000
    Small-map option: raises every object's final cull band to at least
    5 km, including partially default (-1) bands. Earlier LOD thresholds,
    transforms, physics and existing longer distances are preserved.
    Every spatial zone requests the complete draw/model/texture closure,
    including LOD models and their group/per-instance texture dependencies,
    at the highest available streamed texture level. Resident-only
    dependencies retain level-zero requests.
    This prevents zone-driven resource eviction while travelling within
    the map, at increased RAM/VRAM and rendering cost. It is not a repair
    of the underlying texture reload/upload lifecycle.

Both .durango and .pc shader programs are processed. The current FH2 PC
wrapper actually loads .durango programs; testing .pc programs alone does
not validate the in-game result. Flicker/depth artifacts need separate
moving-camera validation; a static screenshot is not proof of a fix.

Startup publishes 1,354 verified world files and five verified lifecycle
resources through the platform's actual Mount\mediaoverride. SQL adds only
custom track/environment 10000 and is verified against the resulting
override database before native hooks/client activation. Stock rows,
stock resource archives and source game databases are not overwritten.
Before each owned lifecycle launch, the captured machine entity's
entity-system owner is shared-pinned and its native stateflow registry is
checked for trackloadercps. If directory enumeration
did not discover the additive XML, the native parser loads that exact
published file. Its compiled references are checked using the same builder
as native group initialization, validating only the custom group.
Compiler errors, registration and the engine's Boolean launch
result are verified; rejection fails explicitly without starting a fallback
stock transition. This does not depend on replacing a stock stateflow file.
CPS Maps UI and standard "track 10000" routing are NOT integrated yet.

The old diagnostic TopGear pack must be replaced, not installed alongside
the test pack: both reserve ID 10000. Both TrackLoaderCPS.dll and the updated
CarPackStudio.dll are required in the active wrapper's fh2-mods directory.
Close the game before replacing either DLL. Preserve the replaced DLLs
and the old TopGear pack outside the scanned TrackPacks directory.

The current test DLLs and armed TopGear pack are installed in:
  C:\Users\wotsu\AppData\Local\CarPackStudio\runtime\fh2-mods
Replaced files are preserved under the runtime's
TrackLoaderCPS-backup-20261107-124248 directory. The cooperating CPS DLL
also replaces the project's runtime template and the current
CarPackStudio-0.2.3-beta packaged runtime template, preventing Runtime.prepare
from restoring the old DLL.

Offline validation passed: 95 Python tests, 23 native bridge modes,
stateflow registration/compiler-error tests and owned executable contracts.
Installed DLL hashes and all 1,359 published resource hashes were verified.
The installed-DLL mock host verifies publication requests, explicit startup
refusal and the disarmed next-launch path; it does NOT run the real engine.
Actual native startup, map rendering, ground contact and driving still
require the first in-game test.

The live attempts exposed confusion between runtime and reflection layouts.
Runtime Entity+0x68 holds a circular list of types, each with a component
list at type+0x18. Component list nodes hold shared object/control at
+0x10/+0x18. This is verified against runtime visitor 0x551800 and runtime
Entity constructor 0x18FE150, NOT the XML/reflection tree/vector routines.
The walker preserves bounds, cycle checks, unique selection and ownership
pins. A fresh process is required after installing this correction.
The later failures exposed an object-identity mistake in the investigation:
context accessor 0x8679F0 returns the executing ENTITY, not a global group.
EntitySystem+0x88 is an entity map, but does not describe a Group object.
The real Group constructor 0x18FE260 allocates its entity map at Group+0x28.
The walker uses that constructor-verified layout. Global microsave capture
now resolves "global" through the live entity system's group resolver
0x19065E0, instead of trying to traverse the current pause entity as a group.
The earlier 0x1906CA0 lookup returned an interned identifier, NOT a group.
Concrete Group primary vtable 0x2F5F6B8 is now required before traversal,
preventing identifier objects or entities from being interpreted as maps.
Lifecycle ownership retains the parsed Group and machine, records the
containing entity, and obtains the entity system through the same weak
owner accessor as native context code. Registry absence, invalid shared
ownership, map-header failure and map-root failure are separately reported.

Media-copy caching is deferred until map entry works. Existing files or
SQL rows do not establish native group/component ownership, so skipping
publication cannot fix these pre-entry ownership failures.

Experimental.Enabled=1 now keeps track activation enabled across launches;
activation no longer modifies the manifest. Enabled=0 disables publication
and native activation on the next launch. Legacy SessionOnly=0/1 is read
only when Enabled is absent, for compatibility with older packs.
Repeated custom-map visits are allowed after a verified mainland return.
Returns always use CPS's Anthem Airport position:
425.574,110.821,-1478.082. Facing is retained from the mainland departure,
because CPS's fixed Airport destination specifies position only.

Saving is protected from early profile construction until return.
RestoreSavingOnReturn=1 explicitly permits re-enabling saving once native
mainland transition/startup, Airport pose, unchanged owner identities,
and idle persistence jobs/submissions are verified. Career/profile mutations
are NOT audited or rolled back; the absence of activities on a custom map
is not proof that its profile stayed unchanged. This opt-in was requested
for testing. Back up your save before using it. The default remains 0,
which retains save protection until process exit. Disable Enabled and restart
to resume ordinary saving without completing a custom-map return.
Failed or incomplete returns never release save protection.

The captured Top Gear arrival is 283.592712,0.381009579,195.880096,
facing -0.998773813,-0.0034530554,0.0496612005. It is persisted in the
installed manifest and work\TopGear\session-v1\track.ini. Future pack builds
can preserve it using build_session.py --arrival-position X Y Z
--arrival-facing X Y Z; supply both, with finite XYZ and unit facing.
--restore-saving-on-return enables the explicit save-restoration opt-in.
Live position capture reads XYZ and supplies a homogeneous W=1: the fourth
SIMD lane returned by native physics is non-spatial and can contain NaN.
The finite XYZ and unit-facing checks remain mandatory; native memory is
not changed by capture.

build_session.py assembles media-v7 plus visit-v4 in work\TopGear\session-v1.
The manifest explicitly selects State=experimental and Enabled=1,
and pins the SQL, world catalog and strict five-path lifecycle catalog.
Normal State=blocked diagnostic packs still never publish or travel.

Native ownership is captured on CPS's native free-roam dispatch thread:
the global group/microsave component are shared-pinned, the primary player
is intrusive-pinned, and each newly resolved live car reference is released.
The profile is borrowed from player+0x328 and is revalidated before use;
it is NOT falsely treated as an independently retained object.

The initial persistence boundary is profile construction, not a delay:
the exact current profile must have completed native constructor 0x2BF410
while all seventeen hooks were active. Profiles predating observation are
refused with a restart-required error. The same profile identity is required
for entry and return; all subsequent common submissions and completion
lifetimes are tracked. This closes the initial-job gap for that profile,
without asserting that arbitrary unrelated system persistence is covered.

An armed pack now starts native protection on a separate code-ready worker
BEFORE slow catalog hashing and media publication, without waiting for
other mod initialization or database verification. The
activation flag remains unchanged. On a later pack-validation failure,
disable Enabled and restart to restore normal saving. That worker installs and enables all
native visit/persistence hooks itself; CPS initialization only verifies
that protection is active and attaches the free-roam client. A failure there
requires disabling Enabled and restarting to restore saving. The custom stateflow group is
registered in the owning GameStateMachineModule exactly like native launch
0x563F90: GameStateMachineComponent+0x100 weak owner (via 0x3EB640),
verified module vtable 0x2CA1740, then its stateflow manager at +0x50.
Travel remains unavailable until full
pack/database validation and CPS attachment finish. Early protection does
not adopt an already-created profile or ignore pending persistence jobs.
Wait at the title screen for "early native protection active" before
pressing A; if a profile predates that boundary it is still refused.

The earlier sections below document the superseded investigation stages.

HISTORICAL DISABLED WRAPPER DIAGNOSTIC TEST SETUP

The ACTIVE CPS wrapper mod directory is:
  C:\Users\wotsu\AppData\Local\CarPackStudio\runtime\fh2-mods

The earlier E:\forza2port_win\azimuth\fh2-mods installation was NOT the
active CPS wrapper target. The user manually moved the diagnostic setup
to the CPS runtime and verified its four commands in game on 2026-11-07.
The installed 0.1.1-dev DLL has been built and native-host-tested against
that catalog. The new 0.1.2-dev build adds native media preflight and is
not installed pending a usable custom-travel test entry. Updating disk files never replaces a DLL already loaded
by a live game; installation requires closing the wrapper game process.

Diagnostic storage layout:
  TrackLoaderCPS.dll
  TrackPacks\TopGear\track.ini
  TrackPacks\TopGear\media-v6\media.json
  TrackPacks\TopGear\media-v6\mediaoverride\tracks\TopGearCPS\...

The nested staged media directory is storage ONLY, not an active game-file
override. Registration SQL is NOT installed or executed. The manifest
declares its future registration filename, but the diagnostic DLL never
opens it. No hooks, save edits or CPS built-in travel changes are enabled.
Previous Azimuth-install DLL/manifest backups remain in that earlier
TopGear\diagnostic-backup-* folder. They are not CPS-runtime backups.
Updated CPS-runtime files are backed up separately under the active
runtime's TopGear\diagnostic-backup-* folder before replacement.

Start the wrapper/game offline. In the CPS platform-command console run:
  TrackLoaderCPS status
  TrackLoaderCPS list
  TrackLoaderCPS pack 10000
  TrackLoaderCPS travel 10000

Expected: reserved_tracks=1, local_dlc_packs=1, registered_tracks=0,
playable_tracks=0; Dunsfold Aerodrome [blocked]; registration=disabled.
Travel MUST return "error: ... is not playable" without starting a load.
Do not use CPS "track 10000" or teleport expecting a playable custom map.
If absent from the MODS tab, check fh2-mods\platform.log for the
TrackLoaderCPS initialization line/API compatibility and offline-only
status. In-process native-host verification does not prove actual wrapper
initialization; that still needs a fresh game launch.

To disable this setup, close the game and rename TrackLoaderCPS.dll to
TrackLoaderCPS.dll.disabled. Staged files can remain; they are not mounted.

Top Gear is NOT converted or playable yet. This is not a release candidate
for Car Pack Studio. No stock map, career save or game database is replaced.

port_topgear.py validates the complete FM5 IMAP and the known FH2 IMAP tables,
explicitly reports undecoded FH2 extension bytes, audits the source archive,
compares manifests and shader names, and creates a checksum-verified copy of
owned FM5 track assets. The source subdirectory is an untouched staging copy,
not FH2-ready content. Exit code 2 means staging succeeded but conversion is
blocked; 1 means an error. No assets are fetched or redistributed.

Example (PowerShell, use a new output directory):
python .\port_topgear.py `
  --source "E:\FM5_1.0.0.20_x64\Forza_1.0.0.20_x64__8wekyb3d8bbwe\Mount\media\tracks\topgear" `
  --reference "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\tracks\anthem" `
  --output ".\work\TopGear" --track-id 10000

The report records the unresolved geometry mapping, PVS conversion,
materials/shaders, navigation, and spawn/native travel requirements.
FM5 IMAP geometry references are 8 bytes; FH2's version-1 references are
7 bytes with a counted additional model-mapping array. Do not insert
a magic header and call it converted. Likewise,
FM5 and FH2 have DIFFERENT FPVS root schemas despite overlapping version
numbers. FM5 version 50 writes two uint16 fields and visibility zones where
FH2 expects five uint32 fields and additional tables. Keeping version 50
unchanged is not compatible either.

build_geometry.py builds and SHA256-roundtrip-verifies an actual FH2
PGZP version-100 GeoChunk0.minizip and its ChunkMap0.dat. The local
work\TopGear\geometry-v2 component contains 1376 original model payloads and
4796 texture payloads with FH2-supported burG blob versions and verified
PVS descriptor/streaming-level keys. geometry-v1 is obsolete: its texture
IDs were provisional. Neither component is a playable map.

ChunkMap records are uint24 resource ID plus uint8 resource type, not a
uint16 ID and separate quality byte. FH2 native RVA 0x1340F9D constructs
texture keys as 0x05000000 + (descriptor_index << 4) + (streaming_level - 1).
The base source filename corresponds to streaming level 1; suffix _2
corresponds to level 2, and so on. Resident STex blobs are separate.

geometry-v2 also contains the verified 2301 resident texture containers,
a counted table of 2301 converted 56-byte FH2 texture descriptor records,
and PVSAssets.json with source zones and bindings. These resident/descriptor
files are conversion components, NOT files to install directly into FH2:
their destination world metadata and any resident references still need
serialization. Source-declared streaming variants absent from bin.zip are
listed explicitly rather than replaced with invented textures.

python .\build_geometry.py `
  --source-archive ".\work\TopGear\source\bin.zip" `
  --pvs ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --output ".\work\TopGear\geometry-v2"

fm5_pvs.py decodes the verified FM5 visibility/texture/shader/material
prefix, checks all zone model/instance/texture references, and verifies
every texture descriptor ordinal/hash against AssetManifest.xml in bin.zip.
It associates all texture payload filenames and resident STex blobs with
their source descriptor, preserving source-declared missing streaming
variants. No separate FH2 ChunkMap quality byte is invented.
Its report explicitly counts the still-undecoded instance/spatial tail.

python .\fm5_pvs.py `
  --source ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --archive ".\work\TopGear\source\bin.zip" `
  --output ".\work\TopGear\pvs-assets.json"

The geometry builder and PVS report command refuse existing outputs.

fm5_world.py now decodes the ENTIRE Top Gear FPVS v50 file: all 2909664
bytes, with no unparsed trailer. Its tables contain:
  3567 instance texture/material binding records
  3187 primary and 380 secondary model draw placements
  1387 full row-vector transform groups
  2161 per-instance material override bundles and texture hash/index pairs
  61 visibility-zone uint16 arrays, named objects, reference maps and aliases

Every draw's model ID is checked against bin.zip, every instance is covered
exactly once across the two draw tables, texture references are checked,
and non-finite placement/group transforms are rejected. Source half-float
limit bits (including valid infinity sentinels) are preserved, not clamped.
The final texture hash lookup includes 62 aliases; different keys targeting
the same descriptor are retained rather than discarded as invalid.

The world-v1 output contains native FH2 v50 counted draw-placement vectors:
  ModelDraws50.dat       -- 3187 records
  StaticModelDraws50.dat -- 380 records
  WorldSource.json      -- complete decoded source world, including overrides
  world.json            -- verified component report and remaining blockers

All source record bytes and transforms roundtrip unchanged. FH2's additional
optional per-draw settings are serialized as absent because FM5 has no such
field. FH2 reads but discards the old uint16 instance index: the corresponding
target instance/material wiring must still be rebuilt. Raw flags are not
assumed semantically identical. A v60 migration is now schema-verified below.
These files are NOT a complete FPVS/PVSZ world or an installable map.

python .\fm5_world.py `
  --source ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --archive ".\work\TopGear\source\bin.zip" `
  --component-output ".\work\TopGear\world-v1"

For a source-only JSON report, use --output instead of --component-output.
Both forms refuse existing outputs. A source file with the optional extra
world tree present is rejected explicitly until that serializer is decoded.

build_zones.py converts the source visibility lists into binary FH2 v60
PVSZone resources in a separate small PGZP archive:
  zones-v2\GeoChunk1.minizip -- 61 geometry-only PVSZone resources
  zones-v2\ChunkMap1.dat     -- native type-12 keys, local zone IDs 0..60
  zones-v2\zones.json        -- verified cross-component bindings and blockers

The reader/writer was validated byte-for-byte against 95 homespace02 zones
and 553 Anthem mainland zones (648 original FH2 resources in total).
The binary tag is FIVE bytes "PVSZ\0", followed by counted arrays of packed
draw references, model IDs, texture descriptors, uint16 quality words,
8-byte dependency records and two auxiliary uint32 tables. Mistaking the
first vector count for a four-byte header field shifts every later table.

FM5 instance IDs are remapped to PRIMARY draw-vector ordinals, not copied
unchanged; secondary/static instances are reported for separate root wiring.
Each visible draw's model must exist both in the archive and in its zone's
model list. Every requested nonzero texture level must have the matching
packed streaming resource key; level zero must have a resident texture.
The quality word follows FH2's native legacy conversion: low byte 1,
high byte the source quality. Every requested Top Gear level passed these
checks; the 38 missing source-declared variants are still recorded by the
geometry report, not synthesized or silently substituted.

python .\build_zones.py `
  --source ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --archive ".\work\TopGear\source\bin.zip" `
  --output ".\work\TopGear\zones-v2"

These are GEOMETRY-ONLY zones, not a finished track. Non-geometry dependency
and auxiliary tables are empty, upper packed-draw metadata bits are unset,
and source shader/material/physics behavior is not advertised as compatible.
PVSZLookup/ChunkAssign, target v60 root assembly and placement registration, material
overrides, collision/navigation and native travel must still be assembled.
Do not install these components or mark track 10000 ready.

fh2_pvs.py now reads and writes the COMPLETE native FH2 v60 streamed FPVS
root. Original HomeSpace02 (391018 bytes) and Anthem (24200394 bytes) roots
roundtrip byte-for-byte, including optional settings, all draw/group tables,
scene flags, signed visibility maxima and texture aliases. Inline zones and
the optional extra world tree remain explicitly unsupported.
Its --source/--output command persists checksum and byte-exact schema evidence.
This validates the reference schema, not a playable Top Gear conversion.

world-v2 contains all 3187 primary and 380 secondary native v60 draw records.
Source 73-byte fields are preserved; native constructor additions are four
0x7F metadata bytes, absent optional settings and the vector (0,1,0,0).
Use the fm5_world.py component command with --target-version 60 and a new
output directory. Full target root/spatial/material assembly remains.

collision.py decodes the source legacy collision and writes actual native
FH2 streamed .phys resources. The first two big-endian .col integers are
grid dimensions, NOT file versions; the real envelope version is 2.
Native triangle attributes are TWO original bytes in original order:
the Top Gear ground word 0x00770000 becomes (0,119), never (119,0).
Vertex byte pointers become indices; padded source subcell lists become
dense counted uint16 lists with signed offsets and -1 for missing cells.
XYZ, packed vertex metadata, triangle normals and attributes are checked
against the original after reading the persisted archive. Target vertex W
is 1 for homogeneous points; the legacy loader discards the source W.

The latest physics component contains 210 cells, 65236 vertices and 53862
triangles, with type-1 keys in ChunkMap2.dat, GeoChunk2.minizip and a counted
CollisionRoots60.dat table. Cell bounds use the source grid border and
actual vertex heights; identity transforms preserve world-space geometry.
An optional --ground-at X Z compares source and persisted target ground hits.
The candidate arrival (202.150299,31.536470) hits Y=-0.7472915046 twice,
from duplicate boundary-cell triangles. This is not in-game spawn validation.

python .\collision.py `
  --source ".\work\TopGear\source\ribbon_00\TopGear_track_00.col" `
  --component-output ".\work\TopGear\physics-v3" `
  --ground-at 202.150299 31.536470

Use a new output directory for every build. Native reference physics
serialization was verified against all 529 original HomeSpace02 templates.
Zone dependency records have a separate index plus a packed resource key:
the reference physics indices start at node 219 while resource IDs start at
0. Do NOT substitute the resource ordinal for the first dependency field.
The first index's runtime binding, surface-material semantics and root integration
are still unresolved. No physics component has been installed in the game.

ForzaTechStudio's documented converter handles modelbin/carbin/swatchbin
car assets. It is useful for geometry and textures, but does not document
a world/PVS/navigation converter. Shader-name matches alone are insufficient.

build.bat builds the offline-only diagnostic mod with the existing CPS API
header and VS 2022 Build Tools. The diagnostic DLL belongs in the real
Azimuth fh2-mods directory. Only a blocked diagnostic track.ini belongs in
fh2-mods\TrackPacks\TopGear\track.ini; leave conversion assets in this workspace.
The latest DLL build is not automatically installed.

NAVIGATION AND FREE-ROAM SPATIAL VISIBILITY

navigation.py validates every named MLP table in source ribbon_00\AI.zip.
All fields, offsets, types, counts and payload boundaries are checked;
overlap, duplicate names, non-finite values and unparsed bytes are rejected.
FM5 uses both int/int32 spellings and byte char tables. Top Gear contains
1435 waypoints and 18 navigation segments. Its native waypoint visibility
step is 25: ceil(1435/25) is exactly the source's 58 route zones, followed
by 3 special zones. Native FH2 RVA 0x1156790 performs integer waypoint/step
division. This establishes the source route mapping, not free-roam behavior.

The original FH2 Anthem Track.geo has the same named XYZ/normal/width
field types, plus a six-word iCRC integrity field absent from FM5.
That field is NOT copied, zero-filled or claimed optional. Engine integrity
requirements and AI activation remain unresolved. navigation-v1 contains
verified counted XYZ data and navigation.json, not an installable AI package.

python .\navigation.py `
  --source-ai ".\work\TopGear\source\ribbon_00\AI.zip" `
  --reference-ai "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\tracks\anthem\ribbon_00\AI.zip" `
  --source-pvs ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --output ".\work\TopGear\navigation-v1"

spatial.py implements native HEXY v100/v101 grids: a 32-byte header, a
signed int32 cell-to-zone table, counted-by-header int32 coordinate pairs,
and (v101 only) one six-bit neighbor mask per zone. Native construction and
queries were traced at RVAs 0x1887C50, 0x1887DB0, 0x1888770 and 0x1889D20.
Coordinate queries use the original single-precision staggered hex math,
not a rectangular-grid approximation. Original HomeSpace02 (203 zones)
and Anthem (2796 zones) files roundtrip byte-exact; every zone center maps
back to its original zone. No original visibility files were changed.

build_spatial.py builds a new free-roam coverage grid rather than retaining
Top Gear's race-route-only zone selector. spatial-v1 has 288 zones in a
24x12 grid with native radius 100, covering every source collision vertex
and navigation waypoint. Each spatial zone combines source views supported
by nearby route samples and their adjacent source route zones. At track
crossings, multiple source views are combined; all 3 special source views
are retained everywhere until their selectors are decoded. Texture requests
use the maximum requested original quality for each descriptor, validated
against actual resident/streamed payloads. Source-visible instances must
remain represented. This is conservative source-view synthesis, NOT a full
visibility/occlusion bake or proven off-route rendering.

python .\build_spatial.py `
  --source-pvs ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --archive ".\work\TopGear\source\bin.zip" `
  --source-ai ".\work\TopGear\source\ribbon_00\AI.zip" `
  --source-collision ".\work\TopGear\source\ribbon_00\TopGear_track_00.col" `
  --output ".\work\TopGear\spatial-v1"

spatial-v1 contains TopGearCPSout_00.hex, 288 geometry-only PVSZone resources
in GeoChunk1.minizip, type-12 ChunkMap1.dat and a version-100 ChunkAssign.dat
assigning the new zones to dedicated chunk 1. Every persisted binary is
checked against its conversion input. These replace the older zones-v2
component ONLY in future assembly, not in the installed game. Final FPVS
scene/ranges, PVSZLookup runtime-node IDs, physics/light dependencies,
static placement registration and off-route validation remain required.
Do not install this component or advertise it as playable.

LOCAL DLC DELIVERY (mod-managed, not a platform-signed DLC)

Top Gear's intended engine identity is track 10000, environment 10000,
MediaName TopGearCPS, under game:\media\tracks\TopGearCPS. Its eventual
root must be named ribbon_00\TopGearCPS_00.pvs. This is an additive map,
not a replacement for Anthem/Test03 and not an impersonation of Storm
Island's paid offer, product ID, license or mount entitlement.

build_dlc.py creates a disabled registration component from read-only FH2
and FM5 databases. It inserts only a new Tracks row and Environments row,
and verifies the resulting rows in an isolated in-memory database against
the actual FH2 schemas. All original rows must remain identical. ID and
case-insensitive MediaName conflicts fail explicitly, including conflicts
introduced later before the SQL runs. Platform API add_sql supplies the
per-mod transaction; the prototype does NOT call it yet.

python .\build_dlc.py `
  --database "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\stripped\gamedbRC.slt" `
  --source-database "E:\FM5_1.0.0.20_x64\Forza_1.0.0.20_x64__8wekyb3d8bbwe\Mount\media\db\gamedb.slt" `
  --output ".\work\TopGear\dlc-v1"

The output includes registration.sql, a blocked track.ini with LocalDLC
metadata, and dlc.json. New Active/Renderable/IsInstalled/IsPurchased flags
are zero; ContentId 0 identifies local mod content, not a paid DLC license.
MediaTrack points to the NEW track ID, not mainland 542. Source length and
friction are carried over; inherited FH2 UI/start-grid fields remain
provisional. No FM5 UI, navigation, platform offer or save data is imported.
Do not run this SQL or install these components as a playable map.

TrackLoaderCPS accepts both old blocked diagnostic manifests and the new
LocalDLC section (Format=1, EnvironmentId, MediaName, Delivery=mod-managed,
Registration=registration.sql). IDs/namespaces must be unique among packs.
The new pack command reports the namespace and disabled registration state;
it does not execute SQL or mount assets. Future verified packs need the
complete media, native loading/travel flow and game validation first.
Native SwitchOnActiveTrack at RVA 0x93F0F0 compares 542 and 549 explicitly;
other IDs fall through to the mainland transition. A database row cannot
make this a generic custom-map selector. Stock transitions/checks must remain
unchanged while a separate verified custom-target flow is implemented.

From the CPS console, explicit platform commands are:
  TrackLoaderCPS list
  TrackLoaderCPS status
  TrackLoaderCPS travel 10000
  TrackLoaderCPS pack 10000

10000 is RESERVED by the manifest, not registered as an engine track.
Travel returns an explicit error until the port and custom loading flow
exist. Current CPS "track" and "tp" built-in behavior is unchanged.
Future integration must use the native game thread, handle custom target
selection, preserve built-in DLC checks, and verify loading, arrival,
drivable collision and return-to-mainland before advertising support.

build_bindings.py combines the verified spatial and physics components into
physics-bound v60 zones and a native raw-u32 PVSZLookup.dat. Dependency pairs
are runtime node index + packed resource key, NOT two resource IDs. Native
RVA 0x133F2AA uses the spatial ordinal to read PVSZLookup, then requests the
type-12 zone key using that node. All 203 HomeSpace02 lookup entries matched
their original zone dependency pairs. RVA 0x1359943 uses type-1 resource IDs
to select physics templates independently of their runtime dependency nodes.

python .\build_bindings.py `
  --spatial ".\work\TopGear\spatial-v1" `
  --physics ".\work\TopGear\physics-v3" `
  --output ".\work\TopGear\bindings-v1"

The builder validates input checksums, every archived payload and root
physics-template ordinal before writing. It preserves geometry visibility
unchanged and requests every collision template in each zone, following
HomeSpace02's conservative collision policy. Persisted zone/node/lookup
consistency is verified. Its 288 zone nodes and 210 physics nodes occupy
498 indices in a NEW component-local node namespace; they do not reference
stock node IDs. Final assembly must retain this namespace, allocate sufficient
native capacity, and add nodes for other resource families without collisions.
The assembled root is now supplied by build_root.py below. Other resource
families and engine registration remain unverified. Collision files remain
in physics-v3; bindings-v1 is not
an installable map. Memory/performance and actual engine loading still need
validation. No registration, stock files, or installed mods are modified.

NATIVE ROOT AND STAGED MEDIA

build_root.py assembles the complete native v60 FPVS schema, collision
roots, runtime capacities and canonical source-instance draw ordinals.
FH2 initialization at RVA 0x1336E70 clears the serialized secondary draw
table, so the candidate promotes all 380 source secondary placements.
A real coverage audit also found 381 source placements absent from every
source-backed route view. root-v2 requests those placements globally,
including their 44 models and 101 group/instance/material textures, rather
than merely including invisible records in the root. This conservative
policy needs an engine/performance test; it is not an occlusion bake.

python .\build_root.py `
  --source-pvs ".\work\TopGear\source\ribbon_00\TopGear_00.pvs" `
  --source-archive ".\work\TopGear\source\bin.zip" `
  --geometry ".\work\TopGear\geometry-v2" `
  --bindings ".\work\TopGear\bindings-v1" `
  --spatial ".\work\TopGear\spatial-v1" `
  --output ".\work\TopGear\root-v2"

The persisted root contains all 3,567 original placement records, 288
spatial zones and 210 collision roots. The source bytes/transforms and
native root roundtrip are verified. Opaque FM5 route visibility data is
omitted using the native serializer's absent-table flag, not reinterpreted
as draw IDs. Rendering with that flag remains experimental.

shader_scenes.py decodes LSCE/DBLS v1.3 and v1.4 and validates compiled
vertex/pixel program closure for both durango and pc. The native parser
supports the source v1.3 layout; no shader version headers are spoofed.
All 36 Top Gear shaders have all 1,152 required compiled program members.
Root shader/material paths are relocated into the custom bin namespace;
the original shader execution, material hashes and animated asset bindings
still require engine confirmation. bin.zip retains original asset bytes.

NATIVE OPEN-WORLD NAVIGATION

FH2's free-roam LoadNavigationWorld uses NAVW v0x100, not just Track.geo.
The waypoint module also opens OWWF v0x102. These share a generation stamp
and repeat their 16-byte envelope as a footer. NAVW has a 112-byte table
header and nine separately 16-byte-aligned vectors; native pointer fixups
cover ten reference families. openworld_navigation.py reads/writes the
original Anthem NAVW (1,691,824 bytes) and OWWF (5,568 bytes) byte-exact.
Native readers were traced at RVAs 0x191B5E0/0x191B1B0 and 0x18E0620.
OWWF requires a positive cell count; an empty-file workaround is not used.

python .\build_openworld.py `
  --source-ai ".\work\TopGear\source\ribbon_00\AI.zip" `
  --source-collision ".\work\TopGear\source\ribbon_00\TopGear_track_00.col" `
  --output ".\work\TopGear\navigation-v3"

navigation-v3 contains a source-backed closed native road: 1,435 nodes,
1,436 ordered references and matching reverse incidences. Every source
waypoint position/order is preserved. Its 98 size-200 waypoint cells cover
all source route samples and collision vertices. AI and traffic are
explicitly disabled through stock disabled-road metadata. The native
none_10000 profile reference and cell-query behavior still need engine
confirmation. The shared deterministic NAVW/OWWF generation stamp is not
an invented or copied six-word MLP iCRC. This is first-test navigation,
not a complete road/profile/traffic bake or a first-test-ready map.

python .\build_media.py `
  --root ".\work\TopGear\root-v2" `
  --navigation ".\work\TopGear\navigation-v3" `
  --output ".\work\TopGear\media-v2"

media-v2 stages 1,348 verified files under media\tracks\TopGearCPS.
All three GeoChunks/maps, ChunkAssign and PVSZLookup live at track level,
following the original HomeSpace02 layout. The root, HEXY, NAVW and OWWF
live in ribbon_00. Support assets are unpacked into bin so a future loader
can provide explicit per-member file overrides; no unproven custom ZIP
mount behavior is assumed. The builder checks component hashes, physics
provenance, native draw/model/texture/node closure, disabled navigation
metadata, collision/waypoint cell coverage and every persisted file hash.
Omitting --navigation preserves the root-only diagnostic staging workflow.

FIRST-TEST GATE: STILL BLOCKED

media-v4 adds source-backed track settings, locators and arrival metadata.
It remains persistent conversion output, not installed or enabled DLC.
Runtime environmental companions, resident/animated asset resolution and
safe native custom load/arrival/return remain unfinished. No stock files,
SQL registration, save data, installed diagnostic DLL or CPS travel behavior
have been changed.

CUSTOM MEDIA PATHS AND STATIC-CIRCUIT TEST PROFILE

game:\media\tracks\TopGearCPS is the engine's LOGICAL namespace. Custom
files for that namespace belong under the actual game's
Mount\mediaoverride\tracks\TopGearCPS, never in stock Mount\media\tracks.
Extra Options mounts mediaoverride at game:\media with higher priority.
Its media_overrides.c implements the native folder mount/resolver;
the platform set_file API takes media-relative paths such as
tracks\TopGearCPS\ribbon_00\TopGearCPS_00.pvs and writes mediaoverride.
Current CPS runtime logs confirm EnableMediaOverrides and the native
mount; the mod platform explicitly leaves mounting to Extra Options.
Keep logical shader/material/track names unchanged: replacing them with
mediaoverride paths would bypass the expected engine namespace.

0.1.1-dev pack output distinguishes logical media from the platform's
actual game_dir\mediaoverride destination, and reports file_mount=disabled.
It never infers the game's Mount from the wrapper runtime directory.

With explicit user approval, environment-v3 adds a static-only first-test
profile: PhysicsDefinitions.bin contains exactly eight zero bytes and
ribbon_00\CollObjs.xml contains an empty CollObjs root. The native load
chain is 0x2D3320 -> 0x377460 -> 0x12DB000. Binary reader vtable
0x2EABF28 slot 0x1C8 -> 0x1201790 reads NumDefinitions as UINT64.
At zero definitions, the per-definition/global-index loops are skipped,
and 0x12DC2F0 skips its additional data too. No version is fabricated and
no unrelated stock definition catalog is copied. This is reader-supported,
not engine-tested. Dynamic registration omits source cones/barrels;
their original visual placements remain, but movable-prop collisions
are intentionally unavailable. The 210 streamed road/ground physics
templates and all arrival footprint checks remain unchanged.

The separate cone-free props test builder removes all 318 cone placements from
zone visibility (192 registered cones plus 126 visual-only instances of the
same three models), preserves the other 29 dynamic prop entries
(including traffic signs and poles), and uses the installed game's native
PhysicsDefinitions.bin. This enables the native movable-prop physics path;
actual collision/destruction behavior still needs an in-game check. It builds
a separate pack and does not overwrite the source:

  python .\build_prop_test.py `
    --source-track ".\path\to\CustomTracks\TopGear" `
    --source-objects ".\path\to\Mount\media\tracks\topgear\ribbon_00\CollObjs.xml" `
    --source-pvs ".\path\to\Mount\media\tracks\topgear\ribbon_00\TopGear_00.pvs" `
    --physics-definitions ".\path\to\Mount\media\tracks\anthem\physicsdefinitions.bin" `
    --output ".\work\TopGear-cone-free-test"

Conservative performance comparison (separate pack; no automatic installation):

  python .\build_performance_test.py `
    --source-track ".\path\to\CustomTracks\TopGear" `
    --output ".\work\TopGear-performance-test"

This keeps every model/texture and highest-mip residency request, the accepted
brightness, shaders, physics, and cone omissions. Only small translated prop
draws qualify: the conservative sphere enclosing all linked LOD models must be
at most 20 m. Their final draw range becomes 500 m. Earlier bands are preserved
unless above 450 m, allowing a final LOD before culling. Existing signed LOD
offsets must already be repaired; this does not generate new LOD meshes.
Large objects and zero-translation world-space road/terrain/decals stay intact.
Zone draw pruning includes a whole-cell plus mesh-sphere safety margin and never
prunes model/texture dependencies. Builds verify non-zone archive payloads
byte-for-byte and write a new media catalog and track.ini checksum.

The first cone-free comparison optimized 1,344 of 3,249 visible draw records,
reducing total zone draw references from 935,712 to 608,814. This is an offline
visibility-workload measurement, NOT a measured FPS gain. The 500 m final cull
can still cause distant small props to disappear; no seamless fade is promised.
Test the same camera/car/settings, drive around, revisit earlier locations,
and check texture stability, scenery pop-in, mirrors, and reflections.
Headlight shader changes are deliberately excluded from this FPS comparison.

User testing rejected the first performance profile: prop pop-in increased but
FPS did not observably improve. Do not adopt it as the default working profile.

Temporary plane LOD diagnostics:
Compile standalone TrackLoaderCPS.cpp with TRACKLOADER_STANDALONE and
TRACKLOADER_LOD_DIAGNOSTICS defined to enable two log-only, signature-checked
probes. Normal builds do not enable these probes. The diagnostic loader writes
"lod-trace" entries to the platform log, capturing live plane LOD thresholds,
bypass flags, selected band, view/pass, computed distance, and rejection result.
It does not change rendering decisions or control gameplay. These callbacks
can affect performance: use this build to diagnose LOD switching, not measure FPS.
Drive close to the plane, then to the far end of the track, pause briefly at each
location (sampling is every five seconds), and exit the game normally to preserve
the log. Test normal driving before Photo Mode. Restore the backed-up normal
TrackLoader.dll after diagnosis. No shader/brightness/physics edits are involved.

python .\build_environment.py `
  --source ".\work\TopGear\source" `
  --native-settings "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\tracks\Anthem\TrackSettings.xml" `
  --physics ".\work\TopGear\physics-v3" --static-test `
  --output ".\work\TopGear\environment-v3"

python .\build_media.py `
  --root ".\work\TopGear\root-v2" `
  --navigation ".\work\TopGear\navigation-v3" `
  --environment ".\work\TopGear\environment-v3" --mediaoverride `
  --output ".\work\TopGear\media-v6"

media-v6 stages 1,354 verified files in the correct Mount-relative
mediaoverride tree, with each logical media_path and override_relative_path
in media.json. No game folder is modified by these build commands.
Earlier media-v4 and media-v5 are retained as diagnostic/history outputs.
Static-test companions do NOT unblock first_test_ready: custom native
target preparation, load/arrival/return, lighting and runtime resource
registration still need implementation and in-game verification.

NATIVE MEDIA PUBLICATION AND TARGET PREPARATION

media-v7 adds media.catalog: ASCII TLCPS-MEDIA-1, media namespace,
then SHA256, byte count and media-relative path per record. media.json
records its SHA256 separately. track_media.h verifies the catalog hash,
namespace, duplicate/path rules and ALL files before publication.
Read-only mappings pin source bytes without write/delete sharing, including
the 1.2GB GeoChunk0, instead of allocating another full archive copy.
Reparse-point paths and unsupported sizes/counts fail explicitly.
Package::Publish uses the platform set_file service, not direct stock
game writes. A failure clears that package's successful overrides and
any failed partial write through clear_file; unrelated namespaces remain.
Release must only be called AFTER the native custom session is unloaded.
Publication is implemented/tested but NOT called by the diagnostic DLL.

Optional blocked manifest media preflight:
  [Media]
  Directory=media-v7
  CatalogSha256=<media.json catalog_sha256>

Directory is a plain child folder of the pack. Its mediaoverride tree
remains storage until publication. Preflight errors retain the blocked
track with an explicit error reason. Legacy manifests without [Media]
continue to work. pack output reports verified_media_files. This is
future loader infrastructure, not a request for another diagnostic test.

The native SwapTrack callback is synchronous: 0x93CFD0 -> 0x19070D0
directly calls std::function slot 0x10 -> 0x93F370 -> 0x9393B0.
track_target.h supplies a thread-local preparation scope for the verified
instruction at 0x939410, AFTER the shared descriptor has been read and
BEFORE native modules/target submission use the local arguments.
It changes only R14D, XMM6's float4 position and XMM7's low heading float.
Other registers, shared travel/career data and manager memory are untouched.
Missing/repeated callbacks and nested scopes fail. The descriptor's track
is NOT required to match: SwapTrack reads only descriptor+B0/E0/108 into
these locals and never reads it again, and custom flows do not stage the
descriptor, so live tests observed a stale value. It is logged
("unstaged descriptor track N") and fully overridden instead.
visit-v5 keeps the player car loaded across SwapTrack, like vanilla
track_swap.common_track_swap. TrackSwapManager (0x365AD70) state 0xF waits
for ForzaSimulation phase (+0x2C8) > 1; the load step at 0xFA17D0 requires
the display-readiness gates +0x3C6 (message 0xA/0x10000) and +0x3D5
(0xA/0x1000D, cars ready). Unloading the car before the swap left both
gates 0 and stalled the loading screen. restore_car_position (scoped by
the arrival-pose hook) still positions the car. Timeouts now log the
manager state, simulation phase and these gates.
Live visit-v5 test: cars-ready (+0x3D5) is now set, but track-ready (+0x3C6)
stays 0. The presentation (sim+0x358) posts 0xA/0x10000 only when its
pending flag +0x26A8 is set and CPresentation slot 0x268 (0x1305D60) reports
every render component (+0x38/+0x58/+0x80 and the +0x40 list) loaded. The
vanilla CPS 542->549 trace completes SwapTrack in about 22s through the same
gate, so a converted TopGear render resource is the remaining suspect.
Stalled-process inspection: the blocking component is TrackPresentation11
(CPresentation +0x38). Its ready check 0x133A9E0 fails on the skybox model
(fc08[+0x82A4] -> model 54) and the probe textures (fbd8, textures 39-42).
The load callback 0x133DE30 completed (+0xF370=1), yet none of the 1387
TrackRenderModel11 or 2301 TrackTexture11 objects were ever requested.
track_diag.h adds 8 log-only probes (begin-load, load callback, sky/probe
request results, presentation unload 0x13B0CA0, texture/model request and
model release counts) to show whether requests are never issued or are
issued and then released by a later unload. They do not change registers.
Diagnostic run result (root cause): Anthem unloaded normally; TopGearCPS
loaded with sky list [-1,54] and the sky/probe calls returned 1, but no model
or texture request was issued. 0x1337F20 returns 1 even when it skips the
request: it only requests when 0x1330940 finds key 0x4000000|model in one of
the 256 per-chunk tables (+0xE378) AND the matching archive handle at +0xAB70
is non-null. The loader at 0x133CC2C reads tracks\<name>\ChunkMap%d.dat (the
tables were filled: 12344/576 entries) and then opens
"game:\Media\tracks\<name>\GeoChunk%d\<*>0" through the device that served the
ChunkMap file. Extra Options' mediaoverride device cannot mount
GeoChunk*.minizip archives, so every +0xAB70 handle stayed null and nothing
streamed. Track media is therefore published directly to
<Mount>\media\tracks\<MediaName>\ (Package::PublishTo): size+SHA256-identical
files are kept (the checksum cache), changed ones are written via a temp file
and atomically replaced, and stale copies of the same paths under
mediaoverride are cleared/deleted so ChunkMap is no longer served from the
override device. These files stay on disk after the session as the verified
cache; remove <Mount>\media\tracks\TopGearCPS to uninstall. The five visit
resources still go through set_file/mediaoverride.
Correction after the next live run: publishing the whole folder to media
crashed (0x13E2A10, null +0x18) with models=0/textures=0, because the pack's
bin\ folder is Extra Options' unpacked form of bin.zip and only works inside
mediaoverride (stock Anthem ships media\tracks\Anthem\bin.zip). Now only
tracks\<name>\GeoChunk*.minizip are written to Mount\media (Package::Direct);
all other track files are published through set_file/mediaoverride again,
and stale media copies from the previous build are deleted at startup.
The source active map and descriptor's map are distinct: when returning
from 10000 to 542, the unchanged career descriptor can still contain 542.
This policy helper is unit-tested, but no hook is registered/enabled yet.
It still needs scoped native stateflow dispatch, unload/load and save/control
wrappers around the complete custom visit before activation.

Native tests:
  tests\test_track_media.cpp -- catalogs, pinned byte hashes, namespace,
    transactional publication/rollback with a mocked platform; a two-argument
    invocation preflights a real media directory/catalog SHA without publishing.
    The full invocation also tests direct Mount\media publication next to the
    executable: copy, unchanged-file cache, changed-file rewrite and stale
    mediaoverride removal (copies ~1.2 GB; takes a few minutes).
  tests\test_track_target.cpp -- local argument changes, unaffected registers/
    unrelated threads, missing/repeated callbacks and return descriptor handling.
  tests\test_track_save.cpp -- visit-held microsave/profile guards, cross-thread
    submission, incomplete return refusal and retained protection after abort.

NATIVE VISIT SAVE GATE (NOT ENABLED)

DisableMicroSaveWrapper dispatches SetMicroSaveEnabled(false/true) at
0x8822B0/0x883F50. Its reflected setter 0x5E6730 writes a Boolean at
module+0xB0, not a nesting counter. A parent flow abort can therefore
re-enable microsaves before a custom track has unloaded. track_save.h
holds separate visit protection; target-preparation scope exit, timeout
or a failed return does not release it.

The microsave branch at 0x5E640A normally tests that Boolean before
submitting its native job. The gate sets only ZF before this JE, taking
the existing no-submission epilogue without touching the Boolean.
The full-profile path is separate: SaveProfile calls 0x2F3F40 (sync)
or 0x2F3F50 (async), both entering 0x2F3A60. Several non-stateflow
callers and direct callers also enter that common function. Its branch
at 0x2F3AAA can take the native false-return path 0x2F3F1E before
serialization or dirty-state cleanup. The profile callback sets only ZF
at that branch, never fabricating a successful save.

All four exact instruction byte sequences were checked against the owned
executable. The callbacks are tested for otherwise unchanged CPU context
and block attempts from any thread while held. Unexpected owner instances
retain protection and must be reported; release requires the original
owners, the initiating thread, idle native jobs and verified mainland
gameplay/pose/career restoration. No destructor releases the gate.

This is policy infrastructure, not a claim of complete engine save safety.
No save hooks are registered or enabled by the diagnostic DLL. Hook
acceptance, native save-owner/job observation, all persistence entry
points and the caller reactions to refused full saves still require
integration/validation before a custom driving session is enabled.

Standalone dispatch investigation: LoadEntityGroup's constructor is
0x8A9B50 (vtable 0x2D096C8); its begin callback is 0x8AD6A0.
For wait_for_state_machine_exit=false it obtains the group manager through
0x52BAB0 and calls 0x50A6B0 with an output shared pointer, native filename
string, parent-group shared pointer and two Boolean options. The waiting
variant instead calls 0x50AC30. A dedicated autostart entity group is being
investigated as an entry path without replacing CPS-owned pause.xml.
track_group.h now implements the non-waiting native ABI through verified
TLS context, entity-system and group-manager accessors. Its six signatures
are checked before initialization. The native function consumes/reset its
filename and parent reference; the helper uses native allocation/free and
shared-reference release rather than the mod CRT's string allocator.
Submit reports only a submission receipt, NOT a loaded or activated map.
The receipt can contain a control block with a null object. It is not a
group pointer and cannot establish group/component ownership. Native calls
must come from the CPS game-thread callback, never a console thread.

Group insertion 0x1902230 has an immediate association path through
0x1903480, which visits entity components through 0x551800, and another
path through 0x190D360 that inserts into the owner's +0xE8 list before
dispatching 0x1907240. Its callback 0x19100F0 sets group+0x9C; it does not
directly autostart the component. The association callback 0x1910250 reaches
0x190BE60 and the native event dispatch at 0x190D9B0. Component virtual
slot +0x20 is autostart 0x559260. Entry and replacement therefore cannot
depend on Launch occurring inside the non-waiting loader call.

The bridge now intercepts the native parser 0x50A2E0 before the loader
inserts or replaces the group. Unlike the loader's submission receipt,
the parser output is the real parsed group shared pointer. Native
0x1903480 visits its entity tree at group+0x28, taking entity pointers at
node+0x30. Native 0x551800 visits entity+0x68's outer component-type list,
then the inner list at type-node+0x18, whose nodes hold component/control
pointers at +0x10/+0x18. track_ownership.h mirrors this bounded read-only
walk and requires exactly one matching machine. Invalid/cyclic/oversized
graphs, expired owners or duplicate candidates are explicit errors.
Bounds are 4096 entities, 256 component types per entity, 4096 components
per type, and 16384 combined type/component entries for the entire walk.
It retains the actual group AND machine shared owners before activation.
No guessed manager writes, broad flow latch or submission-receipt cast
is used.

The full-profile early exits at 0x2F3A93/0x2F3AA0 bypass the main submission
gate and can reach cleanup at 0x2F3EDE. A third save-gate hook now intercepts
the JE at 0x2F3EE2 (74 3A) and skips the dirty-state clear at +0x890,
cleanup calls and notification, returning native false at 0x2F3F1E instead.
It uses the same profile-owner verification and changes only ZF. This
closes that observed gap, not every possible persistence path in the game.

Direct-profile microsave 0x2FAD40 is a separate serialization/submission
function. In addition to MicroSaveComponent at 0x5E6562, native functions
0xD6B940 and 0xD70EA0 call it without the module gate. Its JE at 0x2FAD8E
(0F 84 5D 03 00 00) now uses the held profile gate and goes to 0x2FB0F1.
That native disabled-save path clears the output shared handle, constructs
the native empty result and consumes the callback before returning the
output handle at 0x2FB135. It does not serialize, submit or return a fake
save success. Only ZF changes; profile-owner checks and retained protection
for unexpected owners are shared with the full-profile gates.

The module's real microsave job is a shared handle at +0x100/+0x108,
assigned at 0x5E656A and reset by 0x5E4A70. Native MSVC RTTI
identifies MicroSaveComponent@EntityModel@@: type descriptor 0x30EB690,
complete object locator 0x24227A0 (offset zero), vtable 0x2CAA7E8.
track_live.h validates that exact primary vtable, Boolean enabled byte
+0xB0 and consistently read job/control pair. Idle means a null job with
either no control block or the verified native null-job control block.
Reset 0x5E4A70 calls 0x2BA160 with a null object, allocating a control
block (vtable 0x2C60C68) with a null payload at +0x10. Capture verifies
that exact type, positive strong count and null payload, and rechecks
the module handle before accepting it as empty. Other null-job owners
are refused; a non-null job remains pending regardless of control type.
An object without a control block, invalid Boolean or changed read fails.
No job dereference, forced completion or save-enablement write is performed.

FinishReturnObserved now also observes that actual module/job handle and
refuses release while either job or control is held. The full return gate
still compares the module with the original held owner. The native enabled
Boolean alone is NOT proof of save safety or job completion.

Profile+0x898 is a native wide filename string: both full-profile and direct
microsave pass it as R8 into 0x11FEFD0, whose 0x11FF178 SSO check uses
capacity +0x18 (threshold 8), then 0x11FF187 consumes the filename text.
It must NOT be misread as a pending-save-job pointer. The separate global
profile persistence service and outstanding full-profile jobs still need
verified observation before production travel can be enabled.

NATIVE STATIC VISIT FLOW AND CAR RESTORATION (NOT INSTALLED)

build_visit.py now generates five native resources in a separate component:
  stateflow\trackloadercps.xml
  EntityModel\entities_trackloadercps_enter.xml
  EntityModel\entities_trackloadercps_return.xml
  EntityModel\entities_trackloadercps_static_route.xml
  EntityModel\entities_trackloadercps_mainland.xml

The enter and return groups autostart independent GameStateMachineComponent
instances. No CPS pause.xml or stock resource is included or replaced.
The native resolver at 0x50A2E0 prefixes plain entity filenames with
game:\media\EntityModel\; 0x50A6B0 appends .xml. These logical resources
therefore stage under mediaoverride\EntityModel, not in the track folder.

The enter transition takes game control, blocks pause, fades/loads,
disables AI, unloads the car/navigation, uses native SwapTrack, loads the
custom NAVW/OWWF and waits on the native locator predicate. It replaces
track_route with an empty static group, then loads/restores the player car,
waits for surrounding streaming and resumes HUD/control. It intentionally
avoids mainland events, career_swap, StoreCarPosition, StartRace and saves.
AI/traffic and mulligan recording remain disabled for the custom visit.

The return transition swaps to mainland and loads the independent
entities_trackloadercps_mainland group without waiting for group exit.
It inherits the normal entities_offline_freeroam route but adds an explicit
autostart GameStateMachineComponent with trackloadercps.resume_mainland.
It does NOT load entities_route_offline_freeroam or its FreeRoamSetupComponent.
The dedicated resume owns car loading/restoration, streaming, loading
snapshot/fade/HUD release and controls. No career clear, StartRace, save,
initial-experience/post-championship flow or snap-to-road is called.
Road snapping would move the captured departure pose; it is not appropriate
for exact custom-visit return. AI/traffic and mulligan remain disabled on
this first-test return, intentionally matching the static test restrictions.

Returning from the outer transition is NOT proof that dedicated resume
has finished. Both components and live gameplay/save/career observations
remain required. Normal mainland route entities can still have their own
engine-side effects; omitting explicit startup nodes does NOT prove all
career state is unchanged.

The builder checks every external native state and its supplied/required
parameters against the owned stateflow.zip. It also checks the original
mainland entity inheritance/startup target and native autostart component.
The inherited base route must not already contain a stock startup entity
or FreeRoamSetupComponent. Critical resume primitives are verified as
exact direct native behaviors (including StartDriving, NOT StartRace);
an archive which delegates those states into career flow is rejected.
visit-v4 contains all five resources, hashes, input archive hashes and
explicit required runtime bindings. visit-v1/v2 use the earlier return
design; visit-v3 is the initial dedicated-resume draft. Use visit-v4.
All remain unmounted
and first_test_ready=false. No stock archive or game folder was changed.

python .\build_visit.py `
  --stateflow "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\stateflow.zip" `
  --entitymodel "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\EntityModel.zip" `
  --output ".\work\TopGear\visit-v4"

track_pose.h provides scoped native RestoreCarPosition local overrides.
Its begin callback 0x8FDFF0 reads an existing restore record or locator
fallback. At 0x8FE26F and 0x8FE286 the final facing and position vectors
are in XMM0 immediately before stores to local stack slots. The callbacks
replace ONLY XMM0, in facing-then-position order within the same RBP frame.
Missing/repeated/out-of-order callbacks, another frame and nested scopes
fail. They do not change saved restore records or career fields.

Facing is an XYZ DIRECTION VECTOR with W=0, not a quaternion. The native
0x12518E0 builds a basis using a three-component dot product and vector
normalization. Native StoreCarPosition's live-car fallback obtains the
physics object at car+0x4C08, requests axis 2 through vtable+0x970, and
position through vtable+0x270. It stores position/facing at record+0x10/
0x20 through 0xA6A210/0xA6A200. Return capture must use those native live
pose semantics, not the potentially stale career descriptor's position.

Native locator module update 0x5D8200 schedules route collection loading
only for stock IDs 542/549, through 0x5E7E00/0x5E77C0. Its ready flag at
module+0x80 becomes true when the pending collection queue is empty.
Thus HasLoadedRouteCollections alone does NOT prove that custom locators
exist. The static route uses no Route0/event collection, but its actual
track Locators.xml loading and car/streaming readiness still need runtime
verification. The original Anthem ribbon itself has an empty TrackLocators
root; the staged Top Gear root preserves its six source locators.

Validation:
  tests\test_visit.py -- nine build/contract/order/locator-loop tests.
  tests\test_track_pose.cpp -- arrival/return local arguments, unchanged
    registers, unrelated threads, frame/order/count/invalid-input refusal.
Exact pose hook bytes and native facing conversion were verified against
the owned executable. No pose hook or standalone flow is enabled yet.
Remaining activation work includes safe main-thread group dispatch and
lifetime, scoped native behavior binding, live pose/save-owner observation,
verified database publication and actual map resource closure.

LIVE DEPARTURE/RETURN OBSERVATION (NOT INSTALLED)

track_live.h now captures the actual player car physics pose read-only.
StoreCarPosition's fallback at 0x93CDEE uses 0xF605B0 (car+0x4C08) and
physics vtable slots +0x970(axis=2) and +0x270. The verified concrete table
0x2EB5E18 points to facing getter 0x12CBED0 and position getter 0x12CD150.
The former reads physics+(axis-0x38)*16: axis 2 is physics-0x360, row two
of the rotation matrix. The latter reads physics-0x390. Both return native
float4 vectors. Their exact code signatures, car/physics vtables and getter
slots are checked before observation. No native setter, descriptor or
saved restore record is touched. This is not the offset spawn helper.

Capture requires finite position/facing and unit XYZ facing with W=0;
it rejects unsupported getters and a changed physics owner. Position W
is preserved, not required to be 1: the native getter 0x12CD150 and setter
0x12E27D0 copy all four lanes unchanged, as does StoreCarPosition's
live-car fallback. Published arrival points still use W=1. Return capture,
scoped restoration and verification accept finite native position W
without normalizing it; measured return distance uses XYZ only.
Invalid-pose diagnostics include all position/facing lanes.
Callers must supply CPS's freshly resolved active PlayerCar on the visit
game thread, not a cached/invented pointer. Failed capture preserves the
previous output and returns a concrete diagnostic for runtime logging.

Map capture reads the verified manager at global 0x365AD70 and its current,
target and pending-job fields (+8,+0xC,+0x14). The pending job is a 32-bit
native field, NOT an assumed 64-bit pointer. Capture rechecks manager
identity and does not manufacture settled IDs if a read fails.

FinishReturnObserved now captures both live map and active-car state,
compares the captured departure XYZ Euclidean distance (<=0.25m) and unit
facing-vector distance (<=0.01), and only then calls the existing full
return/save release gate. It replaces caller-provided map/pose-success
fields with these observations. A freshly loaded mainland car can have
a different object/physics pointer; matching the old car pointer is not
required. Existing save-owner/job, career-unchanged, gameplay-ready and
machine-completion checks are still mandatory and are NOT proven by pose.

tests\test_track_live.cpp verifies exact getters, finite/unit/W rejection,
failed-read output preservation, unsupported vtables/getter slots, native
pending-job width, wrong map, pending travel, reversed heading and measured
position/facing thresholds. Exactly 0.25m passes, the next representable
distance fails; 0.2m on two axes fails the Euclidean limit. The bridge tests
now exercise observed return: a 1m mismatch retains saves and group owners,
then a real matching snapshot overrides stale supplied map fields and
releases only after the rest of the full return contract passes.

This revision's live/group tests and all fifteen bridge modes passed under
/W4 /WX. Production active-player lookup, live save/profile job ownership,
career snapshot/readiness and experimental activation remain unfinished.
Installed DLLs, database and game media overrides have not been changed.

NATIVE MACHINE BINDING BRIDGE (NOT ENABLED IN THE DIAGNOSTIC DLL)

track_binding.h now implements platform hook registration and scoped native
callback routing. It checks all sixteen function/instruction signatures before
registering any hook. Installation keeps hooks disabled; Enable is separate
and rolls back on failure. The bridge/runtime, its save gate, and Visit data
must have process/visit lifetimes while hooks or a binding reference them.
These methods are game-thread lifecycle operations, not console callbacks.

Native GameStateMachineComponent autostart 0x559260 reads TargetFlow at
component+0xB0 and dispatches launch 0x5641B0. The component's vtable is
0x2C98CF0; update 0x55D070 executes the machine in component+0xF0.
Native completion 0x551950 destroys and clears that machine pointer.
The destructor 0x5415C0 invalidates an unfinished binding rather than
allowing an address reused by a later component to retain visit ownership.

The bridge captures exactly one custom machine from the explicitly armed
dispatch's exact entry/return resource parser output, checking its vtable
and complete TargetFlow. Dispatch now means accepted owned submission,
not completed activation. A later Launch is accepted only from that pinned
component on the dispatch thread. Unrelated same-flow machines are ignored.
Updates before Launch, repeated Launch and changed thread/flow are errors.
Subsequent updates remain bound to the same exact component.
It scopes SwapTrack's 0x93CFD0 callback and RestoreCarPosition's 0x8FDFF0
callback only while that machine executes. Nested unrelated machines
temporarily clear execution ownership, rather than inheriting the override.

On return, the bridge captures trackloadercps.resume_mainland only from the
exact entities_trackloadercps_mainland.xml parser output requested while
the bound return component executes. Its Launch may occur later, outside
the return callback; ownership is already established and pinned. Dedicated mainland resume
then owns the scoped car restoration, not the outer transition. FinishEntry
requires both pose/target hooks, idle machine and verified custom gameplay;
it detaches entry but keeps saving blocked. FinishReturn requires both
machines idle, all scoped callbacks complete and the full mainland return
verification accepted by TrackSave::Gate before detaching/releasing.
Missing/duplicate callbacks, changed flow/thread or premature destruction
report an error and retain visit save protection. Verified completion
releases pinned group/machine references; errors do not release them.

tests\test_track_binding.cpp builds with /W4 /WX. Its mock platform checks
sixteen disabled hook registrations, signature/registration/enable failures,
immediate/deferred entry and return startup, cross-frame restoration, nested unrelated machines,
premature completion refusal and complete bound mainland startup/return.
Additional modes cover wrong-thread update, duplicate swap, changed flow,
missing parsed ownership, component destruction and the early profile-cleanup gate.
Additional modes verify parser-signature rejection, prelaunch update refusal,
duplicate Launch and unrelated machines with the same custom flow.
These are native bridge tests,
NOT evidence that an actual engine session/group dispatch works.

No production call installs or enables this bridge yet. Standalone native
group submission/ownership now have tested implementations. Verified gameplay/pose/career/save observations,
CPS interface attachment, registration and resource mounting still need
end-to-end wiring before the user's first driving test.

CPS CUSTOM-VISIT COOPERATION (SOURCE BUILT; NOT INSTALLED OR ATTACHED)

The CPS native mod now publishes CarPackStudio.TrackVisit version 1.
include\track_visit_api.h defines the process-lifetime client callback and
acquire/release/held API. A single client attaches once. CPS invokes it
from WaitForPause on the native game thread; acquire is accepted only
inside that callback on an idle, settled mainland tick with no CPS event,
spawn, car reload, teleport, stock travel, pad injection, animation request
or input recording/playback/release operation pending. This is eligibility,
not proof of unchanged career/save state. The client verifies owner identity
and persistence-job readiness; RestoreSavingOnReturn opts in without a
career-state audit.

An acquired lease persists through transition completion and error paths.
While held, CPS skips its own pause/event/spawn/travel/reload dispatch and
rejects lifecycle-mutating console commands under the same critical section.
After the held visit settles on its custom track and gameplay is ready, CPS
still runs the stock WaitForPause poll each frame; skipping it left the
player car without driving input and the pause menu dead.
Supported status/list/capture commands remain available. With no attached
client the extra native can-pause call is not executed and stock dispatch
is unchanged. Acquire/release from console or other threads is refused.
The client must retain its callback/data and release only after verified
mainland return; CPS does not automatically release an abandoned visit.

CPS's generic crash recovery is disabled while the lease is held: a native
fault cannot be unwound to a fake successful load/return. Native crashes
still go through the existing critical logging path. No CPS pause.xml
replacement, installation or map registration is part of this change.

tests\test_track_group.cpp checks consumed/unconsumed native strings,
allocation/shared ownership balance, null-object receipts, unavailable
owners, duplicate submission and return filenames. tests\test_track_visit_api.cpp
checks ABI rejection, single-client ownership, native-tick eligibility,
cross-thread release refusal, reentrant ticks and command isolation.
Both build with /W4 /WX; these are offline tests, not driving validation.

tests\test_track_ownership.cpp tests native list/tree layout, unique machine
selection, both owner pins, reference release, missing/nonmatching flow,
expired owners, unreadable pointers, cycles and duplicate candidates.

Validation: the previous complete Python run passed all 88 tests. This
revision's native group/ownership tests and all fifteen bridge modes passed
(default, signature, cleanup-signature, parser-signature, registration,
enable, missing, destroy, thread, duplicate, flow, deferred, prelaunch-update,
duplicate-launch, ownership-failure). Ownership tests also verify the exact
4096-component boundary accepts a unique matching machine and 4097 rejects
without retaining either owner. Parser failure preserves its original
diagnostic instead of being overwritten by the missing-ownership check.
All changed native tests build with /W4 /WX.
CarPackStudio's cooperation build and tests passed in the previous revision.
The complete 88-test Python suite and all related native group, cooperation,
save, pose and target tests were rerun successfully after this revision.
Only project build outputs were updated; the active wrapper's installed
DLLs, media overrides and database were not modified.

Latest save-observation revision: all sixteen bridge modes (the previous
fifteen plus direct-save-signature), native live, group, save, ownership,
cooperation, pose and target tests pass. Direct-profile gate tests check
inactive/held behavior and byte-for-byte preservation of all CPU fields
except ZF. Live tests cover actual job/control/Boolean/vtable validation;
the observed-return integration refuses pending jobs and invented owners,
then releases using the verified idle original owner despite stale supplied
map/pose/microsave-idle fields. Changed native tests build with /W4 /WX.

NATIVE PERSISTENCE OPERATION TRACKING (NOT ENABLED IN PRODUCTION)

track_jobs.h tracks actual operations from common native persistence
submission 0x11FEFD0, used by both full-profile/direct microsave and two
other callers (0x351D50 and 0x11FED90). The ABI takes service, output shared
pointer, wide filename, data, resource, callback and asynchronous Boolean.
A transparent function detour forwards all seven arguments and the native
result, while a scoped counter covers the entire submission call.

At 0x11FF03D, after 0x11DD380 constructs the result and before serialization
or worker submission, a read-only instruction callback captures the actual
operation/control pair from RSP+0x28/+0x30. The operation's verified native
vtable is 0x2EAE2B0. Both valid owner and positive shared reference count are
required; the tracker pins the native shared owner, not a guessed job field.
No CPU registers or native job data are changed.

Native 0x11EE260 invokes the operation's completion callback (with its
success byte +0x28). Its detour calls the original callback first, then
removes/releases the matching captured operation outside the ledger lock.
Unrelated operations sharing that native callback are ignored. Worker
completion is mutex-protected; a completion exception does not release the
owned job or fabricate completion. Operations are tracked from creation,
so synchronous completion before the outer function returns is covered by
the separate submission counter.

The bound is exactly 64 pending operations. Duplicate capture, unavailable
owners, invalid stack/layout, overflow and unbalanced submission lifetime
latch a diagnostic: an empty list afterward does not fake a safe idle.
Unknown callback instances are not treated as proof of completed tracked
work. Dispatch and FinishReturnObserved refuse while tracked operations
or submission calls are pending or the tracker is faulted.

This ledger only covers work observed after its hooks are enabled. It does
NOT prove there were no operations already in flight before installation.
Production must establish a startup observation boundary or independently
verify the initial persistence service before relying on it. The existing
ReturnState.pendingProfileJob check is retained, NOT overwritten with zero
from an unestablished observation history. This is still a production
blocker, rather than a success-shaped idle fallback.

tests\test_track_jobs.cpp checks pinned ownership, worker completion,
unrelated/repeated completion, completion before submission return,
exception unwinding, rejected owners/layout, duplicate/failure retention
and the exact 64/65 limit. Bridge tests exercise the real registered
submission/capture/completion callbacks, seven-argument forwarding and
byte-for-byte unchanged instruction context. Observed return retains saves
while either operations or supplied initial profile-job state are pending.

Latest validation: all 88 Python tests, all seventeen bridge modes (adding
job-signature), and native persistence, live, group, save, ownership,
cooperation, pose and target tests passed. Changed C++ tests build /W4 /WX.
All three persistence hook signatures were verified against the owned EXE.
No installed DLL, game database or mounted media was changed.

The original return safety issue was that native free_roam_start.enter_offline
runs feature/career setup, event-visibility refresh, island/initial-experience
and post-championship flows. That stock startup is no longer used by the
current dedicated-resume design. The custom test still requires explicit live career
and gameplay checks, initial persistence coverage, actual owner lookup,
production interface attachment and additive media/database publication.
At that historical milestone no playable-test package had been installed.
The current session-only package and active-wrapper setup are described
at the top of this document.

DEDICATED MAINLAND RESUME REVISION

Current lifecycle resources bind only the custom resume machine. Entry and
return SwapTrack scoping is unchanged; return restoration is owned by the
separate pinned resume, including delayed autostart. Native stock startup
launches are not claimed. Both outer transition and resume must finish
before the existing observed return/save gate releases protection.

The fifth independent EntityModel resource preserves the normal mainland
route through native source inheritance rather than copying/replacing a
stock archive or using CPS-owned pause.xml. Its explicit startup contains
only a GameStateMachineComponent, not the stock FreeRoamSetupComponent.
The existing two transition resources and empty custom route remain.
All custom files continue to resolve through Mount\mediaoverride.

Tests check five persisted resource hashes, disabled engine/mount gates,
mainland route inheritance, dedicated target/autostart, omission of stock
startup/career/save/race/snap calls, and load/restore/stream/drive order.
Native bridge tests exercise immediate and delayed dedicated resume.
Validation for visit-v4: all 90 Python tests, seventeen bridge modes and
related native tests passed. The actual owned-archive build's five output
hashes/sizes, custom-only resource paths, dedicated return target and
disabled mounting/engine-readiness gates were verified after persistence.

Resident texture loading was traced to the actual native track loader:
0xFC4B40 queries SELECT MediaName, RibbonIndex FROM Tracks WHERE Id=%d,
then passes MediaName into 0xF92790. Optional patch_bin stores and
crowd/GeoChunk patch stores precede the track bin resource load at
0xF92D96. This establishes the track MediaName source; it is not a car
animation loader. Actual resident resource creation/rendering still needs
an engine test.

python .\build_environment.py `
  --source ".\work\TopGear\source" `
  --native-settings "E:\forza2port_win\game\Anthem_1.0.0.64_x64__8wekyb3d8bbwe\Mount\media\tracks\Anthem\tracksettings.xml" `
  --physics ".\work\TopGear\physics-v3" `
  --output ".\work\TopGear\environment-v2"

environment-v2 preserves 119 source track-settings occurrences after
recursive source/native field-schema comparison. Native v83 differs from
source v81 by FileChunkSize's explicit limit and screenAreaDistances.
The compiler adapts the former and supplies only the latter's native
default. Both bloomPersist occurrences are preserved IN ORDER, including
their distinct source values; they must not be collapsed into a tag map.
Unreviewed fields/structures, excess repetitions, external asset paths
and unsupported version envelopes fail explicitly.

All six source locator records remain byte-identical. Arrival uses 321Root
and checks native converted physics against source collision at its center
and four 1.2m x 2.4m footprint corners transformed by the locator's actual
orientation (not a guessed world-axis-aligned rectangle). Every sample has identical ground
hits. The candidate car position has 0.8m clearance above the highest
footprint surface; actual placement and heading units remain unvalidated.
Source smoke.dds and staticcarcubemap.xpr are also preserved unchanged.

python .\build_media.py `
  --root ".\work\TopGear\root-v2" `
  --navigation ".\work\TopGear\navigation-v3" `
  --environment ".\work\TopGear\environment-v2" `
  --output ".\work\TopGear\media-v4"

The 1,352-file media-v4 checks environment world/physics provenance and
binds arrival metadata to a persisted locator transform before staging.
It recomputes every arrival ground sample and candidate from the staged
native collision payloads and locator, rejecting inconsistent metadata.
Source spherical-harmonics probe v2 is NOT copied into native v4 or
version-spoofed. PhysicsDefinitions and dynamic world/object/audio/lighting
registration remain to be resolved, rather than filled with unrelated map
data. --environment is optional for earlier diagnostic assembly workflows.

Native root helper 0x1336C20 checks track-level
colorgradinglookup%02d.dds, then colorgradinglookup.dds, and only loads a
lookup when present. Source Top Gear has neither file; absence follows
the native optional-resource branch. This helper is not a group or
animation registrar. Likewise, 0x138FC90's .gr2 construction uses
GAME:\Media\Drivers and GAME:\Media\Cars: it does NOT establish the
track's animation base-name convention. Do not rename TopGearout .gr2
assets based on this unrelated animation loader.

Native target submission was located at RVA 0xFDBDE0: manager in RCX,
track ID in EDX, aligned float4 position in R8 and heading in XMM3.
SwapTrack's OnBegin at 0x93CFD0 constructs a callback (0x9393B0) through
the std::function implementation at 0x93F370 and vtable 0x2D28BE8.
The state ownership was confirmed through native RTTI: SwapTrack vtable
0x2D27730 points to OnBegin. The callback obtains its target/position
from shared travel information through 0x8E3670 and also updates native
modules before submitting the request. A setter call alone is therefore
not a complete travel pipeline. Do not replace it with raw manager writes,
mutate shared career data, or claim that successful file parsing proves
load/render/drive/return readiness. The existing blocked travel command
continues to reject track 10000.

Tests: python -m unittest discover -s tests -v
Native tests: compile tests\test_trackloader.cpp with the same MSVC environment
as build.bat, then run the executable with a temporary Azimuth-style root
containing the generated blocked manifest at fh2-mods\TrackPacks\TopGear.
The test verifies manifest discovery and pack/status/blocked-travel replies,
without any SQL, file-override or native-hook services.
