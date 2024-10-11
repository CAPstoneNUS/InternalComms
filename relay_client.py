import time
import json
import threading
from utils import writeCSV
from collections import deque
from socket import socket, AF_INET, SOCK_STREAM


class RelayClient(threading.Thread):
    def __init__(self, config, sending_q):
        super().__init__()
        self.config = config
        self.sending_q = sending_q

        self.ip = self.config["device"]["ultra_ip"]
        self.port = self.config["device"]["ultra_port"]
        self.gun_id = self.config["device"]["beetle_1"][-2:]
        self.ankle_id = self.config["device"]["beetle_2"][-2:]

        self.lock = threading.Lock()
        self.gun_buffer = deque(maxlen=1)
        self.ankle_buffer = deque(maxlen=1)

        self.relayclient = socket(AF_INET, SOCK_STREAM)
        self.relayclient.connect((self.ip, self.port))
        print(f"Connected to {self.ip} and port {self.port} \n")
        self.relayclient.setblocking(False)

    def run(self):
        receiving_thread = threading.Thread(target=self.receive)
        receiving_thread.start()

        try:
            while True:
                client_data = self.sending_q.get()
                self.processAndSendData(client_data)
        except Exception as e:
            print(f"Error in main loop of RelayClient: {e}")
            self.relayclient.close()

    def processAndSendData(self, client_data):
        try:
            if client_data["id"] in [self.gun_id, self.ankle_id]:
                with self.lock:
                    if client_data["id"] == self.gun_id:
                        self.gun_buffer.append(client_data)
                    elif client_data["id"] == self.ankle_id:
                        self.ankle_buffer.append(client_data)

                    if self.gun_buffer and self.ankle_buffer:
                        paired_data = self.pairIMUData(
                            self.gun_buffer[0], self.ankle_buffer[0]
                        )
                        self.sendToUltra(paired_data)
            else:
                self.sendToUltra(client_data)

        except Exception as e:
            print(f"Error processing data: {e}")

    def pairIMUData(self, gun_data, ankle_data):
        # Check that both data have the same packet type (for consistency)
        if gun_data["type"] != ankle_data["type"]:
            raise ValueError("Mismatched packet types between gun and ankle data")

        paired_data = {
            "type": gun_data["type"],  # Same for both
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
            print(f"Preparing to send data...")
            self.relayclient.sendall(msg_tosend.encode("utf-8"))
            print("Sent to Ultra96", end="\r")
        except socket.timeout:
            print("Socket timed out")
        except socket.error as e:
            print(f"Failed to send from relay client to Ultra96: {e}")
            self.relayclient.close()

    def receive(self):
        while True:
            try:
                data = b""
                while not data.endswith(b"_"):
                    _d = self.relayclient.recv(1)
                    if not _d:
                        data = b""
                        break
                    data += _d
                    if len(data) == 0:
                        break
                data = data.decode("utf-8")
                length = int(data[:-1])

                data = b""
                while len(data) < length:
                    _d = self.relayclient.recv(length - len(data))
                    if not _d:
                        data = b""
                        break
                    data += _d
                    if len(data) == 0:
                        break
                decoded_data = data.decode("utf-8")
                print(f"[RELAY CLIENT] {decoded_data}\n")
            except BlockingIOError:
                time.sleep(0.1)
            except Exception as e:
                print(f"Error receiving data: {e}")
                self.relayclient.close()
                break
