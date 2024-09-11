from bluepy import btle
import time
import struct
from enum import Enum
from beetle_delegate import BeetleDelegate
from utils import getCRC


class BeetleState(Enum):
    DISCONNECTED = 0
    CONNECTED = 1
    READY = 2


class BeetleConnection:
    def __init__(self, config, mac_address, data_queue):
        self.config = config
        self.mac_address = mac_address
        self.data_queue = data_queue
        self.beetle = None
        self.beetle_delegate = None

        self.syn_flag = False
        self.ack_flag = False
        self.beetle_state = BeetleState.DISCONNECTED

        self.serial_service = None
        self.serial_characteristic = None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.RECONNECTION_INTERVAL = config["timeout"]["reconnection_interval"]

    def startComms(self):
        while True:
            try:
                # Step 1: Open connection
                if self.beetle_state == BeetleState.DISCONNECTED:
                    if self.openConnection():
                        self.beetle_state = BeetleState.CONNECTED
                    else:
                        print(
                            f"Beetle {self.mac_address} failed to connect. Retrying in {self.RECONNECTION_INTERVAL} second(s)..."
                        )
                        time.sleep(self.RECONNECTION_INTERVAL)
                        continue

                # Step 2: Do handshake
                if self.beetle_state == BeetleState.CONNECTED:
                    if self.doHandshake():
                        self.beetle_state = BeetleState.READY
                    else:
                        print(
                            f"Beetle {self.mac_address} failed to handshake. Retrying..."
                        )

                # Step 3: Wait for notifications
                if self.beetle_state == BeetleState.READY:
                    self.beetle.waitForNotifications(1.0)

            except btle.BTLEDisconnectError:
                print(
                    f"Beetle {self.mac_address} disconnected. Reconnecting in {self.RECONNECTION_INTERVAL} second(s)..."
                )
                self.beetle_state = BeetleState.DISCONNECTED
                time.sleep(self.RECONNECTION_INTERVAL)

            except btle.BTLEException as e:
                print(f"Bluetooth error occurred: {e}")
                self.beetle_state = BeetleState.DISCONNECTED

            except Exception as e:
                print(f"Unexpected error occurred: {e}")
                self.beetle_state = BeetleState.DISCONNECTED

    def openConnection(self):
        try:
            self.beetle = btle.Peripheral(self.mac_address)
            print(f"Connected to Beetle {self.mac_address}")

            self.serial_service = self.beetle.getServiceByUUID(self.SERVICE_UUID)
            self.serial_characteristic = self.serial_service.getCharacteristics(
                self.CHARACTERISTIC_UUID
            )[0]
            self.beetle_delegate = BeetleDelegate(
                self, self.config, self.mac_address, self.data_queue
            )
            self.beetle.withDelegate(self.beetle_delegate)
            return True

        except btle.BTLEDisconnectError or btle.BTLEException as e:
            print(f"Connection to Beetle {self.mac_address} failed: {e}")
            return False

    def doHandshake(self):
        self.syn_flag = False
        self.ack_flag = False
        try:
            if not self.syn_flag:
                self.sendSYN()
                if not self.beetle.waitForNotifications(1.0):
                    print(f"Beetle {self.mac_address} failed to receive SYN")
                    return False
                self.syn_flag = True

            if self.ack_flag:
                self.sendACK()
                self.beetle.waitForNotifications(1.0)  # Recv IMU data
                return True

            self.syn_flag = False
            self.ack_flag = False
            return False

        except btle.BTLEDisconnectError:
            print(f"Beetle {self.mac_address} disconnected during handshake")
            return False

    def sendSYN(self):
        print(f"Sending SYN to Beetle {self.mac_address}")
        syn_packet = struct.pack("b18s", ord("S"), bytes(18))
        crc = getCRC(syn_packet)
        syn_packet += struct.pack("B", crc)
        self.serial_characteristic.write(syn_packet)

    def sendACK(self):
        print(f"Established handshake with Beetle {self.mac_address}")
        ack_packet = struct.pack("b18s", ord("A"), bytes(18))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.serial_characteristic.write(ack_packet)

    def setACKFlag(self, value):
        self.ack_flag = value

    def getMACAddress(self):
        return self.mac_address
