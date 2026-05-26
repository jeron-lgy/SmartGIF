# Docker 运行说明

Docker 版本提供与本地版相同的网页转换界面，并将导入视频与输出结果保存到宿主机 `data` 目录。公开镜像发布在：

```text
ghcr.io/jeron-lgy/smartgif:latest
```

## 用户安装

### 使用 Compose

下载项目中的 `docker-compose.yml`，在文件所在目录执行：

```powershell
docker compose up -d
```

也可以仅用命令下载配置并启动：

```powershell
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/jeron-lgy/SmartGIF/main/docker-compose.yml" -OutFile "docker-compose.yml"
docker compose up -d
```

Linux 或 macOS：

```bash
curl -O https://raw.githubusercontent.com/jeron-lgy/SmartGIF/main/docker-compose.yml
docker compose up -d
```

浏览器打开：

```text
http://localhost:8765
```

### 一条命令启动

PowerShell：

```powershell
docker run -d --name smartgif -p 8765:8765 -v "${PWD}\data:/data" --restart unless-stopped ghcr.io/jeron-lgy/smartgif:latest
```

Linux 或 macOS：

```bash
docker run -d --name smartgif -p 8765:8765 -v "$PWD/data:/data" --restart unless-stopped ghcr.io/jeron-lgy/smartgif:latest
```

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

# 拉取并运行最新发布镜像
docker compose pull
docker compose up -d
```

镜像内置 `/api/health` 健康检查，启动后 `docker compose ps` 会显示容器的健康状态。

## 本地开发与构建

需要从当前源码构建镜像时，在项目根目录使用构建覆盖文件：

```powershell
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

首次本地构建会下载 Node、Python 基础镜像并在镜像内安装 FFmpeg，因此需要联网且耗时会较长。如果构建时提示无法连接 `auth.docker.io` 或 `registry-1.docker.io`，请先在 Docker Desktop 中配置当前网络可用的镜像加速。

## 镜像发布

`.github/workflows/publish-container.yml` 会在 `main` 更新、推送 `v*` 标签或手动触发时，将镜像发布到 GHCR：

```text
ghcr.io/jeron-lgy/smartgif:latest
ghcr.io/jeron-lgy/smartgif:sha-<commit>
```

首次发布后，请在 GitHub Packages 页面确认 `smartgif` 镜像可见性设置为 `Public`，这样未登录的使用者才能直接拉取镜像。

容器通过 `ANIMATION_DATA_DIR=/data` 将上传与输出保存在挂载目录；镜像启动时监听 `0.0.0.0:8765`，且不会尝试在容器内打开浏览器。
