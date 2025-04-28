
# decoder.py
"""
재조립된 바이트 메시지를 압축 해제하고 센서값 딕셔너리로 변환
"""
import zlib

def decompress_data(data: bytes):
    try:
        raw = zlib.decompress(data)
    except zlib.error:
        return None
    try:
        parts = raw.decode('utf-8').split(',')
        if len(parts) != 9:
            return None
        keys = [
            'accel_x','accel_y','accel_z',
            'gyro_x','gyro_y','gyro_z',
            'gps_lat','gps_lon','gps_alt'
        ]
        values = [float(p) for p in parts]
        return dict(zip(keys, values))
    except Exception:
        return None