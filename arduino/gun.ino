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
#define GUN_PACKET 'G'
#define RELOAD_PACKET 'R'
#define GUN_ACK_PACKET 'X'     // For gunshot SYN-ACK from laptop
#define RELOAD_ACK_PACKET 'Y'  // For reload ACK from laptop

CRC8 crc8;
Adafruit_MPU6050 mpu;

uint8_t currShot = 1;
const uint8_t magSize = 6;
uint8_t shotsInMag = magSize;
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
  byte padding[17];
  uint8_t crc;
};

void sendPacket(char packetType, uint8_t shotID = 0) {
  Packet packet;
  packet.packetType = packetType;
  packet.shotID = shotID;
  memset(packet.padding, 0, sizeof(packet.padding));

  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  Serial.write((byte *)&packet, sizeof(packet));
}

void handlePacket(Packet &packet) {
  switch (packet.packetType) {
    case SYN_PACKET:
      sendPacket(ACK_PACKET);
      break;
    case ACK_PACKET:
      hasHandshake = true;
      break;
    case GUN_ACK_PACKET:
      unacknowledgedShots.erase(packet.shotID);
      sendPacket(GUN_ACK_PACKET, packet.shotID);
      break;
      // case RELOAD_PACKET: // recvs reload packet from laptop
      //   sendPacket(RELOAD_ACK_PACKET);
      //   reloadInProgress = true;
      //   reloadStartTime = millis();
      //   break;
      // case RELOAD_ACK_PACKET:
      //   if (!unacknowledgedShots.empty()) {
      //     for (const auto& shot : unacknowledgedShots) {
      //       sendPacket(GUN_PACKET, shot);
      //     }
      //   }
      //   unacknowledgedShots.clear();
      //   currShot = 1;
      //   shotsInMag = magSize;
      //   reloadInProgress = false;
      //   reloadStartTime = 0;
      //   break;
  }
}


void setup() {
  Serial.begin(115200);
  // Serial.println("Adafruit MPU6050 test!");
  // pinMode(BUTTON, INPUT);
  // pinMode(LASER, OUTPUT);
  IrSender.begin(IR_PIN);
  pixels.begin();
  reloadMag();
  mpu_setup();
}

int buttonState = 0;
int prevButtonState = 0;

unsigned long previousIMUMillis = 0;    // Variable to store the last time readIMU() was executed
const unsigned long IMUinterval = 100;  // Interval in milliseconds (100 ms)


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
    // Use same curr time in each loop
    unsigned long currMillis = millis();

    // Read the button state and trigger the laser
    readButton(currMillis);


    // Check if 100 ms has passed since the last time readIMU() was called
    if (currMillis - previousIMUMillis >= IMUinterval) {
      previousIMUMillis = currMillis;  // Save the current time
      readIMU();                       // Execute the readIMU() function
    }

    // Handle resending of unacknowledged shots
    if (!unacknowledgedShots.empty() && currMillis - lastGunShotTime >= responseTimeout) {
      uint8_t shotToResend = *unacknowledgedShots.begin();
      sendPacket(GUN_PACKET, shotToResend);
      lastGunShotTime = currMillis;
    }
  }
}

// Reads the button state and triggers the laser
// Appends the shot to the unacknowledgedShots set
void readButton(unsigned long currMillis) {
  buttonState = digitalRead(BUTTON);

  // Serial.println(buttonState);
  // digitalWrite(LASER, HIGH);

  // Button press detected (low to high transition)
  if (prevButtonState == LOW && buttonState == HIGH) {
    buttonPressTime = currMillis;  // Record the time the button is pressed
    buttonPressed = true;
  }

  // Button release detected (high to low transition)
  if (prevButtonState == HIGH && buttonState == LOW) {
    buttonPressed = false;

    // Check if it was a short press (less than 2 seconds) ==> GUN SHOT TRIGGERED
    if (currMillis - buttonPressTime < longPressDuration) {
      if (shotsInMag > 0) {
        IrSender.sendNEC(RED_ENCODING_VALUE, 32);
      }
      shotsInMag--;
      unacknowledgedShots.insert(currShot);
      lastGunShotTime = currMillis;
      currShot++;
      updateLED(shotsInMag);  // updates led strip and reloads mag if empty
      //   Serial.print("Shoot");
    }
  }

  // Check for a long press (button held down for at least 2 seconds)
  if (buttonPressed && (currMillis - buttonPressTime >= longPressDuration)) {
    IrSender.sendNEC(ACTION_ENCODING_VALUE, 32);
    // reloadMag();
    // Serial.print("Reloaded");

    // Prevent further toggling until button is released
    buttonPressed = false;
  }

  prevButtonState = buttonState;
  delay(50);
}

void mpu_setup() {
  if (!mpu.begin()) {
    // Serial.println("Failed to find MPU6050 chip");
    while (1) {
      delay(10);
    }
  }
  //   Serial.println("MPU6050 Found!");
  mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
  mpu.setGyroRange(MPU6050_RANGE_500_DEG);
  mpu.setFilterBandwidth(MPU6050_BAND_5_HZ);
  //   Serial.println("");
  delay(100);
}

void readIMU() {
  sensors_event_t a, g, temp;
  mpu.getEvent(&a, &g, &temp);
  calibrateIMU(&a, &g);

  //   /* Print out the values */
  //   Serial.print("Acceleration X: ");
  //   Serial.print(a.acceleration.x);
  //   Serial.print(", Y: ");
  //   Serial.print(a.acceleration.y);
  //   Serial.print(", Z: ");
  //   Serial.print(a.acceleration.z);
  //   Serial.println(" m/s^2");

  //   Serial.print("Rotation X: ");
  //   Serial.print(g.gyro.x);
  //   Serial.print(", Y: ");
  //   Serial.print(g.gyro.y);
  //   Serial.print(", Z: ");
  //   Serial.print(g.gyro.z);
  //   Serial.println(" rad/s");

  //   Serial.print("Temperature: ");
  //   Serial.print(temp.temperature);
  //   Serial.println(" degC");

  //   Serial.println("");
}

void reloadMag() {
  shotsInMag = magSize;
  for (int i = 0; i < shotsInMag; i++) {
    pixels.setPixelColor(i, pixels.Color(0, 1, 0));
  }
  pixels.show();
}

void updateLED(int shotsInMag) {
  if (shotsInMag <= 0) {
    delay(1000);
    reloadMag();
    return;
  }

  pixels.setPixelColor(shotsInMag, pixels.Color(0, 0, 0));
  pixels.show();
}


#define OFFSET_A_X -0.47
#define OFFSET_A_Y -0.28
#define OFFSET_A_Z 8.34

#define OFFSET_G_X -0.11
#define OFFSET_G_Y 0.03
#define OFFSET_G_Z 0.01

void calibrateIMU(sensors_event_t *a, sensors_event_t *g) {
  a->acceleration.x -= OFFSET_A_X;
  a->acceleration.y -= OFFSET_A_Y;
  a->acceleration.z -= OFFSET_A_Z;

  // Apply gyroscope offsets
  g->gyro.x -= OFFSET_G_X;
  g->gyro.y -= OFFSET_G_Y;
  g->gyro.z -= OFFSET_G_Z;
}
