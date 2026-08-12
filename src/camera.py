"""
MIT License

Copyright DragonDreams GmbH 2024

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

"""Windows-only DirectShow camera capture for the Lip Camera UI."""

import asyncio as aio
import logging
import threading
import time
import traceback
from collections.abc import Callable

try:
    import comtypes as comt
except Exception:  # pragma: no cover - comtypes is Windows-only in this app.
    comt = None

import numpy as np
import pygrabber.dshow_graph as pgdsg


class FTCamera:
    """Open a Windows DirectShow camera and deliver YUV frames as numpy arrays."""

    class FrameSize:
        """Chosen DirectShow media size."""

        def __init__(self, index: int, width: int, height: int, min_fps: int) -> None:
            self.index = index
            self.width = width
            self.height = height
            self.min_fps = min_fps

        def __repr__(self) -> str:
            return "(width={}, height={}, fps={})".format(
                self.width, self.height, self.min_fps
            )

    class FrameFormat:
        """Chosen DirectShow pixel format."""

        def __init__(self, pixel_format: str, description: str) -> None:
            self.pixel_format = pixel_format
            self.description = description

        def __repr__(self) -> str:
            return "(pixel_format={}, description='{}')".format(
                self.pixel_format, self.description
            )

    _logger = logging.getLogger("evcta.FTCamera")

    def __init__(self, index: int) -> None:
        self._index = index
        self._device: pgdsg.VideoInput | None = None
        self._filter_graph: pgdsg.FilterGraph | None = None
        self._filter_video = None
        self._filter_grabber = None

        self._task_read: threading.Thread | None = None
        self._task_process: threading.Thread | None = None
        self._task_read_stop = False
        self._task_lock: threading.Lock | None = None
        self._has_frame = False
        self._read_frame: np.ndarray | None = None

        self._arr_c2: np.ndarray | None = None
        self._arr_c3: np.ndarray | None = None
        self._arr_merge: np.ndarray | None = None

        self.callback_frame: Callable[[np.ndarray], None] | None = None

    def open(self) -> None:
        """Open the DirectShow device without starting preview capture."""
        if self._device:
            return

        FTCamera._logger.info("FTCamera.open: index %s", self._index)

        try:
            self._filter_graph = pgdsg.FilterGraph()
            self._filter_graph.add_video_input_device(self._index)
            self._filter_video = self._filter_graph.get_input_device()
            self._device = self._filter_video
            FTCamera._logger.info("Video input filter: %s", self._filter_video.Name)

            self._filter_graph.add_sample_grabber(self._async_grabber)
            self._filter_grabber = self._filter_graph.filters[
                pgdsg.FilterType.sample_grabber
            ]
            self._filter_graph.add_null_render()

            self._find_format()
            self._find_frame_size()
            self._set_frame_format()
            self._init_arrays()
            self._filter_graph.prepare_preview_graph()
        except Exception:
            self._close_graph()
            raise

    def _find_format(self) -> None:
        FTCamera._logger.info("formats:")
        formats = self._filter_video.get_formats()
        for entry in formats:
            FTCamera._logger.info("%s", entry)

        selected = next(
            (entry for entry in formats if entry["media_type_str"] == "YUY2"),
            None,
        )
        if selected is None:
            raise RuntimeError("Camera has no YUY2 media format")

        self._format = FTCamera.FrameFormat("YUY2", "YUY2")
        FTCamera._logger.info("using format: %s", self._format)

    def _find_frame_size(self) -> None:
        matching_formats = [
            entry
            for entry in self._filter_video.get_formats()
            if entry["media_type_str"] == self._format.pixel_format
        ]
        if not matching_formats:
            raise RuntimeError("Camera has no usable YUY2 frame size")

        selected = next(
            (entry for entry in matching_formats if entry["min_framerate"] >= 30),
            matching_formats[0],
        )
        self._frame_size = FTCamera.FrameSize(
            selected["index"],
            selected["width"],
            selected["height"],
            int(selected["max_framerate"]),
        )

        FTCamera._logger.info("using frame size: %s", self._frame_size)
        self._frame_width = self._frame_size.width
        self._frame_height = self._frame_size.height
        self._pixel_count = self._frame_width * self._frame_height
        self._half_pixel_count = self._pixel_count // 2

    def _set_frame_format(self) -> None:
        self._filter_video.set_format(self._frame_size.index)

        guid_yuy2 = "{32595559-0000-0010-8000-00AA00389B71}"
        self._filter_grabber.set_media_type(pgdsg.MediaTypes.Video, guid_yuy2)

        class SampleGrabberYUY2(pgdsg.SampleGrabberCallback):
            def __init__(self, callback: Callable[[np.ndarray], None]) -> None:
                super().__init__(callback)

            def BufferCB(self, this, sample_time, p_buffer, buffer_len: int) -> int:
                if self.keep_photo:
                    self.keep_photo = False
                    width = self.image_resolution[0]
                    height = self.image_resolution[1]
                    image = np.ctypeslib.as_array(
                        p_buffer, shape=(height, width, 2)
                    )
                    image = np.moveaxis(image, 0, 1)
                    self.callback(image)
                return 0

        self._filter_grabber.set_callback(SampleGrabberYUY2(self._async_grabber), 1)

    def _init_arrays(self) -> None:
        self._arr_merge = np.zeros([self._pixel_count, 3], dtype=np.uint8)
        self._arr_c2 = np.empty([self._half_pixel_count], dtype=np.uint8)
        self._arr_c3 = np.empty([self._half_pixel_count], dtype=np.uint8)

    @property
    def device_index(self) -> int:
        return self._index

    @property
    def device(self) -> pgdsg.VideoInput | None:
        return self._device

    @property
    def frame_width(self) -> int:
        return self._frame_width

    @property
    def frame_height(self) -> int:
        return self._frame_height

    @property
    def frame_fps(self) -> float:
        return float(self._frame_size.min_fps)

    @property
    def frame_format(self) -> str:
        return self._format.pixel_format

    @property
    def frame_format_description(self) -> str:
        return self._format.description

    async def close(self) -> None:
        await self.stop_read()
        if not self._device:
            return

        FTCamera._logger.info("FTCamera.close: index %s", self._index)
        self._close_graph()

    def _close_graph(self) -> None:
        try:
            if self._filter_graph:
                self._filter_graph.stop()
                self._filter_graph.remove_filters()
        except Exception:
            FTCamera._logger.debug("Error while closing graph", exc_info=True)
        finally:
            self._device = None
            self._filter_grabber = None
            self._filter_video = None
            self._filter_graph = None


    @staticmethod
    def _co_initialize() -> bool:
        if comt is None:
            return False
        try:
            comt.CoInitialize()
            return True
        except Exception:
            FTCamera._logger.debug("COM initialization failed", exc_info=True)
            return False

    @staticmethod
    def _co_uninitialize(initialized: bool) -> None:
        if not initialized or comt is None:
            return
        try:
            comt.CoUninitialize()
        except Exception:
            FTCamera._logger.debug("COM uninitialization failed", exc_info=True)

    def start_read(self) -> None:
        """Start preview acquisition. The device must already be enabled/open."""
        if self._task_read or not self._device or not self._filter_graph:
            return

        FTCamera._logger.info("FTCamera.start_read: start preview")
        self._has_frame = False
        self._read_frame = None
        self._task_read_stop = False
        self._task_lock = threading.Lock()

        # DirectShow graph startup can block on Windows driver/USB activity.
        # Keep it off the Toga/asyncio UI thread so pressing Preview or Start
        # Server does not freeze the window when the driver takes time to run.
        self._task_read = threading.Thread(
            target=self._read_loop,
            name="FTCameraPreview",
            daemon=True,
        )
        self._task_process = threading.Thread(
            target=self._process_loop,
            name="FTCameraProcessor",
            daemon=True,
        )
        self._task_read.start()
        self._task_process.start()

    async def stop_read(self) -> None:
        """Stop preview acquisition without releasing the enabled camera."""
        if not self._task_read:
            return

        FTCamera._logger.info("FTCamera.stop_read: stop preview")
        self._task_read_stop = True

        if self._filter_graph:
            await aio.to_thread(self._safe_stop_graph)

        if self._task_read:
            await aio.to_thread(self._task_read.join, 0.8)
            self._task_read = None

        if self._task_process:
            await aio.to_thread(self._task_process.join, 0.8)
            self._task_process = None

        self._task_lock = None
        self._read_frame = None
        self._has_frame = False

    def _safe_stop_graph(self) -> None:
        initialized = self._co_initialize()
        try:
            if self._filter_graph:
                self._filter_graph.stop()
        except Exception:
            FTCamera._logger.debug("Error while stopping graph", exc_info=True)
        finally:
            self._co_uninitialize(initialized)

    def _read_loop(self) -> None:
        graph = self._filter_graph
        if graph is None:
            return

        initialized = self._co_initialize()
        try:
            try:
                graph.run()
            except Exception:
                FTCamera._logger.error("Frame graph start failed:\n%s", traceback.format_exc())
                self._task_read_stop = True
                return

            frame_interval = 1.0 / 60.0
            while not self._task_read_stop and self._filter_graph is graph:
                started_at = time.monotonic()
                try:
                    graph.grab_frame()
                except Exception:
                    FTCamera._logger.error("Frame acquisition failed:\n%s", traceback.format_exc())
                    self._task_read_stop = True
                    break
                elapsed = time.monotonic() - started_at
                if elapsed < frame_interval:
                    time.sleep(frame_interval - elapsed)
        finally:
            self._co_uninitialize(initialized)

    def _async_grabber(self, image: np.ndarray) -> None:
        lock = self._task_lock
        if lock is None:
            return
        with lock:
            self._read_frame = image
            self._has_frame = True

    def _process_loop(self) -> None:
        while not self._task_read_stop:
            frame = None
            lock = self._task_lock
            if lock is not None:
                with lock:
                    if self._has_frame:
                        frame = self._read_frame
                        self._read_frame = None
                        self._has_frame = False

            if frame is not None:
                if not self._process_frame(frame):
                    self._task_read_stop = True
                    return
                time.sleep(0.001)
            else:
                time.sleep(0.002)

    def _process_frame(self, frame: np.ndarray) -> bool:
        if not self.callback_frame or len(frame) == 0:
            return True
        try:
            self._decode_yuv422(frame)
            self.callback_frame(
                self._arr_merge.reshape([self._frame_height, self._frame_width, 3])
            )
        except Exception:
            FTCamera._logger.error(traceback.format_exc())
            return False
        return True

    def _decode_yuv422(self, frame: np.ndarray) -> None:
        """Decode a DirectShow YUY2 frame into planar YUV444 data."""
        self._arr_merge[:, 0] = frame[:, :, 0].ravel(order="F")

        chroma = frame[:, :, 1:].ravel(order="F")
        self._arr_c2[:] = np.array(chroma[0::2])
        self._arr_c3[:] = np.array(chroma[1::2])

        self._arr_merge[0:self._pixel_count:2, 1] = self._arr_c2
        self._arr_merge[1:self._pixel_count:2, 1] = self._arr_c2
        self._arr_merge[0:self._pixel_count:2, 2] = self._arr_c3
        self._arr_merge[1:self._pixel_count:2, 2] = self._arr_c3
