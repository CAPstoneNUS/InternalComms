#include "IRremote.h"
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include "CRC8.h"


#define SYN_PACKET 'S'
#define ACK_PACKET 'A'  // For handshaking
#define IMU_PACKET 'M'


CRC8 crc8;
Adafruit_MPU6050 mpu;

bool hasHandshake = false;
unsigned long previousIMUMillis = 0;    // Variable to store the last time readIMU() was executed
const unsigned long IMUinterval = 100;  // Interval in milliseconds (100 ms)

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
  crc8.add((uint8_t *)&ackPacket, sizeof(ACKPacket) - sizeof(ackPacket.crc));
  ackPacket.crc = (uint8_t)crc8.calc();

  Serial.write((byte *)&ackPacket, sizeof(ackPacket));
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
}

void loop() {
  if (Serial.available() >= 20) {
    byte receivedPacket[20];
    Serial.readBytes(receivedPacket, 20);
    char packetType = receivedPacket[0];

    crc8.restart();
    crc8.add((uint8_t *)&receivedPacket, sizeof(receivedPacket) - sizeof(byte));
    uint8_t calculatedCRC = (uint8_t)crc8.calc();
    uint8_t trueCRC = receivedPacket[19];

    if (calculatedCRC == trueCRC) {
      if (packetType == SYN_PACKET) {
        sendACKPacket();
      } else if (packetType == ACK_PACKET) {
        hasHandshake = true;
      }
    }
  }

  unsigned long currentIMUMillis = millis();

  // Check if 100 ms has passed since the last time readIMU() was called
  if (hasHandshake && (currentIMUMillis - previousIMUMillis >= IMUinterval)) {
    previousIMUMillis = currentIMUMillis;  // Save the current time
    sendIMUData();                         // Execute the readIMU() function
  }
}

void mpu_setup() {
  if (!mpu.begin()) {
    // Serial.println("Failed to find MPU6050 chip");
    while (1) {
      delay(10);
    }
  }
  // Serial.println("MPU6050 Found!");
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_5_HZ);
  Serial.println("");
  delay(100);
}


#define OFFSET_A_X -0.27
#define OFFSET_A_Y -0.38
#define OFFSET_A_Z 9.86

#define OFFSET_G_X -0.04
#define OFFSET_G_Y -0.03
#define OFFSET_G_Z 0.02

void calibrateIMU(sensors_event_t *a, sensors_event_t *g) {
  a->acceleration.x -= OFFSET_A_X;
  a->acceleration.y -= OFFSET_A_Y;
  a->acceleration.z -= OFFSET_A_Z;

  // Apply gyroscope offsets
  g->gyro.x -= OFFSET_G_X;
  g->gyro.y -= OFFSET_G_Y;
  g->gyro.z -= OFFSET_G_Z;
}
