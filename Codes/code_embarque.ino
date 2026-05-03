/*
  ce code fait tourner un esp32 en point d acces wifi avec lecture capteurs et api web
  il genere aussi un signal pwm pseudo sinus, lit deux adc de facon synchrone et renvoie les donnees en json

  table des matieres version 3.2
  #1 bibliotheques
  #2 wifi
  #3 broches
  #4 objets
  #5 touch
  #6 dht20
  #7 signal synchrone
  #8 variables globales signal
  #9 variables globales acquisition
  #10 petits outils
  #11 interruption touch
  #12 lecture capteurs
  #13 table pwm
  #14 config adc et broches
  #15 acquisition synchrone
  #16 web api
  #17 setup et loop
*/

#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <DHT20.h>
#include <math.h>

// #1 bibliotheques
// --------------------

// #2 wifi
// --------------------
const char* AP_SSID = "ESP32_presentation";   // nom du wifi cree par l esp32
const char* AP_PASS = "12345678";             // mot de passe du wifi

// #3 broches
// --------------------
static const int SDA_PIN        = 8;          // ligne sda du i2c
static const int SCL_PIN        = 9;          // ligne scl du i2c
static const int LED_ROUGE_PIN  = 2;          // sortie led rouge
static const int LED_JAUNE_PIN  = 7;          // sortie led jaune
static const int TOUCH_PIN      = 5;          // entree touch ou bouton
static const int BUZZER_PIN     = 0;          // sortie buzzer

static const int PWM_PIN        = 3;          // sortie pwm qui cree le signal
static const int ADC1_PIN       = 1;          // adc entree signal
static const int ADC2_PIN       = 4;          // adc sortie signal

// #4 objets
// --------------------
WebServer server(80);                         // serveur web sur port 80
DHT20 dht20;                                 // objet capteur temperature humidite

// #5 touch
// --------------------
volatile bool gTouchState = false;           // etat instantane du touch
volatile unsigned long gTouchCount = 0;      // nombre d activations
volatile unsigned long gLastTouchIrqUs = 0;  // dernier temps d interruption touch
const unsigned long TOUCH_DEBOUNCE_US = 80000; // filtre anti rebond en microsecondes

// #6 dht20
// --------------------
float gTempC = NAN;                          // temperature stockee
float gHumPct = NAN;                         // humidite stockee
unsigned long lastReadMs = 0;                // dernier moment de lecture capteur
const unsigned long READ_PERIOD_MS = 1000;   // delai mini entre lectures

// #7 signal synchrone
// --------------------
static const float F_SIG_HZ      = 200.0f;   // frequence visee du signal pseudo sinus
static const int   LUT_SIZE      = 24;       // nombre de points dans une periode
static const int   AVG_CYCLES    = 40;       // nombre de cycles pour lisser la mesure

static const uint32_t PWM_FREQ_HZ = 10000;   // frequence rapide du pwm
static const uint8_t  PWM_BITS    = 10;      // resolution pwm 0 a 1023
static const float    DUTY_OFFSET = 0.60f;   // centre du sinus en duty cycle
static const float    DUTY_AMPL   = 0.34f;   // amplitude du sinus autour du centre

static const uint8_t ADC_BITS = 12;          // resolution adc 0 a 4095
static const int WAVE_POINTS = 220;          // nb de points gardes pour tracer la vague

static const float AC_VHIGH = 3.3f;          // tension max theorique
static const float AC_VLOW  = 0.0f;          // tension min theorique

// #8 variables globales signal
// --------------------
uint16_t pwmLut[LUT_SIZE];                   // table de valeurs pwm deja calculees
volatile uint8_t pwmIndex = 0;               // position actuelle dans la table sinus
volatile bool pwmRunning = false;            // dit si generation active ou non
hw_timer_t *waveTimer = nullptr;             // timer materiel pour faire avancer le sinus

// #9 variables globales acquisition
// --------------------
volatile uint16_t gWaveAdc1[WAVE_POINTS];    // buffer des points adc1 pour affichage
volatile uint16_t gWaveAdc2[WAVE_POINTS];    // buffer des points adc2 pour affichage
volatile int16_t  gWaveDiff[WAVE_POINTS];    // buffer difference adc2 - adc1
volatile float    gWaveTimeUs[WAVE_POINTS];  // temps associe a chaque point

volatile float gInstAdc1 = 0.0f;             // moyenne instantanee adc1
volatile float gInstAdc2 = 0.0f;             // moyenne instantanee adc2
volatile float gInstDiff = 0.0f;             // moyenne instantanee diff
volatile float gInstCond = 0.0f;             // conductivite calculee
volatile bool  gValid    = false;            // dit si mesure consideree valide

unsigned long gFrameCounter = 0;             // compteur de tours de boucle
uint32_t gBootMillis = 0;                    // temps de demarrage

// #10 petits outils
// --------------------
String formatUptimeHHMMSS() {
  uint32_t elapsedSec = (millis() - gBootMillis) / 1000UL; // temps passe depuis boot en secondes
  uint32_t h = elapsedSec / 3600UL;                        // extrait heures
  uint32_t m = (elapsedSec % 3600UL) / 60UL;               // extrait minutes
  uint32_t s = elapsedSec % 60UL;                          // extrait secondes

  char buf[16];
  snprintf(buf, sizeof(buf), "%02lu:%02lu:%02lu",          // formate hh:mm:ss
           (unsigned long)h,
           (unsigned long)m,
           (unsigned long)s);
  return String(buf);                                      // renvoie texte pret pour json
}

float bitsToConductivity(float adc1Bits, float adc2Bits, float diffBits) {
  (void)adc1Bits;                                          // variable ignoree pour l instant
  (void)adc2Bits;                                          // variable ignoree pour l instant
  return diffBits;                                         // ici conductivite = simple diff brute
}

unsigned long getAcPeriodUs() {
  if (F_SIG_HZ <= 0.0f) return 0;                          // securite si frequence invalide
  return (unsigned long)(1000000.0f / F_SIG_HZ);           // periode en microsecondes
}

unsigned long getLutStepUs() {
  const float stepUs = 1000000.0f / (F_SIG_HZ * LUT_SIZE); // temps entre 2 points de la table
  return (unsigned long)(stepUs + 0.5f);                   // arrondi simple
}

float getAcInstantValueFromIndex(int idxSnapshot) {
  int idx = idxSnapshot % LUT_SIZE;                        // ramene index dans 0 a lut_size - 1
  if (idx < 0) idx += LUT_SIZE;                            // securite si negatif

  float theta = 2.0f * PI * (float)idx / (float)LUT_SIZE;  // angle du point dans une periode
  float dutyNorm = DUTY_OFFSET + DUTY_AMPL * sinf(theta);  // cree la forme sinus autour du centre
  if (dutyNorm < 0.0f) dutyNorm = 0.0f;                    // evite duty negatif
  if (dutyNorm > 1.0f) dutyNorm = 1.0f;                    // evite duty > 100 pourcent
  return dutyNorm * AC_VHIGH;                              // convertit duty en tension approx
}

unsigned long getAcPhaseUsFromIndex(int idxSnapshot) {
  unsigned long periodUs = getAcPeriodUs();                // periode complete du signal
  if (periodUs == 0) return 0;                             // securite si impossible a calculer

  int idx = idxSnapshot % LUT_SIZE;                        // index replie dans une seule periode
  if (idx < 0) idx += LUT_SIZE;                            // securite si negatif

  unsigned long stepUs = getLutStepUs();                   // duree d un point de table
  unsigned long elapsedInCycle = (unsigned long)idx * stepUs; // temps deja passe dans le cycle
  return elapsedInCycle % periodUs;                        // phase instantanee dans la periode
}

// #11 interruption touch
// --------------------
void IRAM_ATTR handleTouchRise() {
  unsigned long nowUs = micros();                          // temps exact de l interruption

  if ((nowUs - gLastTouchIrqUs) >= TOUCH_DEBOUNCE_US) {    // ignore les rebonds trop rapproches
    gTouchCount++;                                         // incremente le compteur
    gTouchState = true;                                    // marque etat actif
    gLastTouchIrqUs = nowUs;                               // memorise dernier front valide
  }
}

// #12 lecture capteurs
// --------------------
void readSensorsNonBlocking() {
  unsigned long now = millis();                            // temps actuel
  if (now - lastReadMs < READ_PERIOD_MS) return;           // pas encore temps de relire
  lastReadMs = now;                                        // memorise nouveau temps de lecture

  int rc = dht20.read();                                   // lance lecture du dht20
  if (rc == 0) {                                           // 0 = lecture ok
    gTempC = dht20.getTemperature();                       // recup temperature
    gHumPct = dht20.getHumidity();                         // recup humidite
  } else {
    gTempC = NAN;                                          // met vide si erreur
    gHumPct = NAN;                                         // met vide si erreur
  }
}

// #13 table pwm
// --------------------
void buildPwmTable() {
  const int pwmMax = (1 << PWM_BITS) - 1;                  // max pwm selon nb de bits

  for (int i = 0; i < LUT_SIZE; i++) {                     // remplit chaque point du sinus
    float theta = 2.0f * PI * (float)i / (float)LUT_SIZE;  // angle du point i
    float dutyNorm = DUTY_OFFSET + DUTY_AMPL * sinf(theta); // sinus centre sur duty_offset

    if (dutyNorm < 0.0f) dutyNorm = 0.0f;                  // borne min
    if (dutyNorm > 1.0f) dutyNorm = 1.0f;                  // borne max

    pwmLut[i] = (uint16_t)roundf(dutyNorm * pwmMax);       // convertit en valeur pwm entiere
  }
}

void ARDUINO_ISR_ATTR onWaveTimer() {
  if (!pwmRunning) return;                                 // si signal coupe on ne fait rien

  pwmIndex++;                                              // avance au point suivant du sinus
  if (pwmIndex >= LUT_SIZE) pwmIndex = 0;                  // retour debut quand fin atteinte

  ledcWrite(PWM_PIN, pwmLut[pwmIndex]);                    // ecrit nouvelle valeur pwm
}

bool startSignalGeneration() {
  if (!ledcAttach(PWM_PIN, PWM_FREQ_HZ, PWM_BITS)) {       // attache la sortie pwm au canal materiel
    return false;                                          // echec si pwm pas lance
  }

  pwmIndex = 0;                                            // depart au premier point du sinus
  if (!ledcWrite(PWM_PIN, pwmLut[pwmIndex])) {             // envoie premiere valeur pwm
    return false;                                          // echec si ecriture impossible
  }

  const uint32_t lutUpdateHz = (uint32_t)roundf(F_SIG_HZ * LUT_SIZE); // nb updates pwm par seconde
  const uint32_t alarmUs = (uint32_t)roundf(1000000.0f / lutUpdateHz); // delai timer entre 2 updates

  waveTimer = timerBegin(1000000);                         // timer base 1 mhz donc 1 tick = 1 us
  if (waveTimer == nullptr) {
    return false;                                          // echec creation timer
  }

  timerAttachInterrupt(waveTimer, &onWaveTimer);           // relie timer a la routine interruption
  timerAlarm(waveTimer, alarmUs, true, 0);                 // timer repete toutes alarmUs us

  pwmRunning = true;                                       // indique que le signal tourne
  return true;
}

// #14 config adc et broches
// --------------------
void configurePins() {
  pinMode(LED_ROUGE_PIN, OUTPUT);                          // led rouge en sortie
  digitalWrite(LED_ROUGE_PIN, LOW);                        // eteinte au depart

  pinMode(LED_JAUNE_PIN, OUTPUT);                          // led jaune en sortie
  digitalWrite(LED_JAUNE_PIN, LOW);                        // eteinte au depart

  pinMode(BUZZER_PIN, OUTPUT);                             // buzzer en sortie
  digitalWrite(BUZZER_PIN, LOW);                           // eteint au depart

  pinMode(TOUCH_PIN, INPUT_PULLDOWN);                      // entree avec pull down interne

  pinMode(PWM_PIN, OUTPUT);                                // sortie du pwm
  pinMode(ADC1_PIN, INPUT);                                // entree adc 1
  pinMode(ADC2_PIN, INPUT);                                // entree adc 2
}

void configureAdc() {
  analogReadResolution(ADC_BITS);                          // fixe resolution globale adc
  analogSetPinAttenuation(ADC1_PIN, ADC_11db);             // etend plage de tension adc1
  analogSetPinAttenuation(ADC2_PIN, ADC_11db);             // etend plage de tension adc2
}

void clearWaveBuffers() {
  noInterrupts();                                          // bloque interruptions pendant remise a zero
  for (int i = 0; i < WAVE_POINTS; i++) {
    gWaveAdc1[i] = 0;                                      // vide buffer adc1
    gWaveAdc2[i] = 0;                                      // vide buffer adc2
    gWaveDiff[i] = 0;                                      // vide buffer diff
    gWaveTimeUs[i] = 0.0f;                                 // vide buffer temps
  }
  interrupts();                                            // reactive interruptions
}

// #15 acquisition synchrone
// --------------------
bool acquireSynchronizedWave(float &adc1Mean, float &adc2Mean, float &diffMean, float &condOut) {
  if (!pwmRunning) {                                       // si signal absent pas de mesure utile
    adc1Mean = 0;
    adc2Mean = 0;
    diffMean = 0;
    condOut = 0;
    return false;
  }

  const uint32_t totalSamples = (uint32_t)LUT_SIZE * (uint32_t)AVG_CYCLES; // nb total de points a prendre
  const float dtSampleUs = 1000000.0f / ((float)LUT_SIZE * F_SIG_HZ);      // temps entre 2 points logiques

  float sumAdc1 = 0.0f;                                    // somme pour moyenne adc1
  float sumAdc2 = 0.0f;                                    // somme pour moyenne adc2
  float sumDiff = 0.0f;                                    // somme pour moyenne diff

  uint32_t collected = 0;                                  // nb de points deja captures
  uint8_t lastIndex = 255;                                 // valeur impossible au debut pour forcer 1ere lecture

  while (collected < totalSamples) {                       // boucle jusqu a avoir tous les echantillons
    uint8_t idx = pwmIndex;                                // snapshot du point actuel du sinus

    if (idx != lastIndex) {                                // lit une seule fois par changement d index
      lastIndex = idx;                                     // memorise index deja traite

      int raw1 = analogRead(ADC1_PIN);                     // lecture entree
      int raw2 = analogRead(ADC2_PIN);                     // lecture sortie
      int diff = raw2 - raw1;                              // difference brute entre les deux

      if (collected >= totalSamples - WAVE_POINTS) {       // garde seulement les derniers points pour l affichage
        int localPos = (int)(collected - (totalSamples - WAVE_POINTS)); // convertit en position de buffer

        if (localPos >= 0 && localPos < WAVE_POINTS) {     // securite sur index du buffer
          noInterrupts();                                  // protege ecriture partagee avec web
          gWaveAdc1[localPos] = (uint16_t)raw1;            // sauve point adc1
          gWaveAdc2[localPos] = (uint16_t)raw2;            // sauve point adc2
          gWaveDiff[localPos] = (int16_t)diff;             // sauve difference
          gWaveTimeUs[localPos] = localPos * dtSampleUs;   // temps associe au point
          interrupts();                                    // fin protection
        }
      }

      sumAdc1 += raw1;                                     // accumule pour moyenne adc1
      sumAdc2 += raw2;                                     // accumule pour moyenne adc2
      sumDiff += diff;                                     // accumule pour moyenne diff
      collected++;                                         // un point valide de plus
    }
  }

  adc1Mean = sumAdc1 / (float)totalSamples;                // moyenne finale adc1
  adc2Mean = sumAdc2 / (float)totalSamples;                // moyenne finale adc2
  diffMean = sumDiff / (float)totalSamples;                // moyenne finale diff
  condOut  = bitsToConductivity(adc1Mean, adc2Mean, diffMean); // calcule conductivite simplifiee

  if (adc1Mean < 1.0f && adc2Mean < 1.0f) {                // si tout est quasi nul mesure pas credible
    return false;
  }

  return true;                                             // sinon mesure consideree valide
}

void updateInstantValues(float adc1Mean, float adc2Mean, float diffMean, float cond, bool valid) {
  noInterrupts();                                          // protege les variables partagees
  gInstAdc1 = adc1Mean;                                    // stocke moyenne adc1
  gInstAdc2 = adc2Mean;                                    // stocke moyenne adc2
  gInstDiff = diffMean;                                    // stocke moyenne diff
  gInstCond = cond;                                        // stocke conductivite
  gValid = valid;                                          // stocke validite
  interrupts();                                            // fin protection
}

// #16 web api
// --------------------
void sendCommonHeaders() {
  server.sendHeader("Cache-Control", "no-store");          // evite cache navigateur
  server.sendHeader("Access-Control-Allow-Origin", "*");   // autorise acces depuis partout
}

void handleApiState() {
  bool ledRouge = (digitalRead(LED_ROUGE_PIN) == HIGH);    // lit etat led rouge
  bool ledJaune = (digitalRead(LED_JAUNE_PIN) == HIGH);    // lit etat led jaune
  bool buzzer = (digitalRead(BUZZER_PIN) == HIGH);         // lit etat buzzer
  bool touchNow = (digitalRead(TOUCH_PIN) == HIGH);        // lit etat touch instantane

  gTouchState = touchNow;                                  // met a jour etat global touch

  float adc1, adc2, diff, cond;
  bool valid;
  int idxSnapshot;

  noInterrupts();                                          // copie atomique des valeurs partagees
  adc1 = gInstAdc1;                                        // copie adc1 moyen
  adc2 = gInstAdc2;                                        // copie adc2 moyen
  diff = gInstDiff;                                        // copie diff moyen
  cond = gInstCond;                                        // copie conductivite
  valid = gValid;                                          // copie validite
  idxSnapshot = pwmIndex;                                  // copie position actuelle du sinus
  interrupts();

  float acInstant = getAcInstantValueFromIndex(idxSnapshot); // estime tension instantanee du point courant
  unsigned long acPhaseUs = getAcPhaseUsFromIndex(idxSnapshot); // phase actuelle en us
  unsigned long acPeriodUs = getAcPeriodUs();              // periode complete du signal

  String json = "{";                                       // debut json

  if (isnan(gTempC)) json += "\"tempC\":null,";            // null si pas de temperature valide
  else json += "\"tempC\":" + String(gTempC, 2) + ",";     // sinon temperature avec 2 decimales

  if (isnan(gHumPct)) json += "\"humPct\":null,";          // null si pas d humidite valide
  else json += "\"humPct\":" + String(gHumPct, 1) + ",";   // sinon humidite avec 1 decimale

  json += "\"adcRaw\":" + String(adc1, 3) + ",";           // moyenne adc1
  json += "\"adcRawOut\":" + String(adc2, 3) + ",";        // moyenne adc2
  json += "\"adcDiff\":" + String(diff, 3) + ",";          // moyenne diff
  json += "\"cond\":" + String(cond, 6) + ",";             // conductivite actuelle
  json += "\"valid\":" + String(valid ? "true" : "false") + ","; // statut de validite
  json += "\"clock\":\"" + formatUptimeHHMMSS() + "\",";   // uptime formatte
  json += "\"led_rouge\":" + String(ledRouge ? "true" : "false") + ","; // etat led rouge
  json += "\"led_jaune\":" + String(ledJaune ? "true" : "false") + ","; // etat led jaune
  json += "\"touch\":" + String(gTouchState ? "true" : "false") + ",";   // etat touch
  json += "\"touchCount\":" + String(gTouchCount) + ",";   // nb total touch
  json += "\"buzzer\":" + String(buzzer ? "true" : "false") + ",";       // etat buzzer
  json += "\"acEnabled\":" + String(pwmRunning ? "true" : "false") + ","; // signal on off
  json += "\"acSignalFreqHz\":" + String(F_SIG_HZ, 1) + ","; // frequence signal utile
  json += "\"pwmCarrierFreqHz\":" + String(PWM_FREQ_HZ) + ","; // frequence porteuse pwm
  json += "\"acPin\":" + String(PWM_PIN) + ",";            // pin pwm
  json += "\"adcPin\":" + String(ADC1_PIN) + ",";          // pin adc1
  json += "\"adcOutPin\":" + String(ADC2_PIN) + ",";       // pin adc2
  json += "\"acPeriodUs\":" + String(acPeriodUs) + ",";    // periode du signal
  json += "\"acPhaseUs\":" + String(acPhaseUs) + ",";      // phase actuelle
  json += "\"acInstantV\":" + String(acInstant, 3) + ",";  // tension instantanee estimee
  json += "\"acVHigh\":" + String(AC_VHIGH, 2) + ",";      // borne haute theorique
  json += "\"acVLow\":" + String(AC_VLOW, 2);              // borne basse theorique
  json += "}";                                             // fin json

  sendCommonHeaders();
  server.send(200, "application/json", json);              // envoie json et code ok
}

void handleApiWave() {
  String json = "{\"timeUs\":[";                           // debut json de la vague
  noInterrupts();                                          // protege buffers pendant copie
  for (int i = 0; i < WAVE_POINTS; i++) {
    json += String(gWaveTimeUs[i], 1);                     // ajoute temps du point i
    if (i < WAVE_POINTS - 1) json += ",";                  // virgule sauf dernier
  }

  json += "],\"adc1\":[";
  for (int i = 0; i < WAVE_POINTS; i++) {
    json += String((uint16_t)gWaveAdc1[i]);                // ajoute adc1 point par point
    if (i < WAVE_POINTS - 1) json += ",";
  }

  json += "],\"adc2\":[";
  for (int i = 0; i < WAVE_POINTS; i++) {
    json += String((uint16_t)gWaveAdc2[i]);                // ajoute adc2 point par point
    if (i < WAVE_POINTS - 1) json += ",";
  }

  json += "],\"diff\":[";
  for (int i = 0; i < WAVE_POINTS; i++) {
    json += String((int16_t)gWaveDiff[i]);                 // ajoute diff point par point
    if (i < WAVE_POINTS - 1) json += ",";
  }
  interrupts();                                            // fin protection buffers

  json += "]}";                                            // fin json

  sendCommonHeaders();
  server.send(200, "application/json", json);              // renvoie les tableaux de la vague
}

void handleLedRouge() {
  String st = server.arg("state");                         // lit parametre state depuis url
  bool on = (st == "1" || st == "true" || st == "on");    // accepte plusieurs facons de dire on
  digitalWrite(LED_ROUGE_PIN, on ? HIGH : LOW);           // allume ou eteint led rouge
  sendCommonHeaders();
  server.send(200, "text/plain", "OK");
}

void handleLedJaune() {
  String st = server.arg("state");                         // lit parametre state
  bool on = (st == "1" || st == "true" || st == "on");    // convertit en vrai faux
  digitalWrite(LED_JAUNE_PIN, on ? HIGH : LOW);           // allume ou eteint led jaune
  sendCommonHeaders();
  server.send(200, "text/plain", "OK");
}

void handleBuzzer() {
  String st = server.arg("state");                         // lit parametre state
  bool on = (st == "1" || st == "true" || st == "on");    // convertit texte vers bool
  digitalWrite(BUZZER_PIN, on ? HIGH : LOW);              // active ou coupe buzzer
  sendCommonHeaders();
  server.send(200, "text/plain", "OK");
}

void handleAcControl() {
  String st = server.arg("state");                         // lit demande on off du signal
  bool on = (st == "1" || st == "true" || st == "on");    // interprete plusieurs formats

  pwmRunning = on;                                         // active ou coupe la generation
  if (!pwmRunning) {
    ledcWrite(PWM_PIN, 0);                                 // force sortie a zero si off
  } else {
    ledcWrite(PWM_PIN, pwmLut[pwmIndex]);                  // remet valeur courante si on
  }

  sendCommonHeaders();
  server.send(200, "text/plain", "OK");
}

void handleResetTouchCount() {
  gTouchCount = 0;                                         // remet compteur a zero
  gTouchState = false;                                     // remet etat touch a faux
  gLastTouchIrqUs = 0;                                     // reset dernier temps irq
  sendCommonHeaders();
  server.send(200, "text/plain", "OK");
}

void handleOptions() {
  sendCommonHeaders();
  server.send(204, "text/plain", "");                      // reponse vide pour preflight cors
}

// #17 setup et loop
// --------------------
void setup() {
  Serial.begin(115200);                                    // ouvre serie debug
  delay(1200);                                             // petit delai de stabilisation

  gBootMillis = millis();                                  // memorise moment du boot

  configurePins();                                         // prepare toutes les broches
  configureAdc();                                          // prepare adc
  clearWaveBuffers();                                      // vide buffers au depart

  attachInterrupt(digitalPinToInterrupt(TOUCH_PIN), handleTouchRise, RISING); // interruption sur front montant

  Wire.begin(SDA_PIN, SCL_PIN);                            // demarre i2c
  dht20.begin();                                           // demarre dht20

  buildPwmTable();                                         // calcule la table du sinus pwm

  if (!startSignalGeneration()) {                          // lance signal pwm + timer
    Serial.println("Erreur: initialisation PWM/timer echouee");
    while (true) delay(1000);                              // bloque ici si echec critique
  }

  WiFi.mode(WIFI_AP);                                      // esp32 en point d acces
  WiFi.softAP(AP_SSID, AP_PASS);                           // cree le wifi local

  server.on("/api/state", HTTP_GET, handleApiState);       // route json etat general
  server.on("/api/wave", HTTP_GET, handleApiWave);         // route json vague
  server.on("/led", HTTP_GET, handleLedRouge);             // route led rouge
  server.on("/led_jaune", HTTP_GET, handleLedJaune);       // route led jaune
  server.on("/buzzer", HTTP_GET, handleBuzzer);            // route buzzer
  server.on("/ac", HTTP_GET, handleAcControl);             // route signal ac
  server.on("/touch/reset", HTTP_GET, handleResetTouchCount); // route reset compteur touch

  server.on("/api/state", HTTP_OPTIONS, handleOptions);    // cors preflight
  server.on("/api/wave", HTTP_OPTIONS, handleOptions);     // cors preflight
  server.on("/led", HTTP_OPTIONS, handleOptions);          // cors preflight
  server.on("/led_jaune", HTTP_OPTIONS, handleOptions);    // cors preflight
  server.on("/buzzer", HTTP_OPTIONS, handleOptions);       // cors preflight
  server.on("/ac", HTTP_OPTIONS, handleOptions);           // cors preflight
  server.on("/touch/reset", HTTP_OPTIONS, handleOptions);  // cors preflight

  server.begin();                                          // demarre serveur web

  Serial.println("ESP32 pret");
  Serial.print("AP IP: ");
  Serial.println(WiFi.softAPIP());                         // affiche ip du point d acces
}

void loop() {
  server.handleClient();                                   // gere les requetes web
  readSensorsNonBlocking();                                // lit dht20 sans bloquer

  float adc1Mean, adc2Mean, diffMean, cond;
  bool ok = acquireSynchronizedWave(adc1Mean, adc2Mean, diffMean, cond); // mesure synchrone sur plusieurs cycles
  updateInstantValues(adc1Mean, adc2Mean, diffMean, cond, ok);           // met a jour les valeurs partagees

  gFrameCounter++;                                         // compte les tours de loop
}