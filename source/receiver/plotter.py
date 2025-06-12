# plotter.py
# -*- coding: utf-8 -*-
from __future__ import annotations

import matplotlib.pyplot as plt
from typing import List, Dict, Any, Tuple
from collections import deque


class Plotter:
    """
    Visualizes real-time performance and communication quality metrics in two separate figures.
    - Figure 1: loss_pct, latency_ms, jitter_ms, pdr_pct (2x2 grid)
    - Figure 2: RSSI (1x1 grid)
    Supports dynamic axis scaling, current value display, and non-blocking updates.
    """

    def __init__(self, max_points: int = 100, fig1_size=(8, 6), fig2_size=(8, 3)):
        if not isinstance(max_points, int) or max_points <= 0:
            print(f"Warning: Invalid max_points value ({max_points}). Using default (100).")
            max_points = 100
        self.max_points: int = max_points

        # Figure 1: Performance Metrics
        self.performance_metrics: List[str] = ['loss_pct', 'latency_ms', 'jitter_ms', 'pdr_pct']
        self.performance_styles: Dict[str, Dict[str, Any]] = {
            'loss_pct': {'label': 'Packet Loss (%)', 'color': 'salmon', 'marker': '.', 'linestyle': '-'},
            'latency_ms': {'label': 'Latency (ms)', 'color': 'skyblue', 'marker': '.', 'linestyle': '-'},
            'jitter_ms': {'label': 'Jitter (ms)', 'color': 'lightgreen', 'marker': '.', 'linestyle': '-'},
            'pdr_pct': {'label': 'PDR (%)', 'color': 'cyan', 'marker': '.', 'linestyle': '-'},
        }

        # Figure 2: Communication Quality Metrics
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

        plt.ion()

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

        # --- Figure 2: Communication Quality (1x1) ---
        self.fig_qual, self.axes_qual_list = plt.subplots(1, 1, figsize=fig2_size, sharex=True)
        self.fig_qual.suptitle('Real-time Communication Quality', fontsize=12)
        self._set_window_title(self.fig_qual, 'Communication Quality')

        self.lines_qual: Dict[str, Tuple[plt.Axes, plt.Line2D]] = {}
        self.texts_qual: Dict[str, plt.Text] = {}

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
        if not self.is_plot_active:
            return False

        perf_open = hasattr(self, 'fig_perf') and self.fig_perf and plt.fignum_exists(self.fig_perf.number)
        qual_open = hasattr(self, 'fig_qual') and self.fig_qual and plt.fignum_exists(self.fig_qual.number)

        if not (perf_open or qual_open):
            self.is_plot_active = False
            return False
        return True

    def update(self, new_meta_data: Dict[str, Any]):
        if not self.is_alive(): return

        self._current_idx_val += 1
        self.time_idx.append(self._current_idx_val)
        current_xs = list(self.time_idx)

        for metric_name in self.performance_metrics:
            last_val = self.data[metric_name][-1] if self.data[metric_name] else 0.0
            current_val = float(new_meta_data.get(metric_name, last_val))
            self.data[metric_name].append(current_val)
            ax, line = self.lines_perf[metric_name]
            self._update_single_plot(ax, line, self.texts_perf[metric_name], metric_name, current_xs, current_val)

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