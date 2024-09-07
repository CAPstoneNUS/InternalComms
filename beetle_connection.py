from bluepy import btle
import time
import struct
from beetle_delegate import BeetleDelegate
from utils import getCRC

class BeetleConnection:
    def __init__(self, config, mac_address, data_queue):
        self.mac_address = mac_address
        self.data_queue = data_queue
        self.beetle = None
        self.beetle_delegate = None

        self.syn_flag = False
        self.ack_flag = False
        self.has_handshake = False
        self.is_connected = False
        
        self.serial_service = None
        self.serial_characteristic = None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.CONNECTION_TIMEOUT = config["timeout"]["connection_timeout"]
        self.RECONNECTION_INTERVAL = config["timeout"]["reconnection_interval"]

    def startComms(self):
        while True:
            try:
                if not self.is_connected:
                    self.is_connected = self.openConnection()
                    if not self.is_connected:
                        print(f"Failed to connect. Retrying in {self.RECONNECTION_INTERVAL} seconds...")
                        time.sleep(self.RECONNECTION_INTERVAL)
                        continue

                if not self.has_handshake:
                    self.has_handshake = self.doHandshake()
                    if not self.has_handshake:
                        print("Handshake failed. Retrying...")

                if self.is_connected and self.has_handshake:
                    self.beetle.waitForNotifications(1.0)

            except KeyboardInterrupt:
                print("KeyboardInterrupt: Exiting...")
                return
                        
            except btle.BTLEDisconnectError:
                print("Beetle disconnected. Attempting to reconnect...")
                self.is_connected = False
                self.has_handshake = False
                time.sleep(self.RECONNECTION_INTERVAL)

    def openConnection(self):
        try:
            self.beetle = btle.Peripheral(self.mac_address)
            print("Connected to beetle: ", self.beetle)
            
            self.serial_service = self.beetle.getServiceByUUID(self.SERVICE_UUID)
            self.serial_characteristic = self.serial_service.getCharacteristics(self.CHARACTERISTIC_UUID)[0]
            self.beetle_delegate = BeetleDelegate(self, self.mac_address, self.data_queue)
            self.beetle.withDelegate(self.beetle_delegate)
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
        syn_packet = struct.pack('b18s', ord('S'), bytes(18))
        crc = getCRC(syn_packet)
        syn_packet += struct.pack('B', crc)
        self.serial_characteristic.write(syn_packet)

    def sendACK(self):
        print("Established handshake with beetle")
        ack_packet = struct.pack('b18s', ord('A'), bytes(18))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack('B', crc)
        self.serial_characteristic.write(ack_packet)

    def setACKFlag(self, value):
        self.ack_flag = value

    def getMACAddress(self):
        return self.mac_address