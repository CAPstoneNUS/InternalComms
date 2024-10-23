#include <Arduino.h>
#include <IRremote.hpp> // include the library
#include <Adafruit_NeoPixel.h>
#include "CRC8.h"
#include "PinDefinitionsAndMore.h"

#define SYN_PACKET 'S'
#define KILL_PACKET 'K'
#define ACK_PACKET 'A'
#define NAK_PACKET 'N'
#define VESTSHOT_PACKET 'V'
#define UPDATE_STATE_PACKET 'U'
#define VESTSTATE_ACK_PKT 'W'

#define LED_PIN  4
#define NUMPIXELS 10
#define RECV_PIN 2

Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRBW + NEO_KHZ800);

CRC8 crc8;

const uint8_t MAX_SHIELD = 30;
const uint8_t MAX_HEALTH = 100;
const uint8_t PACKET_BUFFER_SIZE = 4;
const unsigned long RESPONSE_TIMEOUT = 1000;

uint8_t shield = 0;
uint8_t health = MAX_HEALTH;
bool hasHandshake = false;
uint8_t sqn = 0;
uint8_t expectedSeqNum = 0;
uint8_t currBufferIdx = 0;

int RED_ENCODING_VALUE = 0xFF6897;
int ATTACK_ENCODING_VALUE = 0xFF9867; //TOD

// struct PacketTimeout {
//   char packetType;
//   unsigned long lastSentTime;
//   bool waiting;
// };

// PacketTimeout timeouts[] = {
//   {UPDATE_STATE_PACKET, 0, false},
//   {VESTSHOT_PACKET, 0, false}
// };

struct Packet {
  char packetType;
  uint8_t sqn;
  uint8_t shield;
  uint8_t health;
  byte padding[15];
  uint8_t crc;
};

Packet packets[PACKET_BUFFER_SIZE];

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
  packet.sqn = sqn;
  packet.shield = pendingState.isPending ? pendingState.shield : shield;
  packet.health = pendingState.isPending ? pendingState.health : health;
  memset(packet.padding, 0, sizeof(packet.padding));
  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  if (packetType != ACK_PACKET) {
    storePacket(packet);
  }

  // // Set timeout for the sent packet
  // for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
  //   if (timeouts[i].packetType == packetType) {
  //     timeouts[i].lastSentTime = millis();
  //     timeouts[i].waiting = true;
  //     break;
  //   }
  // }
}

void storePacket(Packet packet) {
  packets[currBufferIdx] = packet;
  currBufferIdx = (currBufferIdx + 1) % PACKET_BUFFER_SIZE;  // circular buffer
}

Packet retreivePacket(uint8_t sqn) {
  int idx = sqn % PACKET_BUFFER_SIZE;
  return packets[idx];
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
    case VESTSHOT_PACKET:
      if (packet.sqn == sqn) { // if we recv the sqn we sent...
        applyPendingState();
        sqn++;
      } else {
        sendNAKPacket(sqn);
      }
      break;
    case UPDATE_STATE_PACKET:
      if (packet.sqn == expectedSeqNum) {
        updatePendingState(packet.shield, packet.health);
        sendPacket(VESTSTATE_ACK_PKT);
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
      asm volatile ("jmp 0");
      break;
    default:
      sendNAKPacket(expectedSeqNum);
      break;
  }
}

void setup() {
  Serial.begin(115200);
  IrReceiver.begin(RECV_PIN, ENABLE_LED_FEEDBACK);
  initializePendingState();
  pixels.begin();
  updateLED();
  hasHandshake = false;
  sqn = 0;
  expectedSeqNum = 0;
  currBufferIdx = 0;
}

void loop(){
  if (Serial.available() >= sizeof(Packet)) {
    Packet packet;
    Serial.readBytes((byte *)&packet, sizeof(Packet));

    crc8.restart();
    crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
    uint8_t calculatedCRC = (uint8_t)crc8.calc();

    if (calculatedCRC == packet.crc) {
      if (!hasHandshake) {
        switch (packet.packetType) {
          case SYN_PACKET:
            sqn = 0;
            expectedSeqNum = 0;
            updatePendingState(packet.shield, packet.health);
            sendPacket(ACK_PACKET);
            break;
          case ACK_PACKET:
            applyPendingState();
            hasHandshake = true;
            break;
        }
      } else { // has handshake
        handlePacket(packet);
      }
    }
  }

  if (hasHandshake) {
    // Gun (IR Emit) --> Vest (IR Recv)
    if (IrReceiver.decode() && IrReceiver.decodedIRData.command == 0x16) {
      applyDamageToPendingState(5);
      sendPacket(VESTSHOT_PACKET);
      IrReceiver.resume();
    }

    // // Handle timeouts
    // for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
    //   if (timeouts[i].waiting && (millis() - timeouts[i].lastSentTime > RESPONSE_TIMEOUT)) {
    //     sendPacket(timeouts[i].packetType);
    //   }
    // }
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