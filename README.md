# codex对话自动保存

把本机 Codex 聊天记录定期保存成压缩备份，并导出方便阅读、检索和接续工作的 Markdown 文本。

适合希望保留本地聊天、整理项目上下文，或在切换账号前保存工作进度的人。脚本在本机运行，使用 Python 标准库，不需要 API 密钥或第三方 Python 包。

## 快速开始（Windows）

1. 点击仓库的 **Code → Download ZIP**，完整解压到你准备长期保存备份的位置。
2. 双击 **`备份一次.cmd`**，立即检查并保存当前记录。
3. 双击 **`启动自动保存.cmd`**，开始持续保存。默认每 **5 分钟**检查一次，有变化才创建新备份，保留最近 **10 份**。
4. 保持窗口打开。按 **Ctrl+C** 或关闭窗口停止；电脑休眠或关机时不会执行检查。

Windows 启动入口会优先寻找 Codex 自带的 Python；找不到时尝试已安装的 `py -3` 或 `python.exe`。需要 Python **3.9 或以上**。

## 备份放在哪里

默认保存在脚本旁边的 **`data`** 文件夹。

```text
codex-chat-auto-save/
├── backup_codex_chats.py
├── run_backup.ps1
├── 备份一次.cmd
├── 启动自动保存.cmd
└── data/
    ├── snapshots/             # 带日期的聊天快照压缩包
    ├── latest/
    │   ├── 项目接续索引.md     # 最近请求、已完成回复及项目目录
    │   ├── 聊天文本/          # 可直接阅读的 Markdown
    │   ├── backup_manifest.json
    │   └── 备份范围说明.txt
    └── state.json            # 用于检测变化
```

例如把脚本放在 `D:\CodexBackup`，备份就保存在 `D:\CodexBackup\data`。

每份压缩包保存该时刻发现的原始聊天文件，并包含可阅读文本与文件校验信息。保留数量只限制快照数量，**不限制总大小**；占用空间随聊天量增加。没有内容变化时不生成重复快照。

## 保存哪些内容

| 内容 | 处理方式 |
| --- | --- |
| `sessions/` 中的 `.jsonl` | 保存原始文件，导出用户和助手可见文本 |
| `archived_sessions/` 中的 `.jsonl` | 同上 |
| `.jsonl.zst` 较早压缩记录 | 原文件完整保留，此无依赖版本不展开 |
| `session_index.jsonl` | 保存会话名称索引 |
| 本地项目目录 | 从会话中提取路径；项目文件本身需要另行备份 |
| 普通 ChatGPT 云端聊天 | 不在本脚本的读取范围内 |
| 图片、附件、实验数据和代码项目 | 不额外复制原文件；请另行保存 |
| `auth.json`、配置和登录文件 | 不复制 |

默认读取 `CODEX_HOME` 指向的目录；没有设置时读取用户目录下的 `.codex`。

正在写入的对话只保存该次检查时已写入磁盘的内容。遇到尚未写完整的行会在索引中说明，后续检查会继续保存更新。

脚本只读取源聊天文件。保留数量清理只针对自己在 `data/snapshots/` 中生成的日期压缩包。独立的 `latest/聊天文本/` 目录保留已导出的文本，可能包含已删除或已改名会话的旧副本。

## 命令行使用

立即备份：

```powershell
python backup_codex_chats.py
```

每分钟检查一次，保留最近 20 份：

```powershell
python backup_codex_chats.py --watch --interval 60 --keep 20
```

指定保存位置或源目录：

```powershell
python backup_codex_chats.py --output "D:\CodexBackup\data"
python backup_codex_chats.py --source "D:\MyCodexHome"
```

强制生成一份快照：

```powershell
python backup_codex_chats.py --force
```

使用 Windows 启动入口修改间隔：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\run_backup.ps1 -Watch -IntervalSeconds 60 -Keep 20
```

这个执行策略参数只作用于本次 PowerShell 进程，不会永久修改系统设置。

## 项目接续

换号前，先等其他对话中的任务完成，再运行一次“备份一次”。换号后先尝试打开原对话；如果原对话不可见，可以在新聊天中提供 `项目接续索引.md` 和对应聊天文本，并重新打开原项目文件夹。

示例提示：

> 请先阅读项目接续索引和对应聊天记录，核对项目文件及尚未完成的任务，再继续最近的工作。历史回复仅作参考，执行前以当前文件和我的最新要求为准。

网页版无法仅凭文件路径访问你的本地磁盘，需要上传所需记录和项目文件。此工具不保证跨账号恢复原来的聊天列表，也不是官方账号迁移工具。

## 可选项目名称清单

把 `project_inventory.example.json` 复制为 `project_inventory.json`，填写自己的项目名称和路径，脚本会把它们加入接续索引。不提供这个文件也能正常备份。

`project_inventory.json` 和 `data/` 已被 Git 忽略。发布自己的修改时，请继续保留这些规则；聊天备份及其中的路径、工具记录或用户输入可能包含私人信息，不应当作为源代码上传。

## 验证

```powershell
python -m unittest discover -s tests -v
```

测试使用临时生成的示例会话，不访问真实聊天，检查原始数据完整性、文本导出、变化检测、保留策略及正在写入的记录。

## 参考

- [Codex 本地状态位置](https://learn.chatgpt.com/docs/config-file/config-advanced)
- [Codex 保存与恢复聊天命令](https://learn.chatgpt.com/docs/developer-commands)
- [ChatGPT 账号间使用导出的对话](https://help.openai.com/en/articles/9106926-transfer-exported-conversations-between-chatgpt-accounts)

本项目为独立本地工具，与 OpenAI 官方无隶属关系。
