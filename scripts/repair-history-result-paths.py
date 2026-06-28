#!/usr/bin/env python3
import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


DEFAULT_DB_PATH = Path("data/downloads.db")
DEFAULT_COMPLETED_ROOT = Path("/downloads/completed")
DOWNLOAD_FORMAT_DIRS = {"ALAC", "AAC", "Atmos", "ATMOS"}
KNOWN_DISC_DIR_NAMES = tuple(f"CD{index}" for index in range(1, 100))


def parseArgs() -> argparse.Namespace:
  parser = argparse.ArgumentParser(
    description="Repair completed download history paths without recursively scanning the completed folder."
  )
  parser.add_argument("--db", default=str(DEFAULT_DB_PATH), help="downloads.db path")
  parser.add_argument("--completed-root", default=str(DEFAULT_COMPLETED_ROOT), help="completed music root")
  parser.add_argument("--write", action="store_true", help="write repaired result_json values")
  parser.add_argument(
    "--touch-updated-at",
    action="store_true",
    help="also refresh updated_at for repaired records; default preserves history ordering",
  )
  parser.add_argument("--verbose", action="store_true", help="print repaired URL and path counts")
  return parser.parse_args()


def parseResult(rawResult: str) -> list[dict[str, Any]]:
  try:
    parsed = json.loads(rawResult)
  except json.JSONDecodeError:
    return []
  if not isinstance(parsed, list):
    return []
  return [item for item in parsed if isinstance(item, dict)]


def candidateCompletedPath(originalPath: Path, completedRoot: Path) -> Path | None:
  parts = originalPath.parts
  for index, part in enumerate(parts):
    if part not in DOWNLOAD_FORMAT_DIRS or index + 3 >= len(parts):
      continue
    artist = parts[index + 1]
    album = parts[index + 2]
    relativeParts = parts[index + 3:]
    if not relativeParts:
      return None
    return completedRoot / artist / album / Path(*relativeParts)
  return None


def findKnownCompletedPath(originalPath: Path, completedRoot: Path) -> Path | None:
  if originalPath.is_file():
    return originalPath

  directCandidate = candidateCompletedPath(originalPath, completedRoot)
  if directCandidate is None:
    return None
  if directCandidate.is_file():
    return directCandidate

  albumDir = directCandidate.parent
  filename = directCandidate.name
  for discDirName in KNOWN_DISC_DIR_NAMES:
    discCandidate = albumDir / discDirName / filename
    if discCandidate.is_file():
      return discCandidate
  return None


def repairResult(rawResult: str, completedRoot: Path) -> tuple[str, int]:
  result = parseResult(rawResult)
  if not result:
    return rawResult, 0

  changedCount = 0
  repaired: list[dict[str, Any]] = []
  for item in result:
    rawPath = str(item.get("path", "") or "").strip()
    if not rawPath:
      repaired.append(item)
      continue
    repairedPath = findKnownCompletedPath(Path(rawPath), completedRoot)
    if repairedPath is None or str(repairedPath) == rawPath:
      repaired.append(item)
      continue
    repaired.append({**item, "path": str(repairedPath)})
    changedCount += 1

  if changedCount == 0:
    return rawResult, 0
  return json.dumps(repaired, ensure_ascii=False), changedCount


def main() -> int:
  args = parseArgs()
  dbPath = Path(args.db)
  completedRoot = Path(args.completed_root)
  if not dbPath.is_file():
    print(f"database not found: {dbPath}")
    return 1

  connection = sqlite3.connect(dbPath)
  connection.row_factory = sqlite3.Row
  try:
    rows = connection.execute(
      """
      SELECT url, result_json
      FROM downloads
      WHERE (status = 'completed' OR ever_completed = 1)
        AND result_json NOT IN ('', '[]')
      """
    ).fetchall()

    repairedRecords = 0
    repairedPaths = 0
    for row in rows:
      repairedResult, changedCount = repairResult(str(row["result_json"]), completedRoot)
      if changedCount == 0:
        continue
      repairedRecords += 1
      repairedPaths += changedCount
      if args.verbose:
        print(f"repairable record: url={row['url']} changed_paths={changedCount}")
      if args.write:
        if args.touch_updated_at:
          connection.execute(
            "UPDATE downloads SET result_json = ?, updated_at = CURRENT_TIMESTAMP WHERE url = ?",
            (repairedResult, row["url"]),
          )
        else:
          connection.execute(
            "UPDATE downloads SET result_json = ? WHERE url = ?",
            (repairedResult, row["url"]),
          )
    if args.write:
      connection.commit()
    print(
      f"{'updated' if args.write else 'dry-run'}: "
      f"records={repairedRecords} paths={repairedPaths}"
    )
  finally:
    connection.close()
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
