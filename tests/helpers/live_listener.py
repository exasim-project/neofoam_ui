"""Under ``pvpython``: the field view's data mode (``_ui/_live.py``) listening on the port given
as the first argument. Prints a JSON line when it starts listening and for each new extract it
holds (source, cells, range of ``T``); switches to the source named by the second argument
after the first one. Runs until killed, at most 120 s."""

import asyncio
import json
import os
import sys
import time

from neofoam_ui._ui._live import _LiveData


def _say(obj):
    os.write(1, (json.dumps(obj) + "\n").encode())  # pvpython captures sys.stdout


def _held(data):
    source = data._source
    if source is None or not source.GetDataInformation().GetNumberOfCells():
        return None
    return {"source": data.name, "cells": source.GetDataInformation().GetNumberOfCells(),
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
        data.extract(then)


asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
