import sys
import os
from loguru import logger
from dotenv import load_dotenv

# 添加 Spider_XHS-master 到系统路径
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
XHS_SPIDER_DIR = os.path.join(BASE_DIR, 'Spider_XHS-master')

if XHS_SPIDER_DIR not in sys.path:
    sys.path.append(XHS_SPIDER_DIR)

# 初始化环境变量
ENV_PATH = os.path.join(XHS_SPIDER_DIR, '.env')
load_dotenv(ENV_PATH)

# 配置 NODE_PATH，以便 PyExecJS 运行时能找到相应的 node_modules
os.environ["NODE_PATH"] = os.path.join(XHS_SPIDER_DIR, "node_modules")

# Spider_XHS 中的 JS 代码（如 xhs_xs_xsc_56.js 内）使用了基于当前路径的 require('./static/xxx')，
# 为了让 node 子进程成功定位到文件，我们需要让 Python 进程停留在该目录下运作。
original_cwd = os.getcwd()
os.chdir(XHS_SPIDER_DIR)

from apis.xhs_pc_apis import XHS_Apis

def get_xhs_cookies():
    # Spider_XHS 有一个 utils 初始化，但为了简洁，直接提 cookies
    import dotenv
    # Spider_XHS 的默认 .env 可能是 COOKIE 或 cookies
    cookies_str = dotenv.get_key(ENV_PATH, 'cookies')
    if not cookies_str:
        cookies_str = dotenv.get_key(ENV_PATH, 'COOKIE')
    if not cookies_str:
        cookies_str = dotenv.get_key(ENV_PATH, 'COOKIES')
    if not cookies_str:
        logger.warning(f"未能从 {ENV_PATH} 读取到小红书 cookies，请确保已经正确配置！")
        return ""
    return cookies_str

xhs_api = XHS_Apis()


def parse_likes(likes_str) -> int:
    """将小红书返回的点赞数字符串转为整数，支持 '1.2万'、'3万'、'500' 等格式"""
    try:
        s = str(likes_str).strip().replace(",", "")
        if "万" in s:
            return int(float(s.replace("万", "")) * 10000)
        return int(float(s))
    except Exception:
        return 0


def search_xhs_posts(keyword: str, require_num: int = 5, min_likes: int = 0, cookies_str: str = ""):
    """
    逐页搜索小红书笔记，直到凑满 require_num 个点赞数 >= min_likes 的帖子为止。
    按点赞数降序排列（sort_type_choice=2），翻页上限 10 页防止无限循环。
    """
    if not cookies_str:
        cookies_str = get_xhs_cookies()
    if not cookies_str:
        return []

    logger.info(f"开始搜索小红书，关键词：{keyword}，目标数量：{require_num}，最低点赞：{min_likes}")

    target_posts = []
    page = 1
    max_pages = 10  # 最多翻 10 页（每页 20 条，最多扫描 200 篇候选）

    while len(target_posts) < require_num and page <= max_pages:
        success, msg, res_json = xhs_api.search_note(
            query=keyword,
            cookies_str=cookies_str,
            page=page,
            sort_type_choice=2,  # 最多点赞排序
            note_type=0,
            note_time=0,
            note_range=0,
            pos_distance=0,
            geo=""
        )

        if not success:
            logger.error(f"第 {page} 页搜索失败: {msg}")
            break

        items = res_json.get("data", {}).get("items", [])
        has_more = res_json.get("data", {}).get("has_more", False)

        for note in items:
            note_card = note.get('note_card', {})
            likes_raw = note_card.get('interact_info', {}).get('liked_count', '0')
            likes_num = parse_likes(likes_raw)
            title = note.get('display_title', '') or note_card.get('title', '')

            if min_likes > 0 and likes_num < min_likes:
                logger.info(f"跳过低点赞帖子：{title[:20]}（{likes_num} < {min_likes}）")
                continue

            url = f"https://www.xiaohongshu.com/explore/{note['id']}?xsec_token={note.get('xsec_token', '')}"
            target_posts.append({
                "id": note['id'],
                "title": title,
                "url": url,
                "likes": likes_num,
            })

            if len(target_posts) >= require_num:
                break

        logger.info(f"第 {page} 页扫描完毕，当前已找到 {len(target_posts)}/{require_num} 篇有效帖子")

        if not has_more:
            logger.info("已无更多搜索结果，停止翻页")
            break

        page += 1

    logger.info(f"搜索结束，共找到有效帖子：{len(target_posts)} 篇（扫描了 {page} 页）")
    return target_posts


def get_xhs_note_detail(post_url: str, cookies_str: str = "") -> dict:
    """
    根据帖子 URL 获取文章标题和正文内容（desc 字段）。
    返回 {"title": str, "content": str}，失败时返回空字符串。
    """
    if not cookies_str:
        cookies_str = get_xhs_cookies()
    if not cookies_str:
        return {"title": "", "content": ""}

    logger.info(f"开始获取帖子详情：{post_url}")
    success, msg, res_json = xhs_api.get_note_info(url=post_url, cookies_str=cookies_str)

    if not success or not res_json:
        logger.error(f"获取帖子详情失败: {msg}")
        return {"title": "", "content": ""}

    try:
        note_card = res_json["data"]["items"][0]["note_card"]
        title = note_card.get("title", "").strip() or "无标题"
        content = note_card.get("desc", "").strip()
        logger.info(f"成功获取帖子详情，标题：{title[:30]}，正文长度：{len(content)}")
        return {"title": title, "content": content}
    except (KeyError, IndexError) as e:
        logger.error(f"解析帖子详情失败: {e}")
        return {"title": "", "content": ""}


def get_xhs_comments(post_url: str, cookies_str: str = ""):
    """
    根据给定的帖子 URL 抓取其评论
    """
    if not cookies_str:
        cookies_str = get_xhs_cookies()
    if not cookies_str:
        return []
    
    logger.info(f"开始获取评论：{post_url}")
    success, msg, comments = xhs_api.get_note_all_comment(url=post_url, cookies_str=cookies_str)
    
    if not success:
        logger.error(f"获取评论失败: {msg}")
        return []
    
    # 将包含 sub_comments 的树状评论拍平
    flat_comments = []
    
    for c in comments:
        content = c.get('content', '')
        if content:
            flat_comments.append(content)
        # 获取二级评论
        sub_comments = c.get('sub_comments', [])
        for sub_c in sub_comments:
            sub_content = sub_c.get('content', '')
            if sub_content:
                flat_comments.append(sub_content)
                
    return flat_comments
