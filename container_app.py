"""DwellMind HA container entry point. Bounded observation, then offline report."""
import argparse
import json
import os
from pathlib import Path
import signal
import stat
import sys
import uuid

from ha_live_observer import Capture, LiveConfig
from live_report import load_journals, summarize
from policy import SafeError, strict_json
from private_journal import Journal
from rooms import PROFILES
from upstream import read_secret

VERSION = "0.2.1a1"


def number(env, name, default):
    value = env.get(name, str(default))
    if not isinstance(value, str) or not value.isascii() or not value.isdigit():
        raise SafeError("Invalid numeric environment setting.")
    return int(value)


def boolean(env, name, default="false"):
    value = env.get(name, default).lower()
    if value not in {"true", "false"}:
        raise SafeError("Boolean settings must be true or false.")
    return value == "true"


def environment_config(env):
    """Tokens are intentionally absent from this configuration and reports."""
    selected = env.get("ROOMS", "office,living_room").split(',')
    selected = [name.strip() for name in selected]
    overrides = {}
    for key, room in PROFILES.items():
        changes = {}
        for role in ("primary_light","extra_light","pir","mmwave","derived","lux","media"):
            name = f"{key.upper()}_{role.upper()}"
            if name in env:
                value = env[name].strip()
                changes[role] = (value or None) if role == "media" else value
        if changes:
            overrides[key] = changes
    output = env.get("DATA_DIR", "/data")
    if not Path(output).is_absolute():
        raise SafeError("DATA_DIR must be absolute.")
    return LiveConfig({"ha_url":env.get("HA_URL", ""),
                       "allow_plaintext_ha":boolean(env, "ALLOW_PLAINTEXT_HA"),
                       "ha_token_file":env.get("HA_TOKEN_FILE") or "/run/secrets/ha_observer_token",
                       "journal_directory":str(Path(output)/"captures"),
                       "rooms":selected, "room_overrides":overrides,
                       "duration_seconds":number(env,"CAPTURE_SECONDS",300),
                       "max_messages":number(env,"MAX_MESSAGES",10000),
                       "max_reconnects":number(env,"MAX_RECONNECTS",3),
                       "engine_entities":[s.strip() for s in env.get("ENGINE_ENTITIES","").split(',') if s.strip()]})


def load_token(env):
    raw, path = env.get("HA_TOKEN", ""), env.get("HA_TOKEN_FILE", "")
    if bool(raw) == bool(path):
        raise SafeError("Set exactly one of HA_TOKEN or HA_TOKEN_FILE.")
    value = read_secret(path) if path else raw.strip()
    if not 32 <= len(value) <= 2048 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise SafeError("Invalid HA token format.")
    return value


def private_directory(path):
    path = Path(path)
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise SafeError("Appdata directory must be service-owned and mode 0700.")
    finally:
        os.close(fd)


def write_json(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as handle:
        json.dump(data, handle, ensure_ascii=False, allow_nan=False, indent=2)
        handle.write('\n')


def stopped(signum, frame):
    raise KeyboardInterrupt


def run(env, capture_factory=Capture):
    config = environment_config(env)
    token = load_token(env)
    data_dir = Path(env.get("DATA_DIR", "/data"))
    private_directory(data_dir)
    private_directory(data_dir/"captures")
    private_directory(data_dir/"reports")
    # A hard cap on existing capture directories prevents unbounded repeated starts.
    if sum(1 for _ in (data_dir/"captures").iterdir()) >= 64:
        raise SafeError("Capture retention cap reached (64 runs); archive or remove old captures locally.")
    run_id = "capture-" + uuid.uuid4().hex
    folder = data_dir/"captures"/run_id
    folder.mkdir(mode=0o700)
    journal = Journal(folder, "events")
    stats = {"stop_reason":"interrupted", "control_enabled":False}
    capture = capture_factory(config, token, journal)
    print(json.dumps({"product":"DwellMind HA","version":VERSION,"status":"starting",
                      "room_count":len(config.profiles),"duration_seconds":config.duration,
                      "control_enabled":False}),flush=True)
    interrupted = False
    try:
        stats = capture.run()
    except KeyboardInterrupt:
        interrupted = True
        stats = {**capture.stats,"stop_reason":"interrupted","control_enabled":False}
    finally:
        journal.close()
    files = sorted(folder.glob('events-*.jsonl'))
    report = summarize(load_journals(files), config.profiles)
    report.update(product="DwellMind HA", version=VERSION, capture_stats=stats)
    report_path = data_dir/"reports"/(run_id+".json")
    write_json(report_path,report)
    print(json.dumps({"status":"stopped","summary_file":str(report_path),"capture_stats":stats}),flush=True)
    if interrupted:
        return 130
    if stats["stop_reason"] not in {"duration","message_budget"} or not stats.get("max_initialized_entities"):
        return 1
    return 0


def main():
    parser = argparse.ArgumentParser(description="DwellMind HA — observation-only room intelligence")
    parser.add_argument('--version',action='version',version=f'DwellMind HA {VERSION}')
    parser.add_argument('--check',action='store_true',help='Validate environment/credentials locally without network or output files')
    parser.add_argument('--service',action='store_true',help='Run the HA companion worker API')
    args = parser.parse_args()
    os.umask(0o077)
    signal.signal(signal.SIGTERM,stopped)
    try:
        if args.check:
            config = environment_config(os.environ)
            load_token(os.environ)
            print(json.dumps({"configuration_valid":True,"entities":sum(len(p.entities) for p in config.profiles),"network_checked":False}))
            return 0
        if args.service or os.environ.get('RUN_MODE') == 'service':
            from service_app import serve
            return serve(os.environ)
        return run(os.environ)
    except SafeError as error:
        print(str(error),file=sys.stderr)  # Only fixed messages from our code.
        return 1
    except Exception:
        print('DwellMind HA stopped. Check URL, credentials, permissions and appdata ownership locally; no raw error logged.',file=sys.stderr)
        return 1


if __name__=='__main__':
    raise SystemExit(main())
