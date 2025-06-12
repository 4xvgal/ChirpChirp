# receiver_integrated_en.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
import time
import json
import datetime
import serial
import struct
import binascii
from typing import List, Optional, Dict, Any, Tuple
from collections import deque

# --- Import Matplotlib and Plotter Class ---
try:
    import matplotlib
    # The 'TkAgg' backend is often needed for GUI environments.
    # If running in a non-GUI environment (like a bare SSH terminal),
    # this might need to be commented out or changed to 'Agg'.
    # matplotlib.use('TkAgg')
    import matplotlib.pyplot as plt

    print("Matplotlib imported successfully.")
except ImportError:
    print("Error: Matplotlib library not found. Please install it using 'pip install matplotlib'.")
    exit(1)

# --- Import Decoder and Logger Modules ---
try:
    import decoder  # Assuming it's in the same folder
    import rx_logger  # Assuming rx_logger.py is in the same folder
except ImportError as e:
    print(f"Module import failed: {e}. Please ensure decoder.py and rx_logger.py are in the same directory.")
    exit(1)


# ==============================================================================
# Plotter Class (Integrated from plotter_enhanced.py, English Version)
# ==============================================================================
class Plotter:
    """
    Visualizes real-time performance and communication quality metrics in two separate figures.
    - Figure 1: loss_pct, latency_ms, jitter_ms, pdr_pct (2x2 grid) # <<< 변경됨: link_score -> pdr_pct
    - Figure 2: RSSI (1x1 grid) # <<< 변경됨: SNR 제거
    Supports dynamic axis scaling, current value display, and non-blocking updates.
    """

    def __init__(self, max_points: int = 100, fig1_size=(8, 6), fig2_size=(8, 3)):  # <<< 변경됨: 창 크기 조절
        if not isinstance(max_points, int) or max_points <= 0:
            print(f"Warning: Invalid max_points value ({max_points}). Using default (100).")
            max_points = 100
        self.max_points: int = max_points

        # Figure 1: Performance Metrics
        # <<< 변경됨: 'link_score'를 'pdr_pct'로 교체
        self.performance_metrics: List[str] = ['loss_pct', 'latency_ms', 'jitter_ms', 'pdr_pct']
        self.performance_styles: Dict[str, Dict[str, Any]] = {
            'loss_pct': {'label': 'Packet Loss (%)', 'color': 'salmon', 'marker': '.', 'linestyle': '-'},
            'latency_ms': {'label': 'Latency (ms)', 'color': 'skyblue', 'marker': '.', 'linestyle': '-'},
            'jitter_ms': {'label': 'Jitter (ms)', 'color': 'lightgreen', 'marker': '.', 'linestyle': '-'},
            'pdr_pct': {'label': 'PDR (%)', 'color': 'cyan', 'marker': '.', 'linestyle': '-'},  # <<< 변경됨
        }

        # Figure 2: Communication Quality Metrics
        # <<< 변경됨: 'snr' 제거
        self.quality_metrics: List[str] = ['rssi_dbm_estimated']
        self.quality_styles: Dict[str, Dict[str, Any]] = {
            'rssi_dbm_estimated': {'label': 'RSSI (dBm)', 'color': 'gold', 'marker': '.', 'linestyle': '--'},
        }

        self.all_metrics: List[str] = self.performance_metrics + self.quality_metrics
        self.all_styles: Dict[str, Dict[str, Any]] = {**self.performance_styles, **self.quality_styles}

        self.data: Dict[str, deque] = {metric: deque(maxlen=self.max_points) for metric in self.all_metrics}
        self.time_idx: deque = deque(maxlen=self.max_points)
        self._current_idx_val: int = 0
        self.is_plot_active = True

        plt.ion()  # Turn on interactive mode for non-blocking plots

        # --- Figure 1: Performance Metrics (2x2) ---
        self.fig_perf, self.axes_perf_list = plt.subplots(2, 2, figsize=fig1_size, sharex=True)
        self.fig_perf.suptitle('Real-time Performance Metrics', fontsize=12)
        self._set_window_title(self.fig_perf, 'Performance Metrics')

        self.lines_perf: Dict[str, Tuple[plt.Axes, plt.Line2D]] = {}
        self.texts_perf: Dict[str, plt.Text] = {}
        for ax, metric_name in zip(self.axes_perf_list.flatten(), self.performance_metrics):
            style = self.performance_styles[metric_name]
            ax.set_title(style['label'], fontsize=9)
            ax.set_ylabel(style['label'].split('(')[0].strip(), fontsize=8)
            ax.grid(True, linestyle=':', alpha=0.5)
            line, = ax.plot([], [], color=style['color'], marker=style['marker'], markersize=3,
                            linestyle=style['linestyle'])
            text = ax.text(0.02, 0.88, '', transform=ax.transAxes, fontsize=7,
                           bbox=dict(boxstyle='round,pad=0.2', fc=ax.get_facecolor(), alpha=0.6))
            self.lines_perf[metric_name] = (ax, line)
            self.texts_perf[metric_name] = text
        self.axes_perf_list[1, 0].set_xlabel('Sample Index', fontsize=8)
        self.axes_perf_list[1, 1].set_xlabel('Sample Index', fontsize=8)
        self.fig_perf.tight_layout(rect=[0, 0.03, 1, 0.92])

        # --- Figure 2: Communication Quality (1x1) --- # <<< 변경됨
        # <<< 변경됨: 1행 1열로 수정
        self.fig_qual, self.axes_qual_list = plt.subplots(1, 1, figsize=fig2_size, sharex=True)
        self.fig_qual.suptitle('Real-time Communication Quality', fontsize=12)
        self._set_window_title(self.fig_qual, 'Communication Quality')

        self.lines_qual: Dict[str, Tuple[plt.Axes, plt.Line2D]] = {}
        self.texts_qual: Dict[str, plt.Text] = {}

        # <<< 변경됨: 1x1 서브플롯을 처리하기 위한 로직 수정
        axes_qual_flat = [self.axes_qual_list] if not hasattr(self.axes_qual_list,
                                                              'flatten') else self.axes_qual_list.flatten()
        for ax, metric_name in zip(axes_qual_flat, self.quality_metrics):
            style = self.quality_styles[metric_name]
            ax.set_title(style['label'], fontsize=9)
            ax.set_xlabel('Sample Index', fontsize=8)
            ax.set_ylabel(style['label'].split('(')[0].strip(), fontsize=8)
            ax.grid(True, linestyle=':', alpha=0.5)
            line, = ax.plot([], [], color=style['color'], marker=style['marker'], markersize=3,
                            linestyle=style['linestyle'])
            text = ax.text(0.02, 0.88, '', transform=ax.transAxes, fontsize=7,
                           bbox=dict(boxstyle='round,pad=0.2', fc=ax.get_facecolor(), alpha=0.6))
            self.lines_qual[metric_name] = (ax, line)
            self.texts_qual[metric_name] = text
        self.fig_qual.tight_layout(rect=[0, 0.03, 1, 0.88])

        plt.show(block=False)
        self._flush_all_figures_initial()

    def _set_window_title(self, fig: plt.Figure, title: str):
        try:
            fig.canvas.manager.set_window_title(title)
        except AttributeError:
            pass

    def _flush_all_figures_initial(self):
        try:
            if hasattr(self, 'fig_perf') and plt.fignum_exists(self.fig_perf.number):
                self.fig_perf.canvas.flush_events()
            if hasattr(self, 'fig_qual') and plt.fignum_exists(self.fig_qual.number):
                self.fig_qual.canvas.flush_events()
        except Exception:
            pass

    def _update_single_plot(self, ax: plt.Axes, line: plt.Line2D, text_obj: plt.Text, metric_name: str, xs: List[int],
                            current_value: float):
        ys = list(self.data[metric_name])
        line.set_data(xs, ys)

        style_info = self.all_styles[metric_name]
        text_obj.set_text(f'{style_info["label"].split("(")[0].strip()}: {current_value:.2f}')

        ax.relim()
        ax.autoscale_view(tight=None, scalex=False, scaley=True)

        if xs:
            padding = max(1, int(len(xs) * 0.05))
            ax.set_xlim(xs[0] if len(xs) >= self.max_points else 0, xs[-1] + padding)
        else:
            ax.set_xlim(0, 10)

    def is_alive(self) -> bool:
        """Checks if any plot window is still open."""
        if not self.is_plot_active:
            return False

        perf_open = hasattr(self, 'fig_perf') and self.fig_perf and plt.fignum_exists(self.fig_perf.number)
        qual_open = hasattr(self, 'fig_qual') and self.fig_qual and plt.fignum_exists(self.fig_qual.number)

        if not (perf_open or qual_open):
            self.is_plot_active = False  # Disable plotting once closed by user
            return False
        return True

    def update(self, new_meta_data: Dict[str, Any]):
        if not self.is_alive(): return

        self._current_idx_val += 1
        self.time_idx.append(self._current_idx_val)
        current_xs = list(self.time_idx)

        # Update performance metrics
        for metric_name in self.performance_metrics:
            last_val = self.data[metric_name][-1] if self.data[metric_name] else 0.0
            current_val = float(new_meta_data.get(metric_name, last_val))
            self.data[metric_name].append(current_val)
            ax, line = self.lines_perf[metric_name]
            self._update_single_plot(ax, line, self.texts_perf[metric_name], metric_name, current_xs, current_val)

        # Update quality metrics
        for metric_name in self.quality_metrics:
            default_val = -100.0 if 'rssi' in metric_name else 0.0
            last_val = self.data[metric_name][-1] if self.data[metric_name] else default_val
            current_val = float(new_meta_data.get(metric_name, last_val))
            self.data[metric_name].append(current_val)
            ax, line = self.lines_qual[metric_name]
            self._update_single_plot(ax, line, self.texts_qual[metric_name], metric_name, current_xs, current_val)

    def close(self):
        print("Closing Plotter...")
        if not plt.isinteractive(): return

        plt.ioff()
        if hasattr(self, 'fig_perf') and plt.fignum_exists(self.fig_perf.number): plt.close(self.fig_perf)
        if hasattr(self, 'fig_qual') and plt.fignum_exists(self.fig_qual.number): plt.close(self.fig_qual)
        print("All plot windows have been closed.")


# ==============================================================================
# Receiver Code (Based on receiver1.py, English Version)
# ==============================================================================

# ────────── Settings ──────────
PORT = "/dev/ttyAMA0"
BAUD = 9600

SERIAL_READ_TIMEOUT = 0.05
INITIAL_SYN_TIMEOUT = 5

SYN_MSG = b"SYN\r\n"
ACK_TYPE_HANDSHAKE = 0x00
ACK_TYPE_DATA = 0xAA
QUERY_TYPE_SEND_REQUEST = 0x50
ACK_TYPE_SEND_PERMIT = 0x55

ACK_PACKET_LEN = 2
HANDSHAKE_ACK_SEQ = 0x00

# --- Logger Initialization ---
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                    datefmt="%Y-%m-%d %H:%M:%S")
logger = logging.getLogger(__name__)

# --- Valid Data Packet Length Range ---
MIN_COMPRESSED_PAYLOAD_LEN = 5
MAX_PAYLOAD_CHUNK_FROM_ENCODER = 56
NEW_MIN_FRAME_CONTENT_LEN = 1 + MIN_COMPRESSED_PAYLOAD_LEN
NEW_FRAME_MAX_CONTENT_LEN = 1 + MAX_PAYLOAD_CHUNK_FROM_ENCODER
VALID_DATA_PKT_LENGTH_RANGE = range(NEW_MIN_FRAME_CONTENT_LEN, NEW_FRAME_MAX_CONTENT_LEN + 1)
logger.info(f"Valid data frame content length range (LENGTH byte value): {list(VALID_DATA_PKT_LENGTH_RANGE)}")

KNOWN_CONTROL_TYPES_FROM_SENDER = [QUERY_TYPE_SEND_REQUEST]

DATA_DIR = "data/raw"
os.makedirs(DATA_DIR, exist_ok=True)


def _log_json(payload: dict, meta: dict):
    fn = datetime.datetime.now().strftime("%Y-%m-%d") + ".jsonl"
    with open(os.path.join(DATA_DIR, fn), "a", encoding="utf-8") as fp:
        fp.write(json.dumps({
            "ts_recv_utc": datetime.datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "data": payload,
            "meta": meta
        }, ensure_ascii=False) + "\n")


def _send_control_response(s: serial.Serial, seq: int, ack_type: int) -> bool:
    ack_bytes = struct.pack("!BB", ack_type, seq)
    type_name = {
        ACK_TYPE_HANDSHAKE: "HANDSHAKE_ACK",
        ACK_TYPE_DATA: "DATA_ACK",
        ACK_TYPE_SEND_PERMIT: "SEND_PERMIT_ACK"
    }.get(ack_type, f"UNKNOWN_0x{ack_type:02x}")

    try:
        written = s.write(ack_bytes)
        s.flush()
        logger.info(f"CTRL RSP TX: TYPE={type_name}, SEQ=0x{seq:02x}")
        return written == len(ack_bytes)
    except Exception as e:
        logger.error(f"CTRL RSP TX failed (TYPE={type_name}, SEQ=0x{seq:02x}): {e}")
        return False


def receive_loop():
    ser: Optional[serial.Serial] = None
    plotter: Optional[Plotter] = None

    try:
        logger.info(f"Attempting to open serial port {PORT} (Baud: {BAUD})...")
        ser = serial.Serial(PORT, BAUD, timeout=INITIAL_SYN_TIMEOUT, inter_byte_timeout=0.02)
        logger.info(f"Serial port {PORT} opened successfully.")
        rx_logger.log_rx_event(event_type="SERIAL_PORT_OPEN_SUCCESS", notes=f"Port: {PORT}, Baud: {BAUD}")

        # --- Create Plotter Object ---
        try:
            # <<< 변경됨: figsize 값 수정
            plotter = Plotter(max_points=100, fig1_size=(8, 6), fig2_size=(8, 3))
            logger.info("Real-time plotter initialized successfully.")
        except Exception as e_plot:
            logger.error(f"Plotter initialization failed: {e_plot}. Proceeding without visualization.")
            plotter = None
        # ---------------------------

    except serial.SerialException as e:
        logger.error(f"Failed to open port ({PORT}): {e}")
        rx_logger.log_rx_event(event_type="SERIAL_PORT_OPEN_FAIL", notes=f"Port: {PORT}, Error: {e}")
        return

    # Handshake Logic
    while True:
        logger.info(f"Waiting for SYN ('{SYN_MSG!r}') (Timeout: {ser.timeout}s)...")
        if ser.in_waiting > 0: ser.reset_input_buffer()
        line = ser.readline()
        if line == SYN_MSG:
            logger.info("SYN received, sending Handshake ACK")
            if _send_control_response(ser, HANDSHAKE_ACK_SEQ, ACK_TYPE_HANDSHAKE):
                logger.info("Handshake successful.")
                break
        elif not line:
            logger.warning("Handshake: SYN wait timed out. Retrying...")
        else:
            logger.debug(f"Unexpected data during handshake (ignored): {line!r}")

    ser.timeout = SERIAL_READ_TIMEOUT
    logger.info("Waiting for data...")

    received_message_count = 0

    # <<< 추가됨: PDR 및 Loss 계산을 위한 변수
    pdr_window = deque(maxlen=50)  # 최근 50개 패킷을 기준으로 PDR 계산
    last_seq: Optional[int] = None
    current_pdr_pct = 100.0
    current_loss_pct = 0.0

    try:
        while True:
            # --- GUI Update & Event Handling ---
            if plotter and plotter.is_alive():
                try:
                    plt.pause(0.01)
                except Exception:
                    logger.warning("Error during plot window event handling. Disabling plotting.")
                    if plotter: plotter.close()
                    plotter = None
            # ---------------------------------

            first_byte_data = ser.read(1)
            if not first_byte_data: continue

            first_byte_val = first_byte_data[0]

            if first_byte_val in KNOWN_CONTROL_TYPES_FROM_SENDER:
                seq_byte = ser.read(1)
                if seq_byte:
                    seq_num = seq_byte[0]
                    logger.info(
                        f"Control packet received: TYPE=QUERY_SEND_REQUEST (0x{first_byte_val:02x}), SEQ=0x{seq_num:02x}")
                    _send_control_response(ser, seq_num, ACK_TYPE_SEND_PERMIT)
                continue

            elif first_byte_val in VALID_DATA_PKT_LENGTH_RANGE:
                content_len = first_byte_val
                content_bytes = ser.read(content_len)

                rssi_raw, rssi_dbm = None, None
                if len(content_bytes) == content_len:
                    rssi_byte = ser.read(1)
                    if rssi_byte:
                        rssi_raw = rssi_byte[0]
                        rssi_dbm = -(256 - rssi_raw)

                if len(content_bytes) == content_len:
                    seq = content_bytes[0]
                    payload_chunk = content_bytes[1:]

                    logger.info(
                        f"Data frame received: SEQ=0x{seq:02x}, PAYLOAD_LEN={len(payload_chunk)}B, RSSI={rssi_dbm}dBm")
                    _send_control_response(ser, seq, ACK_TYPE_DATA)

                    # <<< 추가됨: PDR/Loss 계산 로직
                    if last_seq is not None:
                        # 8-bit 시퀀스 번호의 wrap-around 처리
                        seq_diff = (seq - last_seq + 256) % 256
                        if seq_diff > 1:
                            lost_count = seq_diff - 1
                            logger.warning(
                                f"Packet loss detected! SEQ Gap: {last_seq:02x} -> {seq:02x}. Lost: {lost_count}")
                            for _ in range(lost_count):
                                pdr_window.append(0)  # 0은 손실된 패킷
                        pdr_window.append(1)  # 1은 수신된 패킷
                    else:
                        pdr_window.append(1)  # 첫 패킷은 수신 성공으로 처리

                    last_seq = seq

                    if len(pdr_window) > 0:
                        received_count_in_window = sum(pdr_window)
                        total_count_in_window = len(pdr_window)
                        current_pdr_pct = (received_count_in_window / total_count_in_window) * 100
                        current_loss_pct = 100.0 - current_pdr_pct
                    # ------------------------------------

                    try:
                        payload_dict = decoder.decompress_data(payload_chunk)
                        if payload_dict:
                            received_message_count += 1

                            ts_value = payload_dict.get('ts', 0.0)
                            latency_ms = 0
                            if isinstance(ts_value, (int, float)) and ts_value > 0:
                                latency_ms = int((time.time() - ts_value) * 1000)

                            meta_data = {
                                "recv_frame_seq": seq,
                                "latency_ms": latency_ms,
                                "rssi_dbm_estimated": rssi_dbm,
                                # <<< 변경됨: 계산된 PDR 및 Loss 값 사용
                                "pdr_pct": current_pdr_pct,
                                "loss_pct": current_loss_pct,
                                "jitter_ms": 0.0,  # Placeholder
                            }
                            _log_json(payload_dict, meta_data)
                            logger.info(
                                f"[OK#{received_message_count} SEQ:0x{seq:02x}] Latency: {latency_ms}ms. JSON saved.")

                            # --- Plotter Update ---
                            if plotter and plotter.is_alive():
                                plotter.update(meta_data)
                            # ----------------------

                        else:
                            logger.error(f"Message (SEQ: 0x{seq:02x}): Decoding failed.")

                    except Exception as e_proc:
                        logger.error(f"Error processing message (SEQ: 0x{seq:02x}): {e_proc}", exc_info=True)
                continue

    except KeyboardInterrupt:
        logger.info("Reception stopped (KeyboardInterrupt)")
    except Exception as e_global:
        logger.error(f"Global exception caught: {e_global}", exc_info=True)
    finally:
        if plotter:
            plotter.close()
        if ser and ser.is_open:
            ser.close()
            logger.info("Serial port closed.")


if __name__ == "__main__":
    logging.getLogger().setLevel(logging.INFO)
    receive_loop()