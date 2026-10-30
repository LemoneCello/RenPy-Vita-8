# Temporary: log a stage breakdown of every frame slower than 100 ms to
# log.txt. Diagnostic only; remove from the Vita after use.
init python:
    config.profile = True
    config.profile_time = 0.1
