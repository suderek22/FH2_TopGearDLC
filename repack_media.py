"""Re-encode a built media directory's GeoChunk archives with FH2's native 4 KB zlib window."""
import argparse
import collections
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import struct
import sys

from build_media import media_catalog
from fh2_world import MiniZip, Resource, write_minizip

TRACK_PARAMS_SLOT = 7


def digest(path):
    with Path(path).open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def legacy_entries(path):
    """Read entries without the v100 window check so archives built before the fix can be repacked."""
    import zlib
    archive = MiniZip(path)
    rows = []
    with archive.path.open("rb") as file:
        for entry in archive.entries:
            file.seek(entry.offset)
            data = file.read(entry.stored_size)
            if entry.method == 8:
                data = zlib.decompress(data)
            if len(data) != entry.size:
                raise ValueError("Source MiniZip entry size mismatch: " + str(path))
            rows.append(data)
    return rows


def repack(source, output, single_chunk=False):
    """Re-encode with the native window and merge physics into chunk 1.

    FH2 opens max(ChunkAssign)+1 GeoChunk archives (RVA 0x133CB44); zones are all assigned
    to chunk 1, so a separate GeoChunk2 would never be opened.

    Zone models and textures are resolved through the ChunkMap of the zone's assigned
    chunk, while global resources (sky) use chunk 0. With single_chunk every resource is
    merged into GeoChunk0 and every zone is assigned to chunk 0 so both lookups succeed.
    """
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    source_root = source / report["override_relative_path"]
    assignments = (source_root / "ChunkAssign.dat").read_bytes()
    _, count = struct.unpack_from("<2I", assignments)
    if set(struct.unpack_from("<%dI" % count, assignments, 8)) != {1}:
        raise ValueError("Expected every zone in chunk 1")
    merged = (source_root / "GeoChunk2.minizip").exists()
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("*.minizip", "ChunkMap2.dat"))
    root = output / report["override_relative_path"]
    layout = [[0], [1, 2] if merged else [1]]
    if single_chunk:
        layout = [[0, 1, 2] if merged else [0, 1]]
        (root / "ChunkAssign.dat").write_bytes(assignments[:8] + bytes(4 * count))
        (root / "ChunkMap1.dat").unlink()
    for target, parts in enumerate(layout):
        keys, payloads = b"", []
        for index in parts:
            keys += (source_root / ("ChunkMap%d.dat" % index)).read_bytes()
            payloads += legacy_entries(source_root / ("GeoChunk%d.minizip" % index))
        name = "GeoChunk%d.minizip" % target
        resources = [Resource("%08x" % key, key & 0xFFFFFF, key >> 24)
                     for (key,) in struct.iter_unpack("<I", keys)]
        if len(resources) != len(payloads):
            raise ValueError("Chunk map/archive counts disagree: " + name)
        by_name = {resource.name: payload for resource, payload in zip(resources, payloads)}
        if write_minizip(root / name, resources, lambda resource: by_name[resource.name]) != keys:
            raise ValueError("Repacked chunk map differs: " + name)
        (root / ("ChunkMap%d.dat" % target)).write_bytes(keys)
        rebuilt = MiniZip(root / name)
        for position, payload in enumerate(payloads):
            if rebuilt.read(position) != payload:
                raise ValueError("Repacked entry differs: %s #%d" % (name, position))
    report["files"] = [row for row in report["files"] if (output / row["path"]).exists()]
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["minizip_zlib_window_bits"] = 12
    report["geochunk_count"] = len(layout)
    report["chunk_assignment_sha256"] = digest(root / "ChunkAssign.dat")
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# Stock FH2 CubemapProbeDiffuse descriptor tail (anthem/homespace02): one streamed level,
# 32x32 cube, six mips, 0xC000 bytes, BC6H.
PROBE_DIFFUSE_DESCRIPTOR = bytes.fromhex(
    "00100000200020000600060000c00000ed008000000000600000000000000000"
    "0000000000000000000000000000000000000000")
PROBE_DIFFUSE_PAYLOAD = 0x53 + 0xC000


def resident_blobs(store, count):
    if store[:4] != b"burG":
        raise ValueError("Invalid resident texture store")
    version, stored, start, total = struct.unpack_from("<HHII", store, 4)
    if version != 1 or stored != count or total != len(store):
        raise ValueError("Resident texture store does not match the PVS texture table")
    blobs = []
    for index in range(count):
        tag, _, _, _, _, payload, size, unpacked = struct.unpack_from("<4sBBHIIII", store, 16 + 24 * index)
        if tag != b"xeTS" or size != unpacked or payload < start or payload + size > total:
            raise ValueError("Invalid resident STex entry %d" % index)
        blobs.append(store[payload:payload + size])
    return blobs


def stream_probe_diffuse(source, output):
    """Give FM5 resident-only diffuse probe cubemaps the streamed level FH2 requires.

    FM5 ships CubemapProbeDiffuse textures with QualityLevels=0 (resident blob only).
    FH2's presentation readiness (RVA 0x138CCF0) waits until both textures of the active
    cubemap probe are streaming; a zero-level texture never streams, so loading stalls.
    The resident 32x32 cube is byte-compatible with FH2's streamed diffuse probe payload,
    so it is added as level 1 and the descriptor is replaced with the stock FH2 record.
    """
    import re
    import xml.etree.ElementTree as ET
    import zipfile
    from fh2_pvs import read_root60
    from fh2_world import RESOURCE_TEXTURE

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    source_root = source / report["override_relative_path"]
    if (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Probe streaming requires single-chunk media")
    media_name = report["media_name"]
    pvs_relative = Path("ribbon_00") / (media_name + "_00.pvs")
    pvs = bytearray((source_root / pvs_relative).read_bytes())
    descriptors = read_root60(bytes(pvs))["textures"]
    with zipfile.ZipFile(source_root / "bin.zip") as archive:
        manifest = ET.fromstring(archive.read("AssetManifest.xml"))
        store = archive.read(media_name + ".stx.bin")
    probes = {item.attrib["Name"].upper() for item in manifest.findall("./Textures/Texture")
              if item.attrib.get("LightMapType") == "CubemapProbeDiffuse"}
    blobs = resident_blobs(store, len(descriptors))
    added = {}
    for index, descriptor in enumerate(descriptors):
        raw = bytes.fromhex(descriptor)
        texture_hash = struct.unpack_from("<I", raw)[0]
        if "_0X%08X.SWATCHBIN" % texture_hash not in probes or raw[4:8] != b"\x01\0\0\0":
            continue
        blob = blobs[index]
        if (len(blob) != PROBE_DIFFUSE_PAYLOAD or blob[0x10:0x14] != b"BCXT" or
                struct.unpack_from("<I", blob, 0x20)[0] != 0xC000):
            raise ValueError("Unexpected resident diffuse probe payload for texture %d" % index)
        offsets = [match.start() for match in re.finditer(re.escape(raw), pvs)]
        if len(offsets) != 1:
            raise ValueError("Diffuse probe descriptor is not unique in the PVS root")
        replacement = raw[:4] + PROBE_DIFFUSE_DESCRIPTOR
        if len(replacement) != 56:
            raise ValueError("Internal probe descriptor size mismatch")
        pvs[offsets[0]:offsets[0] + 56] = replacement
        added[(RESOURCE_TEXTURE << 24) | (index << 4)] = blob
    if not added:
        raise ValueError("No resident-only diffuse probe textures found")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("GeoChunk0.minizip"))
    root = output / report["override_relative_path"]
    (root / pvs_relative).write_bytes(pvs)
    keys = [key for (key,) in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    if set(added) & set(keys):
        raise ValueError("Diffuse probe streaming keys already exist")
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(archive.entries) != len(keys):
        raise ValueError("Chunk map/archive counts disagree: GeoChunk0")
    order = list(enumerate(keys))
    for key in sorted(added):
        # Model/texture keys form a sorted leading block; keep the new keys inside it.
        position = next((at for at, (_, existing) in enumerate(order)
                         if existing >> 24 == RESOURCE_TEXTURE and existing > key),
                        max(at for at, (_, existing) in enumerate(order) if existing >> 24 == RESOURCE_TEXTURE) + 1)
        order.insert(position, (None, key))
    resources = [Resource("%08x" % key, key & 0xFFFFFF, key >> 24) for _, key in order]
    sources = {resource.name: index for resource, (index, _) in zip(resources, order)}

    def read_resource(resource):
        index = sources[resource.name]
        return added[int(resource.name, 16)] if index is None else archive.read(index)

    lookup = write_minizip(root / "GeoChunk0.minizip", resources, read_resource)
    (root / "ChunkMap0.dat").write_bytes(lookup)
    rebuilt = MiniZip(root / "GeoChunk0.minizip")
    for position, (index, key) in enumerate(order):
        if index is None and rebuilt.read(position) != added[key]:
            raise ValueError("Rebuilt diffuse probe payload differs")
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(media_name, report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["streamed_diffuse_probes"] = sorted("%08x" % key for key in added)
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def zone_collision(source, output):
    """List every collision root in every PVSZone's collision request vector.

    FH2 builds the per-frame collision request list from the active zone's fifth vector
    (zone+0x60, RVA 0x141327E33): each entry indexes the root collision table (FB90), and
    0x14132D540 instantiates only requested roots. Without entries no ground exists.
    """
    from fh2_pvs import read_root60
    from fh2_world import RESOURCE_PVS_ZONE, read_pvs_zone60, write_pvs_zone60

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    source_root = source / report["override_relative_path"]
    if (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Zone collision requests require single-chunk media")
    media_name = report["media_name"]
    root = read_root60((source_root / "ribbon_00" / (media_name + "_00.pvs")).read_bytes())
    roots = list(range(len(root["root_groups"])))
    if not roots:
        raise ValueError("Root has no collision roots")
    keys = [key for (key,) in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(archive.entries) != len(keys):
        raise ValueError("Chunk map/archive counts disagree: GeoChunk0")
    zones = {}
    for index, key in enumerate(keys):
        if key >> 24 != RESOURCE_PVS_ZONE:
            continue
        zone = read_pvs_zone60(archive.read(index))
        if zone["auxiliary"]:
            raise ValueError("Zone %d already requests collision" % (key & 0xFFFFFF))
        zone["auxiliary"] = roots
        zones[index] = write_pvs_zone60(zone)
    if not zones:
        raise ValueError("No PVSZone resources found")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("GeoChunk0.minizip"))
    out_root = output / report["override_relative_path"]
    resources = [Resource("%d" % index, key & 0xFFFFFF, key >> 24) for index, key in enumerate(keys)]
    lookup = write_minizip(out_root / "GeoChunk0.minizip", resources,
                           lambda resource: zones.get(int(resource.name)) or archive.read(int(resource.name)))
    if lookup != (source_root / "ChunkMap0.dat").read_bytes():
        raise ValueError("Rewritten chunk map differs")
    rebuilt = MiniZip(out_root / "GeoChunk0.minizip")
    for index, data in zones.items():
        if rebuilt.read(index) != data:
            raise ValueError("Rewritten PVSZone differs")
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(media_name, report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["zone_collision_requests"] = {"zones": len(zones), "roots_per_zone": len(roots)}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def set_hud_road_type(source, output, road_type, fm5_axes=False):
    """Rebuild the ribbon_00 closed road with a HUD-drawable road_type; AI/traffic stay disabled.

    fm5_axes repairs navs built before the FM5 (x, z, height) -> FH2 (x, y, z) route conversion.
    """
    from openworld_navigation import (closed_road, fm5_route_positions, grid_covers, read_navw, read_owwf,
                                      validate_closed_road, write_navw)

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"]) / "ribbon_00"
    nav_path = source / relative / (report["media_name"] + "_00.nav")
    world = read_navw(nav_path.read_bytes())
    nodes = bytes.fromhex(world["tables"][0]["data"])
    positions = [list(struct.unpack_from("<3f", nodes, index * 40 + 20))
                 for index in range(world["counts"][0])]
    validate_closed_road(world, positions)
    oww = read_owwf((source / relative / (report["media_name"] + "_00.oww")).read_bytes())
    if oww["stamp"] != world["stamp"]:
        raise ValueError("NAVW/OWWF generation stamps disagree")
    if fm5_axes:
        heights = [p[2] for p in positions]
        if max(heights) - min(heights) > 50 or max(p[1] for p in positions) - min(p[1] for p in positions) < 100:
            raise ValueError("Navigation route is not in the unconverted FM5 (x, z, height) order")
        positions = fm5_route_positions(positions)
    if not grid_covers(oww, positions):
        raise ValueError("Open-world waypoint grid does not cover the navigation route")
    data = write_navw(closed_road(positions, world["stamp"], road_type))
    validate_closed_road(read_navw(data), positions, road_type)
    shutil.copytree(source, output)
    (output / relative / nav_path.name).write_bytes(data)
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["hud_road_type"] = {"road_type": road_type, "waypoints": len(positions), "fm5_axes_repaired": fm5_axes}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def _rewrite_shader_programs(source, output, transform, report_key, suffixes=(".pso", ".vso")):
    """Copy media, passing every bin.zip member with a matching suffix through transform(name, data) -> (data, stats)."""
    import os
    import tempfile
    import zipfile
    from pathlib import PureWindowsPath
    from build_media import verify_native_bin_zip, write_native_bin_zip

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    if report.get(report_key):
        raise ValueError("Media shaders already have " + report_key)
    source_bin = source / report["override_relative_path"] / "bin.zip"
    totals = collections.Counter()

    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    with tempfile.TemporaryDirectory(dir=output.parent) as staging:
        staging = Path(staging)
        with zipfile.ZipFile(source_bin) as archive:
            names = [row.filename for row in archive.infolist()]
            members = []
            for name in names:
                data = archive.read(name)
                if name.lower().endswith(suffixes):
                    patched, stats = transform(name, data)
                    if patched != data:
                        totals["programs"] += 1
                        totals.update(stats)
                    data = patched
                path = staging / "bin" / Path(PureWindowsPath(name))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                members.append((name, hashlib.sha256(data).hexdigest()))
        if not totals["programs"]:
            raise ValueError("No shader programs needed patching")
        write_native_bin_zip(staging / "bin", names, staging / "bin.zip")
        verify_native_bin_zip(staging / "bin.zip", members)
        shutil.copytree(source, output, copy_function=link_or_copy,
                        ignore=lambda folder, items: ["bin.zip"] if Path(folder) == source_bin.parent else [])
        shutil.move(str(staging / "bin.zip"), str(output / report["override_relative_path"] / "bin.zip"))
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.unlink()
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report[report_key] = dict(totals)
    (output / "media.json").unlink()
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def patch_shaders(source, output, baked_shadow_visibility=None):
    """Retarget the bin.zip FM5 shader programs to FH2's light-scenario constants (shader_patch)."""
    from shader_patch import DEFAULT_BAKED_SHADOW_VISIBILITY, patch_program
    if baked_shadow_visibility is None:
        baked_shadow_visibility = DEFAULT_BAKED_SHADOW_VISIBILITY

    def transform(name, data):
        lower = name.lower()
        return patch_program(
            data, "tracklightscenario" in lower or "cubemaplightscenario" in lower,
            baked_shadow_visibility)

    report = _rewrite_shader_programs(source, output, transform, "patched_shader_programs")
    report["baked_shadow_visibility"] = {
        "visibility": float(baked_shadow_visibility),
        "samples": report["patched_shader_programs"]["baked_shadow_samples"],
        "diagnostic": True,
        "in_game_validated": baked_shadow_visibility == DEFAULT_BAKED_SHADOW_VISIBILITY,
    }
    report_path = Path(output) / "media.json"
    report_path.unlink()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def scale_baked_shadow_fallback(source, output, fm5_bin_zip, visibility):
    """Scale t3 fallback light in an already-patched custom pack using original FM5 shaders."""
    import os
    import tempfile
    import zipfile
    from pathlib import PureWindowsPath
    from build_media import verify_native_bin_zip, write_native_bin_zip
    from shader_patch import scale_baked_shadow_fallback as scale_program

    if (isinstance(visibility, bool) or not isinstance(visibility, (int, float)) or
            not math.isfinite(visibility) or not 0 <= visibility <= 1):
        raise ValueError("Baked-shadow visibility must be finite and in [0, 1]")
    source, output = Path(source).resolve(), Path(output).resolve()
    fm5_bin_zip = Path(fm5_bin_zip).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    expected_samples = report.get("patched_shader_programs", {}).get("baked_shadow_samples")
    if not isinstance(expected_samples, int) or expected_samples <= 0:
        raise ValueError("Source media does not report patched baked-shadow samples")
    source_bin = source / report["override_relative_path"] / "bin.zip"
    totals = collections.Counter()

    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    with tempfile.TemporaryDirectory(dir=output.parent) as temporary:
        staging = Path(temporary)
        with zipfile.ZipFile(source_bin) as current, zipfile.ZipFile(fm5_bin_zip) as original:
            current_names = [row.filename for row in current.infolist()]
            original_names = set(original.namelist())
            members = []
            for name in current_names:
                data = current.read(name)
                lower = name.lower()
                if (name in original_names and lower.endswith(".pso") and
                        ("tracklightscenario" in lower or "cubemaplightscenario" in lower)):
                    patched, samples = scale_program(original.read(name), data, visibility)
                    if patched != data:
                        totals["programs"] += 1
                        totals["samples"] += samples
                    data = patched
                path = staging / "bin" / Path(PureWindowsPath(name))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
                members.append((name, hashlib.sha256(data).hexdigest()))
        if totals["samples"] != expected_samples:
            raise ValueError("Scaled baked-shadow samples differ from the patched media report")
        write_native_bin_zip(staging / "bin", current_names, staging / "bin.zip")
        verify_native_bin_zip(staging / "bin.zip", members)
        shutil.copytree(source, output, copy_function=link_or_copy,
                        ignore=lambda folder, items: ["bin.zip"] if Path(folder) == source_bin.parent else [])
        shutil.move(str(staging / "bin.zip"), str(output / report["override_relative_path"] / "bin.zip"))
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.unlink()
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["baked_shadow_visibility"] = {
        "visibility": float(visibility), "programs": totals["programs"],
        "samples": totals["samples"], "fm5_shader_archive_sha256": digest(fm5_bin_zip),
        "diagnostic": True, "in_game_validated": False,
    }
    report_path = output / "media.json"
    report_path.unlink()
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def bake_grass_fade(source, output, start, end):
    """Bake FM5 GrassFadeStart/EndDistance into programs reading trackParamsCBuffer (cb7) row 1.

    FH2 binds cb7 but only fills rows 2-3, so FM5 grass_blend layers fade with garbage
    distances. This stabilizes the fade input, not every possible depth/blending artifact.
    """
    from shader_patch import bake_constants

    if not 0 <= start < end:
        raise ValueError("Grass fade start must be below its end")

    def transform(name, data):
        if b"g_TrackParams" not in data:
            return data, {}
        patched, count = bake_constants(data, TRACK_PARAMS_SLOT, {1: (start, end, 0.0, 0.0)})
        return patched, {"operands": count}

    report = _rewrite_shader_programs(source, output, transform, "baked_grass_fade")
    return report


def _neutralize_macro_tint(source, output, families, report_key):
    from shader_patch import neutralize_texture_rgb

    def transform(name, data):
        stem = Path(name.replace("/", "\\")).name.lower()
        if not stem.endswith(".pso") or not any(
                stem.startswith(family + scenario)
                for family in families
                for scenario in ("tracklightscenario", "cubemaplightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = neutralize_texture_rgb(data, 16)
        if count != 1:
            raise ValueError("Expected one macro tint sample in " + name)
        return patched, {"rgb_samples": count}

    return _rewrite_shader_programs(source, output, transform, report_key)


def neutralize_macro_tint(source, output):
    """Disable incompatible FM5 grass/overlay RGB tinting, preserving texture blend alpha."""
    return _neutralize_macro_tint(
        source, output, ("grass_blend_002", "terrain_3blend_ch4overlay"),
        "neutralized_macro_tint")


def neutralize_terrain_macro_tint(source, output):
    """Remove the macro RGB tint causing black/red terrain strips beside Top Gear roads."""
    return _neutralize_macro_tint(
        source, output, ("terrain_3blend_macrodiffuse",), "neutralized_terrain_macro_tint")


def guard_marking_normals(source, output):
    """Guard the confirmed RoadLineDecal normal failure without replacing texture samples."""
    from shader_patch import guard_marking_normal

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                leaf.startswith("roadlinedecal" + scenario)
                for scenario in ("tracklightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = guard_marking_normal(data)
        return patched, {"normal_sqrt_guards": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "guarded_marking_normals")


TRACK_NORMAL_SHADERS = (
    "road_2blend_threshold_001", "road_basic", "road_basic_ch5multiply",
    "road_basic_nonorm_ch5overlay", "roadedge_003_withnorm", "roadedgedecal_basic",
    "terrain_3blend", "diffnormmaskgloss", "diffnormmaskgloss_2difftint_threshold",
    "diffnormmaskglossalpha", "tint2blend")


def guard_track_normals(source, output):
    """Guard normal-Z reconstruction in road, kerb and terrain pixel shaders (black/dark flicker)."""
    from shader_patch import guard_normal_reconstructions

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                leaf.startswith(shader + scenario)
                for shader in TRACK_NORMAL_SHADERS
                for scenario in ("tracklightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = guard_normal_reconstructions(data)
        return patched, {"track_normal_sqrt_guards": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "guarded_track_normals")


# Road/kerb/pad texture references whose FM5 hashes are absent from the FH2 root texture
# table; FH2 then samples whatever was bound last (camera-dependent dark/black surfaces).
MISSING_TRACK_TEXTURES = {
    0xDD286753: 0x9ACB43CD,  # blank_topg_swatches_mask -> resident white blank_swatches_mask
    0xB7541BC4: 0xA0A446A3,  # blank_topg_swatches_gloss -> blank_swatches_gloss
    0xE041742D: 0xE3400B44,  # blank_swatches_norm -> blank_normal_nrml
    0xEB095BA2: 0xE3400B44,  # road_topg_metal_001_norm -> blank_normal_nrml
    0x843D9F3D: 0x7AF29014,  # road_topg_roaddetail_001_norm -> same texture, FH2 GUID
}


def repoint_material_textures(data, root_hashes, mapping=MISSING_TRACK_TEXTURES):
    """Replace the u32 hash following each missing .swatchbin reference; returns (data, count)."""
    import re
    patched = bytearray(data)
    count = 0
    for match in re.finditer(rb"\.swatchbin(.{4})", data, re.S):
        old = struct.unpack("<I", match.group(1))[0]
        if old in root_hashes or old not in mapping:
            continue
        if mapping[old] not in root_hashes:
            raise ValueError("Replacement texture %08X is missing from the root table" % mapping[old])
        struct.pack_into("<I", patched, match.start(1), mapping[old])
        count += 1
    return bytes(patched), count


def repair_missing_material_textures(source, output):
    """Point road material references with no FH2 texture entry at resident neutral textures."""
    from fh2_pvs import read_root60

    report = json.loads((Path(source) / "media.json").read_text(encoding="utf-8"))
    folder = Path(source) / report["override_relative_path"]
    root = read_root60((folder / "ribbon_00" / (report["media_name"] + "_00.pvs")).read_bytes())
    root_hashes = {struct.unpack_from("<I", bytes.fromhex(row))[0] for row in root["textures"]}

    def transform(name, data):
        patched, count = repoint_material_textures(data, root_hashes)
        return patched, {"texture_references": count} if count else {}

    return _rewrite_shader_programs(
        source, output, transform, "repaired_missing_material_textures", (".materialbin",))


def bind_material_textures(source, output, targets=None, report_key="bound_material_textures"):
    """Add root textures referenced by a model's materials to that model's group texture list.

    FH2 binds a draw's textures from its group list; a material texture outside the list is left
    unbound and samples whatever the previous draw bound (camera-dependent black/dark surfaces).
    """
    import os
    import re
    import zipfile
    from fh2_pvs import read_root60, write_root60

    if targets is None:
        targets = set(MISSING_TRACK_TEXTURES.values())
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    if report.get(report_key):
        raise ValueError("Media already has " + report_key)
    relative = Path(report["override_relative_path"])
    folder = source / relative
    root_path = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    original = (folder / root_path).read_bytes()
    world, before = read_root60(original), read_root60(original)
    texture_index = {struct.unpack_from("<I", bytes.fromhex(row))[0]: index
                     for index, row in enumerate(world["textures"])}
    if targets == "all":
        targets = set(texture_index)
    with zipfile.ZipFile(folder / "bin.zip") as archive:
        members = {name.lower(): name for name in archive.namelist()}
        manifest = archive.read("AssetManifest.xml").decode("utf-8-sig")
        material_hashes = {}
        for match in re.finditer(r'<Material Name="([^"]+)" Source="([^"]+)"', manifest):
            leaf = match[2].lower().rsplit("\\", 1)[-1].rsplit(".", 1)[0]
            data = archive.read(members[match[1].replace("\\", "/").lower()])
            hashes = {struct.unpack("<I", row[1])[0]
                      for row in re.finditer(rb"\.swatchbin(.{4})", data, re.S)}
            material_hashes.setdefault(leaf, set()).update(hashes)
    keys = [key for key, in struct.iter_unpack("<I", (folder / "ChunkMap0.dat").read_bytes())]
    lookup = {key: index for index, key in enumerate(keys)}
    archive = MiniZip(folder / "GeoChunk0.minizip")
    added = {}
    for model, group in enumerate(world["groups"]):
        if (0x04000000 | model) not in lookup:
            continue
        data = archive.read(lookup[0x04000000 | model])
        needed = set()
        for offset in range(16, 16 + struct.unpack_from("<H", data, 6)[0] * 24, 24):
            if data[offset:offset + 4] != b"ItaM":
                continue
            payload, size = struct.unpack_from("<2I", data, offset + 12)
            name = re.search(rb"([\w\-]+)\.materialbin", data[payload:payload + size])
            if not name:
                raise ValueError("Model %d material has no materialbin path" % model)
            leaf = name[1].decode().lower()
            if leaf not in material_hashes:
                raise ValueError("Model %d material %s is missing from the manifest" % (model, leaf))
            needed.update(texture_index[value] for value in material_hashes[leaf] & targets
                          if value in texture_index)
        missing = sorted(needed - set(group["textures"]))
        if missing:
            group["textures"] = list(group["textures"]) + missing
            added[model] = missing
    if not added:
        raise ValueError("No model group needs material texture bindings")
    patched = write_root60(world)
    parsed = read_root60(patched)
    for model, group in enumerate(parsed["groups"]):
        expected = list(before["groups"][model]["textures"]) + added.get(model, [])
        if group["textures"] != expected:
            raise ValueError("Group texture binding round-trip mismatch")
        group["textures"] = before["groups"][model]["textures"]
    if parsed != before:
        raise ValueError("Group texture binding changed unrelated root fields")

    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    shutil.copytree(source, output, copy_function=link_or_copy)
    for path in (output / relative / root_path, output / report["catalog_name"], output / "media.json"):
        path.unlink()
    (output / relative / root_path).write_bytes(patched)
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report[report_key] = {
        "models": len(added), "bindings": sum(map(len, added.values())),
        "textures": sorted({index for values in added.values() for index in values})}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


# Diagnostic flat colours per road/decal shader family (identify which shader draws a surface).
DIAGNOSTIC_SHADER_COLORS = {
    "road_basic": (1.0, 0.0, 1.0),                    # magenta
    "road_basic_ch5multiply": (1.0, 0.5, 0.0),        # orange
    "road_basic_nonorm_ch5overlay": (0.0, 0.3, 1.0),  # blue
    "road_2blend_threshold_001": (0.0, 1.0, 0.0),     # green
    "roadedge_003": (0.5, 0.25, 0.0),                 # brown
    "roadedge_003_withnorm": (0.5, 0.0, 1.0),         # purple
    "roadedgedecal_basic": (0.0, 1.0, 1.0),           # cyan
    "roadlinedecal": (1.0, 1.0, 0.0),                 # yellow
}


# Extended set: every lit track family except sky, characters, glass and car paint.
DIAGNOSTIC_ALL_SHADER_COLORS = {
    **DIAGNOSTIC_SHADER_COLORS,
    "terrain_3blend_macrodiffuse": (1.0, 0.0, 0.0),             # red
    "terrain_3blend_ch4overlay": (1.0, 0.6, 0.7),               # pink
    "terrain_3blend": (0.0, 0.4, 0.0),                          # dark green
    "diffnormmaskgloss": (1.0, 1.0, 1.0),                       # white
    "diffnormmaskglossalpha": (0.5, 0.5, 0.5),                  # grey
    "difftintnormglossmask": (0.0, 0.0, 0.45),                  # navy
    "tint2blend": (0.6, 1.0, 0.3),                              # lime
    "grass_blend": (0.0, 0.5, 0.5),                             # teal
    "grass_blend_002": (0.0, 0.5, 0.5),                         # teal
    "diffnormmaskgloss_2difftint_threshold": (0.5, 0.8, 1.0),   # light blue
    "difftintnormglossmaskao": (0.5, 0.8, 1.0),                 # light blue
    "diffch2tintnormglossmaskao": (0.5, 0.8, 1.0),              # light blue
}


def diagnostic_shader_colors(source, output, colors=None):
    """Diagnostic build: every road/decal pixel shader family outputs its own flat colour."""
    from shader_patch import diagnostic_output_color
    colors = DIAGNOSTIC_SHADER_COLORS if colors is None else colors

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso"):
            return data, {}
        for shader, rgb in colors.items():
            if any(leaf.startswith(shader + scenario)
                   for scenario in ("tracklightscenario", "debuglightinglightscenario")):
                patched, count = diagnostic_output_color(data, rgb)
                return patched, {"output_writes": count} if count else {}
        return data, {}

    return _rewrite_shader_programs(source, output, transform, "diagnostic_shader_colors")


def diagnostic_white_diffuse(source, output):
    """Diagnostic build: road/decal diffuse texture RGB becomes white; lighting and alpha kept."""
    from shader_patch import neutralize_texture_rgb

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                leaf.startswith(shader + scenario)
                for shader in ("road_basic", "roadedgedecal_basic", "roadlinedecal")
                for scenario in ("tracklightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = neutralize_texture_rgb(data, 17)
        return patched, {"diffuse_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "diagnostic_white_diffuse")


def diagnostic_nan_output(source, output):
    """Diagnostic build: every track pixel whose lit colour is NaN is drawn bright green."""
    from shader_patch import diagnostic_nan_output as patch

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                scenario in leaf for scenario in ("tracklightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = patch(data)
        return patched, {"output_writes": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "diagnostic_nan_output")


def separate_grass_depth(source, output):
    """Raise the two static FM5 grass render layers by 5 cm, leaving physics unchanged."""
    from shader_patch import offset_world_height

    def transform(name, data):
        lower = name.lower()
        if not any(lower.endswith(family + "tracklightscenario." + platform + ".vso")
                   for family in ("grass_blend", "grass_blend_002")
                   for platform in ("pc", "durango")):
            return data, {}
        patched, count = offset_world_height(data, 0.05)
        if count != 1:
            raise ValueError("Expected one grass world projection in " + name)
        return patched, {"world_projections": count}

    return _rewrite_shader_programs(source, output, transform, "separated_grass_depth")


def neutralize_legacy_ao(source, output):
    """Opt-in diagnostic for FM5's unsupported t6 baked ambient-occlusion input."""
    from shader_patch import neutralize_legacy_ao as patch_ao

    def transform(name, data):
        lower = name.lower()
        if not lower.endswith(".pso") or not any(
                scenario + "." in lower for scenario in
                ("tracklightscenario", "cubemaplightscenario", "debuglightinglightscenario")):
            return data, {}
        patched, count = patch_ao(data)
        return patched, {"ao_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "neutralized_legacy_ao")


def patch_instancing_poses(source, output):
    """Repair the FM5/FH2 instanced pose stride, including compiled instruction extensions."""
    from shader_patch import patch_pose_stride

    def transform(name, data):
        if not name.lower().endswith(".vso"):
            return data, {}
        return patch_pose_stride(data)

    return _rewrite_shader_programs(source, output, transform, "patched_instancing_poses")


def neutralize_pavement_skid(source, output):
    """Apply the confirmed white skid-mask workaround to every pavement shader."""
    from shader_patch import neutralize_skid_mask

    def transform(name, data):
        if not name.lower().endswith(".pso") or b"SkidmaskTextureTexture\0" not in data:
            return data, {}
        patched, count = neutralize_skid_mask(data)
        return patched, {"skid_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "neutralized_pavement_skid")


def neutralize_blended_pavement_ao(source, output):
    """Apply the aircraft-apron AO-mask workaround without changing its dirt blend."""
    from shader_patch import neutralize_pavement_ao_mask

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if (not leaf.startswith("road_2blend_threshold_001") or not leaf.endswith(".pso")
                or b"AOMaskTextureTexture\0" not in data):
            return data, {}
        patched, count = neutralize_pavement_ao_mask(data)
        return patched, {"ao_mask_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "neutralized_blended_pavement_ao")


ROAD_AO_SHADERS = ("road_basic", "road_basic_ch5multiply", "road_basic_nonorm_ch5overlay",
                   "roadedgedecal_basic")


def neutralize_road_ao(source, output, report_key="neutralized_road_ao",
                       resource_names=(b"AOMaskTextureTexture",), shaders=ROAD_AO_SHADERS):
    """Force the AO-mask red channel to 1 in basic road/edge-decal shaders (close-range black)."""
    from shader_patch import neutralize_ao_mask_channel

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if (not leaf.endswith(".pso") or not any(n + b"\0" in data for n in resource_names)
                or not any(leaf.startswith(shader + scenario) for shader in shaders
                           for scenario in ("tracklightscenario", "cubemaplightscenario",
                                            "debuglightinglightscenario"))):
            return data, {}
        patched, count = neutralize_ao_mask_channel(data, resource_names)
        return patched, {"ao_mask_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, report_key)


def neutralize_edge_decal_pack_ao(source, output):
    """Force the edge decal's Pack_AO_Mask AO (red) to 1; its green channel stays the decal alpha."""
    return neutralize_road_ao(source, output, "neutralized_edge_decal_pack_ao",
                              (b"Pack_AO_MaskTexture",), ("roadedgedecal_basic",))


def diagnostic_full_sun(source, output, shaders=("road_basic", "roadlinedecal",
                                                 "roadedgedecal_basic")):
    """DIAGNOSTIC: the 0.15 baked-shadow fallback becomes 1.0 in the given families only."""
    from shader_patch import DEFAULT_BAKED_SHADOW_VISIBILITY, set_uniform_mov_literal

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                leaf.startswith(shader + scenario) for shader in shaders
                for scenario in ("tracklightscenario", "cubemaplightscenario")):
            return data, {}
        patched, count = set_uniform_mov_literal(data, DEFAULT_BAKED_SHADOW_VISIBILITY, 1.0)
        return patched, {"fallback_movs": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "diagnostic_full_sun")


def neutral_sun(source, output):
    """Neutral-grey sun colour (cb3[1] -> .yyy) in lit/cubemap track pixel shaders only."""
    from shader_patch import neutral_sun_colour

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                s in leaf for s in ("tracklightscenario", "cubemaplightscenario")):
            return data, {}
        patched, count = neutral_sun_colour(data)
        return patched, {"sun_colour_reads": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "neutral_sun")


def diagnostic_flat_normals(source, output):
    """DIAGNOSTIC: flat tangent-space normals in lit track shaders (close-range normal decode)."""
    from shader_patch import flatten_normal_samples

    def transform(name, data):
        leaf = name.replace("\\", "/").rsplit("/", 1)[-1].lower()
        if not leaf.endswith(".pso") or not any(
                s in leaf for s in ("tracklightscenario", "cubemaplightscenario",
                                    "debuglightinglightscenario")):
            return data, {}
        patched, count = flatten_normal_samples(data)
        return patched, {"normal_samples": count} if count else {}

    return _rewrite_shader_programs(source, output, transform, "diagnostic_flat_normals")


def realign_baked_shadow_fallback(source, output, fm5_bin_zip, visibility=None):
    """Move mis-targeted 0.15 baked-shadow fallbacks back from skid-mask MOVs to the t3 MOVs."""
    import zipfile
    from shader_patch import DEFAULT_BAKED_SHADOW_VISIBILITY
    from shader_patch import realign_baked_shadow_fallback as realign_program
    if visibility is None:
        visibility = DEFAULT_BAKED_SHADOW_VISIBILITY
    with zipfile.ZipFile(Path(fm5_bin_zip).resolve()) as original:
        names = set(original.namelist())

        def transform(name, data):
            lower = name.lower()
            if (name not in names or not lower.endswith(".pso") or
                    ("tracklightscenario" not in lower and "cubemaplightscenario" not in lower)):
                return data, {}
            try:
                patched, counts = realign_program(original.read(name), data, visibility)
            except ValueError:
                return data, {}
            return patched, {key: value for key, value in counts.items() if value}

        return _rewrite_shader_programs(source, output, transform, "realigned_baked_shadow_fallback")


def repair_draw_lods(source, output):
    """Populate FH2 draw LOD offsets from the source-backed model group references."""
    from fh2_pvs import read_root60
    from fh2_world import draw_lod_metadata

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"]) / "ribbon_00" / (report["media_name"] + "_00.pvs")
    original = (source / relative).read_bytes()
    pvs = bytearray(original)
    root = read_root60(original)
    changed = 0
    expected = []
    for draw in root["primary_draws"]:
        metadata = draw_lod_metadata(draw["model_id"], root["groups"])
        expected.append(metadata)
        if draw["metadata"] == metadata:
            continue
        if draw["metadata"] != "7f7f7f7f":
            raise ValueError("Draw already has non-default LOD offsets")
        record = bytes.fromhex(draw["source_record"] + draw["metadata"])
        offsets = [match.start() for match in re.finditer(re.escape(record), original)]
        if len(offsets) != 1:
            raise ValueError("Draw record is not unique in the PVS root")
        at = offsets[0] + 73
        pvs[at:at + 4] = bytes.fromhex(metadata)
        changed += 1
    if not changed:
        raise ValueError("No draw needs LOD offset repair")
    if [draw["metadata"] for draw in read_root60(bytes(pvs))["primary_draws"]] != expected:
        raise ValueError("Rewritten PVS root lost LOD offsets")
    shutil.copytree(source, output)
    (output / relative).write_bytes(pvs)
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["repaired_draw_lods"] = {"draws": changed}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def remap_surfaces(source, output, source_surfaces, target_surfaces):
    """Rewrite the surface byte of every streamed physics template in single-chunk media.

    The second triangle attribute byte indexes the game's surfaceTypes.xml list. FM5 and FH2
    lists differ (FM5 119 AsphaltTrackPenalty is FH2 119 Snow), which made parts of the track
    drive like ice. Indices are remapped by surface name (collision.surface_remap).
    """
    from collision import read_physics, remap_triangle_surfaces, surface_remap, write_physics

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    if report.get("remapped_surfaces"):
        raise ValueError("Media surfaces are already remapped")
    source_root = source / report["override_relative_path"]
    if (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Surface remapping requires single-chunk media")
    mapping = surface_remap(source_surfaces, target_surfaces)
    keys = [key for (key,) in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(archive.entries) != len(keys):
        raise ValueError("Chunk map/archive counts disagree: GeoChunk0")
    templates, changed = {}, 0
    for index, key in enumerate(keys):
        if key >> 24 != 1:
            continue
        template = read_physics(archive.read(index))
        triangles = remap_triangle_surfaces(template["triangles"], mapping)
        changed += sum(a[1] != b[1] for a, b in zip(template["triangles"], triangles))
        template["triangles"] = triangles
        templates[index] = write_physics(template)
    if not templates:
        raise ValueError("No physics templates found")
    shutil.copytree(source, output, ignore=shutil.ignore_patterns("GeoChunk0.minizip"))
    out_root = output / report["override_relative_path"]
    resources = [Resource("%d" % index, key & 0xFFFFFF, key >> 24) for index, key in enumerate(keys)]
    lookup = write_minizip(out_root / "GeoChunk0.minizip", resources,
                           lambda resource: templates.get(int(resource.name)) or archive.read(int(resource.name)))
    if lookup != (source_root / "ChunkMap0.dat").read_bytes():
        raise ValueError("Rewritten chunk map differs")
    rebuilt = MiniZip(out_root / "GeoChunk0.minizip")
    for index, data in templates.items():
        if rebuilt.read(index) != data:
            raise ValueError("Rewritten physics template differs")
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["remapped_surfaces"] = {"templates": len(templates), "triangles_changed": changed}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def bake_default_draw_distances(source, output, distances):
    """Replace only all-minus-one LOD bands, whose FH2 fallback culls FM5 props too closely."""
    from fh2_pvs import read_root60

    if (len(distances) != 4 or not 0 < distances[0] < distances[1] < distances[2] < distances[3] < 65504):
        raise ValueError("Default draw distances must be four increasing positive half-float values")
    bands = struct.pack("<4e", *distances)
    distances = list(struct.unpack("<4e", bands))
    if not all(a < b for a, b in zip(distances, distances[1:])):
        raise ValueError("Default draw distances collapse at half-float precision")
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"]) / "ribbon_00" / (report["media_name"] + "_00.pvs")
    original = (source / relative).read_bytes()
    pvs = bytearray(original)
    changed = 0
    expected = []
    for draw in read_root60(original)["primary_draws"]:
        raw = bytes.fromhex(draw["source_record"])
        previous = struct.unpack_from("<4e", raw, 18)
        if previous != (-1, -1, -1, -1):
            expected.append(previous)
            continue
        offsets = [match.start() for match in re.finditer(re.escape(raw), original)]
        if len(offsets) != 1:
            raise ValueError("Draw record is not unique in the PVS root")
        at = offsets[0] + 18
        pvs[at:at + 8] = bands
        expected.append(tuple(distances))
        changed += 1
    if not changed:
        raise ValueError("No draw needs default distance baking")
    if [struct.unpack_from("<4e", bytes.fromhex(draw["source_record"]), 18)
            for draw in read_root60(bytes(pvs))["primary_draws"]] != expected:
        raise ValueError("Rewritten PVS root lost default draw distances")
    shutil.copytree(source, output)
    (output / relative).write_bytes(pvs)
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["baked_default_draw_distances"] = {"bands": distances, "draws": changed}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def extend_draw_distances(source, output, minimum):
    """Raise short final LOD-band distances of root draws to at least ``minimum`` metres.

    Draw records hold four half-float band end distances (record+18); the last positive
    one is the cull distance. FM5 props such as tyre stacks and barriers use 0,0,0,25,
    which FH2 honours literally, so they pop in right next to the car.
    """
    from fh2_pvs import read_root60

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    if not 0 < minimum < 65504:
        raise ValueError("Minimum draw distance must be between 0 and 65504")
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    pvs_relative = Path(report["override_relative_path"]) / "ribbon_00" / (report["media_name"] + "_00.pvs")
    pvs = bytearray((source / pvs_relative).read_bytes())
    changed = 0
    for draw in read_root60(bytes(pvs))["primary_draws"]:
        raw = bytes.fromhex(draw["source_record"])
        distances = struct.unpack_from("<4e", raw, 18)
        if not 0 < distances[3] < minimum:
            continue
        offsets = [match.start() for match in re.finditer(re.escape(raw), pvs)]
        if len(offsets) != 1:
            raise ValueError("Draw record is not unique in the PVS root")
        struct.pack_into("<e", pvs, offsets[0] + 24, minimum)
        changed += 1
    if not changed:
        raise ValueError("No draw needs a longer cull distance")
    shutil.copytree(source, output)
    (output / pvs_relative).write_bytes(pvs)
    draws = read_root60(bytes(pvs))["primary_draws"]
    if sum(struct.unpack_from("<e", bytes.fromhex(d["source_record"]), 24)[0] == minimum for d in draws) < changed:
        raise ValueError("Rewritten PVS root lost draw distance changes")
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["extended_draw_distances"] = {"minimum": minimum, "draws": changed}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def repair_topgear_markings(source, output, include_runway=False):
    """Use the verified FH2 road draw class for FM5-only Top Gear marking models."""
    import os
    from build_geometry import validate_container
    from fh2_pvs import read_root60, write_root60

    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    folder = source / relative
    root_path = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    original = (folder / root_path).read_bytes()
    world = read_root60(original)
    before = read_root60(original)
    keys = [key for key, in struct.iter_unpack("<I", (folder / "ChunkMap0.dat").read_bytes())]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate marking resource key")
    archive = MiniZip(folder / "GeoChunk0.minizip")
    if len(keys) != len(archive.entries):
        raise ValueError("Marking chunk map/archive counts disagree")
    lookup = {key: index for index, key in enumerate(keys)}
    candidates = {
        draw["model_id"] for draw in world["primary_draws"] + world["secondary_draws"]
        if struct.unpack_from("<Q", bytes.fromhex(draw["source_record"]), 4)[0] == 0x1FFF1214}
    models = set()
    for model in sorted(candidates):
        key = 0x04000000 | model
        if key not in lookup:
            raise ValueError("Marking candidate model is missing from chunk zero")
        data = archive.read(lookup[key])
        validate_container(data)
        materials = []
        for offset in range(16, 16 + struct.unpack_from("<H", data, 6)[0] * 24, 24):
            if data[offset:offset + 4] == b"ItaM":
                payload, size = struct.unpack_from("<2I", data, offset + 12)
                materials.append(data[payload:payload + size])
        marking_materials = [b"\\roadlines_topg_" in material.lower() for material in materials]
        companions = (
            b"topg_runwaygrates", b"topg_reflactor_001", b"topg_harrierlandinglights_001",
            b"topg_harrierlandinglights_002", b"topg_harrierpane")
        allowed = [
            marking or (include_runway and any(
                b"\\materials\\" + name + b".materialbin" in material.lower()
                for name in companions))
            for marking, material in zip(marking_materials, materials)]
        if materials and any(marking_materials) and all(allowed):
            models.add(model)
    changed = 0
    for draw in world["primary_draws"] + world["secondary_draws"]:
        raw = bytearray.fromhex(draw["source_record"])
        if draw["model_id"] in models and struct.unpack_from("<Q", raw, 4)[0] == 0x1FFF1214:
            struct.pack_into("<Q", raw, 4, 0x0FFF1024)
            draw["source_record"] = raw.hex()
            changed += 1
    if not changed:
        raise ValueError("No Top Gear marking draw needs repair")
    patched = write_root60(world)
    parsed = read_root60(patched)
    for key in ("primary_draws", "secondary_draws"):
        for old, new in zip(before[key], parsed[key]):
            if old["source_record"] != new["source_record"]:
                expected = bytearray.fromhex(old["source_record"])
                struct.pack_into("<Q", expected, 4, 0x0FFF1024)
                if new["model_id"] not in models or new["source_record"] != expected.hex():
                    raise ValueError("Marking repair changed unrelated draw bytes")
                new["source_record"] = old["source_record"]
    if parsed != before:
        raise ValueError("Marking repair changed unrelated root fields")

    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    shutil.copytree(source, output, copy_function=link_or_copy)
    for path in (output / relative / root_path, output / report["catalog_name"], output / "media.json"):
        path.unlink()
    (output / relative / root_path).write_bytes(patched)
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report_key = "repaired_topgear_runway_markings" if include_runway else "repaired_topgear_markings"
    report[report_key] = {"draws": changed, "models": sorted(models)}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def map_wide_visibility(source, output, minimum):
    """Keep the map's draw/model/texture closure requested in every zone."""
    import math
    import os
    from fh2_pvs import read_root60, write_root60
    from fh2_world import read_pvs_zone60, write_pvs_zone60

    if not math.isfinite(minimum) or not 0 < minimum < 65504:
        raise ValueError("Map-wide distance must be finite and between 0 and 65504")
    minimum = struct.unpack("<e", struct.pack("<e", minimum))[0]
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError("Choose a new media output directory: " + str(output))
    report = json.loads((source / "media.json").read_text(encoding="utf-8"))
    if digest(source / report["catalog_name"]) != report["catalog_sha256"]:
        raise ValueError("Source media catalog checksum mismatch")
    relative = Path(report["override_relative_path"])
    root_path = Path("ribbon_00") / (report["media_name"] + "_00.pvs")
    source_root = source / relative
    if (source_root / "ChunkMap1.dat").exists():
        raise ValueError("Map-wide visibility requires single-chunk media")
    root = read_root60((source_root / root_path).read_bytes())
    if root["secondary_draws"]:
        raise ValueError("Map-wide visibility requires promoted primary draws")
    assignments = (source_root / "ChunkAssign.dat").read_bytes()
    if assignments != struct.pack("<2I", 100, root["zone_count"]) + bytes(4 * root["zone_count"]):
        raise ValueError("Map-wide visibility requires all zones assigned to chunk zero")
    keys = [key for (key,) in struct.iter_unpack("<I", (source_root / "ChunkMap0.dat").read_bytes())]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate map-wide resource key")
    archive = MiniZip(source_root / "GeoChunk0.minizip")
    if len(keys) != len(archive.entries):
        raise ValueError("Chunk map/archive counts disagree")
    zones = {key: read_pvs_zone60(archive.read(index))
             for index, key in enumerate(keys) if key >> 24 == 12}
    if set(zones) != {0x0C000000 | i for i in range(root["zone_count"])} or not zones:
        raise ValueError("Map-wide visibility requires every native zone")
    draws, models, textures = set(), set(), {}
    for zone in zones.values():
        draws.update(zone["draws"])
        models.update(zone["models"])
        for index, quality in zip(zone["textures"], zone["qualities"]):
            if not 0 <= index < len(root["textures"]) or quality & 255 != 1:
                raise ValueError("Invalid map-wide texture request")
            textures[index] = max(textures.get(index, 1), quality)
    if draws != set(range(len(root["primary_draws"]))):
        raise ValueError("Map-wide source visibility omits a draw")
    available = set(keys)
    for draw in root["primary_draws"]:
        raw = bytearray.fromhex(draw["source_record"])
        model = struct.unpack_from("<I", raw)[0]
        if not 0 <= model < len(root["groups"]):
            raise ValueError("Map-wide draw references an invalid model group")
        models.add(model)
        models.update(ref for ref in root["groups"][model]["references"] if ref != 0xFFFFFFFF)
        distances = struct.unpack_from("<4e", raw, 18)
        if not all(math.isfinite(value) for value in distances):
            raise ValueError("Non-finite source draw distance")
        struct.pack_into("<e", raw, 24, max(minimum, *distances))
        draw["source_record"] = raw.hex()
    if any((0x04000000 | model) not in available for model in models):
        raise ValueError("Map-wide visibility references a missing model")
    required_textures = set()
    for model in models:
        if not 0 <= model < len(root["groups"]):
            raise ValueError("Map-wide visibility references an invalid LOD group")
        required_textures.update(root["groups"][model]["textures"])
    for instance in root["instances"]:
        for sets in instance["texture_slot_sets"]:
            for slots in sets:
                required_textures.update(slot[1] for slot in slots)
        for override in instance["material_overrides"]:
            required_textures.update(index for _, index in override["texture_hash_index_pairs"])
    for index in required_textures:
        if not 0 <= index < len(root["textures"]):
            raise ValueError("Map-wide binding references an invalid texture")
        textures.setdefault(index, 1)
    for index, quality in textures.items():
        levels = [level + 1 for level in range(16)
                  if (0x05000000 | (index << 4) | level) in available]
        if quality >> 8 and quality >> 8 not in levels:
            raise ValueError("Map-wide visibility references a missing texture level")
        textures[index] = 1 | (max(levels, default=0) << 8)
    updated = {}
    for key, zone in zones.items():
        zone.update(draws=sorted(draws), models=sorted(models), textures=sorted(textures),
                    qualities=[textures[index] for index in sorted(textures)])
        updated[key] = write_pvs_zone60(zone)
    root["zone_maxima"] = [len(textures), len(models), len(draws)]
    root_data = write_root60(root)
    if any(struct.unpack_from("<e", bytes.fromhex(draw["source_record"]), 24)[0] < minimum
           for draw in read_root60(root_data)["primary_draws"]):
        raise ValueError("Map-wide draw-distance verification failed")

    def link_or_copy(src, dst):
        try:
            os.link(src, dst)
        except OSError:
            shutil.copy2(src, dst)

    shutil.copytree(source, output, copy_function=link_or_copy,
                    ignore=lambda folder, names: ["GeoChunk0.minizip"]
                    if Path(folder) == source_root else [])
    target = output / relative
    for path in (target / root_path, output / report["catalog_name"], output / "media.json"):
        path.unlink()
    (target / root_path).write_bytes(root_data)
    resources = [Resource(str(index), key & 0xFFFFFF, key >> 24) for index, key in enumerate(keys)]
    lookup = write_minizip(target / "GeoChunk0.minizip", resources,
                           lambda resource: updated[keys[int(resource.name)]]
                           if keys[int(resource.name)] in updated else archive.read(int(resource.name)))
    if lookup != (source_root / "ChunkMap0.dat").read_bytes():
        raise ValueError("Map-wide chunk lookup changed")
    rebuilt = MiniZip(target / "GeoChunk0.minizip")
    for index, key in enumerate(keys):
        if rebuilt.read(index) != (updated[key] if key in updated else archive.read(index)):
            raise ValueError("Map-wide resource roundtrip differs")
    for row in report["files"]:
        path = output / row["path"]
        row["bytes"], row["sha256"] = path.stat().st_size, digest(path)
    catalog = output / report["catalog_name"]
    catalog.write_bytes(media_catalog(report["media_name"], report["files"]))
    report["catalog_sha256"] = digest(catalog)
    report["map_wide_visibility"] = {
        "minimum_cull_metres": minimum, "draws": len(draws), "models": len(models),
        "textures": len(textures), "zones": len(zones), "highest_available_texture_levels": True}
    (output / "media.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--single-chunk", action="store_true",
                        help="merge every resource into GeoChunk0 and assign all zones to chunk 0")
    parser.add_argument("--stream-probe-diffuse", action="store_true",
                        help="post-process single-chunk media so diffuse probe cubemaps stream")
    parser.add_argument("--zone-collision", action="store_true",
                        help="post-process single-chunk media so every zone requests all collision roots")
    parser.add_argument("--patch-shaders", action="store_true",
                        help="post-process media so FM5 shader programs use FH2 light-scenario constants")
    parser.add_argument("--baked-shadow-visibility", type=float, default=None,
                        help="fallback visibility for FM5 Direct0 samples during --patch-shaders (default: 0.15)")
    parser.add_argument("--scale-baked-shadow-fallback", type=float, metavar="VISIBILITY",
                        help="scale baked-shadow fallbacks in already-patched media using --fm5-bin-zip")
    parser.add_argument("--fm5-bin-zip", type=Path,
                        help="original FM5 Top Gear bin.zip required by --scale-baked-shadow-fallback")
    parser.add_argument("--remap-surfaces", nargs=2, metavar=("SOURCE_XML", "TARGET_XML"),
                        help="post-process single-chunk media: remap physics surface indices by name "
                             "from the source game's surfaceTypes.xml to FH2's")
    parser.add_argument("--min-draw-distance", type=float, metavar="METRES",
                        help="post-process media: raise short final draw cull distances to METRES")
    parser.add_argument("--bake-grass-fade", nargs=2, type=float, metavar=("START", "END"),
                        help="post-process media: bake the source TrackSettings GrassFadeStart/EndDistance "
                             "into shaders reading trackParamsCBuffer row 1")
    parser.add_argument("--neutralize-macro-tint", action="store_true",
                        help="experimental compatibility option: neutralize macro RGB tint in "
                             "grass_blend_002 and terrain_3blend_ch4overlay, preserving blend alpha")
    parser.add_argument("--neutralize-legacy-ao", action="store_true",
                        help="experimental diagnostic: replace FM5 baked AO samples at unbound t6 "
                             "with full ambient visibility; preserves diffuse and specular textures")
    parser.add_argument("--neutralize-pavement-skid", action="store_true",
                        help="replace named pavement skid-mask samples with white on all shader "
                             "families; preserves base textures and overlays but removes baked skids")
    parser.add_argument("--neutralize-terrain-macro-tint", action="store_true",
                        help="neutralize the macro RGB tint causing black/red terrain strips; "
                             "preserves base diffuse textures, blend masks and alpha")
    parser.add_argument("--neutralize-blended-pavement-ao", action="store_true",
                        help="replace blended-road material AO masks with white; preserves the "
                             "diffuse textures and threshold/dirt blend")
    parser.add_argument("--separate-grass-depth", action="store_true",
                        help="raise the static grass render layers by 5 cm to avoid ground overlap")
    parser.add_argument("--repair-draw-lods", action="store_true",
                        help="populate unresolved FH2 draw LOD offsets from model group references")
    parser.add_argument("--repair-topgear-markings", action="store_true",
                        help="repair FM5 Top Gear marking draw classification; preserves shaders, "
                             "textures, heights, physics and all other draws")
    parser.add_argument("--repair-topgear-runway-markings", action="store_true",
                        help="also repair marking models mixed with audited Top Gear runway "
                             "grates, reflectors and landing lights; preserves other mixed models")
    parser.add_argument("--guard-marking-normals", action="store_true",
                        help="prevent invalid RoadLineDecal normal reconstruction causing X/arrow "
                             "flicker; preserves original texture samples, alpha and height")
    parser.add_argument("--guard-track-normals", action="store_true",
                        help="prevent invalid road/kerb/terrain normal reconstruction causing "
                             "camera-dependent black or dark surfaces; preserves textures")
    parser.add_argument("--realign-baked-shadow-fallback", metavar="FM5_BIN_ZIP",
                        help="repair road decal shaders where the 0.15 fallback hit the skid "
                             "mask instead of the t3 shadow sample (dark kerb/stripe paint)")
    parser.add_argument("--diagnostic-all-shader-colors", action="store_true",
                        help="DIAGNOSTIC: flat colour per track shader family incl. terrain/props")
    parser.add_argument("--hud-road-type", choices=("none", "dirt", "b", "a", "freeway"),
                        help="rebuild the closed nav road with this road_type so the HUD minimap "
                             "draws it (AI/traffic remain disabled)")
    parser.add_argument("--fm5-route-axes", action="store_true",
                        help="with --hud-road-type: convert a nav built from FM5 (x, z, height) "
                             "route points to FH2 (x, y, z)")
    parser.add_argument("--neutral-sun", action="store_true",
                        help="read FH2's yellow time-of-day sun colour as neutral grey in "
                             "lit track shaders (track-only, same brightness)")
    parser.add_argument("--diagnostic-full-sun", action="store_true",
                        help="DIAGNOSTIC: 0.15 baked-shadow fallback -> 1.0 in road_basic, "
                             "roadlinedecal and roadedgedecal_basic only")
    parser.add_argument("--diagnostic-flat-normals", action="store_true",
                        help="DIAGNOSTIC: replace track normal-map samples with a flat normal")
    parser.add_argument("--neutralize-edge-decal-pack-ao", action="store_true",
                        help="force roadedgedecal_basic Pack_AO_Mask AO (red) to 1; decal alpha "
                             "(green) kept (close-range dark/black edge decals)")
    parser.add_argument("--neutralize-road-ao", action="store_true",
                        help="force the AO-mask red channel to 1 in basic road/edge-decal shaders "
                             "(close-range black/dark pads, strips and kerbs); decal alpha kept")
    parser.add_argument("--bind-all-material-textures", action="store_true",
                        help="add every root texture referenced by a model's materials to that "
                             "model's group texture list (unlisted kerb/edge-decal textures)")
    parser.add_argument("--bind-material-textures", action="store_true",
                        help="add repaired material textures to each using model's group texture "
                             "list so FH2 binds them (run after --repair-missing-material-textures)")
    parser.add_argument("--diagnostic-nan-output", action="store_true",
                        help="diagnostic: draw NaN track pixels bright green")
    parser.add_argument("--diagnostic-white-diffuse", action="store_true",
                        help="DIAGNOSTIC: white road/decal diffuse RGB, keeping lighting and alpha")
    parser.add_argument("--diagnostic-shader-colors", action="store_true",
                        help="DIAGNOSTIC: paint each road/decal shader family a flat colour")
    parser.add_argument("--repair-missing-material-textures", action="store_true",
                        help="point road/pad material texture hashes absent from FH2's texture "
                             "table at resident neutral textures (stale-binding dark surfaces)")
    parser.add_argument("--patch-instancing-poses", action="store_true",
                        help="retarget FM5's 272-byte instanced poses to FH2's 288-byte records")
    parser.add_argument("--default-draw-distances", nargs=4, type=float,
                        metavar=("LOD0", "LOD1", "LOD2", "LOD3"),
                        help="bake explicit metre distances for draws whose four bands are all -1")
    parser.add_argument("--map-wide-distance", type=float, metavar="METRES",
                        help="extend every final draw band and keep the complete map resource "
                             "closure at highest available texture quality in every zone")
    args = parser.parse_args()
    try:
        if args.scale_baked_shadow_fallback is not None:
            if args.fm5_bin_zip is None:
                raise ValueError("--scale-baked-shadow-fallback requires --fm5-bin-zip")
            report = scale_baked_shadow_fallback(
                args.source, args.output, args.fm5_bin_zip, args.scale_baked_shadow_fallback)
        elif args.fm5_bin_zip is not None:
            raise ValueError("--fm5-bin-zip requires --scale-baked-shadow-fallback")
        elif args.realign_baked_shadow_fallback:
            report = realign_baked_shadow_fallback(args.source, args.output,
                                                   args.realign_baked_shadow_fallback)
        elif args.diagnostic_all_shader_colors:
            report = diagnostic_shader_colors(args.source, args.output,
                                              DIAGNOSTIC_ALL_SHADER_COLORS)
        elif args.hud_road_type:
            report = set_hud_road_type(args.source, args.output, args.hud_road_type, args.fm5_route_axes)
        elif args.neutral_sun:
            report = neutral_sun(args.source, args.output)
        elif args.diagnostic_full_sun:
            report = diagnostic_full_sun(args.source, args.output)
        elif args.diagnostic_flat_normals:
            report = diagnostic_flat_normals(args.source, args.output)
        elif args.neutralize_edge_decal_pack_ao:
            report = neutralize_edge_decal_pack_ao(args.source, args.output)
        elif args.neutralize_road_ao:
            report = neutralize_road_ao(args.source, args.output)
        elif args.bind_all_material_textures:
            report = bind_material_textures(args.source, args.output, "all",
                                            "bound_all_material_textures")
        elif args.bind_material_textures:
            report = bind_material_textures(args.source, args.output)
        elif args.diagnostic_nan_output:
            report = diagnostic_nan_output(args.source, args.output)
        elif args.diagnostic_white_diffuse:
            report = diagnostic_white_diffuse(args.source, args.output)
        elif args.diagnostic_shader_colors:
            report = diagnostic_shader_colors(args.source, args.output)
        elif args.repair_missing_material_textures:
            report = repair_missing_material_textures(args.source, args.output)
        elif args.guard_track_normals:
            report = guard_track_normals(args.source, args.output)
        elif args.guard_marking_normals:
            report = guard_marking_normals(args.source, args.output)
        elif args.repair_topgear_runway_markings:
            report = repair_topgear_markings(args.source, args.output, include_runway=True)
        elif args.repair_topgear_markings:
            report = repair_topgear_markings(args.source, args.output)
        elif args.map_wide_distance is not None:
            report = map_wide_visibility(args.source, args.output, args.map_wide_distance)
        elif args.default_draw_distances:
            report = bake_default_draw_distances(args.source, args.output, args.default_draw_distances)
        elif args.patch_instancing_poses:
            report = patch_instancing_poses(args.source, args.output)
        elif args.repair_draw_lods:
            report = repair_draw_lods(args.source, args.output)
        elif args.separate_grass_depth:
            report = separate_grass_depth(args.source, args.output)
        elif args.neutralize_blended_pavement_ao:
            report = neutralize_blended_pavement_ao(args.source, args.output)
        elif args.neutralize_pavement_skid:
            report = neutralize_pavement_skid(args.source, args.output)
        elif args.neutralize_terrain_macro_tint:
            report = neutralize_terrain_macro_tint(args.source, args.output)
        elif args.neutralize_legacy_ao:
            report = neutralize_legacy_ao(args.source, args.output)
        elif args.neutralize_macro_tint:
            report = neutralize_macro_tint(args.source, args.output)
        elif args.bake_grass_fade:
            report = bake_grass_fade(args.source, args.output, *args.bake_grass_fade)
        elif args.min_draw_distance is not None:
            report = extend_draw_distances(args.source, args.output, args.min_draw_distance)
        elif args.remap_surfaces:
            report = remap_surfaces(args.source, args.output, *args.remap_surfaces)
        elif args.patch_shaders:
            report = patch_shaders(args.source, args.output, args.baked_shadow_visibility)
        elif args.zone_collision:
            report = zone_collision(args.source, args.output)
        elif args.stream_probe_diffuse:
            report = stream_probe_diffuse(args.source, args.output)
        else:
            report = repack(args.source, args.output, args.single_chunk)
    except (OSError, ValueError, KeyError) as error:
        print("error: " + str(error), file=sys.stderr)
        return 1
    print("catalog_sha256 " + report["catalog_sha256"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
