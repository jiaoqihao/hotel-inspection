"""Exercise answered, unanswered and unmonitored Local calls with no SIP listeners."""

import argparse
import contextlib
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave

from publish import deliver


HERE = Path(__file__).resolve().parent


def wait_for(check, timeout):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if check():
            return
        time.sleep(0.2)
    raise TimeoutError("Timed out waiting for the test PBX")


def exercise(root, inbox, speech):
    modules = next(Path("/usr/lib").rglob("app_mixmonitor.so")).parent
    names = ("etc", "run", "data", "log", "spool", "db", "keys", "agi")
    for name in names:
        (root / name).mkdir()
    (root / "data/documentation").symlink_to("/usr/share/asterisk/documentation")
    config = root / "etc/asterisk.conf"
    dirs = dict(astetcdir="etc", astrundir="run", astlogdir="log",
                astvarlibdir="data", astdatadir="data", astdbdir="db",
                astkeydir="keys", astagidir="agi", astspooldir="spool")
    config.write_text("[directories]\n" + "".join(
        f"{key} => {root / value}\n" for key, value in dirs.items()
    ) + f"astmoddir => {modules}\n[options]\nverbose=3\n")
    loads = ("res_timing_timerfd codec_ulaw codec_alaw format_wav bridge_simple "
             "bridge_builtin_features pbx_config app_stack app_dial app_exec "
             "app_mixmonitor app_playback func_strings func_callerid res_clioriginate")
    (root / "etc/modules.conf").write_text("[modules]\nautoload=no\n" + "".join(
        f"load={m}.so\n" for m in loads.split()))
    (root / "etc/logger.conf").write_text("[logfiles]\nconsole => warning,error,verbose\n")
    shutil.copyfile(speech, root / "speech.wav")
    shutil.copyfile(HERE / "hotel-inbound.conf", root / "etc/hotel-inbound.conf")
    dialplan = """[globals]
HOTEL_LINE_A=8101
HOTEL_LINE_B=8102
HOTEL_RECORD_SPOOL=SPOOL
#include "hotel-inbound.conf"
[qa-dial]
exten => _XXXX,1,Set(CALLERID(num)=8206)
 same => n,Dial(Local/${EXTEN}@qa-target/n,4,U(hotel-inbound^${EXTEN}^${CALLERID(num)}))
 same => n,Hangup()
exten => 9,1,Dial(Local/s@qa-noanswer/n,1,U(hotel-inbound^8101^8206))
 same => n,Hangup()
[qa-noanswer]
exten => s,1,Ringing()
 same => n,Wait(4)
[qa-target]
exten => _XXXX,1,Answer()
 same => n,Wait(1)
 same => n,GotoIf($["${EXTEN}" = "8102"]?listen)
 same => n,Playback(SPEECH)
 same => n,Hangup()
 same => n(listen),Wait(SECONDS)
 same => n,Hangup()
"""
    with wave.open(str(speech)) as src:
        duration = src.getnframes() / src.getframerate()
    (root / "etc/extensions.conf").write_text(dialplan.replace("SPOOL\n", str(root / "spool")+"\n")
        .replace("SPEECH", str(root / "speech")).replace("SECONDS", str(int(duration) + 3)))
    cmd = [shutil.which("asterisk") or "/usr/sbin/asterisk", "-C", str(config)]
    with (root / "console.log").open("w") as log:
        pbx = subprocess.Popen(cmd + ["-f"], stdout=log, stderr=log, cwd=root)
        def cli(command):
            return subprocess.run(cmd + ["-rx", command], capture_output=True, text=True)
        try:
            wait_for(lambda: "fully booted" in cli("core waitfullybooted").stdout.lower(), 15)
            for target in ("8101", "8102", "8103", "9"):
                app = f"Playback {root}/speech" if target == "8102" else "Wait 60"
                cli(f"channel originate Local/{target}@qa-dial/n application {app}").check_returncode()
            wait_for(lambda: len(list((root / "spool").glob("*.closed.wav"))) >= 2, duration + 22)
            wait_for(lambda: "0 active channels" in cli("core show channels").stdout, 20)
            closed = sorted((root / "spool").glob("*.closed.wav"))
            assert sorted(f.name.split("_")[-1] for f in closed) == ["8101.closed.wav", "8102.closed.wav"]
            try:
                deliver(root / "spool", root / "unmounted")
            except ValueError:
                assert all(f.is_file() for f in closed)
            else:
                raise AssertionError("Missing inbox did not retain the recordings")
            assert deliver(root / "spool", inbox)
            assert deliver(root / "spool", inbox)
            assert len(list(inbox.glob("*.ready.json"))) == 2
        except BaseException:
            print((root / "console.log").read_text()[-14000:], file=sys.stderr)
            raise
        finally:
            cli("core stop now")
            with contextlib.suppress(ProcessLookupError):
                pbx.terminate()
            pbx.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("speech", type=Path, help="Synthetic 8 kHz mono PCM16 WAV")
    parser.add_argument("inbox", type=Path, help="Existing empty, isolated inbox")
    args = parser.parse_args()
    if any(args.inbox.iterdir()):
        parser.error("Use an empty test inbox")
    with tempfile.TemporaryDirectory(prefix="hotel-pbx-") as directory:
        exercise(Path(directory), args.inbox, args.speech)
    print("Two answered calls delivered, one in each audio direction. Controls excluded.")
