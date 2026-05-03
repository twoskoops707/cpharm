import os
from pathlib import Path

PORT    = 8080
WS_PORT = 8081
APK_DIR = Path(__file__).parent.parent / "apks"
REC_DIR = Path(__file__).parent / "recordings"
TOR_DIR = Path(__file__).parent / "tor"

# ADB ports to auto-connect on startup (covers AVD + common third-party emulators)
# AVD emulators launched by the wizard use even ports starting at 5554:
#   Phone 1 → 5554,  Phone 2 → 5556,  Phone 3 → 5558,  ... up to Phone 10 → 5572
# Other emulators: BlueStacks 5 (5555), BlueStacks instances (5565-5585),
#   MEmu (21503), NOX (62001), Genymotion (7555)
# Same JSON sequences; per-device timing/tap variance. Enable via env CPHARM_HUMAN_VARIATION=1 or set True below.
HUMAN_VARIATION_FORCE = False
HUMAN_VARIATION = HUMAN_VARIATION_FORCE or os.environ.get("CPHARM_HUMAN_VARIATION", "0").lower() in (
    "1",
    "true",
    "yes",
)

EMULATOR_PORTS = [
    # AVD emulators — even ports, 5554 through 5572 (supports 10 phones)
    ("127.0.0.1", 5554),
    ("127.0.0.1", 5556),
    ("127.0.0.1", 5558),
    ("127.0.0.1", 5560),
    ("127.0.0.1", 5562),
    ("127.0.0.1", 5564),
    ("127.0.0.1", 5566),
    ("127.0.0.1", 5568),
    ("127.0.0.1", 5570),
    ("127.0.0.1", 5572),
    # BlueStacks 5
    ("127.0.0.1", 5555),
    ("127.0.0.1", 5565),
    ("127.0.0.1", 5575),
    ("127.0.0.1", 5585),
    # MEmu
    ("127.0.0.1", 21503),
    # NOX Player
    ("127.0.0.1", 62001),
    # Genymotion
    ("127.0.0.1", 7555),
]
