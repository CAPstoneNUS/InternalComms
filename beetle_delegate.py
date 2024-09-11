import time
import struct
from bluepy import btle
from utils import getCRC, displayTransmissionSpeed


class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetleConnection, config, mac_address, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetleConnection = beetleConnection
        self.config = config
        self.beetle_id = mac_address[-2:]
        self.data_queue = data_queue
        self.buffer = bytearray()
        self.frag_packet_count = 0

        # # For transmission speed stats
        # self.start_time = time.time()
        # self.total_data_size = 0

    def handleNotification(self, cHandle, data):

        # # For transmission speed stats
        # end_time = time.time()
        # time_diff = end_time - self.start_time
        # self.total_data_size += len(data)
        # if time_diff >= 3:
        #     displayTransmissionSpeed(time_diff, self.total_data_size)
        #     self.start_time = time.time()
        #     self.total_data_size = 0

        if len(self.buffer) + len(data) > 1000:
            print("Buffer size limit exceeded. Discarding oldest data.")
            self.buffer = self.buffer[-(1000 - len(data)) :]

        self.buffer.extend(data)

        while len(self.buffer) >= 20:
            # Splice packet from buffer
            packet = self.buffer[:20]
            self.buffer = self.buffer[20:]

            # Extract packet type and CRC
            packet_type = packet[0]
            calculated_crc = getCRC(packet[:-1])
            true_crc = struct.unpack("<B", packet[-1:])[0]

            if calculated_crc == true_crc:
                if packet_type == ord("A"):
                    self.beetleConnection.setACKFlag(True)
                    return
                elif packet_type == ord("M"):
                    self.processIMUPacket(packet[1:-1])
                else:
                    print(f"Unknown packet type: {chr(packet_type)}")
            else:
                print("CRC check failed. Discarding data...")

        if len(self.buffer) > 0:
            self.frag_packet_count += 1
            # print(
            #     f"Fragmented packet count on Beetle {self.beetle_id}: {self.frag_packet_count}"
            # )

    def processIMUPacket(self, data):
        unpacked_data = struct.unpack("<6h6x", data)
        accX, accY, accZ, gyrX, gyrY, gyrZ = unpacked_data
        imu_data = {
            "id": self.beetle_id,
            "accX": accX,
            "accY": accY,
            "accZ": accZ,
            "gyrX": gyrX,
            "gyrY": gyrY,
            "gyrZ": gyrZ,
        }
        if self.data_queue.qsize() < 2000:
            self.data_queue.put(imu_data)
        else:
            print("Data queue is full. Discarding data...")
