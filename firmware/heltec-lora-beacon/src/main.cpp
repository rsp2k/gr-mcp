/*
 * Heltec V3 LoRa TX Beacon
 *
 * Transmits periodic LoRa packets for testing gr-mcp SDR reception.
 * Parameters match the gr-lora_sdr receiver defaults.
 *
 * Hardware: Heltec WiFi LoRa 32 V3 (ESP32-S3 + SX1262)
 */

#include <Arduino.h>
#include <SPI.h>
#include <RadioLib.h>

// Heltec V3 SX1262 pin mapping
#define LORA_NSS   8
#define LORA_DIO1  14
#define LORA_RST   12
#define LORA_BUSY  13
#define LORA_SCK   9
#define LORA_MOSI  10
#define LORA_MISO  11

// Heltec V3 power control — Vext must be LOW to power LoRa + OLED
#define VEXT_CTRL  36

// Custom SPI bus for the LoRa radio (not the default Arduino SPI pins)
SPIClass loraSPI(FSPI);

// SX1262 with custom SPI
SX1262 radio = new Module(LORA_NSS, LORA_DIO1, LORA_RST, LORA_BUSY, loraSPI);

// LoRa parameters — must match gr-lora_sdr receiver
const float FREQUENCY     = 915.0;   // MHz (US ISM band)
const float BANDWIDTH     = 125.0;   // kHz
const uint8_t SPREADING   = 7;       // SF7
const uint8_t CODING_RATE = 5;       // CR 4/5
const uint8_t SYNC_WORD   = 0x12;    // LoRaWAN public sync word
const int8_t TX_POWER     = 14;      // dBm
const uint16_t PREAMBLE_LEN = 8;     // symbols

uint32_t packet_count = 0;

void setup() {
    Serial.begin(115200);
    delay(2000);  // wait for USB CDC enumeration

    // Enable Vext to power the LoRa radio
    pinMode(VEXT_CTRL, OUTPUT);
    digitalWrite(VEXT_CTRL, LOW);
    delay(100);

    Serial.println("=== Heltec V3 LoRa TX Beacon ===");
    Serial.printf("Freq: %.1f MHz, SF%d, BW%.0fk, CR4/%d\n",
                  FREQUENCY, SPREADING, BANDWIDTH, CODING_RATE);
    Serial.printf("Sync: 0x%02X, Power: %d dBm\n", SYNC_WORD, TX_POWER);

    // Initialize custom SPI bus with Heltec V3 LoRa pins
    loraSPI.begin(LORA_SCK, LORA_MISO, LORA_MOSI, LORA_NSS);

    Serial.println("SPI initialized on SCK=9 MISO=11 MOSI=10 NSS=8");

    int state = radio.begin(
        FREQUENCY,
        BANDWIDTH,
        SPREADING,
        CODING_RATE,
        SYNC_WORD,
        TX_POWER,
        PREAMBLE_LEN
    );

    if (state != RADIOLIB_ERR_NONE) {
        Serial.printf("Radio init FAILED: %d\n", state);
        while (true) { delay(1000); }
    }

    // SX1262-specific: set DIO2 as RF switch control (required for Heltec V3)
    radio.setDio2AsRfSwitch(true);

    // Use explicit header mode (default for LoRa)
    radio.explicitHeader();

    // Enable CRC (gr-lora_sdr expects CRC)
    radio.setCRC(true);

    Serial.println("Radio initialized OK, starting TX loop");
}

void loop() {
    char payload[64];
    snprintf(payload, sizeof(payload), "GR-MCP #%lu", (unsigned long)packet_count);

    Serial.printf("[TX %lu] \"%s\" ... ", (unsigned long)packet_count, payload);

    int state = radio.transmit((uint8_t*)payload, strlen(payload));

    if (state == RADIOLIB_ERR_NONE) {
        Serial.printf("OK (%d dBm)\n", TX_POWER);
    } else {
        Serial.printf("FAIL: %d\n", state);
    }

    packet_count++;
    delay(3000);  // TX every 3 seconds
}
