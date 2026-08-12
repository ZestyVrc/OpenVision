"""
MIT License

Copyright DragonDreams GmbH 2024
Copyright modifications 2026

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

"""Windows-only Toga UI and IP-camera server for the Lip Camera."""


import asyncio as aio
from io import BytesIO
import ipaddress
import json
import logging
import multiprocessing as mp
from pathlib import Path
import sys
import traceback

from PIL import Image
import toga
import toga.style.pack as tp

from camera_pipeline import CameraPipeline, CameraPipelineError
from camera_server import MjpegCameraServer


class TestApp(toga.App):
    """Windows-only lip camera activation, preview and MJPEG streaming app."""

    # Stream dimensions are used for MJPEG delivery and must remain unchanged.
    STREAM_WIDTH = 320
    STREAM_HEIGHT = 480

    # Preview dimensions affect only the on-screen ImageView.
    PREVIEW_WIDTH = 256
    PREVIEW_HEIGHT = 384

    LANGUAGE_NAMES = {
        "English": "en",
        "中文": "zh",
        "한국어": "ko",
        "日本語": "ja",
    }
    LANGUAGE_DISPLAY_NAMES = {code: name for name, code in LANGUAGE_NAMES.items()}
    TRANSLATIONS = {
        "en": {
            "language": "Langage",
            "device_number": "Device index",
            "camera_enabled": "Enable camera",
            "camera_info_empty": "Camera: -",
            "camera_info": "Resolution: {width}x{height} / {fps:.1f} FPS",
            "bind_ipv4": "Bind IPv4 (default: 127.0.0.1)",
            "port": "Port",
            "server_start": "Start server",
            "server_stop": "Stop server",
            "server_auto_start": "Start server automatically when the app opens",
            "stream_url": "Video URL",
            "stream_url_placeholder": "The URL is shown after the server starts",
            "preview": "Preview",
            "preview_stop": "Stop preview",
            "control_title": "Camera Control",
            "stream_control_failed": "Camera opened, but camera stream control failed.",
            "server_title": "IP Camera Server",
            "invalid_network_settings": "The IPv4 address or port number is invalid: {error}",
            "bind_ip_not_assigned": (
                "Bind IPv4 {bind_ip} is not assigned to this PC. Enter an IPv4 address assigned to this PC.\n\n{error}"
            ),
            "port_in_use": (
                "Port {port} is already used by another application. "
                "Specify a different port number.\n\n{error}"
            ),
            "server_start_failed": "Failed to start the server: {error}",
            "open_device_title": "Open Device",
            "open_device_failed": "Failed opening device.",
            "extension_control_failed": (
                "Camera opened, but camera stream control failed. See lip_camera_server.log."
            ),
        },
        "zh": {
            "language": "Langage",
            "device_number": "设备编号",
            "camera_enabled": "启用摄像头",
            "camera_info_empty": "摄像头: -",
            "camera_info": "分辨率: {width}x{height} / {fps:.1f} FPS",
            "bind_ipv4": "监听 IPv4（默认：127.0.0.1）",
            "port": "端口",
            "server_start": "启动服务器",
            "server_stop": "停止服务器",
            "server_auto_start": "应用启动时自动启动服务器",
            "stream_url": "视频 URL",
            "stream_url_placeholder": "服务器启动后显示 URL",
            "preview": "预览",
            "preview_stop": "停止预览",
            "control_title": "摄像头控制",
            "stream_control_failed": "摄像头已打开，但视频流控制失败。",
            "server_title": "IP 摄像头服务器",
            "invalid_network_settings": "IPv4 地址或端口号无效：{error}",
            "bind_ip_not_assigned": (
                "监听 IPv4 {bind_ip} 未分配给此电脑。请输入分配给此电脑的 IPv4 地址。\n\n{error}"
            ),
            "port_in_use": "端口 {port} 已被其他应用使用。请指定其他端口号。\n\n{error}",
            "server_start_failed": "无法启动服务器：{error}",
            "open_device_title": "打开设备",
            "open_device_failed": "打开设备失败。",
            "extension_control_failed": "摄像头已打开，但视频流控制失败。请查看 lip_camera_server.log。",
        },
        "ko": {
            "language": "Langage",
            "device_number": "장치 번호",
            "camera_enabled": "카메라 사용",
            "camera_info_empty": "카메라: -",
            "camera_info": "해상도: {width}x{height} / {fps:.1f} FPS",
            "bind_ipv4": "수신 IPv4 (기본값: 127.0.0.1)",
            "port": "포트",
            "server_start": "서버 시작",
            "server_stop": "서버 중지",
            "server_auto_start": "앱 시작 시 서버 자동 시작",
            "stream_url": "영상 URL",
            "stream_url_placeholder": "서버를 시작하면 URL이 표시됩니다",
            "preview": "미리보기",
            "preview_stop": "미리보기 중지",
            "control_title": "카메라 제어",
            "stream_control_failed": "카메라는 열렸지만 카메라 스트림 제어에 실패했습니다.",
            "server_title": "IP 카메라 서버",
            "invalid_network_settings": "IPv4 주소 또는 포트 번호가 올바르지 않습니다: {error}",
            "bind_ip_not_assigned": (
                "수신 IPv4 {bind_ip}은(는) 이 PC에 할당되어 있지 않습니다. 이 PC에 할당된 IPv4 주소를 입력하십시오.\n\n{error}"
            ),
            "port_in_use": "포트 {port}은(는) 이미 다른 앱에서 사용 중입니다. 다른 포트를 지정하십시오.\n\n{error}",
            "server_start_failed": "서버를 시작할 수 없습니다: {error}",
            "open_device_title": "장치 열기",
            "open_device_failed": "장치를 열지 못했습니다.",
            "extension_control_failed": "카메라는 열렸지만 카메라 스트림 제어에 실패했습니다. lip_camera_server.log를 확인하십시오.",
        },
        "ja": {
            "language": "Langage",
            "device_number": "デバイス番号",
            "camera_enabled": "カメラ有効",
            "camera_info_empty": "Camera: -",
            "camera_info": "解像度: {width}x{height} / {fps:.1f} FPS",
            "bind_ipv4": "待受IPv4（初期値: 127.0.0.1）",
            "port": "ポート",
            "server_start": "サーバー開始",
            "server_stop": "サーバー停止",
            "server_auto_start": "アプリ起動時にサーバーを自動開始",
            "stream_url": "映像URL",
            "stream_url_placeholder": "サーバー開始後にURLを表示します",
            "preview": "プレビュー",
            "preview_stop": "プレビュー停止",
            "control_title": "Camera Control",
            "stream_control_failed": "Camera opened, but camera stream control failed.",
            "server_title": "IPカメラサーバー",
            "invalid_network_settings": "IPv4アドレスまたはポート番号が不正です: {error}",
            "bind_ip_not_assigned": (
                "待受IPv4 {bind_ip} はこのPCに割り当てられていません。"
                "このPCに割り当てられたIPv4アドレスを入力してください。\n\n{error}"
            ),
            "port_in_use": "ポート {port} は既に他のアプリで使用されています。別のポート番号を指定してください。\n\n{error}",
            "server_start_failed": "サーバーを開始できませんでした: {error}",
            "open_device_title": "Open Device",
            "open_device_failed": "Failed opening device.",
            "extension_control_failed": "Camera opened, but camera stream control failed. See lip_camera_server.log.",
        },
    }

    def __init__(self) -> None:
        super().__init__(
            formal_name="Lip Camera IP Server",
            app_id="ch.dragondreams.lipcamera.ipserver",
            on_exit=self.on_exit_app,
        )
        self.camera_pipeline = CameraPipeline()
        self.ftcamera: CameraPipeline | None = None
        self.camera_server = MjpegCameraServer()
        self.preview_active = False
        self._suppress_events = False
        self._ui_loop: aio.AbstractEventLoop | None = None
        self._frame_pump_task: aio.Task | None = None
        self.logger = logging.getLogger("evcta.TestApp")

    @property
    def settings_file(self) -> Path:
        """Return the settings file beside the executable in packaged builds."""
        if getattr(sys, "frozen", False):
            app_directory = Path(sys.executable).resolve().parent
        else:
            app_directory = Path(__file__).resolve().parent

        return app_directory / "settings.json"

    def _load_settings(self) -> dict[str, object]:
        defaults: dict[str, object] = {
            "camera_index": 1,
            "enabled": False,
            "bind_ip": "127.0.0.1",
            "server_port": 8080,
            "server_auto_start": False,
            "language": "ja",
        }
        try:
            if self.settings_file.exists():
                loaded = json.loads(self.settings_file.read_text(encoding="utf-8"))
                defaults["camera_index"] = min(max(int(loaded.get("camera_index", 1)), 0), 15)
                defaults["enabled"] = bool(loaded.get("enabled", False))
                defaults["bind_ip"] = self._validated_bind_ip(str(loaded.get("bind_ip", "127.0.0.1")))
                defaults["server_port"] = self._validated_port(int(loaded.get("server_port", 8080)))
                defaults["server_auto_start"] = bool(loaded.get("server_auto_start", False))
                language = str(loaded.get("language", "ja"))
                defaults["language"] = language if language in self.TRANSLATIONS else "ja"
        except Exception:
            self.logger.error("Failed loading settings:\n%s", traceback.format_exc())
        return defaults

    def _save_settings(self) -> None:
        try:
            self.paths.config.mkdir(parents=True, exist_ok=True)
            settings = {
                "camera_index": int(self.edit_device.value),
                "enabled": bool(self.chk_enable.value),
                "bind_ip": str(self.edit_bind_ip.value).strip() or "127.0.0.1",
                "server_port": int(self.edit_server_port.value),
                "server_auto_start": bool(self.chk_server_auto_start.value),
                "language": self.language,
            }
            self.settings_file.write_text(
                json.dumps(settings, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            self.logger.error("Failed saving settings:\n%s", traceback.format_exc())

    @staticmethod
    def _validated_bind_ip(value: str) -> str:
        ip = ipaddress.ip_address(value.strip())
        if ip.version != 4:
            raise ValueError("IPv4 address is required")
        return str(ip)

    @staticmethod
    def _validated_port(value: int) -> int:
        if not 1 <= value <= 65535:
            raise ValueError("Port must be between 1 and 65535")
        return value

    def tr(self, key: str, **kwargs: object) -> str:
        """Return translated UI text for the selected language."""
        text = self.TRANSLATIONS[self.language].get(key, self.TRANSLATIONS["en"].get(key, key))
        return text.format(**kwargs)

    def _update_camera_info(self) -> None:
        """Render resolution and FPS in the currently selected language."""
        if self.ftcamera is None:
            self.lab_cam_info.text = self.tr("camera_info_empty")
            return

        info = self.camera_pipeline.camera_info
        self.lab_cam_info.text = self.tr(
            "camera_info",
            width=info.width,
            height=info.height,
            fps=info.fps,
        )

    def _apply_language(self) -> None:
        """Update all static and state-dependent text in the visible UI."""
        self.lab_language.text = self.tr("language")
        self.lab_device.text = self.tr("device_number")
        self.chk_enable.text = self.tr("camera_enabled")
        self.lab_bind_ip.text = self.tr("bind_ipv4")
        self.lab_port.text = self.tr("port")
        self.chk_server_auto_start.text = self.tr("server_auto_start")
        self.lab_stream_url.text = self.tr("stream_url")
        self.edit_stream_url.placeholder = self.tr("stream_url_placeholder")
        self._update_camera_info()
        self._set_preview_ui(self.preview_active)
        self._set_server_ui()

    def _set_enable_switch(self, value: bool, save: bool = False) -> None:
        self._suppress_events = True
        self.chk_enable.value = value
        self._suppress_events = False
        if save:
            self._save_settings()

    def _set_blank_preview(self) -> None:
        self.view_camera.image = Image.new("L", (self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT), 40)

    def _set_preview_ui(self, active: bool) -> None:
        self.preview_active = active
        self.btn_preview.text = self.tr("preview_stop") if active else self.tr("preview")
        self.btn_preview.enabled = self.ftcamera is not None
        if not active:
            self._set_blank_preview()


    def _set_server_ui(self) -> None:
        active = self.camera_server.running
        self.btn_server.text = self.tr("server_stop") if active else self.tr("server_start")
        self.edit_bind_ip.enabled = not active
        self.edit_server_port.enabled = not active
        if active:
            self.edit_stream_url.value = "http://{}:{}/stream".format(
                self.camera_server.bind_ip,
                self.camera_server.port,
            )
        else:
            self.edit_stream_url.value = ""

    def _remove_file_and_help_commands(self) -> None:
        for name in (
            "NEW",
            "OPEN",
            "SAVE",
            "SAVE_AS",
            "PREFERENCES",
            "ABOUT",
            "VISIT_HOMEPAGE",
            "REPORT_ISSUE",
            "EXIT",
        ):
            command_id = getattr(toga.Command, name, None)
            if command_id is None:
                continue
            try:
                del self.commands[command_id]
            except (KeyError, ValueError):
                pass

    async def on_language_changed(self, widget: toga.Selection) -> None:
        if self._suppress_events:
            return
        selected_name = str(widget.value)
        self.language = self.LANGUAGE_NAMES.get(selected_name, "ja")
        self._apply_language()
        self._save_settings()

    async def on_device_changed(self, widget: toga.NumberInput) -> None:
        if not self._suppress_events:
            self._save_settings()

    async def on_network_setting_changed(self, widget: toga.Widget) -> None:
        if not self._suppress_events and not self.camera_server.running:
            self._save_settings()

    async def on_switch_server_auto_start(self, widget: toga.Switch) -> None:
        if not self._suppress_events:
            self._save_settings()

    async def on_switch_enable(self, widget: toga.Switch) -> None:
        if self._suppress_events:
            return
        self._save_settings()
        if widget.value:
            opened = await self.open_ftcamera()
            if not opened:
                self._set_enable_switch(False, save=True)
        else:
            if self.camera_server.running:
                await self.stop_server()
            await self.close_ftcamera()

    async def on_button_preview(self, widget: toga.Button) -> None:
        if not self.ftcamera:
            return
        if self.preview_active:
            await self.stop_preview()
        else:
            await self.start_preview()

    async def on_button_server(self, widget: toga.Button) -> None:
        if self.camera_server.running:
            await self.stop_server()
        else:
            await self.start_server()

    async def _refresh_capture(self) -> None:
        if not self.ftcamera:
            return

        capture_required = self.preview_active or self.camera_server.running
        if capture_required:
            try:
                self.camera_pipeline.start_capture()
            except CameraPipelineError:
                self.logger.error("Failed starting isolated capture:\n%s", traceback.format_exc())
                await self.main_window.error_dialog(
                    title=self.tr("control_title"),
                    message=self.tr("stream_control_failed"),
                )
                self.preview_active = False
                self.camera_server.stop()
                self._set_preview_ui(False)
                self._set_server_ui()
                return
            self._ensure_frame_pump()
        else:
            self.camera_pipeline.stop_capture()

    async def start_preview(self) -> None:
        if not self.ftcamera or self.preview_active:
            return
        self._set_preview_ui(True)
        await self._refresh_capture()

    async def stop_preview(self) -> None:
        if not self.preview_active:
            self._set_preview_ui(False)
            return
        self._set_preview_ui(False)
        await self._refresh_capture()

    async def start_server(self) -> bool:
        try:
            configured_ip = self._validated_bind_ip(str(self.edit_bind_ip.value))
            port = self._validated_port(int(self.edit_server_port.value))
        except ValueError as error:
            await self.main_window.error_dialog(
                title=self.tr("server_title"),
                message=self.tr("invalid_network_settings", error=error),
            )
            return False

        # Use an explicitly entered IPv4 address as-is. For backwards compatibility,
        # migrate the former wildcard default from an older settings file to loopback.
        bind_ip = configured_ip
        if configured_ip == "0.0.0.0":
            bind_ip = "127.0.0.1"
            self._suppress_events = True
            self.edit_bind_ip.value = bind_ip
            self._suppress_events = False

        if not self.ftcamera:
            self._set_enable_switch(True, save=True)
            if not await self.open_ftcamera():
                self._set_enable_switch(False, save=True)
                return False

        try:
            await aio.to_thread(self.camera_server.start, bind_ip, port)
        except OSError as error:
            self.logger.error("Failed starting MJPEG server:\n%s", traceback.format_exc())
            await aio.to_thread(self.camera_server.stop)
            self._set_server_ui()

            if getattr(error, "winerror", None) == 10049:
                message = self.tr("bind_ip_not_assigned", bind_ip=bind_ip, error=error)
            elif getattr(error, "winerror", None) == 10048:
                message = self.tr("port_in_use", port=port, error=error)
            else:
                message = self.tr("server_start_failed", error=error)
            await self.main_window.error_dialog(title=self.tr("server_title"), message=message)
            return False

        await self._refresh_capture()
        self._save_settings()
        self._set_server_ui()
        return True

    async def stop_server(self) -> None:
        await aio.to_thread(self.camera_server.stop)
        await self._refresh_capture()
        self._set_server_ui()

    async def open_ftcamera(self) -> bool:
        if self.ftcamera:
            self.btn_preview.enabled = True
            return True

        self._set_blank_preview()
        try:
            self.btn_preview.enabled = False
            self.btn_server.enabled = False
            info = await self.camera_pipeline.open(int(self.edit_device.value))
            self.ftcamera = self.camera_pipeline
            self.logger.info(
                "Isolated camera opened: %sx%s %.1ffps %s %s",
                info.width,
                info.height,
                info.fps,
                info.format,
                info.device_name,
            )
            self._update_camera_info()
            self.btn_preview.enabled = True
            self._ensure_frame_pump()
        except Exception:
            self.logger.error("Failed opening isolated camera:\n%s", traceback.format_exc())
            await self.close_ftcamera()
            await self.main_window.error_dialog(
                title=self.tr("open_device_title"),
                message=self.tr("open_device_failed"),
            )
            return False
        finally:
            self.btn_server.enabled = True

        return True

    async def close_ftcamera(self) -> None:
        self.preview_active = False
        self._set_preview_ui(False)
        if self.ftcamera:
            self.camera_pipeline.stop_capture()
            await aio.to_thread(self.camera_pipeline.close)
            self.ftcamera = None
        self._update_camera_info()
        self.btn_preview.enabled = False

    def _ensure_frame_pump(self) -> None:
        if self._frame_pump_task and not self._frame_pump_task.done():
            return
        self._frame_pump_task = aio.create_task(self._frame_pump_loop())

    async def _frame_pump_loop(self) -> None:
        while self.ftcamera is not None and self.camera_pipeline.running:
            self._drain_camera_status()
            self._drain_camera_frames()
            await aio.sleep(0.02)

    def _drain_camera_status(self) -> None:
        for message in self.camera_pipeline.drain_status():
            kind = message.get("type")
            if kind == "error":
                self.logger.error("Camera worker error: %s", message.get("message"))
            elif kind == "warning":
                self.logger.warning("Camera worker warning: %s", message.get("message"))
            else:
                self.logger.info("Camera worker status: %s", message)

    def _drain_camera_frames(self) -> None:
        latest_preview: bytes | None = None
        latest_server: bytes | None = None

        for message in self.camera_pipeline.drain_frames():
            if message.get("type") != "frame":
                continue
            server_jpeg = message.get("server_jpeg")
            preview_jpeg = message.get("preview_jpeg")
            if isinstance(server_jpeg, bytes):
                latest_server = server_jpeg
            if isinstance(preview_jpeg, bytes):
                latest_preview = preview_jpeg

        if latest_server is not None and self.camera_server.running:
            self.camera_server.update_jpeg_frame(latest_server)

        if latest_preview is not None and self.preview_active:
            try:
                image = Image.open(BytesIO(latest_preview))
                image.load()
                self.view_camera.image = image
            except Exception:
                self.logger.debug("Failed updating preview image", exc_info=True)

    async def _restore_enabled_camera(self) -> None:
        if not self.chk_enable.value:
            return
        opened = await self.open_ftcamera()
        if not opened:
            self._set_enable_switch(False, save=True)

    def startup(self) -> None:
        self._ui_loop = aio.get_event_loop()
        self._remove_file_and_help_commands()
        settings = self._load_settings()
        self.language = str(settings["language"])

        content = toga.Box(style=tp.Pack(direction=tp.COLUMN, margin=8))

        # Keep language selection compact and left-aligned instead of reserving
        # a large empty header region.
        language_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        self.lab_language = toga.Label(self.tr("language"), style=tp.Pack(margin_right=5))
        language_line.add(self.lab_language)
        self.select_language = toga.Selection(
            items=list(self.LANGUAGE_NAMES.keys()),
            value=self.LANGUAGE_DISPLAY_NAMES[self.language],
            style=tp.Pack(width=115),
            on_change=self.on_language_changed,
        )
        language_line.add(self.select_language)
        content.add(language_line)

        top_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        content.add(top_line)
        self.lab_device = toga.Label(self.tr("device_number"), style=tp.Pack(margin_right=5))
        top_line.add(self.lab_device)
        self.edit_device = toga.NumberInput(
            min=0,
            max=15,
            value=int(settings["camera_index"]),
            style=tp.Pack(width=70, margin_right=10),
            on_change=self.on_device_changed,
        )
        top_line.add(self.edit_device)
        self.chk_enable = toga.Switch(
            self.tr("camera_enabled"),
            style=tp.Pack(flex=1, margin_right=8),
            value=bool(settings["enabled"]),
            on_change=self.on_switch_enable,
        )
        top_line.add(self.chk_enable)
        self.lab_cam_info = toga.Label(self.tr("camera_info_empty"), style=tp.Pack(text_align=tp.LEFT, margin_bottom=4))
        content.add(self.lab_cam_info)

        # Split network settings into two short rows so the window can be
        # narrower without compressing translated labels.
        bind_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        content.add(bind_line)
        self.lab_bind_ip = toga.Label(self.tr("bind_ipv4"), style=tp.Pack(margin_right=5))
        bind_line.add(self.lab_bind_ip)
        self.edit_bind_ip = toga.TextInput(
            value=str(settings["bind_ip"]),
            style=tp.Pack(width=150),
            on_change=self.on_network_setting_changed,
        )
        bind_line.add(self.edit_bind_ip)

        server_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        content.add(server_line)
        self.lab_port = toga.Label(self.tr("port"), style=tp.Pack(margin_right=5))
        server_line.add(self.lab_port)
        self.edit_server_port = toga.NumberInput(
            min=1,
            max=65535,
            value=int(settings["server_port"]),
            style=tp.Pack(width=90, margin_right=10),
            on_change=self.on_network_setting_changed,
        )
        server_line.add(self.edit_server_port)
        self.btn_server = toga.Button(self.tr("server_start"), on_press=self.on_button_server)
        server_line.add(self.btn_server)

        server_option_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        content.add(server_option_line)
        self.chk_server_auto_start = toga.Switch(
            self.tr("server_auto_start"),
            value=bool(settings["server_auto_start"]),
            on_change=self.on_switch_server_auto_start,
        )
        server_option_line.add(self.chk_server_auto_start)

        url_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_bottom=4, height=32))
        content.add(url_line)
        self.lab_stream_url = toga.Label(self.tr("stream_url"), style=tp.Pack(margin_right=5))
        url_line.add(self.lab_stream_url)
        self.edit_stream_url = toga.TextInput(
            value="",
            readonly=True,
            placeholder=self.tr("stream_url_placeholder"),
            style=tp.Pack(flex=1),
        )
        url_line.add(self.edit_stream_url)

        view_box = toga.Box(style=tp.Pack(direction=tp.ROW, margin_top=2, margin_bottom=2))
        view_box.add(toga.Box(style=tp.Pack(flex=1)))
        self.view_camera = toga.ImageView(
            Image.new("L", (self.PREVIEW_WIDTH, self.PREVIEW_HEIGHT), 40),
            style=tp.Pack(
                width=self.PREVIEW_WIDTH,
                height=self.PREVIEW_HEIGHT,
                background_color="#404040",
                margin_top=2,
                margin_bottom=2,
            ),
        )
        view_box.add(self.view_camera)
        view_box.add(toga.Box(style=tp.Pack(flex=1)))
        content.add(view_box)

        preview_button_line = toga.Box(style=tp.Pack(direction=tp.ROW, margin_top=2))
        preview_button_line.add(toga.Box(style=tp.Pack(flex=1)))
        self.btn_preview = toga.Button(
            self.tr("preview"),
            enabled=False,
            on_press=self.on_button_preview,
            style=tp.Pack(width=120),
        )
        preview_button_line.add(self.btn_preview)
        preview_button_line.add(toga.Box(style=tp.Pack(flex=1)))
        content.add(preview_button_line)

        self.main_window = toga.MainWindow(
            title="Lip Camera IP Server",
            size=(480, 620),
            resizable=False,
        )
        self.main_window.content = content
        self.main_window.show()
        self._apply_language()

        if bool(settings["server_auto_start"]):
            aio.get_event_loop().create_task(self.start_server())
        elif bool(settings["enabled"]):
            aio.get_event_loop().create_task(self._restore_enabled_camera())

    @staticmethod
    async def on_exit_app(app: "TestApp") -> bool:
        app._save_settings()
        await app.stop_server()
        await app.close_ftcamera()
        return True


def main() -> toga.App:
    logging.getLogger("evcta.TestApp").info(
        "Starting Windows-only Lip Camera IP server; display rotation=CCW90"
    )
    return TestApp()


if __name__ == "__main__":
    mp.freeze_support()
    main().main_loop()
