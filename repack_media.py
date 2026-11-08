"""Re-encode a built media directory's GeoChunk archives with FH2's native 4 KB zlib window."""
import argparse
import collections
import hashlib
import json
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


def _rewrite_shader_programs(source, output, transform, report_key):
    """Copy media, passing every .pso/.vso in bin.zip through transform(name, data) -> (data, stats)."""
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
                if name.lower().endswith((".pso", ".vso")):
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


def patch_shaders(source, output):
    """Retarget the bin.zip FM5 shader programs to FH2's light-scenario constants (shader_patch)."""
    from shader_patch import patch_program

    def transform(name, data):
        lower = name.lower()
        return patch_program(data, "tracklightscenario" in lower or "cubemaplightscenario" in lower)

    return _rewrite_shader_programs(source, output, transform, "patched_shader_programs")


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
        if args.guard_marking_normals:
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
            report = patch_shaders(args.source, args.output)
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
