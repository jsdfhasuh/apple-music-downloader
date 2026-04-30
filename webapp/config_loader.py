import os
from pathlib import Path


def getConfigValue(configPath: Path, key: str) -> str | None:
  if not configPath.is_file():
    return None
  prefix = f"{key}:"
  for rawLine in configPath.read_text(encoding="utf-8").splitlines():
    line = rawLine.split("#", 1)[0].strip()
    if not line or not line.startswith(prefix):
      continue
    value = line[len(prefix):].strip()
    if not value:
      return None
    return value.strip('"\'')
  return None


def resolveConfigPath(explicitPath: Path | None = None) -> Path:
  if explicitPath is not None:
    return explicitPath

  envPath = os.environ.get("WEBAPP_CONFIG_PATH", "").strip()
  if envPath:
    return Path(envPath).expanduser()

  webappDir = Path(__file__).resolve().parent
  for candidate in (
    webappDir.parent / "config.yaml",
    webappDir / "config.yaml",
    webappDir / "config.example.yaml",
  ):
    if candidate.is_file():
      return candidate

  return webappDir / "config.yaml"
