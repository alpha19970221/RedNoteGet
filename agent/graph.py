from functools import partial
from langgraph.graph import StateGraph, START, END
from agent.state import AgentState
from agent.nodes import (
    keyword_generator_node,
    post_searcher_node,
    comment_extractor_node,
    post_summarizer_node,
    needs_analyzer_node,
    report_generator_node
)


def should_continue_fetching(state: AgentState):
    """
    判断是否还需要抓取下一篇帖子的评论。
    条件路由函数（从 PostSummarizer 出发）
    """
    idx = state.get("current_post_index", 0)
    target_posts = state.get("target_posts", [])

    if idx < len(target_posts) - 1:
        return "continue"
    else:
        return "analyze"


def increment_index(state: AgentState):
    """一个简单节点：递增索引，走向下一篇帖子"""
    idx = state.get("current_post_index", 0)
    return {"current_post_index": idx + 1}


def create_agent(emit=None):
    workflow = StateGraph(AgentState)

    # 如果传入了 emit 回调，则用 partial 将其绑定到各节点
    if emit:
        kw_node = partial(keyword_generator_node, emit=emit)
        ps_node = partial(post_searcher_node, emit=emit)
        ce_node = partial(comment_extractor_node, emit=emit)
        sum_node = partial(post_summarizer_node, emit=emit)
        na_node = partial(needs_analyzer_node, emit=emit)
        rg_node = partial(report_generator_node, emit=emit)
    else:
        kw_node = keyword_generator_node
        ps_node = post_searcher_node
        ce_node = comment_extractor_node
        sum_node = post_summarizer_node
        na_node = needs_analyzer_node
        rg_node = report_generator_node

    # 注册节点
    workflow.add_node("KeywordGenerator", kw_node)
    workflow.add_node("PostSearcher", ps_node)
    workflow.add_node("CommentExtractor", ce_node)
    workflow.add_node("PostSummarizer", sum_node)   # 新增：逐篇 LLM 总结
    workflow.add_node("NeedsAnalyzer", na_node)
    workflow.add_node("ReportGenerator", rg_node)
    workflow.add_node("IncrementIndex", increment_index)

    # 绘制执行流
    workflow.add_edge(START, "KeywordGenerator")
    workflow.add_edge("KeywordGenerator", "PostSearcher")
    workflow.add_edge("PostSearcher", "CommentExtractor")

    # CommentExtractor → PostSummarizer（逐篇总结）
    workflow.add_edge("CommentExtractor", "PostSummarizer")

    # PostSummarizer 完成后做条件路由
    workflow.add_conditional_edges(
        "PostSummarizer",
        should_continue_fetching,
        {
            "continue": "IncrementIndex",   # 递增并抓取下一篇
            "analyze": "NeedsAnalyzer"      # 全部总结完毕，去汇总分析
        }
    )

    # IncrementIndex 之后重新绕回 CommentExtractor
    workflow.add_edge("IncrementIndex", "CommentExtractor")

    workflow.add_edge("NeedsAnalyzer", "ReportGenerator")
    workflow.add_edge("ReportGenerator", END)

    return workflow.compile()
