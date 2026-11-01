# Discord Rich Presence does nothing on the Vita.
#
# Games ship the discord_rpc package (python-packages/) to show what the
# player is doing in Discord. On anything but Windows it connects through a
# Unix socket, which the Vita doesn't have: socket.AF_UNIX is missing, and
# Hero's Harem Guild stopped on its splash screen with an AttributeError.
# On the Vita, `import discord_rpc` gets a module whose every function does
# nothing instead. Early, so it's in place before any game code imports it.

init -1000000 python:

    def _vita_install_discord_stub():
        import sys
        import types

        if not getattr(renpy, "vita", False):
            return

        stub = types.ModuleType("discord_rpc")
        stub.__getattr__ = lambda name: (lambda *args, **kwargs: None)
        sys.modules["discord_rpc"] = stub

    _vita_install_discord_stub()
