#include <Arduino.h>
#include <IRremote.hpp> // include the library
#include <Adafruit_NeoPixel.h>
#include "CRC8.h"
#include "PinDefinitionsAndMore.h"

#define SYN_PACKET 'S'
#define ACK_PACKET 'A'
#define BOMB_PACKET 'B'
#define BOMB_ACK_PACKET 'C'
#define ACTION_PACKET 'W'
#define ACTION_ACK_PACKET 'E'
#define SHIELD_PACKET 'P'
#define SHIELD_ACK_PACKET 'Q'
#define VESTSHOT_PACKET 'V'
#define VESTSHOT_ACK_PACKET 'Z'

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


/*
 * This include defines the actual pin number for pins like IR_RECEIVE_PIN, IR_SEND_PIN for many different boards and architectures
 */


Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRB + NEO_KHZ800);

CRC8 crc8;

uint8_t shield = 0;
uint8_t health = MAX_HEALTH;
uint8_t deaths = 0;
bool hasHandshake = false;
int RED_ENCODING_VALUE = 0xFF6897;
int ACTION_ENCODING_VALUE = 0xFF9867; //TOD

struct PacketTimeout {
  char packetType;
  unsigned long lastSentTime;
  bool waiting;
};

PacketTimeout timeouts[] = {
  {BOMB_PACKET, 0, false},
  {ACTION_PACKET, 0, false},
  {SHIELD_PACKET, 0, false},
  {VESTSHOT_PACKET, 0, false}
};

struct Packet {
  char packetType;
  uint8_t shield;
  uint8_t health;
  uint8_t deaths;
  byte padding[15];
  uint8_t crc;
};


void applyDamage(uint8_t damage) {
  if (shield > 0) {
    if (shield >= damage) {
      shield -= damage;
    } else {
      damage -= shield;
      shield = 0;
      health = (health > damage) ? health - damage : 0;
    }
  } else {
    health = (health > damage) ? health - damage : 0;
  }
}

void sendPacket(char packetType) {
  // Prepare packet
  Packet packet;
  packet.packetType = packetType;
  packet.health = health;
  packet.shield = shield;
  packet.deaths = deaths;
  memset(packet.padding, 0, sizeof(packet.padding));

  crc8.restart();
  crc8.add((uint8_t *)&packet, sizeof(Packet) - sizeof(packet.crc));
  packet.crc = (uint8_t)crc8.calc();

  // Send packet
  Serial.write((byte *)&packet, sizeof(packet));

  // Set timeout for the sent packet
  for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
    if (timeouts[i].packetType == packetType) {
      timeouts[i].lastSentTime = millis();
      timeouts[i].waiting = true;
      break;
    }
  }
}

void handlePacket(Packet &packet) {
  switch (packet.packetType) {
    case SYN_PACKET:
      sendPacket(ACK_PACKET);
      break;
    case ACK_PACKET:
      hasHandshake = true;
      break;
    case BOMB_PACKET:
      applyDamage(5);
      sendPacket(BOMB_ACK_PACKET);
      break;
    case BOMB_ACK_PACKET:
      timeouts[0].waiting = false;
      break;
    case ACTION_PACKET:
      applyDamage(10);
      sendPacket(ACTION_ACK_PACKET);
      break;
    case ACTION_ACK_PACKET:
      timeouts[1].waiting = false;
      break;
    case SHIELD_PACKET:
      shield = 30; // set to 30 on cast, cannot stack
      sendPacket(SHIELD_ACK_PACKET);
      break;
    case SHIELD_ACK_PACKET:
      timeouts[2].waiting = false;
      break;
    case VESTSHOT_ACK_PACKET:
      timeouts[3].waiting = false;
      break;
  }
}

void updateGameState(Packet &packet) {
  health = packet.health;
  shield = packet.shield;
  deaths = packet.deaths;
}

void setup() {
    Serial.begin(115200);
    // while (!Serial); // Wait for Serial to become available. Is optimized away for some cores.

    // Just to know which program is running on my Arduino
    // Serial.println(F("START " __FILE__ " from " __DATE__ "\r\nUsing library version " VERSION_IRREMOTE));

    IrReceiver.begin(RECV_PIN, ENABLE_LED_FEEDBACK);

    // Start the receiver and if not 3. parameter specified, take LED_BUILTIN pin from the internal boards definition as default feedback LED
    pixels.begin();
    
    updateLED();
}

void loop(){
  // Laptop --> Vest Beetle
  if (Serial.available() >= sizeof(Packet)) {
    Packet receivedPacket;
    Serial.readBytes((byte *)&receivedPacket, sizeof(Packet));

    crc8.restart();
    crc8.add((uint8_t *)&receivedPacket, sizeof(Packet) - sizeof(receivedPacket.crc));
    uint8_t calculatedCRC = (uint8_t)crc8.calc();

    if (calculatedCRC == receivedPacket.crc) {
      handlePacket(receivedPacket);
      if (hasHandshake) {
        updateGameState(receivedPacket);
      }
    }
  }

  if (hasHandshake) {
    // Gun Beetle --> Vest Beetle
    if (IrReceiver.decode() && IrReceiver.decodedIRData.command == 0x16) {
      applyDamage(5); // global health and shield update
      updateLED(); // LED health strip update
      sendPacket(VESTSHOT_PACKET); // send packet to vest
      IrReceiver.resume();
    }

    // Handle timeouts
    for (int i = 0; i < sizeof(timeouts) / sizeof(timeouts[0]); i++) {
      if (timeouts[i].waiting && (millis() - timeouts[i].lastSentTime > TIMEOUT_MS)) {
        sendPacket(timeouts[i].packetType);
      }
    }
  }
}


void updateLED(){
  if (health <= 0){
    deaths++;
    health = 100;
  }
  int full_leds = health / 10; // Number of fully lit LEDs (each represents 10 HP)
  int remainder = health % 10; // Remainder HP (for partial brightness)

  // Turn on the appropriate number of LEDs
  for (int i = 0; i < NUMPIXELS; i++) {
    // Serial.println(i);
    if (i < full_leds) {
      // Serial.println(i);
      // Fully lit LED (Green color: RGB -> 0, 5, 0 for dimmed green)
      pixels.setPixelColor(i, pixels.Color(0, 5, 0));

    } else if (i == full_leds && remainder > 0) {
      // Partially lit LED for the remainder HP (Color: RGB -> 0, 1, 0 for dimmer green)
      pixels.setPixelColor(i, pixels.Color(0, 1, 0));
    } else {
      // Turn off the rest of the LEDs
      pixels.setPixelColor(i, pixels.Color(0, 0, 0));
    }
  }
  pixels.show();   
}