# This file is part of Supysonic.
# Supysonic is a Python implementation of the Subsonic server API.
#
# Distributed under terms of the GNU AGPLv3 license.

import os
import xml.etree.ElementTree as ET
from typing import Dict, Any, Optional, List, Union
from xml.dom import minidom
import logging

logger = logging.getLogger(__name__)


class NfoHandler:
    """
    通用 NFO 文件处理类，支持读取和写入 NFO 文件
    读取时将 XML 数据转换为嵌套字典
    写入时将嵌套字典转换为格式化的 XML
    """

    @staticmethod
    def _element_to_dict(element: ET.Element) -> Dict[str, Any]:
        """将 XML 元素转换为字典"""
        result = {}

        # 处理元素属性
        if element.attrib:
            result["@attributes"] = dict(element.attrib)

        # 处理子元素
        child_elements = list(element)
        if not child_elements:
            # 没有子元素，直接获取文本内容
            text = element.text
            if text is not None and text.strip():
                return text.strip()
            else:
                return result or ""

        # 按标签名对子元素分组
        groups = {}
        for child in child_elements:
            tag = child.tag
            if tag not in groups:
                groups[tag] = []
            groups[tag].append(child)

        # 处理每个分组
        for tag, group in groups.items():
            if len(group) == 1:
                # 单个元素，直接添加到结果
                result[tag] = NfoHandler._element_to_dict(group[0])
            else:
                # 多个同名元素，创建列表
                result[tag] = [NfoHandler._element_to_dict(child) for child in group]

        return result

    @staticmethod
    def _dict_to_element(data: Dict[str, Any], root_name: str) -> ET.Element:
        """将字典转换为 XML 元素"""
        root = ET.Element(root_name)

        for key, value in data.items():
            # 处理属性
            if key == "@attributes":
                for attr_key, attr_value in value.items():
                    root.set(attr_key, str(attr_value))
                continue

            # 处理普通元素
            if isinstance(value, list):
                # 列表变为多个子元素
                for item in value:
                    if isinstance(item, dict):
                        # 递归处理嵌套字典
                        child = NfoHandler._dict_to_element(item, key)
                        root.append(child)
                    else:
                        # 简单值
                        child = ET.SubElement(root, key)
                        child.text = str(item) if item is not None else ""
            elif isinstance(value, dict):
                # 递归处理嵌套字典
                child = NfoHandler._dict_to_element(value, key)
                root.append(child)
            else:
                # 简单值
                child = ET.SubElement(root, key)
                child.text = str(value) if value is not None else ""

        return root

    @classmethod
    def read(cls, source: Union[str, bytes]) -> Optional[Dict[str, Any]]:
        """
        从文件路径或 XML 字符串中读取 NFO 数据

        Args:
            source: NFO 文件路径或 XML 字符串内容

        Returns:
            解析出的字典数据，如果解析失败则返回 None
        """
        try:
            if os.path.isfile(str(source)):
                # 从文件读取
                tree = ET.parse(source)
                root = tree.getroot()
            else:
                # 从字符串读取
                if isinstance(source, bytes):
                    source = source.decode('utf-8')
                root = ET.fromstring(source)

            # 转换为字典
            return {root.tag: cls._element_to_dict(root)}
        except (ET.ParseError, IOError, UnicodeDecodeError) as e:
            logger.error(f"读取 NFO 失败: {str(e)}")
            return None

    @classmethod
    def write(
        cls,
        data: Dict[str, Any],
        output_path: Optional[str] = None,
        pretty: bool = True,
        logger: Optional[logging.Logger] = None
    ) -> Optional[str]:
        """
        将字典数据写入 NFO 文件，支持中文字符

        Args:
            data: 要写入的字典数据，顶层键应为根元素名称
            output_path: 输出文件路径，如果不提供则返回 XML 字符串
            pretty: 是否格式化 XML（美化输出）
            logger: 日志记录器

        Returns:
            如果 output_path 为 None，则返回 XML 字符串；否则返回 None
        """
        try:
            if not data:
                if logger:
                    logger.error("没有可写入的数据")
                return None

            # 获取根元素名称和数据
            root_name = next(iter(data))
            root_data = data[root_name]

            # 转换为 XML
            root = cls._dict_to_element(root_data, root_name)

            # 添加XML声明
            declaration = '<?xml version="1.0" encoding="utf-8" standalone="yes"?>\n'
            
            # 创建 XML 字符串
            xml_str = ET.tostring(root, encoding='utf-8', method='xml').decode('utf-8')

            # 美化 XML
            if pretty:
                dom = minidom.parseString(xml_str)
                # 使用encoding='utf-8'确保中文字符被正确处理
                pretty_xml = dom.toprettyxml(indent="  ", encoding='utf-8')
                # 将字节转换回字符串
                xml_str = pretty_xml.decode('utf-8')
                # 移除额外的空行
                xml_str = "\n".join(line for line in xml_str.split("\n") if line.strip())
            
            # 确保添加XML声明
            if '<?xml' not in xml_str:
                xml_str = declaration + xml_str

            # 写入文件或返回字符串
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(xml_str)
                return None
            else:
                return xml_str
                
        except Exception as e:
            if logger:
                logger.error(f"写入NFO文件时出错: {str(e)}")
            return None

    @classmethod
    def merge(
        cls, nfo1: Dict[str, Any], nfo2: Dict[str, Any], overwrite: bool = False
    ) -> Dict[str, Any]:
        """
        合并两个 NFO 字典，适用于更新现有数据

        Args:
            nfo1: 基础 NFO 字典
            nfo2: 要合并的 NFO 字典
            overwrite: 如果为 True，则 nfo2 中的值将覆盖 nfo1 中的值；否则只添加不存在的值

        Returns:
            合并后的 NFO 字典
        """
        if not nfo1:
            return nfo2.copy() if nfo2 else {}
        if not nfo2:
            return nfo1.copy()

        result = nfo1.copy()
        for key, value in nfo2.items():
            if key not in result:
                result[key] = value
            elif isinstance(value, dict) and isinstance(result[key], dict):
                result[key] = cls.merge(result[key], value, overwrite)
            elif isinstance(value, list) and isinstance(result[key], list):
                # 对于列表，简单地追加不存在的项
                if overwrite:
                    result[key] = value
                else:
                    result[key].extend(
                        item for item in value if item not in result[key]
                    )
            elif overwrite:
                result[key] = value

        return result
    @classmethod
    def show(cls, data: Dict[str, Any], indent: int = 0) -> None:
        """打印 NFO 数据的层次结构，便于调试"""
        for key, value in data.items():
            print(' ' * indent + str(key) + ':', end=' ')
            if isinstance(value, dict):
                print()
                NfoHandler.show(value, indent + 2)
            elif isinstance(value, list):
                print('[')
                for item in value:
                    if isinstance(item, dict):
                        cls.show(item, indent + 2)
                    else:
                        print(' ' * (indent + 2) + str(item))
                print(' ' * indent + ']')
            else:
                print(str(value))
