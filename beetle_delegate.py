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
VESTSHOT_PKT = "V"
GUN_ACK_PKT = "X"
VESTSHOT_ACK_PKT = "Z"
GUN_NAK_PKT = "T"
RELOAD_SYNACK_PKT = "Y"
NAK_PKT = "L"
STATE_PKT = "D"
GUNSTATE_SYNACK_PKT = "U"
VESTSTATE_SYNACK_PKT = "W"


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

        _unacknowledged_gunshots (set): Set to temporarily store Shot IDs with pending ACKs.
        _successful_gunshots (set): Set to store Shot IDs that have been successfully acknowledged.

        frag_packet_count (int): Count of fragmented packets received.
        corrupt_packet_count (int): Count of corrupted packets received.
        dropped_packet_count (int): Count of packets dropped.

        MAX_BUFFER_SIZE (int): Maximum buffer size for storing incoming data.
        MAX_QUEUE_SIZE (int): Maximum queue size for storing IMU data.
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
        self._last_packet = None

        # Transmission speed stats
        self.start_time = time.time()
        self.total_window_data = 0
        self.total_data = 0

        # Gun handling
        self._gunshot_in_progress = False
        self._gun_state_change_in_progress = False
        self._successful_gunshots = set()
        self._unacknowledged_gunshots = set()
        self._expected_gunshot_id = 1

        # Vest handling
        self._vestshot_in_progress = False
        self._vest_state_change_in_progress = False
        self.registered_vestshots = set()
        self.unacknowledged_vestshots = set()

        # Sequence numbers
        self._seq_num = 0
        self._expected_seq_num = 0
        self._last_packet = None

        # Counters
        self.frag_packet_count = 0
        self.corrupt_packet_count = 0
        self.total_corrupted_packets = 0
        self.dropped_packet_count = 0

        # Simulation mode
        self._action_in_progress = False

        # Configuration parameters
        self.MAG_SIZE = self.config["storage"]["mag_size"]
        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
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
                ### TODO figure out NAK later
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
                continue

            # Sequence number handling
            seq_num, payload = struct.unpack("B", payload[:1])[0], payload[1:]
            if seq_num != self._expected_seq_num:
                self.logger.error(
                    f"Sequence number mismatch. Expected {self._expected_seq_num} but got {seq_num} instead."
                )
                self.sendNAKPacket(self._expected_seq_num)
                return

            self._expected_seq_num += 1

            # Packet handling
            if packet_type == HS_SYNACK_PKT:
                self.handleSYNACKPacket()
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
            elif packet_type == GUNSTATE_SYNACK_PKT:
                self.handleGunStateSYNACK(payload)
            elif packet_type == VESTSTATE_SYNACK_PKT:
                self.handleVestStateSYNACK(payload)
            elif packet_type == NAK_PKT:
                pass
                # self.sendLastPacket()
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

    def handlePacketTimeout(self, packet, flag):
        if flag:
            self.logger.warning(f"Packet timeout. Resending {packet}...")
            self.beetle_connection.writeCharacteristic(packet)
            Timer(
                self.RESEND_PKT_TIMEOUT, self.handlePacketTimeout, args=[packet, flag]
            ).start()

    def sendNAKPacket(self, seq_num):
        """
        Sends a retransmission request for the corrupted packet.
        """
        self.logger.info(f"<< Sending NAK for seq_num {seq_num}...")
        nak_packet = struct.pack("<bB17x", ord(NAK_PKT), seq_num)
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
        # Timer(
        #     self.RESEND_PKT_TIMEOUT,
        #     self.handlePacketTimeout,
        #     args=[reload_packet, self._action_in_progress],
        # ).start()

    # ---------------------------- Gun State Handling ---------------------------- #

    def sendGunStatePacket(self, bullets):
        """
        Sends the GUN state packet to the Beetle.

        Args:
            bullets (int): The number of bullets remaining in the gun.
        """
        self._gun_state_change_in_progress = True
        self.logger.info(f"<< Sending GUN STATE packet...")
        gun_packet = struct.pack("b2B16x", ord(STATE_PKT), 7 - bullets, bullets)
        crc = getCRC(gun_packet)
        gun_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(gun_packet)
        # Timer(
        #     self.RESEND_PKT_TIMEOUT,
        #     self.handlePacketTimeout,
        #     args=[gun_packet, self._gun_state_change_in_progress],
        # ).start()

    def handleGunStateSYNACK(self, data):
        """
        Handles the SYN-ACK packet for the STATE packet.
        """
        self._gun_state_change_in_progress = False
        self.logger.info(">> Received GUN STATE SYN-ACK. Applying state...")
        _, remainingBullets = struct.unpack("<2B16x", data)
        print(f"<<<<<< RECEIVED {remainingBullets} BULLETS FROM ARDUINO >>>>>>")
        self.game_state.applyGunState(bullets=remainingBullets)
        self.sendGunStateACK()

    def sendGunStateACK(self):
        """
        Sends the GUN STATE ACK packet to the Beetle.
        """
        self.logger.info("<< Sending GUN STATE ACK...")
        ack_packet = struct.pack("<b18x", ord(GUNSTATE_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

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
        vest_packet = struct.pack("<b2B16x", ord(STATE_PKT), shield, health)
        crc = getCRC(vest_packet)
        vest_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(vest_packet)
        # Timer(
        #     self.RESEND_PKT_TIMEOUT,
        #     self.handlePacketTimeout,
        #     args=[vest_packet, self._vest_state_change_in_progress],
        # ).start()

    def handleVestStateSYNACK(self, data):
        """
        Handles the SYN-ACK packet for the STATE packet.
        """
        self._vest_state_change_in_progress = False
        self.logger.info(">> Received VEST STATE SYN-ACK. Applying state...")
        shield, health = struct.unpack("<2B16x", data)
        print(f"<<<<<< RECEIVED {health} HEALTH FROM ARDUINO >>>>>>")
        print(f"<<<<<< RECEIVED {shield} SHIELD FROM ARDUINO >>>>>>")
        self.game_state.applyVestState(shield=shield, health=health)
        self.sendVestStateACK()

    def sendVestStateACK(self):
        """
        Sends the VEST STATE ACK packet to the Beetle.
        """
        self.logger.info("<< Sending VEST STATE ACK...")
        ack_packet = struct.pack("<b18x", ord(VESTSTATE_SYNACK_PKT))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    # ---------------------------- Gun Handling ---------------------------- #

    def handleGunPacket(self, data):
        """
        Adds the Shot ID to the _unacknowledged_gunshots set() and sends the SYN-ACK packet.

        Args:
            data (bytes): The gun shot packet.
        """
        # FIXME: Drop attempted shots during a reload. May cause shotID mismatch since Arduino will increment
        if self._action_in_progress:
            self.logger.warning(
                ">> Shot triggered while performing action. Dropping packet..."
            )
            self.logger.warning(
                " ------------------ MISSING IMPLEMENTATION HERE ------------------ "
            )
            return

        # FIXME: Send NAK if a shot is skipped and return
        shotID, remainingBullets = struct.unpack("<2B15x", data)
        if shotID != self._expected_gunshot_id:
            self.logger.warning(
                f">> Skipped Shot ID {self._expected_gunshot_id} and got {shotID} instead."
            )
            # self.sendGunNAK(self._expected_gunshot_id)
            return

        # Handle gun shot
        self._gunshot_in_progress = True
        if shotID not in self._unacknowledged_gunshots:
            self.logger.info(f">> Shot ID {shotID} received.")
            self._unacknowledged_gunshots.add(shotID)
            self.game_state.useBullet()
            self.sendGunSYNACK(shotID, remainingBullets)
            # Timer(
            #     self.RESEND_PKT_TIMEOUT,
            #     self.handleGunTimeout,
            #     args=[shotID, remainingBullets],
            # ).start()
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
            "<b3B15x", ord(GUN_ACK_PKT), self.seq_num, shotID, remainingBullets
        )
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)
        self.seq_num += 1
        # Timer(
        #     3,
        #     self.handleGunTimeout,
        #     args=[shotID, self._gunshot_in_progress, remainingBullets],
        # )

    def handleGunACK(self, data):
        """
        Appends the Shot ID to the _successful_gunshots list(), puts it
        in the queue and removes it from _unacknowledged_gunshots set().

        Args:
            data (bytes): The gun shot acknowledgement packet.
        """
        shotID, remainingBullets = struct.unpack("<2B15x", data)
        if shotID in self._unacknowledged_gunshots:
            self._gunshot_in_progress = False
            self._unacknowledged_gunshots.remove(shotID)
            self._successful_gunshots.add(shotID)
            self.game_state.applyGunState(bullets=remainingBullets)
            self.data_queue.put(
                {
                    "id": self.beetle_id,
                    "type": GUN_PKT,
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
            self.logger.info(f">> Shot ID {shotID} acknowledged.")
            self.logger.info(f"Successful shots: {self._successful_gunshots}")
            self._expected_gunshot_id = shotID + 1
        else:
            self.logger.warning(
                f">> Duplicate Shot ID ACK received: {shotID}. Dropping packet..."
            )

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

    def handleGunTimeout(self, shotID, flag, remainingBullets):
        """
        Resends the GUN SYN-ACK packet if no ACK is received within the timeout duration.

        Args:
            shotID (int): The Shot ID of the gun shot.
        """
        if shotID in self._unacknowledged_gunshots and flag:
            self.logger.warning(
                f"Timeout for Shot ID: {shotID}. Resending GUN SYN-ACK."
            )
            self.sendGunSYNACK(shotID, remainingBullets)
            Timer(
                3,
                self.handleGunTimeout,
                args=[shotID, flag, remainingBullets],
            ).start()

    # ---------------------------- Reload Handling ---------------------------- #

    def handleReloadSYNACK(self):
        """
        Disables the action_in_progress flag and sends the RELOAD ACK packet.
        """
        if self._action_in_progress:
            self.logger.info(">> Received RELOAD SYN-ACK.")
            self._expected_gunshot_id = 1
            self._unacknowledged_gunshots = set()
            self._successful_gunshots = set()
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

    # ---------------------------- Vest Handling ---------------------------- #

    def handleVestPacket(self, data):
        self.logger.info(">> VESTSHOT detected.")
        self._vestshot_in_progress = True
        shield, health = struct.unpack("<2B16x", data)
        self.game_state.updateVestState(shield=shield, health=health)
        self.sendVestSYNACK()
        # Timer(self.RESEND_PKT_TIMEOUT, self.handleVestTimeout).start()

    def sendVestSYNACK(self):
        self.logger.info("<< Sending VESTSHOT SYN-ACK...")
        synack_packet = struct.pack("<b18x", ord(VESTSHOT_ACK_PKT))
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)

    def handleVestACK(self, data):
        self.logger.info(">> Received VESTSHOT ACK.")
        self._vestshot_in_progress = False  # flag to prevent timeout trigger
        print("Adding vest trigger to sender queue")
        self.data_queue.put(
            {
                "id": self.beetle_id,
                "type": VESTSHOT_PKT,
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
        shield, health = struct.unpack("<2B16x", data)
        self.game_state.applyVestState(shield=shield, health=health)

    def handleVestTimeout(self):
        """
        Resends the VESTSHOT SYN-ACK packet if no ACK is received within the timeout duration.
        """
        if self._vestshot_in_progress:
            self.logger.info(">> VESTSHOT timeout. Resending VESTSHOT SYN-ACK...")
            self.sendVestSYNACK()
            # Timer(self.RESEND_PKT_TIMEOUT, self.handleVestTimeout).start()

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

    @property
    def last_packet(self):
        return self._last_packet

    @last_packet.setter
    def last_packet(self, value):
        self._last_packet = value

    @property
    def expected_gunshot_id(self):
        return self._expected_gunshot_id

    @expected_gunshot_id.setter
    def expected_gunshot_id(self, value):
        self._expected_gunshot_id = value

    @property
    def successful_gunshots(self):
        return self._successful_gunshots

    @successful_gunshots.setter
    def successful_gunshots(self, value):
        self._successful_gunshots = value

    @property
    def unacknowledged_gunshots(self):
        return self._unacknowledged_gunshots

    @unacknowledged_gunshots.setter
    def unacknowledged_gunshots(self, value):
        self._unacknowledged_gunshots = value

    @property
    def seq_num(self):
        return self._seq_num

    @seq_num.setter
    def seq_num(self, value):
        self._seq_num = value
