#include "DHT.h"
 
// ---- Pines (coinciden con el YAML de ESPHome) ----
#define PIN_DHT        4
#define PIN_PUERTA     5
#define PIN_RELE_VENT  26
#define PIN_RELE_LUZ   27
 
DHT dht(PIN_DHT, DHT22);
 
void setup() {
  Serial.begin(115200);
  pinMode(PIN_PUERTA, INPUT_PULLUP);   // reed switch con pull-up interno
  pinMode(PIN_RELE_VENT, OUTPUT);
  pinMode(PIN_RELE_LUZ, OUTPUT);
  digitalWrite(PIN_RELE_VENT, LOW);
  digitalWrite(PIN_RELE_LUZ, LOW);
  dht.begin();
  Serial.println("Sistema de monitoreo iniciado");
  Serial.println("ESP32 DevKit V1");
}
 
void loop() {
  float t = dht.readTemperature();
  float h = dht.readHumidity();
  
  // En el switch, cuando está cerrado (puerta cerrada), el pin lee LOW. 
  // Cuando está abierto (puerta abierta), lee HIGH por el pull-up.
  bool puertaAbierta = (digitalRead(PIN_PUERTA) == LOW);
 
  if (isnan(t) || isnan(h)) {
    Serial.println("Error leyendo el DHT22");
    delay(2000);
    return;
  }
 
  Serial.print("Temperatura: "); Serial.print(t, 2); Serial.println(" °C");
  Serial.print("Humedad: ");     Serial.print(h, 2); Serial.println(" %");
  Serial.println(puertaAbierta ? "Puerta abierta" : "Puerta cerrada");
 
  // Lógica local de demostración: ventilador si hace calor y la puerta está cerrada
  bool ventilador = (t > 28.0) && !puertaAbierta;
  digitalWrite(PIN_RELE_VENT, ventilador ? HIGH : LOW);
  Serial.println(ventilador ? "Ventilador: ON" : "Ventilador: OFF");
 
  // El relé de la luz se puede quedar apagado en esta prueba (o encenderlo con la puerta abierta si quieres)
  digitalWrite(PIN_RELE_LUZ, LOW); 
 
  delay(2000);   // el DHT22 admite como máximo 1 lectura cada 2 segundos
}