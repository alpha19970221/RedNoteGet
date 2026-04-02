from typing import TypedDict, List, Dict, Any

class AgentState(TypedDict):
    user_input: str                  # 用户输入的内容，比如"笔记本支架"
    cookies: str                     # 用户提供的小红书 Cookie（由前端传入）
    post_count: int                  # 需要抓取的帖子数量（前端可配置，默认 3）
    min_likes: int                   # 有效帖子的最低点赞数（前端可配置，默认 0 表示不过滤）
    search_keywords: List[str]       # 搜索关键词列表
    target_posts: List[Dict]         # 抓取到的目标帖子列表
    current_post_index: int          # 当前处理的帖子索引
    aggregated_posts: List[Dict]     # 每篇帖子的完整数据：{title, content, comments, url, likes}
    aggregated_summaries: List[Dict] # 每篇帖子的 LLM 单独总结：{title, summary, key_products, pain_points, key_needs}
    collected_needs: List[Dict]      # LLM 筛选提取出来的最终需求、痛点总结
    report_content: str              # 最终的深度市场调研报告
    errors: List[str]                # 流程执行中的异常收集
