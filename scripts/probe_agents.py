"""Read each agent's list once and print how many apartments it shows, changing nothing: to see, before the morning
run moves somewhere new (GitHub's servers), which agents answer there as they do on the Mac. Only counts and
statuses are printed, since the Actions log of a public repo is public.

    .venv/bin/python scripts/probe_agents.py
"""
import pathlib
import sys
import tempfile
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scraper"))
import agents  # noqa: E402
import finder  # noqa: E402

robots = agents.Robots()
for ag in agents.AGENTS:
    t = time.time()
    try:
        with finder.launch(False) as b:
            cards = agents.discover(ag, b.new_page(), robots, pathlib.Path(tempfile.mkdtemp()), lambda m: None)
        print(f"{ag['name']:30} {len(cards):5} on the list  ({time.time() - t:.0f} s)", flush=True)
    except Exception as e:
        print(f"{ag['name']:30} failed: {type(e).__name__}: {str(e).splitlines()[0][:120]}", flush=True)
