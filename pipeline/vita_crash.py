# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Crash tool: read a Vita core dump (ux0:data/psp2core-*.psp2dmp).

Prints each thread's name, pc, lr and sp, and a rough backtrace: the words on
the thread's stack that point into the eboot's code (return addresses, plus
some stale ones; read from the top). With --elf (the eboot.elf from
vita_port_build.py), eboot addresses are turned into function names and
source lines with addr2line. Also prints the system console (TTY) when asked.

A dump of a hung app can be had with the source-built eboot: create the file
ux0:data/renpy_hangdump (vitactl.py put any file there) and its watchdog
thread crashes the app on purpose within a second.

Dumps are gzip'd ELF core files whose PT_NOTE segments hold named records
(THREAD_INFO: 0xC8-byte entries, name at +0x10, stop reason at +0x7C, nonzero
only for the thread that faulted, e.g. 0x30004 for a data abort, pc at +0xA4; THREAD_REG_INFO:
0x178-byte entries, r0..r12, sp, lr, pc, cpsr from +0x10; same thread order)
and whose PT_LOAD segments hold the process memory, stacks included.
"""

import argparse
import gzip
import os
import shutil
import struct
import subprocess
from pathlib import Path

EBOOT_BASE = 0x81000000
VITASDK = Path(os.environ.get("VITASDK") or Path.cwd() / "build/toolchain/vitasdk")


def load(path):
    data = Path(path).read_bytes()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    if data[:4] != b"\x7fELF":
        raise SystemExit(f"{path}: not an ELF core dump")
    phoff, = struct.unpack_from("<I", data, 0x1C)
    phentsize, phnum = struct.unpack_from("<HH", data, 0x2A)
    notes, segments = {}, []
    for i in range(phnum):
        p_type, p_off, p_vaddr, _, p_filesz = struct.unpack_from("<5I", data, phoff + i * phentsize)
        if p_type == 4:
            off = p_off
            while off < p_off + p_filesz:
                namesz, descsz, _ = struct.unpack_from("<3I", data, off)
                name = data[off + 12:off + 12 + namesz].rstrip(b"\0").decode(errors="replace")
                desc_off = off + 12 + ((namesz + 3) & ~3)
                notes[name] = data[desc_off:desc_off + descsz]
                off = desc_off + ((descsz + 3) & ~3)
        elif p_type == 1 and p_filesz:
            segments.append((p_vaddr, data[p_off:p_off + p_filesz]))
    return notes, segments


def threads(notes):
    info, regs = notes.get("THREAD_INFO", b""), notes.get("THREAD_REG_INFO", b"")
    count, size = struct.unpack_from("<II", info, 4)
    rcount, rsize = struct.unpack_from("<II", regs, 4) if regs else (0, 0)
    rv = []
    for k in range(count):
        entry = info[k * size:(k + 1) * size]
        name = entry[0x10:0x30].split(b"\0")[0].decode(errors="replace")
        r = struct.unpack_from("<17I", regs, 0x10 + k * rsize) if k < rcount else None
        stop_reason, = struct.unpack_from("<I", entry, 0x7C)
        rv.append(dict(name=name, pc=struct.unpack_from("<I", entry, 0xA4)[0], regs=r, stop_reason=stop_reason))
    return rv


def read_mem(segments, addr, length):
    for base, blob in segments:
        if base <= addr < base + len(blob):
            return blob[addr - base:addr - base + length]
    return b""


def symbolize(elf, addrs):
    if not elf or not addrs:
        return {}
    tool = shutil.which("arm-vita-eabi-addr2line") or str(VITASDK / "bin/arm-vita-eabi-addr2line")
    out = subprocess.run([tool, "-f", "-C", "-i", "-a", "-e", str(elf)] + [hex(a & ~1) for a in addrs],
                         capture_output=True, text=True).stdout.splitlines()
    rv, cur = {}, None
    for line in out:
        if line.startswith("0x"):
            cur = int(line, 16)
            rv[cur] = []
        elif cur is not None:
            rv[cur].append(line)
    return {a: rv.get(a & ~1, []) for a in addrs}


def describe(addr, syms):
    lines = syms.get(addr) or []
    pairs = [f"{lines[i]} ({Path(lines[i + 1]).name})" for i in range(0, len(lines) - 1, 2)]
    return " <- ".join(pairs) if pairs else ""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dump")
    ap.add_argument("--elf", type=Path, help="eboot.elf matching the eboot that crashed")
    ap.add_argument("--text-size", type=lambda s: int(s, 0), default=0x1000000,
                    help="size of the eboot's code range for stack scanning (default 16 MiB)")
    ap.add_argument("--depth", type=int, default=24, help="return addresses per thread")
    ap.add_argument("--stack-bytes", type=int, default=0x4000)
    ap.add_argument("--tty", action="store_true", help="also print the system console")
    args = ap.parse_args()

    notes, segments = load(args.dump)
    in_eboot = lambda a: EBOOT_BASE <= a < EBOOT_BASE + args.text_size

    found = []
    for t in threads(notes):
        r = t["regs"]
        stack = []
        if r:
            sp = r[13]
            mem = read_mem(segments, sp, args.stack_bytes)
            for i in range(0, len(mem) - 3, 4):
                w, = struct.unpack_from("<I", mem, i)
                if in_eboot(w) and w & 1:      # Thumb return addresses
                    stack.append(w)
                    if len(stack) >= args.depth:
                        break
        found.append((t, stack))

    addrs = sorted({a for t, s in found for a in s + ([t["regs"][15], t["regs"][14]] if t["regs"] else [t["pc"]]) if in_eboot(a)})
    syms = symbolize(args.elf, addrs)

    for t, stack in found:
        r = t["regs"]
        crashed = f"   <-- CRASHED (stop reason {t['stop_reason']:#x})" if t["stop_reason"] else ""
        print(f"== {t['name']}{crashed}")
        if r:
            for label, value in (("pc", r[15]), ("lr", r[14])):
                print(f"  {label} {value:#010x} {describe(value, syms) if in_eboot(value) else '(not eboot)'}")
            print(f"  sp {r[13]:#010x}  r0-r3 {' '.join(f'{x:#x}' for x in r[:4])}")
        else:
            print(f"  pc {t['pc']:#010x}")
        for a in stack:
            print(f"    {a:#010x} {describe(a, syms)}")

    if args.tty:
        for name in ("TTY_INFO2", "TTY_INFO"):
            if name in notes:
                print(f"== {name}")
                print(notes[name][12:].split(b"\0")[0].decode(errors="replace"))


if __name__ == "__main__":
    main()
