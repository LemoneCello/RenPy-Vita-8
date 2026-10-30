# Boot-time speed-ups for the Ren'Py PS Vita port, installed from renpy.py
# before bootstrap:
#
#     import vita_boot
#     vita_boot.install(renpy_base)
#
# Automatic garbage collection is off while Ren'Py loads: loading the script
# unpickles millions of objects, and with Ren'Py's thresholds (25000, 10, 10)
# collections keep rescanning the growing heap. Ren'Py still runs its own full
# collection ("Initial gc") before the display starts, and automatic
# collection is back on when the presplash ends.
#
# Once the script is loaded, its objects (about 1.4 million nodes, strings
# and tuples that live for the whole session) are moved to the permanent
# generation with gc.freeze(), so neither Ren'Py's "Initial gc" nor later
# full collections during play scan them again.
#
# Story loads on demand when the build split the scripts (vita_story.py): the
# boot .rpyc files hold only init-relevant statements, story parts sit in
# game/vita_story/, and game/vita_story_index.bin maps labels and script files
# to parts. Script.lookup loads the part on a miss, then re-chains the file's
# statements in their original order so fallthrough between labels works;
# has_label and get_all_labels answer from the index. The bytecode cache keeps
# entries for code not loaded this session, so story code compiled once stays
# cached. Without the index file nothing changes.
#
# Python 3.11's zip importer loads every module from lib/python311.zip twice
# (get_filename() calls _get_module_code(), which reads and unmarshals the
# module just to report its path; loading it then does the same again) and
# reopens the zip file for each read. Here the first result is kept for the
# second call, and the zip stays open.
#
# Ren'Py's loader looks for every file it loads loose in each search
# directory first (os.path.isfile in renpy.loader.transfn), although it has
# already listed all loose files. On the Vita each of those checks costs about
# a millisecond and boot makes ~3,000 of them, nearly all misses (files that
# are in archives, movie files checked under several prefixes). transfn now
# raises "not found" straight away for names that aren't in that listing.
# Like Ren'Py's own loadable cache, this assumes no loose game files appear
# during a session; cache/ and saves/ (which the listing leaves out) still go
# to the file system.
#
# Ren'Py saves its bytecode cache (cache/bytecode-311.rpyb, compiled code for
# every Python expression in the script) as a protocol 2 pickle, in which
# every bytes value is rebuilt through a Python-level reduce. On the Vita,
# unpickling the test game's (59,500 entries) took 3.5 s; as protocol 5, 0.6 s. The
# cache is saved as protocol 5 here (Ren'Py's loader reads any protocol), and
# a cache still in an older protocol is rewritten once.
#
# Preparing screens compiles every screen-language expression from its AST on
# every boot (4,163 for the test game, ~3 s on the Vita). For the boot's
# preparation (Ren'Py calls prepare_screens again at every interaction, where
# it rarely has anything left to do), the compiled code is
# kept in game/cache/vita_screen_code.bin (marshal), keyed by source location
# (plus an occurrence count, as screens are prepared in the same order each
# time) and valid only for the same script digest, Ren'Py and Python version,
# like Ren'Py's own screen analysis cache.
#
# While the script loads, Ren'Py checks each script file's .rpy and .rpyc
# with os.path.exists several times (~650 checks for the test game, ~1-3 ms each on
# the Vita). renpy.script and renpy.lexer get an os module whose
# path.exists answers from one directory listing per directory (compared
# case-insensitively, like the Vita's file system) until the boot ends;
# everything else uses the real os module. The only files written there
# during boot are .rpyc files, which Ren'Py doesn't look for again.
#
# Diagnostic: if a file named vita_bootprof exists next to renpy.py, a thread
# samples the main thread's Python stack from here until the presplash ends
# and writes vita_bootprof.txt. Each sample is weighted by the time since the
# previous one, so long C calls (which hold the GIL and delay the sampler)
# still count in full.

import gc
import os
import time
import traceback

LOG_NAME = "vita_boot.txt"
STORY_INDEX = "vita_story_index.bin"
PROFILE_FLAG = "vita_bootprof"
PROFILE_NAME = "vita_bootprof.txt"


class Profiler(object):

    def __init__(self, path):
        import collections
        import threading

        self.path = path
        self.lock = threading.Lock()
        self.running = True
        self.main_id = threading.main_thread().ident
        self.inclusive = collections.Counter()
        self.self_time = collections.Counter()
        self.total = 0.0
        self.thread = threading.Thread(target=self.run, name="vita_bootprof")
        self.thread.daemon = True
        self.thread.start()

    @staticmethod
    def key(code):
        fn = code.co_filename.replace("\\", "/")
        for marker in ("/renpy/", "renpy/"):
            if marker in fn:
                fn = "renpy/" + fn.split(marker, 1)[1]
                break
        return "%s:%d %s" % (fn, code.co_firstlineno, code.co_name)

    def run(self):
        import sys

        last = time.time()
        while self.running:
            time.sleep(0.005)
            now = time.time()
            weight = now - last
            last = now

            frame = sys._current_frames().get(self.main_id)
            keys = [ ]
            while frame is not None:
                keys.append(self.key(frame.f_code))
                frame = frame.f_back

            with self.lock:
                if not self.running:
                    break
                if keys:
                    self.self_time[keys[0]] += weight
                for k in set(keys):
                    self.inclusive[k] += weight
                self.total += weight

    def stop(self):
        with self.lock:
            self.running = False
        lines = [ "%.1f s sampled\n\nINCLUSIVE (s)\n" % self.total ]
        for k, v in self.inclusive.most_common(70):
            lines.append("%7.2f  %s\n" % (v, k))
        lines.append("\nSELF (s)\n")
        for k, v in self.self_time.most_common(50):
            lines.append("%7.2f  %s\n" % (v, k))
        with open(self.path, "w") as f:
            f.write("".join(lines))


def freeze_after_load(log):
    import renpy.script

    original_load_script = renpy.script.Script.load_script

    def load_script(self, *args, **kwargs):
        rv = original_load_script(self, *args, **kwargs)
        gc.freeze()
        log("script loaded; froze %d objects" % gc.get_freeze_count())
        return rv

    renpy.script.Script.load_script = load_script


def lazy_story(log):
    import marshal
    import zlib
    import renpy
    import renpy.ast
    import renpy.atl
    import renpy.display.predict
    import renpy.exports
    import renpy.script
    from renpy.compat.pickle import loads

    # config.gamedir isn't set yet when this is installed, so the index is
    # read on first use. Without it every hook behaves like Ren'Py's own.
    labels = { }
    files = { }
    loaded = set()
    state = { "read": False }

    def read_index():
        state["read"] = True
        path = os.path.join(renpy.config.gamedir, STORY_INDEX)
        if not os.path.exists(path):
            log("no %s; story loads at startup" % STORY_INDEX)
            return
        with open(path, "rb") as f:
            index = marshal.load(f)
        labels.update(index["labels"])
        files.update(index["files"])
        log("lazy story: %d labels in %d parts" % (len(labels), len(files)))

    def part_for(name):
        if not state["read"]:
            read_index()
        if isinstance(name, str):
            return labels.get(name)
        if isinstance(name, tuple) and name:
            return files.get(name[0])
        return None

    def load_part(part):
        loaded.add(part)
        start = time.time()
        script = renpy.game.script

        with renpy.loader.load(part) as f:
            payload = loads(zlib.decompress(f.read()))

        stmts = payload["stmts"]
        initcode = [ ]

        # As for a normal load: without the .rpy source, statements are
        # renamed to the .rpyc that was loaded.
        filename = payload["filename"]
        if not os.path.exists(os.path.join(renpy.config.basedir, filename)):
            script.finish_load(stmts, initcode, filename=filename + "c")
        else:
            script.finish_load(stmts, initcode)

        # finish_load chained the story statements among themselves; chain
        # the whole file in its original order, as a full load would have.
        full = [ None ] * payload["total"]
        for pos, node in zip(payload["positions"], stmts):
            full[pos] = node
        for pos, name in payload["boot_names"]:
            full[pos] = script.namemap.get(name)
        renpy.ast.chain_block([ n for n in full if n is not None ], None)

        script.translator.chain_translates()
        script.analyze()
        renpy.atl.compile_all()

        # User statements always report an init hook; the build only moves
        # ones whose registration does nothing at init.
        real_init = [ i for i in initcode if getattr(i[1], "__self__", None).__class__.__name__ != "UserStatement" ]
        if real_init:
            log("WARNING: %s had %d init statements" % (part, len(real_init)))

        if script.bytecode_dirty:
            script.save_bytecode()

        gc.freeze()
        log("loaded %s (%d statements) in %.1f s" % (part, len(stmts), time.time() - start))

    def ensure(name):
        # Prediction may look ahead into story that isn't loaded; it copes
        # with missing labels, and Ren'Py forbids opening files meanwhile.
        if renpy.display.predict.predicting:
            return
        part = part_for(name)
        if part is not None and part not in loaded:
            load_part(part)

    Script = renpy.script.Script

    original_lookup = Script.lookup

    def lookup(self, label):
        name = label.block[0].name if isinstance(label, renpy.parser.SubParse) else label
        name = renpy.config.label_overrides.get(name, name)
        if name not in self.namemap:
            ensure(name)
        return original_lookup(self, label)

    original_has_label = Script.has_label

    def has_label(self, label):
        if original_has_label(self, label):
            return True
        # Prediction checks has_label before lookup; it must skip story
        # that isn't loaded (loading isn't allowed while predicting).
        if isinstance(label, renpy.parser.SubParse) or renpy.display.predict.predicting:
            return False
        name = renpy.config.label_overrides.get(label, label)
        return part_for(name) is not None

    original_save_bytecode = Script.save_bytecode

    def save_bytecode(self):
        # Keep entries for code that isn't loaded this session.
        merged = dict(self.bytecode_oldcache)
        merged.update(self.bytecode_newcache)
        self.bytecode_newcache = merged
        return original_save_bytecode(self)

    original_get_all_labels = renpy.exports.get_all_labels

    def get_all_labels():
        rv = original_get_all_labels()
        if not state["read"]:
            read_index()
        rv.update(labels)
        return rv

    Script.lookup = lookup
    Script.has_label = has_label
    Script.save_bytecode = save_bytecode
    renpy.exports.get_all_labels = get_all_labels

    log("lazy story hooks installed")


def zipimport_once(log):
    import threading
    import zipimport

    if getattr(zipimport, "_vita_once", False):
        return

    original_get_module_code = zipimport._get_module_code
    original_get_data = zipimport._get_data

    # (archive, prefix, fullname) -> result of the first _get_module_code.
    results = { }

    def _get_module_code(self, fullname):
        key = (self.archive, self.prefix, fullname)
        rv = results.pop(key, None)
        if rv is None:
            rv = original_get_module_code(self, fullname)
            results[key] = rv
        return rv

    # archive -> open file, shared under a lock.
    files = { }
    lock = threading.Lock()

    def _get_data(archive, toc_entry):
        datapath, compress, data_size, file_size, file_offset, time_, date, crc = toc_entry

        with lock:
            fp = files.get(archive)
            if fp is None:
                fp = files[archive] = open(archive, "rb")

            fp.seek(file_offset)
            header = fp.read(30)
            if len(header) != 30 or header[:4] != b"PK\x03\x04":
                return original_get_data(archive, toc_entry)

            name_size = int.from_bytes(header[26:28], "little")
            extra_size = int.from_bytes(header[28:30], "little")
            fp.seek(file_offset + 30 + name_size + extra_size)
            raw = fp.read(data_size)

        if len(raw) != data_size:
            return original_get_data(archive, toc_entry)

        if compress == 0:
            return raw

        import zlib
        return zlib.decompress(raw, -15)

    zipimport._get_module_code = _get_module_code
    zipimport._get_data = _get_data
    zipimport._vita_once = True
    log("zipimport: single load, archive kept open")


def fast_bytecode(log):
    import pickle
    import zlib
    import renpy.loader
    import renpy.script as script

    Script = script.Script

    if getattr(Script, "_vita_fast_bytecode", False):
        return

    original_init_bytecode = Script.init_bytecode
    original_save_bytecode = Script.save_bytecode

    def init_bytecode(self):
        original_init_bytecode(self)

        if not self.bytecode_oldcache:
            return

        # Rewrite a cache saved in an older protocol.
        try:
            with renpy.loader.load(script.BYTECODE_FILE) as f:
                head = zlib.decompressobj().decompress(f.read(4096))[:2]
            if head != b"\x80\x05":
                self.bytecode_dirty = True
                log("bytecode cache in protocol %r; will rewrite" % (head[1:],))
        except Exception:
            log(traceback.format_exc())

    def save_bytecode(self):
        original_dumps = script.dumps
        script.dumps = lambda o, highest=False: pickle.dumps(o, 5)
        try:
            return original_save_bytecode(self)
        finally:
            script.dumps = original_dumps

    Script.init_bytecode = init_bytecode
    Script.save_bytecode = save_bytecode
    Script._vita_fast_bytecode = True
    log("bytecode cache saved as protocol 5")


def screen_code_cache(log):
    import marshal
    import sys
    import renpy
    import renpy.loader
    import renpy.display.screen as screen
    import renpy.sl2.slast as slast

    if getattr(slast, "_vita_screen_code", False):
        return

    CACHE = "cache/vita_screen_code.bin"

    original_compile_expr = slast.compile_expr
    original_prepare_screens = screen.prepare_screens

    state = { "old" : None, "new" : None, "seen" : None, "misses" : 0 }

    def compile_expr(loc, node):
        new = state["new"]

        if new is None:
            return original_compile_expr(loc, node)

        k = (loc[0], loc[1], getattr(node, "lineno", None), getattr(node, "col_offset", None),
             getattr(node, "end_lineno", None), getattr(node, "end_col_offset", None), type(node).__name__)
        seen = state["seen"]
        n = seen.get(k, 0)
        seen[k] = n + 1
        key = k + (n,)

        rv = state["old"].get(key)
        if rv is None:
            rv = original_compile_expr(loc, node)
            state["misses"] += 1
            if state["old"] and state["misses"] <= 5:
                log("screen code miss %r" % (key,))

        new[key] = rv
        return rv

    def header():
        return repr((renpy.game.script.digest.hexdigest(), renpy.version_only, sys.version)).encode("utf-8") + b"\n"

    def prepare_screens(*args, **kwargs):
        # Ren'Py also calls this at the start of every interaction, where it
        # usually has nothing to do; only the boot's call uses the cache.
        if state.get("done"):
            return original_prepare_screens(*args, **kwargs)

        state["done"] = True

        path = renpy.loader.get_path(CACHE)
        h = header()

        old = { }
        try:
            with open(path, "rb") as f:
                data = f.read()
            if data.startswith(h):
                old = marshal.loads(data[len(h):])
        except Exception:
            pass

        state.update(old=old, new={ }, seen={ }, misses=0)

        try:
            return original_prepare_screens(*args, **kwargs)
        finally:
            new = state["new"]
            misses = state["misses"]
            state.update(old=None, new=None, seen=None)

            if new:
                log("screen code: %d expressions, %d compiled" % (len(new), misses))

            # Ren'Py prepares screens more than once; later passes find
            # nothing new, so only save when something was compiled.
            if misses:
                merged = dict(old)
                merged.update(new)
                try:
                    with open(path, "wb") as f:
                        f.write(h)
                        f.write(marshal.dumps(merged))
                except Exception:
                    log(traceback.format_exc())

    slast.compile_expr = compile_expr
    screen.prepare_screens = prepare_screens
    slast._vita_screen_code = True
    log("screen code cache on")


class _ListingPath(object):
    """
    Stands in for os.path in a module: exists() answers from cached
    directory listings while active.
    """

    def __init__(self, real):
        self._real = real
        self._listings = { }
        self.active = True

    def __getattr__(self, name):
        return getattr(self._real, name)

    def exists(self, path):
        if not self.active or not isinstance(path, str):
            return self._real.exists(path)

        directory, name = self._real.split(path)

        if not name or ".." in path:
            return self._real.exists(path)

        listing = self._listings.get(directory)
        if listing is None:
            try:
                listing = set(i.lower() for i in os.listdir(directory or "."))
            except OSError:
                listing = set()
            self._listings[directory] = listing

        return name.lower() in listing


class _ListingOs(object):
    """
    Stands in for the os module in a module, with path replaced.
    """

    def __init__(self, real, path):
        self._real = real
        self.path = path

    def __getattr__(self, name):
        return getattr(self._real, name)


def listing_exists(log):
    import renpy.lexer
    import renpy.script

    path = _ListingPath(os.path)

    for module in (renpy.script, renpy.lexer):
        if module.os is os:
            module.os = _ListingOs(os, path)

    log("script loading checks files against directory listings")
    return path


def fast_transfn(log):
    import unicodedata
    import renpy.loader as loader

    if getattr(loader, "_vita_fast_transfn", False):
        return

    original_transfn = loader.transfn

    # Lower-cased names of all loose files (any search directory), mapped to
    # their paths, rebuilt when the loader's listing changes. The listing is
    # built from config.searchpath in order, the first directory holding a
    # name winning, as in transfn; its directories are basedir + the search
    # path entry, as transfn joins them.
    state = { "key" : None, "names" : None }

    def names():
        game = loader.game_files
        common = loader.common_files

        if not game and not common:
            return None

        key = (id(game), len(game), id(common), len(common))
        if state["key"] != key:
            paths = { }
            for dn, fn in game + common:
                if dn is not None:
                    paths.setdefault(unicodedata.normalize("NFC", fn.lower()), os.path.join(dn, fn))
            state["names"] = paths
            state["key"] = key

        return state["names"]

    def transfn(name):
        loose = names()

        if loose is not None and isinstance(name, str):
            n = name.lstrip("/")

            if not n.startswith(("cache/", "saves/")) and "\\" not in n and ".." not in n:
                path = loose.get(unicodedata.normalize("NFC", n.lower()))

                if path is None:
                    raise Exception("Couldn't find file '%s'." % name)

                # A stat per hit cost ~3.5 ms (462 Movie definitions check
                # their files at init: 1.6 s of boot).
                loader.add_auto(path)
                return path

        return original_transfn(name)

    loader.transfn = transfn
    loader._vita_fast_transfn = True
    log("transfn answers from the loose-file listing")


def install(renpy_base):
    path = os.path.join(renpy_base, LOG_NAME)

    def log(msg):
        try:
            with open(path, "a") as f:
                f.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    try:
        with open(path, "w") as f:
            f.write("vita_boot\n")
    except Exception:
        pass

    # Things to switch off when the boot ends.
    boot = { }

    try:
        zipimport_once(log)
    except Exception:
        log(traceback.format_exc())

    profiler = None
    if os.path.exists(os.path.join(renpy_base, PROFILE_FLAG)):
        try:
            profiler = Profiler(os.path.join(renpy_base, PROFILE_NAME))
            log("boot profiler on")
        except Exception:
            log(traceback.format_exc())

    try:
        import renpy.display.presplash as presplash

        gc.disable()
        log("automatic gc off for loading")

        original_end = presplash.end

        def end():
            gc.enable()
            log("automatic gc on; counts %r" % (gc.get_count(),))
            if "listing_path" in boot:
                boot["listing_path"].active = False
            if profiler is not None:
                try:
                    profiler.stop()
                    log("boot profile written")
                except Exception:
                    log(traceback.format_exc())
            return original_end()

        presplash.end = end

    except Exception:
        gc.enable()
        log(traceback.format_exc())

    # renpy.script exists once bootstrap has called renpy.import_all(), which
    # happens before the script loads.
    try:
        import renpy

        original_import_all = renpy.import_all

        def import_all():
            original_import_all()
            try:
                fast_transfn(log)
            except Exception:
                log(traceback.format_exc())
            try:
                fast_bytecode(log)
            except Exception:
                log(traceback.format_exc())
            try:
                screen_code_cache(log)
            except Exception:
                log(traceback.format_exc())
            try:
                boot["listing_path"] = listing_exists(log)
            except Exception:
                log(traceback.format_exc())
            try:
                freeze_after_load(log)
            except Exception:
                log(traceback.format_exc())
            try:
                lazy_story(log)
            except Exception:
                log(traceback.format_exc())

        renpy.import_all = import_all

    except Exception:
        log(traceback.format_exc())
