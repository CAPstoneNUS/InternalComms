import time
import struct
import random
from bluepy import btle
from threading import Timer
from utils import getCRC, getTransmissionSpeed, logPacketStats


class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetle_connection, config, logger, mac_address, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetle_connection = beetle_connection
        self.config = config
        self.logger = logger
        self.beetle_id = mac_address[-2:]
        self.data_queue = data_queue
        self.buffer = bytearray()

        # For transmission speed stats
        self.start_time = time.time()
        self.total_data_size = 0

        # Gun handling
        self.unacknowledged_shots = set()
        self.successful_shots = []

        # Error handling counters
        self.crc_error_count = 0
        self.frag_packet_count = 0

        # Testing
        self.corrupt_packet_count = 0
        self.dropped_packet_count = 0

        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.MAX_CRC_ERROR_COUNT = self.config["storage"]["max_CRC_error_count"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]
        self.GUN_TIMEOUT = self.config["timeout"]["gun_timeout"]

    def handleNotification(self, cHandle, data):
        # Display stats every 5 seconds
        end_time = time.time()
        time_diff = end_time - self.start_time
        self.total_data_size += len(data)
        if time_diff >= 5:
            speed_kbps = getTransmissionSpeed(time_diff, self.total_data_size)
            logPacketStats(
                self.logger,
                speed_kbps,
                self.corrupt_packet_count,
                self.dropped_packet_count,
                self.frag_packet_count,
            )
            self.start_time = time.time()
            self.total_data_size = 0

        # Handle buffer overflow
        if len(self.buffer) + len(data) > self.MAX_BUFFER_SIZE:
            self.logger.warning("Buffer size limit exceeded. Discarding oldest data.")
            self.buffer = self.buffer[-(self.MAX_BUFFER_SIZE - len(data)) :]

        self.buffer.extend(data)

        while len(self.buffer) >= self.PACKET_SIZE:
            # Splice packet from buffer
            packet = self.buffer[: self.PACKET_SIZE]
            self.buffer = self.buffer[self.PACKET_SIZE :]

            # Extract packet type and CRC
            packet_type = packet[0]
            calculated_crc = getCRC(packet[:-1])
            true_crc = struct.unpack("<B", packet[-1:])[0]

            # Randomly corrupt data for testing
            if random.random() <= 0.1:
                self.corrupt_packet_count += 1
                data = bytearray([random.randint(0, 255) for _ in range(len(data))])

            # Randomly drop packets for testing
            if random.random() <= 0.1:
                self.dropped_packet_count += 1
                return

            if calculated_crc == true_crc:
                if packet_type == ord("A"):
                    self.logger.info(">> SYN-ACK received.")
                    self.beetle_connection.setACKFlag(True)
                    return
                elif packet_type == ord("M"):
                    self.processIMUPacket(packet[1:-1])
                elif packet_type == ord("G"):
                    self.processGunPacket(packet[1:-1])
                elif packet_type == ord("X"):
                    self.handleGunACK(packet[1:-1])
                elif packet_type == ord("Y"):
                    self.handleReloadSYNACK()
                else:
                    self.logger.error(f"Unknown packet type: {chr(packet_type)}")
            else:
                self.logger.error("CRC check failed. Clearing data buffer.")
                self.buffer = bytearray()
                self.crc_error_count += 1
                if self.crc_error_count > self.config["storage"]["max_CRC_error_count"]:
                    self.logger.error(
                        f"CRC error count: {self.crc_error_count}. Force disconnecting..."
                    )
                    self.beetle_connection.forceDisconnect()
                    self.crc_error_count = 0

        if len(self.buffer) > 0:
            self.frag_packet_count += 1

    def processIMUPacket(self, data):
        unpacked_data = struct.unpack("<6h6x", data)
        accX, accY, accZ, gyrX, gyrY, gyrZ = unpacked_data
        imu_data = {
            "id": self.beetle_id,
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

    def processGunPacket(self, data):
        shotID = struct.unpack("<B", data[:1])[0]
        if shotID not in self.unacknowledged_shots:
            self.logger.info(f">> Shot ID {shotID} received.")
            self.unacknowledged_shots.add(shotID)
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()
        else:
            self.logger.warning(
                f">> Duplicate Shot ID received: {shotID}. Dropping packet..."
            )

    def sendGunSYNACK(self, shotID):
        self.logger.info(f"<< Sending GUN SYN-ACK for Shot ID {shotID}...")
        synack_packet = struct.pack("<bB17x", ord("X"), shotID)
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(synack_packet)

    def handleGunACK(self, data):
        shotID = struct.unpack("<B", data[:1])[0]
        if shotID in self.unacknowledged_shots:
            self.successful_shots.append(shotID)
            self.unacknowledged_shots.remove(shotID)
            self.logger.info(f">> Shot ID {shotID} acknowledged.")
            self.logger.info(f"Successful shots: {self.successful_shots}")
        else:
            self.logger.warning(
                f">> Duplicate Shot ID ACK received: {shotID}. Dropping packet..."
            )

    def handleGunTimeout(self, shotID):
        if shotID in self.unacknowledged_shots:
            self.logger.warning(f"Timeout for Shot ID: {shotID}. Resending SYN-ACK.")
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()

    def handleReloadSYNACK(self):
        if self.beetle_connection.getReloadInProgress():
            self.logger.info(">> Received RELOAD SYN-ACK.")
            self.successful_shots = []
            self.beetle_connection.setReloadInProgress(False)
            self.sendReloadACK()
        else:
            self.logger.warning(">> Received unexpected RELOAD ACK.")

    def sendReloadACK(self):
        self.logger.info("<< Sending RELOAD ACK...")
        ack_packet = struct.pack("<b18x", ord("Y"))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetle_connection.writeCharacteristic(ack_packet)
