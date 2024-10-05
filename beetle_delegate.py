import time
import struct
import random
import itertools
from bluepy import btle
from threading import Timer
from collections import deque
from utils import getCRC, getTransmissionSpeed, logPacketStats

# Packet types (PKT)
HS_SYNACK_PKT = "A"
IMU_DATA_PKT = "M"
GUN_PKT = "G"
RELOAD_PKT = "R"
ATTACK_PKT = "K"
BOMB_PKT = "B"
SHIELD_PKT = "P"
VESTSHOT_PKT = "V"
GUN_ACK_PKT = "X"
VESTSHOT_ACK_PKT = "Z"
GUN_NAK_PKT = "T"
BOMB_SYNACK_PKT = "C"
RELOAD_SYNACK_PKT = "Y"
ATTACK_SYNACK_PKT = "E"
SHIELD_SYNACK_PKT = "Q"
NAK_PKT = "L"


class BeetleDelegate(btle.DefaultDelegate):
    """
    Handles notifications from the Beetle.

    This class extends the DefaultDelegate class from the bluepy library
    and implements the handleNotification() method to process incoming data
    from the Beetle device.

    Attributes:
        beetle_connection (BeetleConnection): BeetleConnection object for communication.
        config (dict): Configuration dictionary loaded from the config.yaml file.
        logger (Logger): Logger object for recording events and errors.
        beetle_id (str): Last two characters of the Beetle device MAC address.
        data_queue (Queue): Shared queue for storing and passing data between threads.
        buffer (deque): Deque to store incoming data packets.

        start_time (float): Start time for calculating transmission speed.
        total_window_data (int): Total size of data received in bytes. Reset everytime stats are displayed.
        total_data (int): Total size of data received in bytes since start.

        unacknowledged_gunshots (set): Set to temporarily store Shot IDs with pending ACKs.
        successful_gunshots (set): Set to store Shot IDs that have been successfully acknowledged.

        frag_packet_count (int): Count of fragmented packets received.
        corrupt_packet_count (int): Count of corrupted packets received.
        dropped_packet_count (int): Count of packets dropped.

        MAX_BUFFER_SIZE (int): Maximum buffer size for storing incoming data.
        MAX_QUEUE_SIZE (int): Maximum queue size for storing IMU data.
        MAX_CORRUPT_PKT_PCT (int): Maximum percentage of corrupt packets to tolerate before disconnecting.
        PACKET_SIZE (int): Size of each data packet.
        RESEND_PKT_TIMEOUT (int): Timeout duration for gun shot acknowledgements.
        STATS_LOG_INTERVAL (int): Interval for displaying transmission speed stats.
    """

    def __init__(
        self, beetle_connection, config, logger, mac_address, data_queue, game_state
    ):
        # Initialize the DefaultDelegate class
        btle.DefaultDelegate.__init__(self)
        self.beetle_connection = beetle_connection
        self.config = config
        self.logger = logger
        self.beetle_id = mac_address[-2:]
        self.data_queue = data_queue
        self.game_state = game_state
        self.buffer = deque()

        # Transmission speed stats
        self.start_time = time.time()
        self.total_window_data = 0
        self.total_data = 0

        # Gun handling
        self._gunshot_in_progress = False
        self.successful_gunshots = set()
        self.unacknowledged_gunshots = set()
        self.expected_gunshot_id = 1

        # Vest handling
        self._vestshot_in_progress = False
        self.registered_vestshots = set()
        self.unacknowledged_vestshots = set()

        # Counters
        self.frag_packet_count = 0
        self.corrupt_packet_count = 0
        self.total_corrupted_packets = 0
        self.dropped_packet_count = 0

        # Simulation mode
        self.simulation_mode = True
        self._action_in_progress = False

        # Configuration parameters
        self.MAG_SIZE = self.config["storage"]["mag_size"]
        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.MAX_CORRUPT_PKT_PCT = self.config["storage"]["max_corrupt_pkt_pct"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]
        self.RESEND_PKT_TIMEOUT = self.config["time"]["resend_pkt_timeout"]
        self.STATS_LOG_INTERVAL = config["time"]["stats_log_interval"]

    def handleNotification(self, cHandle, data):
        """
        Reads from the Beetle characteristic and processes the incoming data.

        Args:
            cHandle (int): The characteristic handle from which the data was received.
            data (bytes): The data received from the Beetle
        """
        # # Randomly corrupt data for testing
        # if random.random() <= 0.1:
        #     self.logger.warning(
        #         f"Corrupting packet {chr(struct.unpack('B', data[0:1])[0])}..."
        #     )
        #     self.corrupt_packet_count += 1
        #     data = bytearray([random.randint(0, 255) for _ in range(len(data))])

        # # Randomly drop packets for testing
        # if random.random() <= 0.1:
        #     self.logger.warning(
        #         f"Dropping packet {chr(struct.unpack('B', data[0:1])[0])}..."
        #     )
        #     self.dropped_packet_count += 1
        #     return

        # Add incoming data to buffer
        self.buffer.extend(data)
        self.total_data += len(data)

        while len(self.buffer) >= self.PACKET_SIZE:
            # Extract a single packet from buffer
            packet = bytes(itertools.islice(self.buffer, self.PACKET_SIZE))
            for _ in range(self.PACKET_SIZE):
                self.buffer.popleft()

            # Parse packet type and CRC
            packet_type = chr(packet[0])
            calculated_crc = getCRC(packet[:-1])
            true_crc = struct.unpack("<B", packet[-1:])[0]

            # Check CRC
            if calculated_crc != true_crc:
                ### TODO figure out NAK later
                self.logger.error("CRC mismatch. DROPPING packet...")
                # self.logger.error("Packet corrupted. Requesting retransmission...")
                # self.sendNAKPacket()
                self.corrupt_packet_count += 1
                self.total_corrupted_packets += 1
                if (
                    self.corrupt_packet_count
                    >= self.MAX_CORRUPT_PKT_PCT * 0.01 * self.total_data
                ):
                    self.logger.error(
                        "Corrupt packet limit reached. Force disconnecting..."
                    )
                    self.beetle_connection.forceDisconnect()
                    self.corrupt_packet_count = 0
                return

            payload = packet[1:-1]

            # Handle packet based on type
            if packet_type == HS_SYNACK_PKT:
                self.handleSYNACKPacket()
            elif packet_type == IMU_DATA_PKT:
                self.handleIMUPacket(payload)
            elif packet_type == GUN_PKT:
                self.handleGunPacket(payload)
            elif packet_type == GUN_ACK_PKT:
                self.handleGunACK(payload)
            elif packet_type == RELOAD_SYNACK_PKT:
                self.handleReloadSYNACK()
            elif packet_type == VESTSHOT_PKT:
                self.handleVestPacket(payload)
            elif packet_type == VESTSHOT_ACK_PKT:
                self.handleVestACK(payload)
            elif packet_type == BOMB_SYNACK_PKT:
                self.handleBombSYNACK(payload)
            elif packet_type == ATTACK_SYNACK_PKT:
                self.handleAttackSYNACK(payload)
            elif packet_type == SHIELD_SYNACK_PKT:
                self.handleShieldSYNACK(payload)
            else:
                self.logger.error(f"Unknown packet type: {packet_type}")

        # Check for fragmented packets
        if len(self.buffer) > 0:
            self.frag_packet_count += 1

        # Check buffer size and discard oldest data if overflow
        if len(self.buffer) > self.MAX_BUFFER_SIZE:
            overflow = len(self.buffer) - self.MAX_BUFFER_SIZE
            self.logger.warning(f"Buffer overflow. Discarding {overflow} bytes.")
            for _ in range(overflow):
                self.buffer.popleft()

        # # Display stats every 5 seconds
        # end_time = time.time()
        # time_diff = end_time - self.start_time
        # self.total_window_data += len(data)
        # if time_diff >= self.STATS_LOG_INTERVAL:
        #     speed_kbps = getTransmissionSpeed(time_diff, self.total_window_data)
        #     logPacketStats(
        #         self.logger,
        #         speed_kbps,
        #         self.total_corrupted_packets,
        #         self.dropped_packet_count,
        #         self.frag_packet_count,
        #     )
        #     self.start_time = time.time()
        #     self.total_window_data = 0

    def sendNAKPacket(self):
        """
        Sends a retransmission request for the corrupted packet.
        """
        self.logger.info(f"<< Sending NAK...")
        nak_packet = struct.pack("<b18x", ord(NAK_PKT))
        crc = getCRC(nak_packet)
        nak_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(nak_packet)

    def handleSYNACKPacket(self):
        """
        Handles the SYN-ACK handshake packet from the Beetle.
        """
        if self.beetle_connection.syn_flag and self.beetle_connection.ack_flag:
            self.logger.warning(">> Duplicate SYN-ACK received. Dropping packet...")
            return
        self.logger.info(">> SYN-ACK received.")
        self.beetle_connection.ack_flag = True

    def handleIMUPacket(self, data):
        """
        Puts the IMU data packet into the shared data queue.

        Args:
            data (bytes): The IMU data packet.
        """
        unpacked_data = struct.unpack("<6h6x", data)
        accX, accY, accZ, gyrX, gyrY, gyrZ = unpacked_data
        imu_data = {
            "id": self.beetle_id,
            "type": IMU_DATA_PKT,
            "accX": accX,
            "accY": accY,
            "accZ": accZ,
            "gyrX": gyrX,
            "gyrY": gyrY,
            "gyrZ": gyrZ,
        }
        if self.data_queue.qsize() > self.MAX_QUEUE_SIZE:
            self.logger.warning("Data queue full. Discarding oldest data...")
            self.data_queue.get()

        self.data_queue.put(imu_data)

    def simulateRandomAction(self):
        """
        Simulates a random action (ATTACK/SHIELD/BOMB) for testing purposes.
        Does NOT simulate RELOAD packets, those occur when the gun is out of bullets.
        """
        if self._action_in_progress:
            self.logger.warning("Action already in progress. Ignoring...")
            return

        if self._gunshot_in_progress:
            self.logger.warning("Gunshot in progress. Ignoring...")
            return

        action = random.choice([ATTACK_PKT, BOMB_PKT, SHIELD_PKT])

        if action == ATTACK_PKT:
            self.game_state.applyDamage(10)  # attacks do 10 damage
        elif action == BOMB_PKT:
            self.game_state.applyDamage(5)  # bombs do 5 damage
        elif action == SHIELD_PKT:
            self.game_state.refreshShield()

        self.logger.info(f"Simulating action: {action}")
        self.simulateAction(action)

    def simulateAction(self, action):
        self._action_in_progress = True
        action_packet = struct.pack("<b18x", ord(action))
        crc = getCRC(action_packet)
        action_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(action_packet)
        Timer(self.RESEND_PKT_TIMEOUT, self.handleActionTimeout, args=[action]).start()

    def handleActionTimeout(self, action):
        """
        Resends the ACTION (specific) packet if no SYN-ACK is received within the timeout duration.
        """
        # FIXME: This might cause a bug if another random action is triggered while this is waiting
        if self._action_in_progress:
            self.logger.warning(f"Timeout for ACTION. Resending {action}...")
            self.simulateAction(RELOAD_PKT)
            Timer(
                self.RESEND_PKT_TIMEOUT, self.handleActionTimeout, args=[action]
            ).start()

    def handleGunPacket(self, data):
        """
        Adds the Shot ID to the unacknowledged_gunshots set() and sends the SYN-ACK packet.

        Args:
            data (bytes): The gun shot packet.
        """
        # FIXME: Drop attempted shots during a reload. May cause shotID mismatch since Arduino will increment
        if self._action_in_progress:
            self.logger.warning(
                ">> Shot triggered while performing action. Dropping packet..."
            )
            return

        # Send NAK if a shot is skipped and return
        shotID, remainingBullets = struct.unpack("<2B16x", data)
        if shotID != self.expected_gunshot_id:
            self.logger.warning(
                f">> Skipped Shot ID {self.expected_gunshot_id} and got {shotID} instead."
            )
            self.sendGunNAK(self.expected_gunshot_id)
            return

        # Handle gun shot
        self._gunshot_in_progress = True
        if shotID not in self.unacknowledged_gunshots:
            self.logger.info(f">> Shot ID {shotID} received.")
            self.unacknowledged_gunshots.add(shotID)
            self.expected_gunshot_id = shotID + 1
            self.game_state.useBullet()  ### TODO Add assert to verify bullet count against remainingBullets
            self.sendGunSYNACK(shotID, remainingBullets)
            Timer(
                self.RESEND_PKT_TIMEOUT,
                self.handleGunTimeout,
                args=[shotID, remainingBullets],
            ).start()
        else:
            self.logger.warning(
                f">> Duplicate Shot ID received: {shotID}. Dropping packet..."
            )

    def sendGunSYNACK(self, shotID, remainingBullets):
        """
        Sends the SYN-ACK packet for the gun shot.

        Args:
            shotID (int): The Shot ID of the gun shot.
        """
        self.logger.info(f"<< Sending GUN SYN-ACK for Shot ID {shotID}...")
        synack_packet = struct.pack(
            "<b2B16x", ord(GUN_ACK_PKT), shotID, remainingBullets
        )
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)

    def handleGunACK(self, data):
        """
        Appends the Shot ID to the successful_gunshots list(), puts it
        in the queue and removes it from unacknowledged_gunshots set().

        Args:
            data (bytes): The gun shot acknowledgement packet.
        """
        shotID, remainingBullets = struct.unpack("<2B16x", data)
        if shotID in self.unacknowledged_gunshots:
            self._gunshot_in_progress = False
            self.unacknowledged_gunshots.remove(shotID)
            self.successful_gunshots.add(shotID)
            self.game_state.applyGunState(bullets=remainingBullets)
            self.checkForMissingShots(shotID)
            self.data_queue.put(
                {
                    "id": self.beetle_id,
                    "type": GUN_PKT,
                    "shotID": shotID,
                    "successfulShots": self.successful_gunshots,
                }
            )
            self.logger.info(f">> Shot ID {shotID} acknowledged.")
            self.logger.info(f"Successful shots: {self.successful_gunshots}")
        else:
            self.logger.warning(
                f">> Duplicate Shot ID ACK received: {shotID}. Dropping packet..."
            )

        ###### Trigger fake reload if no bullets remaining ######
        if remainingBullets == 0 and self.simulation_mode:
            self.logger.info("<< No bullets remaining. Triggering fake reload...")
            self.simulateAction(RELOAD_PKT)
            self.game_state.reload()
        #########################################################

    def checkForMissingShots(self, curr_shot):
        """
        Check that all Shot IDs up to the current shot have been acknowledged.
        If not, send retransmission requests for the missing shots.

        Args:
            curr_shot (int): The Shot ID of the current gun shot.
        """
        for shot in range(1, curr_shot):
            if shot not in self.successful_gunshots:
                self.logger.warning(
                    f"Missing Shot ID: {shot}. Sending retransmission request."
                )
                self.sendGunNAK(shot)

    def sendGunNAK(self, shotID):
        """
        Sends the retransmission request for the missing Shot ID.

        Args:
            shotID (int): The Shot ID of the missing gun shot.
        """
        self.logger.info(f"<< Sending GUN NAK for Shot ID {shotID}...")
        rt_packet = struct.pack("<bB17x", ord(GUN_NAK_PKT), shotID)
        crc = getCRC(rt_packet)
        rt_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(rt_packet)

    def handleGunTimeout(self, shotID, remainingBullets):
        """
        Resends the GUN SYN-ACK packet if no ACK is received within the timeout duration.

        Args:
            shotID (int): The Shot ID of the gun shot.
        """
        if shotID in self.unacknowledged_gunshots:
            self.logger.warning(
                f"Timeout for Shot ID: {shotID}. Resending GUN SYN-ACK."
            )
            self.sendGunSYNACK(shotID, remainingBullets)
            Timer(
                self.RESEND_PKT_TIMEOUT,
                self.handleGunTimeout,
                args=[shotID, remainingBullets],
            ).start()

    def handleReloadSYNACK(self):
        """
        Disables the action_in_progress flag and sends the RELOAD ACK packet.
        """
        if self._action_in_progress:
            self.logger.info(">> Received RELOAD SYN-ACK.")
            self.expected_gunshot_id = 1
            self.unacknowledged_gunshots = set()
            self.successful_gunshots = set()
            self.game_state.applyGunState(bullets=self.MAG_SIZE)
            self._action_in_progress = False
            self.sendReloadACK()
        else:
            self.logger.warning(">> Received unexpected RELOAD ACK.")

    def sendReloadACK(self):
        """
        Sends the RELOAD ACK packet to the Beetle.
        """
        self.logger.info("<< Sending RELOAD ACK...")
        ack_packet = struct.pack("<b18x", ord(RELOAD_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    # ---------------------------- In Progress ---------------------------- #

    def handleVestPacket(self, data):
        self.logger.info(">> VESTSHOT detected.")
        self._vestshot_in_progress = True
        shield, health = struct.unpack("<2B16x", data)
        self.game_state.updateVestState(shield=shield, health=health)
        self.sendVestSYNACK()
        Timer(self.RESEND_PKT_TIMEOUT, self.handleVestTimeout).start()

    def sendVestSYNACK(self):
        self.logger.info("<< Sending VESTSHOT SYN-ACK...")
        synack_packet = struct.pack("<b18x", ord(VESTSHOT_ACK_PKT))
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)

    def handleVestACK(self, data):
        self.logger.info(">> Received VESTSHOT ACK.")
        self._vestshot_in_progress = False  # flag to prevent timeout trigger
        shield, health = struct.unpack("<2B16x", data)
        self.game_state.applyVestState(shield=shield, health=health)

        # Random action simulation with probability 0.5
        if self.simulation_mode and random.random() <= 0.5:
            self.simulateRandomAction()

    def handleVestTimeout(self):
        """
        Resends the VESTSHOT SYN-ACK packet if no ACK is received within the timeout duration.
        """
        if self._vestshot_in_progress:
            self.logger.info(">> VESTSHOT timeout. Resending VESTSHOT SYN-ACK...")
            self.sendVestSYNACK()
            Timer(self.RESEND_PKT_TIMEOUT, self.handleVestTimeout).start()

    def handleBombSYNACK(self, data):
        if not self._vestshot_in_progress:
            self.logger.info(">> Received BOMB SYN-ACK.")
            shield, health = struct.unpack("<2B16x", data)
            self.game_state.applyVestState(shield=shield, health=health)
            self._action_in_progress = False
            self.sendBombACK()
        else:
            self.logger.warning(">> Received BOMB SYN-ACK while vest shot in progress.")

    def sendBombACK(self):
        self.logger.info("<< Sending BOMB ACK...")
        ack_packet = struct.pack("<b18x", ord(BOMB_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    def handleAttackSYNACK(self, data):
        if not self._vestshot_in_progress:
            self.logger.info(">> Received ATTACK SYN-ACK.")
            shield, health = struct.unpack("<2B16x", data)
            self.game_state.applyVestState(shield=shield, health=health)
            self._action_in_progress = False
            self.sendAttackACK()
        else:
            self.logger.warning(
                ">> Received ATTACK SYN-ACK while vest shot in progress."
            )

    def sendAttackACK(self):
        self.logger.info("<< Sending ATTACK ACK...")
        ack_packet = struct.pack("<b18x", ord(ATTACK_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    def handleShieldSYNACK(self, data):
        if not self._vestshot_in_progress:
            self.logger.info(">> Received SHIELD SYN-ACK.")
            shield, health = struct.unpack("<2B16x", data)
            self.game_state.applyVestState(shield=shield, health=health)
            self._action_in_progress = False
            self.sendShieldACK()
        else:
            self.logger.warning(
                ">> Received SHIELD SYN-ACK while vest shot in progress."
            )

    def sendShieldACK(self):
        self.logger.info("<< Sending SHIELD ACK...")
        ack_packet = struct.pack("<b18x", ord(SHIELD_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    @property
    def gunshot_in_progress(self):
        return self._gunshot_in_progress

    @gunshot_in_progress.setter
    def gunshot_in_progress(self, value):
        self.gunshot_in_progress = value

    @property
    def vestshot_in_progress(self):
        return self._vestshot_in_progress

    @vestshot_in_progress.setter
    def vestshot_in_progress(self, value):
        self.vestshot_in_progress = value

    @property
    def action_in_progress(self):
        return self._action_in_progress

    @action_in_progress.setter
    def action_in_progress(self, value):
        self._action_in_progress = value
