from bluepy import btle
import struct

class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetleConnection, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetleConnection = beetleConnection
        self.data_queue = data_queue

    def handleNotification(self, cHandle, data):
        packet_type = data[0]
        if packet_type == ord('A'):
            self.beetleConnection.setACKFlag(True)
            return
        elif packet_type == ord('M'):
            if len(data) != 15:
                print(f"Invalid IMU packet length: {len(data)}")
                return
            
            self.processIMUPacket(data)
        else:
            print(f"Unknown packet type: {chr(packet_type)}")


    def processIMUPacket(self, data):
        unpacked_data = struct.unpack("<b6h2b", data)
        packet_type, accX, accY, accZ, gyrX, gyrY, gyrZ, checksum, eop = unpacked_data
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
