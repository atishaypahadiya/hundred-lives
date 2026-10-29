"""PID 1 of an agent container. Reaps orphans (zombies would otherwise fill the process limit)
and, on SIGUSR1, kills every other process. Neither needs a fork, so it works even when a fork
bomb has used up the process table. The host sends SIGUSR1 with `docker kill --signal USR1`."""
import os
import signal
import time


def reap(*_):
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return
        if pid == 0:
            return


def kill_all(*_):
    for _ in range(20):  # a fork bomb refills the table between sweeps
        try:
            os.kill(-1, signal.SIGKILL)  # everything we may signal, except PID 1
        except ProcessLookupError:
            break
        reap()
        time.sleep(0.05)
    reap()


signal.signal(signal.SIGCHLD, reap)
signal.signal(signal.SIGUSR1, kill_all)
signal.signal(signal.SIGTERM, lambda *_: os._exit(0))
while True:
    signal.pause()
