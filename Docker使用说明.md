# Docker 运行说明

Docker 版本提供与本地版相同的网页转换界面，并将导入视频与输出结果保存到宿主机 `data` 目录。

## 启动

在项目目录执行：

```powershell
docker compose up --build -d
```

浏览器打开：

```text
http://localhost:8765
```

首次构建会下载 Node、Python 基础镜像并在镜像内安装 FFmpeg，因此需要联网且耗时会较长。

如果构建时提示无法连接 `auth.docker.io` 或 `registry-1.docker.io`，请先在 Docker Desktop 中配置当前网络可用的镜像加速，或预先拉取 `node:22-alpine` 与 `python:3.13-slim` 基础镜像后再执行构建。本项目的 `Dockerfile` 保持标准镜像名称，不绑定特定镜像站。

## 数据目录

Compose 默认映射以下目录：

| 宿主机目录 | 容器目录 | 用途 |
| --- | --- | --- |
| `.\data\uploads` | `/data/uploads` | 网页导入的视频 |
| `.\data\outputs` | `/data/outputs` | 转换生成的动图 |

`data` 已加入 Git 忽略列表，容器重建或更新代码不会删除其中的文件。

## 常用命令

```powershell
# 查看运行状态
docker compose ps

# 查看日志
docker compose logs -f

# 停止服务，保留转换结果
docker compose down

# 重新构建并启动最新版
docker compose up --build -d
```

镜像内置 `/api/health` 健康检查，启动后 `docker compose ps` 会显示容器的健康状态。

## 单独运行镜像

不使用 Compose 时，可执行：

```powershell
docker build -t animation-converter .
docker run --rm -p 8765:8765 -v "${PWD}\data:/data" animation-converter
```

容器通过 `ANIMATION_DATA_DIR=/data` 将上传与输出保存在挂载目录；镜像启动时监听 `0.0.0.0:8765`，且不会尝试在容器内打开浏览器。
