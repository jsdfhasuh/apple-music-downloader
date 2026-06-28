#!/usr/bin/env python3
"""
Build NFO file from FLAC metadata
用于从 FLAC 文件元数据生成 NFO 文件

Usage:
    python build_nfo.py <input_folder>
    python build_nfo.py /path/to/album
"""

import os
import re
import shutil
import subprocess
import sys
import logging
from pathlib import Path
from datetime import datetime

from webapp.config_loader import getConfigValue, resolveConfigPath

# import spotify_main
try:
    from .nfo import NfoHandler
except ImportError:
    from nfo import NfoHandler

try:
    from mutagen.flac import FLAC as MutagenFlac
except ImportError:
    MutagenFlac = None

try:
    from .lastfm import MyLastfm_instance
except ImportError:
    try:
        from lastfm import MyLastfm_instance
    except ImportError:
        class _LastfmFallback:
            def get_artist_info(self, artist):
                return []

        MyLastfm_instance = _LastfmFallback()

# 配置常量
def get_logs_dir() -> Path:
    configured = os.environ.get("WEBAPP_LOGS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    container_logs_dir = Path("/app/data/logs")
    if container_logs_dir.parent.exists():
        return container_logs_dir
    return Path(__file__).parent.parent / "logs"


LOGS_DIR = get_logs_dir()
DEFAULT_DOWNLOADS_DIR_NAME = "downloads"
DEFAULT_COMPLETED_ROOT = Path("/downloads/completed")
COMPLETED_ALBUM_DIR_PREFIX = "AMD_COMPLETED_ALBUM_DIR="


def getCompletedRoot(configPath: Path | None) -> Path:
    configuredPath = getConfigValue(resolveConfigPath(configPath), "completed-root-folder")
    if not configuredPath:
        return DEFAULT_COMPLETED_ROOT
    return Path(configuredPath).expanduser()


def setup_logger(folder_name: str) -> logging.Logger:
    """配置日志系统

    Args:
        folder_name: 文件夹名称（用于生成日志文件名）

    Returns:
        logging.Logger: 配置好的日志记录器
    """
    # 创建 logs 目录
    LOGS_DIR.mkdir(exist_ok=True)

    # 生成日志文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    log_file = LOGS_DIR / f"nfo_{folder_name}_{timestamp}.log"

    # 配置日志格式
    log_format = '%(asctime)s | %(levelname)-8s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # 创建日志记录器
    logger = logging.getLogger('build_nfo')
    logger.setLevel(logging.DEBUG)

    # 清除已有的处理器
    logger.handlers.clear()

    # 文件处理器 - 记录所有日志
    file_handler = logging.FileHandler(log_file, encoding='utf-8', mode='a')
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(file_handler)

    # 控制台处理器 - 只显示 INFO 及以上级别
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format, date_format))
    logger.addHandler(console_handler)

    # 记录启动信息
    logger.info("=" * 60)
    logger.info("Build NFO from FLAC - 启动")
    logger.info(f"日志文件: {log_file}")
    logger.info("=" * 60)

    return logger


def decode_tag_bytes(raw_bytes):
    # Try common encodings for music tags; fall back to replacement to avoid crashes.
    for encoding in ("utf-8", "gb18030", "big5"):
        try:
            return raw_bytes.decode(encoding).rstrip()
        except UnicodeDecodeError:
            continue
    return raw_bytes.decode("utf-8", errors="replace").rstrip()


def extract_year(s):
    """提取形如20151201这种字符串的前4位数字作为年份"""
    if not s:
        return None
    match = re.match(r"^(\d{4})\d{4}", s)
    if match:
        return match.group(1)
    match = re.match(r"^(\d{4})-\d{2}", s)
    if match:
        return match.group(1)
    match = re.match(r"^(\d{4})", s)
    if match:
        return match.group(1)
    # '10 Oct 2023, 14:42'
    match = re.match(r"^\d{1,2}\s\w+\s(\d{4})", s)
    if match:
        return match.group(1)
    return None


def extract_artist_name(artist):
    """提取所有括号内的内容并拼接"""
    if "(" in artist and ")" in artist:
        # 查找所有括号内的内容
        artist = artist.replace("CV:", "").replace("CV.", "")
        results = re.findall(r'\(([^)]+)\)', artist)
        if results:
            # 拼接所有括号内容，用空格分隔
            new_result = []
            for result in results:
                new_name = result.replace(' ', '')
                new_result.append(new_name)
            if len(results) == 1:
                return new_result[0].strip()
            # 有多个括号内容时，用逗号连接
            else:
                cleaned_results = [result.strip() for result in new_result]
                return ",".join(cleaned_results)
    return artist.strip()


def get_real_artist(raw_artists):
    new_artists = {}
    for artist in raw_artists:
        name = ""
        if not name:
            names = MyLastfm_instance.get_artist_info(artist)
            if isinstance(names, list) and names:
                name = names[0].get("name", "")
        if not name:
            name = artist
        if name in new_artists:
            new_artists[name] += raw_artists[artist]
        else:
            new_artists[name] = raw_artists[artist]
    new_artists = dict(
        sorted(new_artists.items(), key=lambda item: item[1], reverse=True)
    )
    return list(new_artists.keys())


def getFirstTagValue(rawTags: dict[object, object], key: str) -> str:
    value = rawTags.get(key.lower()) or rawTags.get(key.upper()) or rawTags.get(key)
    if isinstance(value, list) and value:
        return str(value[0]).strip()
    if value is None:
        return ""
    return str(value).strip()


def buildTagMapFromMutagen(rawTags: dict[object, object]) -> dict[str, str]:
    tags: dict[str, str] = {}
    for tag in (
        'TITLE',
        'ALBUM',
        'ARTIST',
        'TRACKNUMBER',
        'GENRE',
        'COMMENT',
        'DATE',
        'DISCNUMBER',
        'ALBUMARTIST',
    ):
        temp = getFirstTagValue(rawTags, tag)
        if "ARTIST" in tag:
            temp = extract_artist_name(temp.replace('\n', ','))
        if 'NUMBER' in tag:
            result = re.match(r'(\d+)/(\d+)', temp)
            if result:
                temp = result.group(1)
        if 'NUMBER' in tag and not temp:
            temp = '1'
        tags[tag] = temp
    return tags


def get_flac_tags(flac, logger):
    if MutagenFlac is not None:
        try:
            audio = MutagenFlac(flac)
            tags = buildTagMapFromMutagen(audio)
            trackNumber = tags.get('TRACKNUMBER', '')
            if not trackNumber.isdigit():
                match = re.search(r'(\d+)-(\d+)', os.path.basename(flac))
                if match:
                    tags['TRACKNUMBER'] = match.group(2)
                else:
                    tags['TRACKNUMBER'] = '1'
            return tags
        except Exception as e:
            logger.warning(f"mutagen 读取标签失败，回退 metaflac: {str(e)}")
    tags = {}
    for tag in (
        'TITLE',
        'ALBUM',
        'ARTIST',
        'TRACKNUMBER',
        'GENRE',
        'COMMENT',
        'DATE',
        'DISCNUMBER',
        'ALBUMARTIST',
    ):
        # 不使用shell=True，更安全的命令构建方式
        tagcommand = ['metaflac', f'--show-tag={tag}', flac]
        tagcommand1 = f"metaflac --export-tags-to=- {flac}"
        logger.debug(f"tagcommand1 :{tagcommand1}")
        # 设置环境变量确保UTF-8编码
        env = os.environ.copy()
        env['LANG'] = 'zh_CN.UTF-8'
        env['LC_ALL'] = 'zh_CN.UTF-8'

        try:
            process = subprocess.Popen(
                tagcommand,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
            )
            stdout_bytes, stderr_bytes = process.communicate()

            if process.returncode != 0:
                logger.warning(f"获取标签 {tag} 失败")
                temp = ""
            else:
                temp = decode_tag_bytes(stdout_bytes)

                temp = re.sub(f'{tag}=', '', temp, flags=re.IGNORECASE)

                if "ARTIST" in tag:
                    temp = extract_artist_name(temp.replace('\n', ','))

                if 'NUMBER' in tag:
                    result = re.match(r'(\d+)/(\d+)', temp)
                    if result:
                        temp = result.group(1)
                if 'NUMBER' in tag and not temp:
                    temp = "1"
                if 'TRACKNUMBER' in tag:
                    pass
            tags[tag] = temp
            del temp
        except Exception as e:
            logger.error(f"处理标签 {tag} 时出错: {str(e)}")
            tags[tag] = ""
    else:
        Tracknumber = tags.get('TRACKNUMBER', '')
        if not Tracknumber.isdigit():
            match = re.search(r'(\d+)-(\d+)', os.path.basename(flac))
            if match:
                tags['TRACKNUMBER'] = int(match.group(2))
            else:
                tags['TRACKNUMBER'] = '1'
    return tags


def split_artist(raw_artists):
    # &符号分割艺术家,with,feat.,/
    input_artists = raw_artists
    split_flag = [
        ',',
        'with',
        'feat.',
        'Feat.',
        '/',
        '、',
        '&',
        'and',
        'vs.',
        'x',
        'vs',
        'VS',
        ';',
    ]
    for flag in split_flag:
        if isinstance(raw_artists, str):
            for flag in split_flag:
                if flag in raw_artists:
                    raw_artists = raw_artists.split(flag)
        elif isinstance(raw_artists, list):
            for element in raw_artists.copy():
                if flag in element:
                    raw_artists.remove(element)
                    raw_artists.extend(element.split(flag))
    else:
        if isinstance(raw_artists, list):
            for element in raw_artists.copy():
                if element == ' ' or element == '':
                    raw_artists.remove(element)
                else:
                    raw_artists[raw_artists.index(element)] = element.strip()
        else:
            raw_artists = [raw_artists]
        return raw_artists


def get_album_name(raw_album_name):
    # 去除disc
    if re.search(r'Disc ?\d', raw_album_name, re.IGNORECASE):
        raw_album_name = re.sub(r'Disc ?\d', '', raw_album_name, flags=re.IGNORECASE)
    # 去除多余的括号
    raw_album_name = re.sub(r'\[\]', '', raw_album_name)
    return raw_album_name.strip()


def scan_folder_for_flac(input_folder):
    flac_files = []
    for root, dirs, files in os.walk(input_folder):
        for file in files:
            if file.lower().endswith('.flac'):
                flac_files.append(os.path.join(root, file))
    return flac_files


def get_album_output_folder(track_path: Path) -> Path:
    if re.match(r'^CD\d+$', track_path.parent.name, re.IGNORECASE):
        return track_path.parent.parent
    return track_path.parent


def reform_flac_to_flac_dict(flac_files, input_folder):
    flac_dict = {}
    for flac in flac_files:
        temp_path = input_folder
        last_point = ""
        relative_path = os.path.relpath(flac, input_folder)
        paths = relative_path.split(os.sep)
        if len(paths) > 1 and re.match(r'^CD\d+$', paths[-2], re.IGNORECASE):
            paths = paths[:-2] + [paths[-1]]
        flac_name = paths[-1]
        if len(paths) == 1:
            root_key = os.path.basename(os.path.normpath(input_folder)) or "root"
            if root_key not in flac_dict:
                flac_dict[root_key] = []
            flac_dict[root_key].append({'file_name': flac_name, 'full_path': flac})
            continue
        for index in range(len(paths) - 1):  # 排除文件名部分
            folder_name = paths[index]
            if index == len(paths) - 2:  # 最后一层目录
                if last_point == "":
                    # 如果是第一层也是最后一层
                    if folder_name not in flac_dict:
                        flac_dict[folder_name] = []
                    last_point = flac_dict[folder_name]
                else:
                    # 如果不是第一层
                    if folder_name not in last_point:
                        last_point[folder_name] = []
                    last_point = last_point[folder_name]
                break
            # 非最后一层目录
            if folder_name not in flac_dict:
                flac_dict[folder_name] = {}
            if last_point == "":
                last_point = flac_dict[folder_name]
            else:
                if folder_name not in last_point:
                    last_point[folder_name] = {}
                last_point = last_point[folder_name]
        last_point.append({'file_name': flac_name, 'full_path': flac})
    return flac_dict


def get_flac_file_point(
    flac_dict,
):
    final_file_dict = {}

    def walk(node, path_parts):
        for key, value in node.items():
            if isinstance(value, dict):
                walk(value, path_parts + [key])
            elif isinstance(value, list):
                album_parts = path_parts + [key]
                album_rel_path = os.path.join(*album_parts)
                # 用完整相对路径做 key，避免不同路径下同名专辑互相覆盖
                final_file_dict[album_rel_path] = {
                    "album_name": key,
                    "files": value,
                    "path": album_rel_path,
                }

    walk(flac_dict, [])
    return final_file_dict


def resolve_input_folder(input_folder: str, script_path: Path) -> Path:
    raw_input = input_folder.strip()
    if raw_input:
        return Path(raw_input).expanduser()
    project_root = script_path.resolve().parent.parent
    return project_root / DEFAULT_DOWNLOADS_DIR_NAME


def build_argument_parser() -> 'argparse.ArgumentParser':
    import argparse

    parser = argparse.ArgumentParser(description='Build NFO file from FLAC metadata')
    parser.add_argument('input_folder', nargs='?', default='', help='FLAC files folder path')
    parser.add_argument(
        '--organize-only',
        action='store_true',
        help='Organize multi-disc album layout without moving to completed',
    )
    return parser


def initialize_nfo_data(local_data: dict | None) -> dict:
    if local_data and isinstance(local_data.get('album'), dict):
        album_data = dict(local_data['album'])
        if not isinstance(album_data.get('track'), list):
            album_data['track'] = []
        album_data.setdefault('lock_data', False)
        return {'album': album_data}
    return {'album': {'lock_data': False, 'track': []}}


def isLockDataEnabled(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return bool(value)


def sanitize_path_component(value: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*]', '_', value).strip()
    cleaned = cleaned.rstrip('. ')
    return cleaned or "Unknown"


def normalizeArtistValues(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        cleaned = str(value).strip().strip(',')
        if not cleaned or cleaned == '???':
            continue
        normalized.append(cleaned)
    return normalized


def getAlbumArtistFallback(
    realArtists: list[str],
    rawAlbumArtists: list[str],
    albumDir: Path,
) -> str:
    normalizedRealArtists = normalizeArtistValues(realArtists)
    if normalizedRealArtists:
        return normalizedRealArtists[0]
    normalizedAlbumArtists = normalizeArtistValues(rawAlbumArtists)
    if normalizedAlbumArtists:
        return ', '.join(normalizedAlbumArtists)
    parentArtist = albumDir.parent.name.strip()
    if parentArtist:
        return parentArtist
    return 'Unknown'


def build_completed_album_target(
    completed_root: Path,
    album_artist: str,
    album_name: str,
) -> Path:
    safe_artist = sanitize_path_component(album_artist)
    safe_album = sanitize_path_component(album_name)
    return completed_root / safe_artist / safe_album


def get_disc_number(raw_disc_number: str) -> str | None:
    match = re.search(r'(\d+)', str(raw_disc_number).strip())
    if not match:
        return None
    return match.group(1)


def get_album_related_files(track_path: Path) -> list[Path]:
    return [
        candidate
        for candidate in track_path.parent.iterdir()
        if candidate.is_file() and candidate.stem == track_path.stem
    ]


def organize_album_by_disc(
    album_dir: Path,
    file_disc_map: dict[str, str],
    logger: logging.Logger,
) -> bool:
    normalized_disc_map: dict[Path, str] = {}
    disc_numbers: set[str] = set()

    for raw_path, raw_disc_number in file_disc_map.items():
        disc_number = get_disc_number(raw_disc_number)
        track_path = Path(raw_path)
        if not disc_number or not track_path.exists() or track_path.parent != album_dir:
            continue
        normalized_disc_map[track_path] = disc_number
        disc_numbers.add(disc_number)

    if len(disc_numbers) <= 1:
        return False

    moved_files: set[Path] = set()
    for track_path, disc_number in normalized_disc_map.items():
        target_dir = album_dir / f'CD{disc_number}'
        target_dir.mkdir(exist_ok=True)
        for related_path in get_album_related_files(track_path):
            if related_path in moved_files:
                continue
            shutil.move(str(related_path), str(target_dir / related_path.name))
            moved_files.add(related_path)

    logger.info(f"多光碟专辑已整理目录: {album_dir}")
    return True


def finalize_album_output(
    source_folder: Path,
    completed_root: Path,
    album_artist: str,
    album_name: str,
    file_disc_map: dict[str, str],
    logger: logging.Logger,
    organize_only: bool = False,
) -> bool:
    if not source_folder.exists():
        logger.warning(f"源专辑目录不存在，跳过移动: {source_folder}")
        return False

    if organize_only:
        organize_album_by_disc(
            album_dir=source_folder,
            file_disc_map=file_disc_map,
            logger=logger,
        )
        logger.info(f"专辑已整理，等待后续确认再移动: {source_folder}")
        return False

    organize_album_by_disc(
        album_dir=source_folder,
        file_disc_map=file_disc_map,
        logger=logger,
    )
    return move_album_to_completed(
        source_folder=source_folder,
        completed_root=completed_root,
        album_artist=album_artist,
        album_name=album_name,
        logger=logger,
    )


def merge_album_to_existing_target(
    source_folder: Path,
    target_folder: Path,
    logger: logging.Logger,
) -> bool:
    if source_folder.resolve() == target_folder.resolve():
        logger.info(f"专辑已在完成目录: {target_folder}")
        return True

    if not target_folder.is_dir():
        logger.warning(f"目标路径不是目录，将替换: {target_folder}")
        target_folder.unlink()
        shutil.move(str(source_folder), str(target_folder))
        return True

    for source_path in sorted(source_folder.iterdir()):
        target_path = target_folder / source_path.name
        if source_path.is_dir():
            if target_path.exists() and not target_path.is_dir():
                target_path.unlink()
            target_path.mkdir(parents=True, exist_ok=True)
            merge_album_to_existing_target(source_path, target_path, logger)
            continue

        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
        shutil.move(str(source_path), str(target_path))

    try:
        source_folder.rmdir()
    except OSError:
        logger.warning(f"源专辑目录未清空，保留: {source_folder}")
        return False

    logger.info(f"专辑已合并到完成目录: {target_folder}")
    return True


def move_album_to_completed(
    source_folder: Path,
    completed_root: Path,
    album_artist: str,
    album_name: str,
    logger: logging.Logger,
) -> bool:
    if not source_folder.exists():
        logger.warning(f"源专辑目录不存在，跳过移动: {source_folder}")
        return False

    target_folder = build_completed_album_target(
        completed_root=completed_root,
        album_artist=album_artist,
        album_name=album_name,
    )
    if target_folder.exists():
        logger.warning(f"目标专辑目录已存在，将合并并替换同名文件: {target_folder}")
        return merge_album_to_existing_target(
            source_folder=source_folder,
            target_folder=target_folder,
            logger=logger,
        )

    target_folder.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source_folder), str(target_folder))
    logger.info(f"专辑已移动到完成目录: {target_folder}")
    return True


def check_metaflac() -> bool:
    try:
        output = subprocess.run(
            ['metaflac'], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        version_raw_info = (
            output.stdout.decode('utf-8').strip()
            or output.stderr.decode('utf-8').strip()
        )
        version_match = re.search(
            r'FLAC metadata editor version (\d+\.\d+\.\d+)', version_raw_info
        )
        if version_match:
            version = version_match.group(1)
            print(f"找到 metaflac 版本: {version}")
        else:
            print("警告: 未能解析 metaflac 版本信息。\n 请确保已正确安装 FLAC 工具包。")
        return True
    except FileNotFoundError:
        print("错误: 未找到 'metaflac' 命令。请确保已安装 FLAC 工具包。")
        return False


def main() -> int:
    parser = build_argument_parser()
    args = parser.parse_args()
    scriptPath = Path(__file__).resolve()
    configPath = resolveConfigPath()
    completedRoot = getCompletedRoot(configPath)

    if not check_metaflac():
        return 1

    input_folder_path = resolve_input_folder(args.input_folder, scriptPath)
    if not input_folder_path.is_dir():
        print(f"错误: 输入的路径不是有效的文件夹: {input_folder_path}")
        return 1

    folder_name = os.path.basename(os.path.normpath(str(input_folder_path)))
    folder_name = re.sub(r'[^\w\s-]', '', folder_name).strip()

    logger = setup_logger(folder_name)
    logger.info(f"输入文件夹: {input_folder_path}")

    flac_files = scan_folder_for_flac(str(input_folder_path))
    logger.info(f"找到 {len(flac_files)} 个 FLAC 文件")

    flac_dict = reform_flac_to_flac_dict(flac_files, input_folder=str(input_folder_path))
    flac_final_dict = get_flac_file_point(flac_dict)
    logger.debug(f"FLAC 字典: {flac_dict}")

    for _, items in flac_final_dict.items():
        logger.info(
            f"处理专辑: {items.get('album_name', '')} (路径: {items.get('path', '')})"
        )
        nfo_data = initialize_nfo_data(None)
        artists = {}
        files = items['files']
        path = items['path']
        album = items.get('album_name', '')
        real_artists = []
        raw_album_artists = []
        flac_path = ''
        tags = {}
        file_disc_map = {}
        logger.info(f"找到 {len(files)} 个文件")
        flac_path = files[0]['full_path']
        final_folder = str(get_album_output_folder(Path(flac_path)))
        nfo_file = os.path.join(final_folder, 'album.nfo')
        if os.path.exists(nfo_file):
            temp_file = files[0]['full_path']
            tags = get_flac_tags(temp_file, logger)
            album = get_album_name(tags.get('ALBUM')) or album
            for file_info in files:
                file_tags = get_flac_tags(file_info['full_path'], logger)
                file_disc_map[file_info['full_path']] = file_tags.get('DISCNUMBER', '')
                raw_artists = split_artist(file_tags.get('ARTIST', ''))
                raw_album_artists.extend(split_artist(file_tags.get('ALBUMARTIST', '')))
                for artist_name in raw_artists:
                    if artist_name in artists:
                        artists[artist_name] += 1
                    else:
                        artists[artist_name] = 1
            logger.info(f"NFO 文件已存在: {nfo_file}")
            local_data = NfoHandler.read(nfo_file)
            nfo_data = initialize_nfo_data(local_data)
            album_artist = local_data['album'].get('albumartist', '') if local_data and 'album' in local_data else ''
            real_artists = get_real_artist(raw_artists=artists) if artists else []
            if not nfo_data['album'].get('artist'):
                nfo_data['album']['artist'] = ', '.join(normalizeArtistValues(real_artists))
            if not album_artist:
                album_artist = getAlbumArtistFallback(
                    realArtists=real_artists,
                    rawAlbumArtists=raw_album_artists,
                    albumDir=Path(final_folder),
                )
                nfo_data['album']['albumartist'] = album_artist
            year = extract_year(tags.get('DATE'))
            if year and not nfo_data['album'].get('year'):
                nfo_data['album']['year'] = year
            nfo_written = False
        else:
            for file_info in files:
                flac_path = file_info['full_path']
                flac_name = file_info['file_name']
                logger.debug(f"处理文件: {flac_name}")
                tags = get_flac_tags(flac_path, logger)
                file_disc_map[flac_path] = tags.get('DISCNUMBER', '')
                raw_artists = split_artist(tags.get('ARTIST', ''))
                raw_album_artists = split_artist(tags.get('ALBUMARTIST', ''))
                cd_num = tags.get('DISCNUMBER')
                album = get_album_name(tags.get('ALBUM')) or album
                cd_nums = []
                for artist_name in raw_artists:
                    if artist_name in artists:
                        artists[artist_name] += 1
                    else:
                        artists[artist_name] = 1
                if cd_num in cd_nums:
                    pass
                else:
                    cd_nums.append(cd_num)
                real_artists = get_real_artist(raw_artists=artists)
                temp_artists = tags.get('ARTIST', '').split(',')
                if not temp_artists:
                    raw_artists = real_artists
                final_artist = ','.join(raw_artists)
                track_info = {
                    'title': tags.get('TITLE', ''),
                    'cdnum': tags.get('DISCNUMBER', '1'),
                    'position': tags.get('TRACKNUMBER', ''),
                }

                if path in raw_artists:
                    track_info['albumartist'] = path
                track_info['artist'] = final_artist
                nfo_data['album']['track'].append(track_info)

            year = extract_year(tags.get('DATE'))
            if year:
                nfo_data['album']['year'] = year
            nfo_data['album']['artist'] = ', '.join(normalizeArtistValues(real_artists))
            album_artist = getAlbumArtistFallback(
                realArtists=real_artists,
                rawAlbumArtists=raw_album_artists,
                albumDir=Path(final_folder),
            )
            nfo_data['album']['albumartist'] = album_artist
            
            
            logger.info(f"生成 NFO 文件: {nfo_file}")
            NfoHandler.show(nfo_data)
            nfo_written = False
        if os.path.exists(nfo_file):
            local_data = NfoHandler.read(nfo_file)
            lock_data_status = False
            if local_data and 'album' in local_data:
                lock_data_status = isLockDataEnabled(local_data['album'].get("lock_data", False))
            if not lock_data_status:
                NfoHandler.write(
                    data=nfo_data,
                    output_path=nfo_file,
                    pretty=True,
                )
                logger.info(f"NFO 文件已更新: {nfo_file}")
                nfo_written = True
            else:
                logger.info(f"NFO 文件已锁定，跳过更新: {nfo_file}")
        else:
            NfoHandler.write(
                data=nfo_data,
                output_path=nfo_file,
                pretty=True,
            )
            logger.info(f"NFO 文件已创建: {nfo_file}")
            nfo_written = True

        if nfo_written:
            finalized = finalize_album_output(
                source_folder=Path(final_folder),
                completed_root=completedRoot,
                album_artist=album_artist,
                album_name=album,
                file_disc_map=file_disc_map,
                logger=logger,
                organize_only=args.organize_only,
            )
            if finalized and not args.organize_only:
                completedAlbumDir = build_completed_album_target(
                    completed_root=completedRoot,
                    album_artist=album_artist,
                    album_name=album,
                )
                print(f"{COMPLETED_ALBUM_DIR_PREFIX}{completedAlbumDir}", flush=True)

    logger.info("=" * 60)
    logger.info("Build NFO - 完成")
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
