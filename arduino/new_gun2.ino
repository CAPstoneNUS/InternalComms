#include "IRremote.h"
#include <ArduinoSTL.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_NeoPixel.h>
#include <Wire.h>
#include "CRC8.h"

#define SYN_PACKET 'S'
#define KILL_PACKET 'K'
#define ACK_PACKET 'A'  // For handshaking
#define NAK_PACKET 'N'
#define IMU_PACKET 'M'
#define GUNSHOT_PACKET 'G'
#define RELOAD_PACKET 'R'
#define UPDATE_STATE_PACKET 'U'
#define GUNSTATE_ACK_PKT 'X'

CRC8 crc8;
Adafruit_MPU6050 mpu;

const uint8_t MAG_SIZE = 6;
const uint8_t PACKET_BUFFER_SIZE = 4;
const unsigned long IMU_INTERVAL = 50;
const unsigned long RESPONSE_TIMEOUT = 1000;

uint8_t calculatedCRC;
uint8_t currShot = 1;
uint8_t remainingBullets = MAG_SIZE;
bool hasHandshake = false;
uint8_t sqn = 0;
uint8_t expectedSeqNum = 0;
uint8_t currBufferIdx = 0;
unsigned long lastGunShotTime = 0;
bool waitingForGunACK = false;

// #define LED 3
#define IR_PIN 3
#define BUTTON 2
// #define LASER 4
#define LED_PIN 4
#define NUMPIXELS 6

Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRBW + NEO_KHZ800);

int RED_ENCODING_VALUE = 0xFF6897;     //TODO
int ACTION_ENCODING_VALUE = 0xFF9867;  //TOD

unsigned long buttonPressTime = 0;       // Time when the button is pressed
unsigned long longPressDuration = 2000;  // 2 seconds

bool isLaserOn = false;      // To track the state of the laser
bool buttonPressed = false;  // To track if the button was pressed

struct Packet {
  char packetType;
  uint8_t sqn;
  uint8_t shotID;  // AKA currShot, also doubles as seq num for NAK pkt
  uint8_t remainingBullets;
  byte padding[15];
  uint8_t crc;
};

// ---------------- Pending State Management ---------------- //

struct PendingState {
  uint8_t currShot;
  uint8_t remainingBullets;
  bool isPending;
} pendingState;

void initializePendingState() {
  pendingState.currShot = currShot;
  pendingState.remainingBullets = remainingBullets;
  pendingState.isPending = false;
}

void updatePendingState(uint8_t currShot, uint8_t remainingBullets) {
  pendingState.currShot = currShot;
  pendingState.remainingBullets = remainingBullets;
  pendingState.isPending = true;
}

void applyPendingState() {
  if (pendingState.isPending) {
    currShot = pendingState.currShot;
    remainingBullets = pendingState.remainingBullets;
    pendingState.isPending = false;
  }
}

// --------------------------------------------------------- //

Packet packets[PACKET_BUFFER_SIZE];

void sendPacket(char packetType) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.sqn = sqn;
  packet.shotID = pendingState.isPending ? pendingState.currShot : currShot;  // AKA currShot sync variable for reconnections
  packet.remainingBullets = pendingState.isPending ? pendingState.remainingBullets : remainingBullets;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  // Store packet if not ACK (don't track sequencing for HS packets)
  if (packetType != ACK_PACKET) {
    storePacket(packet);
  }
}

void storePacket(Packet packet) {
  packets[currBufferIdx] = packet;
  currBufferIdx = (currBufferIdx + 1) % PACKET_BUFFER_SIZE;  // circular buffer
}

Packet retreivePacket(uint8_t sqn) {
  int idx = sqn % PACKET_BUFFER_SIZE;
  return packets[idx];
}

void handlePacket(Packet &packet) {
  switch (packet.packetType) {
    case GUNSHOT_PACKET:
      if (packet.sqn == sqn) { // if we recv what we sent out
        waitingForGunACK = false;
        applyPendingState();
        lastGunShotTime = millis();
        currShot++;
        sqn++;
      } else {
        sendNAKPacket(sqn);
      }
      break;
    case RELOAD_PACKET:
      if (packet.sqn == expectedSeqNum) {
        reloadMag();
        sendPacket(RELOAD_PACKET);
        expectedSeqNum++;
      } else {
        sendNAKPacket(expectedSeqNum);
      }
      break;
    case UPDATE_STATE_PACKET:
      if (packet.sqn == expectedSeqNum) {
        updatePendingState(packet.shotID, packet.remainingBullets);
        sendPacket(GUNSTATE_ACK_PKT);
        applyPendingState();
        expectedSeqNum++;
      } else {
        sendNAKPacket(expectedSeqNum);
      }
      break;
    case NAK_PACKET:
      // packet.sqn refers to laptops expected seq num
      Serial.write((byte *)&(retreivePacket(packet.sqn)), sizeof(Packet));
      break;
    case KILL_PACKET:
      asm volatile("jmp 0");
      break;
    default:
      sendNAKPacket(expectedSeqNum);
      break;
  }
}


void setup() {
  Serial.begin(115200);
  IrSender.begin(IR_PIN);
  pixels.begin();
  initializePendingState();
  reloadMag();
  mpuSetup();
  hasHandshake = false;
  sqn = 0;
  expectedSeqNum = 0;
  currBufferIdx = 0;
}

int buttonState = 0;
unsigned long previousIMUMillis = 0;   // Variable to store the last time sendIMUData() was executed

void loop() {
  if (Serial.available() >= sizeof(Packet)) {
    Packet packet;
    Serial.readBytes((byte *)&packet, sizeof(Packet));

    crc8.restart();
    crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
    calculatedCRC = (uint8_t)crc8.calc();

    if (calculatedCRC == packet.crc) {
      if (!hasHandshake) {
        switch (packet.packetType) {
          case SYN_PACKET:
            sqn = 0;
            expectedSeqNum = 0;
            updatePendingState(packet.shotID, packet.remainingBullets);
            sendPacket(ACK_PACKET);
            break;
          case ACK_PACKET:
            applyPendingState();
            for (int i = 1; i <= currShot; i++) {  // pendingState applied to global currShot
              updateLED(7 - i);
            }
            hasHandshake = true;
            break;
        }
      } else { // has handshake
        handlePacket(packet);
      }
    }
  }

  if (hasHandshake) {
    unsigned long currMillis = millis();
    readButton(currMillis);

    // Send IMU data every 50ms
    if (currMillis - previousIMUMillis >= IMU_INTERVAL) {
      previousIMUMillis = currMillis;
      sendIMUData();
    }

    if (waitingForGunACK && (currMillis - lastGunShotTime) > RESPONSE_TIMEOUT) {
      sendPacket(GUNSHOT_PACKET);
      lastGunShotTime = currMillis;
    }
  }
}

void sendNAKPacket(uint8_t seqNum) {
  // Prepare packet
  Packet packet;
  packet.packetType = NAK_PACKET;
  packet.sqn = seqNum;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));
}

const unsigned long debounceDelay = 50;  // Debounce time in milliseconds
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
          updatePendingState(currShot, --remainingBullets);
          sendPacket(GUNSHOT_PACKET);
          waitingForGunACK = true;
          lastGunShotTime = currMillis;
          updateLED(pendingState.remainingBullets);
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
  remainingBullets = MAG_SIZE;
  for (int i = 0; i < remainingBullets; i++) {
    pixels.setPixelColor(i, pixels.Color(0, 10, 0, 0));
  }
  pixels.show();
}

void updateLED(int bulletToOff) {
  pixels.setPixelColor(bulletToOff, pixels.Color(0, 0, 0, 0));
  pixels.show();
}

#define OFFSET_A_X -9.20
#define OFFSET_A_Y 0.18
#define OFFSET_A_Z -1.76

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
