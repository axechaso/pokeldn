"""Controller installation and input using the upstream pokeldn controller protocol."""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import flet as ft

from gui import board, theme as t
from gui.views.widgets import Log
from pokeldn.app import runner
from pokeldn.app.paths import SESSION
from pokeldn.pad import macro, service


class ControllerView:
    def __init__(self, app):
        self.app = app
        self.client = None
        self.child = None
        self.visible = False
        self.working = False
        self.connected = False
        self.status = {}
        self.message = "Choose a controller board, then connect."
        self.sender = ThreadPoolExecutor(max_workers=1)
        self.port = t.dropdown([], None)
        self.loader = ft.Checkbox(label="Already in ROM loader", value=False)
        self.hold = t.field("Press duration (ms)", value="100", width=170)
        self.macro_text = ""
        self.macro_name = "No macro selected"
        self.log = Log(app.page, "Controller and install output appears here.")
        self.body = ft.ListView(spacing=14, padding=ft.Padding(4, 0, 4, 24), expand=True)
        self.control = ft.Row([
            self.body,
            t.panel(ft.Column([t.panel_header("Controller activity"),
                               ft.Container(self.log.control, padding=14, expand=True)],
                              spacing=0, expand=True), width=380),
        ], spacing=14, expand=True, vertical_alignment=ft.CrossAxisAlignment.STRETCH)
        self.scan()

    def enter(self, **_):
        self.scan()
        if not self.visible:
            self.visible = True
            threading.Thread(target=self._poll, daemon=True).start()

    def leave(self):
        self.visible = False
        if self.connected:
            self._submit(lambda: self.client.call("send", report=macro.NEUTRAL.hex()))

    def scan(self, e=None):
        ports = [p for p in board.ports() if p.supported]
        self.port.options = [ft.DropdownOption(key=p.device, text=f"{p.device} — {p.bridge}") for p in ports]
        if self.port.value not in [p.device for p in ports]:
            self.port.value = ports[0].device if ports else None
        self.render()
        if e is not None:
            self.control.update()

    def _poll(self):
        while self.visible:
            time.sleep(2)
            if self.visible and self.connected and not self.working:
                self._submit(self._read_status)

    def _read_status(self):
        reply = self.client.call("status")
        self.connected = bool(reply.get("connected"))
        self.status = reply.get("status", {})
        if not self.connected:
            self.message = "Controller disconnected. Check its power and press Connect."

    def _submit(self, action):
        if self.working:
            return
        self.working = True

        def work():
            try:
                action()
            except Exception as error:
                self.message = str(error) or type(error).__name__
                self.log.add(f"[app] {self.message}")
                if isinstance(error, (OSError, service.ServiceError)):
                    self.connected = False
            finally:
                self.working = False
                if self.visible:
                    self.app.ui(self._redraw)

        self.sender.submit(work)

    def _redraw(self):
        self.render()
        self.control.update()

    def connect(self, e=None):
        selector = f"serial {self.port.value or ''}"
        port = self.port.value
        if not port:
            self.message = "Choose the classic ESP32's serial port."
            self._redraw()
            return
        if self.app.busy and port == self.app.radio_port():
            self.message = "This port is running a wireless session. Choose a separate controller board."
            self._redraw()
            return

        def work():
            SESSION.mkdir(parents=True, exist_ok=True)
            if not service.running():
                env = dict(os.environ, POKELDN_MANAGED_RUN="1")
                env.pop("POKELDN_RADIO", None)
                args = ["--serial", port]
                self.child = runner.Process(["--module", "pokeldn.pad.service", *args], str(SESSION), env,
                                            self.log.add, lambda code: None)
                for _ in range(100):
                    if service.running() or not self.child.running:
                        break
                    time.sleep(0.1)
            if self.client is not None:
                self.client.close()
            self.client = service.Client()
            reply = self.client.call("status")
            if reply.get("code") != service.CODE or reply.get("selector") != selector:
                raise service.ServiceError("A controller service is using another connection or version. "
                                           "Press Disconnect before connecting this board.")
            self.client.call("connect")
            self._read_status()
            self.message = "Controller connected."

        self.message = "Connecting to the controller..."
        self._submit(work)
        self._redraw()

    def _disconnect(self):
        if self.client is None and service.running():
            self.client = service.Client()
        if self.client is not None:
            try:
                self.client.call("disconnect")
                self.client.call("quit")
            finally:
                self.client.close()
                self.client = None
        self.connected, self.status = False, {}
        self.message = "Controller disconnected."

    def disconnect(self, e=None):
        self._submit(self._disconnect)

    def tap(self, keys=(), left=(0, 0), right=(0, 0)):
        try:
            ms = int(self.hold.value)
            if not 1 <= ms <= 10000:
                raise ValueError()
        except ValueError:
            self.message = "Press duration must be 1 to 10000 ms."
            self._redraw()
            return
        report = macro.state(keys, left, right)
        self._submit(lambda: self.client.call("tap", report=report.hex(), ms=ms))

    def stop(self, e=None):
        self._submit(lambda: self.client.call("stop"))

    def pair(self, e=None):
        def work():
            self.client.call("pair")
            self.status = {}
            self.message = "Previous Bluetooth host unplugged. Start controller inquiry on BlueRetro to pair."
        self._submit(work)

    async def choose_macro(self, e):
        files = await self.app.picker.pick_files(allowed_extensions=["pokemacro"],
                                                 file_type=ft.FilePickerFileType.CUSTOM)
        if not files or not files[0].path:
            return
        try:
            text = Path(files[0].path).read_text(encoding="utf-8")
            parsed = macro.loads(text)
            macro.compile_macro(parsed)
            self.macro_text, self.macro_name = text, parsed.name
        except (OSError, macro.MacroError) as error:
            self.message = str(error)
        self._redraw()

    def play(self, e=None):
        text = self.macro_text
        self._submit(lambda: self.client.call("play", macro=text))

    def install(self, e=None):
        port, loader = self.port.value, bool(self.loader.value)
        if self.app.busy or not port:
            return
        self.app.board_busy = True

        def work():
            try:
                self._disconnect()
                # quit is answered just before the service exits and releases its serial handle.
                for _ in range(30):
                    if not service.running():
                        break
                    time.sleep(0.1)
                SESSION.mkdir(parents=True, exist_ok=True)
                env = dict(os.environ, NO_COLOR="1", PYTHONUNBUFFERED="1")
                env.pop("POKELDN_RADIO", None)
                args = ["--module", "pokeldn.firmware", "--chip", "esp32", "--flash", "--port", port]
                if loader:
                    args.append("--from-loader")
                self.app.process_label = "controller flash"
                self.app.process = runner.Process(args, str(SESSION), env, self.log.add, self._installed)
                self.message = "Installing controller firmware. Follow progress in Controller activity."
            finally:
                self.app.board_busy = False

        self._submit(work)
        self._redraw()

    def _installed(self, code):
        self.message = ("Controller firmware installed. Connect it using the instructions above." if code == 0
                        else "Installation failed. Check the activity log and try holding BOOT.")
        self.log.add(f"[app] {self.message}")
        if self.visible:
            self.app.ui(self._redraw)

    def render(self):
        ready = self.connected and not self.working
        disabled = not ready
        self.port.disabled = self.working or self.connected
        status = self.status
        progress = (f"Firmware {status.get('version', '?')} · "
                    f"Bluetooth host {'ready' if status.get('mounted') else 'not ready'} · "
                    f"{'Playing' if status.get('playing') else 'Idle'} · "
                    f"Entry {status.get('index', 0)}/{status.get('count', 0)} · "
                    f"Loops {status.get('loops_done', 0)}")

        def button(key):
            return t.secondary_button(key, lambda e, k=key: self.tap([k]), disabled=disabled, width=92)

        self.body.controls = [
            t.card("Controller connection", ft.Column([
                t.text("Classic ESP32 · USB serial to this computer · Bluetooth to BlueRetro", 13),
                ft.Row([self.port, t.secondary_button("Refresh ports", self.scan,
                           disabled=self.working or self.connected)], spacing=8),
                t.text("Leave the ESP32 on this computer's USB port. Connect here, then use Pair with BlueRetro "
                       "and start controller inquiry on the BlueRetro receiver. It sees a Pro Controller.", 12, t.MUTED),
                ft.Row([t.button("Connect", self.connect, disabled=self.working or self.connected),
                        t.secondary_button("Disconnect", self.disconnect, disabled=self.working)], spacing=8),
                t.secondary_button("Pair with BlueRetro / new host", self.pair, disabled=not ready),
                t.text(self.message, 12, t.BLUE),
                t.text(progress if self.connected else "", 12, t.MUTED),
            ], spacing=10)),
            t.card("Buttons", ft.Column([
                self.hold,
                ft.Row([button(k) for k in ("UP", "DOWN", "LEFT", "RIGHT")], wrap=True),
                ft.Row([button(k) for k in ("A", "B", "X", "Y")], wrap=True),
                ft.Row([button(k) for k in ("L", "R", "ZL", "ZR", "LSTICK", "RSTICK")], wrap=True),
                ft.Row([button(k) for k in ("MINUS", "PLUS", "HOME", "CAPTURE")], wrap=True),
                ft.Row([t.secondary_button(f"L stick {name}",
                           lambda e, xy=xy: self.tap(left=xy), disabled=disabled)
                        for name, xy in (("↑", (0, 1)), ("↓", (0, -1)), ("←", (-1, 0)), ("→", (1, 0)))], wrap=True),
                ft.Row([t.secondary_button(f"R stick {name}",
                           lambda e, xy=xy: self.tap(right=xy), disabled=disabled)
                        for name, xy in (("↑", (0, 1)), ("↓", (0, -1)), ("←", (-1, 0)), ("→", (1, 0)))], wrap=True),
                t.button("Stop macro and release buttons", self.stop, color=t.RED, disabled=disabled),
            ], spacing=8)),
            t.card("Macro playback", ft.Column([
                t.text(self.macro_name, 13),
                ft.Row([t.secondary_button("Open .pokemacro", self.choose_macro, disabled=self.working),
                        t.button("Play on board", self.play, disabled=disabled or not self.macro_text)], wrap=True),
            ], spacing=10), "The board runs the macro on its own clock; Stop ends playback."),
            t.card("Install controller firmware", ft.Column([
                self.loader,
                t.text("Select a classic ESP32 port above. The app verifies the built controller image "
                       "and its SHA-256 before flashing. If connecting stalls, hold BOOT until writing starts.",
                       12, t.MUTED),
                t.button("Install controller firmware", self.install, "zap",
                         disabled=self.working or self.app.busy or not self.port.value),
            ], spacing=10), "Installation replaces this board's firmware and clears its pairing. "
               "Use a separate board for wireless trades. This firmware requires a classic ESP32."),
        ]
