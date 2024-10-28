import time
import json
import threading
from utils import writeCSV
from collections import deque
from socket import socket, AF_INET, SOCK_STREAM


class RelayClient(threading.Thread):
    def __init__(self, config, sender_queue, server_gun_state, server_vest_state):
        super().__init__()
        self.config = config
        self.sender_queue = sender_queue
        self.server_gun_state = server_gun_state
        self.server_vest_state = server_vest_state

        self.ip = self.config["device"]["ultra_ip"]
        self.port = self.config["device"]["ultra_port"]
        self.gun_id = self.config["device"]["beetle_1"][-2:]
        self.ankle_id = self.config["device"]["beetle_2"][-2:]
        self.player_id = self.config["game"]["player_id"]

        self.lock = threading.Lock()
        self.gun_buffer = deque(maxlen=1)
        self.ankle_buffer = deque(maxlen=1)

        self.relayclient = socket(AF_INET, SOCK_STREAM)
        self.relayclient.connect((self.ip, self.port))
        print(f"\n+++++++++ CONNECTED TO {self.ip} & PORT {self.port} +++++++++\n")
        self.relayclient.setblocking(False)

    def run(self):
        receiver_thread = threading.Thread(target=self.receive)
        receiver_thread.start()

        try:
            while True:
                client_data = self.sender_queue.get()
                self.processAndSendData(client_data)
        except Exception as e:
            print(f"Error in main loop of RelayClient: {e}")
            self.relayclient.close()

    def processAndSendData(self, client_data):
        try:
            if client_data["type"] == "M":
                with self.lock:
                    if client_data["id"] == self.gun_id:
                        self.gun_buffer.append(client_data)
                    elif client_data["id"] == self.ankle_id:
                        self.ankle_buffer.append(client_data)

                    if self.gun_buffer and self.ankle_buffer:
                        paired_data = self.pairIMUData(
                            self.gun_buffer[0], self.ankle_buffer[0]
                        )
                        writeCSV("paired_data.csv", paired_data)
                        self.sendToUltra(paired_data)
            else:
                del client_data["id"]
                print(f"Sending [{client_data['type']}] type")
                self.sendToUltra(client_data)

        except Exception as e:
            print(f"Error processing data: {e}")

    def pairIMUData(self, gun_data, ankle_data):
        # Check that both data have the same packet type (for consistency)
        if gun_data["type"] != ankle_data["type"]:
            raise ValueError("Mismatched packet types between gun and ankle data")

        paired_data = {
            "type": gun_data["type"],  # Same for both
            "player_id": gun_data["player_id"],  # Same for both
            # Gun IMU data
            "gunAccX": gun_data["accX"],
            "gunAccY": gun_data["accY"],
            "gunAccZ": gun_data["accZ"],
            "gunGyrX": gun_data["gyrX"],
            "gunGyrY": gun_data["gyrY"],
            "gunGyrZ": gun_data["gyrZ"],
            # Ankle IMU data
            "ankleAccX": ankle_data["accX"],
            "ankleAccY": ankle_data["accY"],
            "ankleAccZ": ankle_data["accZ"],
            "ankleGyrX": ankle_data["gyrX"],
            "ankleGyrY": ankle_data["gyrY"],
            "ankleGyrZ": ankle_data["gyrZ"],
        }

        return paired_data

    def sendToUltra(self, data):
        try:
            msg_str = json.dumps(data)
            msg_tosend = f"{len(msg_str)}_{msg_str}"
            self.relayclient.sendall(msg_tosend.encode("utf-8"))
        except socket.timeout:
            print("Socket timed out")
        except socket.error as e:
            print(f"Failed to send from relay client to Ultra96: {e}")
            self.relayclient.close()

    def receive(self):
        while True:
            try:
                data = b""
                # Loop to receive data until it ends with '_'
                while not data.endswith(b"_"):
                    _d = self.relayclient.recv(1)
                    if not _d:
                        data = b""
                        break
                    data += _d

                # Decode and parse length if data is not empty
                if data:
                    data = data.decode("utf-8")
                    if data[:-1].isdigit():
                        length = int(data[:-1])
                    else:
                        raise ValueError("Received length is not a valid integer.")

                    data = b""
                    while len(data) < length:
                        _d = self.relayclient.recv(length - len(data))
                        if not _d:
                            data = b""
                            break
                        data += _d

                    # Decode and process JSON data if received completely
                    if data:
                        decoded_data = data.decode("utf-8")
                        print(f"\nServer sent: {decoded_data}\n")

                        # Assuming JSON format in decoded_data
                        decoded_data = json.loads(decoded_data)
                        if (
                            "bullets" in decoded_data
                            and "health" in decoded_data
                            and "hp_shield" in decoded_data
                            and "player_id" in decoded_data
                        ):
                            if decoded_data["player_id"] == self.player_id:
                                self.safePut(
                                    self.server_gun_state,
                                    {"bullets": decoded_data["bullets"]},
                                )
                                self.safePut(
                                    self.server_vest_state,
                                    {
                                        "health": decoded_data["health"],
                                        "shield": decoded_data["hp_shield"],
                                    },
                                )
                        else:
                            raise KeyError("One or more keys missing from the dictionary.")
                else:
                    raise ValueError("Received empty data.")

            except BlockingIOError:
                time.sleep(0.1)
            except Exception as e:
                print(f"Error receiving data: {e}")
                self.relayclient.close()
                break

    def safePut(self, q, data):
        if q.full():
            print(f"Replacing data in queue with {data}")
            q.get()
        q.put(data)
