#include <ArduinoSTL.h>
#include <set>
#include "CRC8.h"

#define SYN_PACKET 'S'
#define ACK_PACKET 'A'  // For handshaking
#define GUN_PACKET 'G'
#define RELOAD_PACKET 'R'
#define GUN_ACK_PACKET 'X'     // For gunshot SYN-ACK from laptop
#define RELOAD_ACK_PACKET 'Y'  // For reload ACK from laptop

CRC8 crc8;
uint8_t currShot = 1;
std::set<uint8_t> unacknowledgedShots;
bool hasHandshake = false;
bool reloadInProgress = false;
unsigned long reloadStartTime = 0;
unsigned long lastGunShotTime = 0;
unsigned long responseTimeout = 1000;
unsigned long gunInterval = random(10000);  // Shots at random
const uint8_t magSize = 6;
uint8_t shotsInMag = magSize;

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

void setup() {
  Serial.begin(115200);
}

void loop() {
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
    if (!reloadInProgress) {
      sendGunShot();
    }
    handleReloadTimeout();
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
    case GUN_ACK_PACKET:
      unacknowledgedShots.erase(packet.shotID);
      sendPacket(GUN_ACK_PACKET, packet.shotID);
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
      currShot = 1;
      shotsInMag = magSize;
      reloadInProgress = false;
      reloadStartTime = 0;
      break;
  }
}

void sendGunShot() {
  unsigned long currentTime = millis();

  // Check if it's time to fire a new shot
  if (currentTime - lastGunShotTime >= gunInterval && shotsInMag > 0) {
    unacknowledgedShots.insert(currShot);
    sendPacket(GUN_PACKET, currShot);
    lastGunShotTime = currentTime;
    currShot++;
    shotsInMag--;
    gunInterval = random(10000);
  }

  // Handle resending of unacknowledged shots
  if (!unacknowledgedShots.empty() && currentTime - lastGunShotTime >= responseTimeout) {
    uint8_t shotToResend = *unacknowledgedShots.begin();
    sendPacket(GUN_PACKET, shotToResend);
    lastGunShotTime = currentTime;
  }
}

// Reload timeout occurred, resend RELOAD ACK
void handleReloadTimeout() {
  if (reloadInProgress && millis() - reloadStartTime >= responseTimeout) {
    sendPacket(RELOAD_ACK_PACKET);
    reloadStartTime = millis();
  }
}