import yaml
from beetle_connection import BeetleConnection

def load_config():
    with open("config.yaml", "r") as file:
        return yaml.safe_load(file)

def main():
    config = load_config()
    BEETLE_1_MAC = config["device"]["beetle_1"]

    try:
        beetle_1 = BeetleConnection(config, BEETLE_1_MAC)
        beetle_1.startComms()
    
    except Exception as e:
        print(f"An error occurred: {str(e)}")

if __name__ == '__main__':
    main()
