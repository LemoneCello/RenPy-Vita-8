#!/bin/sh
# zlib-ng 2.2.4 (https://github.com/zlib-ng/zlib-ng/archive/refs/tags/2.2.4.tar.gz,
# sha256 a73343c3093e5cdc50d9377997c3815b878fd110bf6511c2c7759f2afb90f5a3) as a
# drop-in libz.a for the Vita (zlib API, NEON inflate/adler32/slide_hash).
# Runtime CPU detection can't work on the Vita (no /proc, no getauxval), and
# ACLE CRC32 is ARMv8, which the Cortex-A9 lacks: both off. Only libz.a is
# installed over the SDK's (stock zlib kept in build/toolchain/src/sdk-backup/
# zlib-stock/); headers stay stock (compat mode keeps the same ABI). Relink the
# eboot afterwards (delete port-work/<label>/renpy-vita/build/eboot.elf).
set -e
cd build/toolchain/src/zlib-ng-2.2.4
cmake -B build-vita -G Ninja -DCMAKE_TOOLCHAIN_FILE="$VITASDK/share/vita.toolchain.cmake" \
    -DCMAKE_BUILD_TYPE=Release -DZLIB_COMPAT=ON -DZLIB_ENABLE_TESTS=OFF -DZLIBNG_ENABLE_TESTS=OFF \
    -DWITH_GTEST=OFF -DBUILD_SHARED_LIBS=OFF -DWITH_RUNTIME_CPU_DETECTION=OFF -DWITH_ACLE=OFF
ninja -C build-vita
cp build-vita/libz.a "$VITASDK/arm-vita-eabi/lib/libz.a"
