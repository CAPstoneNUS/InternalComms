import os
import csv
import sys
import crc8
import yaml
import logging
from bluepy import btle
from collections import deque


def signalHandler(signal, frame, game_state, beetles):
    # Save the game state to a file
    game_state.saveState()

    # Send a reset signal to all Beetles
    for beetle in beetles:
        beetle.killBeetle()

    sys.exit(0)


def loadConfig():
    """
    Load the configuration from the config.yaml file.
    """
    with open("config.yaml", "r") as file:
        return yaml.safe_load(file)


def collectData(data_queue, config):
    gun_buffer = deque(maxlen=1)
    ankle_buffer = deque(maxlen=1)

    try:
        while True:
            data = data_queue.get(timeout=1)
            if data["type"] == "M":
                if data["id"] == config["device"]["beetle_1"][-2:]:
                    gun_buffer.append(data)
                elif data["id"] == config["device"]["beetle_2"][-2:]:
                    ankle_buffer.append(data)
                
                if gun_buffer and ankle_buffer:
                    paired_data = pairIMUData(gun_buffer[0], ankle_buffer[0])
                    writeCSV("paired_data.csv", paired_data)
    except Exception as e:
        print(f"Error in data collection: {e}")
                
def pairIMUData(gun_data, ankle_data):
    if gun_data["type"] != "M" or ankle_data["type"] != "M":
        raise ValueError("Invalid data types for pairing.")

    paired_data = {
        "type": gun_data["type"],
        "player_id": gun_data["player_id"],
        "gunAccX": gun_data["accX"],
        "gunAccY": gun_data["accY"],
        "gunAccZ": gun_data["accZ"],
        "gunGyrX": gun_data["gyrX"],
        "gunGyrY": gun_data["gyrY"],
        "gunGyrZ": gun_data["gyrZ"],
        "ankleAccX": ankle_data["accX"],
        "ankleAccY": ankle_data["accY"],
        "ankleAccZ": ankle_data["accZ"],
        "ankleGyrX": ankle_data["gyrX"],
        "ankleGyrY": ankle_data["gyrY"],
        "ankleGyrZ": ankle_data["gyrZ"],
    }

    return paired_data

def writeCSV(file_path, paired_data):
    # Check if the CSV file already exists
    file_exists = os.path.isfile(file_path)

    # Define the fieldnames for the CSV (headers)
    fieldnames = [
        "type",
        "player_id",
        "gunAccX",
        "gunAccY",
        "gunAccZ",
        "gunGyrX",
        "gunGyrY",
        "gunGyrZ",
        "ankleAccX",
        "ankleAccY",
        "ankleAccZ",
        "ankleGyrX",
        "ankleGyrY",
        "ankleGyrZ",
    ]

    # Open the CSV file in append mode
    with open(file_path, mode="a", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

        # Write the header only if the file doesn't exist
        if not file_exists:
            writer.writeheader()

        # Append the data to the CSV file
        writer.writerow(paired_data)

# def dataConsumer(config, data_queue):
#     csv_files = {}
#     csv_writers = {}

#     data_dir = config["folder"]["data"]
#     if not os.path.exists(data_dir):
#         os.makedirs(data_dir)
#     data_path = os.path.join(os.getcwd(), data_dir)

#     while True:
#         try:
#             data = data_queue.get(timeout=1)
#             id_and_type = f"{data["id"]}_{data["type"]}"

#             if id_and_type not in csv_files:
#                 filename = os.path.join(data_path, f"{id_and_type}_data.csv")
#                 csv_files[id_and_type] = open(filename, "w", newline="")
#                 csv_writers[id_and_type] = csv.DictWriter(
#                     csv_files[id_and_type], fieldnames=data.keys()
#                 )
#                 csv_writers[id_and_type].writeheader()

#             csv_writers[id_and_type].writerow(data)
#             csv_files[id_and_type].flush()

#         except Exception as e:
#             # print(f"Filewrite error - no data in queue.")
#             pass


def setupLogger(config, mac_address):
    """
    Set up a logger for the Beetle with the given MAC address.

    This function creates a logger object for the Beetle with the given
    MAC address. It configures the logger to write logs to a file in the logs
    folder based on the MAC address.

    Args:
        config (dict): The configuration dictionary loaded from the config.yaml file.
        mac_address (str): The MAC address of the Beetle.

    Returns:
        logger (Logger): The logger object for the Beetle.
    """
    logs_dir = config["folder"]["logs"]
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
    logs_path = os.path.join(os.getcwd(), logs_dir)  # to logs/ folder
    beetle_id = mac_address[-2:]
    log_file = f"{beetle_id}_log.txt"  # to logs/XX_log.txt file
    log_path = os.path.join(logs_path, log_file)

    logger = logging.getLogger(f"Beetle {beetle_id}")
    logger.setLevel(logging.DEBUG)

    file_handler = logging.FileHandler(log_path, mode="w")
    file_handler.setLevel(logging.DEBUG)

    file_format = logging.Formatter("%(name)s - %(levelname)s - %(message)s")
    file_handler.setFormatter(file_format)

    logger.addHandler(file_handler)

    return logger


def getCRC(data):
    """
    Calculate the CRC-8 checksum for the given data.

    Args:
        data (bytes): The data to calculate the CRC for.

    Returns:
        crc_value (int): The CRC-8 checksum value for the data.
    """
    crc = crc8.crc8()
    crc.update(data)
    bytes_crc = crc.digest()
    crc_value = int.from_bytes(bytes_crc, "little")
    return crc_value


def getTransmissionSpeed(time_diff, total_data_size):
    """
    Calculate the transmission speed in kbps based on the total data size and time taken.

    Args:
        time_diff (float): The time taken for data transmission in seconds.
        total_data_size (int): The total size of data transmitted in bytes.

    Returns:
        speed_kbps (float): The transmission speed in kbps.
    """
    speed_kbps = (total_data_size * 8 / 1000) / time_diff
    return speed_kbps


def logPacketStats(
    logger, speed_kbps, corrupt_packet_count, dropped_packet_count, frag_packet_count
):
    """
    Log the packet statistics for the Beetle.
    """
    logger.info("--------- PACKET STATS ---------")
    logger.info(f"Avg TX speed: {speed_kbps:.2f} kbps")
    logger.info(f"Corrupted packets: {corrupt_packet_count}")
    logger.info(f"Dropped packets: {dropped_packet_count}")
    logger.info(f"Fragmented packets: {frag_packet_count}")
    logger.info("--------------------------------")


def getDeviceInfo(mac_address):
    """
    Get information about the device with the given MAC address.

    This function connects to the device using the Bluepy library and retrieves
    information about the services and characteristics available on the device.

    Args:
        mac_address (str): The MAC address of the device to connect to.
    """
    try:
        device = btle.Peripheral(mac_address)
        services = device.getServices()

        print(f"Device MAC: {mac_address}")
        for service in services:
            print(f"Service UUID: {service.uuid}")
            characteristics = service.getCharacteristics()
            for char in characteristics:
                print(
                    f"  Characteristic UUID: {char.uuid} | Properties: {char.propertiesToString()}"
                )

        device.disconnect()
    except btle.BTLEDisconnectError:
        print(f"Failed to connect to device with MAC address: {mac_address}")
    except Exception as e:
        print(f"An error occurred: {str(e)}")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Usage: python script_name.py <device_mac_address>")
        sys.exit(1)

    mac_address = sys.argv[1]
    getDeviceInfo(mac_address)
