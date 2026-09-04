# Git 远程仓库地址

## 状态

- 本地仓库：已初始化（`main` 分支）
- 初始提交：已完成
- GitHub CLI：已安装（`gh 2.100.0`，路径 `C:\Program Files\GitHub CLI\`）
- 代理：已配置 `http://127.0.0.1:7897`（与系统代理一致）
- 远程推送：**待完成**（需先完成 `gh auth login`）

## 代理配置（已完成）

系统代理为 `127.0.0.1:7897`，已为 Git / gh 写入：

| 配置项 | 值 |
|--------|-----|
| `git config --global http.proxy` | `http://127.0.0.1:7897` |
| `git config --global https.proxy` | `http://127.0.0.1:7897` |
| 用户环境变量 `HTTP_PROXY` / `HTTPS_PROXY` | `http://127.0.0.1:7897` |

**新开终端**后环境变量自动生效。

若 `gh` 找不到（安装后旧终端未刷新 PATH），在当前终端先执行：

```powershell
$env:Path += ";C:\Program Files\GitHub CLI\"
$env:HTTP_PROXY = "http://127.0.0.1:7897"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
```

或直接用完整路径：

```powershell
& "C:\Program Files\GitHub CLI\gh.exe" auth login --hostname github.com --git-protocol https --web
```

## 推送步骤

### 1. 登录 GitHub CLI

```powershell
$env:HTTP_PROXY = "http://127.0.0.1:7897"
$env:HTTPS_PROXY = "http://127.0.0.1:7897"
gh auth login --hostname github.com --git-protocol https --web
```

浏览器打开 https://github.com/login/device ，输入终端里显示的一次性验证码。

### 2. 创建远程仓库并推送

```powershell
cd D:\ann\microban-main
gh repo create microban-main --private --source=. --remote=origin --push
```

如需公开仓库，把 `--private` 改成 `--public`。

### 3. 若仓库已在 GitHub 上手动创建

```powershell
git remote add origin https://github.com/<你的用户名>/microban-main.git
git push -u origin main
```

## 远程地址（推送成功后填写）

```
https://github.com/<你的用户名>/microban-main.git
```
