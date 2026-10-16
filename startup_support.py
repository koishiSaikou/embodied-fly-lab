"""Small shared startup helpers; no simulation imports or global configuration."""
import socket
from contextlib import contextmanager
import signal
import webbrowser


def available_port(preferred, *, automatic=True):
    if type(preferred) is not int or not 1024 <= preferred <= 65535:
        raise ValueError('Port must be an integer in [1024, 65535]')
    stop = min(65536, preferred + 50) if automatic else preferred + 1
    for port in range(preferred, stop):
        with socket.socket() as connection:
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                connection.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                connection.bind(('127.0.0.1', port))
                return port
            except OSError:
                pass
    if automatic:
        raise RuntimeError(f'No free local port in [{preferred}, {stop - 1}]; choose --port explicitly')
    raise RuntimeError(f'Port {preferred} is occupied. Existing service was not changed. Choose another --port.')


def open_dashboard(url, *, enabled=True):
    """Called by the app after its own HTTP server is listening, never by a timer."""
    if not enabled:
        return False
    try:
        opened = webbrowser.open(url, new=2)
    except Exception as error:
        print(f'Cannot open browser: {error}. Open this address manually: {url}', flush=True)
        return False
    if not opened:
        print(f'Browser did not confirm opening. Open this address manually: {url}', flush=True)
    else:
        print(f'Browser opened: {url}', flush=True)
    return bool(opened)


@contextmanager
def defer_interrupts_while_saving():
    """A Ctrl+C during final output requests closure after the current save completes."""
    previous = signal.getsignal(signal.SIGINT)
    state = {'requested': False}

    def finishing(signum, frame):
        if not state['requested']:
            state['requested'] = True
            print('Stop requested while saving; finishing experiment records first...', flush=True)

    signal.signal(signal.SIGINT, finishing)
    try:
        yield state
    finally:
        signal.signal(signal.SIGINT, previous)
