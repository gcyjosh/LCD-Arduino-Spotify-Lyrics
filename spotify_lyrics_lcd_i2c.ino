/*
  Spotify Lyrics Display — Arduino Mega + I2C LCD
  -------------------------------------------------
  Wiring:
    LCD GND → GND
    LCD VCC → 5V
    LCD SDA → Mega Pin 20 (SDA)
    LCD SCL → Mega Pin 21 (SCL)

  Library: LiquidCrystal_I2C by Frank de Araujo
  I2C Address: 0x27 (try 0x3F if blank)

  Protocol: "ROW1_TEXT|ROW2_TEXT\n"
  Custom char 0 = music note (♪), sent as \x08 from Python
*/

#include <Wire.h>
#include <LiquidCrystal_I2C.h>

LiquidCrystal_I2C lcd(0x27, 16, 2);

const int LCD_COLS = 16;
String inputBuffer = "";

// Music note custom character
byte musicNote[8] = {
  0b00100,
  0b00110,
  0b00101,
  0b00101,
  0b00100,
  0b11100,
  0b11100,
  0b00000
};

void setup() {
  Serial.begin(9600);
  lcd.init();
  lcd.backlight();
  lcd.createChar(0, musicNote);
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("  Lyrics LCD    ");
  lcd.setCursor(0, 1);
  lcd.print("  Ready...      ");
}

void loop() {
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') {
      processPacket(inputBuffer);
      inputBuffer = "";
    } else {
      inputBuffer += c;
    }
  }
}

void processPacket(String packet) {
  int sep = packet.indexOf('|');
  if (sep == -1) return;

  String row1 = packet.substring(0, sep);
  String row2 = packet.substring(sep + 1);

  if (row1.length() > LCD_COLS) row1 = row1.substring(0, LCD_COLS);
  if (row2.length() > LCD_COLS) row2 = row2.substring(0, LCD_COLS);

  lcd.setCursor(0, 0);
  printWithCustomChars(row1);
  lcd.setCursor(0, 1);
  printWithCustomChars(row2);
}

void printWithCustomChars(String s) {
  for (int i = 0; i < s.length(); i++) {
    if (s[i] == '\x08') {
      lcd.write(byte(0));  // music note custom char
    } else {
      lcd.write(s[i]);
    }
  }
  // Pad remaining space
  for (int i = s.length(); i < LCD_COLS; i++) {
    lcd.write(' ');
  }
}
