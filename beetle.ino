#include "CRC8.h"

#define SYN_PACKET 'S'
#define ACK_PACKET 'A'
#define IMU_PACKET 'M'

CRC8 crc8;

bool hasHandshake = false;
unsigned long lastSendTime = 0;
const unsigned long sendInterval = 50;

struct ACKPacket {
    char packetType;
    byte padding[18];
    uint8_t crc;
};

struct IMUPacket {
    char packetType;
    int16_t accX;
    int16_t accY;
    int16_t accZ;
    int16_t gyrX;
    int16_t gyrY;
    int16_t gyrZ;
    byte padding[6];
    uint8_t crc;
};

void sendACKPacket() {
    ACKPacket ackPacket;
    ackPacket.packetType = ACK_PACKET;
    memset(ackPacket.padding, 0, sizeof(ackPacket.padding));
    
    crc8.restart();
    crc8.add((uint8_t*)&ackPacket, sizeof(ACKPacket) - sizeof(ackPacket.crc));
    ackPacket.crc = (uint8_t)crc8.calc();

    Serial.write((byte *)&ackPacket, sizeof(ackPacket));
}

void sendIMUPacket() {
    IMUPacket imuPacket;
    imuPacket.packetType = IMU_PACKET;
    imuPacket.accX = random(999);
    imuPacket.accY = random(999);
    imuPacket.accZ = random(999);
    imuPacket.gyrX = random(999);
    imuPacket.gyrY = random(999);
    imuPacket.gyrZ = random(999);
    memset(imuPacket.padding, 0, sizeof(imuPacket.padding));

    crc8.restart();
    crc8.add((uint8_t*)&imuPacket, sizeof(IMUPacket) - sizeof(imuPacket.crc));
    imuPacket.crc = (uint8_t)crc8.calc();
    
    Serial.write((byte *)&imuPacket, sizeof(imuPacket));
}

void setup() {
    Serial.begin(115200);
    hasHandshake = false;
}

void loop() {
    if (Serial.available() >= 20) {
        byte receivedPacket[20];
        Serial.readBytes(receivedPacket, 20);
        char packetType = receivedPacket[0];

        crc8.restart();
        crc8.add((uint8_t*)&receivedPacket, sizeof(receivedPacket) - sizeof(byte));
        uint8_t calculatedCRC = (uint8_t)crc8.calc();
        uint8_t trueCRC = receivedPacket[19];

      if ((packetType == SYN_PACKET) && (calculatedCRC == trueCRC)) {
          sendACKPacket();
      } else if ((packetType == ACK_PACKET) && (calculatedCRC == trueCRC)) {
          hasHandshake = true;
          lastSendTime = millis();
      }          
    }

    if (hasHandshake && (millis() - lastSendTime >= sendInterval)) {
        sendIMUPacket();
        lastSendTime = millis(); // Reset the timer
    }
}
