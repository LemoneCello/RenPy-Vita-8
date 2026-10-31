"""Stand-in for the fcntl module, which the Vita's Python lacks (newlib has
no file locks). Games use it to stop a second copy running (a lock file
held with lockf/flock); only one app runs at a time on the Vita, so locks
always succeed here. Anything else raises OSError, as an unsupported call
would."""

import errno

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8

F_DUPFD = 0
F_GETFD = 1
F_SETFD = 2
F_GETFL = 3
F_SETFL = 4
F_GETLK = 7
F_SETLK = 8
F_SETLKW = 9
FD_CLOEXEC = 1


def lockf(fd, cmd, len=0, start=0, whence=0):
    return None


def flock(fd, operation):
    return None


def fcntl(fd, cmd, arg=0):
    raise OSError(errno.ENOSYS, "fcntl isn't available on the Vita")


def ioctl(fd, request, arg=0, mutate_flag=True):
    raise OSError(errno.ENOSYS, "ioctl isn't available on the Vita")
