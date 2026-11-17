"""The finder's state, kept in Supabase so the morning run can happen on GitHub's servers instead of the Mac.

Everything a run reads and builds on (the listings, price history, annual reports, föreningar, geocoding and
travel caches, the sitemap cache, the floor plans' index) is packed into one file, `state/data.tar.gz`, in the
project's private bucket, which only the secret key can read. The run in the cloud fetches it first and saves it
last; the Mac only fetches it, so the planner on the Mac shows the morning's apartments, and the floor plans
the cloud saved (`private/floorplans/`) are fetched with it.

    .venv/bin/python scraper/state.py --pull          # fetch the latest state into data/ (and new floor plans)
    .venv/bin/python scraper/state.py --pull --everything  # the cloud run: all of it, plan and bookkeeping included
    .venv/bin/python scraper/state.py --push          # save data/ as the latest state (the cloud run does this)
    .venv/bin/python scraper/state.py --pull --if-newer  # only when the saved state is newer than data/
"""
import io
import json
import os
import sys
import tarfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import online  # noqa: E402  (the project's address, the secret key and the requests)

DATA = online.DATA
STATE = "state/data.tar.gz"
# What a run reads and builds on; logs, CSV exports, backups and the floor plan images themselves stay out
KEEP = ("listings.json", "history.sqlite", "reports.json", "foreningar.json", "geocode.json", "travel.json",
        "digest.json", "digest-state.json", "online.json", "routes.json", "routes-cache.json", "predictions.json",
        "plan.json", "scrape.json", "status.json", "bookings.json", "floorplans/index.json", "scraper.log", "scraper.out")
LOG_LINES = 5000  # the logs are kept to their last lines
# What the Mac doesn't take from the cloud: your plan and filter (the local planner may have changed them since,
# and both go through Supabase's plans table anyway) and the cloud run's own upload bookkeeping. It does take
# status.json, which tells the Mac's fallback whether today's run is done
CLOUD_ONLY = ("plan.json", "scrape.json", "online.json")
KEEP_DIRS = ("cache",)
SAVED = DATA / ".state.json"  # when this Mac or runner last fetched or saved the state


def files():
    for name in KEEP:
        if (DATA / name).is_file():
            yield name
    for d in KEEP_DIRS:
        for p in sorted((DATA / d).glob("*")):
            if p.is_file():
                yield f"{d}/{p.name}"


def push(log=print):
    c = online.config()
    if not c:
        sys.exit("No data/supabase.json with the project's url and secret key.")
    for name in ("scraper.log", "scraper.out"):
        p = DATA / name
        if p.exists():
            lines = p.read_text("utf-8", "replace").splitlines(keepends=True)
            if len(lines) > LOG_LINES:
                p.write_text("".join(lines[-LOG_LINES:]), "utf-8")
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in files():
            tar.add(DATA / name, arcname=name)
    raw = buf.getvalue()
    online.upload(c, STATE, raw, "application/gzip")
    at = time.strftime("%Y-%m-%dT%H:%M:%S")
    SAVED.write_text(json.dumps({"at": at, "bytes": len(raw)}))
    log(f"State: saved {len(raw) // 1024} kB")


def _download(c, name):
    import urllib.request
    req = urllib.request.Request(f"{c['url'].rstrip('/')}/storage/v1/object/{online.BUCKET}/{name}",
                                 headers={"apikey": c["serviceKey"], "Authorization": "Bearer " + c["serviceKey"]})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read(), r.headers.get("Last-Modified", "")


def pull(log=print, if_newer=False, images=True, everything=False):
    c = online.config()
    if not c:
        sys.exit("No data/supabase.json with the project's url and secret key.")
    raw, modified = _download(c, STATE)
    seen = json.loads(SAVED.read_text()) if SAVED.exists() else {}
    if if_newer and modified and seen.get("modified") == modified:
        return False
    DATA.mkdir(exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for m in tar.getmembers():  # only plain files inside data/, never a path that leaves it
            if not m.isfile() or m.name.startswith("/") or ".." in Path(m.name).parts:
                continue
            if not everything and m.name in CLOUD_ONLY:
                continue
            target = DATA / m.name
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_bytes(tar.extractfile(m).read())
            os.replace(tmp, target)
    SAVED.write_text(json.dumps({"modified": modified, "bytes": len(raw)}))
    got = 0
    if images:  # floor plans the cloud saved, which the planner on the Mac shows from data/floorplans/
        index = json.loads((DATA / "floorplans" / "index.json").read_text("utf-8")) if (DATA / "floorplans" / "index.json").exists() else {}
        for name in index:
            if not (DATA / "floorplans" / name).exists():
                try:
                    (DATA / "floorplans" / name).write_bytes(_download(c, f"private/floorplans/{name}")[0])
                    got += 1
                except Exception as e:
                    log(f"State: couldn't fetch floor plan {name} ({e})")
    log(f"State: fetched {len(raw) // 1024} kB" + (f" and {got} floor plans" if got else ""))
    return True


# Who is doing today's run. GitHub's scheduled start can come hours late (17 November: 14:16 instead of 05:10), by
# when the Mac's fallback may already be running, and the two would then save over each other's state. Each run
# claims the day first and stands down when the other side's claim is still fresh. A claim older than CLAIM_HOURS
# (a run that died, or a Mac that fell asleep mid-run) doesn't count: the workflow's own limit is 5½ hours.
CLAIM = "state/claim.json"
CLAIM_HOURS = 6


def host():
    if os.environ.get("GITHUB_ACTIONS"):
        return "github"
    import socket
    return socket.gethostname()


def claim(since, log=print):
    """Claim today's run (the day starting at `since`, a naive local datetime). Returns who else holds a fresh
    claim, in which case nothing is claimed and the caller stands down; None when this run may go ahead."""
    import datetime as dt
    c = online.config()
    if not c:
        return None
    try:
        held = json.loads(_download(c, CLAIM)[0])
        at = dt.datetime.fromisoformat(held["at"])
        if held.get("host") != host() and at >= since and dt.datetime.now() - at < dt.timedelta(hours=CLAIM_HOURS):
            return f"{held['host']} since {at:%H:%M}"
    except Exception:  # no claim yet, or one that can't be read: go ahead
        pass
    try:
        online.upload(c, CLAIM, json.dumps({"host": host(), "at": time.strftime("%Y-%m-%dT%H:%M:%S")}).encode())
    except Exception as e:
        log(f"State: couldn't claim today's run ({e})")
    return None


if __name__ == "__main__":
    if "--push" in sys.argv:
        push()
    elif "--pull" in sys.argv:
        # The cloud run takes everything (--everything); the Mac leaves its own plan and the upload bookkeeping
        pull(if_newer="--if-newer" in sys.argv, everything="--everything" in sys.argv, images="--everything" not in sys.argv) \
            or print("State: nothing newer")
    else:
        print(__doc__)
