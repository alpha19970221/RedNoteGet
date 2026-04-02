import os
import uuid
import queue
import threading
import json
import asyncio
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# ── 路径配置（使用绝对路径，防止 xhs_client 的 os.chdir 影响） ──────────────
BASE_DIR = Path(__file__).parent.resolve()
FRONTEND_DIR = BASE_DIR / "frontend"
REPORTS_DIR = BASE_DIR / "reports"

# ── 初始化 FastAPI ────────────────────────────────────────────────────────────
app = FastAPI(title="RedNote Market Intelligence Agent", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 任务注册表（单用户场景，内存存储即可） ───────────────────────────────────
task_store: dict[str, dict] = {}


# ── 请求/响应模型 ─────────────────────────────────────────────────────────────
class ResearchRequest(BaseModel):
    query: str
    cookies: str = ""
    post_count: int = 3
    min_likes: int = 0


# ── 路由：前端静态文件 ────────────────────────────────────────────────────────
@app.get("/")
async def serve_index():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# ── 路由：启动调研任务 ────────────────────────────────────────────────────────
@app.post("/api/research")
async def start_research(request: ResearchRequest):
    if not request.query.strip():
        raise HTTPException(status_code=400, detail="调研话题不能为空")
    if not request.cookies.strip():
        raise HTTPException(status_code=400, detail="请先在设置中填写小红书 Cookie")

    task_id = str(uuid.uuid4())
    q: queue.Queue = queue.Queue()

    task_store[task_id] = {
        "queue": q,
        "status": "running",
        "query": request.query,
        "created_at": datetime.now().isoformat()
    }

    def emit(event_type: str, data: dict):
        """线程安全地向 SSE 队列推送事件"""
        q.put({"type": event_type, "data": data})

    def run_agent_thread():
        """在后台线程中运行 LangGraph Agent"""
        try:
            # 延迟导入，确保 xhs_client 的路径初始化已完成
            from agent.graph import create_agent

            agent = create_agent(emit=emit)
            initial_state = {
                "user_input": request.query,
                "cookies": request.cookies.strip(),
                "post_count": max(1, min(request.post_count, 20)),
                "min_likes": max(0, request.min_likes),
                "search_keywords": [],
                "target_posts": [],
                "current_post_index": 0,
                "aggregated_posts": [],
                "collected_needs": [],
                "report_content": "",
                "errors": []
            }

            final_state = agent.invoke(initial_state)
            report = final_state.get("report_content", "")

            # 保存报告文件
            REPORTS_DIR.mkdir(exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_query = request.query.replace("/", "").replace(" ", "_").replace("\\", "")
            filename = f"{safe_query}_调研报告_{timestamp}.md"
            filepath = REPORTS_DIR / filename
            filepath.write_text(report, encoding="utf-8")

            task_store[task_id]["status"] = "done"
            emit("done", {"report": report, "filename": filename})

        except Exception as e:
            import traceback
            err_msg = traceback.format_exc()
            task_store[task_id]["status"] = "error"
            emit("error", {"message": str(e), "detail": err_msg})
        finally:
            q.put(None)  # 哨兵：通知 SSE 生成器结束

    thread = threading.Thread(target=run_agent_thread, daemon=True)
    thread.start()

    return {"task_id": task_id, "query": request.query}


# ── 路由：SSE 实时进度流 ──────────────────────────────────────────────────────
@app.get("/api/research/{task_id}/stream")
async def stream_research(task_id: str):
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail="任务不存在")

    q = task_store[task_id]["queue"]
    loop = asyncio.get_event_loop()

    async def event_generator():
        try:
            while True:
                # 在线程池中阻塞等待队列消息，不阻塞事件循环
                item = await loop.run_in_executor(None, q.get)
                if item is None:
                    # 哨兵到达，发送结束信号
                    yield f"data: {json.dumps({'type': 'end'}, ensure_ascii=False)}\n\n"
                    break
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            # 客户端断开连接
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        }
    )


# ── 路由：历史报告列表 ────────────────────────────────────────────────────────
@app.get("/api/reports")
async def list_reports():
    REPORTS_DIR.mkdir(exist_ok=True)
    reports = []
    for f in sorted(REPORTS_DIR.glob("*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        reports.append({
            "filename": f.name,
            "size": f.stat().st_size,
            "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        })
    return {"reports": reports}


# ── 路由：获取指定报告内容 ────────────────────────────────────────────────────
@app.get("/api/reports/{filename}")
async def get_report(filename: str):
    # 防止路径遍历攻击
    filepath = (REPORTS_DIR / filename).resolve()
    if not str(filepath).startswith(str(REPORTS_DIR)):
        raise HTTPException(status_code=400, detail="非法路径")
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="报告不存在")

    content = filepath.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


# ── 启动入口 ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
