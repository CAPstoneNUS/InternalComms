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
// #define NAK_PACKET 'L'
#define IMU_PACKET 'M'
#define GUN_PACKET 'G'
#define GUN_NAK_PACKET 'T'
#define GUN_ACK_PACKET 'X'     // For gunshot SYN-ACK from laptop
#define RELOAD_PACKET 'R'
#define RELOAD_ACK_PACKET 'Y'  // For reload ACK from laptop
#define STATE_PACKET 'D'
#define STATE_ACK_PACKET 'U'

CRC8 crc8;
Adafruit_MPU6050 mpu;

uint8_t currShot = 1;
uint8_t pendingCurrShot = 1;
const uint8_t magSize = 6;
uint8_t remainingBullets = magSize;
uint8_t pendingRemainingBullets = magSize;
std::set<uint8_t> unacknowledgedShots;
bool hasHandshake = false;
bool reloadInProgress = false;
bool stateUpdateInProgress = false;
unsigned long reloadStartTime = 0;
unsigned long stateUpdateStartTime = 0;
unsigned long lastGunShotTime = 0;
unsigned long responseTimeout = 1000; // 1s timeout

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
  uint8_t shotID; // AKA currShot
  uint8_t remainingBullets;
  byte padding[16];
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

Packet lastPacket;

void sendPacket(char packetType) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.shotID = pendingState.isPending ? pendingState.currShot : currShot; // AKA currShot sync variable for reconnections
  packet.remainingBullets = pendingState.isPending ? pendingState.remainingBullets : remainingBullets;
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
      // sync game state upon reconnection
      updatePendingState(packet.shotID, packet.remainingBullets);
      sendPacket(ACK_PACKET);
      break;
    case ACK_PACKET:
      applyPendingState();
      for (int i = 1; i <= currShot; i++) {
        updateLED(7-i);
      }
      hasHandshake = true;
      break;
    // case NAK_PACKET:
    //   Serial.write((byte *)&lastPacket, sizeof(lastPacket)); // resend last packet
    //   break;
    case GUN_NAK_PACKET:
      if (std::find(unacknowledgedShots.begin(), unacknowledgedShots.end(), packet.shotID) != unacknowledgedShots.end()) {
        sendPacketWithID(GUN_PACKET, packet.shotID);
      }
      // sendPacketWithID(GUN_PACKET, packet.shotID); // honestly not correct, need to follow the one above this but it hangs in a beetle-laptop packet loop
      break;
    case GUN_ACK_PACKET:
      applyPendingState();
      unacknowledgedShots.erase(packet.shotID);
      sendPacket(GUN_ACK_PACKET);
      currShot++;
      break;
    case RELOAD_PACKET: // recvs reload packet from laptop
      sendPacket(RELOAD_ACK_PACKET);
      reloadInProgress = true;
      reloadStartTime = millis();
      break;
    case RELOAD_ACK_PACKET:
      unacknowledgedShots.clear();
      reloadMag();
      reloadInProgress = false;
      reloadStartTime = 0;
      break;
    case STATE_PACKET:
      updatePendingState(packet.shotID, packet.remainingBullets);
      sendPacket(STATE_ACK_PACKET);
      stateUpdateStartTime = millis();
      break;
    case STATE_ACK_PACKET:
      applyPendingState();
      stateUpdateStartTime = 0;
  }
}


void setup() {
  Serial.begin(115200);
  IrSender.begin(IR_PIN);
  pixels.begin();
  initializePendingState();
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
      sendPacketWithID(GUN_PACKET, shotToResend);
      lastGunShotTime = currMillis;
    }
  }
}

void sendPacketWithID(char packetType, uint8_t shotID) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.shotID = shotID;
  packet.remainingBullets = pendingState.isPending ? pendingState.remainingBullets : remainingBullets;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  // Store packet
  lastPacket = packet;
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
          updatePendingState(currShot, --remainingBullets);
          sendPacket(GUN_PACKET);
          unacknowledgedShots.insert(pendingState.currShot);
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
  remainingBullets = magSize;
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
