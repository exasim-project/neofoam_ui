"""Under ``pvpython``: the field view's data mode (``_ui/_live.py``) listening on the port given
as the first argument. Prints a JSON line when it starts listening and for each new extract it
holds (source, cells, range of ``T``); draws the source named by the second argument
after the first one (as a frame naming it would make the pane do). Runs until killed, at most 120 s."""

import asyncio
import json
import os
import sys
import time

from neofoam_ui._ui._live import _LiveData


def _say(obj):
    os.write(1, (json.dumps(obj) + "\n").encode())  # pvpython captures sys.stdout


def _held(data):
    name = data.specs[0]["source"]
    source = data.sources.get(name)
    if source is None or not source.GetDataInformation().GetNumberOfCells():
        return None
    return {"source": name, "cells": source.GetDataInformation().GetNumberOfCells(),
            "T": list(source.CellData["T"].GetRange())}


async def main(port, then):
    data = _LiveData(port, lambda text: None)
    data.listen()
    _say({"listening": data.url})
    last, end = None, time.monotonic() + 120
    while time.monotonic() < end:
        await asyncio.sleep(0.05)
        held = _held(data)
        if held is None or held == last:
            continue
        _say(held)
        last = held
        data.show([{"id": "d1", "source": then, "field": "T"}])  # what a frame of the scene names


asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
