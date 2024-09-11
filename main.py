import queue
import threading
from beetle_connection import BeetleConnection
from utils import loadConfig, dataConsumer, setupLogger


def main():
    config = loadConfig()
    beetle_macs = [
        config["device"]["beetle_1"],
        config["device"]["beetle_2"],
    ]

    data_queue = queue.Queue()
    beetle_threads = []

    for mac in beetle_macs:
        logger = setupLogger(config, mac)
        beetle = BeetleConnection(config, logger, mac, data_queue)
        thread = threading.Thread(target=beetle.startComms)
        beetle_threads.append(thread)
        thread.start()

    consumer_thread = threading.Thread(target=dataConsumer, args=(config, data_queue))
    consumer_thread.start()

    for thread in beetle_threads:
        thread.join()

    consumer_thread.join()


if __name__ == "__main__":
    main()
