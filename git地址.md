# Git 远程仓库地址

## 仓库

- URL：https://github.com/naruto774/microban-main
- 可见性：Private
- 账号：`naruto774`
- 分支：`main`（已推送并跟踪 `origin/main`）

## 代理与 PATH（本机已配置）

| 配置项 | 值 |
|--------|-----|
| 系统 / Git 代理 | `http://127.0.0.1:7897` |
| `gh` 路径 | `C:\Program Files\GitHub CLI\` |

当前终端若找不到 `gh`，先执行：

```powershell
$env:Path += ";C:\Program Files\GitHub CLI\"
$env:HTTP_PROXY = "http://127.0.0.1:7897"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
```

## 常用命令

```powershell
git pull
git push
gh repo view --web
```
