import json
import re
from dataclasses import dataclass
from typing import Any
from urllib import parse, request

from webapp.config_loader import getConfigValue, resolveConfigPath


DEFAULT_AUTHORIZATION_TOKEN = "your-authorization-token"
AMP_API_BASE_URL = "https://amp-api.music.apple.com/v1/catalog"
APPLE_MUSIC_URL_RE = re.compile(r"https://music\.apple\.com/([a-z]{2})/([a-z-]+)/[^/\s?#]+/(\d+)")
JWT_RE = re.compile(r"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)")
SCRIPT_SRC_RE = re.compile(r"<script[^>]+src=[\"']([^\"']+\.js)[\"']", re.IGNORECASE)


@dataclass(frozen=True)
class AppleMusicArtist:
  artistId: str
  storefront: str
  name: str
  url: str


@dataclass(frozen=True)
class AppleMusicAlbum:
  albumId: str
  name: str
  url: str
  releaseDate: str = ""


def parseAppleMusicUrl(url: str) -> tuple[str, str, str] | None:
  match = APPLE_MUSIC_URL_RE.search(url.strip())
  if match is None:
    return None
  storefront, resourceType, resourceId = match.groups()
  return storefront, resourceType, resourceId


def parseArtistUrl(url: str) -> tuple[str, str] | None:
  parsed = parseAppleMusicUrl(url)
  if parsed is None:
    return None
  storefront, resourceType, artistId = parsed
  if resourceType != "artist":
    return None
  return storefront, artistId


def parseAlbumIdFromUrl(url: str) -> str:
  parsed = parseAppleMusicUrl(url)
  if parsed is None:
    return ""
  query = parse.parse_qs(parse.urlsplit(url).query)
  if query.get("i"):
    return ""
  _, resourceType, albumId = parsed
  if resourceType != "album":
    return ""
  return albumId


def normalizeStorefront(rawValue: str | None) -> str:
  storefront = (rawValue or "us").strip().lower()
  if len(storefront) != 2 or not storefront.isalpha():
    return "us"
  return storefront


def normalizeDeveloperToken(rawValue: str | None) -> str:
  token = (rawValue or "").strip()
  if not token or token == DEFAULT_AUTHORIZATION_TOKEN:
    return ""
  if token.lower().startswith("bearer "):
    token = token[7:].strip()
  return token


class AppleMusicClient:
  def __init__(
    self,
    storefront: str | None = None,
    language: str | None = None,
    authorizationToken: str | None = None,
  ) -> None:
    configPath = resolveConfigPath()
    self.storefront = normalizeStorefront(storefront or getConfigValue(configPath, "storefront"))
    self.language = (language or getConfigValue(configPath, "language") or "en-US").strip() or "en-US"
    self._authorizationToken = normalizeDeveloperToken(
      authorizationToken if authorizationToken is not None else getConfigValue(configPath, "authorization-token")
    )

  @property
  def authorizationToken(self) -> str:
    if not self._authorizationToken:
      self._authorizationToken = fetchAppleMusicBearerToken(self.storefront)
    return self._authorizationToken

  def searchArtists(self, term: str, limit: int = 10) -> list[AppleMusicArtist]:
    payload = self._getJson(
      f"{AMP_API_BASE_URL}/{self.storefront}/search",
      {
        "term": term,
        "types": "artists",
        "limit": str(limit),
        "l": self.language,
      },
    )
    artists = payload.get("results", {}).get("artists", {}).get("data", [])
    return [
      self._parseArtist(item, self.storefront)
      for item in artists
      if isinstance(item, dict)
    ]

  def getArtist(self, storefront: str, artistId: str) -> AppleMusicArtist:
    normalizedStorefront = normalizeStorefront(storefront)
    payload = self._getJson(
      f"{AMP_API_BASE_URL}/{normalizedStorefront}/artists/{parse.quote(artistId)}",
      {"l": self.language},
    )
    artists = payload.get("data", [])
    if not isinstance(artists, list) or not artists:
      raise ValueError("artist not found")
    first = artists[0]
    if not isinstance(first, dict):
      raise ValueError("artist not found")
    return self._parseArtist(first, normalizedStorefront)

  def listArtistAlbums(self, storefront: str, artistId: str, limit: int = 100) -> list[AppleMusicAlbum]:
    normalizedStorefront = normalizeStorefront(storefront)
    albums: list[AppleMusicAlbum] = []
    offset = 0
    while True:
      payload = self._getJson(
        f"{AMP_API_BASE_URL}/{normalizedStorefront}/artists/{parse.quote(artistId)}/albums",
        {
          "limit": str(limit),
          "offset": str(offset),
          "l": self.language,
        },
      )
      data = payload.get("data", [])
      if not isinstance(data, list):
        break
      pageAlbums = [
        self._parseAlbum(item)
        for item in data
        if isinstance(item, dict) and str(item.get("type", "")) == "albums"
      ]
      albums.extend(pageAlbums)
      if len(data) < limit:
        break
      offset += limit
    return albums

  def _getJson(self, url: str, query: dict[str, str]) -> dict[str, Any]:
    fullUrl = f"{url}?{parse.urlencode(query)}" if query else url
    httpRequest = request.Request(
      fullUrl,
      headers={
        "Authorization": f"Bearer {self.authorizationToken}",
        "Origin": "https://music.apple.com",
        "Referer": f"https://music.apple.com/{self.storefront}/browse",
        "User-Agent": "Mozilla/5.0 AppleMusicDownloaderWebapp/1.0",
      },
    )
    with request.urlopen(httpRequest, timeout=30) as response:
      body = response.read().decode("utf-8")
    parsed = json.loads(body)
    if not isinstance(parsed, dict):
      raise ValueError(f"unexpected Apple Music API response from {fullUrl}")
    return parsed

  def _parseArtist(self, item: dict[str, Any], storefront: str) -> AppleMusicArtist:
    attributes = item.get("attributes", {})
    if not isinstance(attributes, dict):
      attributes = {}
    artistId = str(item.get("id", "")).strip()
    name = str(attributes.get("name", "")).strip() or artistId
    url = str(attributes.get("url", "")).strip()
    if not url:
      slug = parse.quote(name.lower().replace(" ", "-"))
      url = f"https://music.apple.com/{storefront}/artist/{slug}/{artistId}"
    return AppleMusicArtist(artistId=artistId, storefront=storefront, name=name, url=url)

  def _parseAlbum(self, item: dict[str, Any]) -> AppleMusicAlbum:
    attributes = item.get("attributes", {})
    if not isinstance(attributes, dict):
      attributes = {}
    albumId = str(item.get("id", "")).strip()
    name = str(attributes.get("name", "")).strip() or albumId
    url = str(attributes.get("url", "")).strip()
    releaseDate = str(attributes.get("releaseDate", "")).strip()
    return AppleMusicAlbum(albumId=albumId, name=name, url=url, releaseDate=releaseDate)


def fetchAppleMusicBearerToken(storefront: str = "us") -> str:
  browserUrl = f"https://music.apple.com/{normalizeStorefront(storefront)}/browse"
  html = readTextUrl(browserUrl)
  scriptUrls = [
    parse.urljoin(browserUrl, scriptSrc)
    for scriptSrc in SCRIPT_SRC_RE.findall(html)
  ]
  for scriptUrl in scriptUrls[:20]:
    try:
      script = readTextUrl(scriptUrl)
    except Exception:  # noqa: BLE001
      continue
    match = JWT_RE.search(script)
    if match is not None:
      return match.group(1)
  match = JWT_RE.search(html)
  if match is not None:
    return match.group(1)
  raise ValueError("failed to extract Apple Music bearer token")


def readTextUrl(url: str) -> str:
  httpRequest = request.Request(
    url,
    headers={"User-Agent": "Mozilla/5.0 AppleMusicDownloaderWebapp/1.0"},
  )
  with request.urlopen(httpRequest, timeout=30) as response:
    return response.read().decode("utf-8", errors="replace")
