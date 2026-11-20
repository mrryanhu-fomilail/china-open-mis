# 发布到 GitHub 的步骤

> 项目已经在本目录初始化好 git 仓库并完成了首次提交，你只需要在 GitHub 上建一个空仓库，
> 然后把本地代码推上去。整个过程 3～5 分钟，不需要懂太多 git。

## 先确认两件事（已经帮你处理好）

1. **`.env` 不会被上传**：里面有你的 MySQL 密码，已在 `.gitignore` 中忽略，
   仓库里只提供模板 `.env.example`（密码留空）。
2. **`__pycache__`、`.DS_Store`、日志等临时文件不会上传**。

可以用下面的命令随时确认 `.env` 没有被纳入版本控制：

```bash
cd china_open_mis
git check-ignore -v .env      # 有输出说明被忽略（正确）
git ls-files | grep '^\.env$'  # 应该没有输出
```

---

## 第 1 步：在 GitHub 上新建空仓库

1. 打开 https://github.com/new
2. 填写：
   - **Repository name**：`china-open-mis`（或你喜欢的名字）
   - **Description**：中国网球公开赛赛事运营管理系统（Python Flask + MySQL 课程设计）
   - 选择 **Public**（公开）或 **Private**（私有），课程设计展示一般选 Public
   - **不要**勾选 "Add a README file"、"Add .gitignore"、"Choose a license"（本地已有这些文件，勾了会冲突）
3. 点 **Create repository**，页面上会显示仓库地址，形如：
   `https://github.com/你的GitHub用户名/china-open-mis.git`

## 第 2 步：把本地代码推上去

在项目目录执行（把地址换成你自己的）：

```bash
cd "/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis"
git remote add origin https://github.com/你的GitHub用户名/china-open-mis.git
git push -u origin main
```

- 第一次 push 会提示输入账号密码：
  - **Username**：你的 GitHub 用户名
  - **Password**：**不是**登录密码，而是 Personal Access Token（见下方说明）
- macOS 会把凭据存进钥匙串，以后 push 不用再输。

### 没有 Personal Access Token？

1. 打开 https://github.com/settings/tokens → **Generate new token (classic)**
2. Note 随便填（如 `mac-git`），Expiration 选 90 天
3. 勾选 **repo** 这一整项（其余不用勾）
4. 点最下面的 **Generate token**，复制 `ghp_` 开头的一长串（**只显示一次**）
5. 回到终端，把它粘贴到 Password 处（输入时不会显示字符，属正常）

## 第 3 步：验证

打开 `https://github.com/你的GitHub用户名/china-open-mis`，应该能看到：

- README 正常渲染（含模块表格、启动步骤）
- 目录里有 `app/`、`sql/`、`docs/`（含 16 张截图）、`tests/`、`启动系统.command` 等
- **没有** `.env` 文件

---

## 可选：安装 GitHub CLI，一条命令建仓并推送

喜欢命令行的可以用官方 CLI，之后建仓/推送都不用管 token：

```bash
brew install gh          # 没装 Homebrew 的话先装 https://brew.sh
gh auth login            # 浏览器授权登录
cd "/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis"
gh repo create china-open-mis --public --source=. --remote=origin --push
```

## 可选：以后改代码怎么更新

```bash
cd "/Users/ryanhu/Desktop/大学课程/大三上/管理信息系统/管理信息系统分析与设计/china_open_mis"
git add -A
git commit -m "说明这次改了什么"
git push
```

---

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `remote origin already exists` | `git remote set-url origin 新地址` 后再 push |
| `Authentication failed` | 密码处必须用 Personal Access Token，不是 GitHub 登录密码 |
| `error: failed to push some refs` / 提示先 pull | 远程仓库不是空的（建仓时勾了 README）。`git pull --rebase origin main` 后再 push，或删掉远程仓库重建一个空的 |
| `src refspec main does not match any` | 本地的分支不叫 main，先执行 `git branch -M main` |
| 想换仓库地址 | `git remote set-url origin 新地址` |
| 不小心把 `.env` 提交上去了 | 立刻 `git rm --cached .env && git commit -m "remove secret" && git push`，并**更换 MySQL 密码** |
