"""Read-only CAN driver discovery. Does not open a channel or transmit."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from can_adapters.device_manager import CanDeviceManager
print(json.dumps(CanDeviceManager().discover(), ensure_ascii=False, indent=2))
