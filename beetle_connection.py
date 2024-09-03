from bluepy import btle
import time
from beetle_delegate import BeetleDelegate

class BeetleConnection:
    def __init__(self, config, macAddress):
        self.macAddress = macAddress
        self.beetle = None
        self.beetleDelegate = None

        self.syn_flag = False
        self.ack_flag = False
        self.hasHandshake = False
        self.isConnected = False
        
        self.serialService = None
        self.serialCharacteristic = None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.IMU_PACKET_FORMAT = config["packet_format"]["imu"]
        self.IMU_DATA_FILE = config["file"]["imu"]
        self.CONNECTION_TIMEOUT = 1
        self.RECONNECTION_INTERVAL = 5

    def startComms(self):
        while True:
            try:
                if not self.isConnected:
                    self.isConnected = self.openConnection()
                    if not self.isConnected:
                        print(f"Failed to connect. Retrying in {self.RECONNECTION_INTERVAL} seconds...")
                        time.sleep(self.RECONNECTION_INTERVAL)
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
                time.sleep(self.RECONNECTION_INTERVAL)

    def openConnection(self):
        try:
            self.beetle = btle.Peripheral(self.macAddress)
            print("Connected to beetle: ", self.beetle)
            
            self.serialService = self.beetle.getServiceByUUID(self.SERVICE_UUID)
            self.serialCharacteristic = self.serialService.getCharacteristics(self.CHARACTERISTIC_UUID)[0]
            self.beetleDelegate = BeetleDelegate(self.IMU_DATA_FILE, self.serialService, self.serialCharacteristic, self, self.IMU_PACKET_FORMAT)
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
                self.beetle.waitForNotifications(1.0)  # Recv IMU data
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
