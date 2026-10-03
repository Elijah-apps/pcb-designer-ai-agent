.PHONY: help test smoke web cli-design cli-bom clean

help:  ## Show this help
	@echo "PCB Designer AI Agent"
	@echo ""
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

test:  ## Run unit tests
	cd anna-app/executas/pcb-designer && PYTHONPATH=. pytest ../../../../tests/ -v

smoke:  ## Run plugin smoke tests
	cd anna-app/executas/pcb-designer && PYTHONPATH=. python3 test_harness.py --smoke

web:  ## Start web dashboard (port 5000)
	python3 run_web.py

web-debug:  ## Start web dashboard in debug mode
	python3 run_web.py --debug

cli-design:  ## Full pipeline (BOM + schematic + PCB)
	cd anna-app/executas/pcb-designer && PYTHONPATH=. python3 -m pcbai.pipeline.cli design "STM32 sensor board with BME280" --out build

cli-bom:  ## BOM only
	cd anna-app/executas/pcb-designer && PYTHONPATH=. python3 -m pcbai.pipeline.cli bom "ESP32 board with USB-C and buck converter" --out build

clean:  ## Remove generated outputs
	rm -rf web/outputs/ anna-app/executas/pcb-designer/build/
	@echo "Cleaned. ✅"
