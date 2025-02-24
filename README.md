# Internal Communications for Action Recognition IoT Game

## Overview
This repository contains the internal communication system for an action recognition IoT game. The system facilitates real-time data streaming and communication between six Beetle IMU devices and an Ultra96 FPGA. It is designed to handle Bluetooth-based IMU data collection, preprocessing for AI model training, and reliable socket communication with the FPGA.

## Key Features
- **Multi-Threaded Data Streaming:** Efficient real-time data collection from six Beetle IMUs using Python multithreading.
- **Bluetooth Communication Management:** Implements logging mechanisms to track the status of Bluetooth connections, ensuring reliability.
- **Socket Communication with Ultra96 FPGA:** Establishes and monitors socket connections for stable data transmission.
- **Protocol Handling:** Manages packet fragmentation, handling of dropped packets, negative acknowledgments (NAKs), and duplicate data prevention.
- **Data Preprocessing:** Processes IMU data for AI model training, ensuring structured and clean input for recognition algorithms.

## Limitations
- This repository is part of a hardware-dependent system and **cannot be executed independently**.
- Requires specific hardware components (Beetle IMUs, Ultra96 FPGA) for full functionality.

## Purpose
This project demonstrates expertise in **real-time embedded systems, Bluetooth communication, networking protocols, multithreading, and AI-focused data processing**. The repository serves as a showcase of engineering skills in **IoT systems, low-latency data handling, and AI pipeline integration**.

## Technologies Used
- **Python** (multithreading, bluepy, socket, os, pandas, NumPy, matplotlib etc.)
- **Hardware** (DFRobot DFR0339 Bluno Beetle BLE Boards x6, IR Emitter & Sensor, Lilypad etc.)
- **Ultra96 FPGA** (Data Processing & AI Inference)
- **Logging & Debugging** (Bluetooth Communication Status, Packet Handling)

For more details or inquiries, feel free to reach out!

