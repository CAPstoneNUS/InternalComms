#include "IRremote.h"
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include "CRC8.h"


#define SYN_PACKET 'S'
#define KILL_PACKET 'J'
#define ACK_PACKET 'A'  // For handshaking
#define IMU_PACKET 'M'


CRC8 crc8;
Adafruit_MPU6050 mpu;

bool hasHandshake = false;
unsigned long previousIMUMillis = 0;    // Variable to store the last time readIMU() was executed
const unsigned long IMUinterval = 50;  // Interval in milliseconds (100 ms)

struct Packet {
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

void sendPacket(char packetType) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(Packet));
}

void sendIMUData() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  calibrateIMU(&a, &g);

  IMUPacket imuPacket;
  imuPacket.packetType = IMU_PACKET;
  imuPacket.accX = a.acceleration.x * 100;
  imuPacket.accY = a.acceleration.y * 100;
  imuPacket.accZ = a.acceleration.z * 100;
  imuPacket.gyrX = g.gyro.x * 100;
  imuPacket.gyrY = g.gyro.y * 100;
  imuPacket.gyrZ = g.gyro.z * 100;
  memset(imuPacket.padding, 0, sizeof(imuPacket.padding));

  crc8.restart();
  crc8.add((uint8_t *)&imuPacket, sizeof(IMUPacket) - sizeof(imuPacket.crc));
  imuPacket.crc = (uint8_t)crc8.calc();

  Serial.write((byte *)&imuPacket, sizeof(imuPacket));
}


void setup() {
  Serial.begin(115200);
  mpu_setup();
  hasHandshake = false;
}

void loop() {
  if (Serial.available() >= 20) {
    Packet packet;
    Serial.readBytes((byte *)&packet, sizeof(Packet));

    crc8.restart();
    crc8.add((uint8_t *)&packet, sizeof(packet) - sizeof(byte));
    uint8_t calculatedCRC = (uint8_t)crc8.calc();

    if (calculatedCRC == packet.crc) {
      switch (packet.packetType) {
        case SYN_PACKET:
          hasHandshake = false;
          sendPacket(ACK_PACKET);
          break;
        case ACK_PACKET:
          hasHandshake = true;
          break;
        case KILL_PACKET:
          asm volatile ("jmp 0");
          break; // idt needed also
        default:
          break;
      }
    }
  }

  unsigned long currentIMUMillis = millis();

  // Check if 50 ms has passed since the last time readIMU() was called
  if (hasHandshake && (currentIMUMillis - previousIMUMillis >= IMUinterval)) {
    previousIMUMillis = currentIMUMillis;  // Save the current time
    sendIMUData();                         // Execute the readIMU() function
  }
}

void mpu_setup() {
  if (!mpu.begin()) {
    while (1) {
      delay(10);
    }
  }
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_21_HZ);
  delay(100);
}

#define OFFSET_A_X 10.14
#define OFFSET_A_Y 2.00
#define OFFSET_A_Z 0.32

#define OFFSET_G_X 0.04
#define OFFSET_G_Y -0.01
#define OFFSET_G_Z -0.00

void calibrateIMU(sensors_event_t *a, sensors_event_t *g) {
  a->acceleration.x -= OFFSET_A_X;
  a->acceleration.y -= OFFSET_A_Y;
  a->acceleration.z -= OFFSET_A_Z;

  // Apply gyroscope offsets
  g->gyro.x -= OFFSET_G_X;
  g->gyro.y -= OFFSET_G_Y;
  g->gyro.z -= OFFSET_G_Z;
}
