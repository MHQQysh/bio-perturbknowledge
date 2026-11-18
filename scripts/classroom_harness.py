"""Local ECE449 classroom runner. It streams real calls and receipts to the report."""

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from threading import Condition, Lock, Thread
from time import perf_counter
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen
from uuid import uuid4

from classroom_deepseek import MODELS, review_paper
from discover_papers import discover
from fetch_classroom_inputs import ensure_inputs


ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "reports/workflow_runs/classroom-live"
REPORT = "ECE449-project-progress.html"
QUERY = "Lalli MA[Author] AND neuronal differentiation[Title] AND 2020[dp]"
PMID = "32887689"
GEO = "GSM4219576"
GEO_PAGE = f"https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc={GEO}"
GEO_SOFT = f"{GEO_PAGE}&targ=self&form=text&view=quick"
STATIC_ROOTS = {"reports", "skills", "data", "presentation", "scripts"}
STATIC_TYPES = {".html", ".py", ".md", ".json", ".jsonl", ".txt", ".csv", ".js", ".css", ".png", ".jpg", ".pptx", ".npz", ".xml"}



def json_text(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def local_url(path):
    return "/" + path.relative_to(ROOT).as_posix()


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_command(args, *, input_text=None, timeout=60):
    payload = input_text.encode("utf-8") if input_text is not None else None
    proc = subprocess.run(args, cwd=ROOT, input=payload, capture_output=True, timeout=timeout)
    if proc.returncode:
        error = proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"{Path(args[0]).name} 退出码 {proc.returncode}。{error[-900:]}")
    return proc.stdout.decode("utf-8", errors="replace").strip()


class Run:
    def __init__(self):
        self.id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:8]
        self.folder = RUNS / self.id
        self.folder.mkdir(parents=True)
        self.changed = Condition()
        self.events = []
        self.finished = False

    def emit(self, kind, **fields):
        with self.changed:
            event = {"id": len(self.events) + 1, "time": datetime.now().astimezone().isoformat(),
                     "type": kind, **fields}
            with (self.folder / "events.jsonl").open("a", encoding="utf-8") as file:
                file.write(json_text(event) + "\n")
            self.events.append(event)
            if kind in ("run/result", "run/error"):
                self.finished = True
            self.changed.notify_all()
        return event

    @contextmanager
    def activity(self, kind, title, args=None):
        start = perf_counter()
        event = self.emit(kind + "/call", title=title, input=args or {})
        result = {}
        try:
            yield result
        except Exception as error:
            self.emit(kind + "/result", call=event["id"], status="failed",
                      elapsed=round(perf_counter() - start, 2), output={"error": str(error)})
            raise
        else:
            self.emit(kind + "/result", call=event["id"], status="ok",
                      elapsed=round(perf_counter() - start, 2), output=result)

    @contextmanager
    def skill(self, name):
        path = ROOT / "skills" / name / "SKILL.md"
        with self.activity("skill", name, {"spec": local_url(path), "executor": "课堂固定流程"}) as result:
            payload = path.read_bytes()
            body = payload.decode("utf-8-sig")
            if not re.search(rf"(?m)^name: {re.escape(name)}\s*$", body):
                raise ValueError(f"Skill 名称与规范不一致：{name}")
            version = re.search(r'(?m)^\s*version:\s*"([^"]+)"', body)
            result.update(version=version[1] if version else "unknown", sha256=hashlib.sha256(payload).hexdigest())
            yield result


def verify_geo():
    request = Request(GEO_SOFT, headers={"User-Agent": "ECE449-classroom-demo/1.0"})
    with urlopen(request, timeout=8) as response:
        meta = response.read(160_000).decode("utf-8", errors="replace")
    if not re.search(rf"(?mi)^!Sample_geo_accession\s*=\s*{GEO}\s*$", meta):
        raise ValueError("GEO 未返回预期的样本元数据")
    return {key: value.strip() for key, value in re.findall(
        r"(?mi)^!Sample_(title|geo_accession|organism_ch1)\s*=\s*(.+)$", meta)}


def execute(run, options):
    provider = options["provider"]
    model = options.get("model")
    run.emit("run/start", title="公开数据课堂演示", query=QUERY, sample=GEO, mode="live",
             provider=provider, model=model)
    try:
        with run.skill("retrieve-link") as skill:
            with run.activity("tool", "检索论文 · discover_papers.py", {"provider": "PubMed", "query": QUERY}) as out:
                def search_step(endpoint, params):
                    return run.activity("search", {"esearch.fcgi": "ESearch · 查找 PMID",
                                                   "efetch.fcgi": "EFetch · 获取论文元数据"}[endpoint], params)

                papers = discover(QUERY, 5, step=search_step)
                path = run.folder / "paper-discovery.json"
                write_json(path, papers)
                selected = next((item for item in papers["papers"] if item["pmid"] == PMID), None)
                if selected is None:
                    raise ValueError("当前检索未命中课堂案例，流程已停止")
                out.update(total_hits=papers["total_search_hits"], returned=papers["returned_papers"],
                           paper={key: selected[key] for key in ("pmid", "title", "url")},
                           files={"检索回执": local_url(path)})
            with run.activity("tool", "定位 GEO 样本与源文件", {"sample": GEO, "url": GEO_PAGE}) as out:
                try:
                    out.update(metadata=verify_geo(), source="live_geo_metadata")
                except (OSError, ValueError) as error:
                    out.update(source="cached_source_manifest", note=f"GEO 实时元数据不可用，继续使用已归档来源：{error}")
                out.update(sample=GEO, bulk_files="已归档的公开表达矩阵、条码、基因 ID 和作者标签")
                geo = {"sample": GEO, "url": GEO_PAGE, "source": out["source"],
                       "metadata": out.get("metadata", {"title": "LUHMES Run 2 (archived GEO manifest)"})}
            with run.activity("tool", "读取论文正文与补充材料 · paper_intake.py",
                              {"query": QUERY, "storage": "本地本轮目录"}) as out:
                intake_dir = run.folder / "paper-intake"
                raw = run_command([sys.executable, "-X", "utf8", str(ROOT / "scripts/paper_intake.py"),
                                   "--query", QUERY, "--limit", "1", "--max-fulltext", "1",
                                   "--max-supplements", "5", "--output", str(intake_dir)], timeout=180)
                intake_status = json.loads(raw)
                details = json.loads((intake_dir / "intake.json").read_text(encoding="utf-8"))
                out.update(statuses=intake_status["statuses"], fulltext_downloaded=intake_status["fulltext_downloaded"],
                           supplements=details[0]["supplements"] if details else [],
                           files={"论文材料获取回执": local_url(intake_dir / "intake.json"),
                                  "论文原文 XML": local_url(intake_dir / PMID / "article.xml"),
                                  "候选证据段落": local_url(intake_dir / PMID / "evidence-candidates.json")})
            if provider == "deepseek":
                with run.activity("model", "DeepSeek · 论文与公开样本初审",
                                  {"model": model, "pmid": PMID, "sample": GEO,
                                   "evidence": "公开标题、摘要、GEO 样本元数据和论文定位"}) as out:
                    assessment = review_paper(options.pop("key"), model, selected, geo)
                    path = run.folder / "deepseek-review.json"
                    write_json(path, assessment)
                    out.update(assessment, files={"DeepSeek 审核结果": local_url(path)})
                if not assessment["eligible"]:
                    raise ValueError("DeepSeek 判定候选证据不足。请打开初审回执查看理由。")
            local_inputs = ROOT / "data/GSM4219576"
            with run.activity("tool", "准备 GEO 数据 · 首次下载并核验", {"sample": GEO, "source": GEO_PAGE}) as out:
                out.update(ensure_inputs(local_inputs))
            with run.activity("tool", "关联细胞并生成候选 · classroom_lalli.py prepare",
                              {"sample": GEO, "executor": "本地", "source": "本地公开 GEO 原文件"}) as out:
                python = Path(sys.executable)
                matrix = local_inputs / "GSM4219576_Run2_matrix_fresh.mtx.gz"
                prepared = json.loads(run_command([str(python), "-X", "utf8",
                    str(ROOT / "scripts/classroom_lalli.py"), "prepare", str(run.folder),
                    "--inputs", str(local_inputs), "--matrix", str(matrix)], timeout=120))
                out.update(run_folder=str(run.folder), **prepared)
            skill.update(identified_paper=PMID, sample=GEO, handoff_candidates=prepared["candidates"])

        with run.skill("validate-compare-export") as skill:
            with run.activity("tool", "读取并核验扰动和对照 · read_record()",
                              {"records": prepared["candidates"], "checks": ["样本", "细胞数量", "两组互斥", "标签"]}) as out:
                review = json.loads(run_command([str(python), "-X", "utf8",
                    str(ROOT / "scripts/classroom_lalli.py"), "verify", str(run.folder)], timeout=120))
                receipts = review["records"]
                if len(receipts) != prepared["candidates"]:
                    raise ValueError("本机读取的回执数量不正确")
                out.update(review)

            with run.activity("tool", "计算表达响应 · classroom_lalli.py compute",
                              {"admitted": len(receipts), "sample": GEO}) as out:
                out.update(json.loads(run_command([str(python), "-X", "utf8",
                    str(ROOT / "scripts/classroom_lalli.py"), "compute", str(run.folder)], timeout=120)))

            with run.activity("tool", "保存本轮产物 · 本地", {"storage": str(run.folder)}) as out:
                response_files = sorted(run.folder.glob("lalli2020_Run2_*_response.csv"))
                (run.folder / "response-sha256.txt").write_text(
                    "".join(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n" for path in response_files),
                    encoding="utf-8")
                out["files"] = {"候选交接": local_url(run.folder / "lalli-handoff.json"),
                                "矩阵核验": local_url(run.folder / "lalli-receipt.json"),
                                "本地产物指纹": local_url(run.folder / "response-sha256.txt")}

            with run.activity("tool", "校验并导出 · validate_lalli_outputs.py",
                              {"checks": ["本轮文件 SHA256", "逐基因均值差", "Input / Target 基因对齐"]}) as out:
                python = Path(sys.executable)
                if not python.is_file():
                    raise FileNotFoundError("缺少项目的 .venv-response Python 环境")
                validator = ROOT / "scripts/classroom_validate.py"
                run_command([str(python), "-X", "utf8", str(validator), str(run.folder), "--origin", "local"], timeout=120)
                result = json.loads((run.folder / "lalli-validation.json").read_text(encoding="utf-8"))
                if result["status"] != "passed":
                    raise ValueError("本轮同批校验未通过")
                out.update(shape=result["array_shape"], hashes=result["csv_sha256_matched"],
                           gene_delta_recomputed=result["gene_delta_recomputed"],
                           files={"导出校验": local_url(run.folder / "lalli-validation.json"),
                                  "模型数组": local_url(run.folder / "lalli-model-arrays.npz")})
            skill.update(admitted=len(receipts), scope="同一 GEO 样本的描述性比较")

        run.emit("run/result", title="运行完成", records=receipts,
                 shape=result["array_shape"], notebook=local_url(run.folder / "events.jsonl"),
                 files={"结构化记录": local_url(run.folder / "records.json"),
                        "候选交接": local_url(run.folder / "lalli-handoff.json"),
                        "核验回执": local_url(run.folder / "lalli-receipt.json"),
                        "导出校验": local_url(run.folder / "lalli-validation.json"),
                        "模型数组": local_url(run.folder / "lalli-model-arrays.npz")})
    except Exception as error:
        run.emit("run/error", title="运行未完成", error=str(error), notebook=local_url(run.folder / "events.jsonl"))
    finally:
        options.pop("key", None)


RUN_LOCK = Lock()
ACTIVE = None


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    @property
    def authority(self):
        return f"127.0.0.1:{self.server.server_port}"

    def reply(self, body, status=200, mime="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json_text(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        global ACTIVE
        if (self.headers.get("Host") != self.authority or
                self.headers.get("Origin") != f"http://{self.authority}"):
            self.close_connection = True
            return self.reply({"error": "请从本机运行台启动"}, 403)
        route = urlsplit(self.path).path
        if route not in {"/api/runs", "/api/research-search"}:
            return self.reply({"error": "未知路径"}, 404)
        length = self.headers.get("Content-Length", "0")
        if not re.fullmatch(r"[0-9]{1,4}", length) or int(length) > 4096 or self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self.reply({"error": "启动请求无效"}, 400)
        if int(length):
            if self.headers.get_content_type() != "application/json":
                self.close_connection = True
                return self.reply({"error": "需要 JSON 请求"}, 415)
            try:
                settings = json.loads(self.rfile.read(int(length)))
            except (ValueError, UnicodeError):
                return self.reply({"error": "启动设置不是有效的 JSON"}, 400)
        else:
            settings = {"provider": "local"}
        if route == "/api/research-search":
            if not isinstance(settings, dict):
                return self.reply({"error": "需要搜索对象"}, 400)
            query = settings.get("query")
            if not isinstance(query, str) or not query.strip() or len(query) > 300:
                return self.reply({"error": "请输入 1–300 字的论文主题、题名或作者"}, 400)
            try:
                result = run_command([sys.executable, "-I", "-X", "utf8", str(ROOT / "scripts/research_web_search.py"),
                                      "--query", query.strip(), "--limit", "10"], timeout=50)
                output = json.loads(result)
                saved = Path(output["output"]).resolve()
                if not saved.is_relative_to((ROOT / "downloads/web-search").resolve()):
                    raise ValueError("搜索结果位置无效")
                return self.reply(json.loads(saved.read_text(encoding="utf-8")))
            except (RuntimeError, ValueError, OSError, subprocess.TimeoutExpired) as exc:
                return self.reply({"error": f"公开文献搜索失败：{type(exc).__name__}"}, 502)
        if not isinstance(settings, dict) or settings.get("provider") not in ("deepseek", "local"):
            return self.reply({"error": "请选择有效的运行模式"}, 400)
        options = {"provider": settings["provider"]}
        if options["provider"] == "deepseek":
            key, model = settings.get("api_key"), settings.get("model")
            if not isinstance(key, str) or not re.fullmatch(r"[!-~]{8,512}", key):
                return self.reply({"error": "请输入有效的 DeepSeek API Key"}, 400)
            if model not in MODELS:
                return self.reply({"error": "请选择可用的 DeepSeek 模型"}, 400)
            options.update(key=key, model=model)
        with RUN_LOCK:
            if ACTIVE and not ACTIVE.finished:
                return self.reply({"run_id": ACTIVE.id, "already_running": True}, 200)
            ACTIVE = Run()
            run = ACTIVE
            Thread(target=execute, args=(run, options), daemon=True).start()
        self.reply({"run_id": run.id}, 201)

    def do_GET(self):
        if self.headers.get("Host") != self.authority:
            return self.reply({"error": "请使用 127.0.0.1 地址"}, 403)
        route = unquote(urlsplit(self.path).path)
        if route == "/api/status":
            with RUN_LOCK:
                run_id = ACTIVE.id if ACTIVE else None
            if not run_id:
                run_id = max((p.name for p in RUNS.iterdir() if (p / "events.jsonl").is_file()), default=None)
            return self.reply({"service": "ece449-classroom", "run_id": run_id, "deepseek_models": MODELS})
        match = re.fullmatch(r"/api/runs/(\d{8}-\d{6}-[a-f0-9]{8})/events", route)
        if match:
            return self.stream(match[1])

        rel = PurePosixPath(route.lstrip("/") or REPORT)
        if any(part.startswith(".") for part in rel.parts) or (
                rel.as_posix() not in {REPORT, "task-guide.html", "README.md", "README.txt", "DELIVERY.txt", "requirements.txt", "requirements-remote.txt",
                                      "项目完成逻辑与讲稿.md", "project_report.html"} and
                (not rel.parts or rel.parts[0] not in STATIC_ROOTS or rel.suffix not in STATIC_TYPES)):
            return self.reply({"error": "未找到文件"}, 404)
        path = (ROOT / str(rel)).resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            return self.reply({"error": "未找到文件"}, 404)
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        if path.suffix in {".html", ".md", ".txt", ".json", ".jsonl", ".js", ".css", ".csv", ".py"}:
            mime += "; charset=utf-8"
        self.reply(path.read_bytes(), mime=mime)

    def stream(self, run_id):
        with RUN_LOCK:
            live = ACTIVE if ACTIVE and ACTIVE.id == run_id else None
        path = RUNS / run_id / "events.jsonl"
        if not live and not path.is_file():
            return self.reply({"error": "找不到运行"}, 404)
        try:
            cursor = max(0, int(self.headers.get("Last-Event-ID", "0")))
        except ValueError:
            cursor = 0
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        try:
            if not live:
                archived = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
                if not archived or archived[-1]["type"] not in {"run/result", "run/error"}:
                    archived.append({"id": len(archived) + 1, "type": "run/error", "title": "进程已中断",
                                     "error": "服务退出前未收到此轮的完成回执，可以重新启动。"})
                for event in archived[cursor:]:
                    self.send_event(event)
                return
            while True:
                with live.changed:
                    if cursor >= len(live.events) and not live.finished:
                        live.changed.wait(12)
                    batch = live.events[cursor:]
                    finished = live.finished
                for event in batch:
                    self.send_event(event)
                    cursor = event["id"]
                if finished:
                    return
                if not batch:
                    self.wfile.write(b": waiting\n\n")
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return

    def send_event(self, event):
        self.wfile.write(f"id: {event['id']}\ndata: {json_text(event)}\n\n".encode("utf-8"))
        self.wfile.flush()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8895)
    parser.add_argument("--open", action="store_true")
    settings = parser.parse_args()
    port = settings.port
    RUNS.mkdir(parents=True, exist_ok=True)
    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"ECE449 课堂运行台 · http://127.0.0.1:{server.server_port}/#classroom-demo", flush=True)
        if settings.open:
            import webbrowser
            webbrowser.open(f"http://127.0.0.1:{server.server_port}/#classroom-demo")
        server.serve_forever()


if __name__ == "__main__":
    main()
