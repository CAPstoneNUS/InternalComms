from bluepy import btle
import time
import yaml
import struct

with open("config.yaml", "r") as file:
    config = yaml.safe_load(file)

BEETLE_1_MAC = config["device"]["beetle_1"]
BEETLE_2_MAC = config["device"]["beetle_2"]

SERVICE_UUID = config["uuid"]["service"]
CHARACTERISTIC_UUID = config["uuid"]["characteristic"]

IMU_PACKET_FORMAT = config["packet_format"]["imu"]
IMU_DATA_FILE = config["file"]["imu"]

CONNECTION_TIMEOUT = 1
RECONNECTION_INTERVAL = 5

class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, filename, serialService, serialCharacteristic, beetleConnection):
        btle.DefaultDelegate.__init__(self)
        self.file = open(filename, "w")
        self.serialService = serialService
        self.serialCharacteristic = serialCharacteristic
        self.beetleConnection = beetleConnection

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
        # Format: '<' (little-endian), 'b' (signed char), '6h' (6 short ints), '2b' (2 signed chars)
        unpacked_data = struct.unpack(IMU_PACKET_FORMAT, data)
        
        packet_type, accX, accY, accZ, gyrX, gyrY, gyrZ, checksum, eop = unpacked_data
        
        print("IMU Packet Received:")
        print(f"Packet Type: {chr(packet_type)}")
        print(f"Accelerometer: X={accX}, Y={accY}, Z={accZ}")
        print(f"Gyroscope: X={gyrX}, Y={gyrY}, Z={gyrZ}")
        print(f"Checksum: {checksum}")
        print(f"EOP: {chr(eop)}")
        
        self.file.write(f"{accX},{accY},{accZ},{gyrX},{gyrY},{gyrZ}\n")
        self.file.flush()



class BeetleConnection:
    def __init__(self, macAddress):
        self.macAddress = macAddress
        self.beetle = None
        self.beetleDelegate = None

        self.syn_flag = False
        self.ack_flag = False
        self.hasHandshake = False
        self.isConnected = False
        
        self.serialService = None
        self.serialCharacteristic = None

    def startComms(self):
        while True:
            try:
                if not self.isConnected:
                    self.isConnected = self.openConnection()
                    if not self.isConnected:
                        print(f"Failed to connect. Retrying in {RECONNECTION_INTERVAL} seconds...")
                        time.sleep(RECONNECTION_INTERVAL)
                        continue

                if not self.hasHandshake:
                    self.hasHandshake = self.doHandshake()
                    if not self.hasHandshake:
                        print("Handshake failed. Retrying...")

                if self.isConnected and self.hasHandshake:
                    self.beetle.waitForNotifications(1.0)

            except KeyboardInterrupt:
                print("KeyboardInterrupt: Exiting...")
                return
                        
            except btle.BTLEDisconnectError:
                print("Beetle disconnected. Attempting to reconnect...")
                self.isConnected = False
                self.hasHandshake = False
                time.sleep(RECONNECTION_INTERVAL)


    def openConnection(self):
        try:
            # Connect
            self.beetle = btle.Peripheral(self.macAddress)
            print("Connected to beetle: ", self.beetle)
            
            # Set attributes
            self.serialService = self.beetle.getServiceByUUID(SERVICE_UUID)
            self.serialCharacteristic = self.serialService.getCharacteristics(CHARACTERISTIC_UUID)[0]
            self.beetleDelegate = BeetleDelegate(IMU_DATA_FILE, self.serialService, self.serialCharacteristic, self)
            self.beetle.withDelegate(self.beetleDelegate)
            return True

        except btle.BTLEDisconnectError:
            print("Connection failed")
            return False
        

    def doHandshake(self):
        self.syn_flag = False
        self.ack_flag = False
        try:
            if not self.syn_flag:
                self.sendSYN()
                if not self.beetle.waitForNotifications(1.0):
                    return False
                self.syn_flag = True

            if self.ack_flag:
                self.sendACK()
                self.beetle.waitForNotifications(1.0) # Recv IMU data
                return True
            
            return False
    
        except btle.BTLEDisconnectError:
            print("Disconnected during handshake")
            return False


    def sendSYN(self):
        print("Sending SYN to beetle")
        self.serialCharacteristic.write(bytes('S', encoding="utf-8"))

    def sendACK(self):
        print("Established handshake with beetle")
        self.serialCharacteristic.write(bytes('A', encoding="utf-8"))

    def setACKFlag(self, value):
        self.ack_flag = value

def main():
    try:
        beetle_1 = BeetleConnection(BEETLE_1_MAC)
        beetle_1.startComms()

    
    except btle.BTLEDisconnectError:
        print("Beetle disconnected")
        return

if __name__ == '__main__':
    main()
