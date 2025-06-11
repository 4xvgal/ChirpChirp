import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from source.receiver.rx_logger import SessionLogger
from transmitter.sensor_reader import SensorReader

reader = SensorReader()
logger = SessionLogger()

for _ in range(5):
    logger.log(reader.get_sensor_data())