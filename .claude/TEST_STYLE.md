# Test rules (`tests/`)

## Placement

- Unit tests are named for the module they test: `engine/_fieldview.py` → `tests/test_field_view.py`.
- Integration/e2e tests are named for the scenario (`test_every_pass.py`), never reusing a source-module name.
- Prefer the lowest level that can prove the behavior — a fake program or fake solver, not a NeoFOAM run, for UI and engine logic.
- Tests are free functions, not classes. Shared fakes live in `tests/helpers/`.

## A good test

1. **Proves one named behavior** — `test_<subject>_<behavior>`; if the name needs "and", split it.
2. **Reads as setup → act → assert** — the call to the thing under test is one visible line, not buried in a helper.
3. **Inputs are explicit and on disk** — real case files under `tests/<area>/cases/<name>/` or the shipped `examples/`, never file content as strings/f-string templates in the test.
4. **Never mutates checked-in files** — `shutil.copytree` the case into `tmp_path`.
5. **Builds objects the way production does** — through the adapter/factory the app uses, not hand-constructed with fabricated arguments.
6. **Expectations are pre-written data** — literals in the test/`parametrize` entry or a golden file (`tests/golden/`); never computed by the code under test itself.
7. **Numeric asserts have justified tolerances** — `assert_allclose` with explicit `rtol`/`atol` tied to something physical, and an `err_msg` naming the case.
8. **Deterministic and independent** — seeded RNG, no reliance on test order or leftover state; timing tests state their margin.
9. **Docstring carries the design rationale** (why a subprocess worker, why this tolerance) — once, at module level.
10. **Adding coverage is data, not code** — new case directory or `parametrize` entry, no new test body.

## Hard constraints

- NeoFOAM is not a dependency: tests that need it run its interpreter (`NEOFOAM_PYTHON`, `NEOFOAM_SRC`) in a subprocess and are skipped without it. Catalyst/ParaView (`NEOFOAM_TEST_CATALYST`, a test-only variable: tests hand it to the code as `--catalyst` or in a temp config file) and Node.js are optional the same way.
- One solver per worker process — several runs go through separate worker subprocesses.
- Golden files change only on purpose: regenerate them, review the diff, commit it with the change.
- Never delete or weaken an existing test to get green.

## Done

`pixi run test` passes and you've seen the new test fail once (break the code) so you know it can.
