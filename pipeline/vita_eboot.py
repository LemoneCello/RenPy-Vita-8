# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Eboot stage: patch the port's newlib heap size inside eboot.bin.

The port reserves a fixed heap for Python, Ren'Py, the game's scripts and
decoded images (`_newlib_heap_size_user = 180 MB` in source/main.c). This
rewrites that constant in the fake-signed SELF. The data segment holding it is
stored uncompressed at the end of the file, so no other segment moves.
"""

import argparse
import struct
import sys
import zipfile
import zlib
from pathlib import Path

DEFAULT_PORT_ZIP = Path("build/cache/RenPy_Vita_8_v0.2.1_PVR.zip")
ORIGINAL_HEAP = 180 * 1024 * 1024
PT_LOAD = 1
COMP_NONE, COMP_ZLIB = 1, 2


def read_self(blob):
    if blob[:4] != b"SCE\0":
        sys.exit("not a SELF file")
    # Extended header: elf, phdr, shdr and segment-info offsets at 0x40.
    elf_off, phdr_off, _, seginfo_off = struct.unpack_from("<4Q", blob, 0x40)
    phentsize, phnum = struct.unpack_from("<HH", blob, elf_off + 0x2A)
    segments = []
    for i in range(phnum):
        p_type, _, p_vaddr, _, p_filesz = struct.unpack_from("<5I", blob, phdr_off + i * phentsize)
        info_at = seginfo_off + i * 32
        offset, length, comp, _ = struct.unpack_from("<4Q", blob, info_at)
        segments.append(dict(index=i, type=p_type, vaddr=p_vaddr, filesz=p_filesz,
                             info_at=info_at, offset=offset, length=length, comp=comp))
    return segments


def segment_data(blob, seg):
    data = blob[seg["offset"]:seg["offset"] + seg["length"]]
    return zlib.decompress(data) if seg["comp"] == COMP_ZLIB else data


def find_heap(data, value):
    hits = [i for i in range(0, len(data) - 3, 4) if struct.unpack_from("<I", data, i)[0] == value]
    if len(hits) != 1:
        sys.exit(f"expected one heap-size constant {value:#x} in the data segment, found {len(hits)}")
    return hits[0]


def patch(blob, heap_bytes):
    segments = read_self(blob)
    loads = [s for s in segments if s["type"] == PT_LOAD]
    if len(loads) != 2:
        sys.exit(f"expected text and data PT_LOAD segments, found {len(loads)}")
    data_seg = loads[1]
    data = bytearray(segment_data(blob, data_seg))
    if len(data) != data_seg["filesz"]:
        sys.exit("data segment size does not match its program header")

    at = find_heap(data, ORIGINAL_HEAP)
    struct.pack_into("<I", data, at, heap_bytes)
    print(f"  heap constant at {data_seg['vaddr'] + at:#010x}: {ORIGINAL_HEAP >> 20} MB -> {heap_bytes >> 20} MB")

    out = bytearray(blob)
    out += b"\0" * (-len(out) % 16)
    new_offset = len(out)
    out += data
    struct.pack_into("<3Q", out, data_seg["info_at"], new_offset, len(data), COMP_NONE)
    struct.pack_into("<Q", out, 0x20, len(out))  # self_filesize
    return bytes(out), data_seg, at


def verify(new_blob, data_seg, at, heap_bytes):
    seg = next(s for s in read_self(new_blob) if s["index"] == data_seg["index"])
    value = struct.unpack_from("<I", segment_data(new_blob, seg), at)[0]
    if value != heap_bytes:
        sys.exit("verification failed: patched value not read back")
    print(f"  verified: data segment now uncompressed at {seg['offset']:#x}, heap reads {value >> 20} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--port-zip", type=Path, help=f"port release zip (default {DEFAULT_PORT_ZIP})")
    source.add_argument("--eboot", type=Path, help="an unpatched eboot.bin")
    parser.add_argument("--heap-mb", type=int, default=250, help="new newlib heap size in MB (default 250)")
    parser.add_argument("--out", type=Path, help="output path (default build/eboot/eboot_heap<MB>.bin)")
    args = parser.parse_args()

    if args.eboot:
        blob = args.eboot.read_bytes()
    else:
        with zipfile.ZipFile(args.port_zip or DEFAULT_PORT_ZIP) as z:
            blob = z.read("eboot.bin")

    heap_bytes = args.heap_mb * 1024 * 1024
    out_path = args.out or Path(f"build/eboot/eboot_heap{args.heap_mb}.bin")
    print(f"Patching eboot.bin ({len(blob)} bytes)")
    new_blob, data_seg, at = patch(blob, heap_bytes)
    verify(new_blob, data_seg, at, heap_bytes)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(new_blob)
    print(f"Wrote {out_path} ({len(new_blob)} bytes)")


if __name__ == "__main__":
    main()
