"""Persistent Mystery Gift RPC, using the existing RFU host and native client scripts.

The RFU event loop never waits for disk or a blocking Session operation. A single worker
issues one request at a time. A timeout poisons the connection: ident 19 responses carry
no request ID, so retrying on that link could attribute an old response to a new request.
"""

from concurrent.futures import Future, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from queue import Empty, Queue
from threading import Lock, Thread
from time import monotonic
from typing import Callable

from pokeldn.frlg.gift import mg_script, mg_server
from pokeldn.frlg.gift.host_mystery_gift import HostMysteryGiftEngine
from pokeldn.frlg.gift.mystery_gift import MG_LINKID_RAM_SCRIPT, MG_LINKID_RESPONSE
from pokeldn.frlg.rom import buffer_script as bs
from . import LocalizationError, integer
from .session import Session
from .transport import BufferTransport, Observation


class GiftConnectionClosed(LocalizationError):
    """Reconnect and re-identify before any further request, including a retry."""


@dataclass
class Request:
    sequence: int
    kind: str
    arguments: tuple
    future: Future = field(default_factory=Future)
    progress_at: float = field(default_factory=monotonic)
    on_chunk: Callable[[int, int], None] | None = None


class GiftBridge:
    def __init__(self, timeout=30.0):
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 600:
            raise LocalizationError("RPC timeout must be 0..600 seconds")
        self.timeout = timeout
        self.requests = Queue()
        self._lock = Lock()
        self._pending = None
        self._closed = None
        self._sequence = 0

    def exchange(self, kind, *arguments, on_chunk=None):
        with self._lock:
            if self._closed is not None:
                raise self._closed
            if self._pending is not None:
                raise LocalizationError("Mystery Gift RPC allows one outstanding request")
            self._sequence += 1
            request = Request(self._sequence, kind, arguments, on_chunk=on_chunk)
            self._pending = request
            self.requests.put_nowait(request)
        try:
            while True:
                with self._lock:
                    remaining = self.timeout - (monotonic() - request.progress_at)
                try:
                    return request.future.result(timeout=max(0, remaining))
                except FutureTimeout:
                    with self._lock:
                        idle = monotonic() - request.progress_at
                    if idle >= self.timeout:
                        self.abort(f"Mystery Gift RPC {request.sequence} timed out; reconnect before retry")
                        raise self._closed from None
        finally:
            with self._lock:
                if self._pending is request:
                    self._pending = None

    def take(self):
        with self._lock:
            if self._closed is not None:
                return None
            try:
                return self.requests.get_nowait()
            except Empty:
                return None

    def complete(self, request, value):
        with self._lock:
            if self._closed is not None or self._pending is not request or request.future.done():
                return False
            request.future.set_result(value)
            return True

    def progress(self, request):
        with self._lock:
            if self._closed is not None or self._pending is not request or request.future.done():
                return False
            request.progress_at = monotonic()
            return True

    def abort(self, reason):
        with self._lock:
            if self._closed is None:
                self._closed = GiftConnectionClosed(str(reason))
            if self._pending is not None and not self._pending.future.done():
                self._pending.future.set_exception(self._closed)

    @property
    def closed(self):
        return self._closed is not None


class GiftTransport(BufferTransport):
    def __init__(self, bridge, profile, *, max_dump_blocks=4):
        integer(max_dump_blocks, "maximum dump blocks", 1, mg_script.MAX_DUMP_BLOCKS)
        self.bridge, self.profile = bridge, profile
        # Four blocks reproduce the retail-tested 4 KiB exchange. The protocol can
        # encode 32, but its longer client script can stall a slow radio before any reply.
        self.max_dump_blocks = max_dump_blocks
        super().__init__(lambda: bridge.exchange("observe"), self._read, self._run_code)

    def _read(self, address, size, *, on_chunk=None):
        code = bs.build_memory_dump(address, size, build=self.profile.identity.game_code)
        return self.bridge.exchange("dump", code, size, 1, on_chunk=on_chunk)

    def read_memory(self, address, size, *, progress=lambda done, total: None):
        integer(size, "read size", 1)
        result = bytearray()
        while len(result) < size:
            at, remaining = address + len(result), size - len(result)

            def report_chunk(done, total):
                progress(len(result) + done, size)

            blocks = min(remaining // 1024, self.max_dump_blocks)
            if blocks > 1:
                code = bs.build_memory_dump_multi(at, size=1024, blocks=blocks,
                                                  build=self.profile.identity.game_code)
                chunk = self.bridge.exchange("dump", code, 1024, blocks, on_chunk=report_chunk)
                count = blocks * 1024
            else:
                count = min(remaining, 1024)
                chunk = self._read(at, count, on_chunk=report_chunk)
            if not isinstance(chunk, bytes) or len(chunk) != count:
                raise LocalizationError("short/invalid batched memory response")
            result.extend(chunk)
        return bytes(result)

    def _run_code(self, code):
        bs.validate(code)
        return int.from_bytes(self.bridge.exchange("run", bytes(code), 4, 1), "little")


class LocalizationGiftServer(mg_server.MysteryGiftServer):
    """One game-data handshake and any number of run/dump transactions, then no-save exit."""

    def __init__(self, operation, *, log=lambda *args: None):
        self.operation = operation
        self.inflight = None
        messages = {"probe": ("Read-only probe complete.\nReturn to the game.",
                              "Read stopped.\nReturn to the menu."),
                    "display": ("Chinese display finished.\nPress A to return.",
                                "Display test stopped.\nReturn to the menu.")}
        success, failure = messages.get(operation.operation,
                                        ("ROM pages verified.\nReturn to the game.",
                                         "ROM load stopped.\nKeep the recovery journal."))
        super().__init__(buffer_code=bs.payload(bs.TRAINER_ID_PROBE), log=log,
                         buffer_success_message=success, buffer_failure_message=failure,
                         script=(*mg_server._GAME_DATA_PREFIX,
                                 ("SVR_LOCALIZATION_START",), ("SVR_LOCALIZATION_NEXT",)))

    def run(self):
        # A wait is a yield to RFU, not an operation result or a cached action.
        if self.action == ("wait",):
            if self.operation.bridge.requests.empty() and not self.operation.result.done():
                return self.action
            self.action = None
        return super().run()

    def on_sent(self):
        super().on_sent()
        if self.inflight is not None:
            # Uploading the client script, cursor reset, and payload is real progress
            # before the first reply. Repeated idle frames never refresh this deadline.
            self.operation.bridge.progress(self.inflight)

    def _finish(self):
        self.buffer_matched = (self.operation.result.done()
                               and self.operation.result.exception() is None)
        if self.operation.result.done():
            self.operation.bridge.abort("localization operation finished")
        # This tail displays our message and closes without requesting a save. Host-side
        # operation.result carries completion; gift_sent remains false because no gift was sent.
        self._goto(mg_server._SCRIPT_BUFFER_FAILURE)

    def _do_svr_localization_start(self):
        try:
            data, identity = self.game_data, self.operation.profile.identity
            if (data.game_code != identity.game_code.encode("ascii")
                    or data.software_version != identity.revision):
                raise LocalizationError("console game code/revision does not match localization profile")
            self.operation.start(data)
        except Exception as exc:
            self.operation.reject(exc)
            self._finish()

    def _do_svr_check_game_data(self):
        super()._do_svr_check_game_data()
        if not self.param:
            self.operation.reject(LocalizationError("console Mystery Gift game data is invalid"))

    def _do_svr_localization_next(self):
        request = self.operation.bridge.take()
        if request is None:
            if self.operation.result.done():
                self._finish()
            else:
                self.cmdidx -= 1
                self.action = ("wait",)
            return
        self.inflight = request
        try:
            if request.kind == "observe":
                observation = self.operation.observer(self.game_data)
                if not isinstance(observation, Observation):
                    raise LocalizationError("host did not measure wrapper identity/execution context")
                if (observation.identity.game_code.encode("ascii") != self.game_data.game_code
                        or observation.identity.revision != self.game_data.software_version):
                    raise LocalizationError("host observation conflicts with console game data")
                self.operation.bridge.complete(request, observation)
                self.inflight = None
                self.cmdidx -= 1
                return
            if request.kind not in ("run", "dump"):
                raise LocalizationError("unknown localization RPC request")
            self.buffer_code, self._response_size, self._response_blocks = request.arguments
            self._response_chunks = []
            script = (mg_script.client_script_dump_memory(self._response_blocks) if request.kind == "dump"
                      else mg_script.CLIENT_SCRIPT_RUN_BUFFER)
            reset = ()
            if self._response_blocks > 1:
                # Consecutive multi-dump payloads otherwise inherit the previous cursor
                # from client->param, even when their base address has changed.
                script = mg_script.client_script((mg_script.CLI_RECV, MG_LINKID_RAM_SCRIPT),
                                                  mg_script.CLI_RUN_BUFFER_SCRIPT) + script
                reset = (("SVR_LOCALIZATION_RESET",), (mg_server.SVR_SEND,))
            self._goto(((mg_server.SVR_LOAD_CLIENT_SCRIPT, script), (mg_server.SVR_SEND,),
                        *reset,
                        (mg_server.SVR_LOAD_BUFFER_SCRIPT,), (mg_server.SVR_SEND,),
                        *[step for _ in range(self._response_blocks)
                          for step in ((mg_server.SVR_RECV, MG_LINKID_RESPONSE),
                                       ("SVR_LOCALIZATION_RESPONSE",))],
                        ("SVR_LOCALIZATION_NEXT",)))
        except Exception as exc:
            self.abort(exc)
            self._finish()

    def _do_svr_localization_reset(self):
        reset = bs.payload("client-param-reset")
        self._loaded = (MG_LINKID_RAM_SCRIPT, reset, len(reset))

    def _do_svr_localization_response(self):
        request = self.inflight
        if request is None or len(self._received) != self._response_size:
            self.abort("short/unexpected Mystery Gift RPC response")
            self._finish()
            return
        if not self.operation.bridge.progress(request):
            self.abort("late block on a closed Mystery Gift connection")
            self._finish()
            return
        self._response_chunks.append(bytes(self._received))
        if request.on_chunk is not None:
            try:
                request.on_chunk(len(self._response_chunks) * self._response_size,
                                 self._response_blocks * self._response_size)
            except Exception as exc:
                self.abort(exc)
                self._finish()
                return
        self.trace.append(("localization_block", request.sequence, len(self._response_chunks),
                           len(self._received)))
        if len(self._response_chunks) < self._response_blocks:
            return
        response = b"".join(self._response_chunks)
        if not self.operation.bridge.complete(request, response):
            self.abort("late response on a closed Mystery Gift connection")
            self._finish()
            return
        self.trace.append(("localization_response", request.sequence, request.kind, len(response)))
        self.inflight = None

    def abort(self, reason):
        error = reason if isinstance(reason, BaseException) else GiftConnectionClosed(str(reason))
        self.operation.reject(error)

    @property
    def connection_lost(self):
        return self.inflight is not None and self.operation.bridge.closed


class LocalizationGiftOperation:
    """Compose a Session with the existing host engine. Default operation is read-only.

    observer(game_data) must measure the wrapper and execution context from the host;
    filling them from the profile is only acceptable in an explicitly synthetic test.
    """

    def __init__(self, profile, patch, base, journal_path, observer, *, operation="inspect",
                 timeout=30.0, log=lambda *args: None):
        if operation not in ("inspect", "load", "restore"):
            raise LocalizationError("operation must be inspect, load or restore")
        if not callable(observer):
            raise LocalizationError("a host observation callback is required")
        self.profile, self.observer, self.operation = profile, observer, operation
        self.bridge = GiftBridge(timeout)
        self.transport = GiftTransport(self.bridge, profile)
        self.session = Session(profile, patch, base, self.transport, journal_path)
        self.result = Future()
        self.worker = None
        self.server = LocalizationGiftServer(self, log=log)

    def start(self, game_data):
        if self.worker is not None or self.result.done():
            raise LocalizationError("localization operation already started")
        def work():
            try:
                result = self._perform()
            except BaseException as exc:
                self.result.set_exception(exc)
            else:
                self.result.set_result(result)
        self.worker = Thread(target=work, name="frlg-localization", daemon=True)
        self.worker.start()

    def _perform(self):
        if self.operation == "inspect":
            _, states = self.session.inspect()
            return {"state": "IDENTIFIED", "read_only": True, "pages": states}
        return getattr(self.session, "apply" if self.operation == "load" else "restore")()

    def reject(self, reason):
        self.bridge.abort(reason)
        if self.worker is None and not self.result.done():
            self.result.set_exception(reason)

    def make_engine(self, **kwargs):
        return HostMysteryGiftEngine(server=self.server, **kwargs)

    def close(self):
        self.reject(GiftConnectionClosed("localization host closed"))
        if self.worker is not None:
            self.worker.join(timeout=2.0)
