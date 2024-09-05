from bluepy import btle
import struct

class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetleConnection, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetleConnection = beetleConnection
        self.data_queue = data_queue
        self.buffer = bytearray()

    def handleNotification(self, cHandle, data):
        self.buffer.extend(data)

        while len(self.buffer) >= 20:
            packet = self.buffer[:20]
            self.buffer = self.buffer[20:]

            packet_type = packet[0]
            if packet_type == ord('A'):
                self.beetleConnection.setACKFlag(True)
                return
            elif packet_type == ord('M'):
                self.processIMUPacket(data)
            else:
                print(f"Unknown packet type: {chr(packet_type)}")


    def processIMUPacket(self, data):
        unpacked_data = struct.unpack("<b6h6xb", data)
        packet_type, accX, accY, accZ, gyrX, gyrY, gyrZ, checksum = unpacked_data
        mac = self.beetleConnection.getMACAddress()

        data = {
            'mac_address': mac,
            'accX': accX, 'accY': accY, 'accZ': accZ,
            'gyrX': gyrX, 'gyrY': gyrY, 'gyrZ': gyrZ
        }
        if self.data_queue.qsize() < 1000:
            self.data_queue.put(data)
        else:
            print("Data queue is full. Discarding data...")
