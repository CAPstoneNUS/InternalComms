import os
import csv
import sys
import crc8
import logging
from bluepy import btle
import yaml


def loadConfig():
    with open("config.yaml", "r") as file:
        return yaml.safe_load(file)


def dataConsumer(config, data_queue):
    csv_files = {}
    csv_writers = {}

    data_dir = config["folder"]["data"]
    if not os.path.exists(data_dir):
        os.makedirs(data_dir)
    data_path = os.path.join(os.getcwd(), data_dir)

    while True:
        try:
            data = data_queue.get(timeout=1)
            id = data["id"]

            if id not in csv_files:
                filename = os.path.join(data_path, f"{id}_data.csv")
                csv_files[id] = open(filename, "w", newline="")
                csv_writers[id] = csv.DictWriter(csv_files[id], fieldnames=data.keys())
                csv_writers[id].writeheader()

            csv_writers[id].writerow(data)
            csv_files[id].flush()

        except Exception as e:
            # print(f"Filewrite error - no data in queue.")
            pass


def setupLogger(config, mac_address):
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
    crc = crc8.crc8()
    crc.update(data)
    bytes_crc = crc.digest()
    crc_value = int.from_bytes(bytes_crc, "little")
    return crc_value


def getTransmissionSpeed(time_diff, total_data_size):
    speed_kbps = (total_data_size * 8 / 1000) / time_diff
    return speed_kbps


def getDeviceInfo(mac_address):
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
