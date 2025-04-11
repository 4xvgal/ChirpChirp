import random

class SensorReader:
    def __init__(self):
        pass
    def _mock_accel_data(self):
        #임의 가속 데이터
        return {
            "x": round(random.uniform(-2.0,2.0), 2),
            "y": round(random.uniform(-2.0,2.0), 2),
            "z": round(random.uniform(9.5,10.5), 2)
        }
    def _mock_gyro_data(self):
        #임의 자이로 데이터 
        return {
            "x": round(random.uniform(-250.0,250.0),1),
            "y": round(random.uniform(-250.0,250.0),1),
            "z": round(random.uniform(-250.0,250.0),1)
        }
    def _mock_gps_data(self):
        #임의 GPS 데이터
        return {
            "lat": round(random.uniform(33.0, 38.0), 5),
            "lon": round(random.uniform(126.0, 130.0), 5)
        }
    
    def get_sensor_data(self) -> dict:
        return{
            # caution: mock data
            "accel":self._mock_accel_data(),
            "zyro":self._mock_gyro_data(),
            "gps":self._mock_gps_data()
        }
    
if __name__ == "__main__":
    sensor_reader = SensorReader()
    data = sensor_reader.get_sensor_data()
    print(data)
