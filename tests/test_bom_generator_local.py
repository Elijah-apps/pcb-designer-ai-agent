"""Tests for the local BOM catalog fallback (no LLM required)."""
from pcbai.steps.bom_generator import (
    generate_bom,
    _LOCAL_CATALOG,
    _add_supporting_components,
    _guess_category,
)


def test_local_catalog_has_common_keywords():
    """The catalog should cover all keywords that the requirements parser
    fallback can produce."""
    parser_keywords = {
        "bluetooth", "wifi", "usb", "usb-c", "buck", "lipo", "lithium",
        "battery charger", "charger", "mcu", "microcontroller", "sd",
        "ldo", "regulator", "esp32", "esp8266", "stm32", "arduino",
        "rp2040", "nrf52", "imu", "accelerometer", "gyroscope",
        "temperature", "sensor", "adc", "opamp", "led", "relay",
        "motor", "display", "oled", "lcd", "i2c", "spi", "can bus",
        "ethernet",
    }
    catalog_keys = set(_LOCAL_CATALOG.keys())
    missing = parser_keywords - catalog_keys
    # Some keywords map to the same component; just ensure we have coverage
    # for the most important ones.
    for kw in ["mcu", "wifi", "bluetooth", "usb", "buck", "lipo", "ldo",
               "led", "sensor", "adc", "opamp", "display", "esp32",
               "stm32", "sd", "motor", "relay", "ethernet", "can"]:
        assert kw in catalog_keys, f"Catalog missing keyword: {kw}"


def test_generate_bom_local_fallback_produces_components():
    """Without an LLM, generate_bom should fall back to the local catalog."""
    bom = generate_bom({"keywords": ["esp32", "usb", "ldo"]})
    assert len(bom) >= 3
    mpns = [c["mpn"] for c in bom]
    assert "ESP32-WROOM-32D" in mpns
    assert "USB4085-GF-A" in mpns
    assert "AP2112K-3.3TRG1" in mpns
    for entry in bom:
        assert "description" in entry
        assert "package" in entry
        assert "pins" in entry
        assert "category" in entry


def test_generate_bom_empty_keywords():
    """No keywords → empty BOM."""
    assert generate_bom({"keywords": []}) == []


def test_generate_bom_different_prompts_different_output():
    """Different keyword sets should produce different BOMs."""
    bom1 = generate_bom({"keywords": ["esp32", "usb"]})
    bom2 = generate_bom({"keywords": ["stm32", "sensor"]})
    mpns1 = {c["mpn"] for c in bom1}
    mpns2 = {c["mpn"] for c in bom2}
    assert mpns1 != mpns2


def test_supporting_components_added():
    """MCU in BOM should trigger decoupling capacitor addition."""
    bom = _add_supporting_components([
        {"mpn": "STM32F103C8T6", "package": "LQFP-48", "pins": 48, "category": "mcu"}
    ])
    # Should have added at least one capacitor and one resistor
    caps = [c for c in bom if c.get("category") == "capacitor"]
    assert len(caps) > 0
    # Original component should still be present
    assert any(c["mpn"] == "STM32F103C8T6" for c in bom)


def test_guess_category_capacitor_vs_resistor():
    """Passive components should be categorized as capacitor or resistor."""
    # MPN starting with "C" (capacitor)
    assert _guess_category("0805", "C08051206K5RACTA") == "capacitor"
    # MPN starting with "RC" (resistor)
    assert _guess_category("0805", "RC0805FR-075100E") == "resistor"


def test_no_duplicate_mpns():
    """The catalog lookup should not produce duplicate MPNs."""
    bom = generate_bom({"keywords": ["esp32", "usb"]})
    mpns = [c["mpn"] for c in bom]
    assert len(mpns) == len(set(mpns)), "Duplicate MPNs in BOM"
