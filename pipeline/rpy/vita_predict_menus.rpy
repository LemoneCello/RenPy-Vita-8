# Ren'Py preloads the images of screens that on-screen buttons can open
# (ShowMenu, Show, ...) during idle time, so menus open quickly. But it does
# that last, after predicting the next statements (~32, each with a say-screen
# prediction), and starts over at every interaction. On the Vita that order
# never gets through at a normal reading pace: the test game's character menu (21
# full-screen images) took 5-6 s to open because none of its images had been
# preloaded.
#
# This runs first in each interaction's prediction: it collects the screens
# reachable from on-screen buttons the way Ren'Py does, and predicts each one
# not yet predicted this session, one per idle step and only in "expensive"
# idle frames (nothing needs redrawing), before Ren'Py's own prediction
# continues as usual. Their images go to Ren'Py's preload thread and stay in
# the image cache until it needs the room. Once every reachable screen has
# been predicted, this does nothing more.
#
# Costs: each such screen is predicted once in the background; a save screen
# prediction can take ~0.9 s on the Vita, during which input waits.
#
# The main menu's images are collected at the start of the game (running the
# screen once in prediction mode) and queued for preloading at every
# interaction until it first appears, so they load while the splash screens
# wait for input.
# Without that, the test game's main menu spent its first frame (2.3-2.7 s) loading
# ~25 images, and its entrance animations, which keep time while that frame
# renders, jumped to their ends.
#
# Active on the Vita; VITA_PREDICT_MENUS_TEST=1 turns it on elsewhere.

init -1500 python:

    def _vita_install_predict_menus():
        import os

        if not (getattr(renpy, "vita", False) or os.environ.get("VITA_PREDICT_MENUS_TEST")):
            return

        predict = renpy.display.predict

        if getattr(predict, "_vita_predict_menus", False):
            return

        predict._vita_predict_menus = True

        original_coroutine = predict.prediction_coroutine

        # Screens predicted this session, by name and arguments.
        done = set()

        def key(t):
            name, args, kwargs = t
            try:
                return (name, repr(args), repr(sorted(kwargs.items())))
            except Exception:
                return (name, id(args), id(kwargs))

        def reachable(root_widget):
            # The screens predict_one_action() asks for, without leaving them
            # in Ren'Py's own list (it collects them again later).
            screens = predict.screens
            n = len(screens)

            predict.predicting = True
            try:
                root_widget.visit_all(lambda i : i.predict_one_action())
            except Exception:
                pass
            finally:
                predict.predicting = False

            rv = screens[n:]
            del screens[n:]
            return rv

        def prediction_coroutine(root_widget):
            inner = original_coroutine(root_widget)

            # Ren'Py's start (cleans the cache) and its first yield.
            yield inner.send(None)

            pending = [ t for t in reachable(root_widget) if not t[0].startswith("_") and key(t) not in done ]

            for t in pending:

                while not (yield False):
                    continue

                k = key(t)
                if k in done:
                    continue
                done.add(k)

                name, args, kwargs = t

                predict.image = renpy.display.im.cache.preload_image
                predict.predicting = True
                try:
                    renpy.display.screen.predict_screen(name, *args, **kwargs)
                except Exception:
                    pass
                finally:
                    predict.predicting = False

            yield from inner

        predict.prediction_coroutine = prediction_coroutine

        # The main menu's images, collected once at the start, requeued for
        # preloading at every interaction until the main menu first appears.
        # (Ren'Py empties the preload queue at each interaction, and the
        # splash screens rarely leave idle time for screen prediction.)
        # preload_image skips images already in the cache.
        main_menu = { "images" : None }

        def start():
            if not renpy.has_screen("main_menu"):
                return

            images = [ ]
            old_image = predict.image
            predict.image = images.append
            predict.predicting = True
            try:
                renpy.display.screen.predict_screen("main_menu")
            except Exception:
                pass
            finally:
                predict.predicting = False
                predict.image = old_image

            main_menu["images"] = images

            for im in images:
                renpy.display.im.cache.preload_image(im)

        def interact():
            if main_menu["images"] is not None and renpy.get_screen("main_menu"):
                main_menu["images"] = None

        # Requeue right after the image cache empties its queue at each
        # interaction (prediction itself barely runs while a video plays,
        # and the test game's logo video takes ~12 s of the splash).
        Cache = renpy.display.im.Cache
        original_tick = Cache.tick

        def tick(self):
            rv = original_tick(self)
            for im in main_menu["images"] or ( ):
                self.preload_image(im)
            return rv

        Cache.tick = tick

        config.start_callbacks.append(start)
        config.interact_callbacks.append(interact)

    _vita_install_predict_menus()
