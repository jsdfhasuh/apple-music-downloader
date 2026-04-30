# Configurable Completed Root Folder Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `build_nfo.py` move completed albums into a configurable completed root folder, defaulting to `/downloads/completed`.

**Architecture:** Keep the existing move behavior, but replace the hard-coded Windows path with a small config reader that extracts `completed-root-folder` from `config.yaml`. If the key is absent or empty, fall back to `/downloads/completed`.

**Tech Stack:** Python 3, unittest, pathlib, existing `config.yaml`

---

### Task 1: Add failing tests for completed root resolution

**Files:**
- Create: `webapp/tests/test_build_nfo.py`
- Test: `webapp/tests/test_build_nfo.py`

**Step 1: Write the failing tests**

```python
def testGetCompletedRootFallsBackToDownloadsCompleted():
  result = getCompletedRoot(Path("/tmp/missing.yaml"))
  assert result == Path("/downloads/completed")


def testGetCompletedRootReadsConfigValue():
  config_path.write_text('completed-root-folder: "/music/completed"\n', encoding="utf-8")
  result = getCompletedRoot(config_path)
  assert result == Path("/music/completed")
```

**Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m unittest webapp.tests.test_build_nfo -v`
Expected: FAIL because `getCompletedRoot` does not exist yet.

### Task 2: Implement minimal completed root resolution

**Files:**
- Modify: `tools/build_nfo.py`
- Test: `webapp/tests/test_build_nfo.py`

**Step 1: Add a small config reader**

```python
DEFAULT_COMPLETED_ROOT = Path("/downloads/completed")


def getCompletedRoot(configPath: Path) -> Path:
  ...
```

**Step 2: Use the resolved path in main**

```python
completedRoot = getCompletedRoot(scriptPath.resolve().parent.parent / "config.yaml")
```

**Step 3: Run tests to verify they pass**

Run: `.venv/bin/python -m unittest webapp.tests.test_build_nfo -v`
Expected: PASS

### Task 3: Update config and docs

**Files:**
- Modify: `config.yaml`
- Modify: `README.md`
- Modify: `README-CN.md`

**Step 1: Add the new config key**

```yaml
completed-root-folder: "/downloads/completed"
```

**Step 2: Document move target behavior**

Mention that `build_nfo.py` moves album folders into `completed-root-folder/<artist>/<album>` after NFO generation.

### Task 4: Full verification

**Files:**
- Test: `webapp/tests/test_app.py`
- Test: `webapp/tests/test_build_nfo.py`

**Step 1: Run all tests**

Run: `.venv/bin/python -m unittest webapp.tests.test_app webapp.tests.test_build_nfo -v`
Expected: PASS

**Step 2: Run compile check**

Run: `.venv/bin/python -m py_compile webapp/app.py webapp/tests/test_app.py webapp/tests/test_build_nfo.py tools/build_nfo.py`
Expected: PASS
