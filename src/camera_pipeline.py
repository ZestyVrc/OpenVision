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

from __future__ import annotations

# Out-of-process camera capture pipeline for the Lip Camera UI.
#
# The DirectShow/pygrabber/comtypes path can block inside Windows camera drivers or
# hold the Python GIL while it waits for COM/DirectShow. Running it in a child
# process keeps the Toga WinForms UI process responsive even when the camera stack
# stalls.

import asyncio as aio
from dataclasses import dataclass
import logging
import multiprocessing as mp
import queue
import time
import traceback
from typing import Any


@dataclass(slots=True)
class CameraInfo:
    width: int = 0
    height: int = 0
    fps: float = 0.0
    device_name: str = ""
    format: str = ""


class CameraPipelineError(RuntimeError):
    """Raised when the isolated camera process cannot be started or controlled."""


class CameraPipeline:
    """Parent-side controller for the isolated camera capture process."""

    OPEN_TIMEOUT_SECONDS = 8.0
    CLOSE_TIMEOUT_SECONDS = 2.0

    def __init__(self) -> None:
        self._logger = logging.getLogger("evcta.CameraPipeline")
        self._process: mp.Process | None = None
        self._control_queue: mp.Queue | None = None
        self._status_queue: mp.Queue | None = None
        self._frame_queue: mp.Queue | None = None
        self._camera_info = CameraInfo()
        self._capture_active = False

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.is_alive()

    @property
    def capture_active(self) -> bool:
        return self._capture_active and self.running

    @property
    def camera_info(self) -> CameraInfo:
        return self._camera_info

    async def open(self, device_index: int) -> CameraInfo:
        """Start the child process and wait until the camera graph is opened."""
        if self.running:
            return self._camera_info

        self.close(force=True)

        ctx = mp.get_context("spawn")
        self._control_queue = ctx.Queue(maxsize=8)
        self._status_queue = ctx.Queue(maxsize=8)
        self._frame_queue = ctx.Queue(maxsize=4)
        self._capture_active = False
        self._camera_info = CameraInfo()

        process = ctx.Process(
            target=_camera_worker_main,
            args=(device_index, self._control_queue, self._status_queue, self._frame_queue),
            name="LipCameraCaptureProcess",
            daemon=True,
        )
        self._process = process
        process.start()

        try:
            info = await aio.to_thread(self._wait_until_opened)
            self._camera_info = info
            return info
        except Exception:
            self._logger.error("Camera process failed to open:\n%s", traceback.format_exc())
            self.close(force=True)
            raise

    def _wait_until_opened(self) -> CameraInfo:
        if self._status_queue is None:
            raise CameraPipelineError("Camera status queue is not available")

        deadline = time.monotonic() + self.OPEN_TIMEOUT_SECONDS
        last_message: Any = None
        while time.monotonic() < deadline:
            process = self._process
            if process is not None and not process.is_alive():
                exitcode = process.exitcode
                raise CameraPipelineError(f"Camera process exited before opening; exitcode={exitcode}")
            try:
                message = self._status_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            last_message = message
            kind = message.get("type")
            if kind == "opened":
                return CameraInfo(
                    width=int(message.get("width", 0)),
                    height=int(message.get("height", 0)),
                    fps=float(message.get("fps", 0.0)),
                    device_name=str(message.get("device_name", "")),
                    format=str(message.get("format", "")),
                )
            if kind == "error":
                raise CameraPipelineError(str(message.get("message", "Unknown camera error")))

        raise CameraPipelineError(f"Timed out opening camera process; last_message={last_message!r}")

    def start_capture(self) -> None:
        """Tell the child process to start DirectShow reading."""
        if not self.running or self._control_queue is None:
            raise CameraPipelineError("Camera process is not running")
        self._send_command({"command": "start_capture"})
        self._capture_active = True

    def stop_capture(self) -> None:
        """Tell the child process to stop DirectShow reading."""
        if not self.running or self._control_queue is None:
            self._capture_active = False
            return
        self._send_command({"command": "stop_capture"})
        self._capture_active = False

    def close(self, force: bool = False) -> None:
        """Stop the child process and release all queues."""
        process = self._process
        if process is None:
            self._reset_handles()
            return

        if process.is_alive() and self._control_queue is not None:
            try:
                self._send_command({"command": "shutdown"})
            except Exception:
                self._logger.debug("Failed sending shutdown command", exc_info=True)

        process.join(timeout=0.2 if force else self.CLOSE_TIMEOUT_SECONDS)
        if process.is_alive():
            process.terminate()
            process.join(timeout=1.0)
        if process.is_alive():
            process.kill()
            process.join(timeout=1.0)

        self._reset_handles()

    def _reset_handles(self) -> None:
        self._process = None
        self._control_queue = None
        self._status_queue = None
        self._frame_queue = None
        self._capture_active = False
        self._camera_info = CameraInfo()

    def _send_command(self, message: dict[str, Any]) -> None:
        if self._control_queue is None:
            raise CameraPipelineError("Camera control queue is not available")
        try:
            self._control_queue.put_nowait(message)
        except queue.Full:
            # Control messages are small and rare. If the queue is full, remove one
            # stale command and retry once so the UI never blocks here.
            try:
                self._control_queue.get_nowait()
            except queue.Empty:
                pass
            self._control_queue.put_nowait(message)

    def drain_frames(self) -> list[dict[str, Any]]:
        """Return all currently queued preview/server frame messages."""
        messages: list[dict[str, Any]] = []
        frame_queue = self._frame_queue
        if frame_queue is None:
            return messages

        while True:
            try:
                message = frame_queue.get_nowait()
            except queue.Empty:
                break
            messages.append(message)
        return messages

    def drain_status(self) -> list[dict[str, Any]]:
        """Return status messages that arrived after opening."""
        messages: list[dict[str, Any]] = []
        status_queue = self._status_queue
        if status_queue is None:
            return messages

        while True:
            try:
                message = status_queue.get_nowait()
            except queue.Empty:
                break
            messages.append(message)
        return messages


# The worker must live at module top level for Windows multiprocessing spawn.
def _camera_worker_main(
    device_index: int,
    control_queue: mp.Queue,
    status_queue: mp.Queue,
    frame_queue: mp.Queue,
) -> None:
    import cv2 as cv
    from camera import FTCamera
    from tracker_control import TrackerController

    logging.basicConfig(
        filename="lip_camera_server.log",
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(processName)s:%(threadName)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger("evcta.CameraPipeline.worker")

    camera: FTCamera | None = None
    tracker_controller: TrackerController | None = None
    capture_started = False
    last_server_frame_at = 0.0
    last_preview_frame_at = 0.0

    stream_width = 320
    stream_height = 480
    preview_width = 256
    preview_height = 384

    def put_status(message: dict[str, Any]) -> None:
        _put_latest(status_queue, message)

    def put_frame(message: dict[str, Any]) -> None:
        _put_latest(frame_queue, message)

    def process_frame(data):
        nonlocal last_server_frame_at, last_preview_frame_at
        try:
            if tracker_controller is not None:
                data = tracker_controller.process_frame(data)

            luminance = cv.split(data)[0]
            upright = cv.rotate(luminance, cv.ROTATE_90_COUNTERCLOCKWISE)
            portrait = cv.resize(
                upright,
                (stream_width, stream_height),
                interpolation=cv.INTER_AREA,
            )

            now = time.monotonic()
            server_jpeg: bytes | None = None
            preview_jpeg: bytes | None = None

            if now - last_server_frame_at >= 1.0 / 30.0:
                ok, encoded = cv.imencode(
                    ".jpg",
                    portrait,
                    [int(cv.IMWRITE_JPEG_QUALITY), 85],
                )
                if ok:
                    server_jpeg = encoded.tobytes()
                    last_server_frame_at = now

            if now - last_preview_frame_at >= 1.0 / 15.0:
                preview_frame = cv.resize(
                    portrait,
                    (preview_width, preview_height),
                    interpolation=cv.INTER_AREA,
                )
                ok, encoded = cv.imencode(
                    ".jpg",
                    preview_frame,
                    [int(cv.IMWRITE_JPEG_QUALITY), 80],
                )
                if ok:
                    preview_jpeg = encoded.tobytes()
                    last_preview_frame_at = now

            if server_jpeg is not None or preview_jpeg is not None:
                put_frame(
                    {
                        "type": "frame",
                        "server_jpeg": server_jpeg,
                        "preview_jpeg": preview_jpeg,
                        "monotonic": now,
                    }
                )
        except Exception:
            logger.error("Frame processing failed:\n%s", traceback.format_exc())
            put_status(
                {
                    "type": "error",
                    "message": "Frame processing failed. See lip_camera_server.log.",
                }
            )

    try:
        logger.info("Opening camera in isolated process: index=%s", device_index)
        camera = FTCamera(device_index)
        camera.open()

        try:
            device_name = camera.device.Name if camera.device is not None else ""
        except Exception:
            device_name = ""

        try:
            if camera.device is not None and TrackerController.is_supported_camera(camera.device):
                tracker_controller = TrackerController(camera.device, camera.device_index)
        except Exception:
            logger.error("Camera extension control failed:\n%s", traceback.format_exc())
            put_status(
                {
                    "type": "warning",
                    "message": "Camera extension control failed. See lip_camera_server.log.",
                }
            )

        put_status(
            {
                "type": "opened",
                "width": camera.frame_width,
                "height": camera.frame_height,
                "fps": camera.frame_fps,
                "format": camera.frame_format,
                "device_name": device_name,
            }
        )

        while True:
            try:
                message = control_queue.get(timeout=0.05)
            except queue.Empty:
                continue

            command = message.get("command")
            if command == "start_capture":
                if camera is not None and not capture_started:
                    logger.info("Starting isolated camera capture")
                    camera.callback_frame = process_frame
                    camera.start_read()
                    capture_started = True
                    put_status({"type": "capture_started"})
            elif command == "stop_capture":
                if camera is not None and capture_started:
                    logger.info("Stopping isolated camera capture")
                    camera.callback_frame = None
                    aio.run(camera.stop_read())
                    capture_started = False
                    put_status({"type": "capture_stopped"})
            elif command == "shutdown":
                logger.info("Shutting down isolated camera process")
                break

    except Exception:
        logger.error("Camera worker failed:\n%s", traceback.format_exc())
        put_status(
            {
                "type": "error",
                "message": traceback.format_exc(),
            }
        )
    finally:
        try:
            if camera is not None:
                camera.callback_frame = None
                aio.run(camera.stop_read())
        except Exception:
            logger.debug("Error while stopping camera read", exc_info=True)
        try:
            if tracker_controller is not None:
                tracker_controller.dispose()
        except Exception:
            logger.debug("Error while disposing tracker controller", exc_info=True)
        try:
            if camera is not None:
                aio.run(camera.close())
        except Exception:
            logger.debug("Error while closing camera", exc_info=True)


def _put_latest(target_queue: mp.Queue, message: dict[str, Any]) -> None:
    """Put a message without blocking; discard stale messages if necessary."""
    for _ in range(2):
        try:
            target_queue.put_nowait(message)
            return
        except queue.Full:
            try:
                target_queue.get_nowait()
            except queue.Empty:
                return
