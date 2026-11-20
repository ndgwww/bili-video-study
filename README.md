# B 站视频研读 · bili-video-study

输入一个 B 站视频链接，在本机提取中文口播和字幕，把学习笔记中的每个主要观点连回原片。学习笔记记录讲者说了什么，另一份外部核查报告记录证据支持到什么程度。

## 手动使用

这条流程只需要终端、文本编辑器和浏览器。命令负责下载、转写、对齐字幕和校验发布。你负责阅读材料、组织笔记、打开外部来源并写下判断。**如果只需要转写和字幕，做到第 3 步即可。** 以下命令在 macOS 的同一个终端窗口按顺序执行。

### 1. 下载并进入仓库

目前的本地转写使用 Apple Silicon Mac，终端运行 `uname -m` 应显示 `arm64`。用 `swift --version` 检查画面字幕识别所需的工具。首次配置 Mac 时，可运行 `xcode-select --install` 并等待安装完成。[Apple 命令行工具安装说明](https://developer.apple.com/documentation/xcode/installing-the-command-line-tools/)

**用 Git 下载**，在希望存放项目的位置执行以下命令。

```sh
git clone https://github.com/ndgwww/bili-video-study.git
cd bili-video-study
```

**已经下载 ZIP**，双击解压。在终端输入 `cd` 和一个空格，把解压后的 `bili-video-study-main` 文件夹拖入终端，再按回车。两种方式都可以用 `ls bili-study requirements.txt` 确认已进入项目根目录。

### 2. 安装并检查运行环境（首次使用）

先运行 `uv --version`。如果找不到 uv，已安装 Homebrew 的用户可执行 `brew install uv`，也可以按官方说明安装再重新打开终端。[uv 安装说明](https://docs.astral.sh/uv/getting-started/installation/)

```sh
uv venv --python 3.13 .venv
uv pip install --python .venv/bin/python -r requirements.txt
test -x .venv/bin/ffmpeg || ln -s "$(.venv/bin/python -c 'import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())')" .venv/bin/ffmpeg
.venv/bin/ffmpeg -version
./bili-study --help
```

看到 FFmpeg 版本和 `prepare`、`publish` 两个子命令即可继续。首次转写还会下载默认语音识别模型 `mlx-community/whisper-large-v3-turbo`，权重约 1.61 GB。也可以先在仓库下创建 `.models/whisper-large-v3-turbo/`，放入同一模型的 `config.json` 和 `weights.safetensors`，程序会优先读取本地文件。以后处理新视频从第 3 步开始。[模型文件](https://huggingface.co/mlx-community/whisper-large-v3-turbo/tree/main)

### 3. 输入视频链接，取得转写和字幕

把第一行引号内的文字换成普通 BV 视频的完整链接或 b23.tv 短链。多分 P 视频保留 `?p=2` 等选集参数。命令会把任务目录存入变量 `run_dir`，供后面的步骤使用。

```sh
video_url='在这里粘贴 B 站视频链接'
unset run_dir
prepare_json=$(mktemp -t bili-prepare)
./bili-study prepare "$video_url" > "$prepare_json" &&
  run_dir=$(.venv/bin/python -c 'import json,sys; print(json.load(open(sys.argv[1]))["run"])' "$prepare_json") &&
  printf '本次任务目录：%s\n' "$run_dir"
```

下载、转写和字幕抽帧可能需要几分钟，看到“本次任务目录”后继续。打开材料，通读首段到结尾，记下能支撑每个主要观点的段号。例如 `[段 12 | …]` 的引用写作 `{{seg:12}}`。

```sh
less "$run_dir/写作材料.md"
```

按空格翻页，按 `q` 返回终端。每段先展示独立口播转写，再列出同期字幕候选。以口播为主，用字幕核对人名、数字和画面信息，只标注影响理解的差异。程序优先读取平台字幕轨，缺少可用轨时尝试识别画面底部字幕。只需口播时，可在 `prepare` 后加 `--no-ocr` 跳过新的画面识别。此时 `"$run_dir/成品/"` 已有《口播转写.srt》，取得字幕时还会有《原片字幕.srt》。实际字幕来源和状态见 `"$run_dir/captions.json"`。

### 4. 建立观点清单

在同一终端完整复制下面的代码。它从本次任务读取视频身份和材料摘要，在任务目录创建 `evidence.json` 的起稿文件。已有清单会保留。

```sh
.venv/bin/python - "$run_dir" <<'PY'
import json
import sys
from pathlib import Path

run = Path(sys.argv[1])
meta = json.loads((run / "manifest.json").read_text())
evidence = {key: meta[key] for key in ("bvid", "cid", "part")}
evidence["material_sha256"] = meta["stages"]["material"]["sha256"]
evidence["claims"] = [
    {"id": "V01", "claim": "", "segments": [], "check_question": None}
]
path = run / "evidence.json"
try:
    with path.open("x", encoding="utf-8") as f:
        json.dump(evidence, f, ensure_ascii=False, indent=2)
        f.write("\n")
except FileExistsError:
    print("继续编辑已有清单")
print(path)
PY
nano "$run_dir/evidence.json"
```

保留自动填好的四个字段，只编辑 `claims`。给每个主要观点一条记录，编号依次为 `V01`、`V02`。下例只演示一条记录的填法，请替换为视频里的真实主张和段号。需要外查时填写具体问题，其余填 JSON 值 `null`。多条记录之间用逗号分隔。使用 nano 时按 **Ctrl+O、回车** 保存，再按 **Ctrl+X** 退出。

```json
{"id": "V01", "claim": "讲者认为样本在观测期内增加了一倍", "segments": [12, 13], "check_question": "原始统计表是否使用相同的时间范围和单位？"}
```

### 5. 写学习笔记和外部核查报告

逐行执行。第一条打开笔记，保存退出后，第二条打开核查报告。也可以用自己的纯文本编辑器打开同一路径。

```sh
nano "$run_dir/drafts/study.md"
nano "$run_dir/drafts/review.md"
```

**学习笔记**用一级标题开头，覆盖清单中的每个观点。每节写清主张、讲者给出的理由或例子，以及成立条件，再附对应段号。下面是正文骨架，方括号内的提示和段号要换成自己的内容。

```markdown
# [视频标题] · 学习笔记

## V01 [用一句话写出观点]
[讲者观察到了什么？举了什么例子？结论适用于什么范围？] {{seg:12}}

## 复习
[用自己的话回答视频开头的问题。]
```

**外部核查报告**逐项处理有 `check_question` 的观点。先在浏览器打开原始论文、官方数据或一手声明，核对其日期、单位和适用范围。每项用 `## V01 …` 作标题，引用该观点登记过的实际段号，加一个可访问的外站 HTTPS 链接。判断选用“有支持”“部分支持”“与证据冲突”或“证据不足”，并写明理由。没有待查命题或暂时找不到适当来源时，只执行下一步的学习笔记发布命令。

```markdown
# [视频标题] · 外部核查

## V01 [要核查的问题]
原片依据：[概括原说法] {{seg:12}}
核查判断：[填写判断及理由，指出来源能验证哪部分。]
来源：[机构、资料标题和日期](在这里粘贴已阅读的外部资料的完整HTTPS链接)
```

### 6. 发布并打开结果

```sh
./bili-study publish "$run_dir" --document study
./bili-study publish "$run_dir" --document review
open "$run_dir/成品"
```

每次发布成功会输出 `"status": "done"`。Finder 打开的文件夹对应仓库下的 `生成文档/<标题>（<BV>-<CID>）/`。打开《视频学习笔记.md》和《外部核查与补充.md》，点一个时间链接回看原片，再点一个外部来源核对报告。改稿后只需重新发布对应文档。如果更改了观点清单，请复核并重新发布两份。

### 常见问题

| 遇到的情况 | 怎么继续 |
| --- | --- |
| 关闭终端后想继续写 | 重新进入仓库。在终端输入 `run_dir=`，把 `runs/<BV>/<CID>` 的实际文件夹拖到等号后，按回车，再从第 4、5 或 6 步继续。 |
| 只有口播，没有原片字幕 | 查看 `captions.json` 的状态。可继续按口播整理，或在第 3 步的 `prepare` 命令中加入 `--roi x,y,w,h` 调整 OCR 区域。四个数分别是左边距、上边距、宽和高，均按画面比例填写。 |
| 发布提示段号无效或缺少对应依据 | 对照《写作材料.md》修正 `segments` 和草稿中的 `{{seg:…}}`。核查段落至少引用一个在该观点下登记的段号。 |
| 提示材料已变化 | 重新运行第 3 步，核对新材料中的段号和主张，再将清单的 `material_sha256` 更新为 `manifest.json` 中的 `stages.material.sha256`。 |
| 报告缺少外部来源或核查判断 | 检查每个待查观点的二级标题、原片标记、Markdown 外部链接和判断用语，改好后单独重跑 `--document review`。 |

## 测试

可参考[工作流测试](tests/test_workflow.py)中的观点清单和文稿示例。从项目根目录运行全部测试。

```sh
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
