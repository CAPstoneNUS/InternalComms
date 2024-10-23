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
GUNSHOT_PKT = "G"
RELOAD_PKT = "R"
VESTSHOT_PKT = "V"
NAK_PKT = "L"
UPDATE_STATE_PKT = "U"
GUNSTATE_ACK_PKT = "X"
VESTSTATE_ACK_PKT = "W"


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

        frag_packet_count (int): Count of fragmented packets received.
        corrupt_packet_count (int): Count of corrupted packets received.
        dropped_packet_count (int): Count of packets dropped.

        MAX_BUFFER_SIZE (int): Maximum buffer size for storing incoming data.
        MAX_QUEUE_SIZE (int): Maximum queue size for storing IMU data.
        PACKET_SIZE (int): Size of each data packet.
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
        self._gun_state_change_in_progress = False
        self._shots_fired = set()

        # Vest handling
        self._vest_state_change_in_progress = False
        self.registered_vestshots = set()

        # Sequencing
        self._sqn = 0  # sequence number for packets sent to Beetle
        self._expected_seq_num = 0
        self._sent_packets = []

        # Counters
        self.frag_packet_count = 0
        self.corrupt_packet_count = 0
        self.total_corrupted_packets = 0
        self.dropped_packet_count = 0

        # Simulation mode
        self._action_in_progress = False

        # Configuration parameters
        self.player_id = self.config["game"]["player_id"]
        self.MAG_SIZE = self.config["storage"]["mag_size"]
        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]
        self.RESPONSE_TIMEOUT = self.config["time"]["response_timeout"]
        self.STATS_LOG_INTERVAL = config["time"]["stats_log_interval"]

    def handleNotification(self, cHandle, data):
        """
        Reads from the Beetle characteristic and processes the incoming data.

        Args:
            cHandle (int): The characteristic handle from which the data was received.
            data (bytes): The data received from the Beetle
        """
        # Randomly corrupt data for testing
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
                self.logger.error("CRC mismatch. Dropping packet...")
                # self.logger.error("Packet corrupted. Requesting retransmission...")
                # self.sendNAKPacket()
                self.corrupt_packet_count += 1
                self.total_corrupted_packets += 1
                if self.corrupt_packet_count >= 20:  # 1 second of corrupted packets
                    self.logger.error(
                        "Corrupt packet limit reached. Force disconnecting..."
                    )
                    self.beetle_connection.killBeetle()
                    self.beetle_connection.forceDisconnect()
                    self.corrupt_packet_count = 0
                return

            payload = packet[1:-1]

            # IMU data stream (does NOT handle corrupted or dropped packets)
            if packet_type == IMU_DATA_PKT:
                self.handleIMUPacket(payload)
                continue  # so the expected sequence number does NOT get updated

            # Sequence number handling
            beetle_sqn = struct.unpack("B", payload[:1])[0]
            self.logger.info(
                f"Beetle sent SQN {beetle_sqn}. Expected SQN is {self._expected_seq_num}."
            )
            if beetle_sqn != self._expected_seq_num:
                self.logger.error(
                    f"Sequence number mismatch. Expected {self._expected_seq_num} but got {beetle_sqn} instead."
                )
                self.sendNAKPacket()
                return

            # Packet handling
            if packet_type == HS_SYNACK_PKT:
                self.handleSYNACKPacket()
                return  # so the expected sequence number does NOT get updated
            elif packet_type == GUNSHOT_PKT:
                self.handleGunPacket(payload)
            elif packet_type == VESTSHOT_PKT:
                self.handleVestPacket(payload)
            elif packet_type == RELOAD_PKT:
                self.handleReloadACK()
            elif packet_type == GUNSTATE_ACK_PKT:
                self.handleGunStateACK(payload)
            elif packet_type == VESTSTATE_ACK_PKT:
                self.handleVestStateACK(payload)
            elif packet_type == NAK_PKT:
                self.handleNAKPacket(payload)
            else:
                self.logger.error(f"Unknown packet type: {packet_type}")

            self._expected_seq_num += 1
            self.logger.info(
                f"Expected sequence number updated to {self._expected_seq_num}."
            )

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

    # ---------------------------- SN, HS, Timeouts & NAKs ---------------------------- #

    def resetSeqNum(self):
        self._sqn = 0
        self._expected_seq_num = 0

    def handlePacketTimeout(self, flag):
        if flag:
            self.logger.warning(f"Packet timeout. Resending last packet...")
            self.beetle_connection.writeCharacteristic(self._sent_packets[-1])
            Timer(
                self.RESPONSE_TIMEOUT,
                self.handlePacketTimeout,
                args=[flag],
            ).start()

    def handleNAKPacket(self, data):
        self.logger.warning(">> NAK received.")
        requested_sqn = struct.unpack("B", data[:1])[0] # shotID (gun), shield (vest)
        self.logger.warning(f"<< Resending requested packet {requested_sqn}...")
        self.beetle_connection.writeCharacteristic(self._sent_packets[requested_sqn])

    def sendNAKPacket(self):
        """
        Sends a retransmission request for the corrupted packet.
        """
        self.logger.info(
            f"<< Sending NAK for sequence number {self._expected_seq_num}..."
        )
        nak_packet = struct.pack("<bB17x", ord(NAK_PKT), self._expected_seq_num)
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
        self.beetle_connection.ack_flag = True  # allows doHandshake() to send ACK

    # ---------------------------- IMU ---------------------------- #

    def handleIMUPacket(self, data):
        unpacked_data = struct.unpack("<6h6x", data)
        accX, accY, accZ, gyrX, gyrY, gyrZ = unpacked_data
        imu_data = {
            "id": self.beetle_id,
            "type": IMU_DATA_PKT,
            "playerID": self.player_id,
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

    # ---------------------------- Gun State Handling ---------------------------- #

    def sendGunStatePacket(self, bullets):
        """
        Sends the GUN state packet to the Beetle.

        Args:
            bullets (int): The number of bullets remaining in the gun.
        """
        self._gun_state_change_in_progress = True
        self.logger.info(f"<< Sending GUN STATE packet...")
        gun_packet = struct.pack("b2B16x", ord(UPDATE_STATE_PKT), 7 - bullets, bullets)
        crc = getCRC(gun_packet)
        gun_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(gun_packet)
        Timer(
            self.RESPONSE_TIMEOUT,
            self.handlePacketTimeout,
            args=[self._gun_state_change_in_progress],
        ).start()

    def handleGunStateACK(self, data):
        self._gun_state_change_in_progress = False
        self.logger.info(">> Received GUN STATE SYN-ACK. Applying state...")
        _, remainingBullets = struct.unpack("<2B16x", data)
        print(f"<<<<<< RECEIVED {remainingBullets} BULLETS FROM ARDUINO >>>>>>")
        self.game_state.applyGunState(bullets=remainingBullets)
        # self.sendGunStateACK()
        self._sqn += 1

    # def sendGunStateACK(self):
    # """
    # Sends the GUN STATE ACK packet to the Beetle.
    # """
    # self.logger.info("<< Sending GUN STATE ACK...")
    # ack_packet = struct.pack("<b18x", ord(GUNSTATE_ACK_PKT))
    # crc = getCRC(ack_packet)
    # ack_packet += struct.pack("B", crc)
    # self.beetle_connection.writeCharacteristic(ack_packet)

    # ---------------------------- Vest State Handling ---------------------------- #

    def sendVestStatePacket(self, shield, health):
        """
        Sends the VEST state packet to the Beetle.

        Args:
            shield (int): The shield of the player.
            health (int): The health of the player.
        """
        self._vest_state_change_in_progress = True
        self.logger.info(f"<< Sending VEST STATE packet...")
        vest_packet = struct.pack("<b2B16x", ord(UPDATE_STATE_PKT), shield, health)
        crc = getCRC(vest_packet)
        vest_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(vest_packet)
        Timer(
            self.RESPONSE_TIMEOUT,
            self.handlePacketTimeout,
            args=[self._vest_state_change_in_progress],
        ).start()

    def handleVestStateACK(self, data):
        self._vest_state_change_in_progress = False
        self.logger.info(">> Received VEST STATE SYN-ACK. Applying state...")
        shield, health = struct.unpack("<2B16x", data)
        print(f"<<<<<< RECEIVED {health} HEALTH FROM ARDUINO >>>>>>")
        print(f"<<<<<< RECEIVED {shield} SHIELD FROM ARDUINO >>>>>>")
        self.game_state.applyVestState(shield=shield, health=health)
        # self.sendVestStateACK()
        self._sqn += 1

    # def sendVestStateACK(self):
    # """
    # Sends the VEST STATE ACK packet to the Beetle.
    # """
    # self.logger.info("<< Sending VEST STATE ACK...")
    # ack_packet = struct.pack("<b18x", ord(VESTSTATE_ACK_PKT))
    # crc = getCRC(ack_packet)
    # ack_packet += struct.pack("B", crc)
    # self.beetle_connection.writeCharacteristic(ack_packet)

    # ---------------------------- Gun Handling ---------------------------- #

    def handleGunPacket(self, data):
        # FIXME handle action in progress?
        beetle_sqn, shotID, remainingBullets = struct.unpack("<3B15x", data)

        # Handle gun shot
        if shotID not in self._shots_fired:
            self.logger.info(f">> GUNSHOT {shotID} received.")
            self.data_queue.put(
                {
                    "id": self.beetle_id,
                    "type": GUNSHOT_PKT,
                    "playerID": self.player_id,
                    "gunAccX": 0,
                    "gunAccY": 0,
                    "gunAccZ": 0,
                    "gunGyrX": 0,
                    "gunGyrY": 0,
                    "gunGyrZ": 0,
                    "ankleAccX": 0,
                    "ankleAccY": 0,
                    "ankleAccZ": 0,
                    "ankleGyrX": 0,
                    "ankleGyrY": 0,
                    "ankleGyrZ": 0,
                }
            )
            self._shots_fired.add(shotID)
            self.game_state.useBullet()
            self.sendGunACK(beetle_sqn, shotID, remainingBullets)
        else:
            self.logger.warning(
                f">> Duplicate Shot received: {shotID}. Dropping packet..."
            )

    def sendGunACK(self, beetle_sqn, shotID, remainingBullets):
        self.logger.info(f"<< Sending GUNSHOT {shotID} ACK...")

        ack_packet = struct.pack("<bB17x", ord(GUNSHOT_PKT), beetle_sqn)
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

        self._sent_packets.append(ack_packet)
        self.game_state.applyGunState(bullets=remainingBullets)

    # ---------------------------- Reload Handling ---------------------------- #

    def sendReloadPacket(self):
        """
        Sends the RELOAD packet to the Beetle.
        """
        self._action_in_progress = True
        self.logger.info("<< Sending RELOAD...")
        reload_packet = struct.pack("<b18x", ord(RELOAD_PKT))
        crc = getCRC(reload_packet)
        reload_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(reload_packet)
        Timer(
            self.RESPONSE_TIMEOUT,
            self.handlePacketTimeout,
            args=[self._action_in_progress],
        ).start()

    def handleReloadACK(self):
        """
        Disables the action_in_progress flag and sends the RELOAD ACK packet.
        """
        if self._action_in_progress:
            self.logger.info(">> Received RELOAD ACK.")
            self._shots_fired = set()
            self.game_state.applyGunState(bullets=self.MAG_SIZE)
            self._action_in_progress = False
            self._sqn += 1
        else:
            self.logger.warning(">> Received unexpected RELOAD ACK.")

    # ---------------------------- Vest Handling ---------------------------- #

    def handleVestPacket(self, data):
        self.logger.info(">> VESTSHOT detected.")
        self.data_queue.put(
            {
                "id": self.beetle_id,
                "type": VESTSHOT_PKT,
                "playerID": self.player_id,
                "gunAccX": 0,
                "gunAccY": 0,
                "gunAccZ": 0,
                "gunGyrX": 0,
                "gunGyrY": 0,
                "gunGyrZ": 0,
                "ankleAccX": 0,
                "ankleAccY": 0,
                "ankleAccZ": 0,
                "ankleGyrX": 0,
                "ankleGyrY": 0,
                "ankleGyrZ": 0,
            }
        )
        beetle_sqn, shield, health = struct.unpack("<3B15x", data)
        self.game_state.updateVestState(shield=shield, health=health)
        self.sendVestACK(beetle_sqn, shield, health)

    def sendVestACK(self, beetle_sqn, shield, health):
        self.logger.info("<< Sending VESTSHOT ACK...")

        ack_packet = struct.pack("<bB17x", ord(VESTSHOT_PKT), beetle_sqn)
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

        self._sent_packets.append(ack_packet)
        self.game_state.applyVestState(shield=shield, health=health)

    # @property
    # def action_in_progress(self):
    #     return self._action_in_progress

    # @action_in_progress.setter
    # def action_in_progress(self, value):
    #     self._action_in_progress = value

    @property
    def shots_fired(self):
        return self._shots_fired

    @shots_fired.setter
    def shots_fired(self, value):
        self._shots_fired = value

    # @property
    # def sent_packets(self):
    #     return self._sent_packets

    # @sent_packets.setter
    # def sent_packets(self, packet):
    #     self._sent_packets.append(packet)

    @property
    def sqn(self):
        return self._sqn

    @sqn.setter
    def seq_num(self, value):
        self._sqn = value
