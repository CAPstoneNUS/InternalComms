#include <Arduino.h>
#include <IRremote.hpp> // include the library
#include <Adafruit_NeoPixel.h>
#include "CRC8.h"
#include "PinDefinitionsAndMore.h"

#define SYN_PACKET 'S'
#define KILL_PACKET 'J'
#define ACK_PACKET 'A'
// #define NAK_PACKET 'L'
#define VESTSHOT_PACKET 'V'
#define VESTSHOT_ACK_PACKET 'Z'
#define STATE_PACKET 'D'
#define STATE_ACK_PACKET 'W'

#define MAX_SHIELD 30
#define MAX_HEALTH 100
#define TIMEOUT_MS 1000 // 1 second timeout
/*
 * Specify which protocol(s) should be used for decoding.
 * If no protocol is defined, all protocols (except Bang&Olufsen) are active.
 * This must be done before the #include <IRremote.hpp>
 */
// #define DECODE_NEC          // Includes Apple and Onkyo. To enable all protocols , just comment/disable this line.
#define LED_PIN  4
#define NUMPIXELS 10
#define RECV_PIN 2

Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRBW + NEO_KHZ800);

CRC8 crc8;

uint8_t shield = 0;
uint8_t health = MAX_HEALTH;

bool hasHandshake = false;
int RED_ENCODING_VALUE = 0xFF6897;
int ATTACK_ENCODING_VALUE = 0xFF9867; //TOD

// struct PacketTimeout {
//   char packetType;
//   unsigned long lastSentTime;
//   bool waiting;
// };

// PacketTimeout timeouts[] = {
//   {STATE_PACKET, 0, false},
//   {VESTSHOT_PACKET, 0, false}
// };

struct Packet {
  char packetType;
  uint8_t shield;
  uint8_t health;
  byte padding[16];
  uint8_t crc;
};

Packet lastPacket;

// ---------------- Pending State Management ---------------- //

struct PendingState {
  uint8_t shield;
  uint8_t health;
  bool isPending;
} pendingState;

void initializePendingState() {
  pendingState.shield = shield;
  pendingState.health = health;
  pendingState.isPending = false;
}

void updatePendingState(uint8_t shield, uint8_t health) {
  pendingState.shield = shield;
  pendingState.health = health;
  pendingState.isPending = true;
}

void applyPendingState() {
  if (pendingState.isPending) {
    shield = pendingState.shield;
    health = pendingState.health;
    pendingState.isPending = false;
    updateLED();
  }
}

// --------------------------------------------------------- //

void sendPacket(char packetType) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.shield = pendingState.isPending ? pendingState.shield : shield;
  packet.health = pendingState.isPending ? pendingState.health : health;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  // Store last packet
  lastPacket = packet;

  // // Set timeout for the sent packet
  // for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
  //   if (timeouts[i].packetType == packetType) {
  //     timeouts[i].lastSentTime = millis();
  //     timeouts[i].waiting = true;
  //     break;
  //   }
  // }
}

void applyDamageToPendingState(uint8_t damage) {
  if (pendingState.shield >= damage) {
    pendingState.shield -= damage;
  } else {
    uint8_t remainingDamage = damage - pendingState.shield;
    pendingState.shield = 0;
    if (pendingState.health > remainingDamage) {
      pendingState.health -= remainingDamage;
    } else {
      pendingState.shield = 0;
      pendingState.health = 100;
    }
  }
  pendingState.isPending = true;
}

void handlePacket(Packet &packet) {
  switch (packet.packetType) {
    case SYN_PACKET:
      // Sync game state upon reconnection
      updatePendingState(packet.shield, packet.health);
      sendPacket(ACK_PACKET);
      break;
    case ACK_PACKET:
      applyPendingState();
      hasHandshake = true;
      break;
    // case NAK_PACKET:
    //   Serial.write((byte *)&lastPacket, sizeof(lastPacket)); // resend last packet
    //   break;
    case STATE_PACKET:
      updatePendingState(packet.shield, packet.health);
      sendPacket(STATE_ACK_PACKET);
      break;
    case STATE_ACK_PACKET:
      applyPendingState();
      break;
    case VESTSHOT_ACK_PACKET:
      applyPendingState();
      // timeouts[1].waiting = false;
      sendPacket(VESTSHOT_ACK_PACKET);
      break;
    case KILL_PACKET:
      asm volatile ("jmp 0");
  }
}

void setup() {
  Serial.begin(115200);
  IrReceiver.begin(RECV_PIN, ENABLE_LED_FEEDBACK);
  initializePendingState();
  pixels.begin();
  updateLED();
  hasHandshake = false;
}

void loop(){
  // Laptop --> Vest
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
    // Gun --> Vest
    if (IrReceiver.decode() && IrReceiver.decodedIRData.command == 0x16) {
      applyDamageToPendingState(5);
      sendPacket(VESTSHOT_PACKET);
      IrReceiver.resume();
    }

    // // Handle timeouts
    // for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
    //   if (timeouts[i].waiting && (millis() - timeouts[i].lastSentTime > TIMEOUT_MS)) {
    //     sendPacket(timeouts[i].packetType);
    //   }
    // }
  }
}

void updateLED(){
  int full_leds = health / 10; // Number of fully lit LEDs (each represents 10 HP)
  int remainder = health % 10; // Remainder HP (for partial brightness)

  // Turn on the appropriate number of LEDs
  for (int i = 0; i < NUMPIXELS; i++) {
    if (i < full_leds) {
      // Fully lit LED (Green color: RGB -> 0, 5, 0 for dimmed green)
      pixels.setPixelColor(i, pixels.Color(0, 10, 0, 0));

    } else if (i == full_leds && remainder > 0) {
      // Partially lit LED for the remainder HP (Color: RGB -> 0, 1, 0 for dimmer green)
      pixels.setPixelColor(i, pixels.Color(0, 1, 0, 0));
    } else {
      // Turn off the rest of the LEDs
      pixels.setPixelColor(i, pixels.Color(0, 0, 0, 0));
    }
  }
  pixels.show();   
}