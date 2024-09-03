import sys
from bluepy import btle
import yaml


def load_config():
    with open("config.yaml", "r") as file:
        return yaml.safe_load(file)


def get_device_info(mac_address):
    try:
        device = btle.Peripheral(mac_address)
        services = device.getServices()

        print(f"Device MAC: {mac_address}")
        for service in services:
            print(f"Service UUID: {service.uuid}")
            characteristics = service.getCharacteristics()
            for char in characteristics:
                print(f"  Characteristic UUID: {char.uuid} | Properties: {char.propertiesToString()}")
        
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
    get_device_info(mac_address)
