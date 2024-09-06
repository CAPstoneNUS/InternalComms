import time
import struct
from bluepy import btle
from utils import getCRC, displayTransmissionSpeed


class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetleConnection, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetleConnection = beetleConnection
        self.data_queue = data_queue
        self.buffer = bytearray()

        # For transmission speed stats
        self.start_time = time.time()
        self.total_data_size = 0

    def handleNotification(self, cHandle, data):
        self.buffer.extend(data)

        # For transmission speed stats
        end_time = time.time()
        time_diff = end_time - self.start_time
        self.total_data_size += len(data)
        if time_diff >= 3:
            displayTransmissionSpeed(time_diff, self.total_data_size)
            self.start_time = time.time()
            self.total_data_size = 0

        while len(self.buffer) >= 20:
            packet = self.buffer[:20]
            self.buffer = self.buffer[20:]

            packet_type = packet[0]
            calculated_crc = getCRC(packet[:-1])
            true_crc = struct.unpack("<B", packet[-1:])[0]

            if calculated_crc == true_crc:
                if packet_type == ord('A'):
                    self.beetleConnection.setACKFlag(True)
                    return
                elif packet_type == ord('M'):
                    self.processIMUPacket(data)
                else:
                    print(f"Unknown packet type: {chr(packet_type)}")
            else:
                print("CRC check failed. Discarding data...")

        self.buffer = bytearray() # Discard any remaining data


    def processIMUPacket(self, data):
        unpacked_data = struct.unpack("<b6h6xB", data)
        _, accX, accY, accZ, gyrX, gyrY, gyrZ, _ = unpacked_data
        beetle_id = (self.beetleConnection.getMACAddress())[-2:]
        imu_data = {
            'id': beetle_id,
            'accX': accX, 'accY': accY, 'accZ': accZ,
            'gyrX': gyrX, 'gyrY': gyrY, 'gyrZ': gyrZ,
        }
        if self.data_queue.qsize() < 1000:
            self.data_queue.put(imu_data)
        else:
            print("Data queue is full. Discarding data...")
