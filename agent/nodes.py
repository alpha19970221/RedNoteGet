import json
import re
import os
import logging
from typing import Dict, List, Any, Callable, Optional
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from agent.state import AgentState
from agent.xhs_client import search_xhs_posts, get_xhs_note_detail, get_xhs_comments

# 加载项目根目录的 .env 文件
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(BASE_DIR, ".env"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

llm = ChatOpenAI(model="gpt-4o", temperature=0.7)


def keyword_generator_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    user_input = state["user_input"]
    logging.info(f"==> Node: KeywordGenerator | User Input: {user_input}")

    # 直接使用用户输入作为搜索关键词，不再经过 LLM 转化
    keywords = [user_input]

    if emit:
        emit("node_start", {
            "node": "KeywordGenerator",
            "message": f"使用关键词「{user_input}」直接搜索..."
        })
        emit("node_done", {
            "node": "KeywordGenerator",
            "message": f"关键词已确认：{user_input}",
            "keywords": keywords
        })

    return {"search_keywords": keywords}


# ── 评论智能过滤函数 ─────────────────────────────────────────────────────────
# 纯 Emoji 正则（仅匹配高位 Emoji，不触碰中文、字母、标点等任何可读字符）
_EMOJI_RE = re.compile(
    r'^[\U0001F300-\U0001FAFF'   # 主力 Emoji 区（约文字符号、旗帜、人物、动物、食物等）
    r'\uFE00-\uFE0F'             # 变体选择符
    r'\u200D'                    # 零宽连接符（用于 Emoji 组合）
    r'\s]+$'
)


# 重复字符（3 次以上同一字符）
_REPEAT_RE = re.compile(r'(.)\1{3,}')
# 纯数字 / 纯标点
_NOISE_RE = re.compile(r'^[\d\s\W]+$', re.UNICODE)

def _filter_comments(raw_comments: list, limit: int = 150) -> list:
    """
    三阶段智能过滤，最终按评论长度降序排名，确保 limit 配额内全是高价值内容。

    阶段一：规则去噪   — 去掉字数 ≤ 4、纯表情、纯数字标点、重复灌水
    阶段二：去重        — 相同内容只保留一条
    阶段三：长度降序   — 越长的评论越可能含有具体反馈，排在前面优先纳入
    """
    seen = set()
    filtered = []
    for c in raw_comments:
        c = c.strip()
        # ① 字数太短
        if len(c) <= 4:
            continue
        # ② 纯 emoji
        if _EMOJI_RE.match(c):
            continue
        # ③ 纯数字 / 纯标点
        if _NOISE_RE.match(c):
            continue
        # ④ 重复字符（如 "哈哈哈哈哈哈"）
        if _REPEAT_RE.search(c) and len(set(c.replace(' ', ''))) <= 3:
            continue
        # ⑤ 去重
        key = c[:30]   # 用前 30 字做指纹，宽容同义不同末尾
        if key in seen:
            continue
        seen.add(key)
        filtered.append(c)

    # 按长度降序，确保内容最丰富的评论优先纳入 limit
    filtered.sort(key=len, reverse=True)
    return filtered[:limit]


def post_searcher_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    keywords = state.get("search_keywords", [])
    logging.info(f"==> Node: PostSearcher | Keywords: {keywords}")

    if emit:
        kw_display = keywords[0] if keywords else ""
        emit("node_start", {
            "node": "PostSearcher",
            "message": f"正在搜索「{kw_display}」相关热门帖子（按点赞数排序）..."
        })

    post_count = state.get("post_count", 3)
    min_likes = state.get("min_likes", 0)
    cookies = state.get("cookies", "")
    target_posts = []
    if keywords:
        main_keyword = keywords[0]
        posts = search_xhs_posts(keyword=main_keyword, require_num=post_count, min_likes=min_likes, cookies_str=cookies)
        target_posts.extend(posts)

    filter_hint = f"（最低点赞 {min_likes}）" if min_likes > 0 else ""

    if not target_posts and min_likes > 0:
        msg = f"未找到点赞数超过 {min_likes} 的帖子，建议降低最低点赞数后重试"
        logging.warning(msg)
        if emit:
            emit("node_done", {"node": "PostSearcher", "message": msg, "posts": []})
            emit("error", {"message": msg, "detail": msg})
        return {"target_posts": [], "current_post_index": 0}

    if emit:
        emit("node_done", {
            "node": "PostSearcher",
            "message": f"找到 {len(target_posts)} 篇有效热门帖子{filter_hint}，即将逐一抓取评论",
            "posts": [{"title": p.get("title", ""), "likes": p.get("likes", 0)} for p in target_posts]
        })

    return {"target_posts": target_posts, "current_post_index": 0}


def comment_extractor_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    index = state.get("current_post_index", 0)
    target_posts = state.get("target_posts", [])

    if index >= len(target_posts):
        return {}

    post = target_posts[index]
    post_url = post.get("url", "")
    post_title_short = post.get('title', '')[:20]
    total = len(target_posts)
    logging.info(f"==> Node: CommentExtractor | 准备抓取第 {index+1}/{total} 篇帖子: {post_title_short}")

    if emit:
        emit("node_start", {
            "node": "CommentExtractor",
            "message": f"正在获取第 {index+1}/{total} 篇：「{post_title_short}」的标题、正文及评论区...",
            "current": index + 1,
            "total": total
        })

    cookies = state.get("cookies", "")

    # ── Step 1: 获取帖子详情（标题 + 正文）────────────────────────
    detail = get_xhs_note_detail(post_url=post_url, cookies_str=cookies)
    article_title = detail.get("title") or post.get("title", "无标题")
    article_content = detail.get("content", "")
    logging.info(f"==> 帖子详情获取完毕。标题：{article_title[:30]}，正文长度：{len(article_content)}")

    if emit:
        emit("log", {
            "message": f"📖 「{article_title[:30]}」正文已获取（{len(article_content)} 字）"
        })

    # ── Step 2: 获取评论────────────────────────────────────────────
    comments = get_xhs_comments(post_url=post_url, cookies_str=cookies)
    logging.info(f"==> 评论抓取完毕！该贴共获取到 {len(comments)} 条原始文本。")

    cleaned_comments = _filter_comments(comments, limit=150)
    logging.info(f"==> 过滤后保留 {len(cleaned_comments)} 条高价值评论（原始 {len(comments)} 条）")

    if emit:
        emit("log", {
            "message": f"💬 「{article_title[:20]}」共抓取 {len(comments)} 条评论，过滤后保留 {len(cleaned_comments)} 条有效内容"
        })

    if emit:
        emit("node_done", {
            "node": "CommentExtractor",
            "message": f"第 {index+1}/{total} 篇抓取完毕：正文 {len(article_content)} 字，有效评论 {len(cleaned_comments)} 条",
            "current": index + 1,
            "total": total
        })

    # ── Step 3: 将标题+正文+评论作为一个结构化单元存入聚合列表──────
    existing_posts = state.get("aggregated_posts", []) or []
    existing_posts.append({
        "title": article_title,
        "content": article_content,
        "comments": cleaned_comments,
        "url": post_url,
        "likes": post.get("likes", 0),
    })

    return {"aggregated_posts": existing_posts}


def post_summarizer_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    """
    对当前帖子（正文 + 评论）单独做一次 LLM 摘要。
    每次调用只处理一篇帖子，Token 可控；多篇摘要汇总后再传给 NeedsAnalyzer。
    """
    aggregated_posts = state.get("aggregated_posts", []) or []
    if not aggregated_posts:
        return {}

    # 取最新压入的帖子（当前篇）
    post = aggregated_posts[-1]
    title = post.get("title", "无标题")
    content = post.get("content", "") or "（无正文）"
    comments = post.get("comments", [])
    index = len(aggregated_posts)   # 当前是第几篇
    total = len(state.get("target_posts", []))

    logging.info(f"==> Node: PostSummarizer | 正在总结第 {index}/{total} 篇：{title[:20]}")

    if emit:
        emit("log", {
            "message": f"🤖 正在对「{title[:20]}」做 AI 文本总结（正文 {len(content)} 字，{len(comments)} 条评论）..."
        })

    # 构造评论文本（全量评论，不截断，由 LLM 自行提炼）
    comments_text = "\n".join(f"- {c}" for c in comments) if comments else "（无评论）"

    prompt = PromptTemplate.from_template(
        "你是一个专业的市场需求分析师，正在分析一篇小红书爆款帖子。\n"
        "帖子标题：{title}\n"
        "博主正文：{content}\n"
        "评论区（共 {comment_count} 条，以下为经过质量过滤后的有效评论）：\n{comments_text}\n\n"
        "请对上述内容进行精炼总结，输出一个 JSON 对象，包含以下四个字段：\n"
        "1. \"post_theme\": 该帖子的核心主题（一句话概括博主分享的内容）\n"
        "2. \"key_products\": 帖子正文和评论中提及的产品或品牌列表。"
        "   格式：[{{\"name\":\"产品名\", \"attitude\":\"好评/差评/中立\", \"reason\":\"主要原因\"}}]\n"
        "3. \"pain_points\": 从评论中提炼的高频抱怨、吐槽或求推荐。"
        "   格式：[{{\"issue\":\"问题描述\", \"frequency\":\"高/中/低\", \"example\":\"典型用户原话（20字内）\"}}]\n"
        "4. \"unmet_needs\": 评论中明确表达的、目前产品尚未满足的诉求。"
        "   格式：[\"需求描述1\", \"需求描述2\", ...]\n"
        "请确保输出合法 JSON，不要有 Markdown 标记。"
    )

    try:
        response = llm.invoke(prompt.format(
            title=title,
            content=content,
            comment_count=len(comments),
            comments_text=comments_text
        ))
        clean = response.content.strip("` \n")
        if clean.startswith("json"):
            clean = clean[4:]
        summary = json.loads(clean)
    except Exception as e:
        logging.error(f"PostSummarizer 解析失败: {e}")
        summary = {
            "post_theme": title,
            "key_products": [],
            "pain_points": [],
            "unmet_needs": []
        }

    summary["title"] = title
    summary["likes"] = post.get("likes", 0)

    logging.info(f"==> 第 {index} 篇总结完毕：{len(summary.get('pain_points', []))} 个痛点，{len(summary.get('unmet_needs', []))} 个未满足需求")

    if emit:
        emit("log", {
            "message": f"✅ 「{title[:20]}」总结完成：{len(summary.get('pain_points', []))} 个痛点，{len(summary.get('unmet_needs', []))} 个潜在需求"
        })

    existing = state.get("aggregated_summaries", []) or []
    existing.append(summary)
    return {"aggregated_summaries": existing}

def needs_analyzer_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    logging.info("==> Node: NeedsAnalyzer | 汇总各篇摘要，进行横向综合分析")
    aggregated_summaries = state.get("aggregated_summaries", []) or []
    user_input = state["user_input"]

    if emit:
        emit("node_start", {
            "node": "NeedsAnalyzer",
            "message": f"正在综合 {len(aggregated_summaries)} 篇帖子的 AI 摘要，进行横向痛点与需求分析（约 15~30 秒）..."
        })

    if not aggregated_summaries:
        logging.warning("帖子摘要为空，无痛点可分析。")
        if emit:
            emit("node_done", {"node": "NeedsAnalyzer", "message": "数据为空，跳过分析"})
        return {"collected_needs": []}

    # 将各篇摘要序列化为供 LLM 阅读的 JSON（已是精炼数据，Token 极小）
    summaries_text = json.dumps(aggregated_summaries, ensure_ascii=False, indent=2)

    prompt = PromptTemplate.from_template(
        "你是一个专业的市场调研分析师和产品经理。\n"
        "以下是从小红书关于【{user_input}】的爆款帖子中，经过逐篇 AI 分析后得到的结构化摘要（JSON 格式）：\n"
        "=====\n{summaries_text}\n=====\n\n"
        "请基于以上多篇帖子的摘要，进行横向整合分析，输出一个 JSON 对象，包含以下四个字段：\n\n"
        "1. \"products_mentioned\"（提及的产品）\n"
        "   合并各篇中提及的产品，去重后评估整体口碑和来源。\n"
        "   格式：[{{\"name\": \"产品名称\", \"source\": \"博主推荐/用户评论/两者均有\", "
        "\"attitude\": \"推荐/避坑/对比/好评/差评/中立\", \"reason\": \"核心理由\"}}]\n\n"
        "2. \"pain_points_and_needs\"（抱怨与需求）\n"
        "   合并各篇痛点，按出现频率排序，相似问题合并。\n"
        "   格式：[{{\"type\": \"抱怨/需求/求推荐/求功能/选购困惑\", "
        "\"description\": \"具体描述\", \"frequency\": \"高/中/低\", \"example\": \"典型用户原话\"}}]\n\n"
        "3. \"user_personas\"（用户群体画像）\n"
        "   根据各篇摘要推断核心用户群体。\n"
        "   格式：[{{\"persona\": \"人群描述\", \"characteristics\": \"典型特征\", "
        "\"key_concerns\": \"该群体最关注的核心诉求\"}}]\n\n"
        "4. \"needs_satisfaction\"（需求满足度分析）\n"
        "   综合各篇 unmet_needs，总结哪些需求已满足、哪些有明显缺口。\n"
        "   格式：{{\"satisfied\": [\"已满足的需求描述\", ...], "
        "\"unsatisfied\": [\"未被满足的需求描述\", ...]}}\n\n"
        "请直接输出合法的 JSON 对象（包含以上四个键），不要有任何 Markdown 标记或多余文字。"
    )

    response = llm.invoke(prompt.format(user_input=user_input, summaries_text=summaries_text))

    try:
        clean_content = response.content.strip("` \n")
        if clean_content.startswith("json"):
            clean_content = clean_content[4:]
        analysis = json.loads(clean_content)
    except Exception as e:
        logging.error(f"解析需求分析结果失败: {e}, \n返回值：{response.content}")
        analysis = {}

    pain_count = len(analysis.get("pain_points_and_needs", []))
    product_count = len(analysis.get("products_mentioned", []))
    persona_count = len(analysis.get("user_personas", []))

    if emit:
        emit("node_done", {
            "node": "NeedsAnalyzer",
            "message": f"分析完成！识别出 {product_count} 款产品、{pain_count} 个痛点/需求、{persona_count} 类用户群体",
            "needs_count": pain_count
        })

    return {"collected_needs": [analysis]}


def report_generator_node(state: AgentState, emit: Optional[Callable] = None) -> Dict:
    logging.info("==> Node: ReportGenerator | 生成最终产业调研报告")
    needs = state.get("collected_needs", [])
    aggregated_posts = state.get("aggregated_posts", []) or []
    user_input = state["user_input"]

    if emit:
        emit("node_start", {
            "node": "ReportGenerator",
            "message": "正在撰写完整市场调研报告（约 15~30 秒）..."
        })

    if not needs:
        report = f"# {user_input} 市场需求调研报告\n\n未找到明显的受众需求和痛点。"
        if emit:
            emit("node_done", {"node": "ReportGenerator", "message": "报告生成完成"})
        return {"report_content": report}

    # 构建帖子概览摘要（供报告生成参考，列出每篇帖子标题和点赞数）
    posts_summary = "\n".join(
        f"- 《{p.get('title', '无标题')}》（点赞 {p.get('likes', 0)}，评论 {len(p.get('comments', []))} 条）"
        for p in aggregated_posts
    )

    prompt = PromptTemplate.from_template(
        "你是一个资深市场调研顾问和产品策略分析师。"
        "我们对【{user_input}】在小红书进行了深度调研，综合分析了以下 {post_count} 篇爆款帖子的标题、正文及评论区：\n"
        "{posts_summary}\n\n"
        "基于以上帖子内容，我们提取出了以下四个维度的结构化分析数据：\n"
        "=====\n{analysis}\n=====\n\n"
        "请根据以上内容撰写一份完整的《{user_input} 市场需求调研报告》，使用 Markdown 格式，严格包含以下六个章节：\n\n"
        "## 一、调研背景\n"
        "简述本次调研的方法（综合分析小红书爆款帖子的标题、正文内容及评论区）、数据规模与调研目标。\n\n"
        "## 二、市场现有产品概览\n"
        "汇总博主在正文中推荐的产品，以及评论区用户额外提及的产品，说明各自的来源（博主推荐 or 用户提及）、"
        "用户整体态度（好评/差评/对比/避坑），并总结被认可或被吐槽的核心原因。\n\n"
        "## 三、用户群体画像\n"
        "描述参与该话题讨论的核心用户群体（结合正文受众定位与评论区画像），"
        "分析各类群体的典型特征、使用场景与核心关注点。\n\n"
        "## 四、核心痛点与真实需求\n"
        "系统整理用户的抱怨、吐槽和明确诉求（包括对博主推荐产品的质疑与补充需求），"
        "按出现频率从高到低排列，每条需求需附上典型用户声音或表述方式。\n\n"
        "## 五、需求满足度评估\n"
        "对比分析：当前博主推荐及市场现有产品已较好满足了哪些需求？哪些需求存在明显供给缺口？"
        "特别指出：评论区用户对博主推荐方案的不满点或提出的改进建议。\n"
        "用【已满足 / 未满足】的方式清晰呈现。\n\n"
        "## 六、市场机会与产品建议\n"
        "面向普通个人创业者或小团队，给出 3~5 个基于真实用户声音的可落地产品机会。每条建议必须包含：\n"
        "（1）目标用户是谁；（2）他们遇到了什么具体问题（最好引用帖子或评论中的真实场景）；"
        "（3）市场现有产品在哪里做得不够好；（4）建议做一款什么产品，需要具备哪些具体功能或属性。\n"
        "示例格式：「普拉提爱好者在练习时容易打滑 → 现有防滑袜款式单一、防滑颗粒脱落快 "
        "→ 建议推出针对普拉提场景的防滑袜，采用大面积硅胶防滑底 + 时尚撞色设计，主打小红书女性用户」。\n"
        "禁止给出需要大规模资本或政策支持才能实现的宏观建议，所有建议必须是普通人能够独立执行的。\n\n"
        "报告语言应通俗易懂、观点清晰，结论需有数据或用户声音支撑，产品建议需落到具体的功能、材质、场景或人群。"
    )

    analysis_str = json.dumps(needs[0] if needs else {}, ensure_ascii=False, indent=2)
    response = llm.invoke(prompt.format(
        user_input=user_input,
        post_count=len(aggregated_posts),
        posts_summary=posts_summary,
        analysis=analysis_str
    ))

    if emit:
        emit("node_done", {
            "node": "ReportGenerator",
            "message": "✅ 市场调研报告生成完成！"
        })

    return {"report_content": response.content}
