# B 站视频研读 · bili-video-study

把 B 站视频整理成可以回到原片核对的学习笔记。工具按分 P 提取音轨，用 MLX Whisper 转写中文口播，并单独获取字幕。学习笔记以口播为主，字幕补充术语和画面数字。影响理解的差异才需要在笔记中说明。

## 安装

运行环境为 Apple Silicon Mac 和 Python 3.13。准备好 uv、FFmpeg 和 Swift 命令行工具后，在仓库根目录执行以下命令。首次使用默认语音模型时需要联网下载权重。

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
./bili-study --help
```

## 从链接到笔记

1. **准备材料。** 把下面的占位符替换为视频链接。多分 P 视频可保留链接中的 `?p=2` 等选集参数。命令返回 JSON，其中 `run` 是本次任务目录，`materials` 是按时间对齐的写作材料。

   ```sh
   ./bili-study prepare '<B站视频链接>'
   ```

   程序优先读取平台字幕轨，缺少可用轨时默认通过 macOS Vision 识别画面底部字幕。可用 `--no-ocr` 跳过画面识别，或用 `--roi x,y,w,h` 调整识别区域。字幕来源可在任务目录的 `captions.json` 查看。

2. **撰写和核查。** 由编辑或 AI 助手阅读写作材料，整理讲者的问题、观点和依据，再查阅外部来源。在任务目录创建 `evidence.json`，用 `bvid`、`cid`、`part` 和 `material_sha256` 绑定本次材料。每条 `claims` 记录包含观点编号 `id`、主张 `claim`、原片段号数组 `segments`，需要外查时填写 `check_question`。字段实例见[工作流测试](tests/test_workflow.py)。

   学习笔记写入 `drafts/study.md`。用 `V01` 等编号对应观点，用 `{{seg:12}}` 这样的标记引用实际转写段号。外查报告写入 `drafts/review.md`，每个待查观点以 `## V01 …` 这样的标题起段，附原片依据、外部来源链接和核查判断。

3. **分别发布。** 将占位符替换为第一步返回的任务目录。程序校验段号与来源，并把段号转成可点击的原片时间链接。外查尚未完成时，可以先发布学习笔记。

   ```sh
   ./bili-study publish '<任务目录>' --document study
   ./bili-study publish '<任务目录>' --document review
   ```

默认成品位于 `生成文档/<标题>（<BV>-<CID>）/`，包括学习笔记、外部核查报告、口播转写 SRT 及可用的原片字幕 SRT。音视频、草稿和证据保存在本机 `runs/<BV>/<CID>/`。

## 测试

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
