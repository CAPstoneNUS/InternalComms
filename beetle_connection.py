from bluepy import btle
import time
import struct
import random
from enum import Enum
from beetle_delegate import BeetleDelegate
from utils import getCRC
from threading import Timer

HS_SYN_PKT = "S"
HS_ACK_PKT = "A"
ATTACK_PKT = "K"
BOMB_PKT = "B"
KILL_PKT = "K"
SHIELD_PKT = "P"


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
        sender_queue (Queue): Shared queue for storing and passing data between threads.
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

    """

    def __init__(
        self,
        config,
        logger,
        mac_address,
        sender_queue,
        server_gun_state,
        server_vest_state,
        game_state,
    ):
        self.config = config
        self.logger = logger
        self.mac_address = mac_address
        self.sender_queue = sender_queue
        self.server_gun_state = server_gun_state
        self.server_vest_state = server_vest_state
        self.game_state = game_state
        self.beetle = None
        self.beetle_delegate = None
        self.beetle_state = BeetleState.DISCONNECTED
        self._syn_flag, self._ack_flag = False, False
        self.serial_service, self.serial_characteristic = None, None

        self.SERVICE_UUID = config["uuid"]["service"]
        self.CHARACTERISTIC_UUID = config["uuid"]["characteristic"]
        self.RESPONSE_TIMEOUT = config["time"]["response_timeout"]
        self.HANDSHAKE_INTERVAL = config["time"]["handshake_interval"]
        self.RECONNECTION_INTERVAL = config["time"]["reconnection_interval"]
        self.MAX_NOTIF_WAIT_TIME = config["time"]["max_notif_wait_time"]
        self.MAG_SIZE = self.config["storage"]["mag_size"]

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
                    # ------------------------- Data from Relay Server ------------------------- #
                    if (
                        not self.server_gun_state.empty()
                        and self.mac_address == self.config["device"]["beetle_1"]  # gun
                    ):
                        data = self.server_gun_state.get()
                        self.handleServerGunState(data)

                    if (
                        not self.server_vest_state.empty()
                        and self.mac_address
                        == self.config["device"]["beetle_3"]  # vest
                    ):
                        data = self.server_vest_state.get()
                        self.handleServerVestState(data)

                    # ---------------------------- Data from Beetle ---------------------------- #
                    if not self.beetle.waitForNotifications(self.MAX_NOTIF_WAIT_TIME):
                        self.logger.error("Failed to receive notifs. Disconnecting...")
                        self.forceDisconnect()

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
            self.logger.info("Connected!")

            self.serial_service = self.beetle.getServiceByUUID(self.SERVICE_UUID)
            self.serial_characteristic = self.serial_service.getCharacteristics(
                self.CHARACTERISTIC_UUID
            )[0]
            self.beetle_delegate = BeetleDelegate(
                self,
                self.config,
                self.logger,
                self.mac_address,
                self.sender_queue,
                self.game_state,
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
                self.sendSYNPacket()
                if not self.beetle.waitForNotifications(self.HANDSHAKE_INTERVAL):
                    self.logger.error("Failed to receive SYN.")
                    return False
                self._syn_flag = True

            if self._ack_flag:
                self.sendACKPacket()
                self.logger.info("Handshake successful!")
                return True

            return False

        except btle.BTLEDisconnectError:
            self.logger.error("Disconnected during handshake.")
            return False

    def forceDisconnect(self):
        """
        Forces a disconnection from the Beetle.

        This method is typically called when an error occurs or when ending the connection.
        """
        self.beetle.disconnect()
        self.beetle_state = BeetleState.DISCONNECTED

    # ---------------------------- Handshaking ---------------------------- #

    def sendSYNPacket(self):
        """
        Sends a SYN packet to the Beetle as part of the handshake process.
        """
        self.logger.info(f"<< Sending SYN...")
        self.beetle_delegate.resetSeqNum()

        # Sync game state
        if self.mac_address == self.config["device"]["beetle_1"]:  # gun
            currShot = self.game_state.getCurrShot()
            remainingBullets = self.game_state.getRemainingBullets()
            self.beetle_delegate._shots_fired = {i for i in range(1, currShot)}
            syn_packet = struct.pack(
                "b3B15x",
                ord(HS_SYN_PKT),
                self.beetle_delegate.sqn,
                currShot,
                remainingBullets,
            )
        elif self.mac_address == self.config["device"]["beetle_3"]:  # vest
            shield, health = self.game_state.getShieldHealth()
            syn_packet = struct.pack(
                "b3B15x", ord(HS_SYN_PKT), self.beetle_delegate.sqn, shield, health
            )
        else:
            syn_packet = struct.pack("b18x", ord(HS_SYN_PKT))

        crc = getCRC(syn_packet)
        syn_packet += struct.pack("B", crc)
        self.serial_characteristic.write(syn_packet)

    def sendACKPacket(self):
        """
        Sends an ACK packet to the Beetle as part of the handshake process.
        """
        if self._syn_flag:
            self.logger.info(f"<< Sending ACK...")
            ack_packet = struct.pack(
                "bB17x", ord(HS_ACK_PKT), self.beetle_delegate.sqn
            )
            crc = getCRC(ack_packet)
            ack_packet += struct.pack("B", crc)
            self.serial_characteristic.write(ack_packet)

    # ------------------------- Handling game state from above ------------------------- #

    def handleServerGunState(self, data):
        """
        Sends the gun state packet to the Beetle.

        Args:
            data (int): Dictionary containing remaining bullet from relay server.
        """
        bullets = data["bullets"]
        if bullets == self.MAG_SIZE:
            self.game_state.reload()
            self.beetle_delegate.sendReloadPacket()
        elif not bullets == self.game_state.getState()["bullets"]:
            print("Gun state client-server mismatch. Recalibrating...")
            self.game_state.updateGunState(bullets=bullets)
            self.beetle_delegate.sendGunStatePacket(bullets)
        else:
            print("Client-server gun states match.")

    def handleServerVestState(self, data):
        """
        Sends the vest state packet to the Beetle.

        Args:
            data (dict): Dictionary containing shield and health values from relay server.
        """
        shield, health = data["shield"], data["health"]
        if not (shield, health) == self.game_state.getShieldHealth():
            print("Changes to vest state detected. Recalibrating...")
            self.game_state.updateVestState(shield=shield, health=health)
            self.beetle_delegate.sendVestStatePacket(shield, health)
        else:
            print("Client-server vest states match.")

    def killBeetle(self):
        """
        Sends a reset command to the Beetle to reset the game state.
        """
        self.logger.info(
            f"---------------------- KILLING BEETLE ----------------------"
        )
        reset_packet = struct.pack("b18x", ord(KILL_PKT))
        crc = getCRC(reset_packet)
        reset_packet += struct.pack("B", crc)
        self.serial_characteristic.write(reset_packet)

    def writeCharacteristic(self, packet):
        self.serial_characteristic.write(packet)

    @property
    def syn_flag(self):
        return self._syn_flag

    @property
    def ack_flag(self):
        return self._ack_flag

    @ack_flag.setter
    def ack_flag(self, value):
        self._ack_flag = value
