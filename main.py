import sys
import signal
import queue
import threading
from game_state import GameState
from relay_client import RelayClient
from beetle_connection import BeetleConnection
from utils import loadConfig, collectData, setupLogger, signalHandler


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
    beetle_conns = []
    game_state = GameState()
    sender_queue = queue.Queue()
    server_gun_state, server_vest_state = queue.Queue(maxsize=1), queue.Queue(maxsize=1)
    relay_client = RelayClient(config, sender_queue, server_gun_state, server_vest_state)

    for mac in beetle_macs:
        logger = setupLogger(config, mac)
        beetle = BeetleConnection(
            config,
            logger,
            mac,
            sender_queue,
            server_gun_state,
            server_vest_state,
            game_state,
        )
        beetle_conns.append(beetle)
        thread = threading.Thread(target=beetle.startComms)
        beetle_threads.append(thread)
        thread.start()

    signal.signal(signal.SIGINT, lambda sig, frame: signalHandler(sig, frame, game_state, beetle_conns))

    # consumer_thread = threading.Thread(target=collectData, args=(sender_queue, config))
    # consumer_thread.start()
    # consumer_thread.join()
    
    relay_client.start()

    for thread in beetle_threads:
        thread.join()

    relay_client.join()


if __name__ == "__main__":
    main()
