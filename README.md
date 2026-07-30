# 国令慕贤任务识别

用于识别 QQ 三国“国令慕贤”任务追踪中的物品要求，并自动复制识别结果到系统剪贴板的 Windows 桌面工具。

## 功能

- 从指定 QQ 三国窗口截图，支持 `WGC` 与 `PrintWindow`。
- 根据任务追踪、国令等文字自动定位任务区域；定位失败时回退到右侧任务栏区域。
- PaddleOCR 中文识别、物品词表匹配与已知错别字校正。
- 全局快捷键录制，支持单键和组合键。
- 可设置 0.5 至 60 秒的实时识别间隔。

## 环境要求

- Windows 10 或更高版本。
- 64 位 Python 3.11。PaddlePaddle 2.6.2 不支持 Python 3.14。

## 从源码运行

1. 双击 [scripts/安装OCR依赖.cmd](scripts/安装OCR依赖.cmd)，它会创建 `.venv` 并安装依赖。
2. 双击 [scripts/启动国令任务识别.cmd](scripts/启动国令任务识别.cmd)。
3. 在程序中选择 QQ 三国窗口，选择截图方式后点击“截取窗口”。

首次 OCR 会下载中文模型，需要网络连接。安装日志保存在项目根目录的 `install_ocr.log`，运行日志为 `ocr_app.log`。

## 发布 EXE

运行 [scripts/构建发布版.cmd](scripts/构建发布版.cmd)。构建结果为：

```text
release/GuolingTaskOCR-1.0.2.exe
```

`release/` 已被 Git 忽略。创建 GitHub Release 时，将该 EXE 作为 Release asset 上传，不应提交进源码仓库。首次运行 EXE 时，PaddleOCR 仍会下载中文模型。

## 目录结构

```text
.
├─ src/guoling_task_ocr/       # 应用源码与随包数据
│  ├─ app.py
│  └─ data/                    # OCR 纠错和官方物品词表
├─ scripts/                    # 安装、启动与发布构建脚本
├─ requirements.txt            # 运行时依赖
├─ requirements-build.txt      # 打包依赖
├─ pyproject.toml              # Python 包元数据
└─ README.md
```

## 截图方式说明

`WGC（后台，高效）` 使用 Windows Graphics Capture，已在本机 QQ 三国窗口验证可取图；若它不支持目标窗口、会话关闭或 3 秒内没有画面，程序会自动回退到 `PrintWindow`。

截图方式只涉及兼容性，不能代表任何反作弊检测风险保证。
