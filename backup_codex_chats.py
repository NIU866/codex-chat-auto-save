"""Local Codex conversation backup. Python 3.9+, standard library only."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile
import time
import zipfile


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def safe_child(root, relative):
    result = (root / relative).resolve()
    if result == root.resolve() or root.resolve() not in result.parents:
        raise ValueError("文件路径超出了备份目录。")
    return result


def discover(source):
    files = {}
    for folder in ("sessions", "archived_sessions"):
        root = source / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            if not (path.name.endswith(".jsonl") or path.name.endswith(".jsonl.zst")):
                continue
            resolved = path.resolve()
            if source not in resolved.parents:
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                continue
            files[path.relative_to(source).as_posix()] = {
                "path": path, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns
            }
    index = source / "session_index.jsonl"
    if index.is_file() and not index.is_symlink():
        stat = index.stat()
        files["session_index.jsonl"] = {
            "path": index, "size": stat.st_size, "mtime_ns": stat.st_mtime_ns
        }
    return files


def copy_snapshot(info, target):
    """Copy only the bytes present at stat time; a growing file is retried next cycle."""
    target.parent.mkdir(parents=True, exist_ok=True)
    remaining = info["size"]
    digest = hashlib.sha256()
    with info["path"].open("rb") as src, target.open("wb") as dst:
        while remaining:
            block = src.read(min(1024 * 1024, remaining))
            if not block:
                raise OSError("聊天文件在备份过程中缩短，请重试：" + str(info["path"]))
            dst.write(block)
            digest.update(block)
            remaining -= len(block)
    return digest.hexdigest()


def session_titles(index):
    result = {}
    if index.exists():
        for line in index.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            sid = row.get("id") or row.get("thread_id")
            title = row.get("thread_name") or row.get("title")
            if sid and title:
                result[str(sid)] = str(title)
    return result


def message_text(content):
    if isinstance(content, str):
        return content
    parts = []
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict) and part.get("type") in ("input_text", "output_text", "text"):
            parts.append(str(part.get("text", "")))
    return "\n".join(parts).strip()


def export_session(path):
    messages, fallback, meta = [], [], {}
    invalid = 0
    with path.open(encoding="utf-8-sig", errors="replace") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                invalid += 1
                continue
            payload = row.get("payload") or {}
            if not isinstance(payload, dict):
                continue
            kind = row.get("type")
            if kind == "session_meta":
                meta = {"id": payload.get("id") or payload.get("session_id"),
                        "cwd": payload.get("cwd", ""), "timestamp": payload.get("timestamp", "")}
            elif kind == "response_item" and payload.get("type") == "message":
                role = payload.get("role")
                if role not in ("user", "assistant"):
                    continue
                if payload.get("channel") not in (None, "final", "commentary"):
                    continue
                if payload.get("phase") not in (None, "commentary", "final_answer", "final"):
                    continue
                body = message_text(payload.get("content"))
                # Runtime envelopes provide no conversational context for a new account.
                if role == "user" and body.lstrip().startswith(("<environment_context>", "<permissions instructions>")):
                    continue
                if body:
                    messages.append({"role": role, "text": body,
                                     "phase": payload.get("phase"), "timestamp": row.get("timestamp", "")})
            elif kind == "event_msg" and payload.get("type") in ("user_message", "agent_message"):
                body = payload.get("message", "")
                if isinstance(body, str) and body.strip():
                    fallback.append({"role": "user" if payload["type"] == "user_message" else "assistant",
                                     "text": body.strip(), "timestamp": row.get("timestamp", "")})
    return meta, messages or fallback, invalid


def clean_filename(text):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(text)).strip(" .")[:55] or "未命名对话"


def trim_archives(root, keep):
    root = root.resolve()
    pattern = re.compile(r"codex-chats-\d{8}-\d{6}-\d{6}\.zip$")
    candidates = sorted((p for p in root.iterdir() if p.is_file() and not p.is_symlink()
                         and pattern.fullmatch(p.name)), reverse=True)
    for path in candidates[keep:]:
        if path.resolve().parent != root:
            raise ValueError("拒绝清理备份目录以外的文件。")
        path.unlink()


def backup(args):
    source, output = args.source.resolve(), args.output.resolve()
    if not source.is_dir():
        raise ValueError("未找到 Codex 记录目录：" + str(source))
    if output == source or source in output.parents:
        raise ValueError("请把备份保存到 Codex 记录目录以外。")
    files = discover(source)
    session_count = sum(k.startswith(("sessions/", "archived_sessions/")) for k in files)
    if session_count == 0:
        raise ValueError("未找到本地聊天记录；普通 ChatGPT 云端聊天不在此目录。")
    output.mkdir(parents=True, exist_ok=True)
    archives = output / "snapshots"
    archives.mkdir(exist_ok=True)
    state_path = output / "state.json"
    inventory_path = Path(__file__).with_name("project_inventory.json")
    inventory = read_json(inventory_path, {})
    signature = {k: {"size": v["size"], "mtime_ns": v["mtime_ns"]} for k, v in files.items()}
    inventory_hash = hashlib.sha256(json.dumps(inventory, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    state = read_json(state_path, {})
    previous = safe_child(archives, state["archive"]) if state.get("archive") else None
    if not args.force and state.get("signature") == signature and state.get("inventory_hash") == inventory_hash and previous and previous.exists():
        print("[" + dt.datetime.now().strftime("%H:%M:%S") + "] 对话没有变化，保留现有备份。", flush=True)
        return {"changed": False, "archive": str(previous)}
    now = dt.datetime.now().astimezone()
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    archive_name = "codex-chats-" + stamp + ".zip"
    archive = safe_child(archives, archive_name)
    notes = []
    manifest = {"created_at": now.isoformat(), "source": str(source), "files": {},
                "scope": "Local Codex sessions and archived_sessions; project files and cloud chats are excluded."}
    # tempfile cleanup is confined to the explicitly resolved backup directory.
    with tempfile.TemporaryDirectory(prefix=".backup-working-", dir=str(output)) as temp_name:
        stage = Path(temp_name).resolve()
        if output not in stage.parents:
            raise ValueError("临时目录不在指定备份位置。")
        for relative, info in files.items():
            destination = safe_child(stage, relative)
            sha = copy_snapshot(info, destination)
            manifest["files"][relative] = {"size": destination.stat().st_size, "sha256": sha}
        titles = {str(t["id"]): t.get("title", "") for t in inventory.get("threads", [])}
        titles.update(session_titles(stage / "session_index.jsonl"))
        thread_seed = {str(t["id"]): t for t in inventory.get("threads", [])}
        text_root = stage / "聊天文本"
        text_root.mkdir()
        entries, compressed = [], []
        for relative in files:
            if relative == "session_index.jsonl":
                continue
            filename = Path(relative).name
            match = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27,})\.jsonl", filename)
            sid = match.group(1) if match else Path(filename).stem
            if filename.endswith(".zst"):
                compressed.append({"id": sid, "raw": relative, "title": titles.get(sid, "历史对话")})
                continue
            meta, messages, invalid = export_session(safe_child(stage, relative))
            sid = str(meta.get("id") or sid)
            title = titles.get(sid) or next((m["text"].replace("\n", " ")[:60] for m in messages if m["role"] == "user"), "未命名对话")
            cwd = meta.get("cwd") or thread_seed.get(sid, {}).get("cwd", "")
            export_name = clean_filename(title) + "_" + sid[-8:] + ".md"
            lines = ["# " + title, "", "- 备份时间：" + now.isoformat(), "- 会话编号：`" + sid + "`",
                     "- 项目目录：`" + str(cwd) + "`", "- 原始记录：`" + relative + "`", "",
                     "以下为用户消息和助手回复的文本；图片、附件和项目文件请结合原文件阅读。", ""]
            if invalid:
                notes.append(relative + "：跳过 " + str(invalid) + " 行未完整或无法解析的数据；原始字节已备份。")
                lines += ["> 本次读取遇到未完整或无法解析的记录；正在写入的内容可在下一次备份补齐。", ""]
            for message in messages:
                speaker = "你" if message["role"] == "user" else "助手"
                lines += ["## " + speaker + (" · " + message["timestamp"] if message["timestamp"] else ""), "", message["text"], ""]
            (text_root / export_name).write_text("\n".join(lines), encoding="utf-8")
            user_last = next((m["text"] for m in reversed(messages) if m["role"] == "user"), "未提取到用户消息。")
            assistant_last = next((m["text"] for m in reversed(messages) if m["role"] == "assistant" and m.get("phase") in ("final", "final_answer")), "")
            entries.append({"id": sid, "title": title, "cwd": cwd, "file": export_name,
                            "last_request": user_last, "last_answer": assistant_last, "message_count": len(messages)})
        index = ["# 项目接续索引", "", "生成时间：" + now.isoformat(), "",
                 "换号后先尝试打开原对话。若看不到，在新对话中提供本索引和对应的聊天文本，再打开原项目目录继续。",
                 "这是文件快照；仍在运行的对话可能尚未完成，切换账号前先等待任务结束。", "", "## 项目位置", ""]
        for project in inventory.get("projects", []):
            index.append("- " + project["label"] + "：`" + project["path"] + "`")
        index += ["", "## 可直接阅读的对话", ""]
        for entry in entries:
            index += ["### " + entry["title"], "", "- 项目目录：`" + str(entry["cwd"]) + "`",
                      "- 聊天文本：[打开记录](聊天文本/" + entry["file"] + ")",
                      "- 提取消息数：" + str(entry["message_count"]), "", "最近用户请求：", "", entry["last_request"][:1800], ""]
            if entry["last_answer"]:
                index += ["最近一次已完成回复（最多 3500 字；完整内容见聊天文本）：", "", entry["last_answer"][:3500], ""]
        if compressed:
            index += ["## 已保存的较早压缩记录", "", "以下原始 .jsonl.zst 已完整备份；此无依赖脚本不展开旧压缩格式。", ""]
            index += ["- " + item["title"] + "：`" + item["raw"] + "`" for item in compressed]
        if notes:
            index += ["", "## 本次读取说明", ""] + ["- " + note for note in notes]
        (stage / "项目接续索引.md").write_text("\n".join(index) + "\n", encoding="utf-8")
        write_json(stage / "project_inventory.json", inventory)
        write_json(stage / "backup_manifest.json", manifest)
        (stage / "备份范围说明.txt").write_text(
            "此备份包含本地 Codex 会话原始记录、归档记录、会话名称索引和可阅读文本。\n"
            "项目目录中的实验数据、代码、图片、附件等仍位于原文件夹，需要另行备份。\n"
            "普通 ChatGPT 的云端聊天不包含在内，请使用 ChatGPT 的数据导出。\n"
            "备份不包含 auth.json、API 密钥配置和其他登录文件。\n"
            ".jsonl.zst 旧压缩记录只保留原文件；当前 .jsonl 会导出用户及助手可见文本。\n"
            "原记录可以用于本地恢复排查；备份不保证跨账号自动恢复原侧栏。\n"
            "备份包含私人聊天，请保存在你自己的可信设备。\n", encoding="utf-8")
        temporary_archive = archive.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(temporary_archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
                for file in sorted(stage.rglob("*")):
                    if file.is_file():
                        bundle.write(file, file.relative_to(stage).as_posix())
            with zipfile.ZipFile(temporary_archive) as bundle:
                bad_file = bundle.testzip()
                if bad_file:
                    raise OSError("压缩包校验失败：" + bad_file)
            os.replace(temporary_archive, archive)
        finally:
            if temporary_archive.exists():
                temporary_archive.unlink()
        # Publish readable text separately so a new conversation can access it directly.
        latest = output / "latest"
        latest.mkdir(exist_ok=True)
        (latest / "聊天文本").mkdir(exist_ok=True)
        for file in text_root.iterdir():
            shutil.copy2(file, latest / "聊天文本" / file.name)
        for name in ("项目接续索引.md", "project_inventory.json", "backup_manifest.json", "备份范围说明.txt"):
            shutil.copy2(stage / name, latest / name)
    write_json(state_path, {"signature": signature, "inventory_hash": inventory_hash,
                            "archive": archive_name, "created_at": now.isoformat()})
    try:
        trim_archives(archives, args.keep)
    except OSError as exc:
        notes.append("清理旧备份失败，但本次新备份已完成：" + str(exc))
    result = {"changed": True, "archive": str(archive), "sessions": session_count,
              "readable_chats": len(entries), "compressed_chats": len(compressed),
              "index": str(output / "latest" / "项目接续索引.md"), "notes": notes}
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
    return result


class RunLock:
    def __init__(self, path):
        self.path = path
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+b")
        self.handle.seek(0)
        self.handle.write(b"0")
        self.handle.flush()
        self.handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            raise ValueError("已有一个备份程序正在使用此目录；持续保存时无需再启动第二个窗口。")
        return self

    def __exit__(self, *unused):
        if self.handle:
            self.handle.close()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="自动备份本机 Codex 对话并导出聊天文本。")
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex"))
    parser.add_argument("--output", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--watch", action="store_true", help="持续检查，有变化才备份")
    parser.add_argument("--interval", type=int, default=300, help="检查间隔秒数，默认 300")
    parser.add_argument("--keep", type=int, default=10, help="保留最近多少份压缩备份，默认 10")
    parser.add_argument("--force", action="store_true", help="即使没有变化也生成一次新备份")
    args = parser.parse_args()
    if args.interval < 1 or args.keep < 1:
        parser.error("interval 和 keep 必须至少为 1")
    try:
        with RunLock(args.output.resolve() / ".backup.lock"):
            if args.watch:
                print("自动保存已启动；每 " + str(args.interval) + " 秒检查一次。保持窗口打开，按 Ctrl+C 停止。", flush=True)
            while True:
                try:
                    backup(args)
                except Exception as exc:
                    if not args.watch:
                        raise
                    print("本轮未完成：" + str(exc) + "；下一轮会重试。", file=sys.stderr, flush=True)
                if not args.watch:
                    break
                time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\n自动保存已停止，已生成的备份保留。", flush=True)
    except Exception as exc:
        print("备份失败：" + str(exc), file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
