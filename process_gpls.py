#!/usr/bin/env python3
import json
import os
import sys
import glob
import re
from pathlib import Path

def parse_gpls_file(file_path):
    """解析单个gpls文件，提取视频信息"""
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    # 提取#V{num}行的信息
    video_configs = {}
    video_files = []
    
    for line in lines:
        line = line.strip()
        
        # 匹配#V{num}格式的行
        v_match = re.match(r'#V(\d+):(.*)', line)
        if v_match:
            video_index = int(v_match.group(1))
            config_json = v_match.group(2)
            try:
                config = json.loads(config_json)
                video_configs[video_index] = {
                    'loop_start': config.get('loop_start', 0),
                    'loop_end': config.get('loop_end', 0)
                }
            except json.JSONDecodeError as e:
                print(f"警告：解析JSON失败 {file_path} 第{video_index}项: {e}")
                continue
        
        # 收集文件路径（非#开头的行且不为空）
        elif line and not line.startswith('#'):
            video_files.append(line)
    
    # 将配置与文件路径匹配
    result = {}
    for index, config in video_configs.items():
        if index < len(video_files):
            file_path = video_files[index]
            result[file_path] = config
        else:
            print(f"警告：索引{index}超出文件列表范围 {file_path}")
    
    return result

def create_symlinks(media_data, target_dir):
    """为视频文件创建硬链接并更新路径"""
    media_dir = Path(target_dir) / 'video'
    
    # 创建video目录
    media_dir.mkdir(exist_ok=True)
    print(f"已创建/确认video目录: {media_dir}")
    
    updated_media = {}
    path_counter = {}  # 用于跟踪相同路径的计数
    
    for original_path, config in media_data.items():
        # 获取原始文件的绝对路径
        source_file = Path(original_path)
        if not source_file.is_absolute():
            # 如果是相对路径，相对于target_dir
            source_file = Path(target_dir) / original_path
        
        # 检查源文件是否存在
        if not source_file.exists():
            print(f"警告：源文件不存在 {source_file}")
            continue
            
        # 创建硬链接的目标路径
        link_name = source_file.name
        link_path = media_dir / link_name
        
        # 如果目标文件已存在，删除它（准备覆盖）
        if link_path.exists():
            try:
                link_path.unlink()
                print(f"删除已存在的文件: {link_path}")
            except Exception as e:
                print(f"警告：无法删除已存在文件 {link_path}: {e}")
        
        try:
            # 创建硬链接 (不需要特殊权限，但需要在同一文件系统中)
            os.link(source_file.resolve(), link_path)
            print(f"创建硬链接: {link_path} -> {source_file}")
            
            # 使用相对于当前工作目录的路径，统一使用正斜杠
            relative_link_path = os.path.relpath(link_path, target_dir).replace('\\', '/')
            
            # 处理JSON key重复问题：为相同文件的不同配置创建唯一key
            json_key = relative_link_path
            if json_key in updated_media:
                # 如果key已存在，检查配置是否相同
                existing_config = updated_media[json_key]
                if existing_config['loop_start'] != config['loop_start'] or existing_config['loop_end'] != config['loop_end']:
                    # 配置不同，创建唯一key
                    if json_key not in path_counter:
                        path_counter[json_key] = 1
                        # 重命名现有的key
                        updated_media[f"{json_key}#1"] = updated_media.pop(json_key)
                    
                    path_counter[json_key] += 1
                    json_key = f"{json_key}#{path_counter[json_key]}"
            
            updated_media[json_key] = config
            
        except OSError as e:
            print(f"警告：创建硬链接失败 {source_file}: {e}")
            print(f"回退到文件复制模式")
            # 如果硬链接创建失败（如跨文件系统），复制文件
            try:
                import shutil
                shutil.copy2(source_file, link_path)
                print(f"复制文件: {source_file} -> {link_path}")
                
                # 使用相对于当前工作目录的路径，统一使用正斜杠
                relative_link_path = os.path.relpath(link_path, target_dir).replace('\\', '/')
                
                # 处理JSON key重复问题：为相同文件的不同配置创建唯一key
                json_key = relative_link_path
                if json_key in updated_media:
                    # 如果key已存在，检查配置是否相同
                    existing_config = updated_media[json_key]
                    if existing_config['loop_start'] != config['loop_start'] or existing_config['loop_end'] != config['loop_end']:
                        # 配置不同，创建唯一key
                        if json_key not in path_counter:
                            path_counter[json_key] = 1
                            # 重命名现有的key
                            updated_media[f"{json_key}#1"] = updated_media.pop(json_key)
                        
                        path_counter[json_key] += 1
                        json_key = f"{json_key}#{path_counter[json_key]}"
                
                updated_media[json_key] = config
                
            except Exception as copy_error:
                print(f"错误：文件复制也失败 {source_file}: {copy_error}")
                # 失败时保持原始路径
                updated_media[original_path] = config
        except Exception as e:
            print(f"错误：处理文件失败 {source_file}: {e}")
            # 失败时保持原始路径
            updated_media[original_path] = config
    
    return updated_media

def process_directory(directory='.'):
    """处理指定目录中的所有gpls文件"""
    directory = Path(directory)
    
    # 查找所有gpls文件
    gpls_files = list(directory.glob('*.gpls'))
    
    if not gpls_files:
        print(f"在目录 {directory} 中没有找到.gpls文件")
        return {}
    
    print(f"找到 {len(gpls_files)} 个.gpls文件")
    
    all_media = {}
    
    for gpls_file in gpls_files:
        print(f"处理文件: {gpls_file}")
        try:
            media_info = parse_gpls_file(gpls_file)
            # 直接合并到同一级别，不按gpls文件分组
            all_media.update(media_info)
        except Exception as e:
            print(f"错误：处理文件 {gpls_file} 时出错: {e}")
    
    return all_media

def main():
    # 获取目标目录（命令行参数或当前目录）
    target_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    
    print(f"正在处理目录: {os.path.abspath(target_dir)}")
    
    # 处理所有gpls文件
    media_data = process_directory(target_dir)
    
    if not media_data:
        print("没有找到任何有效的媒体信息")
        return
    
    # 为视频文件创建符号链接并更新路径
    updated_media_data = create_symlinks(media_data, target_dir)
    
    if not updated_media_data:
        print("没有成功处理任何视频文件")
        return
    
    # 生成media.json文件
    output_file = os.path.join(target_dir, 'media.json')
    
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(updated_media_data, f, indent=2, ensure_ascii=False)
        
        print(f"\n成功生成 {output_file}")
        print(f"共处理 {len(updated_media_data)} 个视频文件")
        print(f"所有视频文件的符号链接已创建在 video/ 目录中")
            
    except Exception as e:
        print(f"错误：写入文件 {output_file} 时出错: {e}")

if __name__ == '__main__':
    main()