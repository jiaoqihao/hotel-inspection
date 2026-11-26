"""Deliver closed MixMonitor recordings to the HotelVoice inbox (PBX-side Linux)."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sys
from tempfile import NamedTemporaryFile
import wave


KEY = re.compile(
    r"(?P<epoch>[0-9]{1,12})_(?P<id>[A-Za-z0-9.-]{1,120})_"
    r"(?P<caller>anonymous|[0-9+]{1,32})_(?P<callee>[0-9]{1,32})\.closed\.wav"
)


def write_atomic(path, writer):
    with NamedTemporaryFile(dir=path.parent, suffix=".part", delete=False) as tmp:
        try:
            writer(tmp)
            tmp.flush()
            os.fsync(tmp.fileno())
            tmp.close()
            os.replace(tmp.name, path)
        finally:
            Path(tmp.name).unlink(missing_ok=True)


def publish(recording, inbox):
    match = KEY.fullmatch(recording.name)
    if not match:
        raise ValueError("Unexpected recording name")
    with wave.open(str(recording), "rb") as src:
        if not src.getnframes():
            raise ValueError("No bridged audio")
    call_id = match["id"]
    stem = "asterisk-" + call_id
    audio = inbox / (stem + ".wav")
    manifest = inbox / (stem + ".ready.json")
    if not any((inbox / (stem + suffix)).exists()
               for suffix in (".ready.json", ".ready.done", ".ready.rejected")):
        with recording.open("rb") as stream:
            write_atomic(audio, lambda dst: shutil.copyfileobj(stream, dst))
        data = {
            "call_id": call_id,
            "file": audio.name,
            "caller": match["caller"],
            "callee": match["callee"],
            "started": datetime.fromtimestamp(int(match["epoch"]), timezone.utc).isoformat(),
        }
        write_atomic(manifest, lambda dst: dst.write(json.dumps(data).encode("utf-8")))
    recording.unlink()


def deliver(spool, inbox):
    # Require a pre-provisioned inbox. A disconnected mount must not be created here.
    if not inbox.is_dir() or spool.resolve() == inbox.resolve():
        raise ValueError("Specify a separate, existing inbox")
    with (spool / ".publish.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ok = True
        for recording in sorted(spool.glob("*.closed.wav")):
            try:
                publish(recording, inbox)
            except (OSError, ValueError, EOFError, wave.Error) as exc:
                print(f"Recording retained for retry: {exc}", file=sys.stderr)
                ok = False
        return ok


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("spool", type=Path)
    parser.add_argument("inbox", type=Path)
    args = parser.parse_args()
    sys.exit(0 if deliver(args.spool, args.inbox) else 1)
