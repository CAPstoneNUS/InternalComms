from bluepy import btle
import struct

class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, filename, serialService, serialCharacteristic, beetleConnection, imu_packet_format):
        btle.DefaultDelegate.__init__(self)
        self.file = open(filename, "w")
        self.serialService = serialService
        self.serialCharacteristic = serialCharacteristic
        self.beetleConnection = beetleConnection
        self.IMU_PACKET_FORMAT = imu_packet_format

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

    def __del__(self):
        self.file.close()

    def processIMUPacket(self, data):
        unpacked_data = struct.unpack(self.IMU_PACKET_FORMAT, data)
        
        packet_type, accX, accY, accZ, gyrX, gyrY, gyrZ, checksum, eop = unpacked_data
        
        print("IMU Packet Received:")
        print(f"Packet Type: {chr(packet_type)}")
        print(f"Accelerometer: X={accX}, Y={accY}, Z={accZ}")
        print(f"Gyroscope: X={gyrX}, Y={gyrY}, Z={gyrZ}")
        print(f"Checksum: {checksum}")
        print(f"EOP: {chr(eop)}")
        
        self.file.write(f"{accX},{accY},{accZ},{gyrX},{gyrY},{gyrZ}\n")
        self.file.flush()
