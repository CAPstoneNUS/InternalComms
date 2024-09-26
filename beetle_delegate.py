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
VEST_PKT = "V"
GUN_ACK_PKT = "X"
VEST_ACK_PKT = "Z"
GUN_NAK_PKT = "T"
BOMB_SYNACK_PKT = "C"
RELOAD_SYNACK_PKT = "Y"
ACTION_SYNACK_PKT = "E"
SHIELD_SYNACK_PKT = "Q"


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

        unacknowledged_shots (set): Set to temporarily store Shot IDs with pending ACKs.
        successful_shots (set): Set to store Shot IDs that have been successfully acknowledged.

        frag_packet_count (int): Count of fragmented packets received.
        corrupt_packet_count (int): Count of corrupted packets received.
        dropped_packet_count (int): Count of packets dropped.

        MAX_BUFFER_SIZE (int): Maximum buffer size for storing incoming data.
        MAX_QUEUE_SIZE (int): Maximum queue size for storing IMU data.
        MAX_CORRUPT_PKT_PCT (int): Maximum percentage of corrupt packets to tolerate before disconnecting.
        PACKET_SIZE (int): Size of each data packet.
        GUN_TIMEOUT (int): Timeout duration for gun shot acknowledgements.
        STATS_LOG_INTERVAL (int): Interval for displaying transmission speed stats.
    """

    def __init__(self, beetle_connection, config, logger, mac_address, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetle_connection = beetle_connection
        self.config = config
        self.logger = logger
        self.beetle_id = mac_address[-2:]
        self.data_queue = data_queue
        self.buffer = deque()

        # For transmission speed stats
        self.start_time = time.time()
        self.total_window_data = 0
        self.total_data = 0

        # Gun handling
        self._shot_in_progress = False
        self.expected_shot_id = 1
        self.successful_shots = set()
        self.unacknowledged_shots = set()

        # Counters
        self.frag_packet_count = 0
        self.corrupt_packet_count = 0
        self.total_corrupted_packets = 0
        self.dropped_packet_count = 0

        self.MAG_SIZE = self.config["storage"]["mag_size"]
        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.MAX_CORRUPT_PKT_PCT = self.config["storage"]["max_corrupt_pkt_pct"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]
        self.GUN_TIMEOUT = self.config["time"]["gun_timeout"]
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
            packet_type = packet[0]
            calculated_crc = getCRC(packet[:-1])
            true_crc = struct.unpack("<B", packet[-1:])[0]

            # Check CRC
            if calculated_crc != true_crc:
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

            # Handle packet based on type
            if packet_type == ord(HS_SYNACK_PKT):
                self.handleSYNACKPacket()
            elif packet_type == ord(IMU_DATA_PKT):
                self.handleIMUPacket(packet[1:-1])
            elif packet_type == ord(GUN_PKT):
                self.handleGunPacket(packet[1:-1])
            elif packet_type == ord(GUN_ACK_PKT):
                self.handleGunACK(packet[1:-1])
            elif packet_type == ord(RELOAD_SYNACK_PKT):
                self.handleReloadSYNACK()
            elif packet_type == ord(VEST_PKT):  # player vest shot detected by IR
                self.handleVestPacket(packet[1:-1])
            elif packet_type == ord(VEST_ACK_PKT):  # vest shot ACK
                self.handleVestACK(packet[1:-1])
            elif packet_type == ord(BOMB_SYNACK_PKT):
                self.handleBombSYNACK()
            elif packet_type == ord(ACTION_SYNACK_PKT):
                self.handleActionSYNACK()
            elif packet_type == ord(SHIELD_SYNACK_PKT):
                self.handleShieldSYNACK()
            else:
                self.logger.error(f"Unknown packet type: {chr(packet_type)}")

        # Check for fragmented packets
        if len(self.buffer) > 0:
            self.frag_packet_count += 1

        # Check buffer size and discard oldest data if overflow
        if len(self.buffer) > self.MAX_BUFFER_SIZE:
            overflow = len(self.buffer) - self.MAX_BUFFER_SIZE
            self.logger.warning(f"Buffer overflow. Discarding {overflow} bytes.")
            for _ in range(overflow):
                self.buffer.popleft()

        # Display stats every 5 seconds
        end_time = time.time()
        time_diff = end_time - self.start_time
        self.total_window_data += len(data)
        if time_diff >= self.STATS_LOG_INTERVAL:
            speed_kbps = getTransmissionSpeed(time_diff, self.total_window_data)
            logPacketStats(
                self.logger,
                speed_kbps,
                self.total_corrupted_packets,
                self.dropped_packet_count,
                self.frag_packet_count,
            )
            self.start_time = time.time()
            self.total_window_data = 0

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

    def handleGunPacket(self, data):
        """
        Adds the Shot ID to the unacknowledged_shots set() and sends the SYN-ACK packet.

        Args:
            data (bytes): The gun shot packet.
        """
        # Drop attempted shots during a reload
        if self.beetle_connection.reload_in_progress:
            self.logger.warning(">> Shot triggered while reloading. Dropping packet...")
            return

        # Send NAK if a shot is skipped and return
        shotID = struct.unpack("<B", data[:1])[0]
        if shotID != self.expected_shot_id:
            self.logger.warning(
                f">> Skipped Shot ID {self.expected_shot_id} and got {shotID} instead."
            )
            self.sendGunNAK(self.expected_shot_id)
            return

        # Handle gun shot
        self._shot_in_progress = True
        if shotID not in self.unacknowledged_shots:
            self.logger.info(f">> Shot ID {shotID} received.")
            self.unacknowledged_shots.add(shotID)
            self.expected_shot_id = shotID + 1
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()
        else:
            self.logger.warning(
                f">> Duplicate Shot ID received: {shotID}. Dropping packet..."
            )

    def sendGunSYNACK(self, shotID):
        """
        Sends the SYN-ACK packet for the gun shot.

        Args:
            shotID (int): The Shot ID of the gun shot.
        """
        self.logger.info(f"<< Sending GUN SYN-ACK for Shot ID {shotID}...")
        synack_packet = struct.pack("<bB17x", ord("X"), shotID)
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)

    def handleGunACK(self, data):
        """
        Appends the Shot ID to the successful_shots list(), puts it
        in the queue and removes it from unacknowledged_shots set().

        Args:
            data (bytes): The gun shot acknowledgement packet.
        """
        shotID = struct.unpack("<B", data[:1])[0]
        if shotID in self.unacknowledged_shots:
            self._shot_in_progress = False
            self.unacknowledged_shots.remove(shotID)
            self.successful_shots.add(shotID)
            self.checkForMissingShots(shotID)
            self.data_queue.put(
                {
                    "id": self.beetle_id,
                    "type": GUN_PKT,
                    "shotID": shotID,
                    "successfulShots": self.successful_shots,
                }
            )
            self.logger.info(f">> Shot ID {shotID} acknowledged.")
            self.logger.info(f"Successful shots: {self.successful_shots}")
        else:
            self.logger.warning(
                f">> Duplicate Shot ID ACK received: {shotID}. Dropping packet..."
            )

    def checkForMissingShots(self, curr_shot):
        """
        Check that all Shot IDs up to the current shot have been acknowledged.
        If not, send retransmission requests for the missing shots.

        Args:
            curr_shot (int): The Shot ID of the current gun shot.
        """
        for shot in range(1, curr_shot):
            if shot not in self.successful_shots:
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

    def handleGunTimeout(self, shotID):
        """
        Resends the GUN SYN-ACK packet if no ACK is received within the timeout duration.

        Args:
            shotID (int): The Shot ID of the gun shot.
        """
        if shotID in self.unacknowledged_shots:
            self.logger.warning(
                f"Timeout for Shot ID: {shotID}. Resending GUN SYN-ACK."
            )
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()

    def handleReloadSYNACK(self):
        """
        Disables the reload_in_progress flag and sends the RELOAD ACK packet.
        """
        if self.beetle_connection.reload_in_progress:
            self.logger.info(">> Received RELOAD SYN-ACK.")
            self.expected_shot_id = 1
            self.unacknowledged_shots = set()
            self.successful_shots = set()
            self.beetle_connection.reload_in_progress = False
            self.sendReloadACK()
        else:
            self.logger.warning(">> Received unexpected RELOAD ACK.")

    def sendReloadACK(self):
        """
        Sends the RELOAD ACK packet to the Beetle.
        """
        self.logger.info("<< Sending RELOAD ACK...")
        ack_packet = struct.pack("<b18x", ord("Y"))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)

    def handleVestPacket(self, data):
        pass

    def handleVestACK(self, data):
        pass

    def handleBombSYNACK(self):
        pass

    def handleActionSYNACK(self):
        pass

    def handleShieldSYNACK(self):
        pass

    def handleHealthNAK(self):
        # Logic to be added later
        pass

    def handleBombTimeout(self):
        # Logic to be added later
        pass

    @property
    def shot_in_progress(self):
        return self._shot_in_progress

    @shot_in_progress.setter
    def shot_in_progress(self, value):
        self.shot_in_progress = value
