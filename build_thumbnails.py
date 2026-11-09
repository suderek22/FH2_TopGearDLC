"""Prepare FH2-native browser artwork from the supplied FM5 and FH2 archives."""

import argparse
import configparser
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile

from PIL import Image, ImageOps


def letterbox(image, size):
    if size[0] <= 0 or size[1] <= 0:
        raise ValueError("thumbnail dimensions must be positive")
    fitted = ImageOps.contain(image.convert("RGBA"), size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(fitted, ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2))
    return canvas


def decode_fm5(data, swatch):
    width, height, mips, header, payload = swatch.read_header(data)
    if mips != 1 or header[31:] != bytes.fromhex("ae010090"):
        raise ValueError("unsupported FM5 UI texture descriptor")
    computer = swatch._Computer(width, height, mips, 28, 14)
    try:
        if not computer.ok or computer.size() != len(payload):
            raise ValueError("FM5 RGBA8/tile14 payload size mismatch")
        row, depth = swatch._pitches(width, height, 28)
        texture = swatch.Texture(width, height, mips, 28, computer.detile(payload, row, depth))
        return Image.fromarray(texture.rgba())
    finally:
        computer.close()

def decode_native(data, swatch):
    width, height, mips, header, payload = swatch.read_header(data)
    if mips != 1 or header[31:] != bytes.fromhex("2e0100b8"):
        raise ValueError("unsupported FH2 thumbnail descriptor")
    matches = []
    row, depth = swatch._pitches(width, height, 98)
    for mode in (13, 14):
        computer = swatch._Computer(width, height, mips, 98, mode)
        try:
            if not computer.ok or computer.size() != len(payload):
                continue
            linear = computer.detile(payload, row, depth)
            pointer = ctypes.c_void_p
            vtable = ctypes.cast(ctypes.cast(computer.ptr, ctypes.POINTER(pointer))[0], ctypes.POINTER(pointer))
            tile = ctypes.CFUNCTYPE(ctypes.c_int, pointer, pointer, ctypes.c_uint32, ctypes.c_uint32,
                                   pointer, ctypes.c_uint32, ctypes.c_uint32)(vtable[8])
            source = ctypes.create_string_buffer(linear, len(linear))
            result = ctypes.create_string_buffer(len(payload))
            status = tile(computer.ptr, ctypes.addressof(result), 0, 0, ctypes.addressof(source), row, depth)
            if status != 0:
                raise OSError(f"XG native thumbnail tiling failed: {status:#x}")
            if result.raw == payload:
                matches.append((mode, Image.fromarray(swatch.Texture(width, height, mips, 98, linear).rgba())))
        finally:
            computer.close()
    if len(matches) != 1:
        raise ValueError("FH2 thumbnail must have exactly one lossless XG tile layout")
    return matches[0]


def install(output, track, manifest):
    report = json.loads((output / "thumbnails.json").read_text(encoding="ascii"))
    if report["format"] != "TLCPS-THUMBNAILS-1":
        raise ValueError("unsupported artwork manifest")
    ini = configparser.ConfigParser(interpolation=None)
    ini.optionxform = str
    with manifest.open(encoding="utf-8-sig") as stream:
        ini.read_file(stream)
    payloads = {}
    hashes = {}
    for size, name in (("large", "poster"), ("small", "logo")):
        entry = report["tracks"][track][size]
        filename = f"{track}_{size.title()}.swatchbin"
        if entry["file"] != filename:
            raise ValueError("unexpected artwork filename")
        data = (output / filename).read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise ValueError(f"artwork SHA256 mismatch: {filename}")
        payloads[name] = data
        hashes[name.title() + "Sha256"] = entry["sha256"]
    browser = manifest.parent / "browser"
    if manifest.is_symlink() or browser.is_symlink():
        raise ValueError("redirected artwork installation is not supported")
    browser.mkdir(exist_ok=True)
    for name, data in payloads.items():
        path = browser / f"{name}.swatchbin"
        if path.is_symlink():
            raise ValueError("redirected artwork file is not supported")
        path.write_bytes(data)
    ini["Browser"] = hashes
    with manifest.open("w", encoding="utf-8", newline="") as stream:
        ini.write(stream)


def build(fm5_mount, fh2_mount, cps_source, forzatech, converter, output):
    sys.path.insert(0, str(cps_source))
    from cps import swatch

    swatch.set_xg_dir(str(forzatech))
    fm5 = fm5_mount / "media" / "ui" / "textures" / "data_bound"
    fh2 = fh2_mount / "media" / "ui" / "textures" / "data_bound"
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="trackloader-thumbnails-") as temporary:
        scratch = Path(temporary)
        templates, pngs, converted = (scratch / name for name in ("templates", "pngs", "converted"))
        templates.mkdir()
        pngs.mkdir()
        with zipfile.ZipFile(fh2 / "Destinations.zip") as destinations:
            stock = {
                size: destinations.read(f"Rivals_Destination_Images/StormIsland_{size}.swatchbin")
                for size in ("Large", "Small")
            }
        for size, archive, entry in (
            ("Large", "Track_Posters.zip", "TopGear.swatchbin"),
            ("Small", "Track_Logos.zip", "L_TRK_TopGear.swatchbin"),
        ):
            with zipfile.ZipFile(fm5 / archive) as source:
                image = decode_fm5(source.read(entry), swatch)
            width, height, mips, _, _ = swatch.read_header(stock[size])
            if mips != 1:
                raise ValueError("FH2 thumbnail template must have one mip")
            name = f"TopGear_{size}"
            (templates / f"{name}.swatchbin").write_bytes(stock[size])
            letterbox(image, (width, height)).save(pngs / f"{name}.png")
        subprocess.run([
            "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(converter),
            "-ForzaTechDir", str(forzatech), "-StockSwatchbinDir", str(templates),
            "-SourcePngDir", str(pngs), "-OutputDir", str(converted),
        ], check=True)
        report = {"format": "TLCPS-THUMBNAILS-1", "in_game_validated": False, "tracks": {}}
        for track in ("StormIsland", "TopGear"):
            artwork = {}
            for size in ("Large", "Small"):
                filename = f"{track}_{size}.swatchbin"
                data = stock[size] if track == "StormIsland" else (converted / filename).read_bytes()
                if data[:83] != stock[size][:83] or len(data) != len(stock[size]):
                    raise ValueError(f"native template mismatch: {filename}")
                tile_mode, preview = decode_native(data, swatch)
                (output / filename).write_bytes(data)
                artwork[size.lower()] = {
                    "file": filename,
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "logical_path": f"UI\\textures\\data_bound\\TrackLoader\\{filename}",
                    "dxgi": 98,
                    "tile_mode": tile_mode,
                }
                preview.save(output / f"{track}_{size}.png")
            report["tracks"][track] = artwork
        (output / "thumbnails.json").write_text(json.dumps(report, indent=2) + "\n", encoding="ascii")
        return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for option in ("fm5-mount", "fh2-mount", "cps-source", "forzatech", "converter", "output"):
        parser.add_argument("--" + option, type=Path)
    parser.add_argument("--install-pack", type=Path, help="Existing track.ini to receive staged artwork (game stopped)")
    parser.add_argument("--track", choices=("TopGear", "StormIsland"))
    args = parser.parse_args()
    if args.install_pack:
        if not args.output or not args.track:
            parser.error("--install-pack requires --output and --track")
        install(args.output, args.track, args.install_pack)
    else:
        if not all((args.fm5_mount, args.fh2_mount, args.cps_source, args.forzatech, args.converter, args.output)):
            parser.error("conversion requires all six source/tool/output arguments")
        build(args.fm5_mount, args.fh2_mount, args.cps_source, args.forzatech, args.converter, args.output)
