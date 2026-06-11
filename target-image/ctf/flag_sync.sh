#!/bin/bash
# MegaCorp asynchronous job worker.
# Processes report, DB audit, credential, template, and webhook queues.

set -e

cd /app
python3 - <<'PY'
import json
import sys

import app

processed = app.process_pending_jobs()
if processed:
    sys.stdout.write(json.dumps({"processed": processed}, ensure_ascii=False) + "\n")
PY
