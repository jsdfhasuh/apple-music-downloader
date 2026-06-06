#!/usr/bin/env python3
"""
Apple Music Decrypt M4A to FLAC Converter
用于将下载的 M4A 文件转换为 FLAC 格式的 Python 脚本
支持单文件和文件夹批量转换

Usage:
    python convert_to_flac.py <input_file_or_folder> [options]
    python convert_to_flac.py song.m4a
    python convert_to_flac.py /path/to/album --retry 3
"""

import sys
import os
import subprocess
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Tuple

try:
    from mutagen.mp4 import MP4
    from mutagen.flac import FLAC

    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False


# 配置常量
DEFAULT_RETRY_COUNT = 3
DEFAULT_TIMEOUT = 300  # 5分钟


def get_logs_dir() -> Path:
    configured = os.environ.get("WEBAPP_LOGS_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    container_logs_dir = Path("/app/data/logs")
    if container_logs_dir.parent.exists():
        return container_logs_dir
    return Path(__file__).parent.parent / "logs"


LOGS_DIR = get_logs_dir()


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
    log_file = LOGS_DIR / f"convert_{folder_name}_{timestamp}.log"

    # 配置日志格式
    log_format = '%(asctime)s | %(levelname)-8s | %(message)s'
    date_format = '%Y-%m-%d %H:%M:%S'

    # 创建日志记录器
    logger = logging.getLogger('convert_to_flac')
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
    logger.info("Apple Music M4A to FLAC Converter - 启动")
    logger.info(f"日志文件: {log_file}")
    logger.info("=" * 60)

    return logger


def check_ffmpeg(logger: logging.Logger) -> bool:
    """检查 FFmpeg 是否可用"""
    logger.debug("检查 FFmpeg 可用性...")
    try:
        result = subprocess.run(
            ['ffmpeg', '-version'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
        )
        if result.returncode == 0:
            version_line = result.stdout.split('\n')[0]
            logger.info(f"FFmpeg 检查通过: {version_line}")
            return True
        else:
            logger.error(f"FFmpeg 返回非零退出码: {result.returncode}")
            return False
    except subprocess.TimeoutExpired:
        logger.error("FFmpeg 检查超时 (>5秒)")
        return False
    except FileNotFoundError:
        logger.error("未找到 FFmpeg 可执行文件")
        return False
    except Exception as e:
        logger.error(f"FFmpeg 检查时发生异常: {e}")
        return False


def check_metaflac(logger: logging.Logger) -> bool:
    """检查 metaflac 是否可用"""
    logger.debug("检查 metaflac 可用性...")
    try:
        result = subprocess.run(
            ['metaflac', '--version'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=5,
        )
        if result.returncode == 0:
            version_line = result.stdout.strip()
            logger.info(f"metaflac 检查通过: {version_line}")
            return True
        else:
            return False
    except (FileNotFoundError, subprocess.TimeoutExpired):
        logger.warning("未找到 metaflac，将跳过封面嵌入")
        return False
    except Exception as e:
        logger.warning(f"metaflac 检查异常: {e}")
        return False


def get_m4a_track_info(
    input_file: Path,
    logger: logging.Logger = None,
) -> tuple[int | None, int | None]:
    """从 M4A 的 trkn 标签读取轨号信息

    Returns:
        (track_number, track_total)
    """
    if logger is None:
        logger = logging.getLogger('convert_to_flac')

    if not MUTAGEN_AVAILABLE:
        logger.warning("未安装 mutagen，无法读取 trkn 标签")
        return None, None

    try:
        mp4 = MP4(str(input_file))
        trkn = mp4.tags.get('trkn') if mp4.tags else None
        if not trkn:
            return None, None

        track_num, track_total = trkn[0]
        if track_num == 0:
            track_num = None
        if track_total == 0:
            track_total = None

        return track_num, track_total
    except Exception as e:
        logger.warning(f"读取 trkn 失败: {e}")
        return None, None


def write_flac_track_info(
    flac_path: Path,
    track_num: int | None,
    track_total: int | None,
    logger: logging.Logger = None,
) -> bool:
    """写入 FLAC 的 TRACKNUMBER/TRACKTOTAL 标签"""
    if logger is None:
        logger = logging.getLogger('convert_to_flac')

    if track_num is None:
        return False

    if not MUTAGEN_AVAILABLE:
        logger.warning("未安装 mutagen，无法写入 TRACKNUMBER")
        return False

    try:
        flac = FLAC(str(flac_path))
        flac['TRACKNUMBER'] = str(track_num)
        if track_total:
            flac['TRACKTOTAL'] = str(track_total)
        flac.save()
        if track_total:
            logger.info(f"写入轨号: {track_num}/{track_total}")
        else:
            logger.info(f"写入轨号: {track_num}")
        return True
    except Exception as e:
        logger.warning(f"写入 TRACKNUMBER 失败: {e}")
        return False


def find_cover_image(directory: Path) -> Path | None:
    """在目录中查找封面图片文件

    按优先级查找: cover.jpg > cover.png > cover.jpeg

    Args:
        directory: 查找目录

    Returns:
        封面图片路径，未找到返回 None
    """
    for name in ['cover.jpg', 'cover.png', 'cover.jpeg']:
        cover_path = directory / name
        if cover_path.exists():
            return cover_path
    return None


def resize_cover(
    cover_path: Path,
    output_path: Path,
    max_size: int = 600,
    logger: logging.Logger = None,
) -> bool:
    """缩小封面图片到指定尺寸

    Args:
        cover_path: 原始封面路径
        output_path: 缩小后的封面输出路径
        max_size: 最大边长（像素）
        logger: 日志记录器

    Returns:
        bool: 是否成功
    """
    if logger is None:
        logger = logging.getLogger('convert_to_flac')

    try:
        result = subprocess.run(
            [
                'ffmpeg',
                '-hide_banner',
                '-loglevel',
                'warning',
                '-y',
                '-i',
                str(cover_path),
                '-vf',
                f'scale={max_size}:{max_size}',
                '-frames:v',
                '1',
                '-update',
                '1',
                str(output_path),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if output_path.exists() and output_path.stat().st_size > 0:
            size_kb = output_path.stat().st_size / 1024
            logger.debug(f"封面已缩小: {max_size}x{max_size} ({size_kb:.1f} KB)")
            return True
        else:
            logger.warning("封面缩小失败: 输出文件为空")
            return False
    except Exception as e:
        logger.warning(f"封面缩小失败: {e}")
        return False


def embed_cover(
    flac_path: Path,
    cover_path: Path,
    logger: logging.Logger = None,
) -> bool:
    """用 metaflac 将封面嵌入 FLAC 文件的元数据块

    Args:
        flac_path: FLAC 文件路径
        cover_path: 封面图片路径
        logger: 日志记录器

    Returns:
        bool: 是否成功
    """
    if logger is None:
        logger = logging.getLogger('convert_to_flac')

    try:
        result = subprocess.run(
            [
                'metaflac',
                f'--import-picture-from=3||||{cover_path}',
                str(flac_path),
            ],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=30,
        )
        if result.returncode == 0:
            logger.debug(f"🖼️ 封面已嵌入: {flac_path.name}")
            return True
        else:
            logger.warning(f"封面嵌入失败 (退出码: {result.returncode})")
            if result.stderr:
                logger.warning(f"  {result.stderr.strip()}")
            return False
    except Exception as e:
        logger.warning(f"封面嵌入异常: {e}")
        return False


def scan_m4a_files(input_path: str, recursive: bool = True) -> List[Path]:
    """扫描目录中的所有 M4A 文件

    Args:
        input_path: 输入路径（文件或文件夹）
        recursive: 是否递归扫描子目录

    Returns:
        List[Path]: M4A 文件路径列表
    """
    path = Path(input_path)

    # 如果是文件，直接返回
    if path.is_file():
        if path.suffix.lower() == '.m4a':
            return [path]
        else:
            return []

    # 如果是文件夹，扫描 M4A 文件
    if path.is_dir():
        if recursive:
            # 递归扫描
            return list(path.rglob('*.m4a')) + list(path.rglob('*.M4A'))
        else:
            # 只扫描当前目录
            return list(path.glob('*.m4a')) + list(path.glob('*.M4A'))

    return []


def convert_single_file(
    input_file: Path,
    remove_original: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
    logger: logging.Logger = None,
) -> bool:
    """转换单个 M4A 文件为 FLAC

    Args:
        input_file: 输入的 M4A 文件路径
        remove_original: 是否删除原始文件
        timeout: 转换超时时间（秒）
        logger: 日志记录器

    Returns:
        bool: 转换是否成功
    """
    if logger is None:
        logger = logging.getLogger('convert_to_flac')

    logger.info(f"开始转换: {input_file.name}")

    # 检查输入文件
    if not input_file.exists():
        logger.error(f"输入文件不存在: {input_file}")
        return False

    # 获取文件信息
    file_size = input_file.stat().st_size
    file_size_mb = file_size / (1024 * 1024)
    logger.info(f"输入文件大小: {file_size_mb:.2f} MB")

    # 生成输出文件名
    output_path = input_file.with_suffix('.flac')

    # 如果输出文件已存在,直接删除
    if output_path.exists():
        os.remove(output_path)
        logger.debug(f"已删除已存在的输出文件: {output_path.name}")
    # 构建 ffmpeg 命令（只映射音频流，避免封面视频流导致兼容性问题）
    ffmpeg_cmd = [
        'ffmpeg',
        '-hide_banner',  # 隐藏版本信息
        '-loglevel',
        'warning',  # 只显示警告和错误
        '-i',
        str(input_file),
        '-map',
        '0:a:0',  # 只取第一条音频流
        '-vn',  # 禁用视频/封面流
        '-c:a',
        'flac',
        '-compression_level',
        '8',
        '-map_metadata',
        '0',
        '-y',
        str(output_path),
    ]

    logger.debug(f"FFmpeg 命令: {' '.join(ffmpeg_cmd)}")
    # 查找并准备封面图片（缩小到 600x600 以兼容更多播放器）
    cover_dir = input_file.parent
    cover_path = find_cover_image(cover_dir)
    cover_small_path = None
    if cover_path:
        cover_small_path = cover_dir / '_cover_small.jpg'
        if not resize_cover(cover_path, cover_small_path, max_size=600, logger=logger):
            cover_small_path = None
            logger.warning("封面缩小失败，将不嵌入封面")
        else:
            logger.info(f"🖼️ 找到封面: {cover_path.name} -> 缩小到 600x600")
    else:
        logger.debug(f"未找到封面图片: {cover_dir}")
    try:
        start_time = datetime.now()

        result = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
        )

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"转换耗时: {duration:.2f} 秒")

        if result.returncode == 0:
            # 检查输出文件
            if output_path.exists() and output_path.stat().st_size > 0:
                output_size = output_path.stat().st_size
                output_size_mb = output_size / (1024 * 1024)
                logger.info(
                    f"✅ 转换成功: {output_path.name} ({output_size_mb:.2f} MB)"
                )

                # 写入轨号
                track_num, track_total = get_m4a_track_info(input_file, logger=logger)
                if track_num is not None:
                    write_flac_track_info(
                        output_path,
                        track_num,
                        track_total,
                        logger=logger,
                    )

                # 嵌入封面
                if cover_small_path and cover_small_path.exists():
                    embed_cover(output_path, cover_small_path, logger=logger)

                # 删除原文件
                if remove_original:
                    try:
                        input_file.unlink()
                        logger.info(f"🗑️ 已删除原文件: {input_file.name}")
                    except OSError as e:
                        logger.error(f"删除原文件失败: {e}")

                return True
            else:
                logger.error(f"转换失败：输出文件为空")
                return False
        else:
            logger.error(f"FFmpeg 转换失败 (退出码: {result.returncode})")
            if result.stderr:
                stderr_lines = result.stderr.strip().split('\n')
                for line in stderr_lines[-3:]:
                    if line.strip():
                        logger.error(f"  {line}")
            return False

    except subprocess.TimeoutExpired:
        logger.error(f"转换超时 (>{timeout}秒)")
        return False
    except Exception as e:
        logger.exception(f"转换过程出错: {e}")
        return False
    finally:
        # 清理临时封面文件
        if cover_small_path and cover_small_path.exists():
            try:
                cover_small_path.unlink()
            except OSError:
                pass


def convert_with_retry(
    input_file: Path,
    max_retries: int = DEFAULT_RETRY_COUNT,
    remove_original: bool = False,
    logger: logging.Logger = None,
) -> bool:
    """带重试机制的转换

    Args:
        input_file: 输入的 M4A 文件路径
        max_retries: 最大重试次数
        remove_original: 是否删除原始文件
        logger: 日志记录器

    Returns:
        bool: 转换是否成功
    """
    for attempt in range(max_retries):
        if attempt > 0:
            logger.info(
                f"🔄 第 {attempt + 1}/{max_retries} 次尝试转换: {input_file.name}"
            )

        success = convert_single_file(input_file, remove_original=False, logger=logger)

        if success:
            # 最后一次成功才删除原文件
            if remove_original:
                try:
                    input_file.unlink()
                    logger.info(f"🗑️ 已删除原文件: {input_file.name}")
                except OSError as e:
                    logger.error(f"删除原文件失败: {e}")
            return True

        if attempt < max_retries - 1:
            logger.warning(
                f"转换失败，准备重试... (剩余 {max_retries - attempt - 1} 次)"
            )

    logger.error(f"💥 转换失败，已重试 {max_retries} 次: {input_file.name}")
    return False


def convert_folder(
    input_path: str,
    recursive: bool = True,
    max_retries: int = DEFAULT_RETRY_COUNT,
    remove_original: bool = False,
    logger: logging.Logger = None,
) -> Tuple[int, int]:
    """批量转换文件夹中的所有 M4A 文件

    Args:
        input_path: 输入路径
        recursive: 是否递归扫描子目录
        max_retries: 最大重试次数
        remove_original: 是否删除原始文件
        logger: 日志记录器

    Returns:
        Tuple[int, int]: (成功数量, 失败数量)
    """
    # 扫描 M4A 文件
    m4a_files = scan_m4a_files(input_path, recursive)

    if not m4a_files:
        logger.warning(f"未找到 M4A 文件: {input_path}")
        return 0, 0

    total_files = len(m4a_files)
    logger.info(f"找到 {total_files} 个 M4A 文件")
    logger.info("=" * 60)

    success_count = 0
    fail_count = 0

    for index, m4a_file in enumerate(m4a_files, 1):
        logger.info(f"\n[{index}/{total_files}] 处理: {m4a_file}")

        success = convert_with_retry(
            m4a_file,
            max_retries=max_retries,
            remove_original=remove_original,
            logger=logger,
        )

        if success:
            success_count += 1
        else:
            fail_count += 1

        # 显示进度
        progress = (index / total_files) * 100
        logger.info(f"进度: {progress:.1f}% ({success_count} 成功, {fail_count} 失败)")

    return success_count, fail_count


def main():
    """主函数"""
    # 解析命令行参数
    parser = argparse.ArgumentParser(
        description='Apple Music M4A to FLAC Converter',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
    python convert_to_flac.py song.m4a
    python convert_to_flac.py /path/to/album
    python convert_to_flac.py /path/to/album --retry 5 --remove-original
        """,
    )

    parser.add_argument('input', help='输入文件或文件夹路径')

    parser.add_argument(
        '--no-recursive', action='store_true', help='不递归扫描子目录（仅对文件夹有效）'
    )

    parser.add_argument(
        '--retry',
        type=int,
        default=DEFAULT_RETRY_COUNT,
        help=f'转换失败时的重试次数（默认: {DEFAULT_RETRY_COUNT}）',
    )

    parser.add_argument(
        '--remove-original', action='store_true', help='转换成功后删除原始 M4A 文件'
    )

    args = parser.parse_args()

    # 检查输入路径
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"错误: 输入路径不存在: {args.input}")
        sys.exit(1)

    # 确定文件夹名称（用于日志文件名）
    if input_path.is_file():
        folder_name = input_path.parent.name or input_path.stem
    else:
        folder_name = input_path.name

    # 设置日志
    logger = setup_logger(folder_name)
    logger.debug(f"命令行参数: {args}")
    logger.info(f"输入路径: {args.input}")
    logger.info(f"递归扫描: {not args.no_recursive}")
    logger.info(f"最大重试: {args.retry}")
    logger.info(f"删除原文件: {args.remove_original}")

    # 检查 FFmpeg
    if not check_ffmpeg(logger):
        logger.error("未找到 FFmpeg，请确保已安装并添加到 PATH")
        logger.info("下载地址: https://ffmpeg.org/download.html")
        sys.exit(1)

    # 检查 metaflac（可选，用于嵌入封面）
    has_metaflac = check_metaflac(logger)
    if not has_metaflac:
        logger.warning("未安装 metaflac，转换将不嵌入封面")
        logger.info("安装方法: apt install flac  /  brew install flac")

    # 执行转换
    logger.info("开始执行转换...")
    logger.info("=" * 60)

    success_count, fail_count = convert_folder(
        args.input,
        recursive=not args.no_recursive,
        max_retries=args.retry,
        remove_original=args.remove_original,
        logger=logger,
    )

    # 显示结果
    logger.info("\n" + "=" * 60)
    logger.info("转换完成!")
    logger.info(f"成功: {success_count}")
    logger.info(f"失败: {fail_count}")
    logger.info(f"总计: {success_count + fail_count}")
    logger.info("=" * 60)

    # 根据结果返回退出码
    if fail_count == 0:
        logger.info("🎉 所有文件转换成功!")
        sys.exit(0)
    elif success_count > 0:
        logger.warning(f"⚠️ 部分文件转换失败 ({fail_count} 个失败)")
        sys.exit(2)  # 部分成功
    else:
        logger.error("💥 所有文件转换失败!")
        sys.exit(1)


if __name__ == "__main__":
    main()
