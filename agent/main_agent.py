import argparse
from loguru import logger
from agent.graph import create_agent

def run_research(user_input: str):
    logger.info("========== 初始化智能体执行工作流 ==========")
    agent = create_agent()
    
    # 构造初始 State
    initial_state = {
        "user_input": user_input,
        "cookies": "",           # CLI 模式从 Spider_XHS .env 自动读取
        "post_count": 3,
        "min_likes": 0,
        "search_keywords": [],
        "target_posts": [],
        "current_post_index": 0,
        "aggregated_posts": [],
        "collected_needs": [],
        "report_content": "",
        "errors": []
    }
    
    logger.info(f"开启 [ {user_input} ] 的市场需求调研：")
    
    # invoke 完整跑完图流程
    final_state = agent.invoke(initial_state)
    logger.info("========== 解析与工作流执行完毕 ==========")
    
    report = final_state.get("report_content", "")
    print("\n")
    print("=" * 60)
    print("                 最 终 调 研 报 告                ")
    print("=" * 60)
    print(report)
    print("=" * 60)
    print("\n")
    
    # 自动保存报告为 Markdown 文件
    import os
    from datetime import datetime
    
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "reports")
    os.makedirs(report_dir, exist_ok=True)
    
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_query = user_input.replace("/", "").replace(" ", "_")
    report_filename = f"{safe_query}_调研报告_{timestamp}.md"
    report_path = os.path.join(report_dir, report_filename)
    
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
        
    logger.info(f"✅ 调研报告已成功保存至本地文件：{report_path}")
    
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="小红书需求挖掘智能体 CLI 入口")
    parser.add_argument("--query", type=str, required=True, help="输入你想调研的产品/行业信息（例如：笔记本电脑支架、人体工学椅）")
    args = parser.parse_args()
    
    # 请确保设置了 OPENAI_API_KEY 环境变量
    run_research(args.query)
