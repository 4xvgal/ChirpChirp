# plotter.py
import matplotlib.pyplot as plt
from collections import deque
from config import MAX_POINTS

class Plotter:
    """
    전체 히스토리를 0부터 현재까지 보여주는 실시간 PDR 그래프 클래스
    """
    def __init__(self):
        plt.ion()
        self.fig, self.ax = plt.subplots()
        self.line, = self.ax.plot([], [], lw=2, marker='o')
        self.text = self.ax.text(0.02, 0.95, '', transform=self.ax.transAxes)

        self.ax.set_xlabel('Elapsed Time (s)')
        self.ax.set_ylabel('PDR (%)')
        self.ax.set_title('Real-time Packet Delivery Ratio')
        self.ax.set_xlim(0, 1)
        self.ax.set_ylim(0, 100)
        self.ax.grid(True)

        self.timestamps = deque(maxlen=MAX_POINTS)
        self.pdr_values = deque(maxlen=MAX_POINTS)

        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def update(self, elapsed: float, pdr: float):
        self.timestamps.append(elapsed)
        self.pdr_values.append(pdr)

        self.line.set_data(self.timestamps, self.pdr_values)
        # x축을 0에서 마지막까지 확장
        xmax = self.timestamps[-1] if self.timestamps else 1
        self.ax.set_xlim(0, xmax)
        self.ax.set_ylim(0, 100)

        self.text.set_text(f'Current PDR: {pdr:.2f}%')

        self.ax.relim()
        self.ax.autoscale_view(False, True, True)
        self.fig.canvas.draw()
        self.fig.canvas.flush_events()

    def close(self):
        plt.ioff()
        plt.show()