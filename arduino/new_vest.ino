#include <Arduino.h>
/*
 * Specify which protocol(s) should be used for decoding.
 * If no protocol is defined, all protocols (except Bang&Olufsen) are active.
 * This must be done before the #include <IRremote.hpp>
 */
#define DECODE_NEC          // Includes Apple and Onkyo. To enable all protocols , just comment/disable this line.
#define LED_PIN  4
#define NUMPIXELS 10
#define RECV_PIN 2



/*
 * This include defines the actual pin number for pins like IR_RECEIVE_PIN, IR_SEND_PIN for many different boards and architectures
 */
#include "PinDefinitionsAndMore.h"
#include <IRremote.hpp> // include the library
#include <Adafruit_NeoPixel.h>

Adafruit_NeoPixel pixels(NUMPIXELS, LED_PIN, NEO_GRB + NEO_KHZ800);

int max_hp = 100;
int curr_hp = max_hp;
int RED_ENCODING_VALUE = 0xFF6897;
int ACTION_ENCODING_VALUE = 0xFF9867; //TOD

void setup() {
    Serial.begin(115200);
    while (!Serial)
        ; // Wait for Serial to become available. Is optimized away for some cores.

    // Just to know which program is running on my Arduino
    // Serial.println(F("START " __FILE__ " from " __DATE__ "\r\nUsing library version " VERSION_IRREMOTE));
    IrReceiver.begin(RECV_PIN, ENABLE_LED_FEEDBACK);

    // Start the receiver and if not 3. parameter specified, take LED_BUILTIN pin from the internal boards definition as default feedback LED
    pixels.begin();
    
    updateLED(max_hp);
    // delay(5000000);
}

void loop() {
    /*
     * Check if received data is available and if yes, try to decode it.
     * Decoded result is in the IrReceiver.decodedIRData structure.
     *
     * E.g. command is in IrReceiver.decodedIRData.command
     * address is in command is in IrReceiver.decodedIRData.address
     * and up to 32 bit raw data in IrReceiver.decodedIRData.decodedRawData
     */
     if (IrReceiver.decode()) {
        if (IrReceiver.decodedIRData.protocol == UNKNOWN) {
            IrReceiver.resume(); 
        } else {
            IrReceiver.resume(); 
            IrReceiver.printIRResultShort(&Serial);
        }
        Serial.println(IrReceiver.decodedIRData.command);

        if (IrReceiver.decodedIRData.command == 0x16) {
          Serial.println("shot received");
          curr_hp -= 5;
          updateLED(curr_hp);                  
        }

        if (IrReceiver.decodedIRData.command == 0x19) {
          Serial.println("shot received");
          curr_hp -= 10;
          updateLED(curr_hp);                  
        }
      

       
     }
     
   
}


void updateLED(int hp){
  if (hp <= 0){
    curr_hp = 100;
    hp = 100;
  }
  int full_leds = hp / 10; // Number of fully lit LEDs (each represents 10 HP)
  int remainder = hp % 10; // Remainder HP (for partial brightness)

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