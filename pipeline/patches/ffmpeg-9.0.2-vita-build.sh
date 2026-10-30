#!/bin/sh
# FFmpeg 9.0.2 for the Vita, as vdpm's recipe (vitasdk-packages/ffmpeg/VITABUILD)
# but WITHOUT -ffast-math. With it, av_ts_make_time_string2() turns a timestamp
# of exactly 0 into precision = (int)+inf = INT_MAX on ARM (fast-math assumes
# isfinite(-INFINITY)), and snprintf("%.*f") then generates 2^31 digits: ~20 s
# on the Vita for every audio file whose start time is 0, while the decode
# thread starves the game (whole app and music freeze). The string is built
# for a debug log line in avformat_find_stream_info even when not printed.
# Since 2026-10-30 also the mov demuxer (MP4: the list's "mp4" and "m4a" aren't
# demuxer names, so MP4 couldn't be read at all), the h264 parser and the
# h264_mp4toannexb bitstream filter, for the port's own hardware video player,
# and the h264 (software) decoder.
# vita_toolchain.py runs this in an unpacked ffmpeg-9.0.2 tree (VITASDK set,
# $VITASDK/bin on PATH), then `make` and `make install`.
./configure --prefix=$VITASDK/arm-vita-eabi \
  --enable-cross-compile --cross-prefix=$VITASDK/bin/arm-vita-eabi- --disable-shared --disable-runtime-cpudetect --disable-armv5te --disable-programs --disable-doc --disable-network --enable-libmp3lame --disable-everything \
  --enable-decoder=aac,adpcm_ima_qt,alac,ape,ac3,cinepak,flac,h264,mp3,mpeg4,opus,pcm_s16le,pcm_s24le,pcm_s32le,pcm_s8,pcm_u8,svq1,tta,vorbis,wmalossless,wavpack \
  --enable-demuxer=aac,ac3,aiff,ape,asf,avi,caf,cine,flac,matroska,mov,mp3,mp4,m4a,pcm_s16le,pcm_s24le,pcm_s32le,pcm_s8,pcm_u8,ogg,opus,tta,wav,webm,wv \
  --enable-muxer=aac,ac3,aiff,ape,asf,avi,caf,flac,matroska,mp3,mp4,m4a,pcm_s16le,pcm_s24le,pcm_s32le,pcm_s8,pcm_u8,ogg,opus,tta,wav,webm,wv \
  --enable-encoder=aac,alac,ape,ac3,flac,libmp3lame,mpeg4,opus,pcm_s16le,pcm_s24le,pcm_s32le,pcm_s8,pcm_u8,tta,vorbis,wmalossless,wavpack \
  --enable-parser=aac,ac3,flac,h263,h264,mpeg4video,mpegaudio,opus,vorbis --enable-bsf=h264_mp4toannexb --enable-protocol=file --enable-static --enable-small --disable-debug --arch=armv7-a --cpu=cortex-a9 --disable-armv6t2 --target-os=none \
  --extra-cflags=" -std=gnu11 -Wno-error=implicit-function-declaration -Wno-error=int-conversion -Wno-error=incompatible-pointer-types -Wl,-q -O2 -ftree-vectorize -fomit-frame-pointer -D_BSD_SOURCE" \
  --extra-cxxflags=" -Wl,-q -O2 -ftree-vectorize -fomit-frame-pointer -fno-rtti -fno-exceptions -std=gnu++11 -D_BSD_SOURCE" \
  --extra-ldflags=" -L$VITASDK/lib " --extra-libs=-lmpg123 --disable-bzlib --disable-iconv --disable-lzma --disable-sdl2 --disable-securetransport --disable-xlib --enable-pthreads
