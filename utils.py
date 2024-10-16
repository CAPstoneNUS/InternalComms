import os
import csv
import sys
import crc8
import logging
from bluepy import btle
import yaml


def signal_handler(signal, frame, beetles):
    print("Ctrl+C detected. Sending reset signals to all Beetles...")

    for beetle in beetles:
        beetle.sendResetCommand()

    sys.exit(0)


def loadConfig():
    """
    Load the configuration from the config.yaml file.
    """
    with open("config.yaml", "r") as file:
        return yaml.safe_load(file)


def writeCSV(data):
    """
    Function to process data and write it to CSV files.

    This function writes the input data to separate CSV files based on
    the Beetle ID and packet type. It creates a new CSV file (if it doesn't exist)
    and appends the data to the file as it arrives.

    Args:
        data (dict): A dictionary containing the data to be written to the CSV.
    """
    data_dir = "data"
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    data_path = os.path.join(os.getcwd(), data_dir)

    id_and_type = f"{data['id']}_{data['type']}"
    filename = os.path.join(data_path, f"{id_and_type}_data_stream.csv")

    # Open file in append mode using a context manager
    with open(filename, "a", newline="") as csv_file:
        csv_writer = csv.DictWriter(csv_file, fieldnames=data.keys())

        # Only write the header if the file is empty
        if csv_file.tell() == 0:
            csv_writer.writeheader()

        # Write the row of data
        csv_writer.writerow(data)


def dataConsumer(config, data_queue):
    """
    Consumer function to process data from the queue and write it to CSV files.

    This function reads data from the queue and writes it to separate CSV files
    based on the Beetle ID and packet type. It creates a new CSV file and appends
    the data to the file as it arrives.

    Args:
        config (dict): The configuration dictionary loaded from the config.yaml file.
        data_queue (Queue): The shared queue object for data storage.
    """
    csv_files = {}
    csv_writers = {}

    data_dir = config["folder"]["data"]
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    data_path = os.path.join(os.getcwd(), data_dir)

    while True:
        try:
            data = data_queue.get(timeout=1)
            id_and_type = f"{data["id"]}_{data["type"]}"

            if id_and_type not in csv_files:
                filename = os.path.join(data_path, f"{id_and_type}_data.csv")
                csv_files[id_and_type] = open(filename, "w", newline="")
                csv_writers[id_and_type] = csv.DictWriter(
                    csv_files[id_and_type], fieldnames=data.keys()
                )
                csv_writers[id_and_type].writeheader()

            csv_writers[id_and_type].writerow(data)
            csv_files[id_and_type].flush()

        except Exception as e:
            # print(f"Filewrite error - no data in queue.")
            pass


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
