# SmartGIF

将视频压制为 `GIF`、`WebP`、`APNG` 或 `AVIF` 的网页工具，支持文件大小上限、自动寻优和新页面并排效果对比。

## Docker 快速开始

PowerShell 一条命令启动：

```powershell
docker run -d --name smartgif -p 8765:8765 -v "${PWD}\data:/data" --restart unless-stopped ghcr.io/jeron-lgy/smartgif:latest
```

Linux 或 macOS：

```bash
docker run -d --name smartgif -p 8765:8765 -v "$PWD/data:/data" --restart unless-stopped ghcr.io/jeron-lgy/smartgif:latest
```

启动后访问 [http://localhost:8765](http://localhost:8765)。

## Docker Compose

只下载一个配置文件即可运行：

```bash
curl -O https://raw.githubusercontent.com/jeron-lgy/SmartGIF/main/docker-compose.yml
docker compose up -d
```

上传视频与转换结果保存在当前目录的 `data` 文件夹中。

## 本地源码构建

开发或修改代码后运行：

```powershell
docker compose -f docker-compose.yml -f docker-compose.build.yml up --build -d
```

Windows 上也可直接双击 `启动网页动图转换器.cmd` 运行本地版本，需要本机已有 Python 与 FFmpeg。

更多说明见 [Docker使用说明.md](./Docker使用说明.md) 与 [使用说明.md](./使用说明.md)。
