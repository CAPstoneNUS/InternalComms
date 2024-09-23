from bluepy import btle
import time
import struct
import random
from enum import Enum
from beetle_delegate import BeetleDelegate
from utils import getCRC


class BeetleState(Enum):
    DISCONNECTED = 0
    CONNECTED = 1
    READY = 2


class BeetleConnection:
    """
    Manages the connection and communication with a Beetle.

    This class handles the entire lifecycle of a Bluetooth connection with a Beetle,
    including connection establishment, handshake, data transfer, and disconnection.

    Attributes:
        config (dict): Configuration dictionary loaded from the config.yaml file.
        logger (Logger): Logger object for recording events and errors.
        mac_address (str): MAC address of the Beetle.
        data_queue (Queue): Shared queue for storing and passing data between threads.
        beetle (Peripheral): Bluetooth peripheral object for the Beetle.
        beetle_state (BeetleState): Current state of the connection.
        _syn_flag (bool): Flag to indicate if SYN packet has been sent.
        _ack_flag (bool): Flag to indicate if ACK packet has been received.
        serial_service (Service): Bluetooth service object for serial communication.
        serial_characteristic (Characteristic): Bluetooth characteristic object for serial communication.

        SERVICE_UUID (str): UUID of the Bluetooth service.
        CHARACTERISTIC_UUID (str): UUID of the Bluetooth characteristic.
        HANDSHAKE_INTERVAL (float): Time interval for handshake attempts.
        RECONNECTION_INTERVAL (int): Time interval for reconnection attempts.
        MIN_RELOAD_INTERVAL (int): Minimum time interval between reload requests.
        MAX_RELOAD_INTERVAL (int): Maximum time interval between reload requests.

        _reload_in_progress (bool): Flag to indicate if a reload request is in progress.
        last_reload_time (float): Time of the last reload request.
        reload_interval (float): Time interval between reload requests.
    """

    def __init__(self, config, logger, mac_address, data_queue):
        self.config = config
        self.logger = logger
        self.mac_address = mac_address
        self.data_queue = data_queue
        self.beetle = None
        self.beetle_delegate = None
        self.beetle_state = BeetleState.DISCONNECTED
        self._syn_flag, self._ack_flag = False, False
        self.serial_service, self.serial_characteristic = None, None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.HANDSHAKE_INTERVAL = config["time"]["handshake_interval"]
        self.RECONNECTION_INTERVAL = config["time"]["reconnection_interval"]
        self.MIN_RELOAD_INTERVAL = config["time"]["min_reload_interval"]
        self.MAX_RELOAD_INTERVAL = config["time"]["max_reload_interval"]

        # For random reload request
        self._reload_in_progress = False
        self.last_reload_time = time.time()
        self.reload_interval = random.uniform(
            self.MIN_RELOAD_INTERVAL, self.MAX_RELOAD_INTERVAL
        )

    def startComms(self):
        """
        Starts and maintains communication with the Beetle.

        This method runs in a loop, handling connection, handshake, and data transfer.
        It also manages error cases and reconnection attempts.
        """
        while True:
            try:
                # Step 1: Open connection
                if self.beetle_state == BeetleState.DISCONNECTED:
                    if self.openConnection():
                        self.beetle_state = BeetleState.CONNECTED
                    else:
                        self.logger.error(
                            f"Reconnecting in {self.RECONNECTION_INTERVAL} second(s)..."
                        )
                        time.sleep(self.RECONNECTION_INTERVAL)

                # Step 2: Do handshake
                if self.beetle_state == BeetleState.CONNECTED:
                    if self.doHandshake():
                        self.beetle_state = BeetleState.READY
                    else:
                        self.logger.error(
                            f"Handshake failed. Retrying in {self.HANDSHAKE_INTERVAL} second(s)..."
                        )
                        time.sleep(self.HANDSHAKE_INTERVAL)

                # Step 3: Wait for notifications
                if self.beetle_state == BeetleState.READY:
                    if not self.beetle.waitForNotifications(10):
                        self.logger.error(
                            f"Failed to receive notifications. Disconnecting..."
                        )
                        self.forceDisconnect()

                    # Simulate reload request
                    current_time = time.time()
                    if (
                        current_time - self.last_reload_time >= self.reload_interval
                    ) and (self.mac_address == self.config["device"]["beetle_1"]):
                        self.sendReload()
                        self.last_reload_time = current_time
                        self.reload_interval = random.uniform(
                            self.MIN_RELOAD_INTERVAL, self.MAX_RELOAD_INTERVAL
                        )

            except btle.BTLEDisconnectError:
                self.logger.error(
                    f"Disconnected. Reconnecting in {self.RECONNECTION_INTERVAL} second(s)..."
                )
                self.forceDisconnect()
                time.sleep(self.RECONNECTION_INTERVAL)

            except btle.BTLEException as e:
                self.logger.error(f"Bluetooth error occurred: {e}")
                self.forceDisconnect()

            except Exception as e:
                self.logger.exception(f"Unexpected error occurred: {e}")
                self.forceDisconnect()

    def openConnection(self):
        """
        Establishes a Bluetooth connection with the Beetle.

        Returns:
            bool: True if connection is successful, False otherwise.
        """
        try:
            self.beetle = btle.Peripheral()
            self.beetle.connect(self.mac_address)
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
            self.logger.error(f"Connection failed: {e}")
            return False

    def doHandshake(self):
        """
        Performs the handshake protocol with the connected Beetle.

        Returns:
            bool: True if handshake is successful, False otherwise.
        """
        self._syn_flag, self._ack_flag = False, False
        try:
            if not self._syn_flag:
                self.sendSYN()
                if not self.beetle.waitForNotifications(1.0):
                    self.logger.error(f"Failed to receive SYN.")
                    return False
                self._syn_flag = True

            if self._ack_flag:
                self.sendACK()
                self.logger.info(f"Handshake successful!")
                return True

            return False

        except btle.BTLEDisconnectError:
            self.logger.error(f"Disconnected during handshake.")
            return False

    def sendReload(self):
        """
        Sends a reload signal to the Beetle.

        This method is used to initiate a reload operation on the Beetle.
        """
        if not self._reload_in_progress:
            self._reload_in_progress = True
            self.logger.info("<< Relaying RELOAD signal from above...")
            reload_packet = struct.pack("<b18x", ord("R"))
            crc = getCRC(reload_packet)
            reload_packet += struct.pack("B", crc)
            self.serial_characteristic.write(reload_packet)
            if not self.beetle.waitForNotifications(1.0):
                self.handleReloadTimeout()

    def handleReloadTimeout(self):
        """
        Handles the case when a reload operation times out.

        Attempts to resend the reload signal if a timeout occurs.
        """
        if self._reload_in_progress:
            self.logger.warning("Reload timeout. Resending RELOAD signal.")
            self.sendReload()

    def forceDisconnect(self):
        """
        Forces a disconnection from the Beetle.

        This method is typically called when an error occurs or when ending the connection.
        """
        self.beetle.disconnect()
        self.beetle_state = BeetleState.DISCONNECTED

    def sendSYN(self):
        """
        Sends a SYN packet to the Beetle as part of the handshake process.
        """
        self.logger.info(f"<< Sending SYN...")
        syn_packet = struct.pack("b18s", ord("S"), bytes(18))
        crc = getCRC(syn_packet)
        syn_packet += struct.pack("B", crc)
        self.serial_characteristic.write(syn_packet)

    def sendACK(self):
        """
        Sends an ACK packet to the Beetle as part of the handshake process.
        """
        if self._syn_flag:
            self.logger.info(f"<< Sending ACK...")
            ack_packet = struct.pack("b18s", ord("A"), bytes(18))
            crc = getCRC(ack_packet)
            ack_packet += struct.pack("B", crc)
            self.serial_characteristic.write(ack_packet)

    def writeCharacteristic(self, data):
        self.serial_characteristic.write(data)

    @property
    def syn_flag(self):
        return self._syn_flag

    @property
    def ack_flag(self):
        return self._ack_flag

    @ack_flag.setter
    def ack_flag(self, value):
        self._ack_flag = value

    @property
    def reload_in_progress(self):
        return self._reload_in_progress

    @reload_in_progress.setter
    def reload_in_progress(self, value):
        self._reload_in_progress = value
