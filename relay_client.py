import json
import threading
import time
from utils import writeCSV
from socket import socket, AF_INET, SOCK_STREAM


class RelayClient(threading.Thread):
    def __init__(self, sending_q, ip, port):
        super().__init__()
        self.ip = ip
        self.port = port
        self.sending_q = sending_q
        self.relayclient = socket(AF_INET, SOCK_STREAM)
        self.relayclient.connect((self.ip, self.port))
        print(f"Connected to {ip} and port {port} \n")
        self.relayclient.setblocking(False)

    def run(self):
        receiving_thread = threading.Thread(target=self.receive)
        receiving_thread.start()

        try:
            while True:
                client_data = self.sending_q.get()
                writeCSV(client_data)
                msg_str = json.dumps(client_data)
                # msg_str = json.dumps(IMU_DATA)
                msg_tosend = str(len(msg_str)) + "_" + msg_str
                print("Preparing to send ...\n")
                self.relayclient.sendall(msg_tosend.encode("utf-8"))
                print("Sent to Ultra96", end="\r")

        except socket.timeout:
            print("Socket timed out")
        except socket.error as e:
            print("Failed to send from relay client to Ultra96")
            print(e)
            self.relayclient.close()

    def receive(self):
        try:
            while True:
                try:
                    data = b""
                    while not data.endswith(b"_"):
                        _d = self.relayclient.recv(1)
                        # print(f"_d is {_d}")
                        if not _d:
                            data = b""
                            break
                        data += _d
                        if len(data) == 0:
                            # print("no more data to receive \n")
                            break
                    data = data.decode("utf-8")
                    # print(f"data is {data}")
                    length = int(data[:-1])

                    data = b""
                    while len(data) < length:
                        _d = self.relayclient.recv(length - len(data))
                        if not _d:
                            data = b""
                            break
                        data += _d
                        if len(data) == 0:
                            # print("no more data to receive \n")
                            break
                    decoded_data = data.decode("utf-8")
                    print(f"[RELAY CLIENT] {decoded_data}\n")
                except BlockingIOError:
                    time.sleep(0.1)
        except Exception as e:
            print("Error receiving data")
            print(e)
            self.relayclient.close()
