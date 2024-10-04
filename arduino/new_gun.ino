#include "IRremote.h"
#include <ArduinoSTL.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include <set>
#include "CRC8.h"

#define SYN_PACKET 'S'
#define ACK_PACKET 'A'  // For handshaking
#define NAK_PACKET 'L'
#define IMU_PACKET 'M'
#define GUN_PACKET 'G'
#define RELOAD_PACKET 'R'
#define GUN_ACK_PACKET 'X'     // For gunshot SYN-ACK from laptop
#define RELOAD_ACK_PACKET 'Y'  // For reload ACK from laptop

CRC8 crc8;
Adafruit_MPU6050 mpu;

uint8_t currShot = 1;
const uint8_t magSize = 6;
uint8_t remainingBullets = magSize;
std::set<uint8_t> unacknowledgedShots;
bool hasHandshake = false;
bool reloadInProgress = false;
unsigned long reloadStartTime = 0;
unsigned long lastGunShotTime = 0;
unsigned long responseTimeout = 1000;

// #define LED 3
#define IR_PIN 3
#define BUTTON 2
// #define LASER 4
#define LED_PIN 4
#define NUMPIXELS 6

Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRB + NEO_KHZ800);

int RED_ENCODING_VALUE = 0xFF6897;     //TODO
int ACTION_ENCODING_VALUE = 0xFF9867;  //TOD

unsigned long buttonPressTime = 0;       // Time when the button is pressed
unsigned long longPressDuration = 2000;  // 2 seconds

bool isLaserOn = false;      // To track the state of the laser
bool buttonPressed = false;  // To track if the button was pressed

struct Packet {
  char packetType;
  uint8_t shotID;
  uint8_t remainingBullets;
  byte padding[16];
  uint8_t crc;
};

Packet lastPacket;

void sendPacket(char packetType, uint8_t shotID = 0, uint8_t remainingBullets = 0) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.shotID = shotID;
  packet.remainingBullets = remainingBullets;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  // Store packet
  lastPacket = packet;
}

void handlePacket(Packet &packet) {
  switch (packet.packetType) {
    case SYN_PACKET:
      sendPacket(ACK_PACKET);
      break;
    case ACK_PACKET:
      hasHandshake = true;
      break;
    case NAK_PACKET:
      Serial.write((byte *)&lastPacket, sizeof(lastPacket)); // resend last packet
      break;
    case GUN_ACK_PACKET:
      unacknowledgedShots.erase(packet.shotID);
      sendPacket(GUN_ACK_PACKET, packet.shotID, remainingBullets);
      break;
      case RELOAD_PACKET: // recvs reload packet from laptop
        sendPacket(RELOAD_ACK_PACKET);
        reloadInProgress = true;
        reloadStartTime = millis();
        break;
      case RELOAD_ACK_PACKET:
        if (!unacknowledgedShots.empty()) {
          for (const auto& shot : unacknowledgedShots) {
            sendPacket(GUN_PACKET, shot);
          }
        }
        unacknowledgedShots.clear();
        reloadMag();
        reloadInProgress = false;
        reloadStartTime = 0;
        break;
  }
}


void setup() {
  Serial.begin(115200);
  // pinMode(BUTTON, INPUT);
  // pinMode(LASER, OUTPUT);
  IrSender.begin(IR_PIN);
  pixels.begin();
  reloadMag();
  mpuSetup();
}

int buttonState = 0;
unsigned long previousIMUMillis = 0;    // Variable to store the last time sendIMUData() was executed
const unsigned long IMUInterval = 50;  // Interval in milliseconds (50 ms)


void loop() {
  // Check if a packet has been received on the serial port
  if (Serial.available() >= sizeof(Packet)) {
    Packet receivedPacket;
    Serial.readBytes((byte *)&receivedPacket, sizeof(Packet));

    crc8.restart();
    crc8.add((uint8_t *)&receivedPacket, sizeof(Packet) - sizeof(receivedPacket.crc));
    uint8_t calculatedCRC = (uint8_t)crc8.calc();

    if (calculatedCRC == receivedPacket.crc) {
      handlePacket(receivedPacket);
    }
  }

  if (hasHandshake) {
    unsigned long currMillis = millis();
    readButton(currMillis);

    // Check if 100 ms has passed since the last time sendIMUData() was called
    if (currMillis - previousIMUMillis >= IMUInterval) {
      previousIMUMillis = currMillis;  // Save the current time
      sendIMUData();
    }

    // Handle resending of unacknowledged shots
    if (!unacknowledgedShots.empty() && currMillis - lastGunShotTime >= responseTimeout) {
      uint8_t shotToResend = *unacknowledgedShots.begin();
      sendPacket(GUN_PACKET, shotToResend, remainingBullets);
      lastGunShotTime = currMillis;
    }
  }
}


const unsigned long debounceDelay = 50; // Debounce time in milliseconds
unsigned long lastDebounceTime = 0;
int lastButtonState = HIGH;

void readButton(unsigned long currMillis) {
  int reading = digitalRead(BUTTON);

  // If the button state has changed, reset the debounce timer
  if (reading != lastButtonState) {
    lastDebounceTime = currMillis;
  }

  // Check if enough time has passed since the last state change
  if ((currMillis - lastDebounceTime) > debounceDelay) {
    // If the button state has changed:
    if (reading != buttonState) {
      buttonState = reading;

      // Button press detected (low to high transition)
      if (buttonState == HIGH) {
        if (remainingBullets > 0) {
          IrSender.sendNEC(RED_ENCODING_VALUE, 32);
          remainingBullets--;
          sendPacket(GUN_PACKET, currShot, remainingBullets);
          unacknowledgedShots.insert(currShot);
          lastGunShotTime = currMillis;
          currShot++;
          updateLED(remainingBullets);  // updates led strip and reloads mag if empty
        }
      }
    }
  }

  lastButtonState = reading;
}


void mpuSetup() {
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

void reloadMag() {
  currShot = 1;
  remainingBullets = magSize;
  for (int i = 0; i < remainingBullets; i++) {
    pixels.setPixelColor(i, pixels.Color(0, 1, 0));
  }
  pixels.show();
}

void updateLED(int remainingBullets) {
  pixels.setPixelColor(remainingBullets, pixels.Color(0, 0, 0));
  pixels.show();
}


#define OFFSET_A_X -9.05
#define OFFSET_A_Y 1.47
#define OFFSET_A_Z -1.17

#define OFFSET_G_X -0.11
#define OFFSET_G_Y 0.03
#define OFFSET_G_Z 0.01

void calibrateIMU(sensors_event_t *a, sensors_event_t *g) {
  // Apply accelerometer offsets
  a->acceleration.x -= OFFSET_A_X;
  a->acceleration.y -= OFFSET_A_Y;
  a->acceleration.z -= OFFSET_A_Z;

  // Apply gyroscope offsets
  g->gyro.x -= OFFSET_G_X;
  g->gyro.y -= OFFSET_G_Y;
  g->gyro.z -= OFFSET_G_Z;
}
