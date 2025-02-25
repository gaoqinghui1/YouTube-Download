from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import yt_dlp
import os
import time
from pathlib import Path
import uvicorn
import browser_cookie3
import re
import unicodedata

app = FastAPI()

# 配置模板和静态文件
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

# 获取项目根目录的绝对路径
BASE_DIR = Path(__file__).resolve().parent
VIDEOS_DIR = BASE_DIR / "static" / "videos"
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# 存储下载任务状态
download_tasks = {}

def sanitize_filename(filename):
    """清理文件名，移除特殊字符"""
    # 移除非法字符
    filename = re.sub(r'[<>:"/\\|?*]', '', filename)
    # 移除控制字符
    filename = "".join(char for char in filename if unicodedata.category(char)[0] != "C")
    # 将空格替换为下划线
    filename = filename.replace(' ', '_')
    # 移除前后空格
    filename = filename.strip()
    # 限制长度
    filename = filename[:50]  # 限制文件名长度
    # 确保文件名不为空
    if not filename:
        filename = "video"
    # 确保文件名只包含安全字符
    filename = re.sub(r'[^a-zA-Z0-9_-]', '', filename)
    return filename

def get_format_info(format_dict):
    """从格式字典中提取有用的信息"""
    return {
        'format_id': format_dict.get('format_id', ''),
        'ext': format_dict.get('ext', ''),
        'resolution': format_dict.get('resolution', 'N/A'),
        'filesize': format_dict.get('filesize', 0),
        'format_note': format_dict.get('format_note', ''),
        'vcodec': format_dict.get('vcodec', ''),
        'acodec': format_dict.get('acodec', ''),
        'has_audio': format_dict.get('acodec') != 'none',  # 添加音频检查
        'has_video': format_dict.get('vcodec') != 'none',  # 添加视频检查
    }

@app.get("/formats")
async def get_formats(url: str):
    """获取视频可用的格式列表"""
    try:
        # 尝试获取Firefox cookies
        try:
            firefox_cookies = browser_cookie3.firefox()
            print("Successfully got Firefox cookies for format fetching")
        except Exception as e:
            print(f"Failed to get Firefox cookies: {e}")
            firefox_cookies = None
        
        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'cookiesfrombrowser': ('firefox',),  # 添加cookies支持
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Connection': 'keep-alive'
            }
        }
        
        # 处理Shorts URL
        if 'shorts' in url:
            url = url.replace('shorts/', 'watch?v=')
            print(f"Converted shorts URL to: {url}")
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            formats = []
            
            # 过滤并整理格式信息
            for f in info.get('formats', []):
                # 只保留同时包含视频和音频的格式
                if f.get('vcodec') != 'none' and f.get('acodec') != 'none':
                    format_info = get_format_info(f)
                    if format_info['filesize']:
                        size_mb = format_info['filesize'] / (1024 * 1024)
                        format_info['filesize_str'] = f"{size_mb:.1f} MB"
                    else:
                        format_info['filesize_str'] = 'Unknown'
                    formats.append(format_info)
            
            # 如果没有找到包含音视频的格式，添加最佳组合格式
            if not formats:
                formats.append({
                    'format_id': 'best',
                    'ext': 'mp4',
                    'resolution': 'Best quality',
                    'filesize_str': 'Unknown',
                    'format_note': 'Best quality with audio',
                    'has_audio': True,
                    'has_video': True
                })
            
            return {"formats": formats, "title": info.get('title', '')}
    except Exception as e:
        print(f"Error getting formats: {str(e)}")
        return {"error": str(e)}

def download_video(url: str, task_id: str, format_id: str = 'best'):
    """后台下载视频的函数"""
    download_tasks[task_id] = {"status": "downloading", "progress": 0}
    
    def progress_hook(d):
        if d['status'] == 'downloading':
            if 'total_bytes' in d:
                progress = (d['downloaded_bytes'] / d['total_bytes']) * 100
                download_tasks[task_id]["progress"] = round(progress, 2)
            print(f"Download progress: {progress}%")
        elif d['status'] == 'finished':
            print(f"Download finished: {d.get('filename')}")
    
    try:
        firefox_cookies = browser_cookie3.firefox()
        print("Successfully got Firefox cookies")
    except Exception as e:
        print(f"Failed to get Firefox cookies: {e}")
        firefox_cookies = None
    
    # 使用临时文件名模板
    temp_template = str(VIDEOS_DIR / "%(id)s.%(ext)s")
    print(f"Temporary output template: {temp_template}")
    
    ydl_opts = {
        'format': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best',  # 选择最高画质
        'outtmpl': temp_template,
        'progress_hooks': [progress_hook],
        'no_warnings': False,
        'quiet': False,
        'cookiesfrombrowser': ('firefox',),
        'merge_output_format': 'mp4',  # 确保输出MP4格式
        'http_headers': {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/115.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Connection': 'keep-alive'
        }
    }
    
    try:
        if 'shorts' in url:
            url = url.replace('shorts/', 'watch?v=')
            print(f"Converted shorts URL to: {url}")
        
        print("Starting download...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # 获取视频信息
            info = ydl.extract_info(url, download=True)
            video_id = info.get('id', '')
            print(f"Video ID: {video_id}")
            
            # 检查临时文件是否存在
            temp_path = VIDEOS_DIR / f"{video_id}.mp4"
            if not temp_path.exists():
                print(f"Temp file not found at: {temp_path}")
                print("Directory contents:")
                for file in VIDEOS_DIR.iterdir():
                    print(f"- {file}")
                raise Exception(f"Downloaded file not found at {temp_path}")
            
            # 生成最终的文件名
            safe_title = sanitize_filename(info.get('title', 'video'))
            final_filename = f"{safe_title}_{video_id}.mp4"
            final_path = VIDEOS_DIR / final_filename
            
            # 重命名文件
            try:
                temp_path.rename(final_path)
                print(f"File renamed from {temp_path} to {final_path}")
            except Exception as e:
                print(f"Failed to rename file: {e}")
                final_path = temp_path
                final_filename = temp_path.name
            
            # 使用相对URL路径
            url_path = f"/static/videos/{final_filename}"
            
            download_tasks[task_id].update({
                "status": "completed",
                "title": safe_title,
                "duration": info.get('duration'),
                "uploader": info.get('uploader'),
                "description": info.get('description', '')[:500],
                "filename": final_filename,
                "file_path": url_path,
                "mime_type": "video/mp4"
            })
            print("Task updated successfully")
            
    except Exception as e:
        error_msg = str(e)
        print(f"Download error: {error_msg}")
        download_tasks[task_id].update({
            "status": "error",
            "error": error_msg
        })

@app.get("/")
async def home(request: Request):
    # 获取已下载的视频列表
    videos = []
    for task in download_tasks.values():
        if task.get("status") == "completed":
            videos.append(task)
    return templates.TemplateResponse(
        "index.html", 
        {"request": request, "videos": videos}
    )

@app.post("/download")
async def download(url: str, background_tasks: BackgroundTasks, format_id: str = 'best'):
    task_id = str(time.time())
    background_tasks.add_task(download_video, url, task_id, format_id)
    return {"task_id": task_id}

@app.get("/status/{task_id}")
async def get_status(task_id: str):
    return download_tasks.get(task_id, {"status": "not_found"})

if __name__ == "__main__":
    print("\n=== YouTube视频下载器 ===")
    print("请按照以下步骤运行应用:")
    print("1. 确保已安装所需依赖:")
    print("   pip install fastapi uvicorn python-multipart yt-dlp jinja2 browser-cookie3")
    print("2. 使用以下命令启动服务器:")
    print("   python main.py")
    print("3. 在浏览器中访问:")
    print("   http://localhost:8001")
    print("注意: 不要直接打开HTML文件,必须通过HTTP服务器访问!\n")
    
    uvicorn.run(app, host="0.0.0.0", port=8001) 