#include "CRC8.h"

byte dataPacket[15];
bool hasHandshake = false;

CRC8 crc;

#define SYN_PACKET 'S'
#define ACK_PACKET 'A'
#define IMU_PACKET 'M'

unsigned long lastSendTime = 0;
const unsigned long sendInterval = 500;

struct IMUPacket {
    byte packetType;
    int16_t accX;
    int16_t accY;
    int16_t accZ;
    int16_t gyrX;
    int16_t gyrY;
    int16_t gyrZ;
    byte checksum;
    byte EOP;
};

void sendIMUPacket() {
    IMUPacket imuPacket;
    imuPacket.packetType = IMU_PACKET;
    imuPacket.accX = random(999);
    imuPacket.accY = random(999);
    imuPacket.accZ = random(999);
    imuPacket.gyrX = random(999);
    imuPacket.gyrY = random(999);
    imuPacket.gyrZ = random(999);

    crc.restart();
    crc.add((uint8_t*)&imuPacket, sizeof(IMUPacket) - 2);
    imuPacket.checksum = crc.calc();
    imuPacket.EOP = '!';

    Serial.write((byte *)&imuPacket, sizeof(imuPacket));
}

void setup() {
    Serial.begin(115200);
    hasHandshake = false;
}

void loop() {
    if (Serial.available()) {
        char serialRead = Serial.read();
        if (serialRead == SYN_PACKET) {
            Serial.write(ACK_PACKET);
        } else if (serialRead == ACK_PACKET) {
            hasHandshake = true;
            lastSendTime = millis();
        }
    }

    if (hasHandshake && (millis() - lastSendTime >= sendInterval)) {
        sendIMUPacket();
        lastSendTime = millis(); // Reset the timer
    }
}
