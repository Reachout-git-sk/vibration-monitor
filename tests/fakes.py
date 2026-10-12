class FakeSerial:
    """Stands in for a pySerial port: records writes, replays scripted replies.

    `replies` maps an exact command (without newline) to the bytes readline()
    should return, or to None to simulate a timeout (readline returns b"").
    """

    def __init__(self, replies=None, fail_on_write=False):
        self.replies = dict(replies or {})
        self.written = []
        self.is_open = True
        self.fail_on_write = fail_on_write
        self._pending = b""
        self.flushed_input = 0

    def write(self, data):
        if self.fail_on_write:
            raise OSError("device unplugged")
        cmd = data.decode("ascii").rstrip("\n")
        self.written.append(cmd)
        reply = self.replies.get(cmd, b"ERR UNKNOWN_COMMAND\n")
        self._pending = reply or b""

    def flush(self):
        pass

    def readline(self):
        out, self._pending = self._pending, b""
        return out

    def reset_input_buffer(self):
        self.flushed_input += 1
        self._pending = b""

    def close(self):
        self.is_open = False


def opener_for(fake):
    def opener(url, **kwargs):
        fake.opened_with = (url, kwargs)
        return fake
    return opener
