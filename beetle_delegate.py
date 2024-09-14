import time
import struct
from bluepy import btle
from threading import Timer
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

        # Error handling counters
        self.crc_error_count = 0
        self.frag_packet_count = 0

        # For transmission speed stats
        self.start_time = time.time()
        self.total_data_size = 0

        # Gun and reload handling
        self.unacknowledged_shots = set()
        self.reload_in_progress = False
        self.reload_timer = None
        self.GUN_TIMEOUT = 3
        self.RELOAD_TIMEOUT = 5

        self.MAX_BUFFER_SIZE = self.config["storage"]["max_buffer_size"]
        self.MAX_QUEUE_SIZE = self.config["storage"]["max_queue_size"]
        self.MAX_CRC_ERROR_COUNT = self.config["storage"]["max_CRC_error_count"]
        self.PACKET_SIZE = self.config["storage"]["packet_size"]

    def handleNotification(self, cHandle, data):

        # For transmission speed stats
        end_time = time.time()
        time_diff = end_time - self.start_time
        self.total_data_size += len(data)
        if time_diff >= 3:
            speed_kbps = getTransmissionSpeed(time_diff, self.total_data_size)
            # self.logger.info(
            #     f"Transmission speed over {time_diff:.2f} seconds: {speed_kbps:.2f} kbps"
            # )
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
                elif packet_type == ord("G"):
                    self.processGunPacket(packet[1:-1])
                elif packet_type == ord("X"):
                    self.handleGunACK(packet[1:-1])
                elif packet_type == ord("M"):
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
                    self.beetleConnection.forceDisconnect()
                    self.crc_error_count = 0

        if len(self.buffer) > 0:
            self.frag_packet_count += 1
            # self.logger.warning(
            #     f"Fragmented packet count on Beetle {self.beetle_id}: {self.frag_packet_count}"
            # )

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
            self.logger.info(f"Shot ID {shotID} received.")
            self.unacknowledged_shots.add(shotID)
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()
        else:
            self.logger.error(f"Duplicate shot ID: {shotID}. Resending GUN SYN-ACK...")
            self.sendGunSYNACK(shotID)

    def sendGunSYNACK(self, shotID):
        self.logger.info(f"Sending GUN SYN-ACK for Shot ID {shotID}...")
        synack_packet = struct.pack("<bB17x", ord("X"), shotID)
        crc = getCRC(synack_packet)
        synack_packet += struct.pack("B", crc)
        self.beetleConnection.writeCharacteristic(synack_packet)

    def handleGunACK(self, data):
        shotID = struct.unpack("<B", data[:1])[0]
        if shotID in self.unacknowledged_shots:
            self.unacknowledged_shots.remove(shotID)
            self.logger.info(f"Shot ID {shotID} acknowledged.")
        else:
            self.logger.warning(f"Received ACK for unknown gun shot: {shotID}")

    def handleGunTimeout(self, shotID):
        if shotID in self.unacknowledged_shots:
            self.logger.warning(f"Timeout for shot ID: {shotID}. Resending SYN-ACK.")
            self.sendGunSYNACK(shotID)
            Timer(self.GUN_TIMEOUT, self.handleGunTimeout, args=[shotID]).start()

    def sendReload(self):
        if not self.reload_in_progress:
            self.reload_in_progress = True
            self.logger.info("Sending RELOAD signal...")
            reload_packet = struct.pack("<b18x", ord("R"))
            crc = getCRC(reload_packet)
            reload_packet += struct.pack("B", crc)
            self.beetleConnection.writeCharacteristic(reload_packet)
            # self.reload_timer = Timer(self.RELOAD_TIMEOUT, self.handleReloadTimeout)
            self.reload_timer.start()

    def handleReloadSYNACK(self):
        if self.reload_in_progress:
            self.reload_in_progress = False
            if self.reload_timer:
                self.reload_timer.cancel()
            self.logger.info("Reload acknowledged by Arduino.")
            self.sendReloadACK()
        else:
            self.logger.warning("Received unexpected RELOAD ACK.")

    def sendReloadACK(self):
        self.logger.info("Sending RELOAD ACK...")
        ack_packet = struct.pack("<b18x", ord("Y"))
        crc = getCRC(ack_packet)
        ack_packet += struct.pack("B", crc)
        self.beetleConnection.writeCharacteristic(ack_packet)

    def handleReloadTimeout(self):
        if self.reload_in_progress:
            self.logger.warning("Reload timeout. Resending RELOAD signal.")
            self.sendReload()
