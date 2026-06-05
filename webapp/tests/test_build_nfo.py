import tempfile
import unittest
from pathlib import Path
import sys
import os
import subprocess
import logging


TOOLS_PATH = Path(__file__).resolve().parents[2] / "tools"
if str(TOOLS_PATH) not in sys.path:
  sys.path.insert(0, str(TOOLS_PATH))

from build_nfo import getCompletedRoot  # noqa: E402
from build_nfo import getAlbumArtistFallback  # noqa: E402
from build_nfo import buildTagMapFromMutagen  # noqa: E402
from build_nfo import get_real_artist  # noqa: E402
from build_nfo import isLockDataEnabled  # noqa: E402
from build_nfo import finalize_album_output  # noqa: E402


class BuildNfoConfigTest(unittest.TestCase):
  def testGetCompletedRootFallsBackToDownloadsCompleted(self):
    result = getCompletedRoot(Path("/tmp/does-not-exist.yaml"))

    self.assertEqual(result, Path("/downloads/completed"))

  def testGetCompletedRootReadsConfigValue(self):
    with tempfile.TemporaryDirectory() as tempDir:
      configPath = Path(tempDir) / "config.yaml"
      configPath.write_text('completed-root-folder: "/music/completed"\n', encoding="utf-8")

      result = getCompletedRoot(configPath)

    self.assertEqual(result, Path("/music/completed"))

  def testGetCompletedRootReadsWebappConfigPathFromEnv(self):
    originalConfigPath = os.environ.get("WEBAPP_CONFIG_PATH")
    try:
      with tempfile.TemporaryDirectory() as tempDir:
        configPath = Path(tempDir) / "webapp-config.yaml"
        configPath.write_text('completed-root-folder: "/music/from-env"\n', encoding="utf-8")
        os.environ["WEBAPP_CONFIG_PATH"] = str(configPath)

        result = getCompletedRoot(None)

      self.assertEqual(result, Path("/music/from-env"))
    finally:
      if originalConfigPath is None:
        os.environ.pop("WEBAPP_CONFIG_PATH", None)
      else:
        os.environ["WEBAPP_CONFIG_PATH"] = originalConfigPath

  def testGetAlbumArtistFallbackUsesFolderNameWhenTagsMissing(self):
    result = getAlbumArtistFallback([], [], Path("/downloads/ALAC/宋雨琦/Motivation - Single"))

    self.assertEqual(result, "宋雨琦")

  def testGetAlbumArtistFallbackIgnoresQuestionMarks(self):
    result = getAlbumArtistFallback([""], ["???"], Path("/downloads/ALAC/宋雨琦/Motivation - Single"))

    self.assertEqual(result, "宋雨琦")

  def testBuildTagMapFromMutagenKeepsUnicodeArtist(self):
    result = buildTagMapFromMutagen({
      "artist": ["宋雨琦"],
      "albumartist": ["宋雨琦"],
      "tracknumber": ["1/3"],
      "discnumber": ["1/1"],
      "album": ["Motivation - Single"],
      "title": ["M.O."]
    })

    self.assertEqual(result["ARTIST"], "宋雨琦")
    self.assertEqual(result["ALBUMARTIST"], "宋雨琦")
    self.assertEqual(result["TRACKNUMBER"], "1")

  def testGetRealArtistFallsBackToRawArtistName(self):
    result = get_real_artist({"IVE": 6})

    self.assertEqual(result, ["IVE"])

  def testIsLockDataEnabledTreatsFalseStringAsUnlocked(self):
    self.assertFalse(isLockDataEnabled("False"))
    self.assertFalse(isLockDataEnabled(False))
    self.assertTrue(isLockDataEnabled("true"))

  def testBuildNfoSupportsModuleExecution(self):
    result = subprocess.run(
      [
        sys.executable,
        "-m",
        "tools.build_nfo",
        "/tmp/does-not-exist",
      ],
      cwd=str(Path(__file__).resolve().parents[2]),
      capture_output=True,
      text=True,
    )

    self.assertNotIn("ModuleNotFoundError", result.stdout)
    self.assertNotIn("ModuleNotFoundError", result.stderr)

  def testFinalizeAlbumOutputMergesExistingCompletedAlbum(self):
    logger = logging.getLogger("test-build-nfo")
    logger.addHandler(logging.NullHandler())

    with tempfile.TemporaryDirectory() as tempDir:
      root = Path(tempDir)
      source = root / "ALAC" / "IVE" / "LUCID DREAM - EP"
      source.mkdir(parents=True)
      (source / "1-1 LUCID DREAM.flac").write_text("new flac", encoding="utf-8")
      (source / "album.nfo").write_text("new nfo", encoding="utf-8")

      completedRoot = root / "completed"
      target = completedRoot / "IVE" / "LUCID DREAM - EP"
      target.mkdir(parents=True)
      (target / "1-1 LUCID DREAM.flac").write_text("old flac", encoding="utf-8")
      (target / "album.nfo").write_text("old nfo", encoding="utf-8")

      result = finalize_album_output(
        source_folder=source,
        completed_root=completedRoot,
        album_artist="IVE",
        album_name="LUCID DREAM - EP",
        file_disc_map={},
        logger=logger,
      )

      self.assertTrue(result)
      self.assertFalse(source.exists())
      self.assertEqual((target / "1-1 LUCID DREAM.flac").read_text(encoding="utf-8"), "new flac")
      self.assertEqual((target / "album.nfo").read_text(encoding="utf-8"), "new nfo")


if __name__ == "__main__":
  unittest.main()
