from __future__ import annotations

import json
import re
from typing import List, Dict, Any, Optional
from pcbai.llm.provider import get_provider

SYSTEM_PROMPT = """\
You are an expert electronics engineer. Given a list of required component types, select real-world, highly available Manufacturer Part Numbers (MPNs) for each.
Return ONLY a valid JSON array of objects. Do not write any thoughts, greetings, or formatting blocks.
Schema:
[
  {
    "description": "brief description",
    "mpn": "Real MPN (e.g. STM32F103C8T6)",
    "package": "Standard package (e.g. LQFP-48)",
    "pins": 48
  }
]
"""

# ─────────────────────────────────────────────────────────────────────────────
# Local component catalog — used as a fallback when no LLM is available
# or when the LLM response cannot be parsed.
#
# Each keyword maps to a list of BOM entries.  Entries include a "category"
# field so that downstream code (ref-prefix assignment, footprint selection)
# can make the right decision without guessing from the MPN text alone.
# ─────────────────────────────────────────────────────────────────────────────

_LOCAL_CATALOG: Dict[str, List[Dict[str, Any]]] = {
    # ── MCUs / processors ──────────────────────────────────────────────────
    "esp32": [
        {"description": "ESP32-WROOM-32D dual-core 32-bit LX6 MCU, WiFi+BT, 4 MB flash",
         "mpn": "ESP32-WROOM-32D", "package": "Module", "pins": 38,
         "voltage": "3.3V", "category": "mcu"},
    ],
    "stm32": [
        {"description": "STM32F103C8T6 ARM Cortex-M3 MCU, 72 MHz, 64 KB Flash",
         "mpn": "STM32F103C8T6", "package": "LQFP-48", "pins": 48,
         "voltage": "3.3V", "category": "mcu"},
    ],
    "mcu": [
        {"description": "STM32F103C8T6 ARM Cortex-M3 MCU, 72 MHz, 64 KB Flash",
         "mpn": "STM32F103C8T6", "package": "LQFP-48", "pins": 48,
         "voltage": "3.3V", "category": "mcu"},
    ],
    "rp2040": [
        {"description": "Raspberry Pi RP2040 MCU, dual-core ARM Cortex-M0+, 264 KB Flash",
         "mpn": "RP2040", "package": "QFN-48", "pins": 48,
         "voltage": "3.3V", "category": "mcu"},
    ],
    "arduino": [
        {"description": "ATmega328P-PU 8-bit AVR MCU, Arduino Uno compatible",
         "mpn": "ATmega328P-PU", "package": "DIP-28", "pins": 28,
         "voltage": "5V", "category": "mcu"},
    ],
    # ── Connectivity / wireless ────────────────────────────────────────────
    "wifi": [
        {"description": "ESP-01 WiFi module (ESP8266)",
         "mpn": "ESP-01", "package": "Module", "pins": 8,
         "voltage": "3.3V", "category": "wifi"},
    ],
    "bluetooth": [
        {"description": "nRF52832-QF Bluetooth LE 64-MHz Cortex-M4 SoC",
         "mpn": "nRF52832-QF", "package": "QFN-48", "pins": 48,
         "voltage": "3.3V", "category": "bluetooth"},
    ],
    "nrf52": [
        {"description": "nRF52832-QF Bluetooth LE 64-MHz Cortex-M4 SoC",
         "mpn": "nRF52832-QF", "package": "QFN-48", "pins": 48,
         "voltage": "3.3V", "category": "bluetooth"},
    ],
    "ethernet": [
        {"description": "LAN8720A 10/100 Ethernet PHY",
         "mpn": "LAN8720A", "package": "QFN-32", "pins": 32,
         "voltage": "3.3V", "category": "ethernet"},
    ],
    "can": [
        {"description": "MCP2551 CAN bus transceiver",
         "mpn": "MCP2551FD", "package": "SOIC-8", "pins": 8,
         "voltage": "5V", "category": "ic"},
    ],
    # ── Power / regulators ────────────────────────────────────────────────
    "buck": [
        {"description": "MP1584EN 1.2 A step-down switching regulator",
         "mpn": "MP1584EN", "package": "SOIC-8", "pins": 8,
         "voltage": "28V", "category": "ic"},
    ],
    "lipo": [
        {"description": "MCP73831T 100 mA single-cell LiPo charger",
         "mpn": "MCP73831T-2ACI/OT", "package": "SOT-23-5", "pins": 5,
         "voltage": "6V", "category": "ic"},
    ],
    "ldo": [
        {"description": "AP2112K-3.3 3.3 V 600 mA LDO regulator",
         "mpn": "AP2112K-3.3TRG1", "package": "SOT-23-5", "pins": 5,
         "voltage": "3.3V", "category": "ic"},
    ],
    "regulator": [
        {"description": "AP2112K-3.3 3.3 V 600 mA LDO regulator",
         "mpn": "AP2112K-3.3TRG1", "package": "SOT-23-5", "pins": 5,
         "voltage": "3.3V", "category": "ic"},
    ],
    # ── Connectors ────────────────────────────────────────────────────────
    "usb": [
        {"description": "USB Type-C 2.0 receptacle, 24-pin, Molex 2011640100",
         "mpn": "USB4085-GF-A", "package": "USB-C-SMD", "pins": 24,
         "voltage": "VBUS", "category": "connector"},
    ],
    "usb-c": [
        {"description": "USB Type-C 2.0 receptacle, 24-pin, Molex 2011640100",
         "mpn": "USB4085-GF-A", "package": "USB-C-SMD", "pins": 24,
         "voltage": "VBUS", "category": "connector"},
    ],
    "sd": [
        {"description": "MicroSD card slot, push-push, 8-pin",
         "mpn": "DM3D-SF", "package": "SD_Card", "pins": 8,
         "voltage": "3.3V", "category": "connector"},
    ],
    "sd card": [
        {"description": "MicroSD card slot, push-push, 8-pin",
         "mpn": "DM3D-SF", "package": "SD_Card", "pins": 8,
         "voltage": "3.3V", "category": "connector"},
    ],
    # ── Sensors ───────────────────────────────────────────────────────────
    "sensor": [
        {"description": "BME280 temperature/humidity/pressure sensor",
         "mpn": "BME280", "package": "LGA-8", "pins": 8,
         "voltage": "3.3V", "category": "sensor"},
    ],
    "imu": [
        {"description": "ICM-20948 9-axis IMU (gyro + accel + mag)",
         "mpn": "ICM-20948", "package": "LQFP-48", "pins": 48,
         "voltage": "3.3V", "category": "sensor"},
    ],
    "temperature": [
        {"description": "DS18B20 digital temperature sensor, 1-Wire",
         "mpn": "DS18B20", "package": "TO-92", "pins": 3,
         "voltage": "3.3V", "category": "sensor"},
    ],
    "accelerometer": [
        {"description": "ADXL345 digital accelerometer, I2C/SPI",
         "mpn": "ADXL345", "package": "LGA-14", "pins": 14,
         "voltage": "3.3V", "category": "sensor"},
    ],
    "gyroscope": [
        {"description": "L3GD20 3-axis digital gyroscope, SPI/I2C",
         "mpn": "L3GD20TR", "package": "LGA-16", "pins": 16,
         "voltage": "3.3V", "category": "sensor"},
    ],
    # ── Analog / signal ───────────────────────────────────────────────────
    "adc": [
        {"description": "ADS1115 16-bit I2C ADC, 4-channel",
         "mpn": "ADS1115", "package": "SOIC-8", "pins": 8,
         "voltage": "3.3V", "category": "ic"},
    ],
    "opamp": [
        {"description": "TLV2372 dual precision op-amp, SOIC-8",
         "mpn": "TLV2372ID", "package": "SOIC-8", "pins": 8,
         "voltage": "3.3V", "category": "ic"},
    ],
    # ── Discrete / indicators ────────────────────────────────────────────
    "led": [
        {"description": "Green SMD LED, 0402",
         "mpn": "APGHS1005CGCK", "package": "0402", "pins": 2,
         "voltage": "3.3V", "category": "led"},
    ],
    "relay": [
        {"description": "SRD-05VDC-SL-C 5 V signal relay",
         "mpn": "SRD-05VDC-SL-C", "package": "Relay_Housing", "pins": 4,
         "voltage": "5V", "category": "relay"},
    ],
    "motor": [
        {"description": "TC78HF101FTG dual H-bridge motor driver",
         "mpn": "TC78HF101FTG", "package": "SOIC-20", "pins": 20,
         "voltage": "12V", "category": "ic"},
    ],
    # ── Displays ──────────────────────────────────────────────────────────
    "display": [
        {"description": "SSD1306 0.96\" monochrome OLED display, I2C",
         "mpn": "SSD1306", "package": "LGA-14", "pins": 14,
         "voltage": "3.3V", "category": "display"},
    ],
    "oled": [
        {"description": "SSD1306 0.96\" monochrome OLED display, I2C",
         "mpn": "SSD1306", "package": "LGA-14", "pins": 14,
         "voltage": "3.3V", "category": "display"},
    ],
    "lcd": [
        {"description": "16×2 character LCD display, blue backlight",
         "mpn": "NHD-0216K1Z", "package": "Through_Hole", "pins": 16,
         "voltage": "5V", "category": "display"},
    ],
    # ── Power management ICs ─────────────────────────────────────────────
    "charger": [
        {"description": "MCP73831T 100 mA single-cell LiPo charger",
         "mpn": "MCP73831T-2ACI/OT", "package": "SOT-23-5", "pins": 5,
         "voltage": "6V", "category": "ic"},
    ],
}

# Packages that the parametric footprint builder knows how to generate
# locally (so we don't need an LLM for every footprint).
# Each entry: package_name -> (pins, pitch, body_l, body_w, pad_l, pad_w, ep_l, ep_w)
_LOCAL_PACKAGES = {
    "LQFP-48":       (48, 0.5,  7.0, 7.0, 1.2, 0.30, None, None),
    "QFN-48":        (48, 0.5,  6.0, 6.0, 0.6, 0.25, 3.5, 3.5),
    "QFN-32":        (32, 0.5,  5.0, 5.0, 0.6, 0.25, 2.8, 2.8),
    "SOIC-8":        (8,  1.27, 4.9, 3.9, 1.04, 0.65, None, None),
    "SOIC-14":       (14, 1.27, 8.7, 3.9, 1.04, 0.65, None, None),
    "SOIC-20":       (20, 1.27, 12.6, 5.3, 1.04, 0.65, None, None),
    "SOT-23-5":      (5,  0.65, 2.3, 2.1, 0.8, 0.45, None, None),
    "SOT-223":       (3,  2.3,  6.7, 3.7, 1.2, 0.9, None, None),
    "LGA-8":         (8,  0.65, 2.5, 2.5, 0.5, 0.35, None, None),
    "LGA-14":        (14, 0.8,  3.0, 3.0, 0.5, 0.35, None, None),
    "LGA-16":        (16, 0.5,  3.0, 3.0, 0.4, 0.25, None, None),
    "DIP-28":        (28, 2.54, 39.4, 14.0, 1.0, 0.6, None, None),
    "TO-92":         (3,  2.54, 4.6, 3.5, 1.0, 0.8, None, None),
}


def _local_catalog_lookup(keywords: List[str]) -> List[Dict[str, Any]]:
    """Build a BOM from the local catalog based on *keywords*."""
    bom: List[Dict[str, Any]] = []
    seen_mpns: set = set()
    for kw in keywords:
        entries = _LOCAL_CATALOG.get(kw, [])
        for entry in entries:
            mpn = entry.get("mpn", "")
            if mpn in seen_mpns:
                continue
            seen_mpns.add(mpn)
            bom.append(dict(entry))
    return bom


def _add_supporting_components(bom: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Add standard supporting components (decoupling caps, pull-ups, etc.)."""
    has_mcu = any(c.get("category") == "mcu" for c in bom)
    has_buck = any(c.get("mpn", "").lower().startswith(("mp1584", "lm2596", "sx1308")) for c in bom)
    has_usb = any(c.get("package") == "USB-C-SMD" for c in bom)
    has_sensor = any(c.get("category") == "sensor" for c in bom)
    has_display = any(c.get("category") == "display" for c in bom)
    has_lpo = any(c.get("mpn", "").lower().startswith("l3gd") for c in bom)

    additions: List[Dict[str, Any]] = []

    def _cap(mpn: str, pkg: str, val: str) -> Dict[str, Any]:
        return {"description": f"Capacitor {val}", "mpn": mpn, "package": pkg,
                "pins": 2, "voltage": "16V", "category": "capacitor"}

    def _res(mpn: str, pkg: str, val: str) -> Dict[str, Any]:
        return {"description": f"Resistor {val}", "mpn": mpn, "package": pkg,
                "pins": 2, "voltage": "16V", "category": "resistor"}

    if has_mcu:
        additions.append(_cap("C08051206K5RACTA", "0805", "100 nF"))
        additions.append(_cap("C08051E105KO06P", "0805", "10 µF"))
    if has_buck:
        additions.append(_cap("C08051E105KO06P", "0805", "10 µF"))
        additions.append(_cap("C08051C104K5RACTA", "0805", "100 nF"))
        additions.append(_res("RC0805FR-071001E", "0805", "100 kΩ"))
    if has_usb:
        additions.append(_res("RC0805FR-075100E", "0805", "5.1 kΩ"))
    if has_sensor:
        additions.append(_res("RC0805FR-074701E", "0805", "4.7 kΩ"))
        additions.append(_cap("C08051206K5RACTA", "0805", "100 nF"))
    if has_lpo:
        additions.append(_res("RC0805FR-074701E", "0805", "4.7 kΩ"))
        additions.append(_cap("C08051206K5RACTA", "0805", "100 nF"))
    if has_display:
        additions.append(_res("RC0805FR-074701E", "0805", "4.7 kΩ"))
        additions.append(_cap("C08051206K5RACTA", "0805", "100 nF"))

    return bom + additions


def generate_bom(requirements: Dict) -> List[Dict]:
    keywords = requirements.get("keywords", [])
    if not keywords:
        return []

    # ── Try the LLM first (most accurate for real-world MPNs) ──────────────
    prompt = f"Required components: {', '.join(keywords)}"
    try:
        provider = get_provider()
        raw = provider.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt}
        ], temperature=0.1, max_tokens=4000)

        # Regex to find JSON array or object
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            raw = match.group(0)
        else:
            raw = raw.strip()
            if raw.startswith("```json"):
                raw = raw[7:-3].strip()
            elif raw.startswith("```"):
                raw = raw[3:-3].strip()

        bom = json.loads(raw)

        if isinstance(bom, dict):
            # If it wrapped it in an object, extract the first list value
            for v in bom.values():
                if isinstance(v, list):
                    bom = v
                    break

        # Ensure required fields exist
        for item in bom:
            item.setdefault("voltage", "N/A")
            item.setdefault("category", _guess_category(item.get("package", ""), item.get("mpn", "")))
        return bom

    except Exception as e:
        # ── Local catalog fallback ─────────────────────────────────────────
        print(f"[bom_generator] LLM BOM failed: {e}. Falling back to local catalog.")
        bom = _local_catalog_lookup(keywords)
        bom = _add_supporting_components(bom)
        print(f"[bom_generator] Local catalog produced {len(bom)} components from keywords: {keywords}")
        return bom


def _guess_category(package: str, mpn: str) -> str:
    """Infer a component category from package/MPN text (for LLM-generated BOMs)."""
    pkg = (package or "").lower()
    full = (pkg + " " + (mpn or "").lower())

    if "usb" in pkg or "usb-c" in pkg:
        return "connector"
    if "led" in full:
        return "led"
    if "sd" in pkg or "microsd" in full:
        return "connector"
    if any(k in pkg for k in ("module", "lqfp", "qfn", "lga", "soc", "mcu", "dip")):
        return "mcu"
    if any(k in full for k in ("stm", "esp", "rp2", "atmega", "nrf", "microcontroller")):
        return "mcu"
    if any(k in full for k in ("mp1584", "mcp73831", "ap2112", "ldo", "mcp2551",
                                "ads1115", "tlv23", "pwm")):
        return "ic"
    if any(k in full for k in ("bme280", "icm", "adxl", "ds18", "l3gd", "sensor")):
        return "sensor"
    if any(k in full for k in ("ssd1306", "oled", "lcd", "display", "nhd")):
        return "display"
    if "relay" in full:
        return "relay"
    # Passives — distinguish resistor vs capacitor by MPN prefix or description
    if any(k in pkg for k in ("0805", "0402", "0603", "1206", "smd")):
        desc_lower = (mpn or "").lower()
        if "resistor" in full or desc_lower.startswith(("rc", "cr")):
            return "resistor"
        if "capacitor" in full or desc_lower.startswith("c") and not desc_lower.startswith("cr"):
            return "capacitor"
        return "resistor"  # default ambiguous SMD to resistor
    return "ic"
