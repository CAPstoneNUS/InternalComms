import queue
import threading
from game_state import GameState
from relay_client import RelayClient
from beetle_connection import BeetleConnection
from utils import loadConfig, dataConsumer, setupLogger


def main():
    """
    Main function to set up and run the Beetle communication system.

    This function performs the following tasks:
    1. Loads the configuration file
    2. Initializes a (shared) data queue for communication between threads
    3. Sets up a logger, connection and thread for each Beetle, and starts the threads
    4. Creates and starts a consumer thread to process data from the queue
    5. Waits for all threads to complete

    The function uses threading to handle multiple Beetle connections concurrently
    and manages data flow through a shared queue.
    """
    config = loadConfig()
    beetle_macs = [
        config["device"]["beetle_1"],
        config["device"]["beetle_2"],
        config["device"]["beetle_3"],
    ]

    beetle_threads = []
    game_state = GameState()
    data_queue = queue.Queue()
    # relay_client = RelayClient(
    # config["device"]["ultra_ip"], config["device"]["ultra_port"], config, data_queue
    # )

    for mac in beetle_macs:
        logger = setupLogger(config, mac)
        beetle = BeetleConnection(config, logger, mac, data_queue, game_state)
        thread = threading.Thread(target=beetle.startComms)
        beetle_threads.append(thread)
        thread.start()

    consumer_thread = threading.Thread(target=dataConsumer, args=(config, data_queue))
    # consumer_thread = threading.Thread(target=relay_client.run)
    consumer_thread.start()

    for thread in beetle_threads:
        thread.join()

    consumer_thread.join()


if __name__ == "__main__":
    main()
