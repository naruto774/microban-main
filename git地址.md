# Git 远程仓库地址

## 状态

- 本地仓库：已初始化（`main` 分支）
- 初始提交：已完成（316 个文件）
- GitHub CLI：已安装（`gh 2.100.0`）
- 远程推送：**待完成**（当前网络无法连接 `github.com:443`）

## 推送步骤（网络恢复或配置代理后执行）

### 1. 登录 GitHub CLI

```powershell
gh auth login --hostname github.com --git-protocol https --web
```

### 2. 创建远程仓库并推送

```powershell
cd D:\ann\microban-main
gh repo create microban-main --private --source=. --remote=origin --push
```

如需公开仓库，把 `--private` 改成 `--public`。

### 3. 若仓库已在 GitHub 上手动创建

把下面的 URL 换成你的仓库地址，然后执行：

```powershell
git remote add origin https://github.com/<你的用户名>/microban-main.git
git push -u origin main
```

## 远程地址（推送成功后填写）

```
https://github.com/<你的用户名>/microban-main.git
```
