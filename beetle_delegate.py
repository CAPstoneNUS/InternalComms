import time
import struct
from bluepy import btle
from utils import getCRC, getTransmissionSpeed


class BeetleDelegate(btle.DefaultDelegate):
    def __init__(self, beetleConnection, config, logger, mac_address, data_queue):
        btle.DefaultDelegate.__init__(self)
        self.beetleConnection = beetleConnection
        self.config = config
        self.logger = logger
        self.beetle_id = mac_address[-2:]
        self.data_queue = data_queue
        self.buffer = bytearray()

        self.crc_error_count = 0
        self.frag_packet_count = 0

        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.MAX_CRC_ERROR_COUNT = self.config["storage"]["max_CRC_error_count"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]

        # For transmission speed stats
        self.start_time = time.time()
        self.total_data_size = 0

    def handleNotification(self, cHandle, data):

        # For transmission speed stats
        end_time = time.time()
        time_diff = end_time - self.start_time
        self.total_data_size += len(data)
        if time_diff >= 3:
            speed_kbps = getTransmissionSpeed(time_diff, self.total_data_size)
            self.logger.info(
                f"Transmission speed over {time_diff:.2f} seconds: {speed_kbps:.2f} kbps"
            )
            self.start_time = time.time()
            self.total_data_size = 0

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

            if calculated_crc == true_crc:
                if packet_type == ord("A"):
                    self.logger.info("SYN-ACK received.")
                    self.beetleConnection.setACKFlag(True)
                    return
                elif packet_type == ord("M"):
                    self.processIMUPacket(packet[1:-1])
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
                    self.beetleConnection.forceDisconnect()
                    self.crc_error_count = 0

        if len(self.buffer) > 0:
            self.frag_packet_count += 1
            self.logger.warning(
                f"Fragmented packet count on Beetle {self.beetle_id}: {self.frag_packet_count}"
            )

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
            self.logger.warning("Data queue is full. Discarding oldest data...")
            self.data_queue.get()

        self.data_queue.put(imu_data)
