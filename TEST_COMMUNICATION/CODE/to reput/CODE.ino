#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <DHT20.h>

// WIFI AP
const char* AP_SSID = "ESP32_BRACELET";
const char* AP_PASS = "12345678";

// PINS
const int SDA_PIN = 8;
const int SCL_PIN = 9;
const int LED_PIN = 2;
const int TOUCH_PIN = 5;    // TTP223 output
const int BUZZER_PIN = 4;   // buzzer (active) output
const int ADC_PIN =0;      // analog input

WebServer server(80);
DHT20 dht20;

float gTempC = NAN;
float gHumPct = NAN;
int gAdcRaw = 0;

unsigned long lastReadMs = 0;
const unsigned long READ_PERIOD_MS = 1000;

// ===== SENSOR =====
void readSensorsNonBlocking() {

  unsigned long now = millis();
  if (now - lastReadMs < READ_PERIOD_MS) return;
  lastReadMs = now;

  // DHT20
  int rc = dht20.read();

  if (rc == 0) {
    gTempC = dht20.getTemperature();
    gHumPct = dht20.getHumidity();
  } 
  else {
    gTempC = NAN;
    gHumPct = NAN;
  }

  // ADC read (raw)
  gAdcRaw = analogRead(ADC_PIN);
}

// ===== API =====
void handleApiState() {

  bool led = (digitalRead(LED_PIN) == HIGH);
  bool touch = (digitalRead(TOUCH_PIN) == HIGH);
  bool buzzer = (digitalRead(BUZZER_PIN) == HIGH);

  String json = "{";

  if (isnan(gTempC)) json += "\"tempC\":null,";
  else json += "\"tempC\":" + String(gTempC, 2) + ",";

  if (isnan(gHumPct)) json += "\"humPct\":null,";
  else json += "\"humPct\":" + String(gHumPct, 1) + ",";

  json += "\"adcRaw\":" + String(gAdcRaw) + ",";

  json += "\"led\":" + String(led ? "true" : "false") + ",";
  json += "\"touch\":" + String(touch ? "true" : "false") + ",";
  json += "\"buzzer\":" + String(buzzer ? "true" : "false");
  json += "}";

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "application/json", json);
}

void handleLed() {

  String st = server.arg("state");
  bool on = (st == "1" || st == "true" || st == "on");

  digitalWrite(LED_PIN, on ? HIGH : LOW);

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "OK");
}

void handleBuzzer() {

  String st = server.arg("state");
  bool on = (st == "1" || st == "true" || st == "on");

  digitalWrite(BUZZER_PIN, on ? HIGH : LOW);

  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.send(200, "text/plain", "OK");
}

void setup() {

  Serial.begin(115200);

  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);

  pinMode(BUZZER_PIN, OUTPUT);
  digitalWrite(BUZZER_PIN, LOW);

  pinMode(TOUCH_PIN, INPUT);
  pinMode(ADC_PIN, INPUT);

  // ADC config
  analogReadResolution(12); // 0-4095

  Wire.begin(SDA_PIN, SCL_PIN);
  dht20.begin();

  WiFi.mode(WIFI_AP);
  WiFi.softAP(AP_SSID, AP_PASS);

  server.on("/api/state", handleApiState);
  server.on("/led", handleLed);
  server.on("/buzzer", handleBuzzer);

  server.begin();
}

void loop() {

  server.handleClient();
  readSensorsNonBlocking();
}