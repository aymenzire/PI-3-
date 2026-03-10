print("LOGGER START", flush=True)

import time
import json
import requests
from datetime import datetime

URL = "http://192.168.4.1/api/state"
PERIOD_S = 1.0

start_time = datetime.now()
filename = start_time.strftime("bracelet_log_%Y-%m-%d_%H-%M-%S.jsonl")

print("URL:", URL, flush=True)
print("Output file:", filename, flush=True)
print("Press Ctrl+C to stop", flush=True)

try:
    while True:
        try:
            r = requests.get(URL, timeout=2)
            r.raise_for_status()
            s = r.json()

            sample = {"ts": datetime.utcnow().isoformat() + "Z", **s}
            with open(filename, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample) + "\n")

            print("OK", sample, flush=True)

        except Exception as e:
            print("ERROR", e, flush=True)

        time.sleep(PERIOD_S)

except KeyboardInterrupt:
    print("Stopped. File saved:", filename, flush=True)