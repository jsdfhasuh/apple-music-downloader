#!/usr/bin/env python3
import argparse
import sqlite3
import sys
from contextlib import closing
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from webapp.app import ArtistSubscriptionStore, DownloadHistoryStore, normalizeStrictStorefront  # noqa: E402


def countSubscriptionsToMigrate(dbPath: Path, storefront: str) -> int:
  with closing(sqlite3.connect(dbPath)) as connection:
    row = connection.execute(
      "SELECT COUNT(*) FROM artist_subscriptions WHERE storefront != ?",
      (storefront,),
    ).fetchone()
  return int(row[0] if row else 0)


def countFailedHistoryRows(dbPath: Path, sourceStorefront: str, rows: list[dict[str, str]]) -> int:
  if not rows:
    return 0
  urls = {row["album_url"] for row in rows if row.get("album_url")}
  albumIds = {row["album_id"] for row in rows if row.get("album_id")}
  urlPrefix = f"https://music.apple.com/{sourceStorefront}/%"
  count = 0
  with closing(sqlite3.connect(dbPath)) as connection:
    for url in urls:
      row = connection.execute(
        "SELECT COUNT(*) FROM downloads WHERE url = ? AND status = 'failed' AND source = 'subscription'",
        (url,),
      ).fetchone()
      count += int(row[0] if row else 0)
    for albumId in albumIds:
      row = connection.execute(
        """
        SELECT COUNT(*)
        FROM downloads
        WHERE album_id = ?
          AND status = 'failed'
          AND source = 'subscription'
          AND url LIKE ?
          AND url NOT IN ({})
        """.format(",".join("?" for _ in urls) if urls else "''"),
        (albumId, urlPrefix, *sorted(urls)),
      ).fetchone()
      count += int(row[0] if row else 0)
  return count


def main() -> int:
  parser = argparse.ArgumentParser(description="Migrate artist subscriptions to a target storefront and remove failed source-storefront records.")
  parser.add_argument("db_path", help="Path to downloads.db")
  parser.add_argument("--target-storefront", default="cn", help="Target subscription storefront, default: cn")
  parser.add_argument("--source-storefront", default="hk", help="Failed source storefront to clean, default: hk")
  parser.add_argument("--apply", action="store_true", help="Apply changes. Without this flag only prints a dry run.")
  args = parser.parse_args()

  dbPath = Path(args.db_path)
  if not dbPath.is_file():
    print(f"database not found: {dbPath}", file=sys.stderr)
    return 2

  try:
    targetStorefront = normalizeStrictStorefront(args.target_storefront, "--target-storefront")
    sourceStorefront = normalizeStrictStorefront(args.source_storefront, "--source-storefront")
  except ValueError as exc:
    print(str(exc), file=sys.stderr)
    return 2
  subscriptionStore = ArtistSubscriptionStore(str(dbPath))
  failedRows = subscriptionStore.listFailedSeenAlbumsForStorefront(sourceStorefront)
  subscriptionsToMigrate = countSubscriptionsToMigrate(dbPath, targetStorefront)
  failedHistoryCount = countFailedHistoryRows(dbPath, sourceStorefront, failedRows)

  print(f"database: {dbPath}")
  print(f"target storefront: {targetStorefront}")
  print(f"source failed storefront: {sourceStorefront}")
  print(f"subscriptions to migrate: {subscriptionsToMigrate}")
  print(f"failed subscription rows to delete: {len(failedRows)}")
  print(f"failed history rows to delete: {failedHistoryCount}")

  if not args.apply:
    print("dry run only; pass --apply to change the database")
    return 0

  historyStore = DownloadHistoryStore(str(dbPath))
  migrationResult = subscriptionStore.migrateSubscriptionsToStorefront(targetStorefront)
  deletedRows = subscriptionStore.deleteFailedSeenAlbumsForStorefront(sourceStorefront)
  deletedHistoryCount = historyStore.deleteFailedSubscriptionRecordsForStorefront(
    sourceStorefront,
    [row["album_id"] for row in deletedRows],
    [row["album_url"] for row in deletedRows],
  )
  print(f"migration: {migrationResult}")
  print(f"deleted failed subscription rows: {len(deletedRows)}")
  print(f"deleted failed history rows: {deletedHistoryCount}")
  return 0


if __name__ == "__main__":
  raise SystemExit(main())
