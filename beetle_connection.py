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
    def __init__(self, config, logger, mac_address, data_queue):
        self.config = config
        self.logger = logger
        self.mac_address = mac_address
        self.data_queue = data_queue
        self.beetle = None
        self.beetle_delegate = None
        self.beetle_state = BeetleState.DISCONNECTED
        self.syn_flag, self.ack_flag = False, False
        self.serial_service, self.serial_characteristic = None, None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.HANDSHAKE_INTERVAL = config["timeout"]["handshake_interval"]
        self.RECONNECTION_INTERVAL = config["timeout"]["reconnection_interval"]

    def startComms(self):
        while True:
            try:
                # Step 1: Open connection
                if self.beetle_state == BeetleState.DISCONNECTED:
                    if self.openConnection():
                        self.beetle_state = BeetleState.CONNECTED
                    else:
                        self.logger.error(
                            f"Connection failed. Retrying in {self.RECONNECTION_INTERVAL} second(s)..."
                        )
                        time.sleep(self.RECONNECTION_INTERVAL)
                        continue

                # Step 2: Do handshake
                if self.beetle_state == BeetleState.CONNECTED:
                    if self.doHandshake():
                        self.logger.info(
                            f"Handshake successful! Ready to receive data."
                        )
                        self.beetle_state = BeetleState.READY
                    else:
                        self.logger.error(
                            f"Handshake failed. Retrying in {self.HANDSHAKE_INTERVAL} second(s)..."
                        )
                        time.sleep(self.HANDSHAKE_INTERVAL)

                # Step 3: Wait for notifications
                if self.beetle_state == BeetleState.READY:
                    self.beetle.waitForNotifications(1.0)

            except btle.BTLEDisconnectError:
                self.logger.error(
                    f"Disconnected. Reconnecting in {self.RECONNECTION_INTERVAL} second(s)..."
                )
                self.beetle_state = BeetleState.DISCONNECTED
                time.sleep(self.RECONNECTION_INTERVAL)

            except btle.BTLEException as e:
                self.logger.error(f"Bluetooth error occurred: {e}")
                self.beetle_state = BeetleState.DISCONNECTED

            except Exception as e:
                self.logger.exception(f"Unexpected error occurred: {e}")
                self.beetle_state = BeetleState.DISCONNECTED

    def openConnection(self):
        try:
            self.beetle = btle.Peripheral(self.mac_address)
            self.logger.info(f"Connected!")

            self.serial_service = self.beetle.getServiceByUUID(self.SERVICE_UUID)
            self.serial_characteristic = self.serial_service.getCharacteristics(
                self.CHARACTERISTIC_UUID
            )[0]
            self.beetle_delegate = BeetleDelegate(
                self, self.config, self.logger, self.mac_address, self.data_queue
            )
            self.beetle.withDelegate(self.beetle_delegate)
            return True

        except btle.BTLEDisconnectError or btle.BTLEException as e:
            self.logger.error(e)
            return False

    def doHandshake(self):
        self.syn_flag, self.ack_flag = False, False
        try:
            if not self.syn_flag:
                self.sendSYN()
                if not self.beetle.waitForNotifications(1.0):
                    self.logger.error(f"Failed to receive SYN.")
                    return False
                self.syn_flag = True

            if self.ack_flag:
                self.sendACK()
                if self.beetle.waitForNotifications(1.0):
                    return True

            self.syn_flag, self.ack_flag = False, False
            return False

        except btle.BTLEDisconnectError:
            self.logger.error(f"Disconnected during handshake.")
            return False

    def forceDisconnect(self):
        self.beetle_state = BeetleState.DISCONNECTED

    def sendSYN(self):
        self.logger.info(f"Sending SYN...")
        syn_packet = struct.pack("b18s", ord("S"), bytes(18))
        crc = getCRC(syn_packet)
        syn_packet += struct.pack("B", crc)
        self.serial_characteristic.write(syn_packet)

    def sendACK(self):
        self.logger.info(f"Sending ACK...")
        ack_packet = struct.pack("b18s", ord("A"), bytes(18))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.serial_characteristic.write(ack_packet)

    def setACKFlag(self, value):
        self.ack_flag = value

    def getMACAddress(self):
        return self.mac_address
