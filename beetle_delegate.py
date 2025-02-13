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
VESTSHOT_PKT = "V"
NAK_PKT = "N"
UPDATE_STATE_PKT = "U"
GUNSTATE_ACK_PKT = "X"
VESTSTATE_ACK_PKT = "W"
KILL_PKT = 'K'


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

        # Timers
        self.start_time = time.time()
        self.last_successful_packet_time = time.time()

        # Gun and vest handling
        self._state_change_ip = False

        # Sequencing
        self._sqn = 0
        self._expected_seq_num = 0
        self._sent_packets = []

        # Counters
        self.total_window_data = 0
        self.frag_packet_count = 0
        self.corrupt_packet_count = 0
        self.total_corrupted_packets = 0
        self.dropped_packet_count = 0
        self._timeout_resend_attempts = 0
        self._nak_packet_count = 0

        # Configuration parameters
        self.PLAYER_ID = self.config["game"]["player_id"]
        self.MAG_SIZE = self.config["storage"]["mag_size"]
        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]
        self.RESPONSE_TIMEOUT = self.config["time"]["response_timeout"]
        self.STATS_LOG_INTERVAL = config["time"]["stats_log_interval"]
        self.MAX_CORRUPT_PACKETS = config["storage"]["max_corrupt_packets"]
        self.MAX_TIMEOUT_RESEND_ATTEMPTS = config["storage"]["max_timeout_resend_attempts"]
        self.PACKET_TYPES = {value for key, value in config["packet"].items()}

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

        try:
            # Add incoming data to buffer
            self.buffer.extend(data)

            # Process packets in buffer
            while len(self.buffer) >= self.PACKET_SIZE:
                # Extract packet
                packet = bytes(itertools.islice(self.buffer, self.PACKET_SIZE))
                for _ in range(self.PACKET_SIZE):
                    self.buffer.popleft()

                # Validate packet type
                packet_type = chr(packet[0])
                if packet_type not in self.PACKET_TYPES:
                    self.logger.error(f"Unknown packet type: {packet_type}")
                    self.logger.warning("Clearing buffer...")
                    self.buffer.clear()
                    continue

                # Validate CRC
                calculated_crc = getCRC(packet[:-1])
                true_crc = struct.unpack("<B", packet[-1:])[0]
                if calculated_crc != true_crc:
                    self.handleCorruptPacket(packet_type)
                    continue

                # Extract payload (excluding packet type (FIRST) and CRC (LAST))
                payload = packet[1:-1]

                if packet_type == IMU_DATA_PKT:
                    self.handleIMUPacket(payload)
                    continue

                if packet_type == NAK_PKT:
                    self.handleNAKPacket(payload)
                    continue

                # Validate sequence number
                beetle_sqn = struct.unpack("B", payload[:1])[0]
                self.logger.info(f">> Beetle sent SQN {beetle_sqn}. Expected SQN {self._expected_seq_num}.")

                # Drop duplicate packets
                if beetle_sqn < self._expected_seq_num:
                    self.logger.warning(f"Ignoring duplicate SQN {beetle_sqn}.")
                    continue

                # if random.random() <= 0.1:
                #     beetle_sqn = 99
                #     self.logger.warning(f"*** SIMULATING out-of-order packet. Setting SQN to {beetle_sqn} ***")
                #     self.logger.warning("+++++++++++++ SLEEPING 2 SEC TO TEST BEETLE RESEND +++++++++++++")
                #     time.sleep(2)

                # Handle out-of-order packets
                if beetle_sqn > self._expected_seq_num:
                    self.logger.error(f"SQN mismatch. Got SQN {beetle_sqn} instead of expected SQN {self._expected_seq_num}.")
                    self.sendNAKPacket()
                    return
                
                # Update flag params
                self.last_successful_packet_time = time.time()
                self._nak_packet_count = 0
                
                # Handle packet
                if packet_type == HS_SYNACK_PKT:
                    self.handleSYNACKPacket()
                elif packet_type == GUNSHOT_PKT:
                    self.handleGunPacket(payload)
                elif packet_type == VESTSHOT_PKT:
                    self.handleVestPacket(payload)
                elif packet_type == GUNSTATE_ACK_PKT:
                    self.handleGunStateACK(payload)
                elif packet_type == VESTSTATE_ACK_PKT:
                    self.handleVestStateACK(payload)
                elif packet_type == KILL_PKT:
                    self.handleKillPacket()
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

        except Exception as e:
            self.logger.error(f"Error handling notification: {e}")

    def handleCorruptPacket(self, packet_type):
        self.corrupt_packet_count += 1
        self.total_corrupted_packets += 1

        if packet_type == IMU_DATA_PKT:
            self.logger.warning(">> Corrupt IMU data packet. Dropping packet...")
        else:
            self.logger.warning(f">> Corrupt {packet_type} packet. Sending NAK...")
            self.sendNAKPacket()

        if time.time() - self.last_successful_packet_time > 1:
            self.logger.warning("Clearing buffer...")
            self.buffer.clear()
            self.corrupt_packet_count = 0
        elif self.corrupt_packet_count >= self.MAX_CORRUPT_PACKETS:
            self.logger.error(
                f"Exceeded {self.MAX_CORRUPT_PACKETS} corrupt packets. Force disconnecting..."
            )
            # self.beetle_connection.killBeetle()
            self.beetle_connection.forceDisconnect()

    # ---------------------------- SQN, HS, Timeouts, Kill & NAKs ---------------------------- #

    def resetSeqNum(self):
        self._sqn = 0
        self._expected_seq_num = 0

    def sendLastStateChangePacket(self):
        if not self._sent_packets:
            self.logger.warning("No packets to resend.")
            return

        for packet in reversed(self._sent_packets):
            if packet[0] == ord(UPDATE_STATE_PKT):
                self.beetle_connection.writeCharacteristic(packet)
                return

    def handleStateTimeout(self):
        if self._timeout_resend_attempts >= self.MAX_TIMEOUT_RESEND_ATTEMPTS:
            self.logger.error(f"Exceeded {self.MAX_TIMEOUT_RESEND_ATTEMPTS} timeout resend attempts. Force disconnecting...")
            # self.beetle_connection.killBeetle()
            self.beetle_connection.forceDisconnect()

        if self._state_change_ip:
            self.logger.warning("<< [TIMEOUT] Resending LAST STATE CHANGE since no ACK received...")
            self.sendLastStateChangePacket()
            self._timeout_resend_attempts += 1
            Timer(self.RESPONSE_TIMEOUT, self.handleStateTimeout).start()

    def handleNAKPacket(self, data):
        self._nak_packet_count += 1
        if self._nak_packet_count >= self.MAX_TIMEOUT_RESEND_ATTEMPTS:
            self.logger.warning(f"Received {self.MAX_TIMEOUT_RESEND_ATTEMPTS} consecutive NAK's. Force disconnecting...")
            self.beetle_connection.forceDisconnect()

        requested_sqn = struct.unpack("B", data[:1])[0]

        if len(self._sent_packets) < requested_sqn:
            self.logger.error(f"Length of _sent_packets {len(self._sent_packets)} < requested SQN {requested_sqn}. Unable to send NAK.")
            return
        
        self.logger.warning(f">> Received NAK. Resending requested packet {requested_sqn}.")
        for packet in reversed(self._sent_packets):
            packet_sqn = struct.unpack("B", packet[1:2])[0]  # Extract SQN from 2nd byte
            if packet_sqn == requested_sqn:
                self.beetle_connection.writeCharacteristic(packet)
                return

        self.logger.error(f"Requested packet with SQN {requested_sqn} not found in _sent_packets.")

    def sendNAKPacket(self):
        """
        Sends a retransmission request for the packet with the expected sequence number.
        """
        self.logger.info(
            f"<< Sending NAK for expected SQN {self._expected_seq_num}..."
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

    def handleKillPacket(self):
        self.logger.error(">> Received KILL packet. Force disconnecting...")
        self.beetle_connection.forceDisconnect()

    # ---------------------------- IMU ---------------------------- #

    def handleIMUPacket(self, data):
        unpacked_data = struct.unpack("<6h6x", data)
        accX, accY, accZ, gyrX, gyrY, gyrZ = unpacked_data
        imu_data = {
            "id": self.beetle_id,
            "type": IMU_DATA_PKT,
            "player_id": self.PLAYER_ID,
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

    def sendGunStatePacket(self, remainingBullets):
        self._state_change_ip = True
        self.logger.info(f"<< Sending GUN STATE packet...")
        gun_packet = struct.pack("b2B16x", ord(UPDATE_STATE_PKT), self._sqn, remainingBullets)
        crc = getCRC(gun_packet)
        gun_packet += struct.pack("B", crc)
        self._sent_packets.append(gun_packet)
        self.beetle_connection.writeCharacteristic(gun_packet)
        Timer(self.RESPONSE_TIMEOUT, self.handleStateTimeout).start()

    def handleGunStateACK(self, data):
        if self._state_change_ip:
            self._state_change_ip = False
            self.logger.info(">> Received GUN STATE ACK. Applying state...")
            _, remainingBullets = struct.unpack("<2B16x", data)
            print(f"*** ARDUINO LED SHOULD SHOW {remainingBullets} BULLETS ***")
            self.game_state.applyGunState(bullets=remainingBullets)
            self._sqn += 1
        else:
            self.logger.warning(">> [DUPLICATE] GUN STATE ACK. Ignoring...")

    # ---------------------------- Vest State Handling ---------------------------- #

    def sendVestStatePacket(self, shield, health):
        self._state_change_ip = True
        self.logger.info(f"<< Sending VEST STATE packet...")
        vest_packet = struct.pack("<b3B15x", ord(UPDATE_STATE_PKT), self._sqn, shield, health)
        crc = getCRC(vest_packet)
        vest_packet += struct.pack("B", crc)
        self._sent_packets.append(vest_packet)
        self.beetle_connection.writeCharacteristic(vest_packet)
        Timer(self.RESPONSE_TIMEOUT, self.handleStateTimeout).start()


    def handleVestStateACK(self, data):
        if self._state_change_ip:
            self._state_change_ip = False
            self.logger.info(">> Received VEST STATE ACK. Applying state...")
            _, shield, health = struct.unpack("<3B15x", data)
            print(f"*** ARDUINO LED SHOULD SHOW {health} HEALTH ***")
            print(f"*** ARDUINO HAS {shield} SHIELD ***")
            self.game_state.applyVestState(shield=shield, health=health)
            self._sqn += 1
        else:
            self.logger.warning(">> [DUPLICATE] VEST STATE ACK. Ignoring...")

    # ---------------------------- Gun Handling ---------------------------- #

    def handleGunPacket(self, data):
        beetle_sqn, remainingBullets = struct.unpack("<2B16x", data)

        # Handle gun shot
        self.logger.info(f">> [G] received.")
        self.data_queue.put(
            {
                "id": self.beetle_id,
                "type": GUNSHOT_PKT,
                "player_id": self.PLAYER_ID,
            }
        )
        self.game_state.useBullet()
        self.sendGunACK(beetle_sqn, remainingBullets)


    def sendGunACK(self, beetle_sqn, remainingBullets):
        self.logger.info(f"<< Sending [G] ACK...")
        ack_packet = struct.pack("<bB17x", ord(GUNSHOT_PKT), beetle_sqn)
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self._sent_packets.append(ack_packet)
        self.beetle_connection.writeCharacteristic(ack_packet)
        self._expected_seq_num += 1
        self.game_state.applyGunState(bullets=remainingBullets)

    # ---------------------------- Vest Handling ---------------------------- #

    def handleVestPacket(self, data):
        self.logger.info(">> [V] received.")
        self.data_queue.put(
            {
                "id": self.beetle_id,
                "type": VESTSHOT_PKT,
                "player_id": self.PLAYER_ID,
            }
        )
        beetle_sqn, shield, health = struct.unpack("<3B15x", data)
        self.game_state.updateVestState(shield=shield, health=health)
        self.sendVestACK(beetle_sqn, shield, health)

    def sendVestACK(self, beetle_sqn, shield, health):
        self.logger.info("<< Sending [V] ACK...")

        ack_packet = struct.pack("<bB17x", ord(VESTSHOT_PKT), beetle_sqn)
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self._sent_packets.append(ack_packet)
        self.beetle_connection.writeCharacteristic(ack_packet)
        self._expected_seq_num += 1
        self.game_state.applyVestState(shield=shield, health=health)

    @property
    def sqn(self):
        return self._sqn

    @sqn.setter
    def seq_num(self, value):
        self._sqn = value
